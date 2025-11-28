"""
Real W-3A Extraction Integration Test

This script sends the actual W-3A PDF to OpenAI and captures the real JSON response.
NOT a mock test - actual API integration with real data.

Usage:
    python manage.py shell < apps/public_core/tests/test_w3_extraction_real.py
    
Or run directly:
    python -c "exec(open('apps/public_core/tests/test_w3_extraction_real.py').read())"
"""

import json
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import extraction service
from apps.public_core.services.w3_extraction import extract_w3a_from_pdf
from apps.public_core.services.w3_extraction import _validate_w3a_structure


def test_real_w3a_extraction():
    """
    Send real W-3A PDF to OpenAI and capture actual response.
    
    Tests with: Approved_W3A_00346118_20250826_214942_.pdf
    """
    # Path to real W-3A PDF
    pdf_path = Path(__file__).parent.parent.parent.parent.parent / "tmp" / "W3A_Examples" / "Approved_W3A_00346118_20250826_214942_.pdf"
    
    if not pdf_path.exists():
        logger.error(f"❌ PDF not found at: {pdf_path}")
        return False
    
    logger.info(f"📄 Testing real W-3A extraction")
    logger.info(f"   PDF: {pdf_path}")
    logger.info(f"   Size: {pdf_path.stat().st_size} bytes")
    
    try:
        # Extract W-3A from PDF (real API call to OpenAI)
        logger.info("🔄 Sending PDF to OpenAI...")
        w3a_data = extract_w3a_from_pdf(str(pdf_path))
        
        logger.info("✅ Extraction successful!")
        logger.info(f"   Response size: {len(json.dumps(w3a_data))} chars")
        
        # Pretty print the extracted JSON
        logger.info("\n📋 EXTRACTED W-3A DATA:\n")
        print(json.dumps(w3a_data, indent=2))
        
        # Validate structure
        logger.info("\n🔍 Validating structure...")
        _validate_w3a_structure(w3a_data)
        logger.info("✅ Structure validation passed!")
        
        # Summary of extracted data
        logger.info("\n📊 EXTRACTION SUMMARY:")
        if "header" in w3a_data:
            header = w3a_data["header"]
            logger.info(f"   API Number: {header.get('api_number')}")
            logger.info(f"   Well Name: {header.get('well_name')}")
            logger.info(f"   Operator: {header.get('operator')}")
            logger.info(f"   County: {header.get('county')}")
            logger.info(f"   RRC District: {header.get('rrc_district')}")
            logger.info(f"   Field: {header.get('field')}")
            logger.info(f"   Total Depth: {header.get('total_depth')} ft")
        
        if "casing_record" in w3a_data:
            casings = w3a_data["casing_record"]
            logger.info(f"   Casing Strings: {len(casings)}")
            for cs in casings:
                logger.info(f"      - {cs.get('string_type', 'unknown').title()}: {cs.get('size_in')}\" @ {cs.get('top_ft')}-{cs.get('bottom_ft')} ft")
        
        if "perforations" in w3a_data:
            perfs = w3a_data["perforations"]
            logger.info(f"   Perforations: {len(perfs)}")
            for perf in perfs:
                logger.info(f"      - {perf.get('interval_top_ft')}-{perf.get('interval_bottom_ft')} ft ({perf.get('formation')}, {perf.get('status')})")
        
        if "plugging_proposal" in w3a_data:
            plugs = w3a_data["plugging_proposal"]
            logger.info(f"   Plugging Proposal: {len(plugs)} plugs")
            for plug in plugs[:5]:  # Show first 5
                logger.info(f"      - Plug {plug.get('plug_number')}: {plug.get('depth_top_ft')}-{plug.get('depth_bottom_ft')} ft ({plug.get('type')})")
            if len(plugs) > 5:
                logger.info(f"      ... and {len(plugs) - 5} more plugs")
        
        if "duqw" in w3a_data:
            duqw = w3a_data["duqw"]
            logger.info(f"   DUQW: {duqw.get('depth_ft')} ft ({duqw.get('formation')})")
        
        logger.info("\n✅ REAL EXTRACTION TEST PASSED!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Extraction failed: {e}", exc_info=True)
        return False


def save_extracted_json(output_path: str = None):
    """
    Extract W-3A and save JSON to file for inspection.
    
    Args:
        output_path: Path to save JSON (default: tmp/w3a_extracted.json)
    """
    if output_path is None:
        output_path = Path(__file__).parent.parent.parent.parent.parent / "tmp" / "w3a_extracted.json"
    
    pdf_path = Path(__file__).parent.parent.parent.parent.parent / "tmp" / "W3A_Examples" / "Approved_W3A_00346118_20250826_214942_.pdf"
    
    if not pdf_path.exists():
        logger.error(f"❌ PDF not found: {pdf_path}")
        return False
    
    logger.info(f"🔄 Extracting and saving to {output_path}...")
    
    try:
        w3a_data = extract_w3a_from_pdf(str(pdf_path))
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            json.dump(w3a_data, f, indent=2)
        
        logger.info(f"✅ Saved to: {output_path}")
        logger.info(f"   Size: {output_path.stat().st_size} bytes")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to save: {e}", exc_info=True)
        return False


# Always run (works with both direct execution and manage.py shell)
logger.info("🚀 Starting real W-3A extraction test...")
logger.info("=" * 80)

# Run extraction test
success = test_real_w3a_extraction()

logger.info("\n" + "=" * 80)

# Optionally save extracted JSON
if success:
    logger.info("\n💾 Saving extracted JSON...")
    save_extracted_json()

if __name__ == "__main__":
    exit(0 if success else 1)

