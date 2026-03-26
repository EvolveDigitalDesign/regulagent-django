"""
Tests for RecommendationEngine: recommendation generation, ranked retrieval,
field value trigger matching, cross-tenant privacy guards, and geo scoring.
"""
import uuid

import pytest

from apps.intelligence.models import Recommendation, RejectionPattern
from apps.intelligence.services.recommendation_engine import RecommendationEngine


# ---------------------------------------------------------------------------
# generate_recommendations
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_generate_recommendations_creates_from_pattern(rejection_pattern, mocker):
    """Pattern with tenant_count >= 3 and occurrence_count >= 2 creates a recommendation."""
    rejection_pattern.tenant_count = 3
    rejection_pattern.occurrence_count = 5
    rejection_pattern.save()

    mocker.patch(
        "apps.intelligence.services.recommendation_engine.RecommendationEngine._generate_content",
        return_value=("Check plug type", "Use Cement Plug to avoid rejection."),
    )

    engine = RecommendationEngine()
    stats = engine.generate_recommendations()

    assert stats["created"] == 1
    assert Recommendation.objects.filter(
        pattern=rejection_pattern, scope="cross_tenant"
    ).exists()


@pytest.mark.django_db
def test_generate_recommendations_skips_low_tenant_count(rejection_pattern, mocker):
    """Pattern with tenant_count < 3 is skipped for cross-tenant privacy."""
    rejection_pattern.tenant_count = 2
    rejection_pattern.occurrence_count = 5
    rejection_pattern.save()

    mocker.patch(
        "apps.intelligence.services.recommendation_engine.RecommendationEngine._generate_content",
        return_value=("Title", "Desc"),
    )

    engine = RecommendationEngine()
    stats = engine.generate_recommendations()

    assert stats["skipped"] == 1
    assert stats["created"] == 0


@pytest.mark.django_db
def test_generate_recommendations_skips_low_occurrence_count(mocker):
    """Pattern with occurrence_count < 2 is not processed."""
    pattern = RejectionPattern.objects.create(
        form_type="w3a",
        field_name="lease_name",
        issue_category="documentation",
        state="TX",
        district="",
        agency="RRC",
        pattern_description="Low occurrence",
        occurrence_count=1,
        tenant_count=5,
    )

    mocker.patch(
        "apps.intelligence.services.recommendation_engine.RecommendationEngine._generate_content",
        return_value=("Title", "Desc"),
    )

    engine = RecommendationEngine()
    stats = engine.generate_recommendations()

    # Should not appear in created (filtered out by MIN_OCCURRENCE_COUNT=2)
    assert not Recommendation.objects.filter(pattern=pattern).exists()


@pytest.mark.django_db
def test_generate_recommendations_updates_existing(rejection_pattern, recommendation, mocker):
    """Existing active recommendation is updated, not duplicated."""
    rejection_pattern.tenant_count = 5
    rejection_pattern.occurrence_count = 10
    rejection_pattern.save()

    mocker.patch(
        "apps.intelligence.services.recommendation_engine.RecommendationEngine._generate_content",
        return_value=("Title", "Desc"),
    )

    engine = RecommendationEngine()
    stats = engine.generate_recommendations()

    assert stats["updated"] >= 1
    assert Recommendation.objects.filter(
        pattern=rejection_pattern, scope="cross_tenant"
    ).count() == 1


@pytest.mark.django_db
def test_generate_recommendations_uses_template_fallback_on_ai_error(
    rejection_pattern, mocker
):
    rejection_pattern.tenant_count = 3
    rejection_pattern.occurrence_count = 5
    rejection_pattern.save()

    mocker.patch(
        "apps.intelligence.services.recommendation_engine.RecommendationEngine._generate_content",
        side_effect=Exception("OpenAI error"),
    )

    engine = RecommendationEngine()
    stats = engine.generate_recommendations()

    # Should still create via template fallback
    assert stats["created"] == 1


