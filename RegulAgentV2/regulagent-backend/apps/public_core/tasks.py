"""
Celery tasks for asynchronous bulk operations on wells and plans.

These tasks handle long-running operations that would timeout in HTTP requests:
- Bulk plan generation
- Bulk status updates
- Bulk data exports
"""
import logging
from typing import Dict, List, Any
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def bulk_generate_plans(
    self,
    job_id: str,
    well_ids: List[str],
    options: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Generate plans for multiple wells asynchronously.

    This task:
    1. Iterates through each well_id
    2. Calls the plan generation orchestrator
    3. Updates BulkJob progress after each well
    4. Collects results and errors

    Args:
        job_id: BulkJob UUID
        well_ids: List of API14 well identifiers
        options: Configuration options
            - jurisdiction: Optional jurisdiction override
            - force_regenerate: Force regeneration even if plan exists
            - plugs_mode: "combined", "isolated", or "both"
            - input_mode: "extractions", "user_files", or "hybrid"

    Returns:
        {
            'status': 'success' | 'failed',
            'processed': int,
            'failed': int,
            'results': [
                {
                    'well_id': str,
                    'status': 'success' | 'failed',
                    'plan_id': str (if success),
                    'snapshot_id': str (if success),
                    'error': str (if failed)
                }
            ]
        }
    """
    from apps.public_core.models import BulkJob, WellRegistry
    from apps.public_core.services.w3a_orchestrator import generate_w3a_for_api

    logger.info(f"[BulkTask] Starting bulk_generate_plans for job {job_id}")

    try:
        # Get job and mark as processing
        job = BulkJob.objects.get(id=job_id)
        job.start_processing()
        job.celery_task_id = self.request.id
        job.save(update_fields=['celery_task_id'])

        logger.info(f"[BulkTask] Job {job_id} marked as processing. Wells to process: {len(well_ids)}")

        results = []
        processed_count = 0
        failed_count = 0

        # Extract options
        jurisdiction = options.get('jurisdiction')
        force_regenerate = options.get('force_regenerate', False)
        plugs_mode = options.get('plugs_mode', 'combined')
        input_mode = options.get('input_mode', 'extractions')

        for well_id in well_ids:
            try:
                logger.info(f"[BulkTask] Processing well {well_id} ({processed_count + failed_count + 1}/{len(well_ids)})")

                # Validate well exists
                try:
                    well = WellRegistry.objects.get(api14=well_id)
                except WellRegistry.DoesNotExist:
                    raise ValueError(f"Well {well_id} not found in registry")

                # Check if plan already exists (unless force_regenerate)
                from apps.public_core.models import PlanSnapshot
                existing_plan = None
                if not force_regenerate:
                    existing_plan = PlanSnapshot.objects.filter(
                        well=well,
                        tenant_id=job.tenant_id,
                        status__in=[
                            PlanSnapshot.STATUS_DRAFT,
                            PlanSnapshot.STATUS_INTERNAL_REVIEW,
                            PlanSnapshot.STATUS_ENGINEER_APPROVED,
                        ]
                    ).first()

                if existing_plan and not force_regenerate:
                    logger.info(f"[BulkTask] Plan already exists for well {well_id}, skipping")
                    results.append({
                        'well_id': well_id,
                        'status': 'skipped',
                        'plan_id': existing_plan.plan_id,
                        'snapshot_id': str(existing_plan.id),
                        'message': 'Plan already exists (use force_regenerate to override)'
                    })
                    processed_count += 1
                    job.increment_progress(success=True)
                    continue

                # Generate plan using orchestrator
                plan_result = generate_w3a_for_api(
                    api_number=well_id,
                    plugs_mode=plugs_mode,
                    input_mode=input_mode,
                    request=None,  # No HTTP request in background task
                    confirm_fact_updates=False,  # Conservative: don't auto-update facts
                    allow_precision_upgrades_only=True,
                )

                if plan_result.get('success'):
                    snapshot_id = plan_result.get('snapshot_id')
                    logger.info(f"[BulkTask] Successfully generated plan for well {well_id}: {snapshot_id}")

                    results.append({
                        'well_id': well_id,
                        'status': 'success',
                        'snapshot_id': snapshot_id,
                        'auto_generated': plan_result.get('auto_generated', True),
                    })
                    processed_count += 1
                    job.increment_progress(success=True)
                else:
                    error_msg = plan_result.get('error', 'Unknown error')
                    logger.warning(f"[BulkTask] Failed to generate plan for well {well_id}: {error_msg}")

                    results.append({
                        'well_id': well_id,
                        'status': 'failed',
                        'error': error_msg
                    })
                    failed_count += 1
                    job.increment_progress(success=False)

            except Exception as e:
                error_msg = str(e)
                logger.exception(f"[BulkTask] Error processing well {well_id}")

                results.append({
                    'well_id': well_id,
                    'status': 'failed',
                    'error': error_msg
                })
                failed_count += 1
                job.increment_progress(success=False)

        # Mark job as complete
        job.result_data = {
            'results': results,
            'summary': {
                'total': len(well_ids),
                'processed': processed_count,
                'failed': failed_count,
            }
        }
        job.complete_successfully()

        logger.info(
            f"[BulkTask] Job {job_id} completed. "
            f"Processed: {processed_count}, Failed: {failed_count}"
        )

        return {
            'status': 'success',
            'processed': processed_count,
            'failed': failed_count,
            'results': results
        }

    except BulkJob.DoesNotExist:
        logger.error(f"[BulkTask] Job {job_id} not found")
        return {
            'status': 'failed',
            'error': f"Job {job_id} not found"
        }

    except Exception as e:
        logger.exception(f"[BulkTask] Fatal error in bulk_generate_plans for job {job_id}")

        # Mark job as failed
        try:
            job = BulkJob.objects.get(id=job_id)
            job.fail(str(e))
        except Exception:
            pass

        # Retry up to 3 times
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30 * (self.request.retries + 1))

        return {
            'status': 'failed',
            'error': str(e)
        }


@shared_task(bind=True)
def bulk_update_plan_status(
    self,
    job_id: str,
    plan_ids: List[str],
    new_status: str
) -> Dict[str, Any]:
    """
    Update status for multiple plans.

    This task:
    1. Validates status transition for each plan
    2. Updates plan status
    3. Tracks results and errors

    Args:
        job_id: BulkJob UUID
        plan_ids: List of plan_id strings
        new_status: Target status (e.g., 'engineer_approved')

    Returns:
        {
            'status': 'success' | 'failed',
            'processed': int,
            'failed': int,
            'results': [...]
        }
    """
    from apps.public_core.models import BulkJob, PlanSnapshot

    logger.info(f"[BulkTask] Starting bulk_update_plan_status for job {job_id}")

    try:
        # Get job and mark as processing
        job = BulkJob.objects.get(id=job_id)
        job.start_processing()
        job.celery_task_id = self.request.id
        job.save(update_fields=['celery_task_id'])

        logger.info(f"[BulkTask] Job {job_id} processing {len(plan_ids)} plans -> {new_status}")

        results = []
        processed_count = 0
        failed_count = 0

        for plan_id in plan_ids:
            try:
                # Get latest snapshot for this plan_id
                snapshot = PlanSnapshot.objects.filter(
                    plan_id=plan_id,
                    tenant_id=job.tenant_id
                ).order_by('-created_at').first()

                if not snapshot:
                    raise ValueError(f"Plan {plan_id} not found")

                # Validate transition (basic validation)
                if snapshot.status == new_status:
                    logger.info(f"[BulkTask] Plan {plan_id} already in status {new_status}")
                    results.append({
                        'plan_id': plan_id,
                        'status': 'skipped',
                        'message': f'Already in status {new_status}'
                    })
                    processed_count += 1
                    job.increment_progress(success=True)
                    continue

                # Update status
                old_status = snapshot.status
                snapshot.status = new_status
                snapshot.save(update_fields=['status'])

                logger.info(f"[BulkTask] Updated plan {plan_id}: {old_status} -> {new_status}")

                results.append({
                    'plan_id': plan_id,
                    'status': 'success',
                    'old_status': old_status,
                    'new_status': new_status
                })
                processed_count += 1
                job.increment_progress(success=True)

            except Exception as e:
                error_msg = str(e)
                logger.warning(f"[BulkTask] Failed to update plan {plan_id}: {error_msg}")

                results.append({
                    'plan_id': plan_id,
                    'status': 'failed',
                    'error': error_msg
                })
                failed_count += 1
                job.increment_progress(success=False)

        # Mark job as complete
        job.result_data = {
            'results': results,
            'summary': {
                'total': len(plan_ids),
                'processed': processed_count,
                'failed': failed_count,
            }
        }
        job.complete_successfully()

        logger.info(
            f"[BulkTask] Job {job_id} completed. "
            f"Processed: {processed_count}, Failed: {failed_count}"
        )

        return {
            'status': 'success',
            'processed': processed_count,
            'failed': failed_count,
            'results': results
        }

    except BulkJob.DoesNotExist:
        logger.error(f"[BulkTask] Job {job_id} not found")
        return {
            'status': 'failed',
            'error': f"Job {job_id} not found"
        }

    except Exception as e:
        logger.exception(f"[BulkTask] Fatal error in bulk_update_plan_status for job {job_id}")

        # Mark job as failed
        try:
            job = BulkJob.objects.get(id=job_id)
            job.fail(str(e))
        except Exception:
            pass

        return {
            'status': 'failed',
            'error': str(e)
        }
