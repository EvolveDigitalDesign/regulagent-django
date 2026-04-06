"""
Celery tasks for asynchronous intelligence pipeline operations.

- parse_rejection_notes: AI-parse a RejectionRecord's raw notes into structured issues.
- create_rejection_from_status: Create a RejectionRecord when a FilingStatus goes rejected.
"""

import logging

from celery import shared_task

from apps.tenants.context import set_current_tenant
from apps.tenants.models import Tenant

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task: parse_rejection_notes
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def parse_rejection_notes(self, rejection_record_id: str):
    """
    Parse rejection notes for a RejectionRecord using AI.

    Steps:
    1. Load RejectionRecord by ID.
    2. Call RejectionParser.parse_rejection().
    3. Update rejection_record.parsed_issues with results.
    4. Update rejection_record.parse_status to 'parsed'.

    Retries on OpenAI API failures (up to 3 times, 60s delay).
    Logs and marks failed on parse/validation errors (no retry for bad data).
    """
    from apps.intelligence.models import RejectionRecord
    from apps.intelligence.services.rejection_parser import RejectionParser

    try:
        rejection_record = RejectionRecord.objects.get(id=rejection_record_id)
    except RejectionRecord.DoesNotExist:
        logger.error(
            "[parse_rejection_notes] RejectionRecord %s not found — task aborted.",
            rejection_record_id,
        )
        return {"status": "error", "reason": "record_not_found"}

    tenant = Tenant.objects.get(id=rejection_record.tenant_id)
    set_current_tenant(tenant)

    logger.info(
        "[parse_rejection_notes] Starting AI parse for RejectionRecord %s "
        "(agency=%s form=%s).",
        rejection_record_id,
        rejection_record.agency,
        rejection_record.form_type,
    )

    try:
        parser = RejectionParser()
        issues = parser.parse_rejection(rejection_record)
    except Exception as exc:
        logger.exception(
            "[parse_rejection_notes] OpenAI call failed for record %s.",
            rejection_record_id,
        )
        # Retry on API-level failures
        raise self.retry(exc=exc, countdown=self.default_retry_delay * (self.request.retries + 1))

    # Persist parsed results
    try:
        rejection_record.parsed_issues = issues
        rejection_record.parse_status = "parsed"
        rejection_record.save(update_fields=["parsed_issues", "parse_status", "updated_at"])

        logger.info(
            "[parse_rejection_notes] Saved %d issue(s) for RejectionRecord %s.",
            len(issues),
            rejection_record_id,
        )
        return {"status": "success", "issues_count": len(issues)}

    except Exception as exc:
        logger.exception(
            "[parse_rejection_notes] Failed to save parsed issues for record %s.",
            rejection_record_id,
        )
        # DB save errors are not retried — something structural is wrong
        return {"status": "error", "reason": str(exc)}


