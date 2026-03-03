import pytest
from django_tenants.utils import schema_context

@pytest.fixture
def tenant_admin(db, test_tenant):
    """Create an admin user within test tenant context."""
    from apps.tenants.models import User
    with schema_context(test_tenant.schema_name):
        user = User.objects.create_user(
            email='admin@example.com',
            password='adminpass123',
            is_active=True
        )
    test_tenant.add_user(user, is_superuser=True, is_staff=True)
    return user
