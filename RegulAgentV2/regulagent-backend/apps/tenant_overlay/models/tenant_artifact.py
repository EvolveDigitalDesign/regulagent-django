import uuid

from django.db import models

from apps.public_core.models import PlanSnapshot
from apps.public_core.models.extracted_document import ExtractedDocument
from apps.tenants.models import Tenant


class TenantArtifact(models.Model):
    ARTIFACT_TYPES = (
        ("w2", "W-2"),
        ("w15", "W-15"),
        ("gau", "GAU"),
        ("schematic", "Wellbore Schematic"),
        ("formation_tops", "Formation Tops"),
        ("other", "Other"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.SET_NULL, null=True, blank=True, related_name="artifacts")
    plan_snapshot = models.ForeignKey(PlanSnapshot, on_delete=models.SET_NULL, null=True, blank=True, related_name="artifacts")
    extracted_document = models.ForeignKey(ExtractedDocument, on_delete=models.SET_NULL, null=True, blank=True, related_name="artifacts")

    artifact_type = models.CharField(max_length=32, choices=ARTIFACT_TYPES, default="other", db_index=True)
    file_path = models.CharField(max_length=512, help_text="Absolute or media-relative path to stored file")
    content_type = models.CharField(max_length=128, blank=True, null=True)
    size_bytes = models.BigIntegerField(blank=True, null=True)
    sha256 = models.CharField(max_length=64, blank=True, null=True)
    metadata = models.JSONField(default=dict)
    uploaded_by = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenant_overlay_artifacts"
        indexes = [
            models.Index(fields=["artifact_type"]),
            models.Index(fields=["plan_snapshot", "created_at"]),
            models.Index(fields=["extracted_document", "created_at"]),
            models.Index(fields=["created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"TenantArtifact<{self.artifact_type}:{self.file_path}>"


