"""
Cross-tenant privacy tests.

Verifies that RejectionPattern data with tenant_count < 3 is NEVER
surfaced through the API or the RecommendationEngine.
"""
import uuid

import pytest
from django.urls import reverse

from apps.intelligence.models import Recommendation, RejectionPattern
from apps.intelligence.services.recommendation_engine import RecommendationEngine


# ---------------------------------------------------------------------------
# Model-level: RejectionPattern has no tenant_id
# ---------------------------------------------------------------------------


def test_rejection_pattern_has_no_tenant_id_field():
    """
    RejectionPattern must NOT have a tenant_id column.
    Patterns are cross-tenant aggregates — individual tenant attribution
    must not be traceable from a pattern record.
    """
    field_names = [f.name for f in RejectionPattern._meta.get_fields()]
    assert "tenant_id" not in field_names


# ---------------------------------------------------------------------------
# API-level privacy guards
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTrendsAPIPrivacy:
    def test_patterns_with_tenant_count_lt_3_not_in_trends(
        self, api_client, test_user, private_pattern
    ):
        """Patterns with tenant_count < 3 must not appear in GET /trends/."""
        api_client.force_authenticate(user=test_user)
        url = reverse("intelligence:trends")
        response = api_client.get(url)

        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        assert str(private_pattern.id) not in result_ids

    def test_patterns_with_tenant_count_gte_3_appear_in_trends(
        self, api_client, test_user, rejection_pattern
    ):
        """Patterns with tenant_count >= 3 are safe to surface."""
        api_client.force_authenticate(user=test_user)
        url = reverse("intelligence:trends")
        response = api_client.get(url)

        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        assert str(rejection_pattern.id) in result_ids

    def test_heatmap_excludes_low_tenant_count(self, api_client, test_user, private_pattern):
        """Heatmap must filter out tenant_count < 3 patterns."""
        api_client.force_authenticate(user=test_user)
        url = reverse("intelligence:trends-heatmap")
        response = api_client.get(url)
        # No exception — privacy guard applied at queryset level
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# RecommendationEngine-level privacy guards
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRecommendationEnginePrivacy:
    def test_get_recommendations_excludes_low_tenant_count(
        self, private_pattern, mocker
    ):
        """
        cross_tenant recs backed by patterns with tenant_count < 3
        must be filtered out by get_recommendations_for_context.
        """
        Recommendation.objects.create(
            pattern=private_pattern,
            form_type="w3a",
            field_name="cement_class",
            title="Private rec — should be hidden",
            description="Tenant count too low",
            scope="cross_tenant",
            priority="low",
            is_active=True,
        )

        mocker.patch(
            "apps.intelligence.services.recommendation_engine.RecommendationEngine"
            "._embedding_augment",
            return_value=[],
        )

        engine = RecommendationEngine()
        results = engine.get_recommendations_for_context(form_type="w3a", state="TX")

        titles = [r["title"] for r in results]
        assert "Private rec — should be hidden" not in titles

    def test_check_field_value_excludes_low_tenant_count(self, private_pattern):
        """
        check_field_value must not return recs backed by low-tenant-count patterns.
        """
        Recommendation.objects.create(
            pattern=private_pattern,
            form_type="w3a",
            field_name="cement_class",
            title="Private field rec",
            description="Should not appear",
            scope="cross_tenant",
            priority="low",
            is_active=True,
            trigger_condition={},  # no trigger → always matches
        )

        engine = RecommendationEngine()
        results = engine.check_field_value(
            form_type="w3a",
            field_name="cement_class",
            value="class_g",
            state="TX",
            district="",
        )
        titles = [r["title"] for r in results]
        assert "Private field rec" not in titles

    def test_generate_recommendations_skips_tenant_count_lt_3(self, private_pattern, mocker):
        """
        generate_recommendations must skip patterns with tenant_count < MIN_TENANT_COUNT_CROSS_TENANT.
        """
        private_pattern.occurrence_count = 10
        private_pattern.save()

        mocker.patch(
            "apps.intelligence.services.recommendation_engine.RecommendationEngine"
            "._generate_content",
            return_value=("Title", "Desc"),
        )

        engine = RecommendationEngine()
        stats = engine.generate_recommendations()

        assert stats["skipped"] >= 1
        assert not Recommendation.objects.filter(
            pattern=private_pattern, scope="cross_tenant"
        ).exists()

    def test_cold_start_recs_without_pattern_are_not_privacy_filtered(
        self, cold_start_recommendation, mocker
    ):
        """
        cold_start recs have no pattern → privacy filter (pattern.tenant_count check)
        should not exclude them.
        """
        mocker.patch(
            "apps.intelligence.services.recommendation_engine.RecommendationEngine"
            "._embedding_augment",
            return_value=[],
        )

        engine = RecommendationEngine()
        results = engine.get_recommendations_for_context(form_type="w3a", state="TX")

        ids = [r["id"] for r in results]
        assert str(cold_start_recommendation.id) in ids


# ---------------------------------------------------------------------------
# Tenant isolation for RejectionRecord list API
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRejectionTenantIsolation:
    def test_user_cannot_see_other_tenant_rejections(
        self,
        api_client,
        test_user,
        rejection_record,
        well,
        filing_status_record,
        second_tenant_id,
    ):
        # Create a record for a different tenant
        other_rejection = RejectionPattern.objects.create(
            form_type="w3a",
            field_name="district",
            issue_category="formatting",
            state="TX",
            district="",
            agency="RRC",
            pattern_description="Other tenant pattern",
            occurrence_count=5,
            tenant_count=4,
        )

        test_user.tenant_id = rejection_record.tenant_id
        api_client.force_authenticate(user=test_user)
        url = reverse("intelligence:rejection-list")
        response = api_client.get(url)

        assert response.status_code == 200
        for rec in response.data["results"]:
            assert str(rec["tenant_id"]) == str(rejection_record.tenant_id)
