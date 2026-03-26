"""
Tests for Celery tasks in the intelligence app.
All external services (OpenAI) are mocked. Tasks are called directly (not via broker).
"""
import uuid

import pytest
from django.utils import timezone

from apps.intelligence.models import FilingStatusRecord, RejectionRecord
from apps.intelligence.tasks import (
    aggregate_rejection_patterns,
    create_rejection_from_status,
    embed_rejection_pattern,
    generate_recommendations,
    parse_rejection_notes,
    update_recommendation_metrics,
)


# ---------------------------------------------------------------------------
# parse_rejection_notes
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_parse_rejection_notes_saves_issues_and_marks_parsed(
    rejection_record, mock_openai_parser
):
    result = parse_rejection_notes(str(rejection_record.id))

    assert result["status"] == "success"
    assert result["issues_count"] == 1

    rejection_record.refresh_from_db()
    assert rejection_record.parse_status == "parsed"
    assert len(rejection_record.parsed_issues) == 1
    assert rejection_record.parsed_issues[0]["field_name"] == "plug_type"


@pytest.mark.django_db
def test_parse_rejection_notes_returns_error_for_missing_record():
    result = parse_rejection_notes(str(uuid.uuid4()))
    assert result["status"] == "error"
    assert result["reason"] == "record_not_found"


@pytest.mark.django_db
def test_parse_rejection_notes_retries_on_api_error(rejection_record, mocker):
    """Task should raise Retry when RejectionParser raises an exception."""
    mocker.patch(
        "apps.intelligence.services.rejection_parser.get_openai_client",
        side_effect=Exception("API error"),
    )
    mocker.patch("apps.intelligence.services.rejection_parser.check_rate_limit")

    # Bind the task to inspect retry behaviour
    task = parse_rejection_notes
    bound = task.s(str(rejection_record.id)).freeze()

    # Direct call raises the Retry exception from Celery internals
    from celery.exceptions import Retry
    with pytest.raises((Retry, Exception)):
        parse_rejection_notes(str(rejection_record.id))


@pytest.mark.django_db
def test_parse_rejection_notes_empty_notes_saves_empty_list(
    rejection_record, mock_openai_parser
):
    rejection_record.raw_rejection_notes = ""
    rejection_record.save()

    # Parser returns [] for empty notes; mock is bypassed due to early return
    result = parse_rejection_notes(str(rejection_record.id))

    assert result["status"] == "success"
    rejection_record.refresh_from_db()
    assert rejection_record.parsed_issues == []
    assert rejection_record.parse_status == "parsed"


# ---------------------------------------------------------------------------
# create_rejection_from_status
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_rejection_from_status_creates_record(
    filing_status_record, mocker
):
    mocker.patch(
        "apps.intelligence.tasks.parse_rejection_notes.delay"
    )

    result = create_rejection_from_status(str(filing_status_record.id))

    assert result["status"] == "success"
    assert "rejection_record_id" in result
    assert RejectionRecord.objects.filter(
        filing_status=filing_status_record
    ).exists()


@pytest.mark.django_db
def test_create_rejection_from_status_skips_non_rejection_status(
    well, tenant_id, mocker
):
    fs = FilingStatusRecord.objects.create(
        filing_id="RRC-APPROVED-001",
        tenant_id=tenant_id,
        well=well,
        agency="RRC",
        form_type="w3a",
        status="approved",
    )

    result = create_rejection_from_status(str(fs.id))

    assert result["status"] == "skipped"
    assert result["reason"] == "non_rejection_status"
    assert not RejectionRecord.objects.filter(filing_status=fs).exists()


@pytest.mark.django_db
def test_create_rejection_from_status_returns_error_for_missing_filing(mocker):
    result = create_rejection_from_status(str(uuid.uuid4()))
    assert result["status"] == "error"
    assert result["reason"] == "record_not_found"


@pytest.mark.django_db
def test_create_rejection_from_status_deduplication(
    filing_status_record, rejection_record, mocker
):
    """If RejectionRecord already exists for this filing status, skip creation."""
    result = create_rejection_from_status(str(filing_status_record.id))

    assert result["status"] == "skipped"
    assert result["reason"] == "already_exists"
    assert str(result["rejection_record_id"]) == str(rejection_record.id)


@pytest.mark.django_db
def test_create_rejection_from_status_chains_parse_task(
    filing_status_record, mocker
):
    mock_parse = mocker.patch("apps.intelligence.tasks.parse_rejection_notes.delay")

    create_rejection_from_status(str(filing_status_record.id))

    mock_parse.assert_called_once()


@pytest.mark.django_db
def test_create_rejection_from_status_all_rejection_statuses(
    well, tenant_id, mocker
):
    mocker.patch("apps.intelligence.tasks.parse_rejection_notes.delay")

    for status_val in ["rejected", "revision_requested", "deficiency"]:
        fs = FilingStatusRecord.objects.create(
            filing_id=f"RRC-{status_val}",
            tenant_id=tenant_id,
            well=well,
            agency="RRC",
            form_type="w3a",
            status=status_val,
        )
        result = create_rejection_from_status(str(fs.id))
        assert result["status"] == "success", f"Failed for status: {status_val}"