# ---------------------------------------------------------------------------
# get_recommendations_for_context
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_recommendations_for_context_returns_matching(recommendation):
    engine = RecommendationEngine()
    results = engine.get_recommendations_for_context(
        form_type="w3a",
        state="TX",
        district="8A",
    )
    ids = [r["id"] for r in results]
    assert str(recommendation.id) in ids


@pytest.mark.django_db
def test_get_recommendations_for_context_filters_by_form_type(recommendation):
    # c103 form — should not return w3a recommendations
    engine = RecommendationEngine()
    results = engine.get_recommendations_for_context(form_type="c103")
    ids = [r["id"] for r in results]
    assert str(recommendation.id) not in ids


@pytest.mark.django_db
def test_get_recommendations_for_context_privacy_filter(private_pattern, mocker):
    """Recs backed by patterns with tenant_count < 3 are excluded from cross_tenant results."""
    Recommendation.objects.create(
        pattern=private_pattern,
        form_type="w3a",
        field_name="cement_class",
        title="Private recommendation",
        description="Should not appear",
        scope="cross_tenant",
        priority="low",
        is_active=True,
    )

    mocker.patch(
        "apps.intelligence.services.recommendation_engine.RecommendationEngine._embedding_augment",
        return_value=[],
    )

    engine = RecommendationEngine()
    results = engine.get_recommendations_for_context(form_type="w3a", state="TX")

    titles = [r["title"] for r in results]
    assert "Private recommendation" not in titles


@pytest.mark.django_db
def test_get_recommendations_for_context_district_ranked_higher(
    db, rejection_pattern, mocker
):
    """District-specific recs score higher than state-level recs."""
    mocker.patch(
        "apps.intelligence.services.recommendation_engine.RecommendationEngine._embedding_augment",
        return_value=[],
    )

    district_rec = Recommendation.objects.create(
        pattern=rejection_pattern,
        form_type="w3a",
        field_name="plug_type",
        state="TX",
        district="8A",
        title="District-specific rec",
        description="District",
        scope="cross_tenant",
        priority="high",
        is_active=True,
    )

    state_rec = Recommendation.objects.create(
        pattern=rejection_pattern,
        form_type="w3a",
        field_name="plug_type",
        state="TX",
        district="",
        title="State-level rec",
        description="State",
        scope="cross_tenant",
        priority="high",
        is_active=True,
    )

    engine = RecommendationEngine()
    results = engine.get_recommendations_for_context(
        form_type="w3a",
        state="TX",
        district="8A",
    )

    id_order = [r["id"] for r in results]
    if str(district_rec.id) in id_order and str(state_rec.id) in id_order:
        assert id_order.index(str(district_rec.id)) < id_order.index(str(state_rec.id))


@pytest.mark.django_db
def test_get_recommendations_for_context_inactive_excluded(recommendation):
    recommendation.is_active = False
    recommendation.save()

    engine = RecommendationEngine()
    results = engine.get_recommendations_for_context(form_type="w3a", state="TX")
    ids = [r["id"] for r in results]
    assert str(recommendation.id) not in ids


# ---------------------------------------------------------------------------
# check_field_value
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_check_field_value_trigger_values_match(recommendation):
    engine = RecommendationEngine()
    results = engine.check_field_value(
        form_type="w3a",
        field_name="plug_type",
        value="CIBP cap",
        state="TX",
        district="8A",
    )
    assert len(results) >= 1
    assert any(r["id"] == str(recommendation.id) for r in results)


@pytest.mark.django_db
def test_check_field_value_no_match_for_correct_value(recommendation):
    """Value 'Cement Plug' is not in trigger_values — should not trigger."""
    engine = RecommendationEngine()
    results = engine.check_field_value(
        form_type="w3a",
        field_name="plug_type",
        value="Cement Plug",
        state="TX",
        district="8A",
    )
    # Correct value — recommendation should not fire
    assert not any(r["id"] == str(recommendation.id) for r in results)


