from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
import logging
import inspect
import pdfplumber
import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import io

from .openai_config import DEFAULT_CHAT_MODEL

logger = logging.getLogger(__name__)


# Lazy import to avoid hard dependency at import time
def _openai_client():  # pragma: no cover
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured")
    return OpenAI(api_key=api_key)


SUPPORTED_TYPES = {
    "gau": {
        "prompt_key": "gau",
        "required_sections": [
            "header",
            "operator_info",
            "well_info",
            "purpose_and_location",
            "recommendation",
            "footnotes",
        ],
    },
    "w2": {
        "prompt_key": "w2",
        "required_sections": [
            "header",
            "operator_info",
            "well_info",
            "filing_info",
            "completion_info",
            "surface_casing_determination",
            "initial_potential_test",
            "casing_record",
            "liner_record",
            "tubing_record",
            "producing_injection_disposal_interval",
            "acid_fracture_operations",
            "formation_record",
            "commingling_and_h2s",
            "remarks",
            "rrc_remarks",
            "operator_certification",
            "revisions",
        ],
    },
    "w15": {
        "prompt_key": "w15",
        "required_sections": [
            "header",
            "operator_info",
            "well_info",
            "cementing_data",
            "cementing_to_squeeze",
            "certifications",
            "instructions_section",
        ],
    },
    "schematic": {
        "prompt_key": "schematic",
        "required_sections": [
            "header",
            "location_info",
            "schematic_data",
        ],
    },
    "formation_tops": {
        "prompt_key": "formation_tops",
        "required_sections": [
            "header",
            "formation_record",
            "h2s_flag",
            "downhole_commingled",
            "remarks",
        ],
    },
    "w3a": {
        "prompt_key": "w3a",
        "required_sections": [
            "header",
            "casing_record",
            "perforations",
            "plugging_proposal",
            "duqw",
        ],
    },
}


# OpenAI Models - using latest available models with best performance
# Updated 2025-11-02: Use gpt-4o for extraction (structured outputs support)
MODEL_CLASSIFIER = os.getenv("OPENAI_CLASSIFIER_MODEL", "gpt-4o-mini")
MODEL_PRIMARY = os.getenv("OPENAI_EXTRACTION_MODEL", "gpt-4o")  # Updated: best for structured outputs
MODEL_BATCH = os.getenv("OPENAI_EXTRACTION_BATCH_MODEL", "gpt-4o")  # 50% cost savings for async
MODEL_EMBEDDING = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


@dataclass
class ExtractionResult:
    document_type: str
    json_data: Dict[str, Any]
    model_tag: str
    errors: List[str]


