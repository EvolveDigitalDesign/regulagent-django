"""
Tests for RejectionEmbedder: embedding generation, DocumentVector
create/update, and similarity search. OpenAI is always mocked.
"""
import pytest

from apps.intelligence.services.rejection_embedder import RejectionEmbedder


FAKE_VECTOR = [0.1] * 3072


# ---------------------------------------------------------------------------
# embed_pattern — happy path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_embed_pattern_creates_document_vector(rejection_pattern, mocker):
    """embed_pattern should create a DocumentVector and link it to the pattern."""
    mock_client = mocker.MagicMock()
    mock_client.embeddings.create.return_value = mocker.MagicMock(
        data=[mocker.MagicMock(embedding=FAKE_VECTOR)]
    )
    mocker.patch(
        "apps.intelligence.services.rejection_embedder.get_openai_client",
        return_value=mock_client,
    )
    mocker.patch("apps.intelligence.services.rejection_embedder.check_rate_limit")

    # Mock DocumentVector.objects.create to avoid needing pgvector in tests
    fake_dv = mocker.MagicMock()
    fake_dv.id = "fake-dv-id"
    mocker.patch(
        "apps.public_core.models.DocumentVector.objects.create",
        return_value=fake_dv,
    )

    embedder = RejectionEmbedder()
    result = embedder.embed_pattern(rejection_pattern)

    assert result is not None
    mock_client.embeddings.create.assert_called_once()


@pytest.mark.django_db
def test_embed_pattern_updates_existing_vector(rejection_pattern, mocker):
    """When pattern already has an embedding_vector_id, it updates in-place."""
    mock_client = mocker.MagicMock()
    mock_client.embeddings.create.return_value = mocker.MagicMock(
        data=[mocker.MagicMock(embedding=FAKE_VECTOR)]
    )
    mocker.patch(
        "apps.intelligence.services.rejection_embedder.get_openai_client",
        return_value=mock_client,
    )
    mocker.patch("apps.intelligence.services.rejection_embedder.check_rate_limit")

    # Simulate pattern already having an embedding_vector_id
    fake_dv = mocker.MagicMock()
    fake_dv.save = mocker.MagicMock()
    rejection_pattern.embedding_vector_id = "existing-id"
    mocker.patch(
        "apps.public_core.models.DocumentVector.objects.get",
        return_value=fake_dv,
    )
    mocker.patch.object(rejection_pattern, "save")

    embedder = RejectionEmbedder()
    embedder.embed_pattern(rejection_pattern)

    fake_dv.save.assert_called_once()


@pytest.mark.django_db
def test_embed_pattern_calls_check_rate_limit(rejection_pattern, mocker):
    mock_client = mocker.MagicMock()
    mock_client.embeddings.create.return_value = mocker.MagicMock(
        data=[mocker.MagicMock(embedding=FAKE_VECTOR)]
    )
    mocker.patch(
        "apps.intelligence.services.rejection_embedder.get_openai_client",
        return_value=mock_client,
    )
    mock_rate_limit = mocker.patch(
        "apps.intelligence.services.rejection_embedder.check_rate_limit"
    )
    mocker.patch("apps.public_core.models.DocumentVector.objects.create", return_value=mocker.MagicMock())

    RejectionEmbedder().embed_pattern(rejection_pattern)

    mock_rate_limit.assert_called_once()


# ---------------------------------------------------------------------------
# _generate_embedding_text
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_generate_embedding_text_includes_key_fields(rejection_pattern):
    embedder = RejectionEmbedder()
    text = embedder._generate_embedding_text(rejection_pattern)

    assert "w3a" in text
    assert "plug_type" in text
    assert "terminology" in text
    assert "RRC" in text


@pytest.mark.django_db
def test_generate_embedding_text_includes_geo_when_present(rejection_pattern):
    embedder = RejectionEmbedder()
    text = embedder._generate_embedding_text(rejection_pattern)

    # rejection_pattern has state=TX and district=8A
    assert "TX" in text
    assert "8A" in text


