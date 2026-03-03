import pytest
from django.conf import settings
from django.db import connection

# Ensure Django settings are configured
def pytest_configure(config):
    settings.DEBUG = False

@pytest.fixture(scope='session')
def django_db_setup(django_db_setup, django_db_blocker):
    """
    Custom database setup for django-tenants.
    Ensures the public schema is created and migrations are run.
    """
    with django_db_blocker.unblock():
        from django.core.management import call_command
        # Run migrations for public schema
        call_command('migrate_schemas', schema_name='public', verbosity=0)

@pytest.fixture
def api_client():
    """Return DRF APIClient for testing."""
    from rest_framework.test import APIClient
    return APIClient()

@pytest.fixture
def authenticated_client(api_client, test_user):
    """Return authenticated APIClient."""
    from rest_framework_simplejwt.tokens import RefreshToken
    refresh = RefreshToken.for_user(test_user)
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')
    return api_client
