"""
NM Extraction Mapper

Converts NMWellData from the scraper to the extraction format expected by
the UI and plan generation flow (matching W-2/W-15 structure).

This allows NM wells to use the same segmented W-3A flow as TX wells,
with scraped data filling in extraction fields instead of PDF extraction.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from apps.public_core.services.nm_well_scraper import NMWellData

logger = logging.getLogger(__name__)


def _collect_perforations(well_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect all perforation intervals from completions."""
    perforations = []
    for completion in well_data.get("completions", []):
        for perf in completion.get("perforations", []):
            perforations.append({
                "top_md": perf.get("top_md_ft"),
                "bottom_md": perf.get("bottom_md_ft"),
                "top_vd": perf.get("top_vd_ft"),
                "bottom_vd": perf.get("bottom_vd_ft"),
                "completion_name": completion.get("completion_name"),
            })
    return perforations


def map_nm_well_to_extractions(well_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map NM scraped data to extraction-like format for UI.

    This creates a structure similar to what OpenAI extraction returns for TX wells,
    allowing the same UI components to work for both states.

    Args:
        well_data: Dictionary from NMWellData.to_dict()

    Returns:
        Dict mimicking extracted W-2 structure with available NM fields.
        Unavailable fields (like casing_record) are empty arrays.
    """
    # Extract lease and well number from well name
    lease_name, well_number = _parse_well_name(well_data.get("well_name", ""))

    # Extract county from surface location
    county = _extract_county(well_data.get("surface_location", ""))

    return {
        "c105": {  # NM equivalent of W-2 (C-105 is NM completion report)
            "header": {
                "api": well_data.get("api10") or well_data.get("api14"),
                "api14": well_data.get("api14"),
                "api10": well_data.get("api10"),
                "source": "NM OCD Scraper",
            },
            "operator_info": {
                "name": well_data.get("operator_name", ""),
                "operator_number": well_data.get("operator_number", ""),
            },
            "well_info": {
                "api": well_data.get("api10"),
                "api14": well_data.get("api14"),
                "county": county or "",
                "field": well_data.get("formation", ""),  # NM uses formation for field
                "lease": lease_name,
                "well_no": well_number,
                "well_name": well_data.get("well_name", ""),
                "well_type": well_data.get("well_type", ""),
                "direction": well_data.get("direction", ""),
                "status": well_data.get("status", ""),
                "location": {
                    "lat": well_data.get("latitude"),
                    "lon": well_data.get("longitude"),
                    "surface_location": well_data.get("surface_location", ""),
                },
                "elevation_ft": well_data.get("elevation_ft"),
            },
            "depths": {
                "proposed_depth": well_data.get("proposed_depth_ft"),
                "tvd": well_data.get("tvd_ft"),
            },
            "formation": well_data.get("formation", ""),
            "dates": {
                "spud_date": well_data.get("spud_date"),
                "completion_date": well_data.get("completion_date"),
            },
            # Casing data from scraper (if available)
            "casing_record": [
                {
                    "casing_type": rec.get("string_type", ""),
                    "diameter": rec.get("diameter_in"),
                    "top": rec.get("top_ft"),
                    "bottom": rec.get("bottom_ft"),
                    "cement_top": rec.get("cement_top_ft"),
                    "cement_bottom": rec.get("cement_bottom_ft"),
                    "sacks": rec.get("cement_sacks"),
                    "grade": rec.get("grade"),
                    "weight": rec.get("weight_ppf"),
                }
                for rec in well_data.get("casing_records", [])
            ],
            # Perforation data from completions
            "producing_injection_disposal_interval": _collect_perforations(well_data),
            # No mechanical equipment data available from scraper
            "mechanical_equipment": [],
            # No formation record available from scraper
            "formation_record": [],
            # Completions from scraper
            "completions": [
                {
                    "id": c.get("completion_id"),
                    "name": c.get("completion_name"),
                    "status": c.get("status"),
                    "production_method": c.get("production_method"),
                    "perforations": c.get("perforations", []),
                }
                for c in well_data.get("completions", [])
            ],
            # Metadata for tracking
            "_metadata": {
                "source_type": "nm_ocd_scraper",
                "extraction_method": "web_scrape",
                "requires_manual_casing_entry": len(well_data.get("casing_records", [])) == 0,
                "has_scraped_casing": len(well_data.get("casing_records", [])) > 0,
                "has_scraped_perforations": any(c.get("perforations") for c in well_data.get("completions", [])),
                "combined_pdf_available": True,
            },
        }
    }


def map_nm_well_to_geometry(well_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map NM scraped data to geometry format for the geometry confirmation stage.

    Uses casing and perforation data from the scraper when available.

    Args:
        well_data: Dictionary from NMWellData.to_dict()

    Returns:
        Dict with geometry structure populated from scraped data.
    """
    casing_records = well_data.get("casing_records", [])
    perforations = _collect_perforations(well_data)

    return {
        "casing_strings": [
            {
                "type": rec.get("string_type"),
                "od_in": rec.get("diameter_in"),
                "top_ft": rec.get("top_ft"),
                "bottom_ft": rec.get("bottom_ft"),
                "cement_top_ft": rec.get("cement_top_ft"),
                "cement_bottom_ft": rec.get("cement_bottom_ft"),
            }
            for rec in casing_records
        ],
        "formation_tops": [],  # Will be populated from policy if available
        "perforations": perforations,
        "mechanical_barriers": [],  # Must be entered manually
        "uqw_data": None,  # NM doesn't have equivalent of TX GAU
        "kop_data": None,  # Can be entered manually if horizontal well
        "_nm_metadata": {
            "well_type": well_data.get("well_type", ""),
            "direction": well_data.get("direction", ""),
            "tvd_ft": well_data.get("tvd_ft"),
            "proposed_depth_ft": well_data.get("proposed_depth_ft"),
            "requires_manual_entry": len(casing_records) == 0 and len(perforations) == 0,
            "has_scraped_casing": len(casing_records) > 0,
            "has_scraped_perforations": len(perforations) > 0,
        },
    }


def create_nm_extracted_document_data(
    well_data: Dict[str, Any],
    documents: List[Dict[str, Any]] = None,
    combined_pdf_url: str = None,
) -> Dict[str, Any]:
    """
    Create data structure for creating ExtractedDocument for NM well.

    This creates a pseudo-extraction that can be stored in ExtractedDocument
    and used by the existing plan generation flow.

    Args:
        well_data: Dictionary from NMWellData.to_dict()
        documents: List of NMDocument dicts from document fetcher
        combined_pdf_url: URL to combined PDF on NM OCD portal

    Returns:
        Dict suitable for creating ExtractedDocument.
    """
    extraction = map_nm_well_to_extractions(well_data)

    return {
        "document_type": "c105",  # NM completion report type
        "source_path": f"nm_ocd_scrape:{well_data.get('api10', 'unknown')}",
        "model_tag": "nm_ocd_scraper_v1",
        "status": "success",
        "errors": [],
        "json_data": extraction["c105"],
        "source_type": "nm_scraper",  # For ExtractedDocument.source_type field
        "_nm_documents": documents or [],
        "_nm_combined_pdf_url": combined_pdf_url,
    }


def _parse_well_name(well_name: str) -> tuple[str, str]:
    """
    Parse NM well name to extract lease name and well number.

    NM well names are often like:
    - "STATE FEDERAL 1"
    - "FEDERAL 1-30H"
    - "BLM 1-30H #1"
    - "JOHN DOE A 1H"

    Args:
        well_name: Full well name string

    Returns:
        Tuple of (lease_name, well_number)
    """
    if not well_name:
        return "", ""

    tokens = well_name.strip().split()
    if not tokens:
        return "", ""

    # Check if last token looks like a well number (contains digit)
    if any(char.isdigit() for char in tokens[-1]):
        well_number = tokens[-1]
        lease_name = " ".join(tokens[:-1])
    else:
        lease_name = well_name
        well_number = ""

    return lease_name, well_number


def _extract_county(surface_location: str) -> str:
    """
    Extract county from NM surface location string.

    NM surface locations often include county:
    - "320 FNL, 660 FWL, SEC 30 T16S R33E, LEA COUNTY"
    - "LOT 1, SEC 1 T1N R1E, EDDY COUNTY, NM"

    Args:
        surface_location: Surface location string

    Returns:
        County name (without "COUNTY" suffix) or empty string
    """
    if not surface_location:
        return ""

    location_upper = surface_location.upper()

    # Look for "COUNTY" in surface location
    if "COUNTY" in location_upper:
        # Extract county name before "COUNTY"
        parts = location_upper.split("COUNTY")
        if parts:
            county_part = parts[0].strip()
            # Get last word/phrase before COUNTY (often after a comma)
            county_tokens = county_part.split(",")
            if county_tokens:
                county = county_tokens[-1].strip()
                # Clean up common artifacts
                county = re.sub(r'\s+', ' ', county)
                return county.title()  # Return as Title Case

    return ""


def get_nm_jurisdiction_policy_id(county: str = None) -> str:
    """
    Get the policy ID for NM jurisdiction.

    Currently returns a single NM policy, but could be extended
    to support county/district-specific policies in the future.

    Args:
        county: Optional county name (for future use)

    Returns:
        Policy ID string
    """
    # For now, return a generic NM policy ID
    # This will need to be created in the policy system
    return "nm.plugging"


def format_nm_api_for_display(api: str) -> str:
    """
    Format NM API number for consistent display.

    Converts any format to standard NM format: 30-XXX-XXXXX

    Args:
        api: API number in any format

    Returns:
        Formatted API string
    """
    digits = re.sub(r'[^0-9]', '', str(api or ""))

    # Handle API-14 (14 digits)
    if len(digits) == 14:
        digits = digits[:10]

    if len(digits) != 10:
        return api  # Return original if can't parse

    return f"{digits[:2]}-{digits[2:5]}-{digits[5:10]}"
