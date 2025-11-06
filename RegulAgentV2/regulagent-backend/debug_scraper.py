#!/usr/bin/env python
"""
Debug script to check Enverus scraper configuration and test functionality.
Run: docker exec -it regulagent_web python debug_scraper.py
"""

import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from apps.integrations.enverus.models import EnverusIntegrationProfile
from apps.integrations.enverus.services import EnverusStagingWellService
from apps.core.tenants.models import Tenant
from django.contrib.auth import get_user_model

User = get_user_model()

print("="*80)
print("ENVERUS SCRAPER DEBUG")
print("="*80)

# Get first tenant
try:
    tenant = Tenant.objects.first()
    print(f"\n✅ Found Tenant: {tenant.name} (ID: {tenant.id})")
except Exception as e:
    print(f"\n❌ Error getting tenant: {e}")
    exit(1)

# Check for Enverus profile
try:
    profile = EnverusIntegrationProfile.get_for_tenant(tenant)
    if profile:
        print(f"✅ Found EnverusIntegrationProfile (ID: {profile.id})")
        print(f"   - Staging wells enabled: {profile.staging_wells_enabled}")
        print(f"   - Subscription active: {profile.is_subscription_active()}")
        print(f"   - Can access staging wells: {profile.can_access_staging_wells()}")
        print(f"   - Has scraper credentials: {profile.has_scraper_credentials()}")
        
        if profile.has_scraper_credentials():
            username, password = profile.get_credentials()
            print(f"   - Username: {username if username else 'None'}")
            print(f"   - Password: {'*' * len(password) if password else 'None'}")
        else:
            print("\n❌ NO CREDENTIALS CONFIGURED!")
            print("   Run the following to configure:")
            print(f"\n   profile = EnverusIntegrationProfile.objects.get(id={profile.id})")
            print("   profile.set_credentials('your_email@example.com', 'your_password')")
            print("   profile.save()")
    else:
        print("❌ No EnverusIntegrationProfile found for tenant!")
        print("\n   Need to create one. Run:")
        print("\n   from apps.integrations.enverus.models import EnverusIntegrationProfile")
        print("   from apps.integrations.models import Service")
        print(f"   tenant = Tenant.objects.get(id={tenant.id})")
        print("   service = Service.objects.filter(provider__iexact='enverus').first()")
        print("   profile = EnverusIntegrationProfile.objects.create(")
        print("       tenant=tenant,")
        print("       service=service,")
        print("       staging_wells_enabled=True")
        print("   )")
        print("   profile.set_credentials('username', 'password')")
        print("   profile.save()")
        exit(1)
except Exception as e:
    print(f"❌ Error getting profile: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

# Test the service
print("\n" + "="*80)
print("TESTING SCRAPER SERVICE")
print("="*80)

try:
    service = EnverusStagingWellService(
        tenant=tenant,
        integration_profile=profile
    )
    print("✅ Service initialized")
    
    # Test access check
    try:
        service.check_access()
        print("✅ Access check passed")
    except Exception as e:
        print(f"❌ Access check failed: {e}")
        exit(1)
    
    # Test API parsing
    test_api = "42-329-41680"
    print(f"\n🔍 Testing with API: {test_api}")
    
    parsed = service.parse_and_validate_api_number(test_api)
    print(f"   - Valid: {parsed['is_valid']}")
    print(f"   - Supported: {parsed['is_supported']}")
    print(f"   - State: {parsed.get('state_code', 'N/A')}")
    
    if not parsed['is_valid'] or not parsed['is_supported']:
        print(f"❌ API number validation failed!")
        print(f"   Error: {parsed.get('error', 'Unknown')}")
        exit(1)
    
    # Check if well exists in DB
    print(f"\n🔍 Checking if well exists in staging DB...")
    existing_well = service.get_staging_well(test_api, auto_scrape=False)
    
    if existing_well:
        print(f"✅ Well found in DB: {existing_well.well_name}")
        print(f"   Operator: {existing_well.operator_name}")
    else:
        print(f"❌ Well NOT found in DB (expected - will trigger scrape)")
    
    print("\n" + "="*80)
    print("READY TO TEST SCRAPING")
    print("="*80)
    print("\nTo test scraping, run:")
    print(f"\n  well = service.scrape_and_save_well('{test_api}')")
    print("  print(f'Scraped: {well.well_name}')")
    
    if not profile.has_scraper_credentials():
        print("\n⚠️  WARNING: Credentials not configured. Scraping will fail!")
    else:
        print("\n✅ Credentials configured - scraping should work!")
        print("\n🚀 Would you like to test scraping now? (This will take 15-20 seconds)")
        print("   Run: python manage.py shell")
        print("   Then paste the commands above")
    
except Exception as e:
    print(f"\n❌ Error testing service: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

print("\n" + "="*80)
print("DEBUG COMPLETE")
print("="*80)

