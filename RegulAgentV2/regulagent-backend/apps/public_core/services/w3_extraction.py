"""
W-3 Form Extraction Service

Handles extraction of W-3A form data from PDF files using OpenAI.
Reuses existing extract_json_from_pdf() pattern from openai_extraction.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional
import logging

from apps.public_core.services.openai_extraction import extract_json_from_pdf

logger = logging.getLogger(__name__)


def extract_w3a_from_pdf(pdf_path: str) -> Dict[str, Any]:
           """
           Extract W-3A form data from PDF using OpenAI.
           
           Sends PDF to OpenAI with structured extraction prompt.
           Returns normalized JSON with all required W-3A sections.
           
           Args:
               pdf_path: Path to W-3A PDF file
               
           Returns:
               Dictionary with structure:
               {
                   "header": {
                       "api_number": "42-501-70575",
                       "well_name": "...",
                       "operator": "...",
                       "rrc_district": "08A",
                       "county": "ANDREWS",
                       "field": "...",
                       ...
                   },
                   "casing_record": [
                       {
                           "string_type": "surface|intermediate|production|liner",
                           "size_in": 13.375,
                           "weight_ppf": 47.0,
                           "top_ft": 0,
                           "bottom_ft": 2000,
                           "shoe_depth_ft": 2000,
                           "cement_top_ft": 0,
                           "removed_to_depth_ft": None
                       },
                       ...
                   ],
                   "perforations": [
                       {
                           "interval_top_ft": 5000,
                           "interval_bottom_ft": 5100,
                           "formation": "Spraberry",
                           "status": "open|perforated|squeezed|plugged",
                           "perforation_date": "2020-01-15" or None
                       },
                       ...
                   ],
                   "plugging_proposal": [
                       {
                           "plug_number": 1,
                           "depth_top_ft": 5100,
                           "depth_bottom_ft": 5000,
                           "type": "cement_plug",
                           "cement_class": "C",
                           "sacks": 40,
                           "remarks": "..."
                       },
                       ...
                   ],
                   "operational_steps": [
                       {
                           "step_order": 1,
                           "step_type": "tag_toc|perforate_and_squeeze|perforate_and_circulate|wait_on_cement|tag_top_of_plug",
                           "plug_number": 1,
                           "depth_ft": null,
                           "wait_hours": null,
                           "description": "Tag top of plug"
                       },
                       ...
                   ],
                   "duqw": {
                       "depth_ft": 3250,
                       "formation": "Santa Rosa",
                       "determination_method": "GAU letter"
                   },
                   "remarks": "..."
               }
               
           Raises:
               ValueError: If PDF extraction fails or returned data is invalid
           """
           try:
               logger.info(f"Extracting W-3A from PDF: {pdf_path}")
               
               # Call existing extract_json_from_pdf with doc_type="w3a"
               result = extract_json_from_pdf(Path(pdf_path), doc_type="w3a")
               
               if result.errors:
                   logger.warning(f"W-3A extraction warnings: {result.errors}")
               
               # Validate that we got the expected structure
               w3a_data = result.json_data or {}
               _validate_w3a_structure(w3a_data)
               
               logger.info("✅ W-3A extraction successful")
               return w3a_data
               
           except Exception as e:
               logger.error(f"❌ W-3A extraction failed: {e}", exc_info=True)
               raise ValueError(f"Failed to extract W-3A from PDF: {e}")


def _validate_w3a_structure(w3a_data: Dict[str, Any]) -> None:
    """
    Validate that extracted W-3A has required top-level sections.
    
    Raises:
        ValueError: If required sections are missing
    """
    required_sections = ["header", "casing_record", "perforations", "plugging_proposal", "operational_steps", "duqw"]
    missing = [s for s in required_sections if s not in w3a_data]
    
    if missing:
        raise ValueError(f"W-3A missing required sections: {missing}")
    
    # Validate header has at least API number
    header = w3a_data.get("header", {})
    if not header.get("api_number"):
        raise ValueError("W-3A header missing api_number")
    
    # Validate operational_steps is a list
    op_steps = w3a_data.get("operational_steps", [])
    if not isinstance(op_steps, list):
        raise ValueError("operational_steps must be a list")
    
    logger.debug(f"✅ W-3A structure validated: {header.get('api_number')}")
    logger.debug(f"   - {len(w3a_data.get('casing_record', []))} casing strings")
    logger.debug(f"   - {len(w3a_data.get('perforations', []))} perforations")
    logger.debug(f"   - {len(w3a_data.get('plugging_proposal', []))} plugs")
    logger.debug(f"   - {len(op_steps)} operational steps")


def load_w3a_form(w3a_reference: Dict[str, Any], request=None) -> Dict[str, Any]:
    """
    Load W-3A form from either RegulAgent database or PDF extraction.
    
    Args:
        w3a_reference: Dictionary with:
            {
                "type": "regulagent" | "pdf",
                "w3a_id": int (if type="regulagent"),
                "w3a_file": UploadedFile (if type="pdf")
            }
        request: HTTP request object (for accessing FILES if needed)
        
    Returns:
        W-3A form dictionary with all required sections
        
    Raises:
        ValueError: If reference type is invalid or loading fails
    """
    ref_type = w3a_reference.get("type")
    
    if ref_type == "regulagent":
        return _load_w3a_from_db(w3a_reference)
    
    elif ref_type == "pdf":
        if request is None:
            raise ValueError("request object required for PDF extraction")
        return _load_w3a_from_pdf_upload(w3a_reference, request)
    
    else:
        raise ValueError(f"Unknown w3a_reference type: {ref_type}")


def _load_w3a_from_db(w3a_reference: Dict[str, Any]) -> Dict[str, Any]:
    """
    Load W-3A form from RegulAgent database.
    
    Args:
        w3a_reference: {"type": "regulagent", "w3a_id": int}
        
    Returns:
        W-3A form dictionary
        
    Note:
        TBD: Identify which model stores W-3A forms.
        Could be:
        - ExtractedDocument (if parsed from uploaded W-3A PDF)
        - PlanSnapshot (if storing baseline plans)
        - Separate W3AForm model
    """
    w3a_id = w3a_reference.get("w3a_id")
    
    if not w3a_id:
        raise ValueError("w3a_reference missing w3a_id for regulagent type")
    
    # TODO: Query database for W-3A form
    # Example:
    #   from apps.public_core.models import ExtractedDocument
    #   doc = ExtractedDocument.objects.get(id=w3a_id, document_type="w3a")
    #   return doc.json_data
    
    raise NotImplementedError("W-3A database loading - TBD which model stores W-3A forms")


def _load_w3a_from_pdf_upload(w3a_reference: Dict[str, Any], request) -> Dict[str, Any]:
    """
    Load W-3A from uploaded PDF file (multipart) or base64-encoded PDF (JSON).
    
    Args:
        w3a_reference: 
            - Multipart: {"type": "pdf", "w3a_file": UploadedFile}
            - JSON: {"type": "pdf", "w3a_file_base64": "base64string", "w3a_filename": "..."}
        request: HTTP request object
        
    Returns:
        W-3A form dictionary
    """
    import tempfile
    import os
    import base64
    
    # Try base64 first (JSON request)
    w3a_file_base64 = w3a_reference.get("w3a_file_base64")
    if w3a_file_base64:
        logger.info(f"📄 Processing base64-encoded PDF from JSON request...")
        try:
            pdf_content = base64.b64decode(w3a_file_base64)
            logger.info(f"   Decoded {len(pdf_content)} bytes from base64")
        except Exception as e:
            raise ValueError(f"Failed to decode base64 PDF: {e}")
    else:
        # Fall back to multipart upload
        logger.info(f"📄 Processing PDF file from multipart upload...")
        w3a_file = request.FILES.get("w3a_file") if request else None
        
        if not w3a_file:
            raise ValueError("w3a_file not provided in request.FILES or w3a_file_base64 in JSON")
        
        # Read uploaded file into memory
        pdf_content = b""
        for chunk in w3a_file.chunks():
            pdf_content += chunk
        logger.info(f"   Read {len(pdf_content)} bytes from uploaded file")
    
    # Save to temporary location for extraction
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_content)
        tmp_path = tmp.name
    
    try:
        # Extract W-3A from PDF
        w3a_form = extract_w3a_from_pdf(tmp_path)
        logger.info(f"✅ W-3A extracted from PDF")
        return w3a_form
        
    finally:
        # Clean up temporary file
        try:
            os.unlink(tmp_path)
        except Exception as e:
            logger.warning(f"Failed to delete temp file {tmp_path}: {e}")


def get_w3a_geometry_from_database(api_number: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve well geometry (casing, formations, perforations, duqw, etc.) 
    from the most recent W-3A plan snapshot for the given API number.
    
    This is used by the W3 builder to populate w3a_well_geometry in the response
    when a W-3A plan already exists in the database.
    
    Args:
        api_number: Normalized or unnormalized API number (e.g., "42-003-01016" or "42003001")
        
    Returns:
        Dictionary with well geometry including:
        {
            "casing_record": [...],
            "formation_tops": [...],
            "perforations": [...],
            "duqw": {...},
            "plugging_proposal": [...],
            "operational_steps": [...]
        }
        Or None if no plan found.
    """
    try:
        from apps.public_core.models import WellRegistry, PlanSnapshot
        from apps.public_core.services.w3_utils import normalize_api_number
        
        logger.info("=" * 80)
        logger.info("🔍 GET_W3A_GEOMETRY_FROM_DATABASE - Starting")
        logger.info("=" * 80)
        logger.info(f"📥 Input API number: {api_number}")
        
        # Normalize API number
        normalized_api = normalize_api_number(api_number) if api_number else None
        if not normalized_api:
            logger.warning(f"❌ Could not normalize API number: {api_number}")
            return None
        
        logger.info(f"✅ Normalized API: {normalized_api}")
        
        # Find well by API number (use last 8 digits for matching)
        logger.info(f"🔎 Searching for well by API (last 8 digits: {normalized_api[-8:]})")
        well = WellRegistry.objects.filter(
            api14__icontains=normalized_api[-8:]
        ).first()
        
        if not well:
            logger.warning(f"❌ No well found in WellRegistry for API {api_number}")
            return None
        
        logger.info(f"✅ Found well: {well.api14} ({well.operator_name or 'Unknown operator'}) - {well.well_number}")
        
        # Find most recent baseline W-3A plan snapshot
        logger.info(f"🔎 Searching for baseline PlanSnapshot for well {well.id}")
        snapshot = (
            PlanSnapshot.objects
            .filter(well=well, kind=PlanSnapshot.KIND_BASELINE)
            .order_by('-created_at')
            .first()
        )
        
        if not snapshot:
            logger.warning(f"❌ No W-3A baseline plan snapshot found for well {well.api14}")
            logger.info("   (This means W-3A has never been generated for this well)")
            return None
        
        logger.info(f"✅ Found W-3A plan snapshot:")
        logger.info(f"   - Plan ID: {snapshot.plan_id}")
        logger.info(f"   - Kind: {snapshot.kind}")
        logger.info(f"   - Status: {snapshot.status}")
        logger.info(f"   - Created: {snapshot.created_at}")
        logger.info(f"   - Payload size: {len(str(snapshot.payload))} bytes")
        
        # Extract geometry from payload
        payload = snapshot.payload or {}
        
        logger.info(f"🔍 Extracting geometry from payload...")
        
        # Build geometry response with all relevant well data
        casing_record = payload.get("casing_record", [])
        formation_tops = payload.get("formation_tops", []) or payload.get("header", {}).get("formation_record", [])
        perforations = payload.get("perforations", [])
        duqw = payload.get("duqw", {})
        plugging_proposal = payload.get("plugging_proposal", [])
        operational_steps = payload.get("operational_steps", [])
        remarks = payload.get("remarks", "")
        
        logger.info(f"   ✅ Casing record: {len(casing_record)} strings")
        for i, casing in enumerate(casing_record[:3]):
            string_type = casing.get("string_type", "unknown")
            size = casing.get("size_in", "?")
            top = casing.get("top_ft", "?")
            bottom = casing.get("bottom_ft", "?")
            logger.debug(f"      [{i}] {string_type}: {size}\" @ {top}-{bottom} ft")
        if len(casing_record) > 3:
            logger.debug(f"      ... and {len(casing_record) - 3} more")
        
        logger.info(f"   ✅ Formation tops: {len(formation_tops)} entries")
        for i, formation in enumerate(formation_tops[:3]):
            name = formation.get("name") or formation.get("formation", "unknown")
            depth = formation.get("depth_ft") or formation.get("top_ft", "?")
            logger.debug(f"      [{i}] {name} @ {depth} ft")
        if len(formation_tops) > 3:
            logger.debug(f"      ... and {len(formation_tops) - 3} more")
        
        logger.info(f"   ✅ Perforations: {len(perforations)} intervals")
        logger.info(f"   ✅ DUQW: {duqw.get('formation', 'unknown') if duqw else 'None'} @ {duqw.get('depth_ft', '?')} ft")
        logger.info(f"   ✅ Plugging proposal: {len(plugging_proposal)} plugs")
        logger.info(f"   ✅ Operational steps: {len(operational_steps)} steps")
        logger.info(f"   ✅ Remarks: {len(remarks)} characters")
        
        geometry = {
            "casing_record": casing_record,
            "formation_tops": formation_tops,
            "perforations": perforations,
            "duqw": duqw,
            "plugging_proposal": plugging_proposal,
            "operational_steps": operational_steps,
            "remarks": remarks,
        }
        
        logger.info("=" * 80)
        logger.info("✅ GET_W3A_GEOMETRY_FROM_DATABASE - SUCCESS")
        logger.info("=" * 80)
        
        return geometry
    
    except Exception as e:
        logger.error(f"❌ Exception in get_w3a_geometry_from_database: {e}", exc_info=True)
        logger.warning(f"❌ Failed to retrieve W-3A geometry from database")
        return None

