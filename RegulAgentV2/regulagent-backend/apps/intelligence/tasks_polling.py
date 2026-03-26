"""
Celery tasks for polling agency portals and capturing post-submission status.

These tasks run separately from tasks.py (rejection parsing) to allow parallel
development and clean separation of concerns. The orchestrator will ensure both
task modules are auto-discovered via celery.py's `app.autodiscover_tasks()`.

Beat schedule:
    'poll-rrc-filing-statuses' runs every 4 hours.
    Configure via django-celery-beat admin or the data migration in:
        apps/intelligence/migrations/0xxx_add_beat_schedule.py
"""

import asyncio
import logging

from asgiref.sync import async_to_sync
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Periodic polling task
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def poll_filing_statuses(self, agency: str = "RRC"):
    """
    Poll agency portal for status updates on all pending/under_review filings.

    Schedule: Every 4 hours via django-celery-beat (see data migration).

    Algorithm:
    1. Collect all FilingStatusRecord(status__in=['pending','under_review'], agency=agency)
    2. Group by tenant_id (each tenant has its own portal credentials)
    3. For each tenant:
         a. Instantiate PortalStatusPoller and run poll_pending_filings()
         b. For each update returned:
              - Skip if status unchanged
              - Update FilingStatusRecord fields
              - If new status is adverse (rejected/revision_requested/deficiency):
                dispatch create_rejection_from_status lazily
    4. Errors for individual filings are logged but do not abort the whole batch.
    """
    from apps.intelligence.models import FilingStatusRecord
    from apps.intelligence.services.portal_poller import (
        ADVERSE_STATUSES,
        PortalStatusPoller,
    )

    logger.info("poll_filing_statuses started — agency=%s", agency)

    pending_qs = FilingStatusRecord.objects.filter(
        agency=agency,
        status__in=["pending", "under_review"],
    ).values("tenant_id").distinct()

    tenant_ids = [row["tenant_id"] for row in pending_qs]

    if not tenant_ids:
        logger.info("poll_filing_statuses: no pending filings for agency=%s", agency)
        return {"polled": 0, "updated": 0}

    total_polled = 0
    total_updated = 0
    poller = PortalStatusPoller(agency=agency)

    for tenant_id in tenant_ids:
        try:
            updates = async_to_sync(poller.poll_pending_filings)(str(tenant_id))
        except Exception as exc:
            logger.exception(
                "poll_filing_statuses: error polling tenant=%s agency=%s: %s",
                tenant_id,
                agency,
                exc,
            )
            continue

        total_polled += len(updates)

        for update in updates:
            filing_status_id = update.get("filing_status_id")
            new_status = update.get("new_status")
            old_status = update.get("old_status")

            if not filing_status_id or not new_status:
                continue

            if new_status == old_status:
                # No change — still update polled_at for tracking
                try:
                    FilingStatusRecord.objects.filter(pk=filing_status_id).update(
                        polled_at=timezone.now(),
                        raw_portal_data=update.get("raw_data", {}),
                    )
                except Exception as exc:
                    logger.exception(
                        "poll_filing_statuses: failed to update polled_at for %s: %s",
                        filing_status_id,
                        exc,
                    )
                continue

            # Status changed — persist update
            try:
                updated_count = FilingStatusRecord.objects.filter(
                    pk=filing_status_id
                ).update(
                    status=new_status,
                    agency_remarks=update.get("remarks", ""),
                    reviewer_name=update.get("reviewer_name", ""),
                    status_date=update.get("status_date"),
                    raw_portal_data=update.get("raw_data", {}),
                    polled_at=timezone.now(),
                )
            except Exception as exc:
                logger.exception(
                    "poll_filing_statuses: failed to update FilingStatusRecord %s: %s",
                    filing_status_id,
                    exc,
                )
                continue

            if updated_count:
                total_updated += 1
                logger.info(
                    "FilingStatusRecord %s status changed: %s -> %s (filing_id=%s)",
                    filing_status_id,
                    old_status,
                    new_status,
                    update.get("filing_id"),
                )

            # Dispatch rejection creation for adverse statuses
            if new_status in ADVERSE_STATUSES:
                try:
                    # Lazy import to avoid circular dependency with tasks.py
                    # (that module is created by a separate agent)
                    from apps.intelligence import tasks as intelligence_tasks  # noqa: PLC0415

                    intelligence_tasks.create_rejection_from_status.delay(
                        filing_status_id
                    )
                    logger.info(
                        "Dispatched create_rejection_from_status for FilingStatusRecord %s",
                        filing_status_id,
                    )
                except (ImportError, AttributeError) as exc:
                    logger.warning(
                        "create_rejection_from_status not yet available (%s). "
                        "FilingStatusRecord %s will need manual rejection record creation.",
                        exc,
                        filing_status_id,
                    )
                except Exception as exc:
                    logger.exception(
                        "Failed to dispatch create_rejection_from_status for %s: %s",
                        filing_status_id,
                        exc,
                    )

    logger.info(
        "poll_filing_statuses complete — agency=%s tenants=%d polled=%d updated=%d",
        agency,
        len(tenant_ids),
        total_polled,
        total_updated,
    )
    return {"polled": total_polled, "updated": total_updated}


# ---------------------------------------------------------------------------
# Post-submission capture task
# ---------------------------------------------------------------------------


@shared_task
def capture_post_submission_status(
    filing_id: str,
    form_type: str,
    agency: str,
    tenant_id: str,
    well_id: str,
    w3_form_id: str = None,
    plan_snapshot_id: str = None,
    c103_form_id: str = None,
    state: str = "",
    district: str = "",
    county: str = "",
):
    """
    Called immediately after RRCFormAutomator.submit_form() succeeds.

    Creates an initial FilingStatusRecord(status='pending') so the polling
    loop can pick it up in the next cycle.

    Args:
        filing_id: Agency tracking/confirmation number captured from submission page.
        form_type: e.g. 'w3', 'w3a', 'c103'
        agency: e.g. 'RRC'
        tenant_id: UUID string of the owning tenant.
        well_id: PK (str) of the related WellRegistry.
        w3_form_id: Optional PK of W3FormORM.
        plan_snapshot_id: Optional PK of PlanSnapshot.
        c103_form_id: Optional PK of C103FormORM.
        state: Two-letter state code.
        district: Agency district.
        county: County name.
    """
    from apps.intelligence.services.portal_poller import PostSubmissionCapture

    logger.info(
        "capture_post_submission_status: filing_id=%s agency=%s form_type=%s "
        "tenant=%s well=%s",
        filing_id,
        agency,
        form_type,
        tenant_id,
        well_id,
    )

    if not filing_id:
        logger.error(
            "capture_post_submission_status called with empty filing_id — "
            "cannot create FilingStatusRecord. agency=%s form_type=%s tenant=%s",
            agency,
            form_type,
            tenant_id,
        )
        return {"created": False, "reason": "empty_filing_id"}

    try:
        record = PostSubmissionCapture.create_initial_filing_status(
            filing_id=filing_id,
            form_type=form_type,
            agency=agency,
            tenant_id=tenant_id,
            well_id=well_id,
            w3_form_id=w3_form_id,
            plan_snapshot_id=plan_snapshot_id,
            c103_form_id=c103_form_id,
            state=state,
            district=district,
            county=county,
        )
        return {"created": True, "filing_status_id": str(record.id)}

    except Exception as exc:
        logger.exception(
            "capture_post_submission_status failed: filing_id=%s agency=%s: %s",
            filing_id,
            agency,
            exc,
        )
        return {"created": False, "reason": str(exc)}
