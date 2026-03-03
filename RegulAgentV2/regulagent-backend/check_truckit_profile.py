import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from apps.integrations.truckit.models import TruckItIntegrationProfile
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant, clear_current_tenant

# Check all profiles without tenant filtering
print("\n=== ALL TruckIt Profiles (bypassing TenantAwareManager) ===")
all_profiles = TruckItIntegrationProfile.objects.using('default').all()
for p in all_profiles:
    print(f"ID: {p.id}, Tenant: {p.tenant_id}, External ID: {p.external_id}, Service: {p.service_id}")

# Check with tenant context
print("\n=== With Tenant Context (ID=1) ===")
tenant = Tenant.objects.get(id=1)
set_current_tenant(tenant)
context_profiles = TruckItIntegrationProfile.objects.all()
print(f"Count: {context_profiles.count()}")
for p in context_profiles:
    print(f"ID: {p.id}, Tenant: {p.tenant_id}, External ID: {p.external_id}")

clear_current_tenant()

