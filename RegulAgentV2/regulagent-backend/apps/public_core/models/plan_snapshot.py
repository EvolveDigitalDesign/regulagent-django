from __future__ import annotations

from django.db import models
from simple_history.models import HistoricalRecords

from .well_registry import WellRegistry


class PlanSnapshot(models.Model):
    """
    Immutable snapshots of plan outputs for audit and comparison.

    kind: baseline | post_edit | submitted | approved (snapshot type)
    status: draft | internal_review | engineer_approved | filed | 
            under_agency_review | agency_approved | agency_rejected (workflow state)
    
    Tenant Isolation (from Consolidated-AI-Roadmap.md lines 296-298):
    - baseline snapshots: public (standard plan from kernel, shareable for learning)
    - post_edit snapshots: private (tenant's work-in-progress modifications)
    - submitted snapshots: public (submitted to regulator, can inform precedents)
    - approved snapshots: public (approved plans, valuable for learning)
    """

    KIND_BASELINE = "baseline"
    KIND_POST_EDIT = "post_edit"
    KIND_SUBMITTED = "submitted"
    KIND_APPROVED = "approved"

    KIND_CHOICES = [
        (KIND_BASELINE, KIND_BASELINE),
        (KIND_POST_EDIT, KIND_POST_EDIT),
        (KIND_SUBMITTED, KIND_SUBMITTED),
        (KIND_APPROVED, KIND_APPROVED),
    ]
    
    VISIBILITY_PUBLIC = "public"
    VISIBILITY_PRIVATE = "private"
    
    VISIBILITY_CHOICES = [
        (VISIBILITY_PUBLIC, "Public - Shareable for learning"),
        (VISIBILITY_PRIVATE, "Private - Tenant-only"),
    ]
    
    # Workflow status
    STATUS_DRAFT = 'draft'
    STATUS_INTERNAL_REVIEW = 'internal_review'
    STATUS_ENGINEER_APPROVED = 'engineer_approved'
    STATUS_FILED = 'filed'
    STATUS_UNDER_AGENCY_REVIEW = 'under_agency_review'
    STATUS_AGENCY_APPROVED = 'agency_approved'
    STATUS_AGENCY_REJECTED = 'agency_rejected'
    STATUS_REVISION_REQUESTED = 'revision_requested'
    STATUS_WITHDRAWN = 'withdrawn'
    
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft - Initial plan generation'),
        (STATUS_INTERNAL_REVIEW, 'Internal Review - Being modified'),
        (STATUS_ENGINEER_APPROVED, 'Engineer Approved - Ready to file'),
        (STATUS_FILED, 'Filed - Submitted to RRC'),
        (STATUS_UNDER_AGENCY_REVIEW, 'Under Agency Review - RRC reviewing'),
        (STATUS_AGENCY_APPROVED, 'Agency Approved - RRC approved'),
        (STATUS_AGENCY_REJECTED, 'Agency Rejected - RRC rejected'),
        (STATUS_REVISION_REQUESTED, 'Revision Requested - RRC requested changes'),
        (STATUS_WITHDRAWN, 'Withdrawn - Filing withdrawn'),
    ]

    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE, related_name="plan_snapshots")
    plan_id = models.CharField(max_length=64, db_index=True)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, db_index=True)

    payload = models.JSONField()  # full plan JSON as returned to clients

    # Provenance
    kernel_version = models.CharField(max_length=32, blank=True)
    overlay_id = models.CharField(max_length=128, blank=True)
    policy_id = models.CharField(max_length=64, blank=True)
    extraction_meta = models.JSONField(default=dict)
    
    # Tenant attribution and visibility (for multi-tenant isolation)
    tenant_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Tenant who created this snapshot (null for RRC-baseline plans)"
    )
    
    visibility = models.CharField(
        max_length=10,
        choices=VISIBILITY_CHOICES,
        default=VISIBILITY_PRIVATE,
        db_index=True,
        help_text="Public: baseline/approved plans (shareable). Private: work-in-progress (tenant-only)."
    )
    
    # Workflow status tracking
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
        help_text="Current workflow status of the plan"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    
    # Django simple-history for audit trail (tracks who/when for all changes)
    history = HistoricalRecords()

    class Meta:
        db_table = "public_core_plan_snapshots"
        indexes = [
            models.Index(fields=["well", "plan_id", "kind"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["tenant_id", "visibility"]),
            models.Index(fields=["visibility", "kind"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"PlanSnapshot<{self.plan_id}:{self.kind}:{self.visibility}>"


