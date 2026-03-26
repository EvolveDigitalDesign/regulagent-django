from __future__ import annotations

import uuid

from django.db import models


class WellComponentSnapshot(models.Model):
    """
    Materialized point-in-time JSON snapshot of resolve_well_components() output.
    Append-only — never updated, new snapshot created instead.

    Created when:
    - A PlanSnapshot is finalized (pre_plugging state)
    - A W3WizardSession reaches 'completed' (post_plugging state)
    - Initial extraction completes (baseline state)
    - Tenant adds/modifies components (tenant_override state)
    """

    class SnapshotContext(models.TextChoices):
        BASELINE = "baseline", "Baseline"
        PRE_PLUGGING = "pre_plugging", "Pre-Plugging"
        POST_PLUGGING = "post_plugging", "Post-Plugging"
        TENANT_OVERRIDE = "tenant_override", "Tenant Override"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    well = models.ForeignKey(
        "public_core.WellRegistry",
        on_delete=models.CASCADE,
        related_name="component_snapshots",
    )
    tenant_id = models.UUIDField(null=True, blank=True, db_index=True)
    context = models.CharField(max_length=20, choices=SnapshotContext.choices)

    # Links to triggering objects
    plan_snapshot = models.ForeignKey(
        "public_core.PlanSnapshot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="component_snapshots",
    )
    wizard_session = models.ForeignKey(
        "public_core.W3WizardSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="component_snapshots",
    )

    # The materialized data
    snapshot_data = models.JSONField(default=dict, help_text="Resolved component list as JSON")
    component_count = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "public_core_well_component_snapshots"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["well", "context"]),
            models.Index(fields=["well", "tenant_id"]),
            models.Index(fields=["plan_snapshot"]),
            models.Index(fields=["wizard_session"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"Snapshot ({self.context}) for {self.well} — {self.component_count} components"
