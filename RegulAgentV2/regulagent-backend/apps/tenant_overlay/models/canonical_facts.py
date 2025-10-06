from django.db import models

from .well_engagement import WellEngagement


class CanonicalFacts(models.Model):
    """
    Tenant overlay facts for an engagement. These override/extend PublicFacts at runtime.
    """

    engagement = models.ForeignKey(WellEngagement, on_delete=models.CASCADE, related_name='canonical_facts')
    fact_key = models.CharField(max_length=128)
    value = models.JSONField()
    units = models.CharField(max_length=32, blank=True)
    provenance = models.JSONField(default=list)  # list of fragments/artifacts
    confidence = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'overlay_canonical_facts'
        unique_together = (
            ('engagement', 'fact_key'),
        )
        indexes = [
            models.Index(fields=['engagement', 'fact_key']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"CanonicalFacts<{self.engagement_id}:{self.fact_key}>"


