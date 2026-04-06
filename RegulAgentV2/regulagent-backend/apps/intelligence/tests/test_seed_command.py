"""
Tests for the seed_recommendations management command.
Covers creation, idempotency, --clear flag, and error handling.
"""
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.intelligence.models import Recommendation, RejectionPattern


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_creates_patterns_and_recommendations():
    """Running seed_recommendations with no prior data creates records."""
    call_command("seed_recommendations")

    # The YAML fixture has 25 entries — all should result in patterns/recs
    assert RejectionPattern.objects.filter(confidence=0.8).count() >= 20
    assert Recommendation.objects.filter(scope="cold_start").count() >= 20


@pytest.mark.django_db
def test_seed_creates_correct_agency_distribution():
    """Fixture has both RRC and NMOCD entries."""
    call_command("seed_recommendations")

    assert RejectionPattern.objects.filter(agency="RRC").exists()
    assert RejectionPattern.objects.filter(agency="NMOCD").exists()


@pytest.mark.django_db
def test_seed_creates_multiple_form_types():
    """Fixture covers w3a, c103, and possibly w3 entries."""
    call_command("seed_recommendations")

    form_types = set(
        RejectionPattern.objects.values_list("form_type", flat=True)
    )
    assert "w3a" in form_types
    assert "c103" in form_types


@pytest.mark.django_db
def test_seed_sets_confidence_to_0_8():
    """Cold-start patterns have confidence=0.8 as defined in the command."""
    call_command("seed_recommendations")

    patterns = RejectionPattern.objects.filter(confidence=0.8)
    assert patterns.count() >= 20


@pytest.mark.django_db
def test_seed_recommendations_are_active():
    """All seeded recommendations must have is_active=True."""
    call_command("seed_recommendations")

    inactive = Recommendation.objects.filter(scope="cold_start", is_active=False)
    assert inactive.count() == 0


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_is_idempotent():
    """Running seed twice must not create duplicates."""
    call_command("seed_recommendations")
    count_patterns_1 = RejectionPattern.objects.count()
    count_recs_1 = Recommendation.objects.filter(scope="cold_start").count()

    call_command("seed_recommendations")
    count_patterns_2 = RejectionPattern.objects.count()
    count_recs_2 = Recommendation.objects.filter(scope="cold_start").count()

    assert count_patterns_1 == count_patterns_2
    assert count_recs_1 == count_recs_2


@pytest.mark.django_db
def test_seed_three_times_still_idempotent():
    for _ in range(3):
        call_command("seed_recommendations")

    count = Recommendation.objects.filter(scope="cold_start").count()
    # Still at most the fixture count (25), not 75
    assert count <= 30


# ---------------------------------------------------------------------------
# --clear flag
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_clear_flag_resets_and_reseeds():
    """--clear removes existing cold_start entries then reseeds from YAML."""
    call_command("seed_recommendations")
    count_before = Recommendation.objects.filter(scope="cold_start").count()
    assert count_before >= 20

    call_command("seed_recommendations", clear=True)
    count_after = Recommendation.objects.filter(scope="cold_start").count()

    assert count_after >= 20


@pytest.mark.django_db
def test_seed_clear_does_not_delete_cross_tenant_recommendations(rejection_pattern):
    """--clear must only delete scope='cold_start' — not cross_tenant recs."""
    cross_rec = Recommendation.objects.create(
        pattern=rejection_pattern,
        form_type="w3a",
        field_name="plug_type",
        title="Cross-tenant rec",
        description="Should survive clear",
        scope="cross_tenant",
        priority="high",
        is_active=True,
    )

    call_command("seed_recommendations", clear=True)

    assert Recommendation.objects.filter(id=cross_rec.id).exists()


@pytest.mark.django_db
def test_seed_clear_removes_previous_cold_start_recs():
    """After --clear + reseed, only fresh cold_start recs exist."""
    call_command("seed_recommendations")
    first_rec_ids = set(
        Recommendation.objects.filter(scope="cold_start").values_list("id", flat=True)
    )

    call_command("seed_recommendations", clear=True)
    second_rec_ids = set(
        Recommendation.objects.filter(scope="cold_start").values_list("id", flat=True)
    )

    # Old IDs should all be gone
    assert first_rec_ids.isdisjoint(second_rec_ids)


# ---------------------------------------------------------------------------
# Trigger condition and content
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_plug_type_rec_has_trigger_condition():
    """The plug_type/w3a rec should have a non-empty trigger_condition."""
    call_command("seed_recommendations")

    rec = Recommendation.objects.filter(
        form_type="w3a", field_name="plug_type", scope="cold_start"
    ).first()

    assert rec is not None
    assert rec.trigger_condition  # non-empty dict
    assert "trigger_values" in rec.trigger_condition or "trigger_pattern" in rec.trigger_condition


@pytest.mark.django_db
def test_seed_recommendations_have_titles_and_descriptions():
    """Every seeded recommendation must have non-empty title and description."""
    call_command("seed_recommendations")

    for rec in Recommendation.objects.filter(scope="cold_start"):
        assert rec.title, f"Empty title for rec {rec.id}"
        assert rec.description, f"Empty description for rec {rec.id}"
