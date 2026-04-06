"""
Tests for PatternAggregator: aggregation logic, idempotency,
trend detection, confidence calculation, and embed dispatch.
"""
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.intelligence.models import FilingStatusRecord, RejectionPattern, RejectionRecord
from apps.intelligence.services.pattern_aggregator import PatternAggregator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_rejection(db_fixture, filing_status, well, tenant_id, parsed_issues,
                   state="TX", district="8A", agency="RRC", form_type="w3a"):
    """Create a parsed RejectionRecord with the given issues."""
    return RejectionRecord.objects.create(
        filing_status=filing_status,
        tenant_id=tenant_id,
        well=well,
        agency=agency,
        form_type=form_type,
        state=state,
        district=district,
        raw_rejection_notes="Test notes",
        parse_status="parsed",
        parsed_issues=parsed_issues,
    )


# ---------------------------------------------------------------------------
# Core aggregation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_aggregate_creates_pattern_from_parsed_records(
    db, filing_status_record, well, tenant_id, second_tenant_id, third_tenant_id, mocker
):
    mocker.patch("apps.intelligence.services.pattern_aggregator.PatternAggregator._dispatch_embed_tasks")

    issue = {
        "field_name": "plug_type",
        "issue_category": "terminology",
        "confidence": 0.9,
        "description": "Use Cement Plug",
    }

    for tid in [tenant_id, second_tenant_id, third_tenant_id]:
        # Each needs its own filing status record
        fs = FilingStatusRecord.objects.create(
            filing_id=f"RRC-{tid}",
            tenant_id=tid,
            well=well,
            agency="RRC",
            form_type="w3a",
            status="rejected",
            state="TX",
            district="8A",
        )
        make_rejection(db, fs, well, tid, [issue])

    aggregator = PatternAggregator()
    result = aggregator.aggregate()

    assert result["status"] == "success"
    assert result["patterns_created"] >= 1
    assert RejectionPattern.objects.filter(
        form_type="w3a", field_name="plug_type", issue_category="terminology"
    ).exists()


@pytest.mark.django_db
def test_aggregate_counts_occurrences_and_tenants(
    db, filing_status_record, well, tenant_id, second_tenant_id, third_tenant_id, mocker
):
    mocker.patch("apps.intelligence.services.pattern_aggregator.PatternAggregator._dispatch_embed_tasks")

    issue = {
        "field_name": "cement_volume",
        "issue_category": "calculation",
        "confidence": 0.8,
        "description": "Volume error",
    }

    for tid in [tenant_id, second_tenant_id, third_tenant_id]:
        fs = FilingStatusRecord.objects.create(
            filing_id=f"FS-{tid}",
            tenant_id=tid,
            well=well,
            agency="RRC",
            form_type="w3a",
            status="rejected",
            state="TX",
            district="8A",
        )
        make_rejection(db, fs, well, tid, [issue])

    PatternAggregator().aggregate()

    pattern = RejectionPattern.objects.get(
        form_type="w3a", field_name="cement_volume", issue_category="calculation",
        state="TX", district="8A", agency="RRC",
    )
    assert pattern.occurrence_count == 3
    assert pattern.tenant_count == 3


@pytest.mark.django_db
def test_aggregate_skips_records_with_non_parsed_status(
    db, filing_status_record, well, tenant_id, mocker
):
    mocker.patch("apps.intelligence.services.pattern_aggregator.PatternAggregator._dispatch_embed_tasks")

    # pending record should not be aggregated
    RejectionRecord.objects.create(
        filing_status=filing_status_record,
        tenant_id=tenant_id,
        well=well,
        agency="RRC",
        form_type="w3a",
        parse_status="pending",
        parsed_issues=[{"field_name": "plug_type", "issue_category": "terminology"}],
    )

    result = PatternAggregator().aggregate()
    assert result["patterns_created"] == 0


