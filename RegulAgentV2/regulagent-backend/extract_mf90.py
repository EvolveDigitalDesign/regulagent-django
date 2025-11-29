#!/usr/bin/env python
import os
import sys
import django
import json
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ra_config.settings.development')
sys.path.insert(0, str(Path(__file__).parent))
django.setup()

from apps.public_core.services.w3_extraction import extract_w3a_from_pdf

pdf_path = "tmp/W3A_Examples/MF Unit 90.pdf"
print(f"Extracting: {pdf_path}")

try:
    w3a_data = extract_w3a_from_pdf(pdf_path)
    
    # Save to file
    output_file = Path("tmp/mf90_w3a_extracted.json")
    with open(output_file, 'w') as f:
        json.dump(w3a_data, f, indent=2)
    
    print(f"\n✅ Extraction complete! Saved to {output_file}")
    print(f"\nQuick summary:")
    print(f"  API: {w3a_data['header'].get('api_number')}")
    print(f"  Well: {w3a_data['header'].get('well_name')}")
    print(f"  Casings: {len(w3a_data['casing_record'])}")
    print(f"  Plugging proposal: {len(w3a_data['plugging_proposal'])} plugs")
    print(f"  Operational steps: {len(w3a_data['operational_steps'])} steps")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
