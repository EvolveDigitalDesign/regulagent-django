from django.db import models
from django.contrib.auth import get_user_model

from apps.public_core.models import WellRegistry


class WellEngagement(models.Model):
    """
    Tenant-scoped engagement linking a tenant to a public well.
    Holds engagement mode and ownership metadata. Draft plans and overlays hang here.
    """

    class Mode(models.TextChoices):
        UPLOAD = 'upload', 'Upload'
        RRC = 'rrc', 'RRC'
        HYBRID = 'hybrid', 'Hybrid'

    tenant_id = models.UUIDField()
    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE, related_name='engagements')
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.HYBRID)

    label = models.CharField(max_length=128, blank=True)
    owner_user = models.ForeignKey(get_user_model(), null=True, blank=True, on_delete=models.SET_NULL)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'overlay_well_engagement'
        unique_together = (
            ('tenant_id', 'well'),
        )
        indexes = [
            models.Index(fields=['tenant_id', 'well']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"Engagement<{self.tenant_id}:{self.well.api14}>"


