"""
Operational Milestone Reconciler

Matches non-plug planned steps (miru, pooh, cleanout, cbl, pressure_test, etc.)
against DWR daily narratives using keyword matching. This prevents operational
milestones from showing as false MISSING in plug reconciliation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MilestoneStatus(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    PARTIAL = "partial"


@dataclass
class MilestoneComparison:
    step_number: Optional[int]
    step_type: str
    planned_description: str
    planned_depth_ft: Optional[float]
    status: MilestoneStatus
    matched_work_dates: List[str] = field(default_factory=list)
    matched_event_descriptions: List[str] = field(default_factory=list)
    notes: str = ""
    comparison_type: str = "milestone"


# Keyword map — each milestone type maps to lowercase search terms
MILESTONE_KEYWORDS: Dict[str, List[str]] = {
    "miru": ["rig up", "rigged up", "move in", "mobilize", "miru"],
    "pooh_tubing_rods": [
        "pull tubing", "pull rods", "poh tubing", "lay down tubing",
        "lay down rods", "pooh tubing", "pooh rods",
    ],
    "pooh": ["pooh", "pull out of hole", "poh"],
    "remove_rbp": ["remove rbp", "pull rbp", "retrieve bridge plug"],
    "cleanout": ["cleanout", "clean out", "circulate", "wash", "tag bottom"],
    "run_cbl": ["cbl", "cement bond log", "rcbl"],
    "cbl": ["cbl", "cement bond log", "rcbl"],
    "pressure_test": ["pressure test", "test to", "tested to", "pressure tested"],
    "casing_cut": ["cut casing", "cut and pull", "casing cut"],
    "cut_wellhead": ["cut wellhead", "wellhead removal", "cut and cap"],
    "rig_up": ["rig up", "rigged up", "miru"],
    "rig_down": ["rig down", "rigged down", "rig release"],
    "move_in": ["move in", "mobilize", "miru"],
    "move_out": ["move out", "demobilize", "rig release"],
}

# Depth pattern for fallback matching
_DEPTH_RE = re.compile(r"(\d{3,5})\s*(?:'|ft|feet)", re.IGNORECASE)


def _collect_narratives(parse_result: dict) -> List[Dict[str, Any]]:
    """Extract all day narratives and event descriptions from parse result.

    Returns list of {"date": str, "text": str} dicts for searching.
    """
    narratives = []
    for day in parse_result.get("days", []):
        if not isinstance(day, dict):
            continue
        date_str = str(day.get("work_date", ""))

        # Daily narrative
        daily_narrative = day.get("daily_narrative", "")
        if daily_narrative:
            narratives.append({"date": date_str, "text": str(daily_narrative)})

        # Individual event descriptions
        for event in day.get("events", []):
            if isinstance(event, dict):
                desc = event.get("description", "")
                if desc:
                    narratives.append({"date": date_str, "text": str(desc)})

    return narratives


def reconcile(
    planned_milestones: List[Dict[str, Any]],
    parse_result: dict,
) -> List[MilestoneComparison]:
    """Reconcile planned operational milestones against DWR narratives.

    Args:
        planned_milestones: List of planned step dicts with keys:
            step_number, step_type, description, depth_top_ft, depth_bottom_ft
        parse_result: Full DWR parse result dict with "days" key.

    Returns:
        List of MilestoneComparison results.
    """
    if not planned_milestones:
        return []

    narratives = _collect_narratives(parse_result)
    results: List[MilestoneComparison] = []

    for milestone in planned_milestones:
        if not isinstance(milestone, dict):
            continue

        step_type = (milestone.get("step_type") or "").lower().strip()
        step_number = milestone.get("step_number")
        description = milestone.get("description") or ""
        depth = milestone.get("depth_top_ft") or milestone.get("depth_bottom_ft")

        # Get keywords for this milestone type
        keywords = MILESTONE_KEYWORDS.get(step_type, [])

        # If no predefined keywords, try to derive from step_type itself
        if not keywords and step_type:
            keywords = [step_type.replace("_", " ")]

        # Search narratives for keyword matches
        matched_dates: List[str] = []
        matched_descriptions: List[str] = []

        for narrative in narratives:
            text_lower = narrative["text"].lower()
            for kw in keywords:
                if kw in text_lower:
                    if narrative["date"] not in matched_dates:
                        matched_dates.append(narrative["date"])
                    # Truncate long descriptions
                    desc_preview = narrative["text"][:200]
                    if desc_preview not in matched_descriptions:
                        matched_descriptions.append(desc_preview)
                    break  # One keyword match per narrative is enough

        # Determine status
        if matched_dates:
            status = MilestoneStatus.FOUND
            notes = f"Matched in {len(matched_dates)} day(s) via keyword"
        else:
            # Depth-based fallback: search for the planned depth in narratives
            if depth is not None:
                depth_str = str(int(depth))
                for narrative in narratives:
                    if depth_str in narrative["text"]:
                        if narrative["date"] not in matched_dates:
                            matched_dates.append(narrative["date"])
                        desc_preview = narrative["text"][:200]
                        if desc_preview not in matched_descriptions:
                            matched_descriptions.append(desc_preview)

                if matched_dates:
                    status = MilestoneStatus.PARTIAL
                    notes = f"Matched by depth reference ({depth_str}') — keyword not found"
                else:
                    status = MilestoneStatus.NOT_FOUND
                    notes = "No keyword or depth match found in DWR narratives"
            else:
                status = MilestoneStatus.NOT_FOUND
                notes = "No keyword match found in DWR narratives"

        results.append(MilestoneComparison(
            step_number=step_number,
            step_type=step_type,
            planned_description=description,
            planned_depth_ft=float(depth) if depth is not None else None,
            status=status,
            matched_work_dates=matched_dates,
            matched_event_descriptions=matched_descriptions,
            notes=notes,
        ))

    logger.info(
        "milestone_reconciler: %d milestones — %d found, %d partial, %d not_found",
        len(results),
        sum(1 for r in results if r.status == MilestoneStatus.FOUND),
        sum(1 for r in results if r.status == MilestoneStatus.PARTIAL),
        sum(1 for r in results if r.status == MilestoneStatus.NOT_FOUND),
    )

    return results
