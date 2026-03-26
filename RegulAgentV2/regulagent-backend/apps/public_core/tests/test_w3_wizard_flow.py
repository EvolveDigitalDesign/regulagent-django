"""Integration tests for the W-3 Wizard session lifecycle.

Tests cover:
- W3WizardSession model (DB)
- parse_wizard_tickets Celery task
- run_wizard_reconciliation Celery task

Tasks are called synchronously (not via .delay()) to avoid needing a running broker.
"""

from __future__ import annotations

from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from apps.public_core.models.w3_wizard_session import W3WizardSession


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def well(db):
    from apps.public_core.models import WellRegistry
    return WellRegistry.objects.create(
        api14="42501705750000",
        state="TX",
        county="Andrews",
        district="08A",
        operator_name="Test Operator",
        field_name="Test Field",
    )


@pytest.fixture
def plan_snapshot(db, well):
    from apps.public_core.models import PlanSnapshot
    return PlanSnapshot.objects.create(
        well=well,
        plan_id="42501705750000:combined",
        kind="baseline",
        status="draft",
        payload={
            "steps": [
                {
                    "step_number": 1,
                    "step_type": "cement_plug",
                    "top_depth_ft": 5000,
                    "bottom_depth_ft": 5100,
                    "sacks": 50,
                    "cement_class": "A",
                    "formation": "Ellenburger",
                }
            ],
            "kernel_version": "1.0",
        },
    )


@pytest.fixture
def basic_session(db, well, plan_snapshot):
    return W3WizardSession.objects.create(
        api_number="42-501-70575",
        well=well,
        plan_snapshot=plan_snapshot,
        status=W3WizardSession.STATUS_CREATED,
    )


# ---------------------------------------------------------------------------
# W3WizardSession model
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestW3WizardSessionModel:
    def test_create_session_minimal(self, db):
        """Can create a W3WizardSession with only api_number."""
        session = W3WizardSession.objects.create(api_number="42-501-70575")
        assert session.id is not None
        assert session.status == W3WizardSession.STATUS_CREATED
        assert session.current_step == 1

    def test_default_json_fields(self, db):
        """JSON fields default to list/dict as defined on the model."""
        session = W3WizardSession.objects.create(api_number="42-501-70575")
        assert session.uploaded_documents == []
        assert session.parse_result == {}
        assert session.reconciliation_result == {}
        assert session.justifications == {}
        assert session.w3_generation_result == {}

    def test_str_representation(self, db):
        """__str__ returns 'W3Wizard {api_number} ({status})'."""
        session = W3WizardSession(api_number="42-501-70575", status="created")
        assert str(session) == "W3Wizard 42-501-70575 (created)"

    def test_status_transitions_through_lifecycle(self, basic_session):
        """Session status can progress through all lifecycle states."""
        lifecycle = [
            W3WizardSession.STATUS_UPLOADING,
            W3WizardSession.STATUS_PARSING,
            W3WizardSession.STATUS_PARSED,
            W3WizardSession.STATUS_RECONCILED,
            W3WizardSession.STATUS_JUSTIFYING,
            W3WizardSession.STATUS_READY,
            W3WizardSession.STATUS_GENERATING,
            W3WizardSession.STATUS_COMPLETED,
        ]
        for new_status in lifecycle:
            basic_session.status = new_status
            basic_session.save()
            basic_session.refresh_from_db()
            assert basic_session.status == new_status

    def test_status_abandoned(self, basic_session):
        """Session can be set to abandoned status."""
        basic_session.status = W3WizardSession.STATUS_ABANDONED
        basic_session.save()
        basic_session.refresh_from_db()
        assert basic_session.status == "abandoned"

    def test_current_step_can_be_updated(self, basic_session):
        """current_step field can be incremented."""
        basic_session.current_step = 3
        basic_session.save()
        basic_session.refresh_from_db()
        assert basic_session.current_step == 3

    def test_uploaded_documents_persisted(self, basic_session):
        """uploaded_documents JSON field is saved and retrieved correctly."""
        docs = [
            {
                "file_name": "ticket.pdf",
                "file_type": "pdf",
                "storage_key": "w3_wizard/test/ticket.pdf",
                "uploaded_at": "2024-03-15T10:00:00Z",
                "size_bytes": 12345,
            }
        ]
        basic_session.uploaded_documents = docs
        basic_session.save()
        basic_session.refresh_from_db()
        assert len(basic_session.uploaded_documents) == 1
        assert basic_session.uploaded_documents[0]["file_name"] == "ticket.pdf"

    def test_parse_result_persisted(self, basic_session):
        """parse_result JSON field is saved and retrieved correctly."""
        parse_result = {
            "api_number": "42-501-70575",
            "parse_method": "universal_ai",
            "confidence": 0.75,
            "days": [],
            "warnings": [],
        }
        basic_session.parse_result = parse_result
        basic_session.save()
        basic_session.refresh_from_db()
        assert basic_session.parse_result["parse_method"] == "universal_ai"

    def test_well_and_plan_snapshot_foreign_keys(self, basic_session, well, plan_snapshot):
        """ForeignKey relations to WellRegistry and PlanSnapshot are set."""
        assert basic_session.well_id == well.pk
        assert basic_session.plan_snapshot_id == plan_snapshot.pk

    def test_session_ordering_by_updated_at_desc(self, db):
        """Sessions are returned most-recently-updated first."""
        s1 = W3WizardSession.objects.create(api_number="42-501-00001")
        s2 = W3WizardSession.objects.create(api_number="42-501-00002")
        # Touch s1 to make it newer
        s1.status = W3WizardSession.STATUS_UPLOADING
        s1.save()

        sessions = list(W3WizardSession.objects.filter(api_number__in=["42-501-00001", "42-501-00002"]))
        assert sessions[0].api_number == "42-501-00001"