# ---------------------------------------------------------------------------
# Task: create_rejection_from_status
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def create_rejection_from_status(self, filing_status_id: str):
    """
    Create a RejectionRecord from a FilingStatusRecord when status changes to a
    rejection-type status (rejected / revision_requested / deficiency).

    Steps:
    1. Load FilingStatusRecord by ID.
    2. Verify status is in {rejected, revision_requested, deficiency}.
    3. Capture a snapshot of the linked form's data.
    4. Create RejectionRecord (parse_status='pending').
    5. Chain to parse_rejection_notes.delay().
    """
    from apps.intelligence.models import FilingStatusRecord, RejectionRecord

    REJECTION_STATUSES = {"rejected", "revision_requested", "deficiency"}

    try:
        filing_status = FilingStatusRecord.objects.select_related(
            "well",
            "w3_form",
            "plan_snapshot",
            "c103_form",
        ).get(id=filing_status_id)
    except FilingStatusRecord.DoesNotExist:
        logger.error(
            "[create_rejection_from_status] FilingStatusRecord %s not found — task aborted.",
            filing_status_id,
        )
        return {"status": "error", "reason": "record_not_found"}

    if filing_status.status not in REJECTION_STATUSES:
        logger.info(
            "[create_rejection_from_status] FilingStatusRecord %s has status '%s' — "
            "no rejection record needed.",
            filing_status_id,
            filing_status.status,
        )
        return {"status": "skipped", "reason": "non_rejection_status"}

    # Avoid duplicate RejectionRecords for the same filing status
    existing = RejectionRecord.objects.filter(filing_status=filing_status).first()
    if existing:
        logger.info(
            "[create_rejection_from_status] RejectionRecord %s already exists for "
            "FilingStatusRecord %s — skipping creation.",
            existing.id,
            filing_status_id,
        )
        return {"status": "skipped", "reason": "already_exists", "rejection_record_id": str(existing.id)}

    logger.info(
        "[create_rejection_from_status] Creating RejectionRecord for "
        "FilingStatusRecord %s (status=%s).",
        filing_status_id,
        filing_status.status,
    )

    try:
        snapshot = _capture_form_snapshot(filing_status)
    except Exception as exc:
        logger.exception(
            "[create_rejection_from_status] Failed to capture form snapshot for "
            "FilingStatusRecord %s.",
            filing_status_id,
        )
        raise self.retry(exc=exc, countdown=self.default_retry_delay * (self.request.retries + 1))

    try:
        rejection_record = RejectionRecord.objects.create(
            filing_status=filing_status,
            # Copy polymorphic form FKs
            w3_form=filing_status.w3_form,
            plan_snapshot=filing_status.plan_snapshot,
            c103_form=filing_status.c103_form,
            # Tenant + well
            tenant_id=filing_status.tenant_id,
            well=filing_status.well,
            # Denormalized geo
            state=filing_status.state,
            district=filing_status.district,
            county=filing_status.county,
            land_type=filing_status.land_type,
            # Agency / form metadata
            agency=filing_status.agency,
            form_type=filing_status.form_type,
            # Rejection details
            raw_rejection_notes=filing_status.agency_remarks,
            rejection_date=filing_status.status_date,
            reviewer_name=filing_status.reviewer_name,
            submitted_form_snapshot=snapshot,
            parse_status="pending",
        )
    except Exception as exc:
        logger.exception(
            "[create_rejection_from_status] Failed to create RejectionRecord for "
            "FilingStatusRecord %s.",
            filing_status_id,
        )
        raise self.retry(exc=exc, countdown=self.default_retry_delay * (self.request.retries + 1))

    logger.info(
        "[create_rejection_from_status] Created RejectionRecord %s — dispatching parse task.",
        rejection_record.id,
    )

    # Chain to AI parse task
    parse_rejection_notes.delay(str(rejection_record.id))

    return {
        "status": "success",
        "rejection_record_id": str(rejection_record.id),
    }


# ---------------------------------------------------------------------------
# Helper: _capture_form_snapshot
# ---------------------------------------------------------------------------


def _capture_form_snapshot(filing_status: "FilingStatusRecord") -> dict:
    """
    Capture a snapshot of the linked form's data for AI comparison.

    Serializes whichever of (W3FormORM, PlanSnapshot, C103FormORM) is linked
    to the FilingStatusRecord. Returns a dict with form field values and metadata.

    Priority: w3_form > plan_snapshot > c103_form
    If none is linked, returns an empty dict.
    """
    # W-3 / W-3A form
    if filing_status.w3_form_id and filing_status.w3_form:
        form = filing_status.w3_form
        return {
            "form_type": "w3",
            "api_number": form.api_number,
            "status": form.status,
            "form_data": form.form_data or {},
            "rrc_export": form.rrc_export or [],
            "validation_warnings": form.validation_warnings or [],
            "validation_errors": form.validation_errors or [],
            "submitted_at": form.submitted_at.isoformat() if form.submitted_at else None,
        }

    # Plan snapshot (W-3A kernel-generated plan)
    if filing_status.plan_snapshot_id and filing_status.plan_snapshot:
        snap = filing_status.plan_snapshot
        return {
            "form_type": "plan_snapshot",
            "plan_id": str(snap.plan_id) if hasattr(snap, "plan_id") else None,
            "kind": snap.kind,
            "status": snap.status,
            "payload": snap.payload or {},
        }

    # C-103 form
    if filing_status.c103_form_id and filing_status.c103_form:
        form = filing_status.c103_form
        return {
            "form_type": "c103",
            "api_number": form.api_number,
            "status": form.status,
            "form_subtype": form.form_type,
            "region": form.region,
            "sub_area": form.sub_area,
            "lease_type": form.lease_type,
            "plan_data": form.plan_data or {},
            "proposed_work_narrative": form.proposed_work_narrative,
            "compliance_violations": form.compliance_violations or [],
            "submitted_at": form.submitted_at.isoformat() if form.submitted_at else None,
        }

    logger.warning(
        "[_capture_form_snapshot] FilingStatusRecord %s has no linked form — "
        "snapshot will be empty.",
        filing_status.id,
    )
    return {}