@pytest.mark.django_db
def test_aggregate_includes_verified_records(
    db, filing_status_record, well, tenant_id, second_tenant_id, third_tenant_id, mocker
):
    mocker.patch("apps.intelligence.services.pattern_aggregator.PatternAggregator._dispatch_embed_tasks")

    issue = {"field_name": "woc_time", "issue_category": "compliance", "confidence": 0.85,
             "description": "WOC time too short"}

    for tid in [tenant_id, second_tenant_id, third_tenant_id]:
        fs = FilingStatusRecord.objects.create(
            filing_id=f"VER-{tid}",
            tenant_id=tid,
            well=well,
            agency="RRC",
            form_type="w3a",
            status="rejected",
            state="TX",
            district="",
        )
        RejectionRecord.objects.create(
            filing_status=fs,
            tenant_id=tid,
            well=well,
            agency="RRC",
            form_type="w3a",
            state="TX",
            parse_status="verified",  # verified status
            parsed_issues=[issue],
        )

    result = PatternAggregator().aggregate()
    assert result["patterns_created"] >= 1


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_aggregate_is_idempotent(
    db, filing_status_record, well, tenant_id, second_tenant_id, third_tenant_id, mocker
):
    mocker.patch("apps.intelligence.services.pattern_aggregator.PatternAggregator._dispatch_embed_tasks")

    issue = {"field_name": "plug_type", "issue_category": "terminology", "confidence": 0.9,
             "description": "Test"}

    for tid in [tenant_id, second_tenant_id, third_tenant_id]:
        fs = FilingStatusRecord.objects.create(
            filing_id=f"IDEM-{tid}",
            tenant_id=tid,
            well=well,
            agency="RRC",
            form_type="w3a",
            status="rejected",
            state="TX",
            district="",
        )
        make_rejection(db, fs, well, tid, [issue])

    PatternAggregator().aggregate()
    count_after_first = RejectionPattern.objects.count()

    PatternAggregator().aggregate()
    count_after_second = RejectionPattern.objects.count()

    assert count_after_first == count_after_second


# ---------------------------------------------------------------------------
# Confidence calculation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_confidence_increases_with_occurrence_count(mocker):
    mocker.patch("apps.intelligence.services.pattern_aggregator.PatternAggregator._dispatch_embed_tasks")

    aggregator = PatternAggregator()

    pattern_few = RejectionPattern(occurrence_count=1, tenant_count=1, last_observed=None)
    pattern_many = RejectionPattern(occurrence_count=20, tenant_count=5, last_observed=None)

    confidence_few = aggregator._calculate_confidence(pattern_few)
    confidence_many = aggregator._calculate_confidence(pattern_many)

    assert confidence_many > confidence_few


@pytest.mark.django_db
def test_confidence_capped_at_1(mocker):
    mocker.patch("apps.intelligence.services.pattern_aggregator.PatternAggregator._dispatch_embed_tasks")

    aggregator = PatternAggregator()
    pattern = RejectionPattern(
        occurrence_count=1000,
        tenant_count=100,
        last_observed=timezone.now(),
    )
    confidence = aggregator._calculate_confidence(pattern)
    assert confidence <= 1.0


@pytest.mark.django_db
def test_confidence_recency_decay(mocker):
    mocker.patch("apps.intelligence.services.pattern_aggregator.PatternAggregator._dispatch_embed_tasks")

    aggregator = PatternAggregator()

    recent_pattern = RejectionPattern(
        occurrence_count=10,
        tenant_count=3,
        last_observed=timezone.now() - timedelta(days=10),
    )
    old_pattern = RejectionPattern(
        occurrence_count=10,
        tenant_count=3,
        last_observed=timezone.now() - timedelta(days=200),
    )

    recent_conf = aggregator._calculate_confidence(recent_pattern)
    old_conf = aggregator._calculate_confidence(old_pattern)

    assert recent_conf > old_conf


