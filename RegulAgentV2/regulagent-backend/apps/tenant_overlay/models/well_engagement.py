from django.db import models
from django.contrib.auth import get_user_model

from apps.public_core.models import WellRegistry


class WellEngagement(models.Model):
    """
    Tenant-scoped engagement linking a tenant to a public well.
    Tracks ALL historical interactions between a tenant and a well,
    including plans generated, documents uploaded, chat threads, and modifications.
    """

    class Mode(models.TextChoices):
        UPLOAD = 'upload', 'Upload'
        RRC = 'rrc', 'RRC'
        HYBRID = 'hybrid', 'Hybrid'

    class InteractionType(models.TextChoices):
        W3A_GENERATED = 'w3a_generated', 'W3A Plan Generated'
        DOCUMENT_UPLOADED = 'document_uploaded', 'Document Uploaded'
        PLAN_MODIFIED = 'plan_modified', 'Plan Modified'
        CHAT_CREATED = 'chat_created', 'Chat Thread Created'
        ADVISORY_REQUESTED = 'advisory_requested', 'Advisory Requested'

    tenant_id = models.UUIDField(db_index=True)
    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE, related_name='engagements')
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.HYBRID)

    label = models.CharField(max_length=128, blank=True)
    owner_user = models.ForeignKey(get_user_model(), null=True, blank=True, on_delete=models.SET_NULL)

    # Interaction tracking fields
    last_interaction_type = models.CharField(
        max_length=32,
        choices=InteractionType.choices,
        null=True,
        blank=True,
        help_text="Type of the most recent interaction with this well"
    )
    interaction_count = models.IntegerField(
        default=0,
        help_text="Total number of interactions with this well"
    )
    first_interaction_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of first interaction (set once, never updated)"
    )
    metadata = models.JSONField(
        default=dict,
        help_text="Summary of interactions: plan_ids, document_ids, chat_thread_ids, counts, etc."
    )

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


