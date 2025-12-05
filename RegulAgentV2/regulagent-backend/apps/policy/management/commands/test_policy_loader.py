"""
Management command to test policy loader with specific district/county/field combinations.
Usage: python manage.py test_policy_loader --district 08 --county GLASSCOCK --field "SPRABERRY (TREND AREA)"
"""
import json
from typing import Any, Dict
from django.core.management.base import BaseCommand
from apps.policy.services.loader import get_effective_policy


class Command(BaseCommand):
    help = 'Test policy loader with specific district/county/field to debug formation_tops lookup'

    def add_arguments(self, parser):
        parser.add_argument('--district', type=str, required=True, help='RRC District (e.g., 08, 08A, 7C)')
        parser.add_argument('--county', type=str, required=True, help='County name (e.g., GLASSCOCK)')
        parser.add_argument('--field', type=str, required=False, help='Field name (e.g., SPRABERRY (TREND AREA))')

    def handle(self, *args: Any, **options: Any) -> None:
        district = options['district']
        county = options['county']
        field = options.get('field')
        
        self.stdout.write(self.style.SUCCESS('='*80))
        self.stdout.write(self.style.SUCCESS(f'Testing Policy Loader'))
        self.stdout.write(self.style.SUCCESS('='*80))
        self.stdout.write(f"District: {district}")
        self.stdout.write(f"County: {county}")
        self.stdout.write(f"Field: {field or '(none)'}")
        self.stdout.write("")
        
        try:
            policy = get_effective_policy(district=district, county=county, field=field)
            
            self.stdout.write(self.style.SUCCESS('✅ Policy loaded successfully'))
            self.stdout.write("")
            
            # Show top-level keys
            self.stdout.write(self.style.WARNING('Top-level policy keys:'))
            for key in policy.keys():
                self.stdout.write(f"  - {key}")
            self.stdout.write("")
            
            # Check for 'effective' key
            if 'effective' in policy:
                effective = policy['effective']
                self.stdout.write(self.style.WARNING('Keys in policy["effective"]:'))
                for key in effective.keys():
                    self.stdout.write(f"  - {key}")
                self.stdout.write("")
                
                # Check district_overrides
                if 'district_overrides' in effective:
                    dist_overrides = effective['district_overrides']
                    self.stdout.write(self.style.WARNING('Keys in policy["effective"]["district_overrides"]:'))
                    for key in dist_overrides.keys():
                        self.stdout.write(f"  - {key}")
                    self.stdout.write("")
                    
                    # Check formation_tops
                    formation_tops = dist_overrides.get('formation_tops') or []
                    if formation_tops:
                        self.stdout.write(self.style.SUCCESS(f'✅ Found {len(formation_tops)} formation tops:'))
                        for ft in formation_tops:
                            formation = ft.get('formation')
                            top_ft = ft.get('top_ft')
                            plug_req = ft.get('plug_required')
                            tag_req = ft.get('tag_required')
                            self.stdout.write(f"  - {formation} @ {top_ft} ft (plug_required={plug_req}, tag_required={tag_req})")
                    else:
                        self.stdout.write(self.style.ERROR('❌ No formation_tops found in district_overrides'))
                else:
                    self.stdout.write(self.style.ERROR('❌ No district_overrides in policy["effective"]'))
            else:
                # No 'effective' key - check if district_overrides is at top level
                self.stdout.write(self.style.WARNING('No "effective" key - checking top-level district_overrides'))
                dist_overrides = policy.get('district_overrides') or {}
                formation_tops = dist_overrides.get('formation_tops') or []
                
                if formation_tops:
                    self.stdout.write(self.style.SUCCESS(f'✅ Found {len(formation_tops)} formation tops at top-level:'))
                    for ft in formation_tops:
                        formation = ft.get('formation')
                        top_ft = ft.get('top_ft')
                        self.stdout.write(f"  - {formation} @ {top_ft} ft")
                else:
                    self.stdout.write(self.style.ERROR('❌ No formation_tops found at top-level either'))
            
            # Show field resolution info
            if 'field_resolution' in policy:
                fr = policy['field_resolution']
                self.stdout.write("")
                self.stdout.write(self.style.WARNING('Field Resolution:'))
                self.stdout.write(f"  - Requested: {fr.get('requested_field')}")
                self.stdout.write(f"  - Matched: {fr.get('matched_field')}")
                self.stdout.write(f"  - County: {fr.get('matched_in_county')}")
                self.stdout.write(f"  - Method: {fr.get('method')}")
                if fr.get('nearest_distance_km'):
                    self.stdout.write(f"  - Distance: {fr.get('nearest_distance_km'):.1f} km")
            
            # Dump full structure as JSON for inspection
            self.stdout.write("")
            self.stdout.write(self.style.WARNING('Full policy structure (JSON):'))
            self.stdout.write(json.dumps(policy, indent=2, default=str))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Failed to load policy: {e}'))
            import traceback
            traceback.print_exc()

