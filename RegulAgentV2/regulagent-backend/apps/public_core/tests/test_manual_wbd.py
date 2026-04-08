"""
Failing tests for ManualWBD model and CRUD API (TDD — implementation pending).

Endpoints tested:
    GET    /api/tenant/manual-wbd/          List (filter by api14, diagram_type)
    POST   /api/tenant/manual-wbd/          Create
    GET    /api/tenant/manual-wbd/{id}/     Retrieve
    PATCH  /api/tenant/manual-wbd/{id}/     Update
    DELETE /api/tenant/manual-wbd/{id}/     Soft-delete (is_archived=True)

NOTE: All tests in this file are expected to FAIL until the ManualWBD model,
serializer, and views are implemented.
"""
from __future__ import annotations

import uuid
import pytest
from rest_framework.test import APIClient

# This import will fail until the model is created — that is the first "red" step.
from apps.public_core.models.manual_wbd import ManualWBD


# ---------------------------------------------------------------------------
# Sample diagram_data fixtures
# ---------------------------------------------------------------------------

CURRENT_DIAGRAM_DATA = {
    "well": {
        "api14": "42383396820000",
        "operator_name": "Test Op",
        "lease_name": "Test Lease",
        "field_name": "Test Field",
        "well_number": "1",
    },
    "well_geometry": {
        "casing_strings": [
            {
                "string": "Surface",
                "top_ft": 0,
                "size_in": 9.625,
                "bottom_ft": 500,
                "hole_size_in": 12.25,
            }
        ],
        "formation_tops": [{"formation": "Spraberry", "top_ft": 6750}],
        "production_perforations": [],
        "tubing": [],
        "existing_tools": [],
    },
}

PLANNED_DIAGRAM_DATA = {
    **CURRENT_DIAGRAM_DATA,
    "payload": {
        "steps": [
            {
                "type": "cement_plug",
                "formation": "Spraberry",
                "top_ft": 6500,
                "bottom_ft": 6800,
                "sacks": 50,
                "display_name": "Plug #1",
            }
        ]
    },
}

AS_PLUGGED_DIAGRAM_DATA = {
    "well": CURRENT_DIAGRAM_DATA["well"],
    "well_geometry": CURRENT_DIAGRAM_DATA["well_geometry"],
    "plugs": [
        {
            "plug_number": 1,
            "type": "cement_plug",
            "top_ft": 6500,
            "bottom_ft": 6800,
            "sacks": 50,
        }
    ],
    "jurisdiction": "TX",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def well(db):
    """Create a test WellRegistry."""
    from apps.public_core.models import WellRegistry

    return WellRegistry.objects.create(
        api14="42383396820000",
        state="TX",
        county="Howard",
        district="8A",
        operator_name="Test Operator",
        field_name="Test Field",
        lease_name="Test Lease",
        well_number="1",
    )


@pytest.fixture
def tenant(db):
    """Create a test tenant using the raw-SQL pattern to avoid circular FK."""
    from apps.tenants.models import Tenant, Domain
    from django.contrib.auth import get_user_model
    from django.db import connection as db_connection, transaction

    User = get_user_model()

    owner, _ = User.objects.get_or_create(
        email="manual_wbd_tenant_owner@test.internal",
        defaults={"is_active": True, "first_name": "", "last_name": ""},
    )

    with transaction.atomic():
        with db_connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tenants_tenant (schema_name, name, slug, owner_id, created, modified, created_on)
                VALUES ('public', 'Public', 'public', %s, NOW(), NOW(), NOW())
                ON CONFLICT (schema_name) DO NOTHING
                """,
                [owner.id],
            )

    tenant_obj = Tenant.objects.get(schema_name="public")

    Domain.objects.get_or_create(
        domain="testserver",
        defaults={"tenant": tenant_obj, "is_primary": True},
    )
    return tenant_obj


@pytest.fixture
def user(db, tenant):
    """Create an authenticated user belonging to the test tenant."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user_obj, _ = User.objects.get_or_create(
        email="manualwbd_user@example.com",
        defaults={"is_active": True},
    )
    user_obj.tenants.add(tenant)
    return user_obj


