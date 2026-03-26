"""
Integration tests for Research Session REST API endpoints.

Endpoints tested:
    POST   /api/research/sessions/
    GET    /api/research/sessions/{id}/
    GET    /api/research/sessions/{id}/documents/
    POST   /api/research/sessions/{id}/ask/
    GET    /api/research/sessions/{id}/chat/
"""
import uuid
import pytest
from unittest.mock import patch, MagicMock
from rest_framework.test import APIClient

from apps.public_core.models import ResearchSession


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def public_tenant(db):
    """
    Create the public tenant + Domain so TenantMainMiddleware resolves 'testserver'.

    tenant_users.TenantBase.owner is NOT NULL, creating a circular dependency:
    User.create_user() requires a Tenant, but Tenant requires an owner User.
    We break the cycle by inserting via raw SQL (bypassing the constraint temporarily),
    creating a User, then backfilling owner_id.
    """
    from apps.tenants.models import Tenant, Domain
    from django.contrib.auth import get_user_model
    from django.db import connection as db_connection

    User = get_user_model()

    # Break the circular dependency (Tenant needs owner, User's create_user needs Tenant).
    # Use SET CONSTRAINTS DEFERRED to allow inserting Tenant with owner FK within
    # the same transaction as the User row is created.
    from django.db import transaction

    # First create User outside a transaction (User table has no Tenant FK at DB level)
    owner, _ = User.objects.get_or_create(
        email="tenant_owner@test.internal",
        defaults={"is_active": True, "first_name": "", "last_name": ""},
    )

    # Then create Tenant with valid owner_id (deferred constraints not needed now)
    with transaction.atomic():
        with db_connection.cursor() as cur:
            cur.execute("""
                INSERT INTO tenants_tenant (schema_name, name, slug, owner_id, created, modified, created_on)
                VALUES ('public', 'Public', 'public', %s, NOW(), NOW(), NOW())
                ON CONFLICT (schema_name) DO NOTHING
            """, [owner.id])

    tenant = Tenant.objects.get(schema_name="public")

    # Step 4: Register 'testserver' domain so middleware routes to public schema
    Domain.objects.get_or_create(
        domain="testserver",
        defaults={"tenant": tenant, "is_primary": True},
    )
    return tenant


@pytest.fixture
def api_client(db, public_tenant):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        email="testresearch@example.com",
        defaults={"is_active": True},
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def pending_session(db):
    return ResearchSession.objects.create(
        api_number="30-015-28692",
        state="NM",
        status="pending",
    )


@pytest.fixture
def ready_session(db):
    return ResearchSession.objects.create(
        api_number="30-015-28692",
        state="NM",
        status="ready",
        total_documents=3,
        indexed_documents=3,
    )


# ---------------------------------------------------------------------------
# POST /api/research/sessions/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@patch("apps.public_core.views.research.start_research_session_task")
def test_create_session_returns_201(mock_task, api_client):
    mock_task_result = MagicMock()
    mock_task_result.id = "fake-celery-task-id"
    mock_task.delay.return_value = mock_task_result

    resp = api_client.post(
        "/api/research/sessions/",
        {"api_number": "30-015-28692"},
        format="json",
    )

    assert resp.status_code == 201
    assert resp.data["api_number"] == "30-015-28692"
    assert resp.data["state"] == "NM"
    assert resp.data["status"] == "pending"
    mock_task.delay.assert_called_once()


@pytest.mark.django_db
@patch("apps.public_core.views.research.start_research_session_task")
def test_create_session_nm_api_number_detected(mock_task, api_client):
    mock_task_result = MagicMock()
    mock_task_result.id = "task-abc"
    mock_task.delay.return_value = mock_task_result

    resp = api_client.post(
        "/api/research/sessions/",
        {"api_number": "30-015-28692"},
        format="json",
    )

    assert resp.status_code == 201
    assert resp.data["state"] == "NM"


@pytest.mark.django_db
@patch("apps.public_core.views.research.start_research_session_task")
def test_create_session_tx_api_number_detected(mock_task, api_client):
    mock_task_result = MagicMock()
    mock_task_result.id = "task-def"
    mock_task.delay.return_value = mock_task_result

    resp = api_client.post(
        "/api/research/sessions/",
        {"api_number": "42-501-70575"},
        format="json",
    )

    assert resp.status_code == 201
    assert resp.data["state"] == "TX"


@pytest.mark.django_db
@patch("apps.public_core.views.research.start_research_session_task")
def test_create_session_explicit_state_override(mock_task, api_client):
    mock_task_result = MagicMock()
    mock_task_result.id = "task-xyz"
    mock_task.delay.return_value = mock_task_result

    resp = api_client.post(
        "/api/research/sessions/",
        {"api_number": "30-015-28692", "state": "TX"},
        format="json",
    )

    assert resp.status_code == 201
    assert resp.data["state"] == "TX"