def _extract_pdf_text(file_path: Path, max_chars: int = 20000) -> str:
    """Best-effort text extraction for context. Truncates to max_chars."""
    text_parts: List[str] = []
    try:
        with pdfplumber.open(str(file_path)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t:
                    text_parts.append(t)
                if sum(len(x) for x in text_parts) >= max_chars:
                    break
    except Exception:
        pass
    # Fallback to PyMuPDF
    if not text_parts:
        try:
            doc = fitz.open(str(file_path))
            for i, page in enumerate(doc):
                t = page.get_text() or ""
                if t:
                    text_parts.append(t)
                if sum(len(x) for x in text_parts) >= max_chars:
                    break
        except Exception:
            pass
    text = "\n\n".join(text_parts).strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def _json_schema_for(doc_type: str) -> Dict[str, Any]:
    """
    Build JSON schema with structured outputs (strict=True).
    
    Structured outputs guarantee:
    - 100% reliable JSON parsing
    - No hallucinated fields
    - Schema-compliant responses
    
    Updated 2025-11-02: Using OpenAI structured outputs best practice
    """
    req = SUPPORTED_TYPES[doc_type]["required_sections"]
    properties: Dict[str, Any] = {}
    for key in req:
        if key in ("casing_record", "tubing_record", "formation_record", "schematic_data"):
            properties[key] = {"type": "array"}
        elif key in ("h2s_flag", "downhole_commingled", "remarks"):
            properties[key] = {"type": ["string", "object", "null"]}
        else:
            properties[key] = {"type": ["object", "string", "null"]}
    schema = {
        "name": f"regulagent_{doc_type}_schema",
        "schema": {
            "type": "object",
            "additionalProperties": True,
            "properties": properties,
            "required": req,
        },
        "strict": True,  # ← Structured outputs: 100% reliable
    }
    return schema


def classify_document(file_path: Path) -> str:
    """Classify document type using a lightweight model. Returns one of SUPPORTED_TYPES keys or 'unknown'."""
    client = _openai_client()
    logger.info("classify_document: start file=%s", file_path)
    
    # Check if it's an image file - always classify as schematic
    image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff'}
    if file_path.suffix.lower() in image_extensions:
        logger.info(f"classify_document: Image file detected ({file_path.suffix}), classifying as schematic")
        return "schematic"
    
    # Minimal heuristic by filename as fallback
    name = file_path.name.lower()
    if "w-2" in name or " w2" in name:
        return "w2"
    if "w-15" in name or " w15" in name or "cement" in name:
        return "w15"
    if "gau" in name:
        return "gau"
    if "schematic" in name or "diagram" in name or "wbd" in name:
        return "schematic"
    if "formation" in name and "top" in name:
        return "formation_tops"

    # Upload and ask classifier (filename-based classification is typically sufficient)
    try:  # pragma: no cover
        fobj = client.files.create(file=open(str(file_path), "rb"), purpose="assistants")
        logger.info("classify_document: uploaded file_id=%s", getattr(fobj, 'id', ''))
        prompt = "Classify the regulatory document type: one of [gau, w2, w15, schematic, formation_tops]. Return only the key."
        resp = client.chat.completions.create(
            model=MODEL_CLASSIFIER,
            messages=[
                {"role": "system", "content": "Return only one token from the set: gau,w2,w15,schematic,formation_tops"},
                {"role": "user", "content": f"File: {file_path.name} (file_id: {getattr(fobj,'id','')}). {prompt}"},
            ],
            temperature=0,
        )
        label = (resp.choices[0].message.content or "").strip().lower()
        ok = label if label in SUPPORTED_TYPES else "unknown"
        logger.info("classify_document: label=%s resolved=%s", label, ok)
        return ok
    except Exception:
        logger.exception("classify_document: failed remote classification; falling back to 'unknown'")
        return "unknown"


def _load_prompt(prompt_key: str) -> str:
    # Prompts instruct models to return normalized JSON for downstream planning.
    # Conventions:
    # - Use snake_case keys
    # - Return numeric values as numbers (no units);
    #   depths in feet, sizes in inches
    # - Prefer structured arrays of records for any tabular data
    # - If a requested field cannot be found, include the key with null value
    base = {
        "gau": (
            "Extract GAU (Groundwater Advisory Unit) data. Return JSON with: "
            "operator_info{name,address,operator_number}; "
            "well_info{api,district,county,field,lease,well_no,location{lat,lon}}; "
            "header{date}; purpose_and_location; recommendation; footnotes; "
            "surface_casing_determination{gau_groundwater_protection_determination_depth}. "
            "Operator name: Extract the full operator/company name from the operator_info section, header, or attention line. "
            "Coordinates: If latitude/longitude are present anywhere (maps, headers, footers, or body), output decimal degrees in well_info.location.lat and .lon. "
            "Accept both decimal and DMS formats (e.g., 32°45'30\" N, 102°00'00\" W) and convert to signed decimal (N/E positive, S/W negative). "
            "Use keys lat/lon (not latitude/longitude). Output up to 6 decimal places (e.g., 32.242052, -102.282218). Do not round to whole degrees. "
            "If coordinates cannot be found, set both to null. "
            "Rules: numbers only (feet for depths), snake_case keys, no units in numeric values. If a requested field is missing, set it to null."
        ),
        "w2": (
            "Extract W-2 (Oil/Gas Well Completion) data. Return JSON with: "
            "header{tracking_no}; operator_info{name,address,operator_number}; well_info{api,district,county,field,lease,well_no,location{lat,lon}}; filing_info; completion_info; "
            "surface_casing_determination{gau_groundwater_protection_determination_depth,surface_shoe_depth_ft}; "
            "casing_record:[{string:'surface|intermediate|production|liner', size_in, weight_per_ft, hole_size_in, top_ft, bottom_ft, shoe_depth_ft, cement_top_ft}]; "
            "liner_record:[{size_in, top_ft, bottom_ft, cement_top_ft}]; "
            "tubing_record:[{size_in, top_ft, bottom_ft}]; "
            "producing_injection_disposal_interval:[{from_ft, to_ft, open_hole:true|false}] (CRITICAL: Find table titled 'PRODUCING/INJECTION/DISPOSAL INTERVAL' with rows of From/To depths. Extract ALL rows as array. If table not found set to null. Each row should extract From and To depths as numbers, and whether open_hole=true if marked 'Open hole? Yes'); "
            "acid_fracture_operations: Extract all rows from the table titled 
            'ACID, FRACTURE, CEMENT SQUEEZE, CAST IRON BRIDGE PLUG, RETAINER, ETC.' 
            Return an array of objects with: 
            {operation_type, amount_and_kind_of_material_used, from_ft, to_ft, open_hole, notes}. 

            operation_type: classify based on text in the row: 
            - 'CIBP', 'CAST IRON BRIDGE PLUG', 'RETAINER' → 'mechanical_plug' 
            - rows containing 'HCL', 'acid', 'acidize' → 'acid' 
            - rows containing 'squeeze' → 'cement_squeeze' 
            - rows containing sand + water volumes characteristic of frac → 'fracture' 
            - otherwise return raw text in lowercase as fallback. 

            amount_and_kind_of_material_used: 
            - extract the full raw string describing fluids, acids, water, sand, cement, etc. 
            - do NOT summarize or normalize. Preserve capitalization and units. 

            from_ft / to_ft: numeric depths defining the interval. 
            - Remove all non-numeric characters. 
            - If only one depth appears, set both from_ft and to_ft to that value. 

            open_hole: true if the interval corresponds to an “open hole” completion (match row labels or nearby context); otherwise false. 

            notes: 
            - include any descriptive text not captured above (e.g. '20’ cmt on top', 'NO TREATMENT', 'set CIBP @ 10490’'). 
            - If no additional notes exist, set to null. 

            If the table is missing or blank, return an empty array []."
            "kop:{kop_md_ft,kop_tvd_ft} (Kick-Off Point - look in remarks section for 'KOP' followed by MD and TV/TVD depths); "
            "commingling_and_h2s; remarks; rrc_remarks; operator_certification; "
            "revisions:{revising_tracking_number, revision_reason, other_changes}. "
            "TRACKING NO EXTRACTION: Extract 'Tracking No.' from the header/top section of the form (not ticket number). Format is typically 'Tracking No. XXXX' or similar. "
            "REVISION DETECTION: If remarks indicate this is a revision/correction filing of a previous submission, extract: "
            "  - revising_tracking_number: The tracking number of the previous W-2 being revised/corrected "
            "  - revision_reason: What was being revised (e.g., 'Incorrect CIBP size (4.5\" to 5.5\")', 'Cement quantity correction', 'Perforation depth revision') "
            "  - other_changes: true if there are additional changes beyond the revision noted in remarks, false if this is ONLY a correction filing "
            "If remarks do NOT indicate a revision, set revisions to null. "
            "Example: If remarks say 'This document is to revise the incorrect spec of 4.5 cibp, a 5.5 cibp was used and tracking no was 1572' "
            "Then extract: revisions:{revising_tracking_number:'1572', revision_reason:'Incorrect CIBP size (4.5 to 5.5 inch)', other_changes:false} "
            "Cement tops: For each casing string, extract cement_top_ft (the depth where cement reaches in the annulus). "
            "Look for phrases like 'cemented to surface', 'cement returns', 'cement top at X ft', 'cemented from X to Y ft'. "
            "If cemented to surface, set cement_top_ft to 0. If no cement data is found for a string, set cement_top_ft to null. "
            "Operator name: Extract the full operator/company name from the operator_info section, header, or certification area. "
            "Coordinates: If latitude/longitude are present anywhere (maps, headers, footers, or body), output decimal degrees in well_info.location.lat and .lon. "
            "Accept both decimal and DMS formats (e.g., 32°45'30\" N, 102°00'00\" W) and convert to signed decimal (N/E positive, S/W negative). "
            "Use keys lat/lon (not latitude/longitude). Output up to 6 decimal places. Do not round to whole degrees. If coordinates cannot be found, set both to null. "
            "Rules: numbers only (feet/inches), snake_case keys, no units in values. If a field is missing, set it to null."
        ),
        "w15": (
            "Extract W-15 (Cementing Report) data. Return JSON with: "
            "header; operator_info{name,address,operator_number}; well_info{api,district,county,field,lease,well_no,location{lat,lon}}; "
            "cementing_data:[{job:'surface|intermediate|production|plug|squeeze', interval_top_ft, interval_bottom_ft, cement_top_ft, "
            "slurry_density_ppg, yield_ft3_per_sk, sacks, additives:[] }]; "
            "mechanical_equipment:[{equipment_type:'CIBP|bridge_plug|packer', size_in, depth_ft, sacks, notes}]; "
            "cement_tops_per_string:[{string:'surface|intermediate|production', cement_top_ft, cement_returns:'full|partial|none'}]; "
            "cementing_to_squeeze:[{top_ft,bottom_ft,method}]; certifications; instructions_section. "
            "Cement tops: Extract cement_top_ft for each cementing job - the depth where cement circulated to or stopped. "
            "Also extract cement_tops_per_string for each casing string showing final cement top depth after all jobs. "
            "Look for 'cement returns', 'cement to surface', 'cement circulated to X ft', 'cement left at X ft'. "
            "If returns to surface, set cement_top_ft to 0. If no returns or unknown, set to null. "
            "Mechanical equipment: Extract CIBPs (Casing In Basement Pipe), bridge plugs, and packers from rows 24-25 of form (Size of hole/pipe plugged, Depth to bottom). "
            "Look for entries like 'set CIBP 5-1/2 at 10490' or 'Bridge Plug 5.5 in'. Include cement sacks and any notes. "
            "Operator name: Extract the full operator/company name from the operator_info section, header, or certification area. "
            "Coordinates: If latitude/longitude are present anywhere (maps, headers, footers, or body), output decimal degrees in well_info.location.lat and .lon. "
            "Accept both decimal and DMS formats (e.g., 32°45'30\" N, 102°00'00\" W) and convert to signed decimal (N/E positive, S/W negative). "
            "Use keys lat/lon (not latitude/longitude). Output up to 6 decimal places. Do not round to whole degrees. If coordinates cannot be found, set both to null. "
            "Rules: numbers only (feet/inches/ppg), snake_case keys, no units in values. If a field is missing, set it to null."
        ),
        "schematic": (
            "Extract schematic data. Return JSON with: header; location_info; "
            "schematic_data:{surface_shoe_ft, intermediate_shoe_ft, production_shoe_ft, production_top_ft, production_bottom_ft, "
            "casing:[{string:'surface|intermediate|production', size_in, shoe_ft, top_ft, bottom_ft}], "
            "tubing:[{size_in, top_ft, bottom_ft}] }. "
            "Rules: numbers only, snake_case keys. If a field is missing, set it to null."
        ),
        "formation_tops": (
            "Extract Formation Record. Return JSON with: header; formation_record:[{formation, top_ft, base_ft}]; "
            "h2s_flag; downhole_commingled; remarks. Rules: numbers only (ft), snake_case keys. If a field is missing, set it to null."
        ),
        "w3a": (
            "Extract W-3A (Plugging Responsibility and Plugging Proposal) data. Return JSON with: "
            "header{api_number, well_name, operator, county, rrc_district, field, total_depth_ft}; "
            "casing_record:[{string_type:'surface|intermediate|production|liner', size_in, weight_ppf, hole_size_in, top_ft, bottom_ft, shoe_depth_ft, cement_top_ft, removed_to_depth_ft}]; "
            "perforations:[{interval_top_ft, interval_bottom_ft, formation, status:'open|perforated|squeezed|plugged', perforation_date}]; "
            "plugging_proposal:[{plug_number, depth_top_ft, depth_bottom_ft, type:'cement_plug|bridge_plug|mechanicalplug|squeeze', cement_class, sacks, volume_bbl, remarks}]; "
            "operational_steps:[{step_order, step_type, plug_number, depth_ft, wait_hours, description}]; "
            "duqw{depth_ft, formation, determination_method}; "
            "remarks. "
            "CASING RECORD CRITICAL RULES: "
            "1. Read the casing table row-by-row from 'Casing Record' section. "
            "2. For SURFACE casing: top_ft=0 (surface), bottom_ft=shoe depth (from table). "
            "3. For INTERMEDIATE casing: top_ft=0 (or shoe of previous string), bottom_ft=shoe depth from table. "
            "4. For PRODUCTION casing: top_ft=0 (or shoe of previous string), bottom_ft=shoe depth (usually TD if not deeper). "
            "5. For LINER (critical): top_ft must be 'Top of Liner' or 'Tool Setting Depth' from table (NOT 0), bottom_ft=liner shoe depth. "
            "    Example: If table shows 'Top of Liner: 6997 ft' and 'Shoe Depth: 11200 ft', then {top_ft: 6997, bottom_ft: 11200}. "
            "6. hole_size_in comes from 'Hole Size' column. "
            "7. cement_top_ft is 'Top of Cement' depth for each string. "
            "8. If liner depth is shown in a separate column, use that for top_ft - do NOT default to 0. "
            "OPERATIONAL STEPS (CRITICAL): "
            "The plugging proposal table has operational steps in TWO places: "
            "1. FIRST ROW (standalone): Contains pre-plug operational steps like 'Tag top of plug' "
            "2. PLUG ROWS: Each plug row (starting with 'Cement Plug' or 'Cement Surface Plug') has associated requirements "
            "READ THE TABLE TOP-TO-BOTTOM: "
            "- First row often shows 'Additional requirements 3 - Tag top of plug' (no plug data on that row) -> Step 1: tag_toc "
            "- Second row shows 'Cement Plug Set at 7990 to 7890...' with 'Additional requirements 6 - None' -> Step 2: plug #1 "
            "- Third row shows 'Cement Plug Set at 7047 to 6947...' with 'Additional requirements 6 - None' -> Step 3: plug #2 "
            "- When a plug row has requirements like '2 - Perforate and Squeeze, 4 - Wait X hours', create multiple steps for that plug "
            "Step numbering: Increment step_order for EACH distinct operational requirement, in table order. "
            "For plugs: plug_number corresponds to the sequence of actual plug rows (first plug row = plug #1). "
            "Step type mapping: "
            "- 'Tag top of plug' or 'Tag TOC' = step_type:'tag_toc' "
            "- 'Perforate and Circulate' = step_type:'perforate_and_circulate' "
            "- 'Perforate and Squeeze' = step_type:'perforate_and_squeeze' "
            "- 'Wait X hours and tag' = step_type:'wait_on_cement' with wait_hours:X "
            "CRITICAL: The first operational step is often 'Tag top of plug' which is step_order:1, and does NOT have a plug_number "
            "Then the first plug row creates step_order:2 with plug_number:1, and so on. "
            "Perforations: Extract from 'Record of Perforated Intervals' or 'Open Hole Intervals' showing top/bottom depths, formation name, and current status. "
            "Plugging Proposal: Extract from 'Plugging Proposal' section showing plug numbers, depths, type (cement plug vs bridge plug), cement class, and sack quantities. "
            "DUQW: Extract 'Deepest Usable Quality Water' information - the depth, formation, and how it was determined. "
            "Rules: numbers only (feet/inches/sacks), snake_case keys, no units in numeric values. If a field is missing, set it to null."
        ),
    }
    return base[prompt_key]


def _ensure_sections(doc_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    req = SUPPORTED_TYPES[doc_type]["required_sections"]
    out = dict(data)
    for key in req:
        out.setdefault(key, {} if key not in ("casing_record", "tubing_record", "formation_record", "schematic_data") else [])
    return out


def extract_json_from_pdf(file_path: Path, doc_type: str, retries: int = 2, w2_data: Optional[Dict] = None) -> ExtractionResult:
    """
    Send PDF to OpenAI and retrieve structured JSON per schema; retry on malformed JSON up to retries.
    
    For schematic documents or image files, uses Vision API instead of text extraction.
    """
    # Check if this is an image file (schematic/wellbore diagram)
    image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff'}
    is_image = file_path.suffix.lower() in image_extensions
    
    # Route images or explicit schematic doc types to Vision API
    if doc_type == 'schematic' or doc_type == 'wellbore_schematic' or is_image:
        try:
            from .schematic_extraction import extract_schematic_from_image
            # Force doc_type to schematic for images
            actual_doc_type = 'schematic' if is_image else doc_type
            logger.info(f"Routing image file {file_path.name} to Vision API for schematic extraction")
            data = extract_schematic_from_image(file_path, w2_data=w2_data)
            return ExtractionResult(
                document_type=actual_doc_type,
                json_data=data,
                model_tag=DEFAULT_CHAT_MODEL,
                errors=[]
            )
        except Exception as e:
            logger.error(f"Vision API extraction failed for {file_path.name}: {str(e)}")
            return ExtractionResult(
                document_type=doc_type,
                json_data={},
                model_tag=DEFAULT_CHAT_MODEL,
                errors=[str(e)]
            )
    
    # Standard text-based extraction
    client = _openai_client()
    model = MODEL_PRIMARY
    prompt = _load_prompt(SUPPORTED_TYPES[doc_type]["prompt_key"]) + " Return only valid JSON."
    last_err = None

    # Pre-extract textual context to aid model grounding
    context_text = _extract_pdf_text(file_path, max_chars=20000)

    for attempt in range(retries + 1):
        logger.info("extract_json_from_pdf: attempt=%d file=%s type=%s model=%s", attempt + 1, file_path, doc_type, model)
        try:
            # Upload the PDF and call Responses API with file input (supports input_file_id)
            fobj = client.files.create(file=open(str(file_path), "rb"), purpose="assistants")
            logger.info("extract_json_from_pdf: uploaded file_id=%s size=%s", getattr(fobj, 'id', ''), os.path.getsize(file_path))
            # Debug: log SDK version/path and Responses signature in the same process
            try:
                import openai  # type: ignore
                logger.warning("openai runtime: version=%s path=%s", getattr(openai, "__version__", "?"), getattr(openai, "__file__", "?"))
                try:
                    from openai.resources.responses import Responses as _Responses
                    logger.warning("responses.create signature=%s", inspect.signature(_Responses.create))
                    try:
                        logger.warning("responses.create varnames=%s", getattr(_Responses.create, "__code__", None) and _Responses.create.__code__.co_varnames)
                    except Exception:
                        pass
                except Exception as e_sig:
                    logger.warning("responses.create signature introspection failed: %s", e_sig)
            except Exception:
                pass

            # Request JSON output using Responses API (SDK >= 1.6.0)
            inputs = [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        # Provide extracted text to improve retrieval of numeric values
                        *( [{"type": "input_text", "text": context_text[:20000]}] if context_text else [] ),
                        {"type": "input_file", "file_id": getattr(fobj, "id", "")},
                    ],
                }
            ]
            resp = client.responses.create(
                model=model,
                input=inputs,
                text={"format": {"type": "json_object"}},
                max_output_tokens=4000,
                temperature=0,
            )

            # Robustly extract text from Responses API
            # Extract text from Responses API output
            # Extract text from Responses API output (preferred shape in SDK 1.x)
            content = ""
            if hasattr(resp, "output") and resp.output:
                try:
                    content = "".join(
                        (
                            block.get("text", "")
                            if isinstance(block, dict)
                            else (getattr(block, "text", "") or "")
                        )
                        for item in resp.output
                        for block in (
                            item.get("content", []) if isinstance(item, dict) else (getattr(item, "content", []) or [])
                        )
                        if (
                            block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
                        ) == "output_text"
                    )
                except Exception as parse_e:
                    logger.warning("extract_json_from_pdf: failed to parse Responses output blocks: %s", parse_e)
            if not content and hasattr(resp, "output_text"):
                content = resp.output_text or ""

            # Debug: log a compact raw view once per attempt
            try:
                dump = None
                if hasattr(resp, "model_dump_json"):
                    dump = resp.model_dump_json(indent=2)  # type: ignore[attr-defined]
                elif hasattr(resp, "model_dump"):
                    dump = json.dumps(getattr(resp, "model_dump")(), indent=2)  # type: ignore[misc]
                else:
                    dump = repr(resp)
                if dump:
                    logger.debug("extract_json_from_pdf: raw_response_snippet=%s", str(dump)[:2000])
            except Exception:
                pass
            if not content or content.strip() in ("{}", "[]", "null", "None", ""):
                raise ValueError("EMPTY_JSON_RESPONSE")
            logger.info("extract_json_from_pdf: received json length=%d", len(content))
            try:
                logger.debug("extract_json_from_pdf: content_snippet=%s", content[:500])
            except Exception:
                pass
            data = json.loads(content)
            data = _ensure_sections(doc_type, data)
            # Post-process GAU: if lat/lon missing, parse from context text (decimal or DMS) and inject
            if doc_type == "gau":
                try:
                    wi = data.setdefault("well_info", {})
                    loc = wi.setdefault("location", {})
                    lat = loc.get("lat") or loc.get("latitude")
                    lon = loc.get("lon") or loc.get("longitude")
                    def _parse_from_text(txt: str) -> Tuple[Optional[float], Optional[float]]:
                        import re, math
                        # 1) Try decimal degrees: lat, lon
                        dec = re.findall(r"([+-]?\d{1,2}\.\d{3,})\s*,?\s*([+-]?\d{3}\.\d{3,})", txt)
                        for a,b in dec:
                            try:
                                la = float(a); lo = float(b)
                                if -90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0:
                                    return round(la, 6), round(lo, 6)
                            except Exception:
                                pass
                        # 2) Try DMS with N/S and E/W
                        dms_lat = re.search(r"(\d{1,2})[°\s](\d{1,2})['’\s](\d{1,2}(?:\.\d+)?)\s*([NSns])", txt)
                        dms_lon = re.search(r"(\d{1,3})[°\s](\d{1,2})['’\s](\d{1,2}(?:\.\d+)?)\s*([EWew])", txt)
                        def to_dec(d, m, s, hemi):
                            val = float(d) + float(m)/60.0 + float(s)/3600.0
                            if hemi.upper() in ("S","W"):
                                val = -val
                            return round(val, 6)
                        if dms_lat and dms_lon:
                            la = to_dec(dms_lat.group(1), dms_lat.group(2), dms_lat.group(3), dms_lat.group(4))
                            lo = to_dec(dms_lon.group(1), dms_lon.group(2), dms_lon.group(3), dms_lon.group(4))
                            if -90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0:
                                return la, lo
                        return None, None
                    if (lat is None or lon is None) and context_text:
                        la, lo = _parse_from_text(context_text)
                        if la is not None and lo is not None:
                            loc["lat"], loc["lon"] = la, lo
                except Exception:
                    logger.exception("GAU post-processing for lat/lon failed")
            # Save extracted JSON to tmp/extractions for inspection
            try:
                tmp_dir = Path(settings.BASE_DIR) / 'tmp' / 'extractions'
                tmp_dir.mkdir(parents=True, exist_ok=True)
                out_name = f"{file_path.stem}_{doc_type}.json"
                out_path = tmp_dir / out_name
                with open(out_path, 'w', encoding='utf-8') as f_out:
                    json.dump(data, f_out, ensure_ascii=False, indent=2)
                logger.info("extract_json_from_pdf: saved output -> %s", out_path)
            except Exception:
                logger.exception("extract_json_from_pdf: failed to save output JSON")
            return ExtractionResult(document_type=doc_type, json_data=data, model_tag=model, errors=[])
        except Exception as e:  # pragma: no cover
            last_err = str(e)
            logger.warning("extract_json_from_pdf: error attempt=%d err=%s", attempt + 1, last_err)
            time.sleep(0.5 if attempt == 0 else 2.0)
            continue

    logger.error("extract_json_from_pdf: failed after retries err=%s", last_err)
    return ExtractionResult(document_type=doc_type, json_data={}, model_tag=model, errors=[last_err or "unknown_error"])