@pytest.fixture
def user_b(db, tenant):
    """A second user for tenant-isolation tests."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user_obj, _ = User.objects.get_or_create(
        email="manualwbd_user_b@example.com",
        defaults={"is_active": True},
    )
    # user_b belongs to a *different* tenant
    from apps.tenants.models import Tenant
    other_tenant = Tenant.objects.create(
        schema_name="tenant_b",
        name="Tenant B",
        slug="tenant-b",
        owner=user_obj,
    )
    user_obj.tenants.add(other_tenant)
    return user_obj


@pytest.fixture
def api_client(user):
    """Authenticated APIClient for primary user."""
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def api_client_b(user_b):
    """Authenticated APIClient for secondary (isolated) user."""
    client = APIClient()
    client.force_authenticate(user=user_b)
    return client


@pytest.fixture
def current_wbd(db, user, tenant):
    """A saved ManualWBD with diagram_type=current."""
    return ManualWBD.objects.create(
        api14="42383396820000",
        diagram_type=ManualWBD.DiagramType.CURRENT,
        title="Current WBD",
        diagram_data=CURRENT_DIAGRAM_DATA,
        tenant_id=tenant.id,
        created_by=user,
    )


@pytest.fixture
def planned_wbd(db, user, tenant):
    """A saved ManualWBD with diagram_type=planned."""
    return ManualWBD.objects.create(
        api14="42383396820000",
        diagram_type=ManualWBD.DiagramType.PLANNED,
        title="Planned WBD",
        diagram_data=PLANNED_DIAGRAM_DATA,
        tenant_id=tenant.id,
        created_by=user,
    )


@pytest.fixture
def as_plugged_wbd(db, user, tenant):
    """A saved ManualWBD with diagram_type=as_plugged."""
    return ManualWBD.objects.create(
        api14="42383396820000",
        diagram_type=ManualWBD.DiagramType.AS_PLUGGED,
        title="As-Plugged WBD",
        diagram_data=AS_PLUGGED_DIAGRAM_DATA,
        tenant_id=tenant.id,
        created_by=user,
    )


# ---------------------------------------------------------------------------
# 1. Model tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestManualWBDModel:
    def test_create_current_diagram(self, user, tenant):
        """Can create a ManualWBD with diagram_type=current and verify all fields."""
        wbd = ManualWBD.objects.create(
            api14="42383396820000",
            diagram_type=ManualWBD.DiagramType.CURRENT,
            title="My Current WBD",
            diagram_data=CURRENT_DIAGRAM_DATA,
            tenant_id=tenant.id,
            created_by=user,
        )

        assert wbd.pk is not None
        assert wbd.api14 == "42383396820000"
        assert wbd.diagram_type == "current"
        assert wbd.title == "My Current WBD"
        assert wbd.diagram_data == CURRENT_DIAGRAM_DATA
        assert wbd.tenant_id == tenant.id
        assert wbd.is_archived is False
        assert wbd.created_at is not None
        assert wbd.updated_at is not None

    def test_create_planned_diagram(self, user, tenant):
        """Can create a ManualWBD with diagram_type=planned."""
        wbd = ManualWBD.objects.create(
            api14="42383396820000",
            diagram_type=ManualWBD.DiagramType.PLANNED,
            diagram_data=PLANNED_DIAGRAM_DATA,
            tenant_id=tenant.id,
            created_by=user,
        )

        assert wbd.diagram_type == "planned"
        assert wbd.diagram_data["payload"]["steps"][0]["type"] == "cement_plug"

    def test_create_as_plugged_diagram(self, user, tenant):
        """Can create a ManualWBD with diagram_type=as_plugged."""
        wbd = ManualWBD.objects.create(
            api14="42383396820000",
            diagram_type=ManualWBD.DiagramType.AS_PLUGGED,
            diagram_data=AS_PLUGGED_DIAGRAM_DATA,
            tenant_id=tenant.id,
            created_by=user,
        )

        assert wbd.diagram_type == "as_plugged"
        assert len(wbd.diagram_data["plugs"]) == 1

    def test_auto_link_to_well_registry(self, well, user, tenant):
        """When api14 matches a WellRegistry, 'well' FK should be auto-linked by view logic."""
        # The model allows well=None; the view (or pre_save signal) wires it up.
        # We assert the FK field exists and can be assigned.
        wbd = ManualWBD.objects.create(
            api14=well.api14,
            well=well,
            diagram_type=ManualWBD.DiagramType.CURRENT,
            diagram_data=CURRENT_DIAGRAM_DATA,
            tenant_id=tenant.id,
            created_by=user,
        )

        assert wbd.well_id == well.pk
        assert wbd.well.api14 == well.api14

    def test_soft_delete_sets_is_archived(self, current_wbd):
        """Soft-delete sets is_archived=True without removing the DB row."""
        current_wbd.is_archived = True
        current_wbd.save(update_fields=["is_archived"])

        refreshed = ManualWBD.objects.get(pk=current_wbd.pk)
        assert refreshed.is_archived is True

    def test_diagram_type_choices(self):
        """DiagramType choices expose CURRENT, PLANNED, AS_PLUGGED values."""
        assert ManualWBD.DiagramType.CURRENT == "current"
        assert ManualWBD.DiagramType.PLANNED == "planned"
        assert ManualWBD.DiagramType.AS_PLUGGED == "as_plugged"

    def test_title_defaults_to_empty_string(self, user, tenant):
        """title field defaults to '' when not provided."""
        wbd = ManualWBD.objects.create(
            api14="42383396820000",
            diagram_type=ManualWBD.DiagramType.CURRENT,
            diagram_data=CURRENT_DIAGRAM_DATA,
            tenant_id=tenant.id,
            created_by=user,
        )
        assert wbd.title == ""

    def test_uuid_primary_key(self, current_wbd):
        """Primary key is a UUID."""
        assert isinstance(current_wbd.pk, uuid.UUID)


# ---------------------------------------------------------------------------
# 2. API tests
# ---------------------------------------------------------------------------

BASE_URL = "/api/tenant/manual-wbd/"


@pytest.mark.django_db
class TestManualWBDCreate:
    def test_create_current_diagram_returns_201(self, api_client):
        """POST with valid current diagram_data → 201."""
        resp = api_client.post(
            BASE_URL,
            {
                "api14": "42383396820000",
                "diagram_type": "current",
                "title": "New Current",
                "diagram_data": CURRENT_DIAGRAM_DATA,
            },
            format="json",
        )

        assert resp.status_code == 201
        assert resp.data["diagram_type"] == "current"
        assert resp.data["api14"] == "42383396820000"
        assert "id" in resp.data

    def test_create_planned_diagram_returns_201(self, api_client):
        """POST with valid planned diagram_data → 201."""
        resp = api_client.post(
            BASE_URL,
            {
                "api14": "42383396820000",
                "diagram_type": "planned",
                "title": "New Planned",
                "diagram_data": PLANNED_DIAGRAM_DATA,
            },
            format="json",
        )

        assert resp.status_code == 201
        assert resp.data["diagram_type"] == "planned"

    def test_create_as_plugged_diagram_returns_201(self, api_client):
        """POST with valid as_plugged diagram_data → 201."""
        resp = api_client.post(
            BASE_URL,
            {
                "api14": "42383396820000",
                "diagram_type": "as_plugged",
                "diagram_data": AS_PLUGGED_DIAGRAM_DATA,
            },
            format="json",
        )

        assert resp.status_code == 201
        assert resp.data["diagram_type"] == "as_plugged"

    def test_create_returns_id_and_timestamps(self, api_client):
        """Newly created record includes id, created_at, updated_at."""
        resp = api_client.post(
            BASE_URL,
            {
                "api14": "42383396820000",
                "diagram_type": "current",
                "diagram_data": CURRENT_DIAGRAM_DATA,
            },
            format="json",
        )

        assert resp.status_code == 201
        assert "id" in resp.data
        assert "created_at" in resp.data
        assert "updated_at" in resp.data

    def test_unauthenticated_create_returns_401(self):
        """Unauthenticated POST → 401."""
        client = APIClient()
        resp = client.post(
            BASE_URL,
            {
                "api14": "42383396820000",
                "diagram_type": "current",
                "diagram_data": CURRENT_DIAGRAM_DATA,
            },
            format="json",
        )

        assert resp.status_code in (401, 403)


@pytest.mark.django_db
class TestManualWBDList:
    def test_list_returns_created_diagrams(self, api_client, current_wbd, planned_wbd):
        """GET list returns diagrams belonging to the authenticated user's tenant."""
        resp = api_client.get(BASE_URL)

        assert resp.status_code == 200
        ids = [str(item["id"]) for item in resp.data["results"] if "results" in resp.data] or \
              [str(item["id"]) for item in resp.data]
        assert str(current_wbd.pk) in ids
        assert str(planned_wbd.pk) in ids

    def test_list_excludes_archived(self, api_client, current_wbd, tenant):
        """GET list does not include archived diagrams."""
        from apps.public_core.models.manual_wbd import ManualWBD as _M
        archived = _M.objects.create(
            api14="42383396820000",
            diagram_type=_M.DiagramType.CURRENT,
            diagram_data=CURRENT_DIAGRAM_DATA,
            tenant_id=tenant.id,
            is_archived=True,
        )

        resp = api_client.get(BASE_URL)

        assert resp.status_code == 200
        data = resp.data.get("results", resp.data)
        ids = [str(item["id"]) for item in data]
        assert str(archived.pk) not in ids

    def test_list_filter_by_api14(self, api_client, current_wbd, tenant, user):
        """GET ?api14=... returns only matching diagrams."""
        from apps.public_core.models.manual_wbd import ManualWBD as _M
        other = _M.objects.create(
            api14="42999999990000",
            diagram_type=_M.DiagramType.CURRENT,
            diagram_data=CURRENT_DIAGRAM_DATA,
            tenant_id=tenant.id,
            created_by=user,
        )

        resp = api_client.get(BASE_URL, {"api14": "42383396820000"})

        assert resp.status_code == 200
        data = resp.data.get("results", resp.data)
        ids = [str(item["id"]) for item in data]
        assert str(current_wbd.pk) in ids
        assert str(other.pk) not in ids

    def test_list_filter_by_diagram_type(
        self, api_client, current_wbd, planned_wbd, as_plugged_wbd
    ):
        """GET ?diagram_type=planned returns only planned diagrams."""
        resp = api_client.get(BASE_URL, {"diagram_type": "planned"})

        assert resp.status_code == 200
        data = resp.data.get("results", resp.data)
        ids = [str(item["id"]) for item in data]
        assert str(planned_wbd.pk) in ids
        assert str(current_wbd.pk) not in ids
        assert str(as_plugged_wbd.pk) not in ids

    def test_unauthenticated_list_returns_401(self):
        """Unauthenticated GET list → 401."""
        client = APIClient()
        resp = client.get(BASE_URL)

        assert resp.status_code in (401, 403)


