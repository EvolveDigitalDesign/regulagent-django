#!/usr/bin/env python
"""
Test W-3 Builder with Real Data

Usage:
    docker exec regulagent_web python manage.py shell < test_w3_builder.py
    
Or directly:
    docker exec regulagent_web python test_w3_builder.py

This script:
1. Loads pnaexchange events from JSON file
2. Loads W-3A from PDF
3. Runs the W3 builder
4. Outputs the generated W-3 form
5. Saves result to JSON file
"""

import os
import sys
import django
import json
from pathlib import Path
import logging

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ra_config.settings.development')
sys.path.insert(0, str(Path(__file__).parent))
django.setup()

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import our services (now that Django is initialized)
from apps.public_core.services.w3_builder import build_w3_from_pna_payload

def main():
    print("\n" + "=" * 80)
    print("🧪 W-3 BUILDER TEST - REAL DATA")
    print("=" * 80 + "\n")
    
    # Paths
    base_dir = Path(__file__).parent
    pna_json_file = base_dir / "tmp" / "midland_farms_unit_90_extracted.json"
    w3a_pdf_file = base_dir / "tmp" / "W3A_Examples" / "MF Unit 90.pdf"
    output_file = base_dir / "tmp" / "w3_output_mf90.json"
    
    # ============================================================
    # STEP 1: Load pnaexchange JSON
    # ============================================================
    print("📥 STEP 1: Loading pnaexchange extracted events...\n")
    
    if not pna_json_file.exists():
        print(f"❌ File not found: {pna_json_file}")
        return
    
    with open(pna_json_file, 'r') as f:
        pna_data = json.load(f)
    
    w3_form_data = pna_data.get("w3_form", {})
    pna_events = w3_form_data.get("events", [])
    
    print(f"✅ Loaded {len(pna_events)} pnaexchange events")
    print(f"   Well: {w3_form_data.get('well', {}).get('well_name')}")
    print(f"   API: {w3_form_data.get('well', {}).get('api_number')}")
    print(f"   Date range: {w3_form_data.get('summary', {}).get('date_range', {})}\n")
    
    # ============================================================
    # STEP 2: Build payload for W3 builder
    # ============================================================
    print("📋 STEP 2: Building W-3 builder payload...\n")
    
    payload = {
        "dwr_id": w3_form_data.get("subproject", {}).get("id"),
        "api_number": w3_form_data.get("well", {}).get("api_number"),
        "well_name": w3_form_data.get("well", {}).get("well_name"),
        "w3a_reference": {
            "type": "pdf",
            "w3a_file": str(w3a_pdf_file)  # For demo, pass file path as string
        },
        "pna_events": [
            {
                "event_id": _map_event_type_to_id(e.get("event_type")),
                "display_text": e.get("event_type"),
                "date": e.get("date"),
                "start_time": e.get("start_time"),
                "end_time": e.get("end_time"),
                "work_assignment_id": e.get("work_assignment_id"),
                "dwr_id": e.get("dwr_id"),
                "input_values": e.get("input_values", {}),
                "transformation_rules": e.get("transformation_rules", {}),
            }
            for e in pna_events
        ]
    }
    
    print(f"✅ Payload built with {len(payload['pna_events'])} events\n")
    
    # ============================================================
    # STEP 3: Run W3 builder
    # ============================================================
    print("🏗️ STEP 3: Running W-3 builder...\n")
    
    try:
        # Note: For this test, we need to mock file upload since we're not in a real HTTP request
        # In production, request.FILES would contain the actual file
        result = build_w3_from_pna_payload(payload, request=None)
        
        # ============================================================
        # STEP 4: Display results
        # ============================================================
        print("\n" + "=" * 80)
        print("📊 RESULTS")
        print("=" * 80 + "\n")
        
        if result.get("success"):
            print("✅ SUCCESS\n")
            
            w3_form = result.get("w3_form", {})
            
            # Header
            print("📋 HEADER:")
            header = w3_form.get("header", {})
            print(f"   API: {header.get('api_number')}")
            print(f"   Well: {header.get('well_name')}")
            print(f"   Operator: {header.get('operator')}")
            print(f"   County: {header.get('county')}")
            print(f"   RRC District: {header.get('rrc_district')}\n")
            
            # Plugs
            plugs = w3_form.get("plugs", [])
            print(f"⚙️ PLUGS ({len(plugs)} total):\n")
            
            for plug in plugs[:6]:  # Show first 6
                print(f"   Plug #{plug.get('plug_number')}:")
                print(f"     Depths: {plug.get('depth_top_ft')} - {plug.get('depth_bottom_ft')} ft")
                print(f"     Type: {plug.get('type')}")
                print(f"     Cement Class: {plug.get('cement_class')}")
                print(f"     Sacks: {plug.get('sacks')}")
                print(f"     Hole Size: {plug.get('hole_size_in')}\"")
                print(f"     Slurry Weight: {plug.get('slurry_weight_ppg')} lbs/gal")
                print(f"     TOC (measured): {plug.get('measured_top_of_plug_ft')} ft")
                print(f"     TOC (calculated): {plug.get('calculated_top_of_plug_ft'):.1f} ft")
                if plug.get('toc_variance_ft') is not None:
                    print(f"     TOC Variance: {plug.get('toc_variance_ft'):+.1f} ft")
                print()
            
            if len(plugs) > 6:
                print(f"   ... and {len(plugs) - 6} more plugs\n")
            
            # Validation
            validation = result.get("validation", {})
            warnings = validation.get("warnings", [])
            errors = validation.get("errors", [])
            
            if warnings:
                print(f"⚠️  WARNINGS ({len(warnings)}):")
                for w in warnings[:3]:
                    print(f"   - {w}")
                if len(warnings) > 3:
                    print(f"   ... and {len(warnings) - 3} more")
                print()
            
            if errors:
                print(f"❌ ERRORS ({len(errors)}):")
                for e in errors:
                    print(f"   - {e}")
                print()
            
            # Metadata
            metadata = result.get("metadata", {})
            print(f"📊 METADATA:")
            print(f"   Events processed: {metadata.get('events_processed')}")
            print(f"   Plugs grouped: {metadata.get('plugs_grouped')}")
            print(f"   Generated: {metadata.get('generated_at')}\n")
        
        else:
            print(f"❌ FAILED: {result.get('error')}\n")
        
        # ============================================================
        # STEP 5: Save to file
        # ============================================================
        print("💾 STEP 4: Saving output...\n")
        
        with open(output_file, 'w') as f:
            json.dump(result, f, indent=2)
        
        print(f"✅ Saved to: {output_file}")
        print(f"   Size: {output_file.stat().st_size} bytes\n")
        
        print("=" * 80)
        print("✅ TEST COMPLETE")
        print("=" * 80 + "\n")
    
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


