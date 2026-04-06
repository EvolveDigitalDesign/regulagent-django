from __future__ import annotations

from django.db import models
from django.contrib.auth import get_user_model

from .plan_snapshot import PlanSnapshot
from .well_registry import WellRegistry

User = get_user_model()


class WellEditAudit(models.Model):
    """
    Tracks all user edits to extracted data and well geometry.
    
    Edits are staged initially (not applied to WellRegistry) until
    explicitly approved via apply-edits endpoint.
    
    Provides full audit trail: who edited, when, why, original vs edited values.
    """
    
    # Stage states
    STAGE_PENDING = 'pending'
    STAGE_APPLIED = 'applied'
    STAGE_REJECTED = 'rejected'
    STAGE_SUPERSEDED = 'superseded'
    
    STAGE_CHOICES = [
        (STAGE_PENDING, 'Pending - Not yet applied to WellRegistry'),
        (STAGE_APPLIED, 'Applied - Written to WellRegistry'),
        (STAGE_REJECTED, 'Rejected - User declined to apply'),
        (STAGE_SUPERSEDED, 'Superseded - Replaced by newer edit'),
    ]
    
    # Edit context types
    CONTEXT_EXTRACTION = 'extraction'  # Edit to raw extracted JSON (W-2, GAU, etc.)
    CONTEXT_GEOMETRY = 'geometry'  # Edit to derived geometry (casing, formations, perfs)
    CONTEXT_PLAN = 'plan'  # Edit within plan (does not affect WellRegistry)
    
    CONTEXT_CHOICES = [
        (CONTEXT_EXTRACTION, 'Extraction - Raw document data edit'),
        (CONTEXT_GEOMETRY, 'Geometry - Derived well geometry edit'),
        (CONTEXT_PLAN, 'Plan - Plan-only edit (not applied to registry)'),
    ]
    
    # Links
    plan_snapshot = models.ForeignKey(
        PlanSnapshot,
        on_delete=models.CASCADE,
        related_name="edits",
        help_text="Plan snapshot this edit is associated with"
    )
    
    well = models.ForeignKey(
        WellRegistry,
        on_delete=models.CASCADE,
        related_name="edit_audits",
        null=True,
        blank=True,
        help_text="Well this edit applies to (null for plan-only edits)"
    )
    
    # Edit metadata
    field_path = models.CharField(
        max_length=255,
        help_text="JSON dotpath to edited field (e.g., 'casing_record.0.cement_top_ft')"
    )
    
    field_label = models.CharField(
        max_length=128,
        blank=True,
        help_text="Human-readable field name (e.g., 'Production Casing TOC')"
    )
    
    context = models.CharField(
        max_length=16,
        choices=CONTEXT_CHOICES,
        default=CONTEXT_EXTRACTION,
        help_text="Where this edit was made (extraction, geometry, or plan)"
    )
    
    # Values
    original_value = models.JSONField(
        null=True,
        blank=True,
        help_text="Original extracted value (null if field was missing)"
    )
    
    edited_value = models.JSONField(
        help_text="User-provided edited value"
    )
    
    # Attribution
    editor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="well_edits",
        help_text="User who made the edit"
    )
    
    editor_display_name = models.CharField(
        max_length=128,
        help_text="Display name at time of edit (for audit trail)"
    )
    
    editor_tenant_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Tenant ID of editor (for filtering cross-tenant visibility)"
    )
    
    edit_reason = models.TextField(
        blank=True,
        help_text="User-provided reason for the edit"
    )
    
    # Stage tracking
    stage = models.CharField(
        max_length=16,
        choices=STAGE_CHOICES,
        default=STAGE_PENDING,
        db_index=True,
        help_text="Current stage of this edit"
    )
    
    # Application metadata (filled when stage = APPLIED)
    applied_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applied_well_edits",
        help_text="User who applied this edit to WellRegistry"
    )
    
    applied_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this edit was applied to WellRegistry"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = "public_core_well_edit_audits"
        indexes = [
            models.Index(fields=["plan_snapshot", "stage"]),
            models.Index(fields=["well", "stage"]),
            models.Index(fields=["editor_tenant_id", "stage"]),
            models.Index(fields=["context", "stage"]),
            models.Index(fields=["created_at"]),
        ]
        ordering = ["-created_at"]
    
    def __str__(self) -> str:
        return f"WellEditAudit<{self.field_label or self.field_path}:{self.stage}>"
    
    def can_be_viewed_by_tenant(self, tenant_id: str) -> bool:
        """
        Check if this edit can be viewed by the given tenant.
        
        Rules:
        - Own edits: always visible
        - Applied edits: visible to all (learning from precedent)
        - Pending edits: visible if plan_snapshot is public
        """
        # Own edits always visible
        if str(self.editor_tenant_id) == str(tenant_id):
            return True
        
        # Applied edits are public (learning)
        if self.stage == self.STAGE_APPLIED:
            return True
        
        # Pending/rejected: only visible if plan is public
        if self.plan_snapshot.visibility == PlanSnapshot.VISIBILITY_PUBLIC:
            return True
        
        return False


