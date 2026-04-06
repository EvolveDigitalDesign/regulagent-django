"""
Tests for research_rag.py — RAG query service.

Covers:
- _build_system_prompt() content
- _extract_citations() deduplication and excerpt truncation
- _build_context_prompt() formatting
- get_chat_history() serialization
- stream_research_answer() SSE format and message persistence
"""
import json
import pytest
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# _build_system_prompt
# ---------------------------------------------------------------------------

def test_build_system_prompt_contains_api_number():
    from apps.public_core.services.research_rag import _build_system_prompt
    prompt = _build_system_prompt("30-015-28692", "NM")
    assert "30-015-28692" in prompt


def test_build_system_prompt_contains_state():
    from apps.public_core.services.research_rag import _build_system_prompt
    prompt = _build_system_prompt("30-015-28692", "NM")
    assert "NM" in prompt


def test_build_system_prompt_tx():
    from apps.public_core.services.research_rag import _build_system_prompt
    prompt = _build_system_prompt("42-501-70575", "TX")
    assert "42-501-70575" in prompt
    assert "TX" in prompt


def test_build_system_prompt_is_string():
    from apps.public_core.services.research_rag import _build_system_prompt
    prompt = _build_system_prompt("30-015-28692", "NM")
    assert isinstance(prompt, str)
    assert len(prompt) > 0


# ---------------------------------------------------------------------------
# _extract_citations
# ---------------------------------------------------------------------------

def test_extract_citations_basic():
    from apps.public_core.services.research_rag import _extract_citations
    sections = [
        {"doc_type": "c_103", "section_name": "header", "section_text": "test content"},
    ]
    citations = _extract_citations(sections)
    assert len(citations) == 1
    assert citations[0]["doc_type"] == "c_103"
    assert citations[0]["section_name"] == "header"
    assert citations[0]["excerpt"] == "test content"


def test_extract_citations_deduplication():
    """Same (doc_type, section_name) pair should appear only once."""
    from apps.public_core.services.research_rag import _extract_citations
    sections = [
        {"doc_type": "c_103", "section_name": "header", "section_text": "first content"},
        {"doc_type": "c_103", "section_name": "header", "section_text": "duplicate content"},
    ]
    citations = _extract_citations(sections)
    assert len(citations) == 1
    # First occurrence wins
    assert citations[0]["excerpt"] == "first content"


def test_extract_citations_different_sections_not_deduped():
    from apps.public_core.services.research_rag import _extract_citations
    sections = [
        {"doc_type": "c_103", "section_name": "header", "section_text": "header text"},
        {"doc_type": "c_103", "section_name": "remarks", "section_text": "remarks text"},
    ]
    citations = _extract_citations(sections)
    assert len(citations) == 2


def test_extract_citations_different_doc_types_not_deduped():
    from apps.public_core.services.research_rag import _extract_citations
    sections = [
        {"doc_type": "c_101", "section_name": "header", "section_text": "c101 header"},
        {"doc_type": "c_103", "section_name": "header", "section_text": "c103 header"},
    ]
    citations = _extract_citations(sections)
    assert len(citations) == 2


def test_extract_citations_truncates_long_excerpt():
    from apps.public_core.services.research_rag import _extract_citations
    long_text = "x" * 500
    sections = [
        {"doc_type": "c_105", "section_name": "completion_data", "section_text": long_text},
    ]
    citations = _extract_citations(sections)
    assert len(citations) == 1
    assert len(citations[0]["excerpt"]) == 203  # 200 chars + "..."
    assert citations[0]["excerpt"].endswith("...")


def test_extract_citations_short_text_not_truncated():
    from apps.public_core.services.research_rag import _extract_citations
    short_text = "Short text"
    sections = [
        {"doc_type": "sundry", "section_name": "description", "section_text": short_text},
    ]
    citations = _extract_citations(sections)
    assert citations[0]["excerpt"] == short_text
    assert not citations[0]["excerpt"].endswith("...")


def test_extract_citations_empty_sections():
    from apps.public_core.services.research_rag import _extract_citations
    citations = _extract_citations([])
    assert citations == []


