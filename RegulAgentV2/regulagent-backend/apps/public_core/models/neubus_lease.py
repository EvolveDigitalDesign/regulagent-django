from django.db import models


class NeubusLease(models.Model):
    """
    Represents a lease record from the Neubus TX RRC document archive.
    One lease may contain documents for multiple wells.
    """
    lease_id = models.CharField(max_length=32, unique=True, db_index=True)
    field_name = models.CharField(max_length=128, blank=True)
    lease_name = models.CharField(max_length=128, blank=True)
    operator = models.CharField(max_length=128, blank=True)
    county = models.CharField(max_length=64, blank=True)
    district = models.CharField(max_length=8, blank=True)
    neubus_record_ids = models.JSONField(default=list,
        help_text="List of Neubus record IDs associated with this lease")
    last_checked = models.DateField(null=True, blank=True)
    max_upload_date = models.CharField(max_length=64, blank=True,
        help_text="Most recent upload date string from Neubus")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'public_core_neubus_lease'

    def __str__(self):
        return f"NeubusLease<{self.lease_id}: {self.lease_name}>"


class NeubusDocument(models.Model):
    """
    Represents a single document (PDF) from the Neubus archive.
    Linked to a lease. Tracks classification and extraction status.
    """
    CLASSIFICATION_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('complete', 'Complete'),
        ('chunked', 'Chunked'),
        ('error', 'Error'),
    ]
    EXTRACTION_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('complete', 'Complete'),
        ('error', 'Error'),
    ]

    lease = models.ForeignKey(NeubusLease, related_name='documents', on_delete=models.CASCADE)
    neubus_filename = models.CharField(max_length=255, unique=True, db_index=True)
    well_number = models.CharField(max_length=32, blank=True)
    api = models.CharField(max_length=20, blank=True, db_index=True)
    pages = models.PositiveIntegerField(default=0)
    form_types_by_page = models.JSONField(default=dict,
        help_text='Map of form type to page numbers, e.g. {"W3": [1], "W-15": [2,3]}')
    uploaded_on = models.CharField(max_length=64, blank=True,
        help_text="Upload date string from Neubus")
    date_ingested = models.DateField(auto_now_add=True)
    file_hash = models.CharField(max_length=64, blank=True)
    classification_status = models.CharField(
        max_length=16, choices=CLASSIFICATION_STATUS_CHOICES, default='pending')
    extraction_status = models.CharField(
        max_length=16, choices=EXTRACTION_STATUS_CHOICES, default='pending')
    triage_confidence = models.CharField(max_length=16, blank=True, default="",
        help_text="Confidence of API triage: high, medium, low, unidentified")
    triage_pages_scanned = models.PositiveIntegerField(default=0,
        help_text="Number of pages scanned during triage")
    local_path = models.TextField(blank=True,
        help_text="Path to the file in cold storage")
    parent_document = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.CASCADE,
        related_name='chunks',
        help_text="If this is a chunk, points to the original full document")
    part_number = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="1-based chunk index (None = not a chunk)")
    part_total = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Total number of chunks (None = not a chunk)")

    class Meta:
        db_table = 'public_core_neubus_document'
        indexes = [
            models.Index(fields=['api']),
            models.Index(fields=['classification_status']),
            models.Index(fields=['extraction_status']),
        ]

    def __str__(self):
        return f"NeubusDocument<{self.neubus_filename}>"
