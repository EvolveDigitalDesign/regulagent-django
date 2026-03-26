"""
Tests for RejectionParser: verifies AI-parsing of rejection notes
into structured field-level issues. OpenAI is always mocked.
"""
import pytest

from apps.intelligence.services.rejection_parser import RejectionParser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_openai(mocker):
    """Mock get_openai_client to return a fake client with a structured parse result."""
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
def mock_openai_multi_issue(mocker):
    """Mock returning two issues."""
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create.return_value = mocker.MagicMock(
        choices=[
            mocker.MagicMock(
                message=mocker.MagicMock(
                    content=(
                        '{"issues": ['
                        '{"field_name": "plug_type", "field_value": "CIBP cap",'
                        ' "expected_value": "Cement Plug", "issue_category": "terminology",'
                        ' "issue_subcategory": "naming_convention", "severity": "rejection",'
                        ' "description": "Use Cement Plug", "form_section": "plugging_record",'
                        ' "confidence": 0.95},'
                        '{"field_name": "depth_top", "field_value": "3100",'
                        ' "expected_value": "3103.5", "issue_category": "precision",'
                        ' "issue_subcategory": "rounding", "severity": "revision",'
                        ' "description": "Depth rounded", "form_section": "plugging_record",'
                        ' "confidence": 0.7}'
                        "]}"
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


# ---------------------------------------------------------------------------
# Happy path tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_parse_rejection_returns_issues(mock_openai, rejection_record):
    parser = RejectionParser()
    issues = parser.parse_rejection(rejection_record)

    assert len(issues) == 1
    assert issues[0]["field_name"] == "plug_type"
    assert issues[0]["field_value"] == "CIBP cap"
    assert issues[0]["expected_value"] == "Cement Plug"
    assert issues[0]["issue_category"] == "terminology"
    assert issues[0]["confidence"] == pytest.approx(0.95)


@pytest.mark.django_db
def test_parse_rejection_returns_multiple_issues(mock_openai_multi_issue, rejection_record):
    parser = RejectionParser()
    issues = parser.parse_rejection(rejection_record)

    assert len(issues) == 2
    field_names = {i["field_name"] for i in issues}
    assert "plug_type" in field_names
    assert "depth_top" in field_names


@pytest.mark.django_db
def test_parse_rejection_calls_openai_once(mock_openai, rejection_record):
    parser = RejectionParser()
    parser.parse_rejection(rejection_record)

    assert mock_openai.chat.completions.create.call_count == 1


@pytest.mark.django_db
def test_parse_rejection_passes_form_type_in_prompt(mock_openai, rejection_record):
    """The user prompt must include the form type so the AI knows valid field names."""
    parser = RejectionParser()
    parser.parse_rejection(rejection_record)

    call_args = mock_openai.chat.completions.create.call_args
    messages = call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][1]
    user_message = next(m["content"] for m in messages if m["role"] == "user")
    assert "W3A" in user_message.upper() or "w3a" in user_message


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_parse_rejection_empty_notes_returns_empty_list(mock_openai, rejection_record):
    rejection_record.raw_rejection_notes = ""
    issues = RejectionParser().parse_rejection(rejection_record)

    assert issues == []
    # Should not call OpenAI at all when there are no notes
    mock_openai.chat.completions.create.assert_not_called()


@pytest.mark.django_db
def test_parse_rejection_whitespace_only_notes_returns_empty_list(mock_openai, rejection_record):
    rejection_record.raw_rejection_notes = "   \n\t  "
    issues = RejectionParser().parse_rejection(rejection_record)

    assert issues == []


@pytest.mark.django_db
def test_parse_rejection_api_failure_returns_empty_list(mock_openai, rejection_record):
    mock_openai.chat.completions.create.side_effect = Exception("OpenAI API Error")
    parser = RejectionParser()
    issues = parser.parse_rejection(rejection_record)

    # Must fail gracefully — never raise
    assert issues == []


@pytest.mark.django_db
def test_parse_rejection_malformed_json_returns_empty_list(mocker, rejection_record):
    """If OpenAI returns invalid JSON, parser should return [] gracefully."""
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create.return_value = mocker.MagicMock(
        choices=[mocker.MagicMock(message=mocker.MagicMock(content="NOT_VALID_JSON{{{{"))]
    )
    mocker.patch(
        "apps.intelligence.services.rejection_parser.get_openai_client",
        return_value=mock_client,
    )
    mocker.patch("apps.intelligence.services.rejection_parser.check_rate_limit")

    issues = RejectionParser().parse_rejection(rejection_record)
    assert issues == []


@pytest.mark.django_db
def test_parse_rejection_empty_issues_array(mocker, rejection_record):
    """OpenAI response with empty issues list is valid."""
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create.return_value = mocker.MagicMock(
        choices=[mocker.MagicMock(message=mocker.MagicMock(content='{"issues": []}'))]
    )
    mocker.patch(
        "apps.intelligence.services.rejection_parser.get_openai_client",
        return_value=mock_client,
    )
    mocker.patch("apps.intelligence.services.rejection_parser.check_rate_limit")

    issues = RejectionParser().parse_rejection(rejection_record)
    assert issues == []


# ---------------------------------------------------------------------------
# Field map coverage
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_valid_field_names_w3a(mock_openai):
    parser = RejectionParser()
    fields = parser._get_valid_field_names("w3a")
    assert "plug_type" in fields
    assert "depth_top" in fields
    assert "cement_volume" in fields


@pytest.mark.django_db
def test_get_valid_field_names_c103(mock_openai):
    parser = RejectionParser()
    fields = parser._get_valid_field_names("c103")
    assert "region" in fields
    assert "sub_area" in fields
    assert "coa_figure" in fields


@pytest.mark.django_db
def test_get_valid_field_names_unknown_defaults_to_w3a(mock_openai):
    parser = RejectionParser()
    fields = parser._get_valid_field_names("unknown_form")
    assert "plug_type" in fields
