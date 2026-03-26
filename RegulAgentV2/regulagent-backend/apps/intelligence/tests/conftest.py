"""
Shared pytest fixtures for the intelligence app test suite.
"""
import uuid

import pytest
from django.utils import timezone

from apps.intelligence.models import (
    FilingStatusRecord,
    Recommendation,
    RejectionPattern,
    RejectionRecord,
)


# ---------------------------------------------------------------------------
# Tenant IDs (UUIDs only — no schema isolation needed for intelligence models)
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_id():
    return uuid.uuid4()


@pytest.fixture
def second_tenant_id():
    return uuid.uuid4()


@pytest.fixture
def third_tenant_id():
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Well (shared helper)
# ---------------------------------------------------------------------------


@pytest.fixture
def well(db):
    from apps.public_core.models import WellRegistry

    return WellRegistry.objects.create(
        api14="42501705750000",
        state="TX",
        county="Andrews",
        district="8A",
        operator_name="Test Operator Inc",
        field_name="Test Field",
        lease_name="Test Lease",
        well_number="1",
    )


# ---------------------------------------------------------------------------
# FilingStatusRecord
# ---------------------------------------------------------------------------


@pytest.fixture
def filing_status_record(db, well, tenant_id):
    return FilingStatusRecord.objects.create(
        filing_id="RRC-2024-00001",
        tenant_id=tenant_id,
        well=well,
        agency="RRC",
        form_type="w3a",
        status="rejected",
        agency_remarks="Plug type must be 'Cement Plug'. Depth values appear rounded.",
        reviewer_name="Jane Smith",
        state="TX",
        district="8A",
        county="Andrews",
    )


@pytest.fixture
def approved_filing_status(db, well, tenant_id):
    return FilingStatusRecord.objects.create(
        filing_id="RRC-2024-00002",
        tenant_id=tenant_id,
        well=well,
        agency="RRC",
        form_type="w3a",
        status="approved",
        state="TX",
        district="8A",
    )


# ---------------------------------------------------------------------------
# RejectionRecord
# ---------------------------------------------------------------------------


@pytest.fixture
def rejection_record(db, filing_status_record, well, tenant_id):
    return RejectionRecord.objects.create(
        filing_status=filing_status_record,
        tenant_id=tenant_id,
        well=well,
        agency="RRC",
        form_type="w3a",
        state="TX",
        district="8A",
        county="Andrews",
        raw_rejection_notes="Plug type must be 'Cement Plug'. Depth values appear rounded.",
        rejection_date=timezone.now().date(),
        reviewer_name="Jane Smith",
        parse_status="pending",
        submitted_form_snapshot={
            "plug_type": "CIBP cap",
            "depth_top": 3100,
            "depth_bottom": 3200,
        },
    )


@pytest.fixture
def parsed_rejection_record(db, filing_status_record, well, tenant_id):
    return RejectionRecord.objects.create(
        filing_status=filing_status_record,
        tenant_id=tenant_id,
        well=well,
        agency="RRC",
        form_type="w3a",
        state="TX",
        district="8A",
        county="Andrews",
        raw_rejection_notes="Plug type must be 'Cement Plug'.",
        rejection_date=timezone.now().date(),
        parse_status="parsed",
        parsed_issues=[
            {
                "field_name": "plug_type",
                "field_value": "CIBP cap",
                "expected_value": "Cement Plug",
                "issue_category": "terminology",
                "issue_subcategory": "naming_convention",
                "severity": "rejection",
                "description": "Use Cement Plug",
                "form_section": "plugging_record",
                "confidence": 0.95,
            }
        ],
    )


# ---------------------------------------------------------------------------
# RejectionPattern
# ---------------------------------------------------------------------------


@pytest.fixture
def rejection_pattern(db):
    return RejectionPattern.objects.create(
        form_type="w3a",
        field_name="plug_type",
        issue_category="terminology",
        issue_subcategory="naming_convention",
        state="TX",
        district="8A",
        agency="RRC",
        pattern_description="Use 'Cement Plug' not 'CIBP cap'",
        example_bad_value="CIBP cap",
        example_good_value="Cement Plug",
        occurrence_count=10,
        tenant_count=5,
        rejection_rate=0.3,
        confidence=0.85,
    )


@pytest.fixture
def private_pattern(db):
    """Pattern with tenant_count < 3 — should not be surfaced cross-tenant."""
    return RejectionPattern.objects.create(
        form_type="w3a",
        field_name="cement_class",
        issue_category="compliance",
        issue_subcategory="incorrect_method",
        state="TX",
        district="",
        agency="RRC",
        pattern_description="Cement class issue",
        occurrence_count=2,
        tenant_count=1,
        confidence=0.4,
    )


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------


@pytest.fixture
def recommendation(db, rejection_pattern):
    return Recommendation.objects.create(
        pattern=rejection_pattern,
        form_type="w3a",
        field_name="plug_type",
        state="TX",
        district="8A",
        title="Use 'Cement Plug' instead of 'CIBP cap'",
        description="RRC requires the term 'Cement Plug'. Using 'CIBP cap' causes rejections.",
        suggested_value="Cement Plug",
        trigger_condition={
            "field_name": "plug_type",
            "trigger_values": ["CIBP cap", "CIBP", "cibp"],
        },
        scope="cross_tenant",
        priority="high",
        is_active=True,
    )


@pytest.fixture
def cold_start_recommendation(db):
    """Recommendation with no linked pattern — cold_start scope."""
    return Recommendation.objects.create(
        form_type="w3a",
        field_name="cement_volume",
        state="TX",
        title="Check cement volume calculation",
        description="Ensure cement volume matches plug interval.",
        scope="cold_start",
        priority="medium",
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Mock fixtures for external services
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_openai_parser(mocker):
    """
    Mock get_openai_client for RejectionParser to return structured parse result.
    """
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create.return_value = mocker.MagicMock(
        choices=[
            mocker.MagicMock(
                message=mocker.MagicMock(
                    content=(
                        '{"issues": [{"field_name": "plug_type", "field_value": "CIBP cap",'
                        ' "expected_value": "Cement Plug", "issue_category": "terminology",'
                        ' "issue_subcategory": "naming_convention", "severity": "rejection",'
                        ' "description": "Use Cement Plug", "form_section": "plugging_record",'
                        ' "confidence": 0.95}]}'
                    )
                )
            )
        ]
    )
    mocker.patch(
        "apps.intelligence.services.rejection_parser.get_openai_client",
        return_value=mock_client,
    )
    mocker.patch("apps.intelligence.services.rejection_parser.check_rate_limit")
    return mock_client


@pytest.fixture
def mock_openai_embedder(mocker):
    """Mock get_openai_client for RejectionEmbedder to return a fake vector."""
    mock_client = mocker.MagicMock()
    mock_client.embeddings.create.return_value = mocker.MagicMock(
        data=[mocker.MagicMock(embedding=[0.1] * 3072)]
    )
    mocker.patch(
        "apps.intelligence.services.rejection_embedder.get_openai_client",
        return_value=mock_client,
    )
    mocker.patch("apps.intelligence.services.rejection_embedder.check_rate_limit")
    return mock_client
