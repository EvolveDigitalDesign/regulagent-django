"""
Tests for intelligence app models: creation, constraints, field defaults,
encrypt/decrypt, and cascade behaviour.
"""
import uuid

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.intelligence.models import (
    FilingStatusRecord,
    PortalCredential,
    Recommendation,
    RecommendationInteraction,
    RejectionPattern,
    RejectionRecord,
)


# ---------------------------------------------------------------------------
# FilingStatusRecord
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFilingStatusRecord:
    def test_create_with_valid_data(self, well, tenant_id):
        record = FilingStatusRecord.objects.create(
            filing_id="RRC-2024-001",
            tenant_id=tenant_id,
            well=well,
            agency="RRC",
            form_type="w3a",
        )
        assert record.id is not None
        assert record.status == "pending"  # default

    def test_status_choices(self, well, tenant_id):
        for status_val in ["pending", "under_review", "approved", "rejected",
                           "revision_requested", "deficiency"]:
            FilingStatusRecord.objects.create(
                filing_id=f"RRC-{status_val}",
                tenant_id=tenant_id,
                well=well,
                agency="RRC",
                form_type="w3a",
                status=status_val,
            )
        assert FilingStatusRecord.objects.filter(tenant_id=tenant_id).count() == 6

    def test_str_representation(self, filing_status_record):
        s = str(filing_status_record)
        assert "RRC" in s
        assert "w3a" in s
        assert "rejected" in s

    def test_optional_fk_fields_default_null(self, well, tenant_id):
        record = FilingStatusRecord.objects.create(
            filing_id="RRC-2024-002",
            tenant_id=tenant_id,
            well=well,
            agency="RRC",
            form_type="w3a",
        )
        assert record.w3_form_id is None
        assert record.plan_snapshot_id is None
        assert record.c103_form_id is None

    def test_raw_portal_data_defaults_to_empty_dict(self, well, tenant_id):
        record = FilingStatusRecord.objects.create(
            filing_id="RRC-2024-003",
            tenant_id=tenant_id,
            well=well,
            agency="RRC",
            form_type="w3a",
        )
        assert record.raw_portal_data == {}


# ---------------------------------------------------------------------------
# RejectionRecord
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRejectionRecord:
    def test_create_with_valid_data(self, filing_status_record, well, tenant_id):
        record = RejectionRecord.objects.create(
            filing_status=filing_status_record,
            tenant_id=tenant_id,
            well=well,
            agency="RRC",
            form_type="w3a",
        )
        assert record.id is not None

    def test_parse_status_defaults_to_pending(self, filing_status_record, well, tenant_id):
        record = RejectionRecord.objects.create(
            filing_status=filing_status_record,
            tenant_id=tenant_id,
            well=well,
            agency="RRC",
            form_type="w3a",
        )
        assert record.parse_status == "pending"

    def test_parsed_issues_defaults_to_empty_list(self, filing_status_record, well, tenant_id):
        record = RejectionRecord.objects.create(
            filing_status=filing_status_record,
            tenant_id=tenant_id,
            well=well,
            agency="RRC",
            form_type="w3a",
        )
        assert record.parsed_issues == []

    def test_submitted_form_snapshot_defaults_to_empty_dict(
        self, filing_status_record, well, tenant_id
    ):
        record = RejectionRecord.objects.create(
            filing_status=filing_status_record,
            tenant_id=tenant_id,
            well=well,
            agency="RRC",
            form_type="w3a",
        )
        assert record.submitted_form_snapshot == {}

    def test_cascade_delete_with_filing_status(self, filing_status_record, well, tenant_id):
        record = RejectionRecord.objects.create(
            filing_status=filing_status_record,
            tenant_id=tenant_id,
            well=well,
            agency="RRC",
            form_type="w3a",
        )
        record_id = record.id
        filing_status_record.delete()
        assert not RejectionRecord.objects.filter(id=record_id).exists()

    def test_str_representation(self, rejection_record):
        s = str(rejection_record)
        assert "RRC" in s
        assert "w3a" in s


