#!/usr/bin/env python
"""
Standalone script to extract W-3A PDF and get OpenAI response.

Usage:
    python extract_w3a.py
"""

import os
import sys
import django
import json
from pathlib import Path

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ra_config.settings.development')
sys.path.insert(0, str(Path(__file__).parent))

django.setup()

# Now import extraction service
from apps.public_core.services.w3_extraction import extract_w3a_from_pdf, _validate_w3a_structure


def main():
    print("\n" + "=" * 80)
    print("🚀 W-3A EXTRACTION - REAL OPENAI RESPONSE")
    print("=" * 80 + "\n")
    
    # Find PDF
    pdf_path = Path(__file__).parent / "tmp" / "W3A_Examples" / "Approved_W3A_00346118_20250826_214942_.pdf"
    
    print(f"📄 PDF: {pdf_path}")
    print(f"✅ Exists: {pdf_path.exists()}")
    
    if not pdf_path.exists():
        print(f"\n❌ ERROR: PDF not found at {pdf_path}")
        sys.exit(1)
    
    print(f"📊 Size: {pdf_path.stat().st_size} bytes\n")
    
    try:
        print("🔄 Sending to OpenAI for extraction...\n")
        w3a_data = extract_w3a_from_pdf(str(pdf_path))
        
        print("✅ EXTRACTION SUCCESSFUL!\n")
        print("=" * 80)
        print("📋 EXTRACTED JSON RESPONSE")
        print("=" * 80 + "\n")
        
        # Print full JSON
        print(json.dumps(w3a_data, indent=2))
        
        print("\n" + "=" * 80)
        print("📊 SUMMARY")
        print("=" * 80 + "\n")
        
        # Validate
        _validate_w3a_structure(w3a_data)
        print("✅ Structure validation passed\n")
        
        # Summary
        if "header" in w3a_data:
            h = w3a_data["header"]
            print(f"API Number:    {h.get('api_number')}")
            print(f"Well Name:     {h.get('well_name')}")
            print(f"Operator:      {h.get('operator')}")
            print(f"County:        {h.get('county')}")
            print(f"RRC District:  {h.get('rrc_district')}")
            print(f"Field:         {h.get('field')}")
            print(f"Total Depth:   {h.get('total_depth')} ft")
        
        if "casing_record" in w3a_data:
            print(f"\nCasing Strings: {len(w3a_data['casing_record'])}")
            for i, cs in enumerate(w3a_data['casing_record'], 1):
                print(f"  {i}. {cs.get('string_type', 'unknown').title()}: {cs.get('size_in')}\" @ {cs.get('top_ft')}-{cs.get('bottom_ft')} ft")
        
        if "perforations" in w3a_data:
            print(f"\nPerforations: {len(w3a_data['perforations'])}")
            for perf in w3a_data['perforations'][:3]:
                print(f"  - {perf.get('interval_top_ft')}-{perf.get('interval_bottom_ft')} ft ({perf.get('formation')}, {perf.get('status')})")
        
        if "plugging_proposal" in w3a_data:
            plugs = w3a_data['plugging_proposal']
            print(f"\nPlugging Proposal: {len(plugs)} plugs")
            for plug in plugs[:5]:
                print(f"  Plug {plug.get('plug_number')}: {plug.get('depth_top_ft')}-{plug.get('depth_bottom_ft')} ft ({plug.get('type')})")
            if len(plugs) > 5:
                print(f"  ... and {len(plugs) - 5} more plugs")
        
        if "duqw" in w3a_data:
            duqw = w3a_data['duqw']
            print(f"\nDUQW: {duqw.get('depth_ft')} ft ({duqw.get('formation')})")
        
        # Save to file
        output_file = Path(__file__).parent / "tmp" / "w3a_extracted.json"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w') as f:
            json.dump(w3a_data, f, indent=2)
        
        print(f"\n💾 Saved to: {output_file}")
        print(f"   Size: {output_file.stat().st_size} bytes")
        
        print("\n" + "=" * 80)
        print("✅ COMPLETE - SEE JSON OUTPUT ABOVE")
        print("=" * 80 + "\n")
        
        return 0
    
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())





