"""
Timeline Builder — Constructs a chronological "life of well" from extracted documents.

For each ExtractedDocument, extracts:
- A temporal anchor (date) based on document type
- An event type classification
- Key data summary (depths, cement, casing changes)
- Links to WellComponent records
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import List, Optional, Tuple

from django.db import transaction

from apps.public_core.models import ExtractedDocument, WellRegistry
from apps.public_core.models.well_timeline_event import WellTimelineEvent

logger = logging.getLogger(__name__)


# Map document types to event types
DOC_TYPE_TO_EVENT_TYPE = {
    "w1": "permit",
    "w2": "completion",
    "w3": "plugging",
    "w3a": "plugging_proposal",
    "w15": "cement_job",
    "g1": "test",
    "gau": "permit",
    "c_101": "permit",
    "c_103": "plugging",
    "c_105": "completion",
    "sundry": "workover",
    "schematic": "other",
    "formation_tops": "other",
    "pa_procedure": "plugging_proposal",
}


def _extract_date(json_data: dict, doc_type: str) -> Tuple[Optional[date], str]:
    """
    Extract the most relevant date from extracted JSON data.

    Returns (date_object, precision) where precision is 'day', 'month', 'year', or 'unknown'.
    """
    date_str = None
    precision = "unknown"

    # Try type-specific date fields first
    if doc_type == "w2":
        date_str = (
            _deep_get(json_data, "completion_info", "completion_date")
            or _deep_get(json_data, "filing_info", "date_filed")
            or _deep_get(json_data, "header", "date_filed")
        )
    elif doc_type == "w3":
        date_str = (
            _deep_get(json_data, "plugging_summary", "plugging_completed_date")
            or _deep_get(json_data, "plugging_summary", "plug_date")
            or _deep_get(json_data, "header", "date_filed")
        )
    elif doc_type == "w15":
        # Look for date in cementing_data array
        cementing = json_data.get("cementing_data")
        if isinstance(cementing, list) and cementing:
            date_str = cementing[0].get("date") if isinstance(cementing[0], dict) else None
        if not date_str:
            date_str = _deep_get(json_data, "header", "date")
    elif doc_type == "w1":
        date_str = (
            _deep_get(json_data, "header", "date_filed")
            or _deep_get(json_data, "permit_info", "permit_date")
        )
    elif doc_type == "w3a":
        date_str = _deep_get(json_data, "header", "date_filed")
    elif doc_type == "g1":
        date_str = (
            _deep_get(json_data, "test_data", "test_date")
            or _deep_get(json_data, "header", "date_filed")
        )
    elif doc_type in ("c_103", "c_105", "c_101", "sundry"):
        date_str = (
            _deep_get(json_data, "header", "date")
            or _deep_get(json_data, "header", "completion_date")
        )

    # Generic fallback
    if not date_str:
        date_str = (
            _deep_get(json_data, "header", "date")
            or _deep_get(json_data, "header", "date_filed")
        )

    if not date_str or not isinstance(date_str, str):
        return None, "unknown"

    # Parse date string
    parsed = _parse_date_string(date_str)
    if parsed:
        return parsed[0], parsed[1]

    return None, "unknown"


def _parse_date_string(date_str: str) -> Optional[Tuple[date, str]]:
    """Parse various date formats and return (date, precision)."""
    date_str = date_str.strip()
    if not date_str:
        return None

    # Try common formats
    for fmt, prec in [
        ("%Y-%m-%d", "day"),
        ("%m/%d/%Y", "day"),
        ("%m-%d-%Y", "day"),
        ("%B %d, %Y", "day"),
        ("%b %d, %Y", "day"),
        ("%Y-%m", "month"),
        ("%m/%Y", "month"),
        ("%Y", "year"),
    ]:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.date(), prec
        except ValueError:
            continue

    return None


def _deep_get(data: dict, *keys) -> Optional[str]:
    """Safely navigate nested dicts."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) else None


def _build_title(doc_type: str, json_data: dict, event_date: Optional[date]) -> str:
    """Build a human-readable event title."""
    year_str = str(event_date.year) if event_date else "unknown year"

    titles = {
        "w1": f"Drilling Permit Filed ({year_str})",
        "w2": f"Completion Report ({year_str})",
        "w3": f"Plugging Record ({year_str})",
        "w3a": f"Plugging Proposal ({year_str})",
        "w15": f"Cementing Report ({year_str})",
        "g1": f"Gas Well Test ({year_str})",
        "gau": f"GAU Determination ({year_str})",
        "c_101": f"NM Permit to Drill ({year_str})",
        "c_103": f"NM P&A / Workover Notice ({year_str})",
        "c_105": f"NM Completion Report ({year_str})",
        "sundry": f"Sundry Notice ({year_str})",
        "schematic": f"Well Schematic ({year_str})",
        "formation_tops": f"Formation Record ({year_str})",
        "pa_procedure": f"P&A Procedure ({year_str})",
    }
    return titles.get(doc_type, f"Document ({doc_type}, {year_str})")


