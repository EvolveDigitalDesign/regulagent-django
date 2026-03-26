"""
Formation Isolation Auditor

Verifies that all required formations are properly isolated per
NM OCD Standard Plugging Conditions (19.15.25 NMAC).

Loads conditions from nm_ocd_plugging_conditions.json and evaluates
triggers against well data, then checks actual plugs for satisfying
placement.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Path to the conditions JSON fixture
_CONDITIONS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "fixtures",
    "nm_ocd_plugging_conditions.json",
)


class IsolationStatus(str, Enum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    INSUFFICIENT = "insufficient"
    UNKNOWN = "unknown"


@dataclass
class IsolationRequirement:
    condition_id: str
    label: str
    formation_name: str
    formation_top_ft: Optional[float]
    requirement_description: str
    status: IsolationStatus
    satisfying_plug_number: Optional[int] = None
    notes: str = ""


@dataclass
class FormationAuditResult:
    api_number: str
    total_requirements: int
    satisfied: int
    unsatisfied: int
    unknown: int
    requirements: List[IsolationRequirement] = field(default_factory=list)
    overall_status: str = ""
    narrative: str = ""


def _load_conditions() -> List[Dict[str, Any]]:
    """Load NM OCD plugging conditions from JSON fixture."""
    try:
        with open(_CONDITIONS_PATH, "r") as f:
            data = json.load(f)
        return data.get("conditions", [])
    except FileNotFoundError:
        logger.warning("NM OCD conditions file not found at %s", _CONDITIONS_PATH)
        return []
    except json.JSONDecodeError as e:
        logger.error("Failed to parse NM OCD conditions JSON: %s", e)
        return []


def _trigger_matches(
    trigger: Dict[str, Any],
    formation_tops: List[Dict[str, Any]],
    existing_perforations: List[Dict[str, Any]],
    casing_record: List[Dict[str, Any]],
) -> bool:
    """Evaluate whether a condition's trigger is active for this well."""
    trigger_type = trigger.get("type", "")

    if trigger_type == "always":
        return True

    if trigger_type == "has_perforations":
        return bool(existing_perforations)

    if trigger_type == "casing_shoe_exists":
        return any(
            c.get("shoe_depth_ft") or c.get("bottom_ft")
            for c in casing_record
            if isinstance(c, dict)
        )

    if trigger_type == "open_hole_below_casing":
        # Check if there's open hole below the deepest casing shoe
        max_shoe = 0
        for c in casing_record:
            if isinstance(c, dict):
                shoe = c.get("shoe_depth_ft") or c.get("bottom_ft") or 0
                try:
                    shoe = float(shoe)
                except (TypeError, ValueError):
                    shoe = 0
                max_shoe = max(max_shoe, shoe)
        # If any formation top is deeper than the deepest shoe, there's open hole
        for ft in formation_tops:
            if isinstance(ft, dict):
                top = ft.get("top_ft")
                if top is not None:
                    try:
                        if float(top) > max_shoe and max_shoe > 0:
                            return True
                    except (TypeError, ValueError):
                        pass
        return False

    if trigger_type == "keyword_match":
        keywords = trigger.get("keywords", [])
        # Search through formation names and descriptions
        all_text = " ".join(
            str(ft.get("formation", "")).lower()
            for ft in formation_tops
            if isinstance(ft, dict)
        )
        return any(kw.lower() in all_text for kw in keywords)

    return False


