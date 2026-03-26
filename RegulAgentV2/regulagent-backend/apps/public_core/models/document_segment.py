import uuid
from django.db import models


class DocumentSegment(models.Model):
    """
    Represents a classified segment (page range) within a source PDF.
    Created during document classification, linked to ExtractedDocument after extraction.
    """

    SOURCE_TYPE_CHOICES = [
        ('neubus', 'Neubus'),
        ('nm_ocd', 'NM OCD'),
        ('upload', 'Upload'),
    ]

    CLASSIFICATION_METHOD_CHOICES = [
        ('text', 'Text'),
        ('vision', 'Vision'),
        ('filename', 'Filename'),
        ('hybrid', 'Hybrid'),
    ]

    CONFIDENCE_CHOICES = [
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
        ('none', 'None'),
    ]

    STATUS_CHOICES = [
        ('classified', 'Classified'),
        ('extracting', 'Extracting'),
        ('extracted', 'Extracted'),
        ('error', 'Error'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    well = models.ForeignKey(
        'public_core.WellRegistry',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='document_segments',
    )
    api_number = models.CharField(max_length=16, db_index=True)

    # Source provenance
    source_filename = models.CharField(max_length=255, db_index=True)
    source_path = models.TextField(blank=True, default='')
    file_hash = models.CharField(max_length=64, blank=True, default='')
    source_type = models.CharField(max_length=16, choices=SOURCE_TYPE_CHOICES)

    # Breakpoint range (0-indexed, inclusive)
    page_start = models.PositiveIntegerField()
    page_end = models.PositiveIntegerField()
    total_source_pages = models.PositiveIntegerField(default=0)

    # Classification
    form_type = models.CharField(max_length=32, db_index=True)
    classification_method = models.CharField(max_length=16, choices=CLASSIFICATION_METHOD_CHOICES)
    classification_confidence = models.CharField(max_length=8, choices=CONFIDENCE_CHOICES)
    classification_evidence = models.TextField(blank=True, default='')

    # Tagging
    tags = models.JSONField(default=list, blank=True)

    # Processing status
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='classified')

    # Link to resulting ExtractedDocument
    extracted_document = models.OneToOneField(
        'public_core.ExtractedDocument',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='source_segment',
    )

    # Text cache (avoids re-extracting later)
    raw_text_cache = models.TextField(blank=True, default='')

    attribution_api = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Resolved API14 for this segment based on text analysis",
    )
    attribution_confidence = models.CharField(
        max_length=10,
        default="unresolved",
        choices=[
            ("high", "High"),
            ("medium", "Medium"),
            ("low", "Low"),
            ("unresolved", "Unresolved"),
        ],
        help_text="Confidence of segment-level well attribution",
    )
    attribution_method = models.CharField(
        max_length=32,
        default="unresolved",
        help_text="Method used for segment attribution (e.g., segment_text_api, segment_text_well_no, unresolved)",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'public_core_document_segment'
        ordering = ['source_filename', 'page_start']
        indexes = [
            models.Index(fields=['api_number', 'form_type'], name='docseg_api_formtype_idx'),
            models.Index(fields=['source_filename', 'page_start'], name='docseg_file_page_idx'),
            models.Index(fields=['status'], name='docseg_status_idx'),
            models.Index(fields=['well', 'form_type'], name='docseg_well_formtype_idx'),
        ]

    def __str__(self):
        return f"{self.source_filename} pp.{self.page_start}-{self.page_end} [{self.form_type}]"

    @property
    def page_count(self):
        return self.page_end - self.page_start + 1
