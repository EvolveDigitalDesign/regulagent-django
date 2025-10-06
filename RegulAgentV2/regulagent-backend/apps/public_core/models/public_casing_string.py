from django.db import models

from .well_registry import WellRegistry


class PublicCasingString(models.Model):
    """
    Regulator-sourced casing program entry for a well.
    Stores outside diameter, weight, grade, and depth extents per string.
    """

    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE, related_name='public_casing_strings')
    string_no = models.PositiveIntegerField(help_text='Ordinal number from surface downward (1=surface)')

    outside_dia_in = models.DecimalField(max_digits=5, decimal_places=2)
    weight_ppf = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    grade = models.CharField(max_length=32, blank=True)
    thread_type = models.CharField(max_length=64, blank=True)

    top_ft = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    shoe_ft = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    cement_to_ft = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    provenance = models.JSONField(default=dict)
    source = models.CharField(max_length=256, blank=True)
    as_of = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'public_core_public_casing_string'
        unique_together = (
            ('well', 'string_no'),
        )
        indexes = [
            models.Index(fields=['well', 'string_no']),
            models.Index(fields=['well']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"CasingString<{self.well.api14} #{self.string_no} {self.outside_dia_in}\" OD>"