def _find_satisfying_plug(
    requirement: Dict[str, Any],
    formation_top_ft: Optional[float],
    actual_plugs: List[Dict[str, Any]],
    existing_perforations: List[Dict[str, Any]],
    casing_record: List[Dict[str, Any]],
) -> tuple[Optional[int], IsolationStatus, str]:
    """Check if any actual plug satisfies the requirement.

    Returns (plug_number, status, notes).
    """
    placement = requirement.get("plug_placement", "")
    min_length = requirement.get("min_plug_length_ft", 0)

    if not actual_plugs:
        return None, IsolationStatus.UNSATISFIED, "No actual plugs found"

    # Surface plug check
    if placement == "top_within_50ft_of_surface":
        for plug in actual_plugs:
            top = plug.get("top_ft") or plug.get("depth_top_ft")
            if top is not None:
                try:
                    if float(top) <= 50:
                        return plug.get("plug_number"), IsolationStatus.SATISFIED, f"Surface plug top at {top}'"
                except (TypeError, ValueError):
                    pass
        return None, IsolationStatus.UNSATISFIED, "No plug with top within 50' of surface"

    # Straddle perforations check
    if placement == "straddle_perforations" and existing_perforations:
        # Find perforation interval range
        perf_tops = []
        perf_bottoms = []
        for perf in existing_perforations:
            if isinstance(perf, dict):
                pt = perf.get("top_ft") or perf.get("depth_top_ft")
                pb = perf.get("bottom_ft") or perf.get("depth_bottom_ft")
                if pt is not None:
                    try:
                        perf_tops.append(float(pt))
                    except (TypeError, ValueError):
                        pass
                if pb is not None:
                    try:
                        perf_bottoms.append(float(pb))
                    except (TypeError, ValueError):
                        pass

        if perf_tops and perf_bottoms:
            perf_top = min(perf_tops)
            perf_bottom = max(perf_bottoms)
            # Check if any plug straddles ±20ft
            for plug in actual_plugs:
                plug_top = plug.get("top_ft") or plug.get("depth_top_ft")
                plug_bottom = plug.get("bottom_ft") or plug.get("depth_bottom_ft")
                if plug_top is not None and plug_bottom is not None:
                    try:
                        pt = float(plug_top)
                        pb = float(plug_bottom)
                        if pt <= perf_top + 20 and pb >= perf_bottom - 20:
                            return (
                                plug.get("plug_number"),
                                IsolationStatus.SATISFIED,
                                f"Plug straddles perfs ({perf_top}'-{perf_bottom}')",
                            )
                    except (TypeError, ValueError):
                        pass

        return None, IsolationStatus.UNSATISFIED, "No plug straddles perforation interval"

    # Casing shoe check (within 50ft)
    if placement == "within_50ft_of_shoe":
        shoe_depths = []
        for c in casing_record:
            if isinstance(c, dict):
                shoe = c.get("shoe_depth_ft") or c.get("bottom_ft")
                if shoe is not None:
                    try:
                        shoe_depths.append(float(shoe))
                    except (TypeError, ValueError):
                        pass

        for shoe_depth in shoe_depths:
            for plug in actual_plugs:
                plug_top = plug.get("top_ft") or plug.get("depth_top_ft")
                plug_bottom = plug.get("bottom_ft") or plug.get("depth_bottom_ft")
                if plug_top is not None and plug_bottom is not None:
                    try:
                        pt = float(plug_top)
                        pb = float(plug_bottom)
                        if abs(pt - shoe_depth) <= 50 or abs(pb - shoe_depth) <= 50:
                            return (
                                plug.get("plug_number"),
                                IsolationStatus.SATISFIED,
                                f"Plug within 50' of shoe at {shoe_depth}'",
                            )
                    except (TypeError, ValueError):
                        pass

        return None, IsolationStatus.UNSATISFIED, "No plug within 50' of any casing shoe"

    # Formation top check (across_formation_top)
    if placement in ("across_formation_top", "straddle_duqw_depth") and formation_top_ft is not None:
        for plug in actual_plugs:
            plug_top = plug.get("top_ft") or plug.get("depth_top_ft")
            plug_bottom = plug.get("bottom_ft") or plug.get("depth_bottom_ft")
            if plug_top is not None and plug_bottom is not None:
                try:
                    pt = float(plug_top)
                    pb = float(plug_bottom)
                    # Plug must straddle the formation top ±20ft
                    if pt <= formation_top_ft + 20 and pb >= formation_top_ft - 20:
                        return (
                            plug.get("plug_number"),
                            IsolationStatus.SATISFIED,
                            f"Plug straddles formation top at {formation_top_ft}'",
                        )
                except (TypeError, ValueError):
                    pass

        return None, IsolationStatus.UNSATISFIED, f"No plug straddles formation top at {formation_top_ft}'"

    # Bottom of open hole check
    if placement == "bottom_of_open_hole":
        # Find deepest point
        max_shoe = 0
        for c in casing_record:
            if isinstance(c, dict):
                shoe = c.get("shoe_depth_ft") or c.get("bottom_ft") or 0
                try:
                    max_shoe = max(max_shoe, float(shoe))
                except (TypeError, ValueError):
                    pass

        if max_shoe > 0:
            for plug in actual_plugs:
                plug_bottom = plug.get("bottom_ft") or plug.get("depth_bottom_ft")
                if plug_bottom is not None:
                    try:
                        pb = float(plug_bottom)
                        if pb >= max_shoe:
                            return (
                                plug.get("plug_number"),
                                IsolationStatus.SATISFIED,
                                f"Plug covers open hole below shoe at {max_shoe}'",
                            )
                    except (TypeError, ValueError):
                        pass

        return None, IsolationStatus.UNSATISFIED, "No plug in open hole below casing shoe"

    # Spacing checks — handled separately by COA checker, mark as UNKNOWN here
    if placement in ("no_gap_exceeding_3000ft", "no_gap_exceeding_2000ft", "all_cement_plugs"):
        return None, IsolationStatus.UNKNOWN, "Spacing/WOC/class checks handled by COA compliance checker"

    # Between zones — generic check
    if placement == "between_each_zone":
        if len(actual_plugs) >= 2:
            return None, IsolationStatus.UNKNOWN, "Multiple zone isolation requires manual review"
        return None, IsolationStatus.UNSATISFIED, "Insufficient plugs for multi-zone isolation"

    return None, IsolationStatus.UNKNOWN, f"Unrecognized placement type: {placement}"