@pytest.mark.django_db
class TestManualWBDRetrieve:
    def test_retrieve_by_uuid_returns_200(self, api_client, current_wbd):
        """GET /api/tenant/manual-wbd/{id}/ → 200 with full record."""
        resp = api_client.get(f"{BASE_URL}{current_wbd.pk}/")

        assert resp.status_code == 200
        assert str(resp.data["id"]) == str(current_wbd.pk)
        assert resp.data["diagram_type"] == "current"
        assert resp.data["api14"] == "42383396820000"

    def test_retrieve_nonexistent_returns_404(self, api_client):
        """GET with random UUID → 404."""
        resp = api_client.get(f"{BASE_URL}{uuid.uuid4()}/")

        assert resp.status_code == 404

    def test_retrieve_archived_returns_404(self, api_client, current_wbd):
        """Archived diagrams are not accessible via retrieve endpoint."""
        current_wbd.is_archived = True
        current_wbd.save(update_fields=["is_archived"])

        resp = api_client.get(f"{BASE_URL}{current_wbd.pk}/")

        assert resp.status_code == 404

    def test_unauthenticated_retrieve_returns_401(self, current_wbd):
        """Unauthenticated GET detail → 401."""
        client = APIClient()
        resp = client.get(f"{BASE_URL}{current_wbd.pk}/")

        assert resp.status_code in (401, 403)


