"""
Tests for BulkJob model (without authentication/tenant complexities).

These tests verify the BulkJob model functionality directly without
dealing with multi-tenant test database setup issues.
"""
import uuid
import pytest
from django.utils import timezone
from datetime import timedelta

from apps.public_core.models import BulkJob


@pytest.mark.django_db
class TestBulkJobModel:
    """Test BulkJob model methods and properties."""

    def test_create_bulk_job(self):
        """Test creating a basic BulkJob."""
        tenant_id = uuid.uuid4()
        job = BulkJob.objects.create(
            tenant_id=tenant_id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_QUEUED,
            total_items=10,
            created_by='test@example.com'
        )

        assert job.id is not None
        assert job.tenant_id == tenant_id
        assert job.job_type == BulkJob.JOB_TYPE_GENERATE_PLANS
        assert job.status == BulkJob.STATUS_QUEUED
        assert job.total_items == 10
        assert job.processed_items == 0
        assert job.failed_items == 0

    def test_progress_percentage_calculation(self):
        """Test progress percentage calculation."""
        job = BulkJob(total_items=100, processed_items=50)
        assert job.progress_percentage == 50.0

        job.processed_items = 0
        assert job.progress_percentage == 0.0

        job.processed_items = 100
        assert job.progress_percentage == 100.0

    def test_progress_percentage_zero_items(self):
        """Test progress percentage with zero total items."""
        job = BulkJob(total_items=0, processed_items=0)
        assert job.progress_percentage == 0.0

    def test_start_processing(self):
        """Test starting job processing."""
        tenant_id = uuid.uuid4()
        job = BulkJob.objects.create(
            tenant_id=tenant_id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_QUEUED,
            total_items=10,
            created_by='test@example.com'
        )

        assert job.status == BulkJob.STATUS_QUEUED
        assert job.started_at is None

        job.start_processing()
        job.refresh_from_db()

        assert job.status == BulkJob.STATUS_PROCESSING
        assert job.started_at is not None

    def test_complete_successfully(self):
        """Test completing job successfully."""
        tenant_id = uuid.uuid4()
        job = BulkJob.objects.create(
            tenant_id=tenant_id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_PROCESSING,
            total_items=10,
            created_by='test@example.com'
        )
        job.start_processing()

        assert job.completed_at is None

        job.complete_successfully()
        job.refresh_from_db()

        assert job.status == BulkJob.STATUS_COMPLETED
        assert job.completed_at is not None

    def test_fail_job(self):
        """Test failing a job with error message."""
        tenant_id = uuid.uuid4()
        job = BulkJob.objects.create(
            tenant_id=tenant_id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_PROCESSING,
            total_items=10,
            created_by='test@example.com'
        )

        error_msg = 'Test error occurred'
        job.fail(error_msg)
        job.refresh_from_db()

        assert job.status == BulkJob.STATUS_FAILED
        assert job.error_message == error_msg
        assert job.completed_at is not None

    def test_increment_progress_success(self):
        """Test incrementing progress on success."""
        tenant_id = uuid.uuid4()
        job = BulkJob.objects.create(
            tenant_id=tenant_id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_PROCESSING,
            total_items=10,
            created_by='test@example.com'
        )

        assert job.processed_items == 0
        assert job.failed_items == 0

        job.increment_progress(success=True)
        job.refresh_from_db()

        assert job.processed_items == 1
        assert job.failed_items == 0

    def test_increment_progress_failure(self):
        """Test incrementing progress on failure."""
        tenant_id = uuid.uuid4()
        job = BulkJob.objects.create(
            tenant_id=tenant_id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_PROCESSING,
            total_items=10,
            created_by='test@example.com'
        )

        job.increment_progress(success=False)
        job.refresh_from_db()

        assert job.processed_items == 0
        assert job.failed_items == 1

    def test_estimated_time_remaining(self):
        """Test estimated time remaining calculation."""
        tenant_id = uuid.uuid4()
        job = BulkJob.objects.create(
            tenant_id=tenant_id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_PROCESSING,
            total_items=100,
            processed_items=50,
            created_by='test@example.com'
        )

        # Set started_at to 10 seconds ago
        job.started_at = timezone.now() - timedelta(seconds=10)
        job.save()

        # 50 items in 10 seconds = 0.2 seconds per item
        # 50 items remaining * 0.2 = 10 seconds
        estimated = job.estimated_time_remaining_seconds
        assert estimated > 0
        assert estimated <= 12  # Allow some margin for timing

    def test_estimated_time_remaining_no_progress(self):
        """Test estimated time when no items processed yet."""
        tenant_id = uuid.uuid4()
        job = BulkJob.objects.create(
            tenant_id=tenant_id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_PROCESSING,
            total_items=100,
            processed_items=0,
            created_by='test@example.com'
        )

        assert job.estimated_time_remaining_seconds == -1

    def test_input_and_result_data(self):
        """Test storing input and result data as JSON."""
        tenant_id = uuid.uuid4()
        input_data = {
            'well_ids': ['123', '456'],
            'options': {'force': True}
        }
        result_data = {
            'results': [
                {'well_id': '123', 'status': 'success'},
                {'well_id': '456', 'status': 'failed', 'error': 'Not found'}
            ]
        }

        job = BulkJob.objects.create(
            tenant_id=tenant_id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_COMPLETED,
            total_items=2,
            input_data=input_data,
            result_data=result_data,
            created_by='test@example.com'
        )

        job.refresh_from_db()

        assert job.input_data == input_data
        assert job.result_data == result_data
        assert job.result_data['results'][0]['status'] == 'success'

    def test_job_type_choices(self):
        """Test all job type choices are valid."""
        tenant_id = uuid.uuid4()

        for job_type, label in BulkJob.JOB_TYPE_CHOICES:
            job = BulkJob.objects.create(
                tenant_id=tenant_id,
                job_type=job_type,
                status=BulkJob.STATUS_QUEUED,
                total_items=1,
                created_by='test@example.com'
            )
            assert job.job_type == job_type

    def test_status_choices(self):
        """Test all status choices are valid."""
        tenant_id = uuid.uuid4()

        for status_value, label in BulkJob.STATUS_CHOICES:
            job = BulkJob.objects.create(
                tenant_id=tenant_id,
                job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
                status=status_value,
                total_items=1,
                created_by='test@example.com'
            )
            assert job.status == status_value

    def test_str_representation(self):
        """Test string representation of BulkJob."""
        tenant_id = uuid.uuid4()
        job = BulkJob.objects.create(
            tenant_id=tenant_id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_PROCESSING,
            total_items=10,
            processed_items=5,
            created_by='test@example.com'
        )

        str_repr = str(job)
        assert 'generate_plans' in str_repr
        assert 'processing' in str_repr
        assert '5/10' in str_repr
