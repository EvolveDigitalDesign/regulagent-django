import uuid
from django.db import models


class WellTimelineEvent(models.Model):
    """
    A single event in the chronological "life of well" timeline.
    Built from ExtractedDocuments — shows what was done when, by whom.
    """

    EVENT_TYPE_CHOICES = [
        ('drilling', 'Drilling'),
        ('completion', 'Completion'),
        ('workover', 'Workover'),
        ('recompletion', 'Recompletion'),
        ('plugging', 'Plugging'),
        ('cement_job', 'Cement Job'),
        ('permit', 'Permit'),
        ('plugging_proposal', 'Plugging Proposal'),
        ('test', 'Test'),
        ('other', 'Other'),
    ]

    DATE_PRECISION_CHOICES = [
        ('day', 'Day'),
        ('month', 'Month'),
        ('year', 'Year'),
        ('unknown', 'Unknown'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    well = models.ForeignKey(
        'public_core.WellRegistry',
        on_delete=models.CASCADE,
        related_name='timeline_events',
    )

    # Temporal
    event_date = models.DateField(null=True, blank=True)
    event_date_precision = models.CharField(
        max_length=8, choices=DATE_PRECISION_CHOICES, default='unknown'
    )

    # Classification
    event_type = models.CharField(max_length=32, choices=EVENT_TYPE_CHOICES)

    # Content
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    key_data = models.JSONField(default=dict, blank=True)

    # Provenance
    source_document = models.ForeignKey(
        'public_core.ExtractedDocument',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='timeline_events',
    )
    source_segment = models.ForeignKey(
        'public_core.DocumentSegment',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='timeline_events',
    )
    source_document_type = models.CharField(max_length=32, blank=True, default='')

    # Component links
    components_installed = models.ManyToManyField(
        'public_core.WellComponent',
        blank=True,
        related_name='installed_by_events',
    )
    components_removed = models.ManyToManyField(
        'public_core.WellComponent',
        blank=True,
        related_name='removed_by_events',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'public_core_well_timeline_event'
        ordering = ['event_date', 'created_at']
        indexes = [
            models.Index(fields=['well', 'event_date'], name='timeline_well_date_idx'),
            models.Index(fields=['well', 'event_type'], name='timeline_well_type_idx'),
        ]

    def __str__(self):
        date_str = self.event_date.isoformat() if self.event_date else "unknown date"
        return f"{self.title} ({date_str})"