@pytest.mark.django_db
class TestManualWBDUpdate:
    def test_patch_title_and_diagram_data_returns_200(self, api_client, current_wbd):
        """PATCH with updated title and diagram_data → 200."""
        updated_data = dict(CURRENT_DIAGRAM_DATA)
        updated_data["well"]["operator_name"] = "Updated Operator"

        resp = api_client.patch(
            f"{BASE_URL}{current_wbd.pk}/",
            {"title": "Updated Title", "diagram_data": updated_data},
            format="json",
        )

        assert resp.status_code == 200
        assert resp.data["title"] == "Updated Title"
        assert resp.data["diagram_data"]["well"]["operator_name"] == "Updated Operator"

    def test_patch_updates_updated_at(self, api_client, current_wbd):
        """PATCH causes updated_at to advance."""
        original_updated_at = current_wbd.updated_at

        resp = api_client.patch(
            f"{BASE_URL}{current_wbd.pk}/",
            {"title": "Changed"},
            format="json",
        )

        assert resp.status_code == 200
        current_wbd.refresh_from_db()
        assert current_wbd.updated_at >= original_updated_at

    def test_patch_nonexistent_returns_404(self, api_client):
        """PATCH on random UUID → 404."""
        resp = api_client.patch(
            f"{BASE_URL}{uuid.uuid4()}/",
            {"title": "Ghost"},
            format="json",
        )

        assert resp.status_code == 404

    def test_unauthenticated_patch_returns_401(self, current_wbd):
        """Unauthenticated PATCH → 401."""
        client = APIClient()
        resp = client.patch(
            f"{BASE_URL}{current_wbd.pk}/",
            {"title": "No Auth"},
            format="json",
        )

        assert resp.status_code in (401, 403)