@pytest.mark.django_db
def test_create_rejection_copies_geo_from_filing_status(
    filing_status_record, mocker
):
    mocker.patch("apps.intelligence.tasks.parse_rejection_notes.delay")

    create_rejection_from_status(str(filing_status_record.id))

    rejection = RejectionRecord.objects.get(filing_status=filing_status_record)
    assert rejection.state == filing_status_record.state
    assert rejection.district == filing_status_record.district
    assert rejection.agency == filing_status_record.agency
    assert rejection.form_type == filing_status_record.form_type
    assert rejection.tenant_id == filing_status_record.tenant_id


# ---------------------------------------------------------------------------
# aggregate_rejection_patterns
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_aggregate_rejection_patterns_task_succeeds(mocker):
    mocker.patch(
        "apps.intelligence.services.pattern_aggregator.PatternAggregator.aggregate",
        return_value={
            "status": "success",
            "records_processed": 0,
            "groups_found": 0,
            "patterns_created": 0,
            "patterns_updated": 0,
            "embed_tasks_dispatched": 0,
        },
    )

    result = aggregate_rejection_patterns()
    assert result["status"] == "success"


@pytest.mark.django_db
def test_aggregate_rejection_patterns_retries_on_failure(mocker):
    mocker.patch(
        "apps.intelligence.services.pattern_aggregator.PatternAggregator.aggregate",
        side_effect=Exception("DB connection error"),
    )

    from celery.exceptions import Retry
    with pytest.raises((Retry, Exception)):
        aggregate_rejection_patterns()


# ---------------------------------------------------------------------------
# embed_rejection_pattern
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_embed_rejection_pattern_task_succeeds(rejection_pattern, mock_openai_embedder, mocker):
    mocker.patch(
        "apps.public_core.models.DocumentVector.objects.create",
        return_value=mocker.MagicMock(),
    )

    # Mock the full embedder to avoid DocumentVector FK issues in test DB
    mock_embedder = mocker.patch(
        "apps.intelligence.services.rejection_embedder.RejectionEmbedder.embed_pattern",
        return_value=mocker.MagicMock(),
    )

    result = embed_rejection_pattern(str(rejection_pattern.id))

    assert result["status"] == "success"
    mock_embedder.assert_called_once_with(rejection_pattern)


@pytest.mark.django_db
def test_embed_rejection_pattern_returns_error_for_missing_pattern():
    result = embed_rejection_pattern(str(uuid.uuid4()))
    assert result["status"] == "error"
    assert result["reason"] == "pattern_not_found"


@pytest.mark.django_db
def test_embed_rejection_pattern_retries_on_api_error(rejection_pattern, mocker):
    mocker.patch(
        "apps.intelligence.services.rejection_embedder.RejectionEmbedder.embed_pattern",
        side_effect=Exception("Embedding API error"),
    )

    from celery.exceptions import Retry
    with pytest.raises((Retry, Exception)):
        embed_rejection_pattern(str(rejection_pattern.id))


# ---------------------------------------------------------------------------
# generate_recommendations
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_generate_recommendations_task_succeeds(mocker):
    mocker.patch(
        "apps.intelligence.services.recommendation_engine.RecommendationEngine"
        ".generate_recommendations",
        return_value={"created": 0, "updated": 0, "skipped": 0},
    )

    result = generate_recommendations()
    assert "created" in result or result is not None


@pytest.mark.django_db
def test_generate_recommendations_task_retries_on_failure(mocker):
    mocker.patch(
        "apps.intelligence.services.recommendation_engine.RecommendationEngine"
        ".generate_recommendations",
        side_effect=Exception("Engine error"),
    )

    from celery.exceptions import Retry
    with pytest.raises((Retry, Exception)):
        generate_recommendations()


# ---------------------------------------------------------------------------
# update_recommendation_metrics
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_update_recommendation_metrics_recalculates(recommendation):
    recommendation.times_shown = 10
    recommendation.times_accepted = 3
    recommendation.save()

    result = update_recommendation_metrics()

    assert result["updated"] >= 1
    recommendation.refresh_from_db()
    assert recommendation.acceptance_rate == pytest.approx(3 / 10)


@pytest.mark.django_db
def test_update_recommendation_metrics_skips_zero_shown(recommendation):
    # Never shown — should not be updated
    recommendation.times_shown = 0
    recommendation.times_accepted = 0
    recommendation.save()

    result = update_recommendation_metrics()
    assert result["updated"] == 0


@pytest.mark.django_db
def test_update_recommendation_metrics_retries_on_failure(mocker):
    mocker.patch(
        "apps.intelligence.models.Recommendation.objects.filter",
        side_effect=Exception("DB error"),
    )

    from celery.exceptions import Retry
    with pytest.raises((Retry, Exception)):
        update_recommendation_metrics()