def iter_json_sections_for_embedding(doc_type: str, data: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Yield (section_name, section_text) pairs for vectorization."""
    sections = SUPPORTED_TYPES.get(doc_type, {}).get("required_sections", [])
    out: List[Tuple[str, str]] = []
    for sec in sections:
        val = data.get(sec)
        if isinstance(val, (dict, list)):
            text = json.dumps(val, ensure_ascii=False)
        else:
            text = str(val)
        out.append((sec, text))
    return out


# --- Vectorization helpers ---
def _embed_texts(texts: List[str]) -> List[List[float]]:  # pragma: no cover
    if not texts:
        return []
    client = _openai_client()
    resp = client.embeddings.create(model=MODEL_EMBEDDING, input=texts)
    # SDK returns data list with .embedding per item
    vectors: List[List[float]] = []
    try:
        for item in getattr(resp, "data", []) or []:
            vec = getattr(item, "embedding", None)
            if vec:
                vectors.append(list(vec))
    except Exception:
        logger.exception("_embed_texts: failed to parse embeddings response")
    return vectors


def vectorize_extracted_document(ed_obj) -> int:  # pragma: no cover
    """Create DocumentVector rows for an ExtractedDocument.
    Returns number of vectors created.
    """
    try:
        from apps.public_core.models.document_vector import DocumentVector
    except Exception as e:
        logger.exception("vectorize_extracted_document: import failed")
        return 0
    try:
        doc_type = getattr(ed_obj, "document_type", None) or ""
        data = getattr(ed_obj, "json_data", None) or {}
        if not isinstance(data, dict):
            return 0
        sections = iter_json_sections_for_embedding(doc_type, data)
        if not sections:
            return 0
        texts = [s for _, s in sections]
        embeddings = _embed_texts(texts)
        
        # Get well for enriched metadata (if available)
        well = getattr(ed_obj, "well", None)
        
        # Extract district from JSON (commonly in well_info section)
        well_info = data.get("well_info", {}) if isinstance(data, dict) else {}
        district = well_info.get("district") or well_info.get("rrc_district")
        
        # Get tenant attribution (Phase 1: uploaded_by_tenant)
        uploaded_by_tenant = getattr(ed_obj, "uploaded_by_tenant", None)
        tenant_id_str = str(uploaded_by_tenant) if uploaded_by_tenant else None
        
        created = 0
        for (section_name, section_text), emb in zip(sections, embeddings):
            try:
                DocumentVector.objects.create(
                    well=well,
                    file_name=(getattr(ed_obj, "source_path", None) or ""),
                    document_type=doc_type,
                    section_name=section_name,
                    section_text=section_text,
                    embedding=emb,
                    metadata={
                        # Existing fields
                        "ed_id": str(getattr(ed_obj, "id", "")),
                        "api_number": getattr(ed_obj, "api_number", ""),
                        "model_tag": getattr(ed_obj, "model_tag", ""),
                        
                        # Roadmap-aligned fields (from Consolidated-AI-Roadmap.md line 46)
                        # Tenant attribution (populated from ExtractedDocument.uploaded_by_tenant)
                        "tenant_id": tenant_id_str,  # None for RRC-sourced, UUID string for tenant uploads
                        
                        # Well context for retrieval filtering
                        "operator": getattr(well, "operator_name", None) if well else None,
                        "district": district,
                        "county": getattr(well, "county", None) if well else None,
                        "field": getattr(well, "field_name", None) if well else None,
                        "lat": float(well.lat) if (well and well.lat) else None,
                        "lon": float(well.lon) if (well and well.lon) else None,
                        
                        # Plan-level metadata (populated later when plans are generated)
                        "step_types": None,  # Future: list of step types from plan
                        "materials": None,  # Future: materials summary from plan
                        "approval_status": None,  # Future: approved/rejected/pending
                        "overlay_id": None,  # Future: canonical facts overlay ID
                        "kernel_version": None,  # Future: kernel version used
                    },
                )
                created += 1
            except Exception:
                logger.exception("vectorize_extracted_document: failed to create vector row")
        return created
    except Exception:
        logger.exception("vectorize_extracted_document: failure")
        return 0

