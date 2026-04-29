"""
TDD: Failing tests for Card 13 — welcome email on new tenant user creation.

These tests define the expected behaviour of three components that have NOT
been implemented yet.  Running this file should produce collection errors or
ImportErrors because the referenced modules do not exist.

Components under test
---------------------
apps/tenants/services/email_service.py  — send_welcome_email(user, temp_password)
apps/tenants/tasks.py                   — send_welcome_email_task(user_id, temp_password)
TenantUserListCreateView.post()         — dispatches task after user creation
"""

import pytest
from unittest.mock import patch, MagicMock
from django_tenants.utils import schema_context, get_public_schema_name

# ---------------------------------------------------------------------------
# These imports MUST fail until the implementation is delivered.
# The ImportError is the "red" signal that keeps tests properly failing.
# ---------------------------------------------------------------------------
from apps.tenants.services.email_service import send_welcome_email  # noqa: F401
from apps.tenants.tasks import send_welcome_email_task  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers (mirrors test_user_management.py patterns)
# ---------------------------------------------------------------------------

def _make_user(tenant, email, password="pass1234!", is_active=True, **kwargs):
    """Create a User inside *tenant*'s schema and add them to the tenant."""
    from apps.tenants.models import User

    with schema_context(tenant.schema_name):
        user = User.objects.create_user(
            email=email,
            password=password,
            is_active=is_active,
            **kwargs,
        )
    tenant.add_user(user, is_superuser=False, is_staff=False)
    return user


def _admin_client(api_client, admin_user):
    """Return an APIClient authenticated as *admin_user* via JWT."""
    from rest_framework_simplejwt.tokens import RefreshToken

    refresh = RefreshToken.for_user(admin_user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client


# ---------------------------------------------------------------------------
# Class 1: Unit tests for the email service directly
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestWelcomeEmailService:
    """Unit tests for send_welcome_email() in apps/tenants/services/email_service.py."""

    def _make_mock_user(self, email="newuser@example.com"):
        """Return a simple mock object that satisfies send_welcome_email's interface."""
        user = MagicMock()
        user.email = email
        return user

    def test_send_welcome_email_calls_send_mail(self):
        """send_welcome_email must call django.core.mail.send_mail exactly once
        with to=[user.email]."""
        user = self._make_mock_user()

        with patch("django.core.mail.send_mail") as mock_send_mail:
            send_welcome_email(user, "TmpPass123")

        mock_send_mail.assert_called_once()
        _args, kwargs = mock_send_mail.call_args
        # Support both positional and keyword 'recipient_list'
        recipient_list = kwargs.get("recipient_list") or _args[3]
        assert recipient_list == [user.email], (
            f"Expected recipient_list=[{user.email!r}], got {recipient_list!r}"
        )

    def test_welcome_email_subject_contains_regulagent(self):
        """The email subject must contain 'RegulAgent'."""
        user = self._make_mock_user()

        with patch("django.core.mail.send_mail") as mock_send_mail:
            send_welcome_email(user, "TmpPass123")

        _args, kwargs = mock_send_mail.call_args
        subject = kwargs.get("subject") or _args[0]
        assert "RegulAgent" in subject, (
            f"Expected 'RegulAgent' in subject, got: {subject!r}"
        )

    def test_welcome_email_subject_contains_welcome(self):
        """The email subject must contain 'Welcome'."""
        user = self._make_mock_user()

        with patch("django.core.mail.send_mail") as mock_send_mail:
            send_welcome_email(user, "TmpPass123")

        _args, kwargs = mock_send_mail.call_args
        subject = kwargs.get("subject") or _args[0]
        assert "Welcome" in subject, (
            f"Expected 'Welcome' in subject, got: {subject!r}"
        )

    def test_welcome_email_body_contains_temp_password(self):
        """The email body must contain the temp_password value."""
        user = self._make_mock_user()
        temp_password = "TmpPass123"

        with patch("django.core.mail.send_mail") as mock_send_mail:
            send_welcome_email(user, temp_password)

        _args, kwargs = mock_send_mail.call_args
        # send_mail positional: subject, message, from_email, recipient_list
        message = kwargs.get("message") or _args[1]
        assert temp_password in message, (
            f"Expected temp_password {temp_password!r} in body, got: {message!r}"
        )

    def test_welcome_email_body_contains_login_url(self):
        """The email body must contain the FRONTEND_URL login link."""
        user = self._make_mock_user()
        frontend_url = "https://app.example.com"

        with patch("django.core.mail.send_mail") as mock_send_mail, \
             patch("django.conf.settings.FRONTEND_URL", frontend_url, create=True):
            send_welcome_email(user, "TmpPass123")

        _args, kwargs = mock_send_mail.call_args
        message = kwargs.get("message") or _args[1]
        assert frontend_url in message, (
            f"Expected FRONTEND_URL {frontend_url!r} in body, got: {message!r}"
        )

    def test_welcome_email_body_contains_change_password_prompt(self):
        """The email body must prompt the user to change their password."""
        user = self._make_mock_user()

        with patch("django.core.mail.send_mail") as mock_send_mail:
            send_welcome_email(user, "TmpPass123")

        _args, kwargs = mock_send_mail.call_args
        message = kwargs.get("message") or _args[1]
        assert "change" in message.lower(), (
            f"Expected 'change' (case-insensitive) in body, got: {message!r}"
        )


# ---------------------------------------------------------------------------
# Class 2: Unit tests for the Celery task
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestWelcomeEmailTask:
    """Unit tests for send_welcome_email_task in apps/tenants/tasks.py."""

    def test_task_calls_send_welcome_email(self, public_tenant):
        """Calling the task directly (not .delay()) must invoke send_welcome_email
        with the correct User instance and temp_password."""
        from apps.tenants.models import User

        temp_password = "TmpPass123"

        with schema_context(get_public_schema_name()):
            user = User.objects.create_user(
                email="tasktest@example.com",
                password="irrelevant_pass",
            )

        patch_target = "apps.tenants.tasks.send_welcome_email"
        with patch(patch_target) as mock_send_welcome_email:
            send_welcome_email_task(user.id, temp_password)

        mock_send_welcome_email.assert_called_once()
        call_args = mock_send_welcome_email.call_args
        called_user, called_password = call_args[0]
        assert called_user.id == user.id, (
            f"Expected user.id={user.id}, got {called_user.id}"
        )
        assert called_password == temp_password, (
            f"Expected temp_password={temp_password!r}, got {called_password!r}"
        )


# ---------------------------------------------------------------------------
# Class 3: Integration test — API dispatch
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCreateUserSendsEmail:
    """Integration test: POST /api/tenant/users/ must dispatch send_welcome_email_task."""

    def test_create_user_dispatches_email_task(
        self, api_client, test_tenant, tenant_admin
    ):
        """After creating a user, send_welcome_email_task.delay must be called with
        the new user's id and the temp_password returned in the response."""
        from django.urls import reverse

        client = _admin_client(api_client, tenant_admin)

        payload = {
            "email": "welcometest@example.com",
            "first_name": "Welcome",
            "last_name": "Tester",
        }

        patch_target = "apps.tenants.views.send_welcome_email_task"
        with patch(patch_target) as mock_task:
            mock_task.delay = MagicMock()

            with schema_context(test_tenant.schema_name):
                url = reverse("tenant-users-list")
                response = client.post(url, payload, format="json")

        assert response.status_code == 201, (
            f"Expected 201, got {response.status_code}: {response.json()}"
        )

        data = response.json()
        new_user_id = data["id"]
        temp_password = data["temp_password"]

        mock_task.delay.assert_called_once_with(new_user_id, temp_password)
