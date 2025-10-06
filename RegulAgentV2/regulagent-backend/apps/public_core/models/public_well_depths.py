from django.db import models

from .well_registry import WellRegistry


class PublicWellDepths(models.Model):
    """
    Depth references for a well from regulator filings.
    Keep one row; update on newer filings with as_of.
    """

    well = models.OneToOneField(WellRegistry, on_delete=models.CASCADE, related_name='public_depths')

    td_ft = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    kb_elev_ft = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    gl_elev_ft = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    surf_shoe_ft = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    int_shoe_ft = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    prod_shoe_ft = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    provenance = models.JSONField(default=dict)
    source = models.CharField(max_length=256, blank=True)
    as_of = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'public_core_public_well_depths'

    def __str__(self) -> str:  # pragma: no cover
        return f"Depths<{self.well.api14} TD={self.td_ft} ft>"


