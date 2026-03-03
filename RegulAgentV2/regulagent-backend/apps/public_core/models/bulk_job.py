"""
BulkJob model for tracking asynchronous bulk operations.

Supports various bulk operations on wells and plans:
- Bulk plan generation
- Bulk status updates
- Bulk data exports
"""
from __future__ import annotations

import uuid
from django.db import models
from django.utils import timezone


class BulkJob(models.Model):
    """
    Track status and results of bulk operations.

    Bulk operations (like generating plans for 100 wells) are processed
    asynchronously via Celery. This model tracks progress and results.

    Workflow:
    1. API creates BulkJob with status='queued'
    2. Celery task picks up job, sets status='processing'
    3. Task processes items, updating processed_items/failed_items
    4. Task completes, sets status='completed' or 'failed'
    5. Frontend polls job status endpoint for progress
    """

    # Job types
    JOB_TYPE_GENERATE_PLANS = 'generate_plans'
    JOB_TYPE_UPDATE_STATUS = 'update_status'
    JOB_TYPE_EXPORT_DATA = 'export_data'

    JOB_TYPE_CHOICES = [
        (JOB_TYPE_GENERATE_PLANS, 'Generate Plans'),
        (JOB_TYPE_UPDATE_STATUS, 'Update Status'),
        (JOB_TYPE_EXPORT_DATA, 'Export Data'),
    ]

    # Job status
    STATUS_QUEUED = 'queued'
    STATUS_PROCESSING = 'processing'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_QUEUED, 'Queued'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    # Primary fields
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(
        db_index=True,
        help_text="Tenant who owns this job"
    )

    job_type = models.CharField(
        max_length=50,
        choices=JOB_TYPE_CHOICES,
        db_index=True,
        help_text="Type of bulk operation"
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
        db_index=True,
        help_text="Current job status"
    )

    # Progress tracking
    total_items = models.IntegerField(
        default=0,
        help_text="Total number of items to process"
    )

    processed_items = models.IntegerField(
        default=0,
        help_text="Number of items successfully processed"
    )

    failed_items = models.IntegerField(
        default=0,
        help_text="Number of items that failed"
    )

    # Input/output data
    input_data = models.JSONField(
        default=dict,
        help_text="Input parameters for the job (e.g., well_ids, options)"
    )

    result_data = models.JSONField(
        default=dict,
        help_text="Results of the operation (e.g., created plan IDs, error details)"
    )

    error_message = models.TextField(
        blank=True,
        help_text="Error message if job failed"
    )

    # Metadata
    created_by = models.EmailField(
        help_text="User who initiated the job"
    )

    celery_task_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Celery task ID for tracking"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "public_core_bulk_jobs"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant_id', 'status']),
            models.Index(fields=['tenant_id', 'created_at']),
            models.Index(fields=['job_type', 'status']),
            models.Index(fields=['celery_task_id']),
        ]

    def __str__(self) -> str:
        return f"BulkJob<{self.job_type}:{self.status} ({self.processed_items}/{self.total_items})>"

    @property
    def progress_percentage(self) -> float:
        """Calculate completion percentage."""
        if self.total_items == 0:
            return 0.0
        return (self.processed_items / self.total_items) * 100

    @property
    def estimated_time_remaining_seconds(self) -> int:
        """
        Estimate remaining time based on current progress.

        Returns estimated seconds, or -1 if cannot estimate.
        """
        if not self.started_at or self.processed_items == 0:
            return -1

        elapsed = (timezone.now() - self.started_at).total_seconds()
        items_remaining = self.total_items - self.processed_items

        if items_remaining <= 0:
            return 0

        avg_time_per_item = elapsed / self.processed_items
        return int(avg_time_per_item * items_remaining)

    def start_processing(self):
        """Mark job as processing."""
        self.status = self.STATUS_PROCESSING
        self.started_at = timezone.now()
        self.save(update_fields=['status', 'started_at'])

    def complete_successfully(self):
        """Mark job as completed."""
        self.status = self.STATUS_COMPLETED
        self.completed_at = timezone.now()
        self.save(update_fields=['status', 'completed_at'])

    def fail(self, error_message: str):
        """Mark job as failed."""
        self.status = self.STATUS_FAILED
        self.error_message = error_message
        self.completed_at = timezone.now()
        self.save(update_fields=['status', 'error_message', 'completed_at'])

    def increment_progress(self, success: bool = True):
        """
        Increment progress counter.

        Args:
            success: True if item succeeded, False if failed
        """
        if success:
            self.processed_items += 1
        else:
            self.failed_items += 1
        self.save(update_fields=['processed_items', 'failed_items'])
