from django.db import models


class WellRegistry(models.Model):
    """
    Global registry entry for a physical well.
    Public identity only: API14, jurisdiction, and location.
    """

    api14 = models.CharField(max_length=14, unique=True)
    state = models.CharField(max_length=2)
    county = models.CharField(max_length=64, blank=True)

    # Store as Decimal for portability; PostGIS PointField can replace later
    lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    lon = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'public_core_well_registry'
        indexes = [
            models.Index(fields=['api14']),
            models.Index(fields=['state', 'county']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"Well {self.api14} ({self.state})"