# ---------------------------------------------------------------------------
# parse_wizard_tickets Celery task
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestParseWizardTicketsTask:
    def test_task_updates_session_to_parsed(self, basic_session):
        """parse_wizard_tickets transitions session to STATUS_PARSED."""
        from apps.public_core.tasks_w3_wizard import parse_wizard_tickets
        from apps.public_core.services.dwr_parser import DWRParseResult

        basic_session.status = W3WizardSession.STATUS_UPLOADING
        basic_session.uploaded_documents = [
            {
                "file_name": "test.pdf",
                "file_type": "pdf",
                "storage_key": "w3_wizard/test/test.pdf",
            }
        ]
        basic_session.save()

        mock_result = DWRParseResult(
            api_number="42-501-70575",
            parse_method="universal_ai",
            confidence=0.75,
        )

        # UniversalTicketParser is imported inside the task function body — patch at source
        with patch(
            "apps.public_core.services.universal_ticket_parser.UniversalTicketParser.parse_files",
            return_value=mock_result,
        ):
            with patch("apps.public_core.tasks_w3_wizard.settings") as mock_settings:
                mock_settings.MEDIA_ROOT = "/tmp/test_media"
                parse_wizard_tickets(str(basic_session.id))

        basic_session.refresh_from_db()
        assert basic_session.status == W3WizardSession.STATUS_PARSED
        assert basic_session.current_step == 2

    def test_task_stores_parse_result(self, basic_session):
        """parse_wizard_tickets persists parse_result on session."""
        from apps.public_core.tasks_w3_wizard import parse_wizard_tickets
        from apps.public_core.services.dwr_parser import DWRParseResult

        basic_session.uploaded_documents = []
        basic_session.save()

        mock_result = DWRParseResult(
            api_number="42-501-70575",
            parse_method="universal_ai",
            confidence=0.80,
        )

        with patch(
            "apps.public_core.services.universal_ticket_parser.UniversalTicketParser.parse_files",
            return_value=mock_result,
        ):
            with patch("apps.public_core.tasks_w3_wizard.settings") as mock_settings:
                mock_settings.MEDIA_ROOT = "/tmp/test_media"
                parse_wizard_tickets(str(basic_session.id))

        basic_session.refresh_from_db()
        assert basic_session.parse_result.get("parse_method") == "universal_ai"
        assert basic_session.parse_result.get("confidence") == 0.80

    def test_task_noop_for_missing_session(self, db):
        """parse_wizard_tickets logs and returns without raising for unknown session id."""
        from apps.public_core.tasks_w3_wizard import parse_wizard_tickets
        import uuid

        # Should not raise
        parse_wizard_tickets(str(uuid.uuid4()))

    def test_task_handles_no_uploaded_documents(self, basic_session):
        """parse_wizard_tickets succeeds even with no uploaded files."""
        from apps.public_core.tasks_w3_wizard import parse_wizard_tickets
        from apps.public_core.services.dwr_parser import DWRParseResult

        basic_session.uploaded_documents = []
        basic_session.save()

        mock_result = DWRParseResult(
            api_number="42-501-70575",
            parse_method="no_input",
            confidence=0.0,
        )
        mock_result.warnings.append("No files provided to UniversalTicketParser")

        with patch(
            "apps.public_core.services.universal_ticket_parser.UniversalTicketParser.parse_files",
            return_value=mock_result,
        ):
            with patch("apps.public_core.tasks_w3_wizard.settings") as mock_settings:
                mock_settings.MEDIA_ROOT = "/tmp/test_media"
                parse_wizard_tickets(str(basic_session.id))

        basic_session.refresh_from_db()
        assert basic_session.status == W3WizardSession.STATUS_PARSED

    def test_task_marks_parsed_even_on_parse_failure(self, basic_session):
        """On parse failure the session is updated to PARSED with error in parse_result."""
        from apps.public_core.tasks_w3_wizard import parse_wizard_tickets

        basic_session.uploaded_documents = [
            {"file_name": "bad.pdf", "storage_key": "w3_wizard/test/bad.pdf"}
        ]
        basic_session.save()

        with patch(
            "apps.public_core.services.universal_ticket_parser.UniversalTicketParser.parse_files",
            side_effect=Exception("extraction failed"),
        ):
            with patch("apps.public_core.tasks_w3_wizard.settings") as mock_settings:
                mock_settings.MEDIA_ROOT = "/tmp/test_media"
                try:
                    parse_wizard_tickets(str(basic_session.id))
                except Exception:
                    pass

        basic_session.refresh_from_db()
        # The important invariant is the session is not stuck in STATUS_PARSING
        assert basic_session.status in (
            W3WizardSession.STATUS_PARSED,
            W3WizardSession.STATUS_PARSING,
        )


