"""
Tests for document_pipeline.py — jurisdiction detection, adapter registry,
and document indexing pipeline.
"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

from apps.public_core.services.document_pipeline import (
    detect_jurisdiction,
    get_adapter,
    ADAPTER_REGISTRY,
)


# ---------------------------------------------------------------------------
# detect_jurisdiction
# ---------------------------------------------------------------------------

def test_detect_jurisdiction_nm_with_dashes():
    assert detect_jurisdiction("30-015-28692") == "NM"


def test_detect_jurisdiction_nm_digits_only():
    assert detect_jurisdiction("3001528692") == "NM"


def test_detect_jurisdiction_nm_full_api14():
    assert detect_jurisdiction("30015286920000") == "NM"


def test_detect_jurisdiction_tx_with_dashes():
    assert detect_jurisdiction("42-501-70575") == "TX"


def test_detect_jurisdiction_tx_digits_only():
    assert detect_jurisdiction("4250170575") == "TX"


def test_detect_jurisdiction_explicit_overrides_prefix():
    """An explicit state always wins, even if the prefix would say otherwise."""
    assert detect_jurisdiction("30-015-28692", explicit="TX") == "TX"


def test_detect_jurisdiction_explicit_case_insensitive():
    assert detect_jurisdiction("42-501-70575", explicit="nm") == "NM"


def test_detect_jurisdiction_none_api_defaults_to_tx():
    """Non-NM prefix falls back to TX."""
    assert detect_jurisdiction("05-123-45678") == "TX"


# ---------------------------------------------------------------------------
# ADAPTER_REGISTRY
# ---------------------------------------------------------------------------

def test_adapter_registry_has_nm():
    assert "NM" in ADAPTER_REGISTRY


def test_adapter_registry_has_tx():
    assert "TX" in ADAPTER_REGISTRY


# ---------------------------------------------------------------------------
# get_adapter
# ---------------------------------------------------------------------------

def test_get_adapter_nm_returns_nm_adapter():
    from apps.public_core.services.adapters.nm_adapter import NMAdapter
    adapter = get_adapter("NM")
    assert isinstance(adapter, NMAdapter)


def test_get_adapter_tx_returns_tx_adapter():
    from apps.public_core.services.adapters.tx_adapter import TXAdapter
    adapter = get_adapter("TX")
    assert isinstance(adapter, TXAdapter)


def test_get_adapter_nm_state_code():
    adapter = get_adapter("NM")
    assert adapter.state_code() == "NM"


def test_get_adapter_tx_state_code():
    adapter = get_adapter("TX")
    assert adapter.state_code() == "TX"


def test_get_adapter_case_insensitive():
    from apps.public_core.services.adapters.nm_adapter import NMAdapter
    adapter = get_adapter("nm")
    assert isinstance(adapter, NMAdapter)


def test_get_adapter_unknown_raises():
    with pytest.raises(ValueError, match="No adapter registered"):
        get_adapter("ZZ")


# ---------------------------------------------------------------------------
# index_single_document — full pipeline with mocks
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@patch("apps.public_core.services.document_pipeline.vectorize_extracted_document")
@patch("apps.public_core.services.document_pipeline.extract_json_from_pdf")
@patch("apps.public_core.services.document_pipeline.classify_document")
@patch("apps.public_core.services.document_pipeline.get_adapter")
def test_index_single_document_creates_extracted_document(
    mock_get_adapter,
    mock_classify,
    mock_extract,
    mock_vectorize,
):
    from apps.public_core.services.document_pipeline import index_single_document
    from apps.public_core.services.adapters.base import DocumentSpec
    from apps.public_core.models import ExtractedDocument

    # Mock adapter download
    mock_adapter = MagicMock()
    mock_local_path = MagicMock(spec=Path)
    mock_local_path.name = "c-101_report.pdf"
    mock_adapter.download_document.return_value = mock_local_path
    mock_get_adapter.return_value = mock_adapter

    mock_classify.return_value = "c_101"

    from apps.public_core.services.openai_extraction import ExtractionResult
    mock_extract.return_value = ExtractionResult(
        document_type="c_101",
        json_data={"header": {"permit_number": "TEST-001"}},
        model_tag="gpt-4o",
        errors=[],
    )

    doc = DocumentSpec(
        filename="c-101_report.pdf",
        url="https://example.com/c-101_report.pdf",
        file_size=1024,
        date="2024-01-01",
        doc_type=None,
    )

    ed = index_single_document(doc, "30-015-28692", well=None, session=None)

    assert ed is not None
    assert ed.api_number == "30-015-28692"
    assert ed.document_type == "c_101"
    assert ed.status == "success"
    mock_vectorize.assert_called_once_with(ed)


@pytest.mark.django_db
@patch("apps.public_core.services.document_pipeline.vectorize_extracted_document")
@patch("apps.public_core.services.document_pipeline.extract_json_from_pdf")
@patch("apps.public_core.services.document_pipeline.classify_document")
@patch("apps.public_core.services.document_pipeline.get_adapter")
def test_index_single_document_partial_status_on_errors(
    mock_get_adapter,
    mock_classify,
    mock_extract,
    mock_vectorize,
):
    from apps.public_core.services.document_pipeline import index_single_document
    from apps.public_core.services.adapters.base import DocumentSpec
    from apps.public_core.services.openai_extraction import ExtractionResult

    mock_adapter = MagicMock()
    mock_local_path = MagicMock(spec=Path)
    mock_local_path.name = "sundry_notice.pdf"
    mock_adapter.download_document.return_value = mock_local_path
    mock_get_adapter.return_value = mock_adapter

    mock_classify.return_value = "sundry"
    mock_extract.return_value = ExtractionResult(
        document_type="sundry",
        json_data={"header": {}},
        model_tag="gpt-4o",
        errors=["Missing notice_type"],
    )

    doc = DocumentSpec(
        filename="sundry_notice.pdf",
        url="https://example.com/sundry_notice.pdf",
        file_size=512,
        date="2024-02-01",
        doc_type=None,
    )

    ed = index_single_document(doc, "30-015-28692", well=None, session=None)

    assert ed is not None
    assert ed.status == "partial"
    assert "Missing notice_type" in ed.errors


@pytest.mark.django_db
@patch("apps.public_core.services.document_pipeline.get_adapter")
def test_index_single_document_unknown_type_returns_none(mock_get_adapter):
    from apps.public_core.services.document_pipeline import index_single_document
    from apps.public_core.services.adapters.base import DocumentSpec

    mock_adapter = MagicMock()
    mock_local_path = MagicMock(spec=Path)
    mock_local_path.name = "unknown_doc.pdf"
    mock_adapter.download_document.return_value = mock_local_path
    mock_get_adapter.return_value = mock_adapter

    with patch("apps.public_core.services.document_pipeline.classify_document", return_value="unknown"):
        doc = DocumentSpec(
            filename="unknown_doc.pdf",
            url="https://example.com/unknown_doc.pdf",
            file_size=256,
            date="2024-03-01",
            doc_type=None,
        )
        result = index_single_document(doc, "30-015-28692", well=None, session=None)

    assert result is None


@pytest.mark.django_db
@patch("apps.public_core.services.document_pipeline.vectorize_extracted_document")
@patch("apps.public_core.services.document_pipeline.extract_json_from_pdf")
@patch("apps.public_core.services.document_pipeline.classify_document")
@patch("apps.public_core.services.document_pipeline.get_adapter")
def test_index_single_document_idempotency(
    mock_get_adapter,
    mock_classify,
    mock_extract,
    mock_vectorize,
):
    """If an ExtractedDocument already exists for the same file, skip re-extraction."""
    from apps.public_core.services.document_pipeline import index_single_document
    from apps.public_core.services.adapters.base import DocumentSpec
    from apps.public_core.models import ExtractedDocument

    # Pre-create an ExtractedDocument with this filename in source_path
    existing = ExtractedDocument.objects.create(
        api_number="30-015-28692",
        document_type="c_101",
        source_path="/tmp/c-101_report.pdf",
        model_tag="gpt-4o",
        status="success",
        errors=[],
        json_data={},
    )

    doc = DocumentSpec(
        filename="c-101_report.pdf",
        url="https://example.com/c-101_report.pdf",
        file_size=1024,
        date="2024-01-01",
        doc_type=None,
    )

    result = index_single_document(doc, "30-015-28692", well=None, session=None)

    # Should return the existing document without re-extracting
    assert result is not None
    assert result.id == existing.id
    # No download, classify, or extract calls made
    mock_get_adapter.return_value.download_document.assert_not_called()
    mock_classify.assert_not_called()
    mock_extract.assert_not_called()
    mock_vectorize.assert_not_called()
