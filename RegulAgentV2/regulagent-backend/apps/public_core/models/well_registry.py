from django.db import models


class WellRegistry(models.Model):
    """
    Global registry entry for a physical well.
    Public identity only: API14, jurisdiction, and location.
    """

    api14 = models.CharField(max_length=20, unique=True)
    state = models.CharField(max_length=2)
    county = models.CharField(max_length=64, blank=True)
    district = models.CharField(max_length=8, blank=True, help_text="RRC District (e.g., '8A', '7C')")
    operator_name = models.CharField(max_length=128, blank=True)
    field_name = models.CharField(max_length=128, blank=True)
    lease_name = models.CharField(max_length=128, blank=True)
    well_number = models.CharField(max_length=32, blank=True)
    lease_id = models.CharField(max_length=32, blank=True, db_index=True,
        help_text="Neubus lease ID for TX wells")

    DATA_STATUS_CHOICES = [
        ("cold_storage", "Cold Storage"),
        ("indexing", "Indexing"),
        ("ready", "Ready"),
    ]
    data_status = models.CharField(
        max_length=16, choices=DATA_STATUS_CHOICES, default="cold_storage",
        db_index=True,
        help_text="Data pipeline status: cold_storage (PDFs only), indexing (in progress), ready (fully extracted)"
    )

    # Client workspace assignment (optional for backward compatibility)
    workspace = models.ForeignKey(
        'tenants.ClientWorkspace',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='wells',
        help_text="Client workspace this well belongs to (optional)",
        db_index=True
    )

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
            models.Index(fields=['district']),  # Critical for district-specific compliance queries
            models.Index(fields=['operator_name']),
            models.Index(fields=['field_name']),
            models.Index(fields=['workspace']),  # For filtering by client workspace
            models.Index(fields=['data_status']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"Well {self.api14} ({self.state})"


