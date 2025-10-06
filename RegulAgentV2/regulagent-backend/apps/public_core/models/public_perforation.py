from django.db import models

from .well_registry import WellRegistry


class PublicPerforation(models.Model):
    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE, related_name='public_perforations')
    top_ft = models.DecimalField(max_digits=8, decimal_places=2)
    bottom_ft = models.DecimalField(max_digits=8, decimal_places=2)

    formation = models.CharField(max_length=128, blank=True)
    shot_density_spf = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    phase_deg = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)

    provenance = models.JSONField(default=dict)
    source = models.CharField(max_length=256, blank=True)
    as_of = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'public_core_public_perforation'
        indexes = [
            models.Index(fields=['well', 'top_ft', 'bottom_ft']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"Perf<{self.well.api14} {self.top_ft}-{self.bottom_ft} ft>"