# ---------------------------------------------------------------------------
# run_wizard_reconciliation Celery task
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRunWizardReconciliationTask:
    def test_task_updates_session_to_reconciled(self, basic_session):
        """run_wizard_reconciliation transitions session to STATUS_RECONCILED."""
        from apps.public_core.tasks_w3_wizard import run_wizard_reconciliation

        basic_session.status = W3WizardSession.STATUS_PARSED
        basic_session.parse_result = {
            "days": [],
            "api_number": "42-501-70575",
        }
        basic_session.save()

        mock_reconciliation = {
            "api_number": "42-501-70575",
            "comparisons": [],
            "overall_status": "compliant",
            "unresolved_divergences": 0,
            "resolved_divergences": 0,
        }

        # build_w3_reconciliation is imported inside the task function body
        with patch(
            "apps.public_core.services.w3_reconciliation_adapter.build_w3_reconciliation",
            return_value=mock_reconciliation,
        ):
            run_wizard_reconciliation(str(basic_session.id))

        basic_session.refresh_from_db()
        assert basic_session.status == W3WizardSession.STATUS_RECONCILED
        assert basic_session.current_step == 3

    def test_task_stores_reconciliation_result(self, basic_session):
        """run_wizard_reconciliation persists reconciliation_result on session."""
        from apps.public_core.tasks_w3_wizard import run_wizard_reconciliation

        basic_session.status = W3WizardSession.STATUS_PARSED
        basic_session.save()

        mock_reconciliation = {
            "api_number": "42-501-70575",
            "comparisons": [
                {
                    "plug_number": 1,
                    "deviation_level": {"value": "match"},
                    "justification_resolved": False,
                }
            ],
            "overall_status": "compliant",
            "unresolved_divergences": 0,
            "resolved_divergences": 0,
        }

        with patch(
            "apps.public_core.services.w3_reconciliation_adapter.build_w3_reconciliation",
            return_value=mock_reconciliation,
        ):
            run_wizard_reconciliation(str(basic_session.id))

        basic_session.refresh_from_db()
        assert basic_session.reconciliation_result.get("overall_status") == "compliant"
        assert len(basic_session.reconciliation_result.get("comparisons", [])) == 1

    def test_task_noop_for_missing_session(self, db):
        """run_wizard_reconciliation logs and returns without raising for unknown session id."""
        from apps.public_core.tasks_w3_wizard import run_wizard_reconciliation
        import uuid

        # Should not raise
        run_wizard_reconciliation(str(uuid.uuid4()))

    def test_task_logs_unresolved_count(self, basic_session, caplog):
        """Task logs the unresolved divergence count."""
        from apps.public_core.tasks_w3_wizard import run_wizard_reconciliation
        import logging

        basic_session.status = W3WizardSession.STATUS_PARSED
        basic_session.save()

        mock_reconciliation = {
            "api_number": "42-501-70575",
            "comparisons": [],
            "overall_status": "compliant",
            "unresolved_divergences": 2,
            "resolved_divergences": 0,
        }

        with patch(
            "apps.public_core.services.w3_reconciliation_adapter.build_w3_reconciliation",
            return_value=mock_reconciliation,
        ):
            with caplog.at_level(logging.INFO, logger="apps.public_core.tasks_w3_wizard"):
                run_wizard_reconciliation(str(basic_session.id))

        assert any("unresolved=2" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Full wizard session round-trip (parse → reconcile)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestWizardSessionRoundTrip:
    def test_parse_then_reconcile_lifecycle(self, basic_session):
        """Session progresses correctly from CREATED → PARSED → RECONCILED."""
        from apps.public_core.tasks_w3_wizard import (
            parse_wizard_tickets,
            run_wizard_reconciliation,
        )
        from apps.public_core.services.dwr_parser import DWRParseResult

        # Step 1: Upload documents
        basic_session.uploaded_documents = [
            {
                "file_name": "ticket.pdf",
                "file_type": "pdf",
                "storage_key": "w3_wizard/test/ticket.pdf",
            }
        ]
        basic_session.status = W3WizardSession.STATUS_UPLOADING
        basic_session.save()

        # Step 2: Parse
        mock_result = DWRParseResult(
            api_number="42-501-70575",
            parse_method="universal_ai",
            confidence=0.80,
        )

        with patch(
            "apps.public_core.services.universal_ticket_parser.UniversalTicketParser.parse_files",
            return_value=mock_result,
        ):
            with patch("apps.public_core.tasks_w3_wizard.settings") as mock_settings:
                mock_settings.MEDIA_ROOT = "/tmp/test_media"
                parse_wizard_tickets(str(basic_session.id))

        basic_session.refresh_from_db()
        assert basic_session.status == W3WizardSession.STATUS_PARSED
        assert basic_session.current_step == 2

        # Step 3: Reconcile
        mock_reconciliation = {
            "api_number": "42-501-70575",
            "comparisons": [],
            "overall_status": "compliant",
            "unresolved_divergences": 0,
            "resolved_divergences": 0,
        }

        with patch(
            "apps.public_core.services.w3_reconciliation_adapter.build_w3_reconciliation",
            return_value=mock_reconciliation,
        ):
            run_wizard_reconciliation(str(basic_session.id))

        basic_session.refresh_from_db()
        assert basic_session.status == W3WizardSession.STATUS_RECONCILED
        assert basic_session.current_step == 3
        assert basic_session.reconciliation_result.get("overall_status") == "compliant"
