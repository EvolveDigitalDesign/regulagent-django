"""
TDD: Failing tests for the TenantPlanningConfig API endpoint.

These tests define the expected behaviour of two endpoints that have NOT
been implemented yet.  Running this file should produce an ImportError
because neither the model nor the view exist — that is the intentional
"red" signal for TDD.

Endpoints under test
--------------------
GET /api/tenant/planning-config/  — return config (auto-create with defaults)
PUT /api/tenant/planning-config/  — update config fields (partial update ok)
"""

import pytest
from django.urls import reverse
from rest_framework import status
from django_tenants.utils import schema_context

# ---------------------------------------------------------------------------
# These imports MUST fail until the implementation is delivered.
# The ImportError is the "red" signal that keeps tests failing correctly.
# ---------------------------------------------------------------------------
from apps.tenants.models import TenantPlanningConfig  # noqa: F401
from apps.tenants.views import TenantPlanningConfigView  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_client(api_client, user):
    """Return an APIClient authenticated as *user* via JWT."""
    from rest_framework_simplejwt.tokens import RefreshToken

    refresh = RefreshToken.for_user(user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client


# Default field values that the auto-created config must expose.
_DEFAULTS = {
    "max_plug_length_ft": None,
    "min_plug_length_ft": None,
    "max_combined_plugs": None,
    "use_cibp": "when_perforated",
    "cibp_cap_ft": 100,
    "use_bailer_method": False,
    "use_cement_retainer": "when_required",
    "cement_to_surface": True,
    "cased_hole_excess_factor": 0.50,
    "open_hole_excess_factor": 1.00,
}


# ---------------------------------------------------------------------------
# GET /api/tenant/planning-config/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGetPlanningConfig:
    """GET /api/tenant/planning-config/ — retrieve (or auto-create) config."""

    def test_unauthenticated_returns_401(self, api_client, test_tenant):
        """Requests without a JWT must be rejected with 401."""
        with schema_context(test_tenant.schema_name):
            url = reverse("tenant-planning-config")
            response = api_client.get(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_get_creates_defaults_if_none_exist(
        self, api_client, test_tenant, tenant_admin
    ):
        """
        When no TenantPlanningConfig row exists for the tenant, a GET request
        must auto-create one with default values and return 200.
        """
        # Ensure no config row exists before the request.
        with schema_context(test_tenant.schema_name):
            tenant = tenant_admin.tenants.exclude(schema_name="public").first()
            TenantPlanningConfig.objects.filter(tenant=tenant).delete()

        client = _auth_client(api_client, tenant_admin)

        with schema_context(test_tenant.schema_name):
            url = reverse("tenant-planning-config")
            response = client.get(url)

        assert response.status_code == status.HTTP_200_OK

        data = response.json()

        # All default values must be present in the response.
        for field, expected in _DEFAULTS.items():
            assert field in data, f"Response missing field '{field}'"
            assert data[field] == expected, (
                f"Field '{field}': expected {expected!r}, got {data[field]!r}"
            )

    def test_get_returns_existing_config(
        self, api_client, test_tenant, tenant_admin
    ):
        """
        When a TenantPlanningConfig row already exists, GET must return the
        stored values rather than defaults.
        """
        tenant = tenant_admin.tenants.exclude(schema_name="public").first()

        # Pre-create the config with non-default values.
        TenantPlanningConfig.objects.update_or_create(
            tenant=tenant,
            defaults={
                "max_plug_length_ft": 500,
                "use_cibp": "always",
                "cibp_cap_ft": 200,
                "cement_to_surface": False,
            },
        )

        client = _auth_client(api_client, tenant_admin)

        with schema_context(test_tenant.schema_name):
            url = reverse("tenant-planning-config")
            response = client.get(url)

        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["max_plug_length_ft"] == 500
        assert data["use_cibp"] == "always"
        assert data["cibp_cap_ft"] == 200
        assert data["cement_to_surface"] is False

    def test_get_returns_null_for_optional_fields(
        self, api_client, test_tenant, tenant_admin
    ):
        """
        The three nullable length-preference fields must be returned as null
        (JSON null / Python None) when they have not been set.
        """
        tenant = tenant_admin.tenants.exclude(schema_name="public").first()

        # Create config without setting the optional fields.
        TenantPlanningConfig.objects.update_or_create(
            tenant=tenant,
            defaults={
                "max_plug_length_ft": None,
                "min_plug_length_ft": None,
                "max_combined_plugs": None,
            },
        )

        client = _auth_client(api_client, tenant_admin)

        with schema_context(test_tenant.schema_name):
            url = reverse("tenant-planning-config")
            response = client.get(url)

        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["max_plug_length_ft"] is None, (
            "max_plug_length_ft must be null when unset"
        )
        assert data["min_plug_length_ft"] is None, (
            "min_plug_length_ft must be null when unset"
        )
        assert data["max_combined_plugs"] is None, (
            "max_combined_plugs must be null when unset"
        )


# ---------------------------------------------------------------------------
# PUT /api/tenant/planning-config/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUpdatePlanningConfig:
    """PUT /api/tenant/planning-config/ — update config (partial update ok)."""

    def test_unauthenticated_put_returns_401(self, api_client, test_tenant):
        """Requests without a JWT must be rejected with 401."""
        with schema_context(test_tenant.schema_name):
            url = reverse("tenant-planning-config")
            response = api_client.put(url, {"cibp_cap_ft": 150}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_update_max_plug_length(
        self, api_client, test_tenant, tenant_admin
    ):
        """
        PUT with max_plug_length_ft must persist the new value and return 200
        with the updated config.
        """
        tenant = tenant_admin.tenants.exclude(schema_name="public").first()
        client = _auth_client(api_client, tenant_admin)

        with schema_context(test_tenant.schema_name):
            url = reverse("tenant-planning-config")
            response = client.put(url, {"max_plug_length_ft": 300}, format="json")

        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["max_plug_length_ft"] == 300

        # Confirm persistence in the database.
        config = TenantPlanningConfig.objects.get(tenant=tenant)
        assert config.max_plug_length_ft == 300

    def test_update_use_cibp_choices(
        self, api_client, test_tenant, tenant_admin
    ):
        """
        PUT with a valid use_cibp choice ('always') must return 200.
        PUT with an invalid choice must return 400.
        """
        client = _auth_client(api_client, tenant_admin)

        with schema_context(test_tenant.schema_name):
            url = reverse("tenant-planning-config")

            # Valid choice — must succeed.
            response_valid = client.put(url, {"use_cibp": "always"}, format="json")
            assert response_valid.status_code == status.HTTP_200_OK
            assert response_valid.json()["use_cibp"] == "always"

            # Invalid choice — must be rejected.
            response_invalid = client.put(
                url, {"use_cibp": "invalid_choice"}, format="json"
            )
            assert response_invalid.status_code == status.HTTP_400_BAD_REQUEST

    def test_update_cement_to_surface(
        self, api_client, test_tenant, tenant_admin
    ):
        """
        PUT with cement_to_surface=false must persist False and return 200.
        A subsequent GET must also reflect the persisted value.
        """
        tenant = tenant_admin.tenants.exclude(schema_name="public").first()
        client = _auth_client(api_client, tenant_admin)

        with schema_context(test_tenant.schema_name):
            url = reverse("tenant-planning-config")

            put_response = client.put(
                url, {"cement_to_surface": False}, format="json"
            )
        assert put_response.status_code == status.HTTP_200_OK
        assert put_response.json()["cement_to_surface"] is False

        # Re-fetch via GET to confirm persistence.
        with schema_context(test_tenant.schema_name):
            get_response = client.get(url)

        assert get_response.status_code == status.HTTP_200_OK
        assert get_response.json()["cement_to_surface"] is False

        # Confirm at the model level.
        config = TenantPlanningConfig.objects.get(tenant=tenant)
        assert config.cement_to_surface is False

    def test_partial_update_preserves_other_fields(
        self, api_client, test_tenant, tenant_admin
    ):
        """
        A PUT that only sends one field must NOT overwrite other fields back to
        their defaults.  The API supports partial update semantics (missing
        fields are left unchanged).
        """
        tenant = tenant_admin.tenants.exclude(schema_name="public").first()

        # Seed the config with a set of non-default values.
        TenantPlanningConfig.objects.update_or_create(
            tenant=tenant,
            defaults={
                "use_cibp": "never",
                "cibp_cap_ft": 75,
                "use_bailer_method": True,
                "cement_to_surface": False,
                "cased_hole_excess_factor": 0.60,
            },
        )

        client = _auth_client(api_client, tenant_admin)

        # Update only cibp_cap_ft — every other field must remain unchanged.
        with schema_context(test_tenant.schema_name):
            url = reverse("tenant-planning-config")
            response = client.put(url, {"cibp_cap_ft": 90}, format="json")

        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["cibp_cap_ft"] == 90, "Updated field must reflect new value"

        # Fields not included in the PUT must retain their pre-update values.
        assert data["use_cibp"] == "never", (
            "use_cibp must not be reset to default"
        )
        assert data["use_bailer_method"] is True, (
            "use_bailer_method must not be reset to default"
        )
        assert data["cement_to_surface"] is False, (
            "cement_to_surface must not be reset to default"
        )
        assert data["cased_hole_excess_factor"] == 0.60, (
            "cased_hole_excess_factor must not be reset to default"
        )