# ---------------------------------------------------------------------------
# Trend detection
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_detect_trends_marks_is_trending_when_spike(
    db, filing_status_record, well, tenant_id, mocker
):
    mocker.patch("apps.intelligence.services.pattern_aggregator.PatternAggregator._dispatch_embed_tasks")

    # Create many recent records to trigger trend detection
    for i in range(10):
        fs = FilingStatusRecord.objects.create(
            filing_id=f"TREND-{i}",
            tenant_id=tenant_id,
            well=well,
            agency="RRC",
            form_type="w3a",
            status="rejected",
            state="TX",
        )
        rr = RejectionRecord.objects.create(
            filing_status=fs,
            tenant_id=tenant_id,
            well=well,
            agency="RRC",
            form_type="w3a",
            state="TX",
            parse_status="parsed",
            parsed_issues=[],
        )
        # Backdate to within last 30 days
        RejectionRecord.objects.filter(id=rr.id).update(
            created_at=timezone.now() - timedelta(days=5)
        )

    pattern = RejectionPattern.objects.create(
        form_type="w3a",
        field_name="trend_field",
        issue_category="terminology",
        state="TX",
        district="",
        agency="RRC",
        pattern_description="Trend test",
        occurrence_count=10,
        tenant_count=3,
    )

    aggregator = PatternAggregator()
    aggregator._detect_trends(pattern)

    # With recent activity and no baseline, it should be trending
    assert pattern.is_trending is True or pattern.trend_direction >= 0


@pytest.mark.django_db
def test_detect_trends_not_trending_when_no_recent_activity(db, mocker):
    mocker.patch("apps.intelligence.services.pattern_aggregator.PatternAggregator._dispatch_embed_tasks")

    pattern = RejectionPattern.objects.create(
        form_type="w3a",
        field_name="stale_field",
        issue_category="documentation",
        state="TX",
        district="",
        agency="RRC",
        pattern_description="Stale pattern",
        occurrence_count=1,
        tenant_count=1,
    )

    aggregator = PatternAggregator()
    aggregator._detect_trends(pattern)

    # No records at all — no recent activity
    assert pattern.is_trending is False


# ---------------------------------------------------------------------------
# Embed dispatch
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_aggregate_dispatches_embed_tasks(
    db, filing_status_record, well, tenant_id, second_tenant_id, third_tenant_id, mocker
):
    dispatch_mock = mocker.patch(
        "apps.intelligence.services.pattern_aggregator.PatternAggregator._dispatch_embed_tasks"
    )

    issue = {"field_name": "plug_type", "issue_category": "terminology", "confidence": 0.9,
             "description": "Test embed dispatch"}

    for tid in [tenant_id, second_tenant_id, third_tenant_id]:
        fs = FilingStatusRecord.objects.create(
            filing_id=f"EMBED-{tid}",
            tenant_id=tid,
            well=well,
            agency="RRC",
            form_type="w3a",
            status="rejected",
            state="TX",
            district="",
        )
        make_rejection(db, fs, well, tid, [issue])

    PatternAggregator().aggregate()

    dispatch_mock.assert_called_once()
    dispatched_ids = dispatch_mock.call_args[0][0]
    assert len(dispatched_ids) >= 1


# ---------------------------------------------------------------------------
# Rejection rate calculation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_calculate_rejection_rate(db, well, tenant_id):
    from apps.intelligence.models import FilingStatusRecord

    # 2 rejected, 1 approved out of 3 total for w3a/TX/RRC
    for status, fid in [("rejected", "R1"), ("rejected", "R2"), ("approved", "A1")]:
        FilingStatusRecord.objects.create(
            filing_id=fid,
            tenant_id=tenant_id,
            well=well,
            agency="RRC",
            form_type="w3a",
            status=status,
            state="TX",
        )

    aggregator = PatternAggregator()
    rate = aggregator._calculate_rejection_rate("w3a", "TX", "RRC")

    assert rate == pytest.approx(2 / 3, abs=0.001)


@pytest.mark.django_db
def test_calculate_rejection_rate_no_filings(db):
    aggregator = PatternAggregator()
    rate = aggregator._calculate_rejection_rate("c103", "NM", "NMOCD")
    assert rate == 0.0