@pytest.mark.django_db
def test_create_session_missing_api_number_returns_400(api_client):
    resp = api_client.post("/api/research/sessions/", {}, format="json")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_create_session_unauthenticated_returns_401():
    client = APIClient()
    resp = client.post(
        "/api/research/sessions/",
        {"api_number": "30-015-28692"},
        format="json",
    )
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /api/research/sessions/{id}/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_get_session_returns_session_data(api_client, pending_session):
    resp = api_client.get(f"/api/research/sessions/{pending_session.id}/")
    assert resp.status_code == 200
    assert str(resp.data["id"]) == str(pending_session.id)
    assert resp.data["api_number"] == "30-015-28692"
    assert resp.data["state"] == "NM"
    assert resp.data["status"] == "pending"


@pytest.mark.django_db
def test_get_session_not_found_returns_404(api_client):
    resp = api_client.get(f"/api/research/sessions/{uuid.uuid4()}/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_get_session_ready_status(api_client, ready_session):
    resp = api_client.get(f"/api/research/sessions/{ready_session.id}/")
    assert resp.status_code == 200
    assert resp.data["status"] == "ready"


# ---------------------------------------------------------------------------
# GET /api/research/sessions/{id}/documents/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_get_documents_returns_document_list(api_client, pending_session):
    resp = api_client.get(f"/api/research/sessions/{pending_session.id}/documents/")
    assert resp.status_code == 200
    assert resp.data["session_id"] == str(pending_session.id)
    assert resp.data["api_number"] == "30-015-28692"
    assert resp.data["state"] == "NM"
    assert "document_list" in resp.data
    assert "extracted_documents" in resp.data


@pytest.mark.django_db
def test_get_documents_not_found_returns_404(api_client):
    resp = api_client.get(f"/api/research/sessions/{uuid.uuid4()}/documents/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_get_documents_returns_counts(api_client, ready_session):
    resp = api_client.get(f"/api/research/sessions/{ready_session.id}/documents/")
    assert resp.status_code == 200
    assert resp.data["total_documents"] == 3
    assert resp.data["indexed_documents"] == 3


# ---------------------------------------------------------------------------
# POST /api/research/sessions/{id}/ask/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ask_on_non_ready_session_returns_409(api_client, pending_session):
    resp = api_client.post(
        f"/api/research/sessions/{pending_session.id}/ask/",
        {"question": "What is the casing depth?"},
        format="json",
    )
    assert resp.status_code == 409


@pytest.mark.django_db
def test_ask_missing_question_returns_400(api_client, ready_session):
    resp = api_client.post(
        f"/api/research/sessions/{ready_session.id}/ask/",
        {},
        format="json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_ask_not_found_returns_404(api_client):
    resp = api_client.post(
        f"/api/research/sessions/{uuid.uuid4()}/ask/",
        {"question": "What is the surface casing depth?"},
        format="json",
    )
    assert resp.status_code == 404


@pytest.mark.django_db
@patch("apps.public_core.views.research.stream_research_answer")
def test_ask_on_ready_session_returns_streaming_response(mock_stream, api_client, ready_session):
    def fake_stream(*args, **kwargs):
        yield 'data: {"type": "token", "content": "Hello"}\n\n'
        yield 'data: {"type": "done"}\n\n'

    mock_stream.return_value = fake_stream()

    resp = api_client.post(
        f"/api/research/sessions/{ready_session.id}/ask/",
        {"question": "What is the surface casing depth?"},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.get("Content-Type", "").startswith("text/event-stream")


# ---------------------------------------------------------------------------
# GET /api/research/sessions/{id}/chat/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_get_chat_returns_empty_list_initially(api_client, pending_session):
    resp = api_client.get(f"/api/research/sessions/{pending_session.id}/chat/")
    assert resp.status_code == 200
    assert resp.data["session_id"] == str(pending_session.id)
    assert resp.data["messages"] == []


@pytest.mark.django_db
def test_get_chat_not_found_returns_404(api_client):
    resp = api_client.get(f"/api/research/sessions/{uuid.uuid4()}/chat/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_get_chat_returns_messages_after_interaction(api_client, pending_session):
    from apps.public_core.models import ResearchMessage
    ResearchMessage.objects.create(
        session=pending_session,
        role="user",
        content="What are the casing depths?",
    )
    ResearchMessage.objects.create(
        session=pending_session,
        role="assistant",
        content="The surface casing is set at 500 ft.",
        citations=[{"doc_type": "c_105", "section_name": "casing_record", "excerpt": "..."}],
    )

    resp = api_client.get(f"/api/research/sessions/{pending_session.id}/chat/")
    assert resp.status_code == 200
    assert len(resp.data["messages"]) == 2
    assert resp.data["messages"][0]["role"] == "user"
    assert resp.data["messages"][1]["role"] == "assistant"
