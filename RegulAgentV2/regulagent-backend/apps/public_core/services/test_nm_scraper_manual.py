#!/usr/bin/env python
"""
Manual test script for NM Well Scraper

This script tests the scraper against the live NM OCD portal.
Run this manually when you want to verify the scraper works with real data.

Usage:
    docker compose -f compose.dev.yml exec web python apps/public_core/services/test_nm_scraper_manual.py
"""

import sys
import os

# Setup Django environment
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ra_config.settings.development')
django.setup()

from apps.public_core.services.nm_well_scraper import fetch_nm_well

def test_real_nm_well():
    """Test fetching a real well from NM OCD portal."""

    # Known good API: 30-015-28692 (EOG RESOURCES INC - FEDERAL 24-19 1H)
    test_api = "30-015-28692"

    print("=" * 80)
    print("NM WELL SCRAPER - MANUAL TEST")
    print("=" * 80)
    print(f"\nTesting with API: {test_api}")
    print("Fetching from: https://wwwapps.emnrd.nm.gov/OCD/OCDPermitting/Data/WellDetails.aspx")
    print("\nThis may take 10-30 seconds...\n")

    try:
        # Fetch well data
        well = fetch_nm_well(test_api)

        print("✅ SUCCESS! Well data retrieved:\n")
        print("-" * 80)
        print(f"API (10-digit):      {well.api10}")
        print(f"API (14-digit):      {well.api14}")
        print(f"Well Name:           {well.well_name}")
        print(f"Operator:            {well.operator_name} [{well.operator_number}]")
        print(f"Status:              {well.status}")
        print(f"Well Type:           {well.well_type}")
        print(f"Direction:           {well.direction}")
        print("-" * 80)
        print(f"Surface Location:    {well.surface_location}")
        print(f"Latitude:            {well.latitude}")
        print(f"Longitude:           {well.longitude}")
        print(f"Elevation:           {well.elevation_ft} ft")
        print("-" * 80)
        print(f"Proposed Depth:      {well.proposed_depth_ft} ft")
        print(f"True Vertical Depth: {well.tvd_ft} ft")
        print(f"Formation:           {well.formation}")
        print("-" * 80)
        print(f"Spud Date:           {well.spud_date}")
        print(f"Completion Date:     {well.completion_date}")
        print("-" * 80)

        # Basic validation
        issues = []
        if not well.well_name:
            issues.append("❌ Well name is empty")
        if not well.operator_name:
            issues.append("❌ Operator name is empty")
        if not well.status:
            issues.append("⚠️  Status is empty")
        if well.latitude is None or well.longitude is None:
            issues.append("⚠️  Coordinates are missing")

        if issues:
            print("\nValidation Issues:")
            for issue in issues:
                print(f"  {issue}")
        else:
            print("\n✅ All critical fields populated!")

        print("\n" + "=" * 80)
        print("TEST COMPLETE")
        print("=" * 80)

        return 0

    except ValueError as e:
        print(f"❌ ERROR - Invalid API format: {e}")
        return 1

    except Exception as e:
        print(f"❌ ERROR - Failed to fetch well data: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(test_real_nm_well())
