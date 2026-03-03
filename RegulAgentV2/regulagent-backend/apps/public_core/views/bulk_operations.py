"""
Bulk operations API endpoints.

Allows efficient batch processing of wells and plans:
- Bulk plan generation
- Bulk status updates
- Job status tracking
"""
import logging
from typing import Optional

from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.public_core.models import BulkJob, PlanSnapshot
from apps.public_core.tasks import bulk_generate_plans, bulk_update_plan_status

logger = logging.getLogger(__name__)


@api_view(['POST'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def bulk_generate_plans_view(request):
    """
    Generate plans for multiple wells asynchronously.

    POST /api/wells/bulk/generate-plans/

    Request:
        {
            "well_ids": ["4230132998", "4230132999", ...],
            "options": {
                "jurisdiction": "TX",  # Optional override
                "force_regenerate": false,
                "plugs_mode": "combined",  # "combined", "isolated", "both"
                "input_mode": "extractions"  # "extractions", "user_files", "hybrid"
            }
        }

    Response:
        {
            "job_id": "uuid",
            "status": "queued",
            "total_wells": 10,
            "message": "Bulk plan generation job queued"
        }
    """
    user_tenant = request.user.tenants.first()
    if not user_tenant:
        return Response(
            {"error": "User not associated with any tenant"},
            status=status.HTTP_403_FORBIDDEN
        )

    # Validate request
    well_ids = request.data.get('well_ids', [])
    options = request.data.get('options', {})

    if not well_ids or not isinstance(well_ids, list):
        return Response(
            {"error": "well_ids must be a non-empty list"},
            status=status.HTTP_400_BAD_REQUEST
        )

    if len(well_ids) > 1000:
        return Response(
            {"error": "Maximum 1000 wells per bulk operation"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Create BulkJob record
    job = BulkJob.objects.create(
        tenant_id=user_tenant.id,
        job_type=BulkJob.JOB_TYPE_GENERATE_PLANS,
        status=BulkJob.STATUS_QUEUED,
        total_items=len(well_ids),
        input_data={
            'well_ids': well_ids,
            'options': options
        },
        created_by=request.user.email
    )

    logger.info(
        f"Created bulk plan generation job {job.id} for {len(well_ids)} wells "
        f"by user {request.user.email}"
    )

    # Queue Celery task
    task = bulk_generate_plans.delay(
        job_id=str(job.id),
        well_ids=well_ids,
        options=options
    )

    logger.info(f"Queued Celery task {task.id} for job {job.id}")

    return Response({
        "job_id": str(job.id),
        "status": job.status,
        "total_wells": len(well_ids),
        "message": f"Bulk plan generation job queued for {len(well_ids)} wells",
        "estimated_time_seconds": len(well_ids) * 10  # Rough estimate: 10s per well
    }, status=status.HTTP_202_ACCEPTED)


@api_view(['POST'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def bulk_update_plan_status_view(request):
    """
    Update status for multiple plans.

    POST /api/plans/bulk/update-status/

    Request:
        {
            "plan_ids": ["4230132998:isolated", "4230132999:combined", ...],
            "new_status": "engineer_approved"
        }

    Response:
        {
            "job_id": "uuid",
            "status": "queued",
            "total_plans": 5,
            "message": "Bulk status update job queued"
        }
    """
    user_tenant = request.user.tenants.first()
    if not user_tenant:
        return Response(
            {"error": "User not associated with any tenant"},
            status=status.HTTP_403_FORBIDDEN
        )

    # Validate request
    plan_ids = request.data.get('plan_ids', [])
    new_status = request.data.get('new_status')

    if not plan_ids or not isinstance(plan_ids, list):
        return Response(
            {"error": "plan_ids must be a non-empty list"},
            status=status.HTTP_400_BAD_REQUEST
        )

    if len(plan_ids) > 1000:
        return Response(
            {"error": "Maximum 1000 plans per bulk operation"},
            status=status.HTTP_400_BAD_REQUEST
        )

    if not new_status:
        return Response(
            {"error": "new_status is required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Validate status value
    valid_statuses = [choice[0] for choice in PlanSnapshot.STATUS_CHOICES]
    if new_status not in valid_statuses:
        return Response(
            {
                "error": f"Invalid status: {new_status}",
                "valid_statuses": valid_statuses
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    # Create BulkJob record
    job = BulkJob.objects.create(
        tenant_id=user_tenant.id,
        job_type=BulkJob.JOB_TYPE_UPDATE_STATUS,
        status=BulkJob.STATUS_QUEUED,
        total_items=len(plan_ids),
        input_data={
            'plan_ids': plan_ids,
            'new_status': new_status
        },
        created_by=request.user.email
    )

    logger.info(
        f"Created bulk status update job {job.id} for {len(plan_ids)} plans "
        f"to status {new_status} by user {request.user.email}"
    )

    # Queue Celery task
    task = bulk_update_plan_status.delay(
        job_id=str(job.id),
        plan_ids=plan_ids,
        new_status=new_status
    )

    logger.info(f"Queued Celery task {task.id} for job {job.id}")

    return Response({
        "job_id": str(job.id),
        "status": job.status,
        "total_plans": len(plan_ids),
        "new_status": new_status,
        "message": f"Bulk status update job queued for {len(plan_ids)} plans"
    }, status=status.HTTP_202_ACCEPTED)


@api_view(['GET'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def get_bulk_job_status(request, job_id):
    """
    Get status and progress of a bulk job.

    GET /api/jobs/{job_id}/

    Response:
        {
            "job_id": "uuid",
            "job_type": "generate_plans",
            "status": "processing",
            "total_items": 100,
            "processed_items": 45,
            "failed_items": 2,
            "progress_percentage": 47.0,
            "estimated_time_remaining_seconds": 120,
            "created_at": "2024-01-01T12:00:00Z",
            "started_at": "2024-01-01T12:00:05Z",
            "completed_at": null,
            "result_data": {...},  # Only if completed
            "error_message": ""
        }
    """
    user_tenant = request.user.tenants.first()
    if not user_tenant:
        return Response(
            {"error": "User not associated with any tenant"},
            status=status.HTTP_403_FORBIDDEN
        )

    try:
        job = BulkJob.objects.get(id=job_id, tenant_id=user_tenant.id)
    except BulkJob.DoesNotExist:
        return Response(
            {"error": f"Job {job_id} not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    response_data = {
        "job_id": str(job.id),
        "job_type": job.job_type,
        "status": job.status,
        "total_items": job.total_items,
        "processed_items": job.processed_items,
        "failed_items": job.failed_items,
        "progress_percentage": job.progress_percentage,
        "estimated_time_remaining_seconds": job.estimated_time_remaining_seconds,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "created_by": job.created_by,
    }

    # Include results if completed
    if job.status in [BulkJob.STATUS_COMPLETED, BulkJob.STATUS_FAILED]:
        response_data["result_data"] = job.result_data
        response_data["error_message"] = job.error_message

    logger.info(f"Job {job_id} status retrieved: {job.status} ({job.progress_percentage:.1f}%)")

    return Response(response_data, status=status.HTTP_200_OK)


@api_view(['GET'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def list_bulk_jobs(request):
    """
    List all bulk jobs for the tenant.

    GET /api/jobs/

    Query params:
        - job_type: Filter by job type (optional)
        - status: Filter by status (optional)
        - limit: Max results (default 50, max 200)

    Response:
        {
            "jobs": [
                {
                    "job_id": "uuid",
                    "job_type": "generate_plans",
                    "status": "completed",
                    "total_items": 10,
                    "processed_items": 10,
                    "failed_items": 0,
                    "created_at": "...",
                    "completed_at": "..."
                },
                ...
            ],
            "total": 42
        }
    """
    user_tenant = request.user.tenants.first()
    if not user_tenant:
        return Response(
            {"error": "User not associated with any tenant"},
            status=status.HTTP_403_FORBIDDEN
        )

    # Build query
    jobs_query = BulkJob.objects.filter(tenant_id=user_tenant.id)

    # Apply filters
    job_type = request.query_params.get('job_type')
    if job_type:
        jobs_query = jobs_query.filter(job_type=job_type)

    job_status = request.query_params.get('status')
    if job_status:
        jobs_query = jobs_query.filter(status=job_status)

    # Limit results
    limit = min(int(request.query_params.get('limit', 50)), 200)
    total = jobs_query.count()
    jobs = jobs_query[:limit]

    # Serialize
    jobs_data = []
    for job in jobs:
        jobs_data.append({
            "job_id": str(job.id),
            "job_type": job.job_type,
            "status": job.status,
            "total_items": job.total_items,
            "processed_items": job.processed_items,
            "failed_items": job.failed_items,
            "progress_percentage": job.progress_percentage,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "created_by": job.created_by,
        })

    return Response({
        "jobs": jobs_data,
        "total": total
    }, status=status.HTTP_200_OK)