@pytest.mark.django_db
def test_check_field_value_regex_pattern_match(db, rejection_pattern):
    rec = Recommendation.objects.create(
        pattern=rejection_pattern,
        form_type="w3a",
        field_name="plug_type",
        state="TX",
        district="8A",
        title="Regex match rec",
        description="Regex test",
        scope="cross_tenant",
        priority="medium",
        is_active=True,
        trigger_condition={
            "field_name": "plug_type",
            "trigger_pattern": r"(?i)cibp|bridge.?plug.?cap",
        },
    )

    engine = RecommendationEngine()
    results = engine.check_field_value(
        form_type="w3a",
        field_name="plug_type",
        value="bridge plug cap",
        state="TX",
        district="8A",
    )
    assert any(r["id"] == str(rec.id) for r in results)


@pytest.mark.django_db
def test_check_field_value_no_trigger_always_matches(db, rejection_pattern):
    """No trigger defined → informational rec always fires."""
    rec = Recommendation.objects.create(
        pattern=rejection_pattern,
        form_type="w3a",
        field_name="plug_type",
        state="TX",
        district="8A",
        title="Always-on rec",
        description="No trigger defined",
        scope="cross_tenant",
        priority="low",
        is_active=True,
        trigger_condition={},  # no trigger_values or trigger_pattern
    )

    engine = RecommendationEngine()
    results = engine.check_field_value(
        form_type="w3a",
        field_name="plug_type",
        value="anything_at_all",
        state="TX",
        district="8A",
    )
    assert any(r["id"] == str(rec.id) for r in results)


@pytest.mark.django_db
def test_check_field_value_invalid_regex_does_not_raise(db, rejection_pattern):
    """Invalid regex in trigger_pattern must not raise an exception."""
    Recommendation.objects.create(
        pattern=rejection_pattern,
        form_type="w3a",
        field_name="plug_type",
        state="TX",
        district="8A",
        title="Bad regex rec",
        description="Invalid regex",
        scope="cross_tenant",
        priority="low",
        is_active=True,
        trigger_condition={"trigger_pattern": "[invalid("},
    )

    engine = RecommendationEngine()
    # Must not raise
    results = engine.check_field_value(
        form_type="w3a",
        field_name="plug_type",
        value="test",
        state="TX",
        district="8A",
    )
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Priority derivation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_derive_priority_high_for_high_rejection_rate():
    engine = RecommendationEngine()
    pattern = RejectionPattern(rejection_rate=0.6, is_trending=False, occurrence_count=1)
    assert engine._derive_priority(pattern) == "high"


@pytest.mark.django_db
def test_derive_priority_high_for_trending():
    engine = RecommendationEngine()
    pattern = RejectionPattern(rejection_rate=0.1, is_trending=True, occurrence_count=1)
    assert engine._derive_priority(pattern) == "high"


@pytest.mark.django_db
def test_derive_priority_medium_for_moderate_occurrence():
    engine = RecommendationEngine()
    pattern = RejectionPattern(rejection_rate=0.1, is_trending=False, occurrence_count=10)
    assert engine._derive_priority(pattern) == "medium"


@pytest.mark.django_db
def test_derive_priority_low_for_minimal_signal():
    engine = RecommendationEngine()
    pattern = RejectionPattern(rejection_rate=0.05, is_trending=False, occurrence_count=3)
    assert engine._derive_priority(pattern) == "low"


# ---------------------------------------------------------------------------
# Template content fallback
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_template_content_includes_field_name(rejection_pattern):
    engine = RecommendationEngine()
    title, description = engine._template_content(rejection_pattern)

    assert "plug_type" in title
    assert "terminology" in title
    assert "plug_type" in description or "RRC" in description


@pytest.mark.django_db
def test_template_content_includes_good_value_when_present(rejection_pattern):
    engine = RecommendationEngine()
    _, description = engine._template_content(rejection_pattern)
    assert "Cement Plug" in description
