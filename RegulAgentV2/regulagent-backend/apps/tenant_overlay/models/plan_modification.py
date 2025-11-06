import uuid

from django.db import models

from apps.public_core.models import PlanSnapshot
from apps.tenants.models import Tenant


class PlanModification(models.Model):
    OPERATION_CHOICES = (
        ("combine_plugs", "Combine Plugs"),
        ("replace_cibp_with_long_plug", "Replace CIBP with Long Plug"),
        ("generic_edit", "Generic Edit"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    tenant = models.ForeignKey(Tenant, on_delete=models.SET_NULL, null=True, blank=True, related_name="plan_modifications")
    plan_snapshot = models.ForeignKey(PlanSnapshot, on_delete=models.CASCADE, related_name="modifications")

    plan_id = models.CharField(max_length=64, db_index=True)
    operation = models.CharField(max_length=64, choices=OPERATION_CHOICES, default="generic_edit", db_index=True)

    # Raw request describing the requested change (e.g., which steps to combine)
    request_payload = models.JSONField(default=dict)

    # Result diff summary (e.g., removed_steps, added_steps, updated_steps, materials_delta)
    result_diff = models.JSONField(default=dict)

    # Optional actor information until full auth is wired
    created_by = models.CharField(max_length=255, blank=True, null=True)
    metadata = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenant_overlay_plan_modifications"
        indexes = [
            models.Index(fields=["plan_snapshot", "created_at"]),
            models.Index(fields=["plan_id"]),
            models.Index(fields=["operation"]),
            models.Index(fields=["created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"PlanModification<{self.operation}:{self.plan_id}:{self.created_at.isoformat()}>"