@pytest.mark.django_db
class TestManualWBDDelete:
    def test_delete_returns_200_and_soft_deletes(self, api_client, current_wbd):
        """DELETE → 200 (or 204) and sets is_archived=True."""
        resp = api_client.delete(f"{BASE_URL}{current_wbd.pk}/")

        assert resp.status_code in (200, 204)

        current_wbd.refresh_from_db()
        assert current_wbd.is_archived is True

    def test_deleted_item_not_returned_in_retrieve(self, api_client, current_wbd):
        """After DELETE, GET detail returns 404."""
        api_client.delete(f"{BASE_URL}{current_wbd.pk}/")
        resp = api_client.get(f"{BASE_URL}{current_wbd.pk}/")

        assert resp.status_code == 404

    def test_deleted_item_excluded_from_list(self, api_client, current_wbd):
        """After DELETE, item does not appear in list."""
        api_client.delete(f"{BASE_URL}{current_wbd.pk}/")
        resp = api_client.get(BASE_URL)

        assert resp.status_code == 200
        data = resp.data.get("results", resp.data)
        ids = [str(item["id"]) for item in data]
        assert str(current_wbd.pk) not in ids

    def test_delete_nonexistent_returns_404(self, api_client):
        """DELETE on random UUID → 404."""
        resp = api_client.delete(f"{BASE_URL}{uuid.uuid4()}/")

        assert resp.status_code == 404

    def test_unauthenticated_delete_returns_401(self, current_wbd):
        """Unauthenticated DELETE → 401."""
        client = APIClient()
        resp = client.delete(f"{BASE_URL}{current_wbd.pk}/")

        assert resp.status_code in (401, 403)