def audit(
    formation_tops: List[Dict[str, Any]],
    existing_perforations: List[Dict[str, Any]],
    casing_record: List[Dict[str, Any]],
    actual_plugs: List[Dict[str, Any]],
    api_number: str = "",
) -> FormationAuditResult:
    """Run formation isolation audit against NM OCD conditions.

    Args:
        formation_tops: List of {"formation": str, "top_ft": float|None}
        existing_perforations: List of perf interval dicts
        casing_record: List of casing string dicts
        actual_plugs: List of actual plug dicts from reconciliation
        api_number: Well API number for reporting

    Returns:
        FormationAuditResult with per-condition status.
    """
    conditions = _load_conditions()
    requirements: List[IsolationRequirement] = []

    for condition in conditions:
        trigger = condition.get("trigger", {})
        requirement = condition.get("requirement", {})
        condition_id = condition.get("condition_id", "")
        label = condition.get("label", "")

        if not _trigger_matches(trigger, formation_tops, existing_perforations, casing_record):
            continue

        # For formation-specific conditions, create one requirement per formation
        placement = requirement.get("plug_placement", "")
        if placement in ("across_formation_top", "straddle_duqw_depth"):
            for ft in formation_tops:
                if not isinstance(ft, dict):
                    continue
                formation_name = ft.get("formation", "Unknown")
                formation_top_ft = ft.get("top_ft")

                plug_num, status, notes = _find_satisfying_plug(
                    requirement,
                    float(formation_top_ft) if formation_top_ft is not None else None,
                    actual_plugs, existing_perforations, casing_record,
                )
                requirements.append(IsolationRequirement(
                    condition_id=condition_id,
                    label=label,
                    formation_name=formation_name,
                    formation_top_ft=float(formation_top_ft) if formation_top_ft is not None else None,
                    requirement_description=requirement.get("note", ""),
                    status=status,
                    satisfying_plug_number=plug_num,
                    notes=notes,
                ))
        else:
            # Non-formation-specific condition
            plug_num, status, notes = _find_satisfying_plug(
                requirement, None, actual_plugs, existing_perforations, casing_record,
            )
            requirements.append(IsolationRequirement(
                condition_id=condition_id,
                label=label,
                formation_name="",
                formation_top_ft=None,
                requirement_description=requirement.get("note", ""),
                status=status,
                satisfying_plug_number=plug_num,
                notes=notes,
            ))

    # Compute summary
    satisfied = sum(1 for r in requirements if r.status == IsolationStatus.SATISFIED)
    unsatisfied = sum(1 for r in requirements if r.status == IsolationStatus.UNSATISFIED)
    insufficient = sum(1 for r in requirements if r.status == IsolationStatus.INSUFFICIENT)
    unknown = sum(1 for r in requirements if r.status == IsolationStatus.UNKNOWN)
    total = len(requirements)

    if unsatisfied > 0 or insufficient > 0:
        overall = "deficient"
    elif unknown > 0:
        overall = "indeterminate"
    elif total == 0:
        overall = "no_conditions_triggered"
    else:
        overall = "compliant"

    # Build narrative
    narrative_parts = [f"Formation isolation audit for {api_number or 'well'}:"]
    narrative_parts.append(f"  {total} conditions evaluated, {satisfied} satisfied, {unsatisfied} unsatisfied, {unknown} indeterminate.")
    if unsatisfied > 0:
        for r in requirements:
            if r.status == IsolationStatus.UNSATISFIED:
                narrative_parts.append(f"  DEFICIENT: {r.label} — {r.notes}")

    result = FormationAuditResult(
        api_number=api_number,
        total_requirements=total,
        satisfied=satisfied,
        unsatisfied=unsatisfied + insufficient,
        unknown=unknown,
        requirements=requirements,
        overall_status=overall,
        narrative="\n".join(narrative_parts),
    )

    logger.info(
        "formation_isolation_auditor: api=%s total=%d satisfied=%d unsatisfied=%d unknown=%d status=%s",
        api_number, total, satisfied, unsatisfied + insufficient, unknown, overall,
    )

    return result
