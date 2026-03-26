"""
Neubus Form-Specific Extraction (Pass 2).

For each form group identified by the classifier, sends the grouped pages
to OpenAI Vision with a form-specific structured prompt for data extraction.

Reuses existing prompts from openai_extraction.py for W-2, W-15, W-3A.
Adds new extraction for W-1, W-3, G-1.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF

from apps.public_core.models import ExtractedDocument, WellRegistry
from apps.public_core.services.neubus_classifier import FormGroup
from apps.public_core.services.openai_config import get_openai_client, DEFAULT_CHAT_MODEL
from apps.public_core.services.openai_extraction import (
    SUPPORTED_TYPES,
    _load_prompt,
    _json_schema_for,
    _ensure_sections,
    vectorize_extracted_document,
)

logger = logging.getLogger(__name__)

# Map classifier form types to openai_extraction doc_type keys
FORM_TYPE_TO_DOC_TYPE = {
    "W-1": "w1",
    "W-2": "w2",
    "W-3": "w3",
    "W-3a": "w3a",
    "W-15": "w15",
    "G-1": "g1",
}

# Model for extraction
MODEL_EXTRACTION = DEFAULT_CHAT_MODEL  # gpt-4o


@dataclass
class ExtractionResult:
    """Result of extracting a single form group."""
    form_type: str
    doc_type: str
    pages: List[int]
    json_data: Dict[str, Any]
    errors: List[str] = field(default_factory=list)
    model_tag: str = ""
    status: str = "success"


def _pages_to_image_messages(pdf_path: Path, pages: List[int], dpi: int = 150) -> List[Dict]:
    """Convert multiple PDF pages to OpenAI Vision image messages."""
    doc = fitz.open(str(pdf_path))
    messages = []

    try:
        for page_num in pages:
            if page_num >= len(doc):
                logger.warning(f"Page {page_num} out of range for {pdf_path.name} ({len(doc)} pages)")
                continue

            page = doc[page_num]
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            png_bytes = pix.tobytes("png")
            b64 = base64.b64encode(png_bytes).decode("utf-8")

            messages.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{b64}",
                    "detail": "high",  # high detail for extraction accuracy
                },
            })
    finally:
        doc.close()

    return messages


def _extract_form_group(
    pdf_path: Path,
    form_group: FormGroup,
    client=None,
    tags: Optional[List[str]] = None,
) -> ExtractionResult:
    """
    Extract structured data from a single form group.

    Uses the appropriate prompt from openai_extraction.py based on form type.
    """
    doc_type = FORM_TYPE_TO_DOC_TYPE.get(form_group.form_type)

    if not doc_type or doc_type not in SUPPORTED_TYPES:
        return ExtractionResult(
            form_type=form_group.form_type,
            doc_type=doc_type or "unknown",
            pages=form_group.pages,
            json_data={},
            errors=[f"Unsupported form type: {form_group.form_type}"],
            status="error",
        )

    if client is None:
        client = get_openai_client(operation="neubus_extraction")

    # Build the prompt from the existing prompt library
    prompt_text = _load_prompt(SUPPORTED_TYPES[doc_type]["prompt_key"], tags=tags)

    # Convert pages to images
    image_messages = _pages_to_image_messages(pdf_path, form_group.pages)

    if not image_messages:
        return ExtractionResult(
            form_type=form_group.form_type,
            doc_type=doc_type,
            pages=form_group.pages,
            json_data={},
            errors=["No valid page images could be generated"],
            status="error",
        )

    # Build message content: prompt text + all page images
    content = [{"type": "text", "text": prompt_text}] + image_messages

    logger.info(
        f"Extracting {form_group.form_type} from pages {form_group.pages} "
        f"({len(image_messages)} images)"
    )

    try:
        resp = client.chat.completions.create(
            model=MODEL_EXTRACTION,
            messages=[{"role": "user", "content": content}],
            temperature=0,
            max_tokens=4000,
            response_format={"type": "json_object"},
        )

        raw = (resp.choices[0].message.content or "").strip()
        json_data = json.loads(raw)

        # Ensure all required sections are present
        json_data = _ensure_sections(doc_type, json_data)

        # Post-extraction validation
        from apps.public_core.services.extraction_validator import validate_extracted_data
        json_data = validate_extracted_data(doc_type, json_data)

        return ExtractionResult(
            form_type=form_group.form_type,
            doc_type=doc_type,
            pages=form_group.pages,
            json_data=json_data,
            model_tag=MODEL_EXTRACTION,
            status="success",
        )

    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error for {form_group.form_type} pages {form_group.pages}: {e}")
        return ExtractionResult(
            form_type=form_group.form_type,
            doc_type=doc_type,
            pages=form_group.pages,
            json_data={},
            errors=[f"JSON parse error: {e}"],
            model_tag=MODEL_EXTRACTION,
            status="error",
        )
    except Exception as e:
        logger.exception(f"Extraction failed for {form_group.form_type} pages {form_group.pages}")
        return ExtractionResult(
            form_type=form_group.form_type,
            doc_type=doc_type,
            pages=form_group.pages,
            json_data={},
            errors=[str(e)],
            model_tag=MODEL_EXTRACTION,
            status="error",
        )


def extract_form_groups(
    pdf_path: Path,
    form_groups: List[FormGroup],
    neubus_doc=None,
    well: Optional[WellRegistry] = None,
    api_number: str = "",
    neubus_filename: str = "",
    file_hash: str = "",
    max_workers: int = 4,
    segment_tags: Optional[Dict[str, List[str]]] = None,
    segments: Optional[list] = None,
    lease_id: str = "",
    lease_well_map: Optional[Dict] = None,
    state: str = "",
    neubus_well_number: str = "",
) -> List[ExtractionResult]:
    """
    Extract structured data from all form groups in a document.
    Runs form groups in parallel for speed.

    Args:
        pdf_path: Path to the PDF file
        form_groups: List of FormGroup from the classifier
        neubus_doc: Optional NeubusDocument to update status
        well: Optional WellRegistry to link ExtractedDocuments to
        api_number: Well API number for the ExtractedDocument
        neubus_filename: Original Neubus filename
        file_hash: SHA-256 hash of the source file
        max_workers: Max parallel extraction threads
        segment_tags: Optional dict mapping form_type → list of tags for that segment
        segments: Optional list of DocumentSegment objects for linking
        lease_id: Lease ID for cross-referencing well numbers
        lease_well_map: Pre-built dict mapping well_no → api14 for fast lookup
        state: State code (TX/NM) for filtering DB lookups

    Returns:
        List of ExtractionResult objects
    """
    if not form_groups:
        logger.info("No form groups to extract")
        return []

    # Update status
    if neubus_doc:
        neubus_doc.extraction_status = "processing"
        neubus_doc.save(update_fields=["extraction_status"])

    client = get_openai_client(operation="neubus_extraction")
    results: List[ExtractionResult] = []

    # Run extractions in parallel
    with ThreadPoolExecutor(max_workers=min(max_workers, len(form_groups))) as executor:
        future_to_group = {
            executor.submit(
                _extract_form_group, pdf_path, fg, client,
                tags=(segment_tags or {}).get(fg.form_type),
            ): fg
            for fg in form_groups
        }

        for future in as_completed(future_to_group):
            fg = future_to_group[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                logger.exception(f"Extraction thread failed for {fg.form_type}")
                results.append(ExtractionResult(
                    form_type=fg.form_type,
                    doc_type=FORM_TYPE_TO_DOC_TYPE.get(fg.form_type, "unknown"),
                    pages=fg.pages,
                    json_data={},
                    errors=[str(e)],
                    status="error",
                ))

    # Sort results by first page to maintain document order
    results.sort(key=lambda r: r.pages[0] if r.pages else 0)

    # Persist ExtractedDocument records
    _persist_extraction_results(
        results=results,
        well=well,
        api_number=api_number,
        neubus_filename=neubus_filename,
        file_hash=file_hash,
        segments=segments,
        lease_id=lease_id,
        lease_well_map=lease_well_map,
        state=state,
        neubus_well_number=neubus_well_number,
        pdf_path=pdf_path,
    )

    # Update NeubusDocument status
    if neubus_doc:
        has_errors = any(r.status == "error" for r in results)
        neubus_doc.extraction_status = "complete" if not has_errors else "error"
        neubus_doc.save(update_fields=["extraction_status"])

    return results


def _clean_api(raw: str | None) -> str:
    """Strip non-digit characters from an API number string."""
    if not raw:
        return ""
    return re.sub(r"\D+", "", str(raw).strip())


def _resolve_well_attribution(
    json_data: dict,
    fallback_api: str,
    state: str,
    lease_id: str | None = None,
    lease_well_map: dict | None = None,
    neubus_well_number: str = "",
) -> tuple[str, str, str]:
    """
    Determine the correct API number for an extracted document using
    multiple fields in priority order.

    Returns: (api14: str, confidence: str, method: str)
    """
    well_info = (json_data or {}).get("well_info", {})
    if not isinstance(well_info, dict):
        well_info = {}

    # 1. Direct API from extracted form (strongest signal)
    extracted_api = _clean_api(well_info.get("api"))
    if extracted_api and len(extracted_api) >= 8:
        return extracted_api, "high", "extracted_api"

    # 2. Well number + lease cross-reference
    well_no = str(well_info.get("well_no", "") or "").strip()
    lease = str(well_info.get("lease", "") or "").strip()

    if well_no:
        well_no_normalized = well_no.lstrip("0")

        # 2a. Fast dict lookup from pre-built lease-well map (most authoritative)
        if lease_well_map and well_no_normalized:
            mapped_api = lease_well_map.get(well_no_normalized)
            if mapped_api:
                return mapped_api, "high", "lease_well_map"

        # 2b. DB lookup: well_number within same lease_id
        if lease_id and well_no_normalized:
            from apps.public_core.models import WellRegistry
            match = WellRegistry.objects.filter(
                lease_id=lease_id,
                well_number__iexact=well_no_normalized,
            ).first()
            if match and match.api14:
                return match.api14, "high", "well_no+lease_id"

        # 2c. DB lookup: well_number + fuzzy lease name match
        if lease and well_no_normalized:
            from apps.public_core.models import WellRegistry
            from django.db.models import Q
            candidates = WellRegistry.objects.filter(
                well_number__iexact=well_no_normalized,
            ).filter(
                Q(lease_name__icontains=lease)
                | Q(lease_name__icontains=lease.split()[0])
            )
            if state:
                candidates = candidates.filter(
                    api14__startswith={"TX": "42", "NM": "30"}.get(state, "")
                )
            if candidates.count() == 1:
                match = candidates.first()
                if match and match.api14:
                    return match.api14, "medium", "well_no+lease_name"

        # 2d. DB lookup: well_number among lease siblings
        if lease_id and well_no_normalized:
            from apps.public_core.models import WellRegistry
            siblings = WellRegistry.objects.filter(lease_id=lease_id)
            for sib in siblings[:50]:  # cap to avoid huge leases
                if sib.well_number and sib.well_number.lstrip("0") == well_no_normalized:
                    if sib.api14:
                        return sib.api14, "medium", "well_no+lease_siblings"

    # 3. Neubus metadata well_number (from Neubus document index, not LLM)
    if neubus_well_number:
        nwn = neubus_well_number.strip().lstrip("0")
        if nwn:
            if lease_well_map and nwn in lease_well_map:
                return lease_well_map[nwn], "medium", "neubus_well_no+map"
            if lease_id:
                from apps.public_core.models import WellRegistry
                match = WellRegistry.objects.filter(
                    lease_id=lease_id,
                    well_number__iexact=nwn,
                ).first()
                if match and match.api14:
                    return match.api14, "medium", "neubus_well_no+lease_id"

    # 4. Fall back to session API (lowest confidence)
    return fallback_api, "low", "session_fallback"


def _persist_extraction_results(
    results: List[ExtractionResult],
    well: Optional[WellRegistry],
    api_number: str,
    neubus_filename: str,
    file_hash: str,
    segments: Optional[list] = None,
    lease_id: str = "",
    lease_well_map: Optional[Dict] = None,
    state: str = "",
    neubus_well_number: str = "",
    pdf_path: Optional[Path] = None,
) -> List[ExtractedDocument]:
    """
    Create ExtractedDocument records for each extraction result.

    Each form group produces one ExtractedDocument. W-3 and W-3A are
    fully independent records.
    """
    from django.db import transaction

    eds = []

    # Track form_group_index per doc_type
    type_counters: Dict[str, int] = {}

    for result in results:
        if result.status == "error" and not result.json_data:
            continue

        doc_type = result.doc_type
        type_counters[doc_type] = type_counters.get(doc_type, 0) + 1
        group_index = type_counters[doc_type]

        with transaction.atomic():
            ed = ExtractedDocument.objects.create(
                well=well,
                api_number=api_number,
                document_type=doc_type,
                source_path=str(neubus_filename),
                neubus_filename=neubus_filename,
                source_page=result.pages[0] + 1 if result.pages else None,  # 1-indexed
                file_hash=file_hash,
                form_group_index=group_index,
                model_tag=result.model_tag,
                status=result.status,
                errors=result.errors,
                json_data=result.json_data,
                source_type=ExtractedDocument.SOURCE_NEUBUS,
            )

            # --- Multi-field well attribution ---
            resolved_api, confidence, method = _resolve_well_attribution(
                json_data=result.json_data,
                fallback_api=api_number,
                state=state,
                lease_id=lease_id,
                lease_well_map=lease_well_map,
                neubus_well_number=neubus_well_number,
            )
            ed.attribution_confidence = confidence
            ed.attribution_method = method
            logger.info(
                f"Attribution for {result.form_type} p{result.pages}: "
                f"api={resolved_api} confidence={confidence} method={method}"
            )

            if resolved_api and resolved_api != api_number:
                ed.api_number = resolved_api
                # Try to link to the correct WellRegistry
                from apps.public_core.models import WellRegistry
                api14 = resolved_api.ljust(14, "0") if len(resolved_api) < 14 else resolved_api[:14]
                correct_well = WellRegistry.objects.filter(api14=api14).first()
                if not correct_well:
                    # Fallback: suffix match
                    correct_well = WellRegistry.objects.filter(
                        api14__endswith=resolved_api[-8:]
                    ).first()
                if correct_well and correct_well != well:
                    ed.well = correct_well

            # Also use segment-level attribution if available
            if segments:
                for seg in segments:
                    if (seg.form_type == result.form_type
                            and seg.page_start == (result.pages[0] if result.pages else -1)
                            and hasattr(seg, 'attribution_api')
                            and seg.attribution_api
                            and confidence == "low"):
                        # Segment had a better attribution — use it
                        ed.api_number = seg.attribution_api
                        ed.attribution_confidence = seg.attribution_confidence
                        ed.attribution_method = f"segment:{seg.attribution_method}"
                        from apps.public_core.models import WellRegistry
                        seg_api14 = seg.attribution_api.ljust(14, "0") if len(seg.attribution_api) < 14 else seg.attribution_api[:14]
                        seg_well = WellRegistry.objects.filter(api14=seg_api14).first()
                        if seg_well:
                            ed.well = seg_well

            # OCR escalation: if attribution is still "low" confidence,
            # try OCR on the first page of this form group
            if ed.attribution_confidence == "low" and result.pages and pdf_path:
                try:
                    from apps.public_core.services.ocr_api_detector import detect_api_from_page
                    ocr_result = detect_api_from_page(
                        pdf_path,
                        page_num=result.pages[0],
                        use_vision_fallback=True,
                    )
                    if ocr_result and ocr_result.get("api"):
                        ocr_api = ocr_result["api"]
                        if len(ocr_api) >= 8:
                            resolved_api = ocr_api
                            confidence = ocr_result.get("confidence", "medium")
                            method = ocr_result["method"]
                            ed.api_number = resolved_api
                            ed.attribution_confidence = confidence
                            ed.attribution_method = method
                            # Try to link to correct well
                            from apps.public_core.models import WellRegistry
                            api14 = resolved_api.ljust(14, "0") if len(resolved_api) < 14 else resolved_api[:14]
                            correct_well = WellRegistry.objects.filter(api14=api14).first()
                            if not correct_well:
                                correct_well = WellRegistry.objects.filter(api14__endswith=resolved_api[-8:]).first()
                            if correct_well:
                                ed.well = correct_well
                            logger.info(f"OCR escalation found API {ocr_api} for {result.form_type} p{result.pages[0]} ({method})")
                except Exception as e:
                    logger.debug(f"OCR escalation failed for {result.form_type} p{result.pages}: {e}")

            ed.save(update_fields=["api_number", "well", "attribution_confidence", "attribution_method"])

            # Link to DocumentSegment if available
            if segments:
                matching_seg = None
                for seg in segments:
                    if seg.form_type == result.form_type and seg.page_start == (result.pages[0] if result.pages else -1):
                        matching_seg = seg
                        break
                if matching_seg:
                    ed.segment = matching_seg
                    matching_seg.extracted_document = ed
                    matching_seg.status = "extracted"
                    matching_seg.save(update_fields=["extracted_document", "status"])
                    ed.save(update_fields=["segment"])

            # Vectorize
            try:
                vectorize_extracted_document(ed)
            except Exception as e:
                logger.warning(f"Vectorization failed for {doc_type} ED {ed.id}: {e}")

            eds.append(ed)

    logger.info(f"Persisted {len(eds)} ExtractedDocument records from {neubus_filename}")
    return eds