def test_extract_citations_custom_max_length():
    from apps.public_core.services.research_rag import _extract_citations
    text = "a" * 100
    sections = [{"doc_type": "gau", "section_name": "header", "section_text": text}]
    citations = _extract_citations(sections, max_excerpt_len=50)
    assert citations[0]["excerpt"].endswith("...")
    assert len(citations[0]["excerpt"]) == 53


# ---------------------------------------------------------------------------
# _build_context_prompt
# ---------------------------------------------------------------------------

def test_build_context_prompt_with_sections():
    from apps.public_core.services.research_rag import _build_context_prompt
    sections = [
        {"doc_type": "c_101", "section_name": "header", "section_text": "Header content here"},
    ]
    prompt = _build_context_prompt(sections)
    assert "c_101" in prompt
    assert "header" in prompt
    assert "Header content here" in prompt


def test_build_context_prompt_empty_returns_no_relevant_message():
    from apps.public_core.services.research_rag import _build_context_prompt
    prompt = _build_context_prompt([])
    assert "No relevant" in prompt


def test_build_context_prompt_multiple_sections_numbered():
    from apps.public_core.services.research_rag import _build_context_prompt
    sections = [
        {"doc_type": "c_101", "section_name": "header", "section_text": "Header text"},
        {"doc_type": "c_105", "section_name": "completion_data", "section_text": "Completion data"},
    ]
    prompt = _build_context_prompt(sections)
    assert "Section 1" in prompt
    assert "Section 2" in prompt
    assert "c_101" in prompt
    assert "c_105" in prompt


def test_build_context_prompt_includes_section_text():
    from apps.public_core.services.research_rag import _build_context_prompt
    sections = [
        {"doc_type": "sundry", "section_name": "description", "section_text": "Workover proposed"},
    ]
    prompt = _build_context_prompt(sections)
    assert "Workover proposed" in prompt


# ---------------------------------------------------------------------------
# get_chat_history
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_get_chat_history_empty():
    from apps.public_core.services.research_rag import get_chat_history
    from apps.public_core.models import ResearchSession

    session = ResearchSession.objects.create(
        api_number="30-015-28692",
        state="NM",
        status="ready",
    )
    history = get_chat_history(session)
    assert history == []


@pytest.mark.django_db
def test_get_chat_history_returns_ordered_messages():
    from apps.public_core.services.research_rag import get_chat_history
    from apps.public_core.models import ResearchSession, ResearchMessage

    session = ResearchSession.objects.create(
        api_number="30-015-28692",
        state="NM",
        status="ready",
    )
    ResearchMessage.objects.create(session=session, role="user", content="Question 1")
    ResearchMessage.objects.create(session=session, role="assistant", content="Answer 1")

    history = get_chat_history(session)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Question 1"
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == "Answer 1"


@pytest.mark.django_db
def test_get_chat_history_includes_required_fields():
    from apps.public_core.services.research_rag import get_chat_history
    from apps.public_core.models import ResearchSession, ResearchMessage

    session = ResearchSession.objects.create(
        api_number="30-015-28692",
        state="NM",
        status="ready",
    )
    ResearchMessage.objects.create(
        session=session,
        role="assistant",
        content="The surface casing is 500 ft.",
        citations=[{"doc_type": "c_105", "section_name": "casing_record", "excerpt": "500 ft..."}],
    )

    history = get_chat_history(session)
    msg = history[0]
    assert "id" in msg
    assert "role" in msg
    assert "content" in msg
    assert "citations" in msg
    assert "created_at" in msg
    assert msg["citations"][0]["doc_type"] == "c_105"


# ---------------------------------------------------------------------------
# stream_research_answer
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@patch("apps.public_core.services.research_rag.OpenAI")
@patch("apps.public_core.services.research_rag._retrieve_relevant_sections")
def test_stream_research_answer_sse_format(mock_retrieve, mock_openai_cls):
    from apps.public_core.services.research_rag import stream_research_answer
    from apps.public_core.models import ResearchSession

    session = ResearchSession.objects.create(
        api_number="30-015-28692",
        state="NM",
        status="ready",
    )

    mock_retrieve.return_value = [
        {"doc_type": "c_103", "section_name": "header", "section_text": "Test section", "distance": 0.1, "file_name": "c-103.pdf"},
    ]

    # Mock OpenAI streaming
    mock_chunk = MagicMock()
    mock_chunk.choices = [MagicMock()]
    mock_chunk.choices[0].delta.content = "Answer token"
    mock_openai_instance = MagicMock()
    mock_openai_instance.chat.completions.create.return_value = iter([mock_chunk])
    mock_openai_cls.return_value = mock_openai_instance

    events = list(stream_research_answer("What is the casing depth?", session, top_k=5))

    # Should have at least: token event, citations event, done event
    assert len(events) >= 3

    # All events must start with "data: "
    for event in events:
        assert event.startswith("data: ")
        assert event.endswith("\n\n")

    # Verify event types in order
    event_types = []
    for event in events:
        payload = json.loads(event[len("data: "):].strip())
        event_types.append(payload["type"])

    assert "token" in event_types
    assert "citations" in event_types
    assert event_types[-1] == "done"


