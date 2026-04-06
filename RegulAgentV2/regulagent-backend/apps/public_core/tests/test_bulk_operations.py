"""
Tests for bulk operations API.

Tests:
- Bulk plan generation endpoint
- Bulk status update endpoint
- Job status tracking endpoint
- Job list endpoint
- Celery task execution
"""
import uuid
from unittest.mock import patch, MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from rest_framework import status

from apps.public_core.models import BulkJob, PlanSnapshot, WellRegistry
from apps.tenants.models import Tenant

User = get_user_model()


@pytest.fixture
def test_tenant(db):
    """Create a test tenant."""
    tenant = Tenant.objects.create(
        schema_name='test_tenant',
        name='Test Tenant',
        paid_until='2025-12-31',
        on_trial=False
    )
    return tenant


@pytest.fixture
def test_user(db, test_tenant):
    """Create a test user associated with tenant."""
    user = User.objects.create_user(
        email='test@example.com',
        password='testpass123'
    )
    user.tenants.add(test_tenant)
    return user


@pytest.fixture
def authenticated_client(test_user):
    """Create an authenticated client."""
    client = Client()
    client.force_login(test_user)
    return client


@pytest.fixture
def test_wells(db):
    """Create test wells."""
    wells = []
    for i in range(5):
        well = WellRegistry.objects.create(
            api14=f'4230100000{i}',
            state='TX',
            county='Midland',
            operator_name='Test Operator',
            field_name='Test Field',
        )
        wells.append(well)
    return wells


