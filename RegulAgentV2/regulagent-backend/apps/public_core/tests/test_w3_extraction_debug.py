"""
Debug script for W-3A extraction - simplified version with verbose output
"""

import sys
import json
import os
from pathlib import Path

print("=" * 80, file=sys.stderr)
print("🚀 STARTING W-3A EXTRACTION DEBUG TEST", file=sys.stderr)
print("=" * 80, file=sys.stderr)

# Check Python environment
print(f"\n📍 Python: {sys.executable}", file=sys.stderr)
print(f"📍 CWD: {os.getcwd()}", file=sys.stderr)

# Try to locate the PDF
print("\n🔍 Looking for PDF...", file=sys.stderr)
pdf_candidates = [
    Path(__file__).parent.parent.parent.parent.parent / "tmp" / "W3A_Examples" / "Approved_W3A_00346118_20250826_214942_.pdf",
    Path("tmp/W3A_Examples/Approved_W3A_00346118_20250826_214942_.pdf"),
    Path("/Users/ru/Git/JMR/RegulatoryAgent/regulagent-django/RegulAgentV2/regulagent-backend/tmp/W3A_Examples/Approved_W3A_00346118_20250826_214942_.pdf"),
]

pdf_path = None
for candidate in pdf_candidates:
    print(f"   Checking: {candidate}", file=sys.stderr)
    if candidate.exists():
        pdf_path = candidate
        print(f"   ✅ FOUND at: {pdf_path}", file=sys.stderr)
        print(f"   Size: {pdf_path.stat().st_size} bytes", file=sys.stderr)
        break
    else:
        print(f"   ❌ Not found", file=sys.stderr)

if not pdf_path:
    print("\n❌ PDF NOT FOUND in any location!", file=sys.stderr)
    print("\nTried:", file=sys.stderr)
    for candidate in pdf_candidates:
        print(f"  - {candidate}", file=sys.stderr)
    sys.exit(1)

# Try to import extraction service
print("\n📦 Importing extraction service...", file=sys.stderr)
try:
    from apps.public_core.services.w3_extraction import extract_w3a_from_pdf, _validate_w3a_structure
    print("   ✅ Import successful", file=sys.stderr)
except Exception as e:
    print(f"   ❌ Import failed: {e}", file=sys.stderr)
    sys.exit(1)

# Try to extract
print("\n🔄 Sending PDF to OpenAI...", file=sys.stderr)
print(f"   PDF: {pdf_path}", file=sys.stderr)

try:
    w3a_data = extract_w3a_from_pdf(str(pdf_path))
    print("   ✅ Extraction successful!", file=sys.stderr)
    
    # Show extracted data
    print("\n✅ EXTRACTED W-3A DATA:", file=sys.stderr)
    print("\nJSON Response:\n", file=sys.stdout)
    print(json.dumps(w3a_data, indent=2))
    
    # Validate
    print("\n🔍 Validating structure...", file=sys.stderr)
    _validate_w3a_structure(w3a_data)
    print("   ✅ Validation passed!", file=sys.stderr)
    
    # Show summary
    print("\n📊 EXTRACTION SUMMARY:", file=sys.stderr)
    if "header" in w3a_data:
        h = w3a_data["header"]
        print(f"   API: {h.get('api_number')}", file=sys.stderr)
        print(f"   Well: {h.get('well_name')}", file=sys.stderr)
        print(f"   County: {h.get('county')}", file=sys.stderr)
        print(f"   Operator: {h.get('operator')}", file=sys.stderr)
        print(f"   Field: {h.get('field')}", file=sys.stderr)
    
    if "casing_record" in w3a_data:
        print(f"   Casings: {len(w3a_data['casing_record'])}", file=sys.stderr)
    
    if "plugging_proposal" in w3a_data:
        print(f"   Plugs: {len(w3a_data['plugging_proposal'])}", file=sys.stderr)
    
    if "perforations" in w3a_data:
        print(f"   Perforations: {len(w3a_data['perforations'])}", file=sys.stderr)
    
    # Save to file
    print("\n💾 Saving to file...", file=sys.stderr)
    output_path = Path("/Users/ru/Git/JMR/RegulatoryAgent/regulagent-django/RegulAgentV2/regulagent-backend/tmp/w3a_extracted.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(w3a_data, f, indent=2)
    print(f"   ✅ Saved to: {output_path}", file=sys.stderr)
    print(f"   Size: {output_path.stat().st_size} bytes", file=sys.stderr)
    
    print("\n" + "=" * 80, file=sys.stderr)
    print("✅ TEST PASSED - JSON OUTPUT ABOVE", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    
except Exception as e:
    print(f"\n❌ EXTRACTION FAILED: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

