"""
Neubus Semantic Index Builder.

Generates text summaries and vector embeddings for extracted Neubus documents:
- Per-form text summary → vector embed
- Per-well timeline synthesis → vector embed
- Stored as DocumentVector rows keyed by document_type and section_name

NOTE on DocumentVector field mapping (actual model fields):
  well          → FK to WellRegistry (looked up by api_number)
  file_name     → neubus_filename from ExtractedDocument
  document_type → form type (w1, w2, w3, etc.)
  section_name  → "neubus_form_summary" or "neubus_well_timeline"
  section_text  → the generated summary or timeline text
  embedding     → 3072-dim vector from text-embedding-3-large
  metadata      → {"api_number": ..., "chunk_index": ..., ...}

The model does NOT have: extracted_document FK, chunk_type, chunk_index, api_number.
These are tracked via metadata and section_name conventions instead.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from apps.public_core.models import DocumentVector, WellRegistry
from apps.public_core.services.openai_config import get_openai_client, DEFAULT_EMBEDDING_MODEL
from apps.public_core.services.text_processing import chunk_text

logger = logging.getLogger(__name__)

# Max characters per chunk for embedding
MAX_CHUNK_SIZE = 2000

# Section name constants (stored in DocumentVector.section_name)
SECTION_FORM_SUMMARY = "neubus_form_summary"
SECTION_WELL_TIMELINE = "neubus_well_timeline"


def build_form_summary(ed) -> str:
    """
    Generate a human-readable text summary for an extracted document.

    The summary captures the key facts from the structured JSON in
    natural language form, suitable for vector embedding and retrieval.

    Args:
        ed: ExtractedDocument instance

    Returns:
        Text summary string
    """
    doc_type = ed.document_type
    data = ed.json_data or {}
    api = ed.api_number or "Unknown"

    builders = {
        "w1": _summarize_w1,
        "w2": _summarize_w2,
        "w3": _summarize_w3,
        "w3a": _summarize_w3a,
        "w15": _summarize_w15,
        "g1": _summarize_g1,
    }

    builder = builders.get(doc_type)
    if builder:
        try:
            return builder(data, api)
        except Exception as e:
            logger.warning(f"Summary builder failed for {doc_type} ED {ed.id}: {e}")

    # Fallback: generic summary from raw text
    raw = data.get("_raw_text", "")
    if raw:
        return f"{doc_type.upper()} document for well {api}. {raw[:500]}"

    return f"{doc_type.upper()} document for well {api}."


def _get(data: dict, *keys, default=""):
    """Safely navigate nested dict."""
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key, default)
        else:
            return default
    return current if current is not None else default


def _summarize_w1(data: dict, api: str) -> str:
    """Summarize W-1 Drilling Permit."""
    well = data.get("well_info", {}) or {}
    permit = data.get("permit_info", {}) or {}
    operator = _get(data, "operator_info", "name")
    proposed = data.get("proposed_work", {}) or {}

    parts = [f"W-1 Drilling Permit — Well {api}"]
    if well.get("lease"):
        parts.append(f"({well['lease']} #{well.get('well_no', '')})")
    if operator:
        parts.append(f"\nOperator: {operator}")
    if permit.get("permit_number"):
        parts.append(f", Permit #{permit['permit_number']}")
    if permit.get("permit_date"):
        parts.append(f", issued {permit['permit_date']}")
    if proposed.get("proposed_total_depth_ft"):
        parts.append(f"\nProposed TD: {proposed['proposed_total_depth_ft']} ft")
    if proposed.get("proposed_formation"):
        parts.append(f", target formation: {proposed['proposed_formation']}")
    if well.get("county"):
        parts.append(f"\nCounty: {well['county']}")
    if well.get("field"):
        parts.append(f", Field: {well['field']}")

    return "".join(parts)


def _summarize_w2(data: dict, api: str) -> str:
    """Summarize W-2 Completion Report."""
    well = data.get("well_info", {}) or {}
    operator = _get(data, "operator_info", "name")
    completion = data.get("completion_info", {}) or {}
    ipt = data.get("initial_potential_test", {}) or {}
    casings = data.get("casing_record", []) or []
    perfs = data.get("producing_injection_disposal_interval", []) or []

    parts = [f"W-2 Completion — Well {api}"]
    if well.get("lease"):
        parts.append(f" ({well['lease']} #{well.get('well_no', '')})")
    if operator:
        parts.append(f"\nOperator: {operator}")
    if well.get("total_depth_ft"):
        parts.append(f", total depth {well['total_depth_ft']} ft")

    # Casing summary
    for c in casings:
        string_type = c.get("string", c.get("string_type", ""))
        if string_type and c.get("size_in") and c.get("bottom_ft"):
            parts.append(
                f"\nCasing: {c['size_in']}\" {string_type} at {c['bottom_ft']} ft"
            )
            if c.get("cement_top_ft") is not None:
                parts.append(f" (cement top {c['cement_top_ft']} ft)")

    # Perforations
    if perfs:
        perf_strs = [f"{p.get('from_ft', '?')}-{p.get('to_ft', '?')} ft" for p in perfs]
        parts.append(f"\nPerforations: {', '.join(perf_strs)}")

    # Initial potential
    if ipt:
        ip_parts = []
        if ipt.get("oil_bpd"):
            ip_parts.append(f"{ipt['oil_bpd']} BOPD")
        if ipt.get("gas_mcfd"):
            ip_parts.append(f"{ipt['gas_mcfd']} MCFD")
        if ipt.get("water_bpd"):
            ip_parts.append(f"{ipt['water_bpd']} BWPD")
        if ip_parts:
            parts.append(f"\nInitial potential: {', '.join(ip_parts)}")

    return "".join(parts)


def _summarize_w3(data: dict, api: str) -> str:
    """Summarize W-3 Plugging Record."""
    well = data.get("well_info", {}) or {}
    operator = _get(data, "operator_info", "name")
    summary = data.get("plugging_summary", {}) or {}
    plugs = data.get("plug_record", []) or []

    parts = [f"W-3 Plugging Record — Well {api}"]
    if well.get("lease"):
        parts.append(f" ({well['lease']} #{well.get('well_no', '')})")
    if operator:
        parts.append(f"\nOperator: {operator}")
    if summary.get("plugging_completed_date"):
        parts.append(f", plugged {summary['plugging_completed_date']}")
    if summary.get("service_company"):
        parts.append(f" by {summary['service_company']}")
    if well.get("total_depth_ft"):
        parts.append(f"\nTotal depth: {well['total_depth_ft']} ft")

    if plugs:
        parts.append(f"\n{len(plugs)} plugs set:")
        for p in plugs:
            plug_desc = f"  #{p.get('plug_number', '?')}"
            if p.get("depth_top_ft") and p.get("depth_bottom_ft"):
                plug_desc += f" {p['depth_top_ft']}-{p['depth_bottom_ft']} ft"
            if p.get("sacks"):
                plug_desc += f" ({p['sacks']} sacks)"
            if p.get("method"):
                plug_desc += f" [{p['method']}]"
            parts.append(f"\n{plug_desc}")

    return "".join(parts)


def _summarize_w3a(data: dict, api: str) -> str:
    """Summarize W-3A Plugging Proposal."""
    header = data.get("header", {}) or {}
    operator = header.get("operator", "")
    plugs = data.get("plugging_proposal", []) or []

    parts = [f"W-3A Plugging Proposal — Well {api}"]
    if header.get("well_name"):
        parts.append(f" ({header['well_name']})")
    if operator:
        parts.append(f"\nOperator: {operator}")
    if header.get("total_depth_ft"):
        parts.append(f", TD {header['total_depth_ft']} ft")

    if plugs:
        parts.append(f"\nProposed {len(plugs)} plugs")

    duqw = data.get("duqw", {}) or {}
    if duqw.get("depth_ft"):
        parts.append(f"\nDUQW: {duqw['depth_ft']} ft ({duqw.get('formation', '')})")

    return "".join(parts)


def _summarize_w15(data: dict, api: str) -> str:
    """Summarize W-15 Cementing Report."""
    well = data.get("well_info", {}) or {}
    operator = _get(data, "operator_info", "name")
    cement_data = data.get("cementing_data", []) or []
    mech = data.get("mechanical_equipment", []) or []

    parts = [f"W-15 Cementing Report — Well {api}"]
    if well.get("lease"):
        parts.append(f" ({well['lease']} #{well.get('well_no', '')})")
    if operator:
        parts.append(f"\nOperator: {operator}")

    for c in cement_data:
        job = c.get("job", "")
        if c.get("sacks"):
            parts.append(f"\n{job}: {c['sacks']} sacks")
            if c.get("interval_top_ft") and c.get("interval_bottom_ft"):
                parts.append(f" ({c['interval_top_ft']}-{c['interval_bottom_ft']} ft)")

    if mech:
        for m in mech:
            parts.append(
                f"\n{m.get('equipment_type', 'Equipment')} at {m.get('depth_ft', '?')} ft"
            )

    return "".join(parts)


def _summarize_g1(data: dict, api: str) -> str:
    """Summarize G-1 Gas Well Test."""
    well = data.get("well_info", {}) or {}
    operator = _get(data, "operator_info", "name")
    test = data.get("test_data", {}) or {}
    deliv = data.get("deliverability", {}) or {}

    parts = [f"G-1 Gas Well Test — Well {api}"]
    if well.get("lease"):
        parts.append(f" ({well['lease']} #{well.get('well_no', '')})")
    if operator:
        parts.append(f"\nOperator: {operator}")
    if well.get("formation_name"):
        parts.append(f", {well['formation_name']}")
    if test.get("test_date"):
        parts.append(f"\nTest date: {test['test_date']}")
    if test.get("shut_in_pressure_psi"):
        parts.append(f", SITP: {test['shut_in_pressure_psi']} psi")
    if deliv.get("aof_mcfd"):
        parts.append(f"\nAOF: {deliv['aof_mcfd']} MCFD")
    if deliv.get("authorized_rate_mcfd"):
        parts.append(f", authorized rate: {deliv['authorized_rate_mcfd']} MCFD")

    return "".join(parts)


def build_well_timeline(api_number: str) -> str:
    """
    Build a chronological timeline of all extracted forms for a well.

    Assembles all ExtractedDocuments for the API number into a single
    narrative suitable for vector embedding.
    """
    from apps.public_core.models import ExtractedDocument

    eds = (
        ExtractedDocument.objects
        .filter(api_number=api_number, status__in=["success", "partial"])
        .order_by("created_at")
    )

    if not eds.exists():
        return ""

    events = []

    for ed in eds:
        data = ed.json_data or {}
        doc_type = ed.document_type

        # Extract date and description for timeline
        event_date = _extract_date(data, doc_type)
        event_desc = _extract_timeline_entry(data, doc_type)

        if event_desc:
            events.append({
                "date": event_date or "Unknown",
                "type": doc_type.upper(),
                "description": event_desc,
            })

    if not events:
        return ""

    # Sort by date (best effort — some dates may be partial or missing)
    events.sort(key=lambda e: e["date"])

    parts = [f"Well {api_number} timeline:"]

    for event in events:
        parts.append(f"\n{event['date']}: ({event['type']}) {event['description']}")

    return "".join(parts)


def _extract_date(data: dict, doc_type: str) -> Optional[str]:
    """Extract the most relevant date from extracted data."""
    if doc_type == "w1":
        return _get(data, "permit_info", "permit_date") or _get(data, "header", "date_filed")
    elif doc_type == "w2":
        return _get(data, "completion_info", "completion_date") or _get(data, "header", "tracking_no")
    elif doc_type == "w3":
        return _get(data, "plugging_summary", "plugging_completed_date") or _get(data, "header", "date_filed")
    elif doc_type == "w3a":
        return _get(data, "header", "date_filed")
    elif doc_type == "w15":
        return _get(data, "header", "date")
    elif doc_type == "g1":
        return _get(data, "test_data", "test_date") or _get(data, "header", "date_filed")
    return None


def _extract_timeline_entry(data: dict, doc_type: str) -> str:
    """Extract a one-line timeline description from extracted data."""
    if doc_type == "w1":
        proposed = data.get("proposed_work", {}) or {}
        depth = proposed.get("proposed_total_depth_ft", "?")
        formation = proposed.get("proposed_formation", "")
        return f"Drilling permit filed, proposed TD {depth} ft" + (f", {formation}" if formation else "")

    elif doc_type == "w2":
        ipt = data.get("initial_potential_test", {}) or {}
        perfs = data.get("producing_injection_disposal_interval", []) or []
        parts = ["Completed"]
        if perfs:
            perf_str = f" perfs {perfs[0].get('from_ft', '?')}-{perfs[0].get('to_ft', '?')} ft"
            parts.append(perf_str)
        ip_parts = []
        if ipt.get("oil_bpd"):
            ip_parts.append(f"{ipt['oil_bpd']} BOPD")
        if ipt.get("gas_mcfd"):
            ip_parts.append(f"{ipt['gas_mcfd']} MCFD")
        if ip_parts:
            parts.append(f", IP: {', '.join(ip_parts)}")
        return "".join(parts)

    elif doc_type == "w3":
        summary = data.get("plugging_summary", {}) or {}
        plugs = data.get("plug_record", []) or []
        company = summary.get("service_company", "")
        return f"Plugged, {len(plugs)} plugs set" + (f" by {company}" if company else "")

    elif doc_type == "w3a":
        plugs = data.get("plugging_proposal", []) or []
        return f"Plugging proposal filed, {len(plugs)} plugs proposed"

    elif doc_type == "w15":
        cement_data = data.get("cementing_data", []) or []
        return f"Cementing report, {len(cement_data)} cement jobs"

    elif doc_type == "g1":
        deliv = data.get("deliverability", {}) or {}
        aof = deliv.get("aof_mcfd", "?")
        return f"Gas well test, AOF {aof} MCFD"

    return f"{doc_type.upper()} document"


def embed_text(text: str) -> List[float]:
    """Generate vector embedding for text using OpenAI."""
    client = get_openai_client(operation="neubus_embedding")
    resp = client.embeddings.create(
        model=DEFAULT_EMBEDDING_MODEL,
        input=text,
    )
    return resp.data[0].embedding


def _get_well_for_api(api_number: str) -> Optional[WellRegistry]:
    """Look up WellRegistry by API number (tolerant of formatting differences)."""
    if not api_number:
        return None
    clean = api_number.replace("-", "").replace(" ", "")
    # Try exact match first, then suffix match
    well = WellRegistry.objects.filter(api14=clean).first()
    if not well and len(clean) >= 8:
        well = WellRegistry.objects.filter(api14__endswith=clean[-8:]).first()
    return well


def index_form_summary(ed) -> Optional[DocumentVector]:
    """
    Generate and store a vector embedding for a single extracted document.

    Creates a text summary of the form, embeds it, and stores as a DocumentVector.
    Uses section_name="neubus_form_summary" to identify these rows.

    Args:
        ed: ExtractedDocument instance

    Returns:
        DocumentVector if created, None if skipped or failed
    """
    summary = build_form_summary(ed)
    if not summary or len(summary) < 20:
        logger.debug(f"Skipping embedding for ED {ed.id}: summary too short")
        return None

    api_number = ed.api_number or ""
    filename = getattr(ed, "source_filename", "") or getattr(ed, "file_name", "") or str(ed.id)
    well = _get_well_for_api(api_number)

    # Check for existing vector keyed by file_name + section_name
    existing = DocumentVector.objects.filter(
        file_name=filename,
        document_type=ed.document_type,
        section_name=SECTION_FORM_SUMMARY,
    ).first()
    if existing:
        logger.debug(f"Vector already exists for ED {ed.id}")
        return existing

    try:
        embedding = embed_text(summary)

        dv = DocumentVector.objects.create(
            well=well,
            file_name=filename,
            document_type=ed.document_type,
            section_name=SECTION_FORM_SUMMARY,
            section_text=summary[:MAX_CHUNK_SIZE],
            embedding=embedding,
            metadata={
                "api_number": api_number,
                "extracted_document_id": str(ed.id),
                "source": "neubus",
            },
        )

        logger.info(f"Indexed form summary for ED {ed.id} ({ed.document_type})")
        return dv

    except Exception as e:
        logger.warning(f"Failed to embed form summary for ED {ed.id}: {e}")
        return None


def index_well_timeline(api_number: str) -> Optional[DocumentVector]:
    """
    Generate and store a vector embedding for the well timeline.

    Builds a chronological narrative of all forms for this well and embeds it.
    Existing timeline vectors for this well are replaced (section_name="neubus_well_timeline").

    Args:
        api_number: Well API number

    Returns:
        First DocumentVector chunk if created, None if skipped or failed
    """
    timeline = build_well_timeline(api_number)
    if not timeline or len(timeline) < 20:
        logger.debug(f"Skipping timeline embedding for {api_number}: too short")
        return None

    well = _get_well_for_api(api_number)

    # Delete existing timeline vectors for this well (rebuilt each time)
    DocumentVector.objects.filter(
        section_name=SECTION_WELL_TIMELINE,
        metadata__api_number=api_number,
    ).delete()

    try:
        chunks = chunk_text(timeline, max_chars=MAX_CHUNK_SIZE)

        dvs = []
        for i, chunk in enumerate(chunks):
            embedding = embed_text(chunk)
            dv = DocumentVector.objects.create(
                well=well,
                file_name=f"timeline_{api_number}",
                document_type="timeline",
                section_name=SECTION_WELL_TIMELINE,
                section_text=chunk,
                embedding=embedding,
                metadata={
                    "api_number": api_number,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "source": "neubus",
                },
            )
            dvs.append(dv)

        logger.info(f"Indexed well timeline for {api_number} ({len(dvs)} chunks)")
        return dvs[0] if dvs else None

    except Exception as e:
        logger.warning(f"Failed to embed well timeline for {api_number}: {e}")
        return None


def build_semantic_index(api_number: str) -> Dict[str, int]:
    """
    Build the full semantic index for a well.

    1. Generate per-form summaries and embed them
    2. Build and embed the well timeline

    Only processes ExtractedDocuments with source_type=SOURCE_NEUBUS when that
    attribute exists; falls back to all success/partial docs for this API.

    Returns counts of indexed items.
    """
    from apps.public_core.models import ExtractedDocument

    # Filter to Neubus-sourced documents if SOURCE_NEUBUS is defined on the model
    source_neubus = getattr(ExtractedDocument, "SOURCE_NEUBUS", None)
    qs = ExtractedDocument.objects.filter(
        api_number=api_number,
        status__in=["success", "partial"],
    )
    if source_neubus is not None:
        qs = qs.filter(source_type=source_neubus)

    form_count = 0
    for ed in qs:
        if index_form_summary(ed):
            form_count += 1

    timeline_count = 1 if index_well_timeline(api_number) else 0

    logger.info(
        f"[Neubus Semantic] Built index for {api_number}: "
        f"{form_count} form summaries, {timeline_count} timeline"
    )

    return {
        "form_summaries": form_count,
        "timeline_chunks": timeline_count,
    }