def _extract_key_data(doc_type: str, json_data: dict) -> dict:
    """Extract structured highlights from the document JSON."""
    key_data = {}

    if doc_type == "w2":
        # Completion info
        comp = json_data.get("completion_info", {})
        if isinstance(comp, dict):
            key_data["completion_date"] = comp.get("completion_date")
            key_data["well_type"] = comp.get("well_type")

        # Casing summary
        casing = json_data.get("casing_record", [])
        if isinstance(casing, list):
            key_data["casing_strings"] = len(casing)
            for c in casing:
                if isinstance(c, dict) and c.get("string"):
                    key_data[f"{c['string']}_shoe_ft"] = c.get("shoe_depth_ft") or c.get("bottom_ft")

        # Perforations
        perfs = json_data.get("producing_injection_disposal_interval", [])
        if isinstance(perfs, list) and perfs:
            key_data["perforation_intervals"] = len(perfs)

    elif doc_type == "w3":
        summary = json_data.get("plugging_summary", {})
        if isinstance(summary, dict):
            key_data["plug_date"] = summary.get("plugging_completed_date") or summary.get("plug_date")
            key_data["service_company"] = summary.get("service_company")

        plugs = json_data.get("plug_record", [])
        if isinstance(plugs, list):
            key_data["total_plugs"] = len(plugs)

        disp = json_data.get("casing_disposition", {})
        if isinstance(disp, dict):
            key_data["casing_left_in_hole"] = disp.get("casing_left_in_hole")

    elif doc_type == "w15":
        cementing = json_data.get("cementing_data", [])
        if isinstance(cementing, list):
            key_data["cement_jobs"] = len(cementing)
            total_sacks = sum(c.get("sacks", 0) or 0 for c in cementing if isinstance(c, dict))
            if total_sacks:
                key_data["total_sacks"] = total_sacks

        mech = json_data.get("mechanical_equipment", [])
        if isinstance(mech, list):
            key_data["mechanical_equipment"] = len(mech)

    elif doc_type == "w1":
        well_info = json_data.get("well_info", {})
        if isinstance(well_info, dict):
            key_data["total_depth_ft"] = well_info.get("total_depth_ft")
            key_data["well_type"] = well_info.get("well_type")

        proposed = json_data.get("proposed_work", {})
        if isinstance(proposed, dict):
            key_data["proposed_total_depth_ft"] = proposed.get("proposed_total_depth_ft")
            key_data["target_formation"] = proposed.get("proposed_formation")

    elif doc_type == "g1":
        test = json_data.get("test_data", {})
        if isinstance(test, dict):
            key_data["test_date"] = test.get("test_date")
            key_data["shut_in_pressure_psi"] = test.get("shut_in_pressure_psi")

        deliv = json_data.get("deliverability", {})
        if isinstance(deliv, dict):
            key_data["aof_mcfd"] = deliv.get("aof_mcfd")

    # Strip None values
    return {k: v for k, v in key_data.items() if v is not None}


def build_timeline(well: WellRegistry) -> List[WellTimelineEvent]:
    """
    Build a chronological timeline for a well from all its ExtractedDocuments.

    Returns list of created WellTimelineEvent objects.
    """
    docs = ExtractedDocument.objects.filter(
        well=well,
        status__in=["success", "partial"],
    ).order_by("created_at")

    events = []
    for doc in docs:
        json_data = doc.json_data or {}
        doc_type = doc.document_type

        event_type = DOC_TYPE_TO_EVENT_TYPE.get(doc_type, "other")
        event_date, precision = _extract_date(json_data, doc_type)
        title = _build_title(doc_type, json_data, event_date)
        key_data = _extract_key_data(doc_type, json_data)

        # Build description
        operator = (
            _deep_get(json_data, "operator_info", "name")
            or _deep_get(json_data, "header", "operator")
            or ""
        )
        description = f"Filed by {operator}" if operator else ""

        event = WellTimelineEvent(
            well=well,
            event_date=event_date,
            event_date_precision=precision,
            event_type=event_type,
            title=title,
            description=description,
            key_data=key_data,
            source_document=doc,
            source_segment=getattr(doc, 'segment', None),
            source_document_type=doc_type,
        )
        events.append(event)

    return events


def refresh_timeline(well: WellRegistry) -> List[WellTimelineEvent]:
    """
    Delete and rebuild the timeline for a well. Idempotent.

    Called from finalize_session_task after all documents are indexed.
    """
    with transaction.atomic():
        # Delete existing events
        deleted_count, _ = WellTimelineEvent.objects.filter(well=well).delete()
        if deleted_count:
            logger.info(f"[Timeline] Deleted {deleted_count} existing events for {well.api14}")

        # Rebuild
        events = build_timeline(well)

        # Bulk create
        created = WellTimelineEvent.objects.bulk_create(events)

        logger.info(f"[Timeline] Created {len(created)} timeline events for {well.api14}")

        # Link components (M2M requires saved objects, so iterate)
        # Component linking is done post-bulk_create since M2M needs PKs
        # For now, component linking is deferred to a future enhancement

    return created
