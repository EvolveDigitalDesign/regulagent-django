"""
Document Segmenter — Text-First Classification + Breakpoint Detection.

Replaces all-Vision classification with:
1. Extract text from each page (PyMuPDF, $0)
2. Classify by regex pattern matching (~80% of pages)
3. Vision fallback only for low/no-text pages
4. Group consecutive same-type pages into segments (breakpoints)
5. Persist as DocumentSegment records

Cost reduction: ~80% fewer Vision API calls.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF

from apps.public_core.services.openai_config import get_openai_client

logger = logging.getLogger(__name__)

# Minimum text length to consider a page as having extractable text
MIN_TEXT_LENGTH = 20


@dataclass
class PageClassification:
    """Classification result for a single page."""
    page: int
    form_type: str
    is_continuation: bool
    confidence: str  # high, medium, low, none
    evidence: str
    method: str  # text, vision, hybrid


@dataclass
class SegmentData:
    """A classified segment (page range) within a source PDF."""
    form_type: str
    page_start: int  # 0-indexed inclusive
    page_end: int    # 0-indexed inclusive
    confidence: str
    method: str  # text, vision, hybrid
    evidence: str
    raw_text_cache: str = ""
    tags: list = field(default_factory=list)
    attribution_api: str = ""
    attribution_confidence: str = "unresolved"
    attribution_method: str = "unresolved"


# ─── TX RRC Form Patterns ───────────────────────────────────────────

TX_FORM_PATTERNS = {
    # --- Currently Extracted (have prompts in openai_extraction.py) ---
    "W-1":  [r"FORM\s+W-?1\b", r"APPLICATION.*PERMIT.*DRILL", r"DRILLING\s+PERMIT"],
    "W-2":  [r"FORM\s+W-?2\b", r"OIL\s+WELL\s+POTENTIAL\s+TEST", r"COMPLETION.*RECOMPLETION\s+REPORT",
             r"GAS.*WELL.*COMPLETION"],
    "W-3":  [r"FORM\s+W-?3\b(?!\s*A)", r"PLUGGING\s+RECORD", r"WELL\s+PLUGGING\s+REPORT"],
    "W-3a": [r"FORM\s+W-?3\s*A\b", r"W-?3A\b", r"NOTICE.*INTENTION.*PLUG", r"PLUGGING.*PROPOSAL"],
    "W-15": [r"FORM\s+W-?15\b", r"CEMENTING\s+REPORT", r"CASING.*CEMENTING"],
    "G-1":  [r"FORM\s+G-?1\b", r"GAS\s+WELL\s+BACK\s*PRESSURE", r"DELIVERABILITY\s+TEST"],
    "gau":  [r"GROUNDWATER\s+ADVISORY\s+UNIT", r"GAU\b.*LETTER", r"USABLE.*QUALITY.*WATER"],

    # --- W-series (not yet extracted but classifiable) ---
    "W-1D": [r"FORM\s+W-?1\s*D\b", r"W-?1D\b", r"DIRECTIONAL\s+SURVEY\s+APPLICATION"],
    "W-1H": [r"FORM\s+W-?1\s*H\b", r"W-?1H\b", r"HORIZONTAL\s+WELL\s+SUPPLEMENT"],
    "W-1A": [r"FORM\s+W-?1\s*A\b", r"W-?1A\b", r"SUBSTANDARD\s+ACREAGE"],
    "W-3C": [r"FORM\s+W-?3\s*C\b", r"W-?3C\b", r"SURFACE\s+EQUIPMENT\s+REMOVAL"],
    "W-3X": [r"FORM\s+W-?3\s*X\b", r"W-?3X\b", r"PLUGGING.*DEADLINE\s+EXTENSION"],
    "W-10": [r"FORM\s+W-?10\b", r"OIL\s+WELL\s+STATUS"],

    # --- G-series ---
    "G-10": [r"FORM\s+G-?10\b", r"GAS\s+WELL\s+STATUS"],

    # --- H-series (safety / injection / compliance) ---
    "H-1":  [r"FORM\s+H-?1\b", r"INJECTION\s+WELL\s+PERMIT\s+APPLICATION",
             r"APPLICATION.*INJECT.*DISPOSE"],
    "H-5":  [r"FORM\s+H-?5\b", r"INJECTION.*PRESSURE\s+TEST",
             r"DISPOSAL.*PRESSURE\s+TEST"],
    "H-8":  [r"FORM\s+H-?8\b", r"SPILL.*LOSS\s+REPORT", r"CRUDE\s+OIL.*LOSS"],
    "H-9":  [r"FORM\s+H-?9\b", r"CERTIFICATE\s+OF\s+COMPLIANCE", r"SWR\s+36"],
    "H-10": [r"FORM\s+H-?10\b", r"ANNUAL.*DISPOSAL.*INJECTION\s+REPORT",
             r"INJECTION.*WELL\s+MONITORING"],
    "H-15": [r"FORM\s+H-?15\b", r"INACTIVE\s+WELL\s+TEST"],

    # --- P-series (operator / production) ---
    "P-4":  [r"FORM\s+P-?4\b", r"GATHERER.*PURCHASER", r"CHANGE\s+OF\s+GATHERER"],
    "P-5":  [r"FORM\s+P-?5\b", r"ORGANIZATION\s+REPORT", r"P-?5\s+RENEWAL"],
    "P-13": [r"FORM\s+P-?13\b", r"CASING\s+PRESSURE\s+TEST"],
    "PR":   [r"PRODUCTION\s+REPORT", r"MONTHLY\s+PRODUCTION"],

    # --- Misc / SWR ---
    "SWR-13": [r"SWR[\s-]*13\b", r"STATEWIDE\s+RULE\s+13", r"CASING.*CEMENTING.*EXCEPTION"],
    "SWR-32": [r"SWR[\s-]*32\b", r"FLARE.*EXCEPTION", r"VENT.*EXCEPTION",
               r"STATEWIDE\s+RULE\s+32"],
    "ST-1":   [r"FORM\s+ST-?1\b", r"SEVERANCE\s+TAX\s+INCENTIVE"],
    "T-4":    [r"FORM\s+T-?4\b", r"PIPELINE\s+PERMIT", r"PIPELINE\s+CONSTRUCTION"],

    # --- Document types (not RRC forms but appear in well files) ---
    "schematic": [r"WELLBORE\s+SCHEMATIC", r"WELL\s+DIAGRAM", r"CASING\s+DIAGRAM",
                  r"WELL\s+BORE\s+SCHEMATIC"],
    "formation_tops": [r"FORMATION\s+TOPS", r"FORMATION\s+RECORD", r"GEOLOGICAL\s+TOPS"],
    "pa_procedure": [r"P\s*&\s*A\s+PROCEDURE", r"PLUG.*ABANDON.*PROCEDURE",
                     r"PLUGGING\s+PROCEDURE"],
    "electric_log": [r"ELECTRIC\s+LOG", r"WELL\s+LOG", r"INDUCTION\s+LOG"],
    "plat":     [r"LOCATION\s+PLAT", r"WELL\s+PLAT", r"SURVEY\s+PLAT"],
    "letter":   [r"LETTER\s+OF\s+DETERMINATION", r"LETTER\s+FROM\s+DISTRICT"],
}

NM_FORM_PATTERNS = {
    "c_101":  [r"C-?101\b", r"APPLICATION.*PERMIT.*DRILL", r"FORM\s+C-?101"],
    "c_103":  [r"C-?103\b", r"NOTICE.*INTENTION", r"FORM\s+C-?103"],
    "c_105":  [r"C-?105\b", r"WELL\s+COMPLETION", r"FORM\s+C-?105"],
    "c_115":  [r"C-?115\b", r"FORM\s+C-?115"],
    "sundry": [r"SUNDRY\s+NOTICE", r"SUNDRY\s+REPORT"],
}

# State header patterns for confidence boosting
STATE_HEADER_PATTERNS = {
    "TX": re.compile(r"RAILROAD\s+COMMISSION\s+OF\s+TEXAS|RAILROAD\s+COMMISSION", re.IGNORECASE),
    "NM": re.compile(r"OIL\s+CONSERVATION\s+DIVISION|ENERGY.*MINERALS.*NATURAL\s+RESOURCES", re.IGNORECASE),
}


def extract_page_text(pdf_path: Path, page_num: int) -> str:
    """Extract text from a single PDF page using PyMuPDF. Milliseconds per page, $0."""
    doc = fitz.open(str(pdf_path))
    try:
        if page_num >= len(doc):
            return ""
        page = doc[page_num]
        return page.get_text() or ""
    finally:
        doc.close()


def extract_all_page_texts(pdf_path: Path) -> List[str]:
    """Extract text from all pages in a PDF. Returns list indexed by page number."""
    doc = fitz.open(str(pdf_path))
    try:
        return [doc[i].get_text() or "" for i in range(len(doc))]
    finally:
        doc.close()


def classify_page_by_text(text: str, state: str) -> PageClassification:
    """
    Classify a page by regex pattern matching against form headers.

    Confidence logic:
    - 2+ pattern matches + state header → high
    - 1 pattern match → medium
    - 0 matches but text extracted (>20 chars) → low (Vision fallback candidate)
    - No/minimal text → none (definitely needs Vision)
    """
    patterns = TX_FORM_PATTERNS if state == "TX" else NM_FORM_PATTERNS
    state_header_re = STATE_HEADER_PATTERNS.get(state)

    has_state_header = bool(state_header_re and state_header_re.search(text)) if text else False

    best_form_type = "Other"
    best_match_count = 0
    best_evidence = ""

    for form_type, form_patterns in patterns.items():
        match_count = 0
        evidence_parts = []
        for pattern in form_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                match_count += 1
                evidence_parts.append(pattern)
        if match_count > best_match_count:
            best_match_count = match_count
            best_form_type = form_type
            best_evidence = f"Matched: {', '.join(evidence_parts[:3])}"

    # Determine confidence
    if best_match_count >= 2 and has_state_header:
        confidence = "high"
    elif best_match_count >= 2:
        confidence = "high"
    elif best_match_count == 1:
        confidence = "medium"
    elif len(text.strip()) > MIN_TEXT_LENGTH:
        confidence = "low"
        best_form_type = "Other"
        best_evidence = "Text present but no pattern match"
    else:
        confidence = "none"
        best_form_type = "Other"
        best_evidence = "No/minimal text on page"

    # Detect continuation: if form type found but text is short or lacks header keywords
    is_continuation = False
    if best_match_count > 0 and not has_state_header:
        # Check for continuation indicators
        continuation_indicators = [
            r"PAGE\s+\d+\s+OF\s+\d+",
            r"CONTINUED",
            r"^\s*\d+\s*$",  # page number only
        ]
        for indicator in continuation_indicators:
            if re.search(indicator, text, re.IGNORECASE):
                is_continuation = True
                break

    return PageClassification(
        page=-1,  # Will be set by caller
        form_type=best_form_type,
        is_continuation=is_continuation,
        confidence=confidence,
        evidence=best_evidence,
        method="text",
    )


def classify_page_by_vision(
    pdf_path: Path,
    page_num: int,
    client=None,
    state: str = "TX",
) -> PageClassification:
    """
    Classify a page using OpenAI Vision. Only called for low/none confidence pages.
    Reuses logic from neubus_classifier._classify_single_page.
    """
    from apps.public_core.services.neubus_classifier import (
        _page_to_image_bytes,
        _classify_single_page,
    )

    image_bytes = _page_to_image_bytes(pdf_path, page_num)
    result = _classify_single_page(image_bytes, page_num, client=client)

    return PageClassification(
        page=page_num,
        form_type=result.form_type,
        is_continuation=result.is_continuation,
        confidence=result.confidence,
        evidence=result.evidence,
        method="vision",
    )


def segment_document(
    pdf_path: Path,
    state: str = "TX",
    client=None,
    lease_well_map: dict | None = None,
) -> List[SegmentData]:
    """
    Main orchestrator: classify all pages and group into segments.

    1. For each page: extract text → classify by text
    2. Batch pages needing Vision fallback → send to Vision
    3. Group consecutive same-type pages into segments (breakpoints)
    4. Return segment descriptors (not yet persisted)
    """
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)
    doc.close()

    if total_pages == 0:
        logger.warning(f"Empty PDF: {pdf_path}")
        return []

    logger.info(f"[Segmenter] Classifying {total_pages} pages in {pdf_path.name} (state={state})")

    # Step 1: Extract text and classify by text for all pages
    page_texts = extract_all_page_texts(pdf_path)
    classifications: List[PageClassification] = []
    vision_needed: List[int] = []

    for page_num, text in enumerate(page_texts):
        classification = classify_page_by_text(text, state)
        classification.page = page_num
        classifications.append(classification)

        if classification.confidence in ("low", "none"):
            vision_needed.append(page_num)

    text_classified = total_pages - len(vision_needed)
    logger.info(
        f"[Segmenter] Text classification: {text_classified}/{total_pages} pages classified by text "
        f"({len(vision_needed)} need Vision fallback)"
    )

    # Step 2: Vision fallback for pages that need it
    if vision_needed:
        if client is None:
            client = get_openai_client(operation="neubus_classification")

        for page_num in vision_needed:
            try:
                vision_result = classify_page_by_vision(pdf_path, page_num, client=client, state=state)
                # Merge vision result, keeping text confidence info
                old = classifications[page_num]
                if vision_result.form_type != "Other":
                    classifications[page_num] = vision_result
                    classifications[page_num].method = "hybrid" if old.confidence == "low" else "vision"
                else:
                    # Vision also couldn't classify — keep as Other
                    classifications[page_num].method = "hybrid" if old.confidence == "low" else "vision"
                    classifications[page_num].evidence = f"Text: {old.evidence}; Vision: {vision_result.evidence}"
            except Exception as e:
                logger.warning(f"[Segmenter] Vision fallback failed for page {page_num}: {e}")

    # Step 3: Group consecutive same-type pages into segments
    segments = _group_into_segments(classifications, page_texts)

    # Step 4: Apply semantic tags to each segment
    try:
        from apps.public_core.services.segment_tagger import tag_segment
        for seg in segments:
            seg.tags = tag_segment(seg.form_type, seg.raw_text_cache)
    except Exception as e:
        logger.warning(f"[Segmenter] Tagging failed, segments will have empty tags: {e}")

    # Step 5: Attribute each segment using text content (runs unconditionally —
    # the API regex scan works without a lease_well_map)
    for seg in segments:
        api, conf, meth = attribute_segment(seg, lease_well_map, state, pdf_path=pdf_path)
        seg.attribution_api = api
        seg.attribution_confidence = conf
        seg.attribution_method = meth

    logger.info(
        f"[Segmenter] Created {len(segments)} segments from {total_pages} pages: "
        f"{[(s.form_type, s.page_start, s.page_end) for s in segments]}"
    )

    return segments


def _group_into_segments(
    classifications: List[PageClassification],
    page_texts: List[str],
) -> List[SegmentData]:
    """
    Group classified pages into segments. A new segment starts when:
    - is_continuation=False and form_type changes
    - OR is_continuation=False and form_type is W-3/W-3a (always independent)
    - "Other" pages break segments
    """
    segments: List[SegmentData] = []
    current: Optional[SegmentData] = None

    for c in classifications:
        if c.form_type == "Other":
            current = None
            continue

        # W-3 and W-3a are always independent segments
        always_independent = c.form_type in ("W-3", "W-3a")

        if c.is_continuation and current is not None and c.form_type == current.form_type:
            # Continue current segment
            current.page_end = c.page
            # Aggregate text cache
            if c.page < len(page_texts):
                current.raw_text_cache += "\n" + page_texts[c.page]
            # Downgrade confidence if any page is low
            if c.confidence == "low":
                current.confidence = "low"
            continue

        if not c.is_continuation or current is None or c.form_type != current.form_type or always_independent:
            # Start new segment
            raw_text = page_texts[c.page] if c.page < len(page_texts) else ""
            current = SegmentData(
                form_type=c.form_type,
                page_start=c.page,
                page_end=c.page,
                confidence=c.confidence,
                method=c.method,
                evidence=c.evidence,
                raw_text_cache=raw_text,
            )
            segments.append(current)

    return segments


def attribute_segment(
    segment_data: SegmentData,
    lease_well_map: dict | None = None,
    state: str = "",
    pdf_path: Path | None = None,
) -> tuple[str, str, str]:
    """
    Attempt to resolve the correct API for a single segment
    using its cached text content. Runs during the cheap text-extraction
    phase (no LLM cost).

    Args:
        segment_data: The SegmentData with raw_text_cache populated
        lease_well_map: Pre-built {well_number: api14} mapping
        state: State code (TX, NM)

    Returns:
        (api14: str, confidence: str, method: str)
        Returns ("", "unresolved", "unresolved") if no attribution found.
    """
    import re

    text = segment_data.raw_text_cache or ""
    if not text.strip():
        return "", "unresolved", "unresolved"

    # 1. Look for API pattern in segment text
    # Matches patterns like: API# 42-003-35663, API No. 42003356630000, API: 42-003-35663-00-00
    api_patterns = [
        r'API\s*(?:#|No\.?|Number)?\s*:?\s*([\d][\d\s-]{7,17})',
        r'(?:^|\s)(4[20]\s*[-.]?\s*\d{3}\s*[-.]?\s*\d{5})',  # TX/NM API prefix pattern
    ]

    for pattern in api_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            clean = re.sub(r'[\s\-.]', '', match.group(1))
            if len(clean) >= 8 and clean.isdigit():
                return clean, "high", "segment_text_api"

    # 2. Look for well number + cross-reference against lease_well_map
    well_no_patterns = [
        r'Well\s*(?:No\.?|Number|#)\s*:?\s*(\d+)',
        r'Well\s+(\d+)\s',
        r'Wl\.\s*No\.\s*(\d+)',
    ]

    for pattern in well_no_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match and lease_well_map:
            well_no = match.group(1).lstrip("0")
            if well_no and well_no in lease_well_map:
                return lease_well_map[well_no], "medium", "segment_text_well_no"

    # 3. OCR-based detection (for scanned documents where text extraction fails)
    if pdf_path and pdf_path.exists():
        try:
            from apps.public_core.services.ocr_api_detector import detect_api_from_page
            ocr_result = detect_api_from_page(
                pdf_path,
                page_num=segment_data.page_start,
                use_vision_fallback=False,  # Vision too expensive per-segment
            )
            if ocr_result:
                ocr_api = ocr_result["api"]
                # Direct API match
                if len(ocr_api) >= 8:
                    return ocr_api, "high", "ocr_tesseract"

                # Cross-reference OCR well number if it looks like a well number
                # (unlikely from this function, but handle it)
        except Exception as e:
            logger.warning(f"[Segmenter] OCR attribution failed for page {segment_data.page_start}: {e}")

    return "", "unresolved", "unresolved"


def persist_segments(
    segments: List[SegmentData],
    well=None,
    api_number: str = "",
    source_filename: str = "",
    source_path: str = "",
    file_hash: str = "",
    source_type: str = "neubus",
    total_source_pages: int = 0,
) -> list:
    """
    Bulk create DocumentSegment model records from segment data.

    Returns list of created DocumentSegment instances.
    """
    from apps.public_core.models.document_segment import DocumentSegment

    created = []
    for seg in segments:
        ds = DocumentSegment.objects.create(
            well=well,
            api_number=api_number,
            source_filename=source_filename,
            source_path=source_path,
            file_hash=file_hash,
            source_type=source_type,
            page_start=seg.page_start,
            page_end=seg.page_end,
            total_source_pages=total_source_pages,
            form_type=seg.form_type,
            classification_method=seg.method,
            classification_confidence=seg.confidence,
            classification_evidence=seg.evidence,
            tags=seg.tags,
            status="classified",
            raw_text_cache=seg.raw_text_cache[:50000],  # Cap at 50KB
            attribution_api=seg.attribution_api if hasattr(seg, 'attribution_api') else "",
            attribution_confidence=seg.attribution_confidence if hasattr(seg, 'attribution_confidence') else "unresolved",
            attribution_method=seg.attribution_method if hasattr(seg, 'attribution_method') else "unresolved",
        )
        created.append(ds)

    logger.info(f"[Segmenter] Persisted {len(created)} DocumentSegment records for {source_filename}")
    return created