@pytest.mark.django_db
def test_generate_embedding_text_includes_stats(rejection_pattern):
    embedder = RejectionEmbedder()
    text = embedder._generate_embedding_text(rejection_pattern)

    assert "Occurrences" in text
    assert "Operators Affected" in text
    assert "Rejection Rate" in text
    assert "Confidence" in text


@pytest.mark.django_db
def test_generate_embedding_text_includes_example_values(rejection_pattern):
    embedder = RejectionEmbedder()
    text = embedder._generate_embedding_text(rejection_pattern)

    assert "CIBP cap" in text
    assert "Cement Plug" in text


@pytest.mark.django_db
def test_generate_embedding_text_no_geo_when_blank():
    from apps.intelligence.models import RejectionPattern

    pattern = RejectionPattern(
        form_type="c103",
        field_name="plug_type",
        issue_category="compliance",
        state="",
        district="",
        agency="NMOCD",
        pattern_description="NM compliance issue",
        occurrence_count=5,
        tenant_count=3,
        confidence=0.7,
    )

    embedder = RejectionEmbedder()
    text = embedder._generate_embedding_text(pattern)

    # No geo section when both state and district are blank
    assert "Geography" not in text


# ---------------------------------------------------------------------------
# find_similar_patterns
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_find_similar_patterns_returns_list(mocker):
    mock_client = mocker.MagicMock()
    mock_client.embeddings.create.return_value = mocker.MagicMock(
        data=[mocker.MagicMock(embedding=FAKE_VECTOR)]
    )
    mocker.patch(
        "apps.intelligence.services.rejection_embedder.get_openai_client",
        return_value=mock_client,
    )
    mocker.patch("apps.intelligence.services.rejection_embedder.check_rate_limit")

    # Mock the queryset chain
    mock_dv = mocker.MagicMock()
    mock_dv.metadata = {
        "pattern_id": "some-id",
        "form_type": "w3a",
        "field_name": "plug_type",
        "issue_category": "terminology",
        "state": "TX",
        "agency": "RRC",
        "occurrence_count": 5,
        "confidence": 0.8,
    }
    mock_dv.distance = 0.1
    mock_dv.section_text = "test"

    mock_qs = mocker.MagicMock()
    mock_qs.__iter__ = mocker.MagicMock(return_value=iter([mock_dv]))
    mock_qs.__getitem__ = mocker.MagicMock(return_value=mock_qs)
    mocker.patch(
        "apps.public_core.models.DocumentVector.objects.filter",
        return_value=mock_qs,
    )
    mock_qs.annotate.return_value = mock_qs
    mock_qs.order_by.return_value = mock_qs

    embedder = RejectionEmbedder()
    results = embedder.find_similar_patterns("plug type issue", limit=5)

    assert isinstance(results, list)


@pytest.mark.django_db
def test_find_similar_patterns_calls_openai(mocker):
    mock_client = mocker.MagicMock()
    mock_client.embeddings.create.return_value = mocker.MagicMock(
        data=[mocker.MagicMock(embedding=FAKE_VECTOR)]
    )
    mocker.patch(
        "apps.intelligence.services.rejection_embedder.get_openai_client",
        return_value=mock_client,
    )
    mocker.patch("apps.intelligence.services.rejection_embedder.check_rate_limit")

    mock_qs = mocker.MagicMock()
    mock_qs.__iter__ = mocker.MagicMock(return_value=iter([]))
    mock_qs.__getitem__ = mocker.MagicMock(return_value=mock_qs)
    mock_qs.annotate.return_value = mock_qs
    mock_qs.order_by.return_value = mock_qs
    mocker.patch(
        "apps.public_core.models.DocumentVector.objects.filter",
        return_value=mock_qs,
    )

    RejectionEmbedder().find_similar_patterns("cement plug issue", limit=3)

    mock_client.embeddings.create.assert_called_once()