@pytest.mark.django_db
class TestManualWBDTenantIsolation:
    def test_user_cannot_see_other_tenants_diagrams(
        self, api_client, api_client_b, current_wbd
    ):
        """User B (different tenant) cannot list User A's diagrams."""
        resp = api_client_b.get(BASE_URL)

        assert resp.status_code == 200
        data = resp.data.get("results", resp.data)
        ids = [str(item["id"]) for item in data]
        assert str(current_wbd.pk) not in ids

    def test_user_cannot_retrieve_other_tenants_diagram(
        self, api_client_b, current_wbd
    ):
        """User B cannot retrieve User A's diagram by UUID."""
        resp = api_client_b.get(f"{BASE_URL}{current_wbd.pk}/")

        assert resp.status_code == 404

    def test_user_cannot_patch_other_tenants_diagram(
        self, api_client_b, current_wbd
    ):
        """User B cannot PATCH User A's diagram."""
        resp = api_client_b.patch(
            f"{BASE_URL}{current_wbd.pk}/",
            {"title": "Hijacked"},
            format="json",
        )

        assert resp.status_code == 404

    def test_user_cannot_delete_other_tenants_diagram(
        self, api_client_b, current_wbd
    ):
        """User B cannot DELETE User A's diagram."""
        resp = api_client_b.delete(f"{BASE_URL}{current_wbd.pk}/")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. Validation tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestManualWBDValidation:
    def test_missing_diagram_data_returns_400(self, api_client):
        """POST without diagram_data → 400."""
        resp = api_client.post(
            BASE_URL,
            {"api14": "42383396820000", "diagram_type": "current"},
            format="json",
        )

        assert resp.status_code == 400
        assert "diagram_data" in resp.data

    def test_invalid_diagram_type_returns_400(self, api_client):
        """POST with unknown diagram_type → 400."""
        resp = api_client.post(
            BASE_URL,
            {
                "api14": "42383396820000",
                "diagram_type": "nonexistent_type",
                "diagram_data": CURRENT_DIAGRAM_DATA,
            },
            format="json",
        )

        assert resp.status_code == 400
        assert "diagram_type" in resp.data

    def test_planned_type_missing_steps_returns_400(self, api_client):
        """POST with diagram_type=planned but diagram_data missing payload.steps → 400."""
        bad_data = {
            "well": CURRENT_DIAGRAM_DATA["well"],
            "well_geometry": CURRENT_DIAGRAM_DATA["well_geometry"],
            # payload key is missing entirely
        }

        resp = api_client.post(
            BASE_URL,
            {
                "api14": "42383396820000",
                "diagram_type": "planned",
                "diagram_data": bad_data,
            },
            format="json",
        )

        assert resp.status_code == 400

    def test_planned_type_empty_steps_returns_400(self, api_client):
        """POST with diagram_type=planned and empty steps list → 400."""
        bad_data = {
            **CURRENT_DIAGRAM_DATA,
            "payload": {"steps": []},
        }

        resp = api_client.post(
            BASE_URL,
            {
                "api14": "42383396820000",
                "diagram_type": "planned",
                "diagram_data": bad_data,
            },
            format="json",
        )

        assert resp.status_code == 400

    def test_as_plugged_type_missing_plugs_returns_400(self, api_client):
        """POST with diagram_type=as_plugged but diagram_data missing plugs → 400."""
        bad_data = {
            "well": CURRENT_DIAGRAM_DATA["well"],
            "well_geometry": CURRENT_DIAGRAM_DATA["well_geometry"],
            # plugs key is missing
            "jurisdiction": "TX",
        }

        resp = api_client.post(
            BASE_URL,
            {
                "api14": "42383396820000",
                "diagram_type": "as_plugged",
                "diagram_data": bad_data,
            },
            format="json",
        )

        assert resp.status_code == 400

    def test_missing_api14_returns_400(self, api_client):
        """POST without api14 → 400."""
        resp = api_client.post(
            BASE_URL,
            {
                "diagram_type": "current",
                "diagram_data": CURRENT_DIAGRAM_DATA,
            },
            format="json",
        )

        assert resp.status_code == 400
        assert "api14" in resp.data

    def test_missing_diagram_type_returns_400(self, api_client):
        """POST without diagram_type → 400."""
        resp = api_client.post(
            BASE_URL,
            {
                "api14": "42383396820000",
                "diagram_data": CURRENT_DIAGRAM_DATA,
            },
            format="json",
        )

        assert resp.status_code == 400
        assert "diagram_type" in resp.data