# ---------------------------------------------------------------------------
# Task: aggregate_rejection_patterns
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=1, default_retry_delay=300)
def aggregate_rejection_patterns(self):
    """
    Aggregate parsed rejection records into cross-tenant patterns.
    Schedule: Every 6 hours via Celery Beat.

    Calls PatternAggregator.aggregate() which groups parsed RejectionRecord
    issues by (form_type, field_name, issue_category, state, district, agency),
    updates RejectionPattern stats and trends, then dispatches embedding tasks.
    """
    from apps.intelligence.services.pattern_aggregator import PatternAggregator

    try:
        aggregator = PatternAggregator()
        result = aggregator.aggregate()
    except Exception as exc:
        logger.exception("[aggregate_rejection_patterns] Aggregation failed.")
        raise self.retry(exc=exc, countdown=self.default_retry_delay)

    logger.info("[aggregate_rejection_patterns] %s", result)
    return result


# ---------------------------------------------------------------------------
# Task: embed_rejection_pattern
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def embed_rejection_pattern(self, pattern_id: str):
    """
    Embed a single RejectionPattern for similarity search.
    Called after aggregation creates or updates a pattern.

    Retries up to 2 times on OpenAI API failures.
    """
    from apps.intelligence.models import RejectionPattern
    from apps.intelligence.services.rejection_embedder import RejectionEmbedder

    try:
        pattern = RejectionPattern.objects.get(id=pattern_id)
    except RejectionPattern.DoesNotExist:
        logger.error(
            "[embed_rejection_pattern] RejectionPattern %s not found — task aborted.",
            pattern_id,
        )
        return {"status": "error", "reason": "pattern_not_found"}

    try:
        embedder = RejectionEmbedder()
        embedder.embed_pattern(pattern)
    except Exception as exc:
        logger.exception(
            "[embed_rejection_pattern] Embedding failed for pattern %s.",
            pattern_id,
        )
        raise self.retry(exc=exc, countdown=self.default_retry_delay * (self.request.retries + 1))

    logger.info("[embed_rejection_pattern] Embedded pattern %s", pattern_id)
    return {"status": "success", "pattern_id": pattern_id}


# ---------------------------------------------------------------------------
# Task: generate_recommendations
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=1, default_retry_delay=300)
def generate_recommendations(self):
    """Generate recommendations from patterns. Daily at 2am."""
    from apps.intelligence.services.recommendation_engine import RecommendationEngine

    try:
        engine = RecommendationEngine()
        result = engine.generate_recommendations()
    except Exception as exc:
        logger.exception("[generate_recommendations] Task failed.")
        raise self.retry(exc=exc, countdown=self.default_retry_delay)

    logger.info("[generate_recommendations] %s", result)
    return result


# ---------------------------------------------------------------------------
# Task: update_recommendation_metrics
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=1, default_retry_delay=300)
def update_recommendation_metrics(self):
    """Recalculate acceptance rates. Every 4 hours."""
    from django.db.models import Case, F, FloatField, Value, When

    from apps.intelligence.models import Recommendation

    try:
        updated = Recommendation.objects.filter(times_shown__gt=0).update(
            acceptance_rate=Case(
                When(
                    times_shown__gt=0,
                    then=F("times_accepted") * 1.0 / F("times_shown"),
                ),
                default=Value(0.0),
                output_field=FloatField(),
            )
        )
    except Exception as exc:
        logger.exception("[update_recommendation_metrics] Task failed.")
        raise self.retry(exc=exc, countdown=self.default_retry_delay)

    logger.info("[update_recommendation_metrics] Updated %d recommendations", updated)
    return {"updated": updated}
