"""
Chat and plan modification models for AI-assisted planning.

Key architecture:
- ChatThread: Conversation tied to a well and plan
- ChatMessage: Individual messages in a thread (user/assistant)
- PlanModification: Tracks edits to plan payload with diffs and audit trail
- RegulatorOutcome: Tracks RRC approval/rejection for learning feedback loop

All plan modifications work on PlanSnapshot.payload (the full W3A plan JSON),
not on database fields. Each modification creates a new PlanSnapshot(kind='post_edit')
with the updated payload.
"""

from django.db import models
from django.contrib.auth import get_user_model
from apps.public_core.models import WellRegistry, PlanSnapshot

User = get_user_model()


class ChatThread(models.Model):
    """
    Conversation thread for AI-assisted plan modification.
    
    Each thread is tied to:
    - A tenant (for isolation)
    - A specific well (context)
    - A baseline plan (what we're modifying)
    
    The thread maintains conversation history and tracks all plan modifications
    made during the conversation.
    
    Sharing model:
    - Owner (created_by): Full edit rights (can modify plan, send messages)
    - Shared users (shared_with): Read-only access (can view thread and messages)
    - All users must be in the same tenant
    """
    
    # Tenant and ownership
    tenant_id = models.UUIDField(db_index=True, help_text="Tenant who owns this conversation")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='owned_chat_threads',
        help_text="Owner with full edit rights"
    )
    
    # Sharing (many-to-many for read-only access)
    shared_with = models.ManyToManyField(
        User,
        related_name='shared_chat_threads',
        blank=True,
        help_text="Users who can view this thread (read-only access)"
    )
    
    # Well and plan context
    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE, related_name='chat_threads')
    baseline_plan = models.ForeignKey(
        PlanSnapshot,
        on_delete=models.CASCADE,
        related_name='chat_threads',
        help_text="Original baseline plan being discussed/modified"
    )
    
    # Current working plan (updated as modifications are applied)
    current_plan = models.ForeignKey(
        PlanSnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='active_chat_threads',
        help_text="Latest modified plan snapshot (starts as baseline)"
    )
    
    # OpenAI integration
    openai_thread_id = models.CharField(
        max_length=128,
        blank=True,
        db_index=True,
        help_text="OpenAI Assistants API thread ID for conversation continuity"
    )
    
    # Thread metadata
    title = models.CharField(max_length=256, blank=True, help_text="Optional user-provided title")
    mode = models.CharField(
        max_length=32,
        default='assistant',
        help_text="Conversation mode: 'assistant' (OpenAI) or 'tool' (direct tool calls)"
    )
    
    # Thread state
    is_active = models.BooleanField(default=True, help_text="False if thread is archived/closed")
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_message_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp of most recent message")
    
    class Meta:
        db_table = 'assistant_chat_threads'
        indexes = [
            models.Index(fields=['tenant_id', '-created_at']),
            models.Index(fields=['well', '-created_at']),
            models.Index(fields=['tenant_id', 'is_active', '-updated_at']),
        ]
        ordering = ['-updated_at']
    
    def __str__(self):
        return f"ChatThread<{self.id}>: {self.well.api14} - {self.title or 'Untitled'}"
    
    def can_edit(self, user):
        """Check if user has edit rights (is the owner)."""
        if not user or not user.is_authenticated:
            return False
        return self.created_by_id == user.id
    
    def can_view(self, user):
        """Check if user can view this thread (owner or shared)."""
        if not user or not user.is_authenticated:
            return False
        if self.created_by_id == user.id:
            return True
        return self.shared_with.filter(id=user.id).exists()
    
    def share_with_user(self, user):
        """Share this thread with a user (grants read-only access)."""
        if user.tenants.filter(id=self.tenant_id).exists():
            self.shared_with.add(user)
            return True
        return False
    
    def unshare_with_user(self, user):
        """Remove shared access for a user."""
        self.shared_with.remove(user)


