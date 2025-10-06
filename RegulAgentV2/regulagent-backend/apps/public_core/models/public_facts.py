from django.db import models

from .well_registry import WellRegistry


class PublicFacts(models.Model):
    """
    Normalized public facts scraped or downloaded from the regulator.
    One row per (well, fact_key) with provenance and as_of timestamp.
    """

    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE, related_name='public_facts')
    fact_key = models.CharField(max_length=128)

    # Store value as JSON to accommodate typed values (numbers/strings/objects)
    value = models.JSONField()
    units = models.CharField(max_length=32, blank=True)

    # Provenance: regulator URL/id, parser version, page/bbox etc.
    provenance = models.JSONField(default=dict)
    source = models.CharField(max_length=256, blank=True)
    as_of = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'public_core_public_facts'
        unique_together = (
            ('well', 'fact_key'),
        )
        indexes = [
            models.Index(fields=['well', 'fact_key']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"PublicFacts<{self.well.api14}:{self.fact_key}>"


