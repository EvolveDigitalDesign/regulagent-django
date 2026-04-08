import uuid
from django.conf import settings
from django.db import models


class ManualWBD(models.Model):
    """
    A manually authored wellbore diagram, stored as a JSON document.
    Users can create Current, Planned, and As-Plugged diagrams by hand.
    """
    class DiagramType(models.TextChoices):
        CURRENT = "current", "Current Wellbore"
        PLANNED = "planned", "Planned Plugging"
        AS_PLUGGED = "as_plugged", "As-Plugged"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    api14 = models.CharField(max_length=20, db_index=True)
    well = models.ForeignKey(
        "public_core.WellRegistry",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="manual_wbds",
    )
    diagram_type = models.CharField(max_length=16, choices=DiagramType.choices, db_index=True)
    title = models.CharField(max_length=255, blank=True, default="")
    diagram_data = models.JSONField(help_text="Complete diagram payload matching frontend renderer shape")
    tenant_id = models.UUIDField(db_index=True)
    workspace = models.ForeignKey(
        "tenants.ClientWorkspace",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manual_wbds",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "public_core_manual_wbd"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["api14", "tenant_id", "diagram_type"]),
            models.Index(fields=["tenant_id"]),
        ]

    def __str__(self):
        return f"ManualWBD({self.diagram_type}) {self.api14} — {self.title or 'Untitled'}"