@pytest.mark.django_db
class TestBulkGeneratePlans:
    """Test bulk plan generation endpoint."""

    def test_bulk_generate_plans_success(self, authenticated_client, test_user, test_tenant, test_wells):
        """Test successful bulk plan generation job creation."""
        well_ids = [well.api14 for well in test_wells[:3]]

        with patch('apps.public_core.views.bulk_operations.bulk_generate_plans.delay') as mock_task:
            mock_task.return_value = MagicMock(id='test-task-id')

            response = authenticated_client.post(
                '/api/wells/bulk/generate-plans/',
                data={
                    'well_ids': well_ids,
                    'options': {
                        'plugs_mode': 'combined',
                        'force_regenerate': False
                    }
                },
                content_type='application/json'
            )

        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()

        # Verify response structure
        assert 'job_id' in data
        assert data['status'] == 'queued'
        assert data['total_wells'] == 3

        # Verify job was created
        job = BulkJob.objects.get(id=data['job_id'])
        assert job.tenant_id == test_tenant.id
        assert job.job_type == BulkJob.JOB_TYPE_GENERATE_PLANS
        assert job.total_items == 3
        assert job.created_by == test_user.email

        # Verify Celery task was called
        mock_task.assert_called_once()

    def test_bulk_generate_plans_empty_list(self, authenticated_client):
        """Test error when well_ids is empty."""
        response = authenticated_client.post(
            '/api/wells/bulk/generate-plans/',
            data={'well_ids': []},
            content_type='application/json'
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'error' in response.json()

    def test_bulk_generate_plans_too_many_wells(self, authenticated_client):
        """Test error when too many wells requested."""
        well_ids = [f'42301000{i:03d}' for i in range(1001)]

        response = authenticated_client.post(
            '/api/wells/bulk/generate-plans/',
            data={'well_ids': well_ids},
            content_type='application/json'
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'Maximum 1000 wells' in response.json()['error']

    def test_bulk_generate_plans_no_tenant(self, db):
        """Test error when user has no tenant."""
        user = User.objects.create_user(
            email='notenant@example.com',
            password='testpass123'
        )
        client = Client()
        client.force_login(user)

        response = client.post(
            '/api/wells/bulk/generate-plans/',
            data={'well_ids': ['4230100001']},
            content_type='application/json'
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestBulkUpdatePlanStatus:
    """Test bulk status update endpoint."""

    def test_bulk_update_status_success(self, authenticated_client, test_user, test_tenant, test_wells):
        """Test successful bulk status update job creation."""
        # Create some plans
        plan_ids = []
        for well in test_wells[:2]:
            plan = PlanSnapshot.objects.create(
                well=well,
                plan_id=f'{well.api14}:isolated',
                kind=PlanSnapshot.KIND_BASELINE,
                payload={'steps': []},
                status=PlanSnapshot.STATUS_DRAFT,
                tenant_id=test_tenant.id
            )
            plan_ids.append(plan.plan_id)

        with patch('apps.public_core.views.bulk_operations.bulk_update_plan_status.delay') as mock_task:
            mock_task.return_value = MagicMock(id='test-task-id')

            response = authenticated_client.post(
                '/api/plans/bulk/update-status/',
                data={
                    'plan_ids': plan_ids,
                    'new_status': 'internal_review'
                },
                content_type='application/json'
            )

        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()

        # Verify response
        assert 'job_id' in data
        assert data['status'] == 'queued'
        assert data['total_plans'] == 2
        assert data['new_status'] == 'internal_review'

        # Verify job was created
        job = BulkJob.objects.get(id=data['job_id'])
        assert job.job_type == BulkJob.JOB_TYPE_UPDATE_STATUS
        assert job.total_items == 2

    def test_bulk_update_status_invalid_status(self, authenticated_client):
        """Test error with invalid status."""
        response = authenticated_client.post(
            '/api/plans/bulk/update-status/',
            data={
                'plan_ids': ['plan1', 'plan2'],
                'new_status': 'invalid_status'
            },
            content_type='application/json'
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'Invalid status' in response.json()['error']

    def test_bulk_update_status_missing_status(self, authenticated_client):
        """Test error when new_status is missing."""
        response = authenticated_client.post(
            '/api/plans/bulk/update-status/',
            data={'plan_ids': ['plan1']},
            content_type='application/json'
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'new_status is required' in response.json()['error']


@pytest.mark.django_db
class TestBulkJobStatus:
    """Test job status endpoint."""

    def test_get_job_status(self, authenticated_client, test_tenant):
        """Test retrieving job status."""
        job = BulkJob.objects.create(
            tenant_id=test_tenant.id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_PROCESSING,
            total_items=10,
            processed_items=5,
            failed_items=1,
            created_by='test@example.com'
        )

        response = authenticated_client.get(f'/api/jobs/{job.id}/')

        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        assert data['job_id'] == str(job.id)
        assert data['job_type'] == BulkJob.JOB_TYPE_GENERATE_PLANS
        assert data['status'] == BulkJob.STATUS_PROCESSING
        assert data['total_items'] == 10
        assert data['processed_items'] == 5
        assert data['failed_items'] == 1
        assert data['progress_percentage'] == 50.0

    def test_get_job_status_not_found(self, authenticated_client):
        """Test error when job not found."""
        fake_id = uuid.uuid4()
        response = authenticated_client.get(f'/api/jobs/{fake_id}/')

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_job_status_completed_includes_results(self, authenticated_client, test_tenant):
        """Test completed job includes result data."""
        job = BulkJob.objects.create(
            tenant_id=test_tenant.id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_COMPLETED,
            total_items=2,
            processed_items=2,
            result_data={'results': [{'well_id': '123', 'status': 'success'}]},
            created_by='test@example.com'
        )

        response = authenticated_client.get(f'/api/jobs/{job.id}/')

        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        assert 'result_data' in data
        assert data['result_data']['results'][0]['well_id'] == '123'


@pytest.mark.django_db
class TestListBulkJobs:
    """Test job list endpoint."""

    def test_list_jobs(self, authenticated_client, test_tenant):
        """Test listing all jobs for tenant."""
        # Create several jobs
        for i in range(3):
            BulkJob.objects.create(
                tenant_id=test_tenant.id,
                job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
                status=BulkJob.STATUS_COMPLETED,
                total_items=10,
                created_by='test@example.com'
            )

        response = authenticated_client.get('/api/jobs/')

        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        assert 'jobs' in data
        assert 'total' in data
        assert data['total'] == 3
        assert len(data['jobs']) == 3

    def test_list_jobs_filter_by_type(self, authenticated_client, test_tenant):
        """Test filtering jobs by type."""
        BulkJob.objects.create(
            tenant_id=test_tenant.id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_COMPLETED,
            total_items=10,
            created_by='test@example.com'
        )
        BulkJob.objects.create(
            tenant_id=test_tenant.id,
            job_type=BulkJob.JOB_TYPE_UPDATE_STATUS,
            status=BulkJob.STATUS_COMPLETED,
            total_items=5,
            created_by='test@example.com'
        )

        response = authenticated_client.get('/api/jobs/?job_type=generate_plans')

        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        assert data['total'] == 1
        assert data['jobs'][0]['job_type'] == BulkJob.JOB_TYPE_GENERATE_PLANS

    def test_list_jobs_filter_by_status(self, authenticated_client, test_tenant):
        """Test filtering jobs by status."""
        BulkJob.objects.create(
            tenant_id=test_tenant.id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_COMPLETED,
            total_items=10,
            created_by='test@example.com'
        )
        BulkJob.objects.create(
            tenant_id=test_tenant.id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_PROCESSING,
            total_items=10,
            created_by='test@example.com'
        )

        response = authenticated_client.get('/api/jobs/?status=processing')

        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        assert data['total'] == 1
        assert data['jobs'][0]['status'] == BulkJob.STATUS_PROCESSING

    def test_list_jobs_limit(self, authenticated_client, test_tenant):
        """Test limiting number of results."""
        for i in range(10):
            BulkJob.objects.create(
                tenant_id=test_tenant.id,
                job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
                status=BulkJob.STATUS_COMPLETED,
                total_items=10,
                created_by='test@example.com'
            )

        response = authenticated_client.get('/api/jobs/?limit=5')

        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        assert data['total'] == 10
        assert len(data['jobs']) == 5


@pytest.mark.django_db
class TestBulkJobModel:
    """Test BulkJob model methods."""

    def test_progress_percentage(self):
        """Test progress percentage calculation."""
        job = BulkJob(total_items=100, processed_items=50)
        assert job.progress_percentage == 50.0

        job.processed_items = 0
        assert job.progress_percentage == 0.0

    def test_start_processing(self, test_tenant):
        """Test starting job processing."""
        job = BulkJob.objects.create(
            tenant_id=test_tenant.id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_QUEUED,
            total_items=10,
            created_by='test@example.com'
        )

        job.start_processing()

        assert job.status == BulkJob.STATUS_PROCESSING
        assert job.started_at is not None

    def test_complete_successfully(self, test_tenant):
        """Test completing job successfully."""
        job = BulkJob.objects.create(
            tenant_id=test_tenant.id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_PROCESSING,
            total_items=10,
            created_by='test@example.com'
        )

        job.complete_successfully()

        assert job.status == BulkJob.STATUS_COMPLETED
        assert job.completed_at is not None

    def test_fail(self, test_tenant):
        """Test failing job."""
        job = BulkJob.objects.create(
            tenant_id=test_tenant.id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_PROCESSING,
            total_items=10,
            created_by='test@example.com'
        )

        job.fail('Test error message')

        assert job.status == BulkJob.STATUS_FAILED
        assert job.error_message == 'Test error message'
        assert job.completed_at is not None

    def test_increment_progress(self, test_tenant):
        """Test incrementing progress."""
        job = BulkJob.objects.create(
            tenant_id=test_tenant.id,
            job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
            status=BulkJob.STATUS_PROCESSING,
            total_items=10,
            created_by='test@example.com'
        )

        job.increment_progress(success=True)
        assert job.processed_items == 1
        assert job.failed_items == 0

        job.increment_progress(success=False)
        assert job.processed_items == 1
        assert job.failed_items == 1
