from django.db import models

from .well_registry import WellRegistry


class PublicArtifacts(models.Model):
    """
    Pointers to regulator-hosted documents (or cached structured extracts).
    Kept in Public Core; avoid storing tenant-uploaded sensitive docs here.
    """

    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE, related_name='public_artifacts')
    kind = models.CharField(max_length=64)  # e.g., 'filing_pdf', 'permit', 'schematic'
    url = models.URLField(max_length=1024)
    title = models.CharField(max_length=256, blank=True)
    mime_type = models.CharField(max_length=128, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict)
    as_of = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'public_core_public_artifacts'
        indexes = [
            models.Index(fields=['well', 'kind']),
            models.Index(fields=['sha256']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"PublicArtifacts<{self.well.api14}:{self.kind}>"