def _map_event_type_to_id(event_type: str) -> int:
    """Map pnaexchange event_type string to event_id."""
    mapping = {
        "Set Intermediate Plug": 4,
        "Set Surface Plug": 3,
        "Squeeze": 7,
        "Broke Circulation": 2,
        "Pressure Up": 9,
        "Set CIBP": 6,
        "Cut Casing": 12,
        "Tag TOC": 8,
        "Tagged TOC": 5,
        "Perforation": 1,
        "Tag CIBP": 11,
        "RRC Approval": 10,
    }
    
    event_lower = event_type.lower()
    
    # Try exact match first
    for key, val in mapping.items():
        if key == event_type:
            return val
    
    # Try substring match
    for key, val in mapping.items():
        if key.lower() in event_lower or event_lower in key.lower():
            return val
    
    # Default based on substring patterns
    if "plug" in event_lower and "intermediate" in event_lower:
        return 4
    elif "plug" in event_lower and "surface" in event_lower:
        return 3
    elif "squeeze" in event_lower:
        return 7
    elif "circulation" in event_lower:
        return 2
    elif "pressure" in event_lower:
        return 9
    elif "cibp" in event_lower or "bridge" in event_lower:
        return 6
    elif "cut" in event_lower and "casing" in event_lower:
        return 12
    elif "tag" in event_lower and ("toc" in event_lower or "cement" in event_lower):
        return 8
    elif "perf" in event_lower:
        return 1
    elif "approval" in event_lower:
        return 10
    
    # Final fallback
    return 1  # Default to perforation


if __name__ == "__main__":
    main()

