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
    Load W-3A from uploaded PDF file.
    
    Args:
        w3a_reference: {"type": "pdf"}
        request: HTTP request with FILES
        
    Returns:
        W-3A form dictionary
    """
    w3a_file = request.FILES.get("w3a_file")
    
    if not w3a_file:
        raise ValueError("w3a_file not provided in request.FILES")
    
    # Save to temporary location for extraction
    import tempfile
    import os
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        # Write uploaded file to temp
        for chunk in w3a_file.chunks():
            tmp.write(chunk)
        tmp_path = tmp.name
    
    try:
        # Extract W-3A from PDF
        w3a_form = extract_w3a_from_pdf(tmp_path)
        logger.info(f"✅ W-3A extracted from uploaded PDF")
        return w3a_form
        
    finally:
        # Clean up temporary file
        try:
            os.unlink(tmp_path)
        except Exception as e:
            logger.warning(f"Failed to delete temp file {tmp_path}: {e}")