@pytest.mark.django_db
@patch("apps.public_core.services.research_rag.OpenAI")
@patch("apps.public_core.services.research_rag._retrieve_relevant_sections")
def test_stream_research_answer_persists_messages(mock_retrieve, mock_openai_cls):
    from apps.public_core.services.research_rag import stream_research_answer
    from apps.public_core.models import ResearchSession, ResearchMessage

    session = ResearchSession.objects.create(
        api_number="30-015-28692",
        state="NM",
        status="ready",
    )

    mock_retrieve.return_value = []

    mock_chunk = MagicMock()
    mock_chunk.choices = [MagicMock()]
    mock_chunk.choices[0].delta.content = "Test answer"
    mock_openai_instance = MagicMock()
    mock_openai_instance.chat.completions.create.return_value = iter([mock_chunk])
    mock_openai_cls.return_value = mock_openai_instance

    # Consume the generator
    list(stream_research_answer("Test question?", session))

    messages = ResearchMessage.objects.filter(session=session).order_by("created_at")
    assert messages.count() == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Test question?"
    assert messages[1].role == "assistant"
    assert "Test answer" in messages[1].content


@pytest.mark.django_db
@patch("apps.public_core.services.research_rag._retrieve_relevant_sections")
def test_stream_research_answer_error_yields_error_event(mock_retrieve):
    from apps.public_core.services.research_rag import stream_research_answer
    from apps.public_core.models import ResearchSession

    session = ResearchSession.objects.create(
        api_number="30-015-28692",
        state="NM",
        status="ready",
    )

    mock_retrieve.side_effect = RuntimeError("pgvector connection failed")

    events = list(stream_research_answer("What happened?", session))

    assert len(events) == 1
    payload = json.loads(events[0][len("data: "):].strip())
    assert payload["type"] == "error"
    assert "pgvector connection failed" in payload["message"]


@pytest.mark.django_db
@patch("apps.public_core.services.research_rag.OpenAI")
@patch("apps.public_core.services.research_rag._retrieve_relevant_sections")
def test_stream_research_answer_citations_event_content(mock_retrieve, mock_openai_cls):
    from apps.public_core.services.research_rag import stream_research_answer
    from apps.public_core.models import ResearchSession

    session = ResearchSession.objects.create(
        api_number="30-015-28692",
        state="NM",
        status="ready",
    )

    mock_retrieve.return_value = [
        {"doc_type": "c_101", "section_name": "proposed_work", "section_text": "Drill to 12500 ft", "distance": 0.05, "file_name": "c-101.pdf"},
        {"doc_type": "c_101", "section_name": "proposed_work", "section_text": "Duplicate entry", "distance": 0.06, "file_name": "c-101.pdf"},
    ]

    mock_chunk = MagicMock()
    mock_chunk.choices = [MagicMock()]
    mock_chunk.choices[0].delta.content = "Answer"
    mock_openai_instance = MagicMock()
    mock_openai_instance.chat.completions.create.return_value = iter([mock_chunk])
    mock_openai_cls.return_value = mock_openai_instance

    events = list(stream_research_answer("What is the proposed depth?", session))

    citations_event = next(
        e for e in events if json.loads(e[len("data: "):].strip()).get("type") == "citations"
    )
    payload = json.loads(citations_event[len("data: "):].strip())
    citations = payload["citations"]

    # Deduplication: same (doc_type, section_name) should appear once
    assert len(citations) == 1
    assert citations[0]["doc_type"] == "c_101"
    assert citations[0]["section_name"] == "proposed_work"