# ---------------------------------------------------------------------------
# RejectionPattern
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRejectionPattern:
    def test_create_with_valid_data(self):
        pattern = RejectionPattern.objects.create(
            form_type="w3a",
            field_name="plug_type",
            issue_category="terminology",
            state="TX",
            district="8A",
            agency="RRC",
            pattern_description="Test pattern",
        )
        assert pattern.id is not None
        assert pattern.occurrence_count == 0
        assert pattern.tenant_count == 0
        assert pattern.confidence == 0.0
        assert pattern.is_trending is False

    def test_unique_together_constraint(self, rejection_pattern):
        with pytest.raises(IntegrityError):
            RejectionPattern.objects.create(
                form_type=rejection_pattern.form_type,
                field_name=rejection_pattern.field_name,
                issue_category=rejection_pattern.issue_category,
                state=rejection_pattern.state,
                district=rejection_pattern.district,
                agency=rejection_pattern.agency,
                pattern_description="Duplicate",
            )

    def test_unique_together_different_district_allowed(self, rejection_pattern):
        # Same key but different district should not raise
        different = RejectionPattern.objects.create(
            form_type=rejection_pattern.form_type,
            field_name=rejection_pattern.field_name,
            issue_category=rejection_pattern.issue_category,
            state=rejection_pattern.state,
            district="7C",  # different district
            agency=rejection_pattern.agency,
            pattern_description="Different district",
        )
        assert different.id is not None

    def test_no_tenant_id_field(self):
        """RejectionPattern must NOT have a tenant_id field (cross-tenant by design)."""
        assert not hasattr(RejectionPattern, "tenant_id")

    def test_str_representation(self, rejection_pattern):
        s = str(rejection_pattern)
        assert "plug_type" in s
        assert "terminology" in s


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRecommendation:
    def test_create_with_valid_data(self, rejection_pattern):
        rec = Recommendation.objects.create(
            pattern=rejection_pattern,
            form_type="w3a",
            field_name="plug_type",
            title="Check plug type",
            description="Use Cement Plug.",
            scope="cross_tenant",
            priority="high",
        )
        assert rec.id is not None
        assert rec.is_active is True
        assert rec.times_shown == 0
        assert rec.times_accepted == 0
        assert rec.acceptance_rate == 0.0

    def test_acceptance_rate_calculation(self, recommendation):
        recommendation.times_shown = 10
        recommendation.times_accepted = 4
        recommendation.acceptance_rate = recommendation.times_accepted / recommendation.times_shown
        recommendation.save()
        recommendation.refresh_from_db()
        assert recommendation.acceptance_rate == pytest.approx(0.4)

    def test_create_without_pattern(self):
        rec = Recommendation.objects.create(
            form_type="c103",
            field_name="plug_type",
            title="Cold start recommendation",
            description="Always use Cement Plug.",
            scope="cold_start",
            priority="medium",
        )
        assert rec.pattern is None

    def test_str_representation(self, recommendation):
        s = str(recommendation)
        assert "plug_type" in s
        assert "w3a" in s


# ---------------------------------------------------------------------------
# RecommendationInteraction
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRecommendationInteraction:
    def test_create_interaction(self, recommendation, test_user, tenant_id):
        interaction = RecommendationInteraction.objects.create(
            recommendation=recommendation,
            tenant_id=tenant_id,
            user=test_user,
            action="shown",
        )
        assert interaction.id is not None
        assert interaction.action == "shown"

    def test_cascade_delete_with_recommendation(
        self, recommendation, test_user, tenant_id
    ):
        interaction = RecommendationInteraction.objects.create(
            recommendation=recommendation,
            tenant_id=tenant_id,
            user=test_user,
            action="accepted",
        )
        interaction_id = interaction.id
        recommendation.delete()
        assert not RecommendationInteraction.objects.filter(id=interaction_id).exists()

    def test_all_action_choices(self, recommendation, test_user, tenant_id):
        for action in ["shown", "accepted", "dismissed", "snoozed"]:
            RecommendationInteraction.objects.create(
                recommendation=recommendation,
                tenant_id=tenant_id,
                user=test_user,
                action=action,
            )
        assert (
            RecommendationInteraction.objects.filter(tenant_id=tenant_id).count() == 4
        )


# ---------------------------------------------------------------------------
# PortalCredential
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPortalCredential:
    def test_encrypt_decrypt_roundtrip(self, settings, tenant_id):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        settings.PORTAL_CREDENTIAL_KEY = key

        cred = PortalCredential(tenant_id=tenant_id, agency="RRC")
        cred.set_username("testuser@rrc.state.tx.us")
        cred.set_password("SecureP@ssword123!")

        assert cred.get_username() == "testuser@rrc.state.tx.us"
        assert cred.get_password() == "SecureP@ssword123!"

    def test_encrypted_bytes_not_plaintext(self, settings, tenant_id):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        settings.PORTAL_CREDENTIAL_KEY = key

        cred = PortalCredential(tenant_id=tenant_id, agency="RRC")
        cred.set_password("mysecret")

        assert b"mysecret" not in bytes(cred.encrypted_password)

    def test_unique_together_tenant_agency(self, db, settings, tenant_id):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        settings.PORTAL_CREDENTIAL_KEY = key

        cred1 = PortalCredential(tenant_id=tenant_id, agency="RRC")
        cred1.set_username("user1")
        cred1.set_password("pass1")
        cred1.save()

        cred2 = PortalCredential(tenant_id=tenant_id, agency="RRC")
        cred2.set_username("user2")
        cred2.set_password("pass2")

        with pytest.raises(IntegrityError):
            cred2.save()

    def test_str_representation(self, db, settings, tenant_id):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        settings.PORTAL_CREDENTIAL_KEY = key

        cred = PortalCredential(tenant_id=tenant_id, agency="NMOCD")
        cred.set_username("nmuser")
        cred.set_password("nmpass")
        cred.save()

        assert "NMOCD" in str(cred)
        assert str(tenant_id) in str(cred)