class ChatMessage(models.Model):
    """
    Individual message in a chat thread.
    
    Messages can be:
    - 'user': Human input
    - 'assistant': AI response
    - 'system': System notifications (e.g., "Plan modified successfully")
    
    Tool calls and results are stored as JSON for audit and replay.
    """
    
    ROLE_USER = 'user'
    ROLE_ASSISTANT = 'assistant'
    ROLE_SYSTEM = 'system'
    
    ROLE_CHOICES = [
        (ROLE_USER, 'User'),
        (ROLE_ASSISTANT, 'Assistant'),
        (ROLE_SYSTEM, 'System'),
    ]
    
    # Thread relationship
    thread = models.ForeignKey(ChatThread, on_delete=models.CASCADE, related_name='messages')
    
    # Message content
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, db_index=True)
    content = models.TextField(help_text="Message text content")
    
    # OpenAI integration
    openai_message_id = models.CharField(max_length=128, blank=True, help_text="OpenAI message ID")
    openai_run_id = models.CharField(max_length=128, blank=True, help_text="OpenAI run ID (for assistant responses)")
    
    # Tool usage tracking
    tool_calls = models.JSONField(
        default=list,
        help_text="List of tool calls made by assistant: [{name, arguments, call_id}, ...]"
    )
    tool_results = models.JSONField(
        default=list,
        help_text="Results of tool calls: [{call_id, result, status}, ...]"
    )
    
    # Metadata
    metadata = models.JSONField(
        default=dict,
        help_text="Additional metadata: model used, tokens, latency, etc."
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        db_table = 'assistant_chat_messages'
        indexes = [
            models.Index(fields=['thread', 'created_at']),
            models.Index(fields=['role', 'created_at']),
        ]
        ordering = ['created_at']
    
    def __str__(self):
        preview = self.content[:50] + '...' if len(self.content) > 50 else self.content
        return f"{self.role}: {preview}"


class PlanModification(models.Model):
    """
    Tracks modifications to plan payloads with full audit trail.
    
    Each modification:
    - References the source snapshot (before)
    - Creates a new snapshot (after) with kind='post_edit'
    - Stores a JSON diff for review/revert
    - Tracks risk score and violations delta
    
    This model operates on PlanSnapshot.payload (the full W3A plan JSON),
    not on database fields.
    """
    
    # Operation types
    OP_COMBINE_PLUGS = 'combine_plugs'
    OP_REPLACE_CIBP = 'replace_cibp'
    OP_ADJUST_INTERVAL = 'adjust_interval'
    OP_CHANGE_MATERIALS = 'change_materials'
    OP_ADD_STEP = 'add_step'
    OP_REMOVE_STEP = 'remove_step'
    OP_REORDER_STEPS = 'reorder_steps'
    OP_CUSTOM = 'custom'
    
    OP_TYPE_CHOICES = [
        (OP_COMBINE_PLUGS, 'Combine Plugs'),
        (OP_REPLACE_CIBP, 'Replace CIBP with Long Plug'),
        (OP_ADJUST_INTERVAL, 'Adjust Depth Interval'),
        (OP_CHANGE_MATERIALS, 'Change Materials'),
        (OP_ADD_STEP, 'Add Step'),
        (OP_REMOVE_STEP, 'Remove Step'),
        (OP_REORDER_STEPS, 'Reorder Steps'),
        (OP_CUSTOM, 'Custom Modification'),
    ]
    
    # Plan snapshots (before and after)
    source_snapshot = models.ForeignKey(
        PlanSnapshot,
        on_delete=models.CASCADE,
        related_name='modifications_from',
        help_text="Plan snapshot before modification"
    )
    result_snapshot = models.ForeignKey(
        PlanSnapshot,
        on_delete=models.CASCADE,
        related_name='modifications_to',
        null=True,
        blank=True,
        help_text="Plan snapshot after modification (kind='post_edit')"
    )
    
    # Modification details
    op_type = models.CharField(max_length=32, choices=OP_TYPE_CHOICES, db_index=True)
    description = models.TextField(help_text="Human-readable description of the change")
    
    # Modification payload and diff
    operation_payload = models.JSONField(
        help_text="Parameters for the operation: {step_ids, interval, materials, etc.}"
    )
    diff = models.JSONField(
        help_text="JSON Patch or step-level diff showing what changed in the payload"
    )
    
    # Risk assessment
    risk_score = models.FloatField(
        default=0.0,
        help_text="Risk score 0.0-1.0 (higher = more divergence from baseline)"
    )
    violations_delta = models.JSONField(
        default=list,
        help_text="New violations introduced (or resolved) by this modification"
    )
    
    # Audit trail
    chat_thread = models.ForeignKey(
        ChatThread,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='modifications',
        help_text="Chat thread that triggered this modification (if applicable)"
    )
    chat_message = models.ForeignKey(
        ChatMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='modifications',
        help_text="Specific message that triggered this modification"
    )
    applied_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='plan_modifications')
    
    # Application status
    is_applied = models.BooleanField(
        default=False,
        help_text="True if this modification is currently applied to the active plan"
    )
    is_reverted = models.BooleanField(
        default=False,
        help_text="True if this modification was later reverted"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    reverted_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'assistant_plan_modifications'
        indexes = [
            models.Index(fields=['source_snapshot', '-created_at']),
            models.Index(fields=['chat_thread', '-created_at']),
            models.Index(fields=['applied_by', '-created_at']),
            models.Index(fields=['is_applied', 'is_reverted']),
        ]
        ordering = ['-created_at']
    
    def __str__(self):
        status = "applied" if self.is_applied else ("reverted" if self.is_reverted else "pending")
        return f"PlanMod<{self.op_type}>: {self.description[:50]} [{status}]"
    
    @classmethod
    def get_modification_chain(cls, snapshot):
        """
        Get the full chain of modifications leading to this snapshot.
        Returns list of PlanModification objects from baseline to current.
        """
        chain = []
        current = snapshot
        
        # Walk backwards to baseline
        while True:
            mod = cls.objects.filter(result_snapshot=current).first()
            if not mod:
                break
            chain.insert(0, mod)
            current = mod.source_snapshot
        
        return chain
    
    @classmethod
    def get_version_history(cls, baseline_snapshot):
        """
        Get all versions (snapshots) that stem from a baseline.
        Returns list of (snapshot, modification) tuples in chronological order.
        """
        versions = [(baseline_snapshot, None)]  # Start with baseline
        
        # Find all modifications from this baseline
        mods = cls.objects.filter(
            source_snapshot__plan_id=baseline_snapshot.plan_id
        ).order_by('created_at')
        
        for mod in mods:
            versions.append((mod.result_snapshot, mod))
        
        return versions


class RegulatorOutcome(models.Model):
    """
    Tracks outcome from regulatory agency (RRC) for a filed plan.
    
    This enables the learning feedback loop:
    1. Plan filed with RRC
    2. RRC approves/rejects
    3. We mark the outcome
    4. Update confidence for similar modifications
    5. Future suggestions weighted by approval rate
    """
    
    STATUS_PENDING = 'pending'
    STATUS_UNDER_REVIEW = 'under_review'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_REVISION_REQUESTED = 'revision_requested'
    STATUS_WITHDRAWN = 'withdrawn'
    
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending - Not yet filed'),
        (STATUS_UNDER_REVIEW, 'Under Review - RRC reviewing'),
        (STATUS_APPROVED, 'Approved - RRC approved'),
        (STATUS_REJECTED, 'Rejected - RRC rejected'),
        (STATUS_REVISION_REQUESTED, 'Revision Requested - RRC requested changes'),
        (STATUS_WITHDRAWN, 'Withdrawn - Filing withdrawn'),
    ]
    
    # Link to filed plan
    plan_snapshot = models.OneToOneField(
        PlanSnapshot,
        on_delete=models.CASCADE,
        related_name='regulator_outcome',
        help_text="Plan that was filed with RRC"
    )
    
    # Regulator details
    agency = models.CharField(
        max_length=64,
        default='RRC',
        help_text="Regulatory agency (e.g., 'RRC', 'EPA')"
    )
    filing_number = models.CharField(
        max_length=128,
        blank=True,
        help_text="Agency filing/permit number"
    )
    
    # Status tracking
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True
    )
    
    # Timeline
    filed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When plan was submitted to RRC"
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When RRC completed review"
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When RRC approved (if approved)"
    )
    
    # Time metrics
    review_duration_days = models.IntegerField(
        null=True,
        blank=True,
        help_text="Days from filed to decision"
    )
    
    # Reviewer feedback
    reviewer_notes = models.TextField(
        blank=True,
        help_text="Notes/comments from RRC reviewer"
    )
    reviewer_name = models.CharField(
        max_length=128,
        blank=True,
        help_text="Name of RRC reviewer (if known)"
    )
    
    # Revision tracking
    revision_count = models.IntegerField(
        default=0,
        help_text="Number of revisions requested/submitted"
    )
    revision_notes = models.JSONField(
        default=list,
        help_text="List of revision requests: [{date, reason, resolved}, ...]"
    )
    
    # Learning metadata
    confidence_score = models.FloatField(
        default=0.5,
        help_text="Confidence score 0.0-1.0 based on approval history"
    )
    influenced_by_modifications = models.ManyToManyField(
        'PlanModification',
        related_name='influenced_outcomes',
        blank=True,
        help_text="Modifications that influenced this plan"
    )
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'assistant_regulator_outcomes'
        indexes = [
            models.Index(fields=['status', '-filed_at']),
            models.Index(fields=['agency', 'status']),
            models.Index(fields=['-approved_at']),
        ]
    
    def __str__(self):
        return f"Outcome<{self.plan_snapshot.plan_id}>: {self.status}"
    
    def mark_approved(self, approved_at=None, reviewer_notes=''):
        """Mark outcome as approved and update confidence."""
        from django.utils import timezone
        
        self.status = self.STATUS_APPROVED
        self.approved_at = approved_at or timezone.now()
        self.reviewed_at = self.approved_at
        self.reviewer_notes = reviewer_notes
        
        # Calculate review duration
        if self.filed_at:
            self.review_duration_days = (self.approved_at - self.filed_at).days
        
        # Boost confidence for approved plans
        self.confidence_score = min(self.confidence_score + 0.3, 1.0)
        
        self.save()
        
        # Trigger learning update
        self._update_similar_modification_confidence(boost=True)
    
    def mark_rejected(self, reviewed_at=None, reviewer_notes=''):
        """Mark outcome as rejected and update confidence."""
        from django.utils import timezone
        
        self.status = self.STATUS_REJECTED
        self.reviewed_at = reviewed_at or timezone.now()
        self.reviewer_notes = reviewer_notes
        
        # Calculate review duration
        if self.filed_at:
            self.review_duration_days = (self.reviewed_at - self.filed_at).days
        
        # Lower confidence for rejected plans
        self.confidence_score = max(self.confidence_score - 0.3, 0.0)
        
        self.save()
        
        # Trigger learning update
        self._update_similar_modification_confidence(boost=False)
    
    def _update_similar_modification_confidence(self, boost: bool):
        """
        Update confidence scores for similar modifications based on this outcome.
        
        This is the core learning feedback loop:
        - If plan approved → boost confidence for similar modifications
        - If plan rejected → lower confidence for similar modifications
        """
        from apps.assistant.services.learning_feedback import update_modification_confidence
        
        # Get modifications that influenced this plan
        modifications = self.influenced_by_modifications.all()
        
        for modification in modifications:
            update_modification_confidence(
                modification=modification,
                outcome_approved=boost,
                outcome_instance=self
            )

