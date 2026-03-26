"""
Tests for C-103 ORM models and REST endpoints.

Covers:
- C103FormORM, C103PlugORM, C103EventORM, DailyWorkRecord CRUD
- C103Form/Plug/Event endpoint auth + basic functionality
- Tenant isolation via workspace_id / tenant_id query params
"""

from __future__ import annotations

import uuid
import pytest
from datetime import date

from rest_framework.test import APIClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    """Unauthenticated DRF APIClient."""
    return APIClient()


@pytest.fixture
def authenticated_client(db, test_user):
    """Authenticated DRF APIClient using JWT."""
    from rest_framework_simplejwt.tokens import RefreshToken

    client = APIClient()
    refresh = RefreshToken.for_user(test_user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


@pytest.fixture
def sample_well(db):
    """WellRegistry with a NM API number."""
    from apps.public_core.models import WellRegistry

    return WellRegistry.objects.create(
        api14="30-025-12345-0000",
        state="NM",
        county="Chaves",
        operator_name="Test Operator NM",
        field_name="Permian Test Field",
    )


@pytest.fixture
def c103_form(db, sample_well):
    """Minimal C103FormORM in draft state."""
    from apps.public_core.models import C103FormORM

    return C103FormORM.objects.create(
        well=sample_well,
        api_number="30-025-12345",
        form_type="noi",
        status="draft",
        region="Southeast",
    )


@pytest.fixture
def c103_form_with_plugs(db, c103_form):
    """C103FormORM with two plugs attached."""
    from apps.public_core.models import C103PlugORM

    C103PlugORM.objects.create(
        c103_form=c103_form,
        plug_number=1,
        step_type="cement_plug",
        operation_type="spot",
        hole_type="cased",
        top_ft=500.0,
        bottom_ft=600.0,
        sacks_required=20.0,
    )
    C103PlugORM.objects.create(
        c103_form=c103_form,
        plug_number=2,
        step_type="surface_plug",
        operation_type="spot",
        hole_type="open",
        top_ft=50.0,
        bottom_ft=100.0,
        sacks_required=10.0,
    )
    return c103_form


@pytest.fixture
def c103_event(db, sample_well, c103_form):
    """C103EventORM linked to well and form."""
    from apps.public_core.models import C103EventORM

    return C103EventORM.objects.create(
        well=sample_well,
        c103_form=c103_form,
        api_number="30-025-12345",
        event_type="set_cement_plug",
        event_date=date(2024, 3, 15),
        depth_top_ft=500.0,
        depth_bottom_ft=600.0,
        sacks=20.0,
    )


# ---------------------------------------------------------------------------
# ORM CRUD Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestC103FormORM:

    def test_create_form(self, sample_well):
        """Create a C103FormORM with basic fields."""
        from apps.public_core.models import C103FormORM

        form = C103FormORM.objects.create(
            well=sample_well,
            api_number="30-025-99999",
            form_type="noi",
            region="Northwest",
        )

        assert form.pk is not None
        assert form.api_number == "30-025-99999"
        assert form.well == sample_well

    def test_form_defaults(self, sample_well):
        """Default status='draft', form_type='noi'."""
        from apps.public_core.models import C103FormORM

        form = C103FormORM.objects.create(
            well=sample_well,
            api_number="30-025-11111",
        )

        assert form.status == "draft"
        assert form.form_type == "noi"
        assert form.plan_data == {}
        assert form.compliance_violations == []

    def test_form_status_transitions(self, c103_form):
        """Status can be set to any valid choice."""
        valid_statuses = [
            "draft",
            "internal_review",
            "engineer_approved",
            "filed",
            "agency_approved",
            "agency_rejected",
            "revision_requested",
        ]
        for status_value in valid_statuses:
            c103_form.status = status_value
            c103_form.save()
            c103_form.refresh_from_db()
            assert c103_form.status == status_value

    def test_plan_data_json_field(self, c103_form):
        """plan_data stores and retrieves JSON correctly."""
        payload = {
            "header": {"well_name": "Test Well"},
            "plugs": [{"plug_number": 1, "top_ft": 500}],
            "compliance": {"violations": []},
        }
        c103_form.plan_data = payload
        c103_form.save()
        c103_form.refresh_from_db()

        assert c103_form.plan_data["header"]["well_name"] == "Test Well"
        assert len(c103_form.plan_data["plugs"]) == 1

    def test_mark_filed_method(self, c103_form):
        """mark_filed() sets status, submitted_at, submitted_by."""
        c103_form.mark_filed(submitted_by="engineer@example.com", nmocd_confirmation_number="NM-2024-001")
        c103_form.refresh_from_db()

        assert c103_form.status == "filed"
        assert c103_form.submitted_by == "engineer@example.com"
        assert c103_form.nmocd_confirmation_number == "NM-2024-001"
        assert c103_form.submitted_at is not None

    def test_str_representation(self, c103_form):
        """__str__ includes API number and status."""
        s = str(c103_form)
        assert "30-025-12345" in s
        assert "Draft" in s

    def test_tenant_isolation_fields(self, sample_well):
        """tenant_id and workspace FK can be set independently."""
        from apps.public_core.models import C103FormORM

        tenant_id = uuid.uuid4()
        form = C103FormORM.objects.create(
            well=sample_well,
            api_number="30-025-22222",
            tenant_id=tenant_id,
        )

        assert form.tenant_id == tenant_id
        assert form.workspace is None


@pytest.mark.django_db
class TestC103PlugORM:

    def test_create_plug(self, c103_form):
        """Create a plug linked to a form."""
        from apps.public_core.models import C103PlugORM

        plug = C103PlugORM.objects.create(
            c103_form=c103_form,
            plug_number=1,
            step_type="cement_plug",
            operation_type="spot",
            hole_type="cased",
            top_ft=500.0,
            bottom_ft=600.0,
            sacks_required=25.0,
        )

        assert plug.pk is not None
        assert plug.c103_form == c103_form
        assert plug.plug_number == 1
        assert plug.step_type == "cement_plug"

    def test_plug_ordering(self, c103_form):
        """Plugs are ordered by plug_number ascending."""
        from apps.public_core.models import C103PlugORM

        C103PlugORM.objects.create(
            c103_form=c103_form,
            plug_number=3,
            step_type="surface_plug",
            operation_type="spot",
            hole_type="open",
            top_ft=0.0,
            bottom_ft=50.0,
        )
        C103PlugORM.objects.create(
            c103_form=c103_form,
            plug_number=1,
            step_type="cement_plug",
            operation_type="spot",
            hole_type="cased",
            top_ft=500.0,
            bottom_ft=600.0,
        )
        C103PlugORM.objects.create(
            c103_form=c103_form,
            plug_number=2,
            step_type="cibp_cap",
            operation_type="spot",
            hole_type="cased",
            top_ft=200.0,
            bottom_ft=250.0,
        )

        plugs = list(C103PlugORM.objects.filter(c103_form=c103_form))
        numbers = [p.plug_number for p in plugs]
        assert numbers == sorted(numbers)

    def test_formation_plug_fields(self, c103_form):
        """Formation plug accepts formation_name and tag_required."""
        from apps.public_core.models import C103PlugORM

        plug = C103PlugORM.objects.create(
            c103_form=c103_form,
            plug_number=1,
            step_type="formation_plug",
            operation_type="squeeze",
            hole_type="open",
            top_ft=1000.0,
            bottom_ft=1100.0,
            formation_name="San Andres",
            tag_required=True,
        )

        assert plug.formation_name == "San Andres"
        assert plug.tag_required is True

    def test_plug_defaults(self, c103_form):
        """Plug has correct defaults for excess_factor, wait_hours, tag_required."""
        from apps.public_core.models import C103PlugORM

        plug = C103PlugORM.objects.create(
            c103_form=c103_form,
            plug_number=1,
            step_type="cement_plug",
            operation_type="spot",
            hole_type="cased",
            top_ft=300.0,
            bottom_ft=400.0,
        )

        assert plug.excess_factor == 0.50
        assert plug.wait_hours == 4
        assert plug.tag_required is True
        assert plug.sacks_required == 0

    def test_plugs_cascade_delete_with_form(self, c103_form):
        """Deleting a form also deletes its plugs."""
        from apps.public_core.models import C103PlugORM

        C103PlugORM.objects.create(
            c103_form=c103_form,
            plug_number=1,
            step_type="cement_plug",
            operation_type="spot",
            hole_type="cased",
            top_ft=500.0,
            bottom_ft=600.0,
        )
        form_id = c103_form.pk
        c103_form.delete()

        assert C103PlugORM.objects.filter(c103_form_id=form_id).count() == 0


@pytest.mark.django_db
class TestC103EventORM:

    def test_create_event(self, sample_well, c103_form):
        """Create an event linked to well and form."""
        from apps.public_core.models import C103EventORM

        event = C103EventORM.objects.create(
            well=sample_well,
            c103_form=c103_form,
            api_number="30-025-12345",
            event_type="set_cement_plug",
            event_date=date(2024, 3, 10),
            depth_top_ft=500.0,
            depth_bottom_ft=600.0,
            sacks=20.0,
        )

        assert event.pk is not None
        assert event.well == sample_well
        assert event.c103_form == c103_form
        assert event.event_type == "set_cement_plug"

    def test_event_ordering(self, sample_well):
        """Events are ordered by event_date then event_start_time."""
        from apps.public_core.models import C103EventORM
        from datetime import time

        C103EventORM.objects.create(
            well=sample_well,
            api_number="30-025-12345",
            event_type="tag_toc",
            event_date=date(2024, 3, 12),
            event_start_time=time(14, 0),
        )
        C103EventORM.objects.create(
            well=sample_well,
            api_number="30-025-12345",
            event_type="set_cement_plug",
            event_date=date(2024, 3, 10),
            event_start_time=time(8, 0),
        )
        C103EventORM.objects.create(
            well=sample_well,
            api_number="30-025-12345",
            event_type="squeeze",
            event_date=date(2024, 3, 12),
            event_start_time=time(9, 0),
        )

        events = list(C103EventORM.objects.filter(api_number="30-025-12345"))
        dates = [(e.event_date, e.event_start_time) for e in events]
        assert dates == sorted(dates)

    def test_event_without_well(self):
        """Event can exist without a well (well is nullable)."""
        from apps.public_core.models import C103EventORM

        event = C103EventORM.objects.create(
            api_number="30-025-99998",
            event_type="circulate",
            event_date=date(2024, 4, 1),
        )

        assert event.well is None
        assert event.pk is not None

    def test_event_str_representation(self, c103_event):
        """__str__ includes event type display and api_number."""
        s = str(c103_event)
        assert "30-025-12345" in s
        assert "2024-03-15" in s


@pytest.mark.django_db
class TestDailyWorkRecord:

    def test_create_dwr(self, c103_form):
        """Create a DWR linked to a form."""
        from apps.public_core.models import DailyWorkRecord

        dwr = DailyWorkRecord.objects.create(
            c103_form=c103_form,
            work_date=date(2024, 3, 10),
            day_number=1,
            daily_narrative="Rigged up and started pumping cement.",
        )

        assert dwr.pk is not None
        assert dwr.c103_form == c103_form
        assert dwr.day_number == 1
        assert "cement" in dwr.daily_narrative

    def test_dwr_m2m_events(self, c103_form, sample_well):
        """DWR can link to multiple events via M2M."""
        from apps.public_core.models import DailyWorkRecord, C103EventORM

        event1 = C103EventORM.objects.create(
            well=sample_well,
            api_number="30-025-12345",
            event_type="set_cement_plug",
            event_date=date(2024, 3, 10),
        )
        event2 = C103EventORM.objects.create(
            well=sample_well,
            api_number="30-025-12345",
            event_type="tag_toc",
            event_date=date(2024, 3, 10),
        )

        dwr = DailyWorkRecord.objects.create(
            c103_form=c103_form,
            work_date=date(2024, 3, 10),
            day_number=1,
        )
        dwr.events.set([event1, event2])

        assert dwr.events.count() == 2
        event_types = set(dwr.events.values_list("event_type", flat=True))
        assert "set_cement_plug" in event_types
        assert "tag_toc" in event_types

    def test_dwr_unique_together(self, c103_form):
        """Cannot create two DWRs for same form + date."""
        from django.db import IntegrityError
        from apps.public_core.models import DailyWorkRecord

        DailyWorkRecord.objects.create(
            c103_form=c103_form,
            work_date=date(2024, 3, 10),
            day_number=1,
        )

        with pytest.raises(IntegrityError):
            DailyWorkRecord.objects.create(
                c103_form=c103_form,
                work_date=date(2024, 3, 10),
                day_number=2,
            )

    def test_dwr_ordering(self, c103_form):
        """DWRs are ordered by work_date ascending."""
        from apps.public_core.models import DailyWorkRecord

        DailyWorkRecord.objects.create(c103_form=c103_form, work_date=date(2024, 3, 12), day_number=3)
        DailyWorkRecord.objects.create(c103_form=c103_form, work_date=date(2024, 3, 10), day_number=1)
        DailyWorkRecord.objects.create(c103_form=c103_form, work_date=date(2024, 3, 11), day_number=2)

        dwrs = list(DailyWorkRecord.objects.filter(c103_form=c103_form))
        work_dates = [d.work_date for d in dwrs]
        assert work_dates == sorted(work_dates)


# ---------------------------------------------------------------------------
# Endpoint Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestC103FormEndpoints:

    def test_list_forms_requires_auth(self, api_client):
        """Unauthenticated GET /api/c103/forms/ returns 401."""
        response = api_client.get("/api/c103/forms/")
        assert response.status_code == 401

    def test_list_forms_authenticated(self, authenticated_client):
        """Authenticated GET /api/c103/forms/ returns 200."""
        response = authenticated_client.get("/api/c103/forms/")
        assert response.status_code == 200

    def test_create_form(self, authenticated_client, sample_well):
        """POST /api/c103/forms/ creates a form and returns 201."""
        payload = {
            "api_number": "30-025-55555",
            "form_type": "noi",
            "region": "Southeast",
        }
        response = authenticated_client.post("/api/c103/forms/", payload, format="json")
        assert response.status_code == 201
        data = response.json()
        assert data["api_number"] == "30-025-55555"

    def test_retrieve_form(self, authenticated_client, c103_form):
        """GET /api/c103/forms/{id}/ returns form with nested plugs and events."""
        response = authenticated_client.get(f"/api/c103/forms/{c103_form.pk}/")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == c103_form.pk
        assert "plugs" in data
        assert "events" in data

    def test_update_form_patch(self, authenticated_client, c103_form):
        """PATCH /api/c103/forms/{id}/ updates the form."""
        response = authenticated_client.patch(
            f"/api/c103/forms/{c103_form.pk}/",
            {"region": "Northwest"},
            format="json",
        )
        assert response.status_code == 200

    def test_delete_form(self, authenticated_client, c103_form):
        """DELETE /api/c103/forms/{id}/ removes the form."""
        response = authenticated_client.delete(f"/api/c103/forms/{c103_form.pk}/")
        assert response.status_code == 204

    def test_submit_action_transitions_to_filed(self, authenticated_client, c103_form):
        """POST /api/c103/forms/{id}/submit/ transitions status to filed."""
        payload = {
            "submitted_by": "engineer@example.com",
            "nmocd_confirmation_number": "NM-2024-TEST-001",
        }
        response = authenticated_client.post(
            f"/api/c103/forms/{c103_form.pk}/submit/",
            payload,
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        c103_form.refresh_from_db()
        assert c103_form.status == "filed"

    def test_submit_action_requires_submitted_by(self, authenticated_client, c103_form):
        """POST /api/c103/forms/{id}/submit/ without submitted_by returns 400."""
        response = authenticated_client.post(
            f"/api/c103/forms/{c103_form.pk}/submit/",
            {},
            format="json",
        )
        assert response.status_code == 400

    def test_submit_already_filed_returns_400(self, authenticated_client, c103_form):
        """POST submit on an already-filed form returns 400."""
        c103_form.status = "filed"
        c103_form.save()

        payload = {"submitted_by": "engineer@example.com"}
        response = authenticated_client.post(
            f"/api/c103/forms/{c103_form.pk}/submit/",
            payload,
            format="json",
        )
        assert response.status_code == 400
        assert "error" in response.json()

    def test_by_api_action(self, authenticated_client, c103_form):
        """GET /api/c103/forms/by_api/?api_number=... returns matching forms."""
        response = authenticated_client.get(
            "/api/c103/forms/by_api/",
            {"api_number": c103_form.api_number},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["api_number"] == c103_form.api_number
        assert data["count"] >= 1
        assert len(data["forms"]) >= 1

    def test_by_api_missing_param_returns_400(self, authenticated_client):
        """GET /api/c103/forms/by_api/ without api_number returns 400."""
        response = authenticated_client.get("/api/c103/forms/by_api/")
        assert response.status_code == 400

    def test_pending_submission_action(self, authenticated_client, c103_form):
        """GET /api/c103/forms/pending_submission/ returns draft forms."""
        response = authenticated_client.get("/api/c103/forms/pending_submission/")
        assert response.status_code == 200
        data = response.json()
        assert "count" in data
        assert "forms" in data
        # c103_form is draft — should appear
        ids = [f["id"] for f in data["forms"]]
        assert c103_form.pk in ids

    def test_pending_submission_excludes_filed(self, authenticated_client, c103_form):
        """Filed forms do not appear in pending_submission."""
        c103_form.mark_filed(submitted_by="engineer@example.com")

        response = authenticated_client.get("/api/c103/forms/pending_submission/")
        assert response.status_code == 200
        data = response.json()
        ids = [f["id"] for f in data["forms"]]
        assert c103_form.pk not in ids

    def test_filed_action(self, authenticated_client, c103_form):
        """GET /api/c103/forms/filed/ returns filed/approved forms."""
        c103_form.mark_filed(submitted_by="engineer@example.com")

        response = authenticated_client.get("/api/c103/forms/filed/")
        assert response.status_code == 200
        data = response.json()
        ids = [f["id"] for f in data["forms"]]
        assert c103_form.pk in ids

    def test_tenant_isolation_filter_by_tenant_id(self, authenticated_client, sample_well):
        """Only forms matching tenant_id are returned when filtering."""
        from apps.public_core.models import C103FormORM

        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()

        form_a = C103FormORM.objects.create(
            well=sample_well,
            api_number="30-025-66661",
            tenant_id=tenant_a,
        )
        C103FormORM.objects.create(
            well=sample_well,
            api_number="30-025-66662",
            tenant_id=tenant_b,
        )

        response = authenticated_client.get("/api/c103/forms/", {"tenant_id": str(tenant_a)})
        assert response.status_code == 200
        data = response.json()
        returned_ids = [f["id"] for f in data["results"]]
        assert form_a.pk in returned_ids
        # form_b should NOT appear since it belongs to tenant_b
        for f in data["results"]:
            assert str(f.get("tenant_id", "")) != str(tenant_b)

    def test_export_pdf_returns_501(self, authenticated_client, c103_form):
        """GET /api/c103/forms/{id}/export-pdf/ returns 501 (not implemented)."""
        response = authenticated_client.get(f"/api/c103/forms/{c103_form.pk}/export-pdf/")
        assert response.status_code == 501

    def test_list_forms_response_structure(self, authenticated_client, c103_form):
        """List response includes expected fields for each form."""
        response = authenticated_client.get("/api/c103/forms/")
        assert response.status_code == 200
        data = response.json()
        results = data.get("results", data)
        if results:
            first = results[0]
            assert "id" in first
            assert "api_number" in first
            assert "form_type" in first
            assert "status" in first
            assert "plug_count" in first


@pytest.mark.django_db
class TestC103PlugEndpoints:

    def test_list_plugs_requires_auth(self, api_client, c103_form):
        """Unauthenticated GET .../plugs/ returns 401."""
        response = api_client.get(f"/api/c103/forms/{c103_form.pk}/plugs/")
        assert response.status_code == 401

    def test_list_plugs_for_form(self, authenticated_client, c103_form_with_plugs):
        """GET /api/c103/forms/{form_pk}/plugs/ returns plugs for that form."""
        response = authenticated_client.get(
            f"/api/c103/forms/{c103_form_with_plugs.pk}/plugs/"
        )
        assert response.status_code == 200
        data = response.json()
        # List endpoint may return a list or paginated dict
        results = data if isinstance(data, list) else data.get("results", data)
        assert len(results) == 2
        plug_numbers = [p["plug_number"] for p in results]
        assert 1 in plug_numbers
        assert 2 in plug_numbers

    def test_create_plug_for_form(self, authenticated_client, c103_form):
        """POST /api/c103/forms/{form_pk}/plugs/ creates a plug."""
        payload = {
            "plug_number": 1,
            "step_type": "cement_plug",
            "operation_type": "spot",
            "hole_type": "cased",
            "top_ft": 400.0,
            "bottom_ft": 500.0,
            "sacks_required": 15.0,
        }
        response = authenticated_client.post(
            f"/api/c103/forms/{c103_form.pk}/plugs/",
            payload,
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["plug_number"] == 1
        assert data["step_type"] == "cement_plug"

    def test_retrieve_plug(self, authenticated_client, c103_form_with_plugs):
        """GET /api/c103/forms/{form_pk}/plugs/{id}/ returns plug detail."""
        from apps.public_core.models import C103PlugORM

        plug = C103PlugORM.objects.filter(c103_form=c103_form_with_plugs).first()
        response = authenticated_client.get(
            f"/api/c103/forms/{c103_form_with_plugs.pk}/plugs/{plug.pk}/"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == plug.pk

    def test_plugs_scoped_to_form(self, authenticated_client, sample_well):
        """Plugs from one form are not visible under another form's URL."""
        from apps.public_core.models import C103FormORM, C103PlugORM

        form_a = C103FormORM.objects.create(well=sample_well, api_number="30-025-AA001")
        form_b = C103FormORM.objects.create(well=sample_well, api_number="30-025-BB001")

        C103PlugORM.objects.create(
            c103_form=form_a,
            plug_number=1,
            step_type="cement_plug",
            operation_type="spot",
            hole_type="cased",
            top_ft=100.0,
            bottom_ft=200.0,
        )

        response = authenticated_client.get(f"/api/c103/forms/{form_b.pk}/plugs/")
        assert response.status_code == 200
        data = response.json()
        results = data if isinstance(data, list) else data.get("results", data)
        assert len(results) == 0


@pytest.mark.django_db
class TestC103EventEndpoints:

    def test_list_events_requires_auth(self, api_client):
        """Unauthenticated GET /api/c103/events/ returns 401."""
        response = api_client.get("/api/c103/events/")
        assert response.status_code == 401

    def test_list_events_authenticated(self, authenticated_client):
        """Authenticated GET /api/c103/events/ returns 200."""
        response = authenticated_client.get("/api/c103/events/")
        assert response.status_code == 200

    def test_create_event(self, authenticated_client, sample_well, c103_form):
        """POST /api/c103/events/ creates an event."""
        payload = {
            "well": sample_well.pk,
            "c103_form": c103_form.pk,
            "api_number": "30-025-12345",
            "event_type": "squeeze",
            "event_date": "2024-04-01",
            "depth_top_ft": 800.0,
            "depth_bottom_ft": 900.0,
        }
        response = authenticated_client.post("/api/c103/events/", payload, format="json")
        assert response.status_code == 201
        data = response.json()
        assert data["event_type"] == "squeeze"

    def test_filter_events_by_api(self, authenticated_client, c103_event):
        """GET /api/c103/events/?api_number=... filters correctly."""
        response = authenticated_client.get(
            "/api/c103/events/",
            {"api_number": c103_event.api_number},
        )
        assert response.status_code == 200
        data = response.json()
        results = data if isinstance(data, list) else data.get("results", data)
        assert len(results) >= 1
        for event in results:
            assert event["api_number"] == c103_event.api_number

    def test_filter_events_by_event_type(self, authenticated_client, c103_event):
        """GET /api/c103/events/?event_type=... filters by type."""
        response = authenticated_client.get(
            "/api/c103/events/",
            {"event_type": "set_cement_plug"},
        )
        assert response.status_code == 200
        data = response.json()
        results = data if isinstance(data, list) else data.get("results", data)
        for event in results:
            assert event["event_type"] == "set_cement_plug"

    def test_filter_events_by_form(self, authenticated_client, c103_event, c103_form):
        """GET /api/c103/events/?c103_form=... filters by parent form."""
        response = authenticated_client.get(
            "/api/c103/events/",
            {"c103_form": c103_form.pk},
        )
        assert response.status_code == 200
        data = response.json()
        results = data if isinstance(data, list) else data.get("results", data)
        assert len(results) >= 1

    def test_by_api_action(self, authenticated_client, c103_event):
        """GET /api/c103/events/by_api/?api_number=... returns matching events."""
        response = authenticated_client.get(
            "/api/c103/events/by_api/",
            {"api_number": c103_event.api_number},
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_by_api_missing_param_returns_400(self, authenticated_client):
        """GET /api/c103/events/by_api/ without api_number returns 400."""
        response = authenticated_client.get("/api/c103/events/by_api/")
        assert response.status_code == 400

    def test_retrieve_event(self, authenticated_client, c103_event):
        """GET /api/c103/events/{id}/ returns event detail."""
        response = authenticated_client.get(f"/api/c103/events/{c103_event.pk}/")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == c103_event.pk
        assert "event_type_display" in data

    def test_delete_event(self, authenticated_client, c103_event):
        """DELETE /api/c103/events/{id}/ removes the event."""
        response = authenticated_client.delete(f"/api/c103/events/{c103_event.pk}/")
        assert response.status_code == 204
