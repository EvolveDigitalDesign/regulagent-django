"""Plug Reconciliation Engine — compares planned vs actual plugging operations.

Used by subsequent reports to generate deviation analysis between
the NOI (planned) and DWR-extracted (actual) plug placements.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class DeviationLevel(Enum):
    MATCH = "match"        # Green — within tolerance
    MINOR = "minor"        # Amber — small deviation, acceptable
    MAJOR = "major"        # Red — significant deviation, needs explanation
    ADDED = "added"        # Blue — plug not in original plan
    MISSING = "missing"    # Red — planned plug not found in actuals


# Tolerance thresholds
DEPTH_TOLERANCE_FT = 20    # ±20 ft for tag depth matching
SACK_TOLERANCE_PCT = 0.10  # ±10% for sack count matching

# Event types that represent actual plug placements (for matching to planned plugs)
PLUG_PLACEMENT_EVENT_TYPES = {
    "set_cement_plug",
    "set_surface_plug",
    "set_bridge_plug",
    "set_marker",
    "squeeze",
}

# Type compatibility: planned_type → set of compatible actual event_types
# Used by _match_plugs to prefer type-compatible pairings.
TYPE_COMPATIBILITY = {
    "cibp":          {"set_bridge_plug"},
    "cement_plug":   {"set_cement_plug", "pump_cement", "squeeze"},
    "spot_plug":     {"set_cement_plug", "pump_cement", "squeeze"},
    "perf_squeeze":  {"squeeze", "set_cement_plug"},
    "perf_and_squeeze": {"squeeze", "set_cement_plug"},
    "surface_plug":  {"set_cement_plug", "set_surface_plug"},
    "topoff":        {"set_cement_plug", "circulate"},
    # casing_cut has NO compatible plug placement types — it's a non-plug operation
    "casing_cut":    set(),
}


def _types_compatible(planned_type: str, actual_type: str) -> bool:
    """Check if a planned operation type is compatible with an actual event type."""
    if not planned_type or not actual_type:
        return True  # Unknown type = don't penalize
    p = (planned_type or "").lower().strip()
    a = (actual_type or "").lower().strip()
    compatible_set = TYPE_COMPATIBILITY.get(p)
    if compatible_set is None:
        return True  # Unknown planned type = don't restrict
    return a in compatible_set


@dataclass
class PlugComparison:
    """Side-by-side comparison of one planned vs actual plug."""
    plug_number: int

    # Planned (from NOI)
    planned_type: Optional[str] = None
    planned_top_ft: Optional[float] = None
    planned_bottom_ft: Optional[float] = None
    planned_sacks: Optional[float] = None
    planned_cement_class: Optional[str] = None
    planned_formation: Optional[str] = None

    # Actual (from DWR)
    actual_type: Optional[str] = None
    actual_top_ft: Optional[float] = None
    actual_bottom_ft: Optional[float] = None
    actual_sacks: Optional[float] = None
    actual_cement_class: Optional[str] = None
    actual_tagged_depth_ft: Optional[float] = None

    # Placement method comparison
    planned_placement_method: Optional[str] = None
    actual_placement_method: Optional[str] = None

    # WOC comparison
    planned_woc_hours: Optional[float] = None
    actual_woc_hours: Optional[float] = None
    actual_woc_tagged: Optional[bool] = None

    # Deviation analysis
    deviation_level: DeviationLevel = DeviationLevel.MATCH
    depth_deviation_ft: Optional[float] = None
    sack_deviation_pct: Optional[float] = None
    deviation_notes: List[str] = field(default_factory=list)

    # Justification tracking
    justification_note: str = ""
    justification_resolved: bool = False
    justification_resolved_by: str = ""
    justification_resolved_at: Optional[str] = None
    # Variance approval
    variance_approval_found: bool = False
    variance_approval_reference: str = ""
    # AI-extracted justification metadata
    justification_source_type: str = ""          # agency_approval | field_condition | corrective_action | combined | none
    justification_confidence: float = 0.0        # 0.0-1.0 confidence score
    justification_source_days: List[str] = field(default_factory=list)  # work dates the justification was extracted from


@dataclass
class ReconciliationResult:
    """Complete reconciliation result."""
    api_number: str
    c103_form_id: Optional[int] = None

    comparisons: List[PlugComparison] = field(default_factory=list)

    # Summary counts
    total_planned: int = 0
    total_actual: int = 0
    matches: int = 0
    minor_deviations: int = 0
    major_deviations: int = 0
    added_plugs: int = 0
    missing_plugs: int = 0

    # Overall assessment
    overall_status: str = ""  # "compliant", "minor_deviations", "major_deviations"
    summary_narrative: str = ""

    # Justification summary
    unresolved_divergences: int = 0
    resolved_divergences: int = 0


def _midpoint(top: Optional[float], bottom: Optional[float]) -> Optional[float]:
    """Return midpoint depth; falls back to whichever value is present."""
    if top is not None and bottom is not None:
        return (top + bottom) / 2.0
    return top if top is not None else bottom


def _get_planned_midpoint(planned: dict) -> Optional[float]:
    """Extract midpoint depth from a planned plug dict."""
    return _midpoint(
        planned.get("top_ft"),
        planned.get("bottom_ft"),
    )


def _get_actual_midpoint(actual: dict) -> Optional[float]:
    """Extract midpoint depth from an actual event dict."""
    return _midpoint(
        actual.get("depth_top_ft"),
        actual.get("depth_bottom_ft"),
    )


class PlugReconciliationEngine:
    """Compare planned plugging program with actual field operations."""

    def __init__(
        self,
        depth_tolerance_ft: float = DEPTH_TOLERANCE_FT,
        sack_tolerance_pct: float = SACK_TOLERANCE_PCT,
    ):
        self.depth_tolerance = depth_tolerance_ft
        self.sack_tolerance = sack_tolerance_pct

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reconcile(
        self,
        planned_plugs: list,
        actual_events: list,
        api_number: str = "",
        c103_form_id: int = None,
    ) -> ReconciliationResult:
        """Reconcile planned plugs with actual DWR events.

        Args:
            planned_plugs: List of planned plug dicts (from C103PlugORM or C103PlugRow).
                Each has: plug_number, step_type, top_ft, bottom_ft,
                          sacks_required, cement_class, formation_name
            actual_events: List of actual event dicts (from DWR parser or C103EventORM).
                Each has: event_type, depth_top_ft, depth_bottom_ft,
                          sacks, cement_class, tagged_depth_ft
            api_number: Well API number.
            c103_form_id: C103 form ID.

        Returns:
            ReconciliationResult with side-by-side comparisons.
        """
        result = ReconciliationResult(
            api_number=api_number,
            c103_form_id=c103_form_id,
        )

        # Filter actual events to plug-placement types only
        plug_events = [
            ev for ev in actual_events
            if ev.get("event_type") in PLUG_PLACEMENT_EVENT_TYPES
        ]

        result.total_planned = len(planned_plugs)
        result.total_actual = len(plug_events)

        logger.info(
            "PlugReconciliationEngine.reconcile: api=%s planned=%d actual=%d",
            api_number,
            result.total_planned,
            result.total_actual,
        )

        # Match planned plugs to actual events
        matched_pairs = self._match_plugs(planned_plugs, plug_events)

        plug_counter = 0
        for planned, actual in matched_pairs:
            plug_counter += 1

            # Derive plug number: prefer planned's plug_number if available
            if planned is not None:
                plug_num = planned.get("plug_number", plug_counter)
            else:
                plug_num = plug_counter

            comparison = self._compare_plug(planned, actual, plug_num)
            result.comparisons.append(comparison)

        # Sort comparisons by depth, deepest first (descending)
        def _sort_depth(c):
            """Use the best available depth (planned or actual midpoint), deepest first."""
            planned_mid = _midpoint(c.planned_top_ft, c.planned_bottom_ft)
            actual_mid = _midpoint(c.actual_top_ft, c.actual_bottom_ft)
            depth = planned_mid if planned_mid is not None else actual_mid
            # Negate so deepest sorts first; None → 0 so they sort last
            return -(depth if depth is not None else 0)

        result.comparisons.sort(key=_sort_depth)

        # Renumber plugs sequentially after depth sort
        for idx, comp in enumerate(result.comparisons, start=1):
            comp.plug_number = idx

        self._calculate_summary(result)
        result.summary_narrative = self._generate_summary_narrative(result)

        return result

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _match_plugs(self, planned: list, actuals: list) -> List[Tuple]:
        """Match planned plugs to actual events using type-aware two-pass algorithm.

        Pass 1 — Type-compatible depth match:
            For each planned plug (deepest first), find the closest unmatched
            actual event that is BOTH within a generous depth window AND has a
            compatible operation type.

        Pass 2 — Depth-only fallback:
            Any remaining planned plugs try to match remaining actuals by depth
            alone, but only if the distance is within 5× the base tolerance.
            Incompatible types (casing_cut vs plug events) are still excluded.

        Unmatched planned → (planned, None)  [MISSING]
        Unmatched actuals → (None, actual)   [ADDED]
        """
        # Sort deepest first; items without a midpoint go last
        sorted_planned = sorted(
            planned,
            key=lambda p: _get_planned_midpoint(p) or 0.0,
            reverse=True,
        )
        sorted_actuals = sorted(
            actuals,
            key=lambda a: _get_actual_midpoint(a) or 0.0,
            reverse=True,
        )

        used_actual_indices: set = set()
        matched_planned_indices: set = set()
        pairs: List[Tuple] = []

        # Max distance for type-compatible match (generous)
        max_compat_dist = self.depth_tolerance * 25  # 500 ft

        # ----------------------------------------------------------
        # Pass 1: Type-compatible + depth proximity
        # ----------------------------------------------------------
        for p_idx, plan_item in enumerate(sorted_planned):
            plan_mid = _get_planned_midpoint(plan_item)
            plan_type = (plan_item.get("plug_type") or plan_item.get("step_type") or "").lower()

            # Skip items that can never match (e.g., casing_cut)
            if plan_type in TYPE_COMPATIBILITY and not TYPE_COMPATIBILITY[plan_type]:
                continue

            if plan_mid is None:
                continue

            best_idx: Optional[int] = None
            best_dist: float = float("inf")

            for a_idx, actual_item in enumerate(sorted_actuals):
                if a_idx in used_actual_indices:
                    continue
                act_mid = _get_actual_midpoint(actual_item)
                if act_mid is None:
                    continue
                act_type = (actual_item.get("event_type") or "").lower()

                if not _types_compatible(plan_type, act_type):
                    continue

                dist = abs(plan_mid - act_mid)
                if dist < best_dist and dist <= max_compat_dist:
                    best_dist = dist
                    best_idx = a_idx

            if best_idx is not None:
                pairs.append((plan_item, sorted_actuals[best_idx]))
                used_actual_indices.add(best_idx)
                matched_planned_indices.add(p_idx)

        # ----------------------------------------------------------
        # Pass 2: Depth-only fallback for remaining planned (with distance cap)
        # ----------------------------------------------------------
        max_fallback_dist = self.depth_tolerance * 5  # 100 ft

        for p_idx, plan_item in enumerate(sorted_planned):
            if p_idx in matched_planned_indices:
                continue

            plan_mid = _get_planned_midpoint(plan_item)
            plan_type = (plan_item.get("plug_type") or plan_item.get("step_type") or "").lower()

            # casing_cut never matches plug events
            if plan_type in TYPE_COMPATIBILITY and not TYPE_COMPATIBILITY[plan_type]:
                pairs.append((plan_item, None))
                matched_planned_indices.add(p_idx)
                continue

            if plan_mid is None:
                pairs.append((plan_item, None))
                matched_planned_indices.add(p_idx)
                continue

            best_idx: Optional[int] = None
            best_dist: float = float("inf")

            for a_idx, actual_item in enumerate(sorted_actuals):
                if a_idx in used_actual_indices:
                    continue
                act_mid = _get_actual_midpoint(actual_item)
                if act_mid is None:
                    continue

                dist = abs(plan_mid - act_mid)
                if dist < best_dist and dist <= max_fallback_dist:
                    best_dist = dist
                    best_idx = a_idx

            if best_idx is not None:
                pairs.append((plan_item, sorted_actuals[best_idx]))
                used_actual_indices.add(best_idx)
            else:
                pairs.append((plan_item, None))

            matched_planned_indices.add(p_idx)

        # ----------------------------------------------------------
        # Remaining unmatched actuals → ADDED
        # ----------------------------------------------------------
        for a_idx, actual_item in enumerate(sorted_actuals):
            if a_idx not in used_actual_indices:
                pairs.append((None, actual_item))

        return pairs

    # ------------------------------------------------------------------
    # Per-plug comparison
    # ------------------------------------------------------------------

    def _compare_plug(
        self,
        planned: Optional[dict],
        actual: Optional[dict],
        plug_number: int,
    ) -> PlugComparison:
        """Compare a single planned plug with its matched actual event."""
        comparison = PlugComparison(plug_number=plug_number)

        # Populate planned fields
        if planned is not None:
            comparison.planned_type = planned.get("plug_type")
            comparison.planned_top_ft = planned.get("top_ft")
            comparison.planned_bottom_ft = planned.get("bottom_ft")
            comparison.planned_sacks = planned.get("sacks")
            comparison.planned_cement_class = planned.get("cement_class") or None
            comparison.planned_formation = planned.get("formation") or None

        # Populate actual fields
        if actual is not None:
            comparison.actual_type = actual.get("event_type")
            comparison.actual_top_ft = actual.get("depth_top_ft")
            comparison.actual_bottom_ft = actual.get("depth_bottom_ft")
            comparison.actual_sacks = actual.get("sacks")
            raw_class = actual.get("cement_class")
            comparison.actual_cement_class = raw_class if raw_class else None
            comparison.actual_tagged_depth_ft = actual.get("tagged_depth_ft")
            comparison.actual_placement_method = actual.get("placement_method") or None
            comparison.actual_woc_hours = actual.get("woc_hours") or None
            actual_woc_tagged = actual.get("woc_tagged")
            comparison.actual_woc_tagged = bool(actual_woc_tagged) if actual_woc_tagged is not None else None

        # Populate planned placement method if available from planned dict
        if planned is not None:
            comparison.planned_placement_method = planned.get("placement_method") or None
            comparison.planned_woc_hours = planned.get("woc_hours") or None

        # Determine deviation level
        if planned is None and actual is not None:
            comparison.deviation_level = DeviationLevel.ADDED
            mid = _get_actual_midpoint(actual)
            depth_label = f"~{mid:.0f}'" if mid is not None else "unknown depth"
            comparison.deviation_notes.append(
                f"Actual plug at {depth_label} "
                "was not in the original NOI plan."
            )
            return comparison

        if planned is not None and actual is None:
            comparison.deviation_level = DeviationLevel.MISSING
            mid = _get_planned_midpoint(planned)
            depth_label = f"~{mid:.0f}'" if mid is not None else "unknown depth"
            comparison.deviation_notes.append(
                f"Planned plug at {depth_label} "
                "was not found in the DWR actuals."
            )
            return comparison

        # Both present — assess individual deviations and roll up to worst level
        worst_level = DeviationLevel.MATCH

        # Depth deviation
        depth_level, depth_dev_ft, depth_notes = self._assess_depth_deviation(
            planned_top=comparison.planned_top_ft,
            planned_bottom=comparison.planned_bottom_ft,
            actual_top=comparison.actual_top_ft,
            actual_bottom=comparison.actual_bottom_ft,
            actual_tagged=comparison.actual_tagged_depth_ft,
        )
        if depth_dev_ft is not None:
            comparison.depth_deviation_ft = depth_dev_ft
        comparison.deviation_notes.extend(depth_notes)
        worst_level = _escalate(worst_level, depth_level)

        # Sack deviation
        if comparison.planned_sacks is not None and comparison.actual_sacks is not None:
            sack_level, sack_dev_pct, sack_notes = self._assess_sack_deviation(
                planned=comparison.planned_sacks,
                actual=comparison.actual_sacks,
            )
            comparison.sack_deviation_pct = sack_dev_pct
            comparison.deviation_notes.extend(sack_notes)
            worst_level = _escalate(worst_level, sack_level)
        elif comparison.planned_sacks is not None and comparison.actual_sacks is None:
            comparison.deviation_notes.append("Actual sack count not recorded in DWR.")

        # Cement class deviation
        if comparison.planned_cement_class and comparison.actual_cement_class:
            class_level, class_notes = self._assess_cement_class_deviation(
                planned=comparison.planned_cement_class,
                actual=comparison.actual_cement_class,
            )
            comparison.deviation_notes.extend(class_notes)
            worst_level = _escalate(worst_level, class_level)

        # Placement method comparison
        if comparison.planned_placement_method and comparison.actual_placement_method:
            if comparison.planned_placement_method != comparison.actual_placement_method:
                comparison.deviation_notes.append(
                    f"Method: planned {comparison.planned_placement_method} "
                    f"vs actual {comparison.actual_placement_method}"
                )
                worst_level = _escalate(worst_level, DeviationLevel.MINOR)

        # WOC check
        if (
            comparison.actual_woc_hours is not None
            and comparison.planned_woc_hours is not None
            and comparison.actual_woc_hours < comparison.planned_woc_hours
        ):
            comparison.deviation_notes.append(
                f"WOC {comparison.actual_woc_hours}h < planned {comparison.planned_woc_hours}h"
            )
            worst_level = _escalate(worst_level, DeviationLevel.MINOR)

        comparison.deviation_level = worst_level
        return comparison

    # ------------------------------------------------------------------
    # Individual assessment helpers
    # ------------------------------------------------------------------

    def _assess_depth_deviation(
        self,
        planned_top: Optional[float],
        planned_bottom: Optional[float],
        actual_top: Optional[float],
        actual_bottom: Optional[float],
        actual_tagged: Optional[float] = None,
    ) -> Tuple[DeviationLevel, Optional[float], List[str]]:
        """Assess depth deviation between planned and actual.

        Compares midpoint depths. Within ±20' = MATCH, otherwise MINOR or MAJOR.
        If an actual tagged depth is available it is used as the reference for
        the top of the actual plug (tagged depth indicates confirmed cement top).

        Returns:
            (DeviationLevel, deviation_ft_or_None, notes_list)
        """
        notes: List[str] = []

        planned_mid = _midpoint(planned_top, planned_bottom)
        actual_mid = _midpoint(actual_top, actual_bottom)

        if planned_mid is None or actual_mid is None:
            return DeviationLevel.MATCH, None, notes

        deviation_ft = abs(planned_mid - actual_mid)

        # If we have a tagged depth, also check top-of-plug alignment
        if actual_tagged is not None and planned_top is not None:
            tag_deviation = abs(actual_tagged - planned_top)
            if tag_deviation > deviation_ft:
                notes.append(
                    f"Tagged depth {actual_tagged:,.0f}' deviates "
                    f"{tag_deviation:.0f}' from planned top {planned_top:,.0f}'."
                )

        if deviation_ft <= self.depth_tolerance:
            if deviation_ft > 0:
                notes.append(
                    f"Depth within tolerance: midpoint deviation {deviation_ft:.0f}' "
                    f"(tolerance ±{self.depth_tolerance:.0f}')."
                )
            return DeviationLevel.MATCH, deviation_ft, notes

        # Outside tolerance
        if deviation_ft <= self.depth_tolerance * 5:
            level = DeviationLevel.MINOR
        else:
            level = DeviationLevel.MAJOR

        direction = "deeper" if actual_mid > planned_mid else "shallower"
        notes.append(
            f"Depth deviation: actual midpoint {actual_mid:,.0f}' is "
            f"{deviation_ft:.0f}' {direction} than planned midpoint {planned_mid:,.0f}'."
        )
        return level, deviation_ft, notes

    def _assess_sack_deviation(
        self,
        planned: float,
        actual: float,
    ) -> Tuple[DeviationLevel, float, List[str]]:
        """Assess sack count deviation. Within ±10% = MATCH.

        Returns:
            (DeviationLevel, deviation_pct, notes_list)
        """
        notes: List[str] = []

        if planned == 0:
            # Avoid divide-by-zero; any actual usage is notable
            if actual > 0:
                notes.append(
                    f"Planned 0 sacks but {actual:.0f} sacks used in field."
                )
                return DeviationLevel.MINOR, 100.0, notes
            return DeviationLevel.MATCH, 0.0, notes

        deviation_pct = abs(actual - planned) / planned

        if deviation_pct <= self.sack_tolerance:
            if deviation_pct > 0:
                notes.append(
                    f"Sack count within tolerance: {actual:.0f} vs planned {planned:.0f} "
                    f"({deviation_pct * 100:.1f}% deviation, tolerance ±{self.sack_tolerance * 100:.0f}%)."
                )
            return DeviationLevel.MATCH, deviation_pct, notes

        direction = "over" if actual > planned else "under"
        notes.append(
            f"Sack count {direction}: {actual:.0f} sacks used vs {planned:.0f} planned "
            f"({deviation_pct * 100:.1f}% deviation)."
        )

        if deviation_pct <= self.sack_tolerance * 3:
            return DeviationLevel.MINOR, deviation_pct, notes
        return DeviationLevel.MAJOR, deviation_pct, notes

    def _assess_cement_class_deviation(
        self,
        planned: str,
        actual: str,
    ) -> Tuple[DeviationLevel, List[str]]:
        """Cement class mismatch is always a MAJOR deviation.

        Returns:
            (DeviationLevel, notes_list)
        """
        notes: List[str] = []

        p = planned.upper().strip() if planned else ""
        a = actual.upper().strip() if actual else ""

        if p == a:
            return DeviationLevel.MATCH, notes

        notes.append(
            f"Cement class mismatch: planned Class {p}, actual Class {a}. "
            "Cement class substitution requires engineering justification."
        )
        return DeviationLevel.MAJOR, notes

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _calculate_summary(self, result: ReconciliationResult) -> None:
        """Calculate summary counts and overall status from comparisons."""
        matches = 0
        minor = 0
        major = 0
        added = 0
        missing = 0

        for comp in result.comparisons:
            level = comp.deviation_level
            if level == DeviationLevel.MATCH:
                matches += 1
            elif level == DeviationLevel.MINOR:
                minor += 1
            elif level == DeviationLevel.MAJOR:
                major += 1
            elif level == DeviationLevel.ADDED:
                added += 1
            elif level == DeviationLevel.MISSING:
                missing += 1

        result.matches = matches
        result.minor_deviations = minor
        result.major_deviations = major
        result.added_plugs = added
        result.missing_plugs = missing

        if major > 0 or missing > 0:
            result.overall_status = "major_deviations"
        elif minor > 0 or added > 0:
            result.overall_status = "minor_deviations"
        else:
            result.overall_status = "compliant"

    def _generate_summary_narrative(self, result: ReconciliationResult) -> str:
        """Generate human-readable summary of reconciliation."""
        lines: List[str] = []

        lines.append(
            f"Plug reconciliation for well {result.api_number}: "
            f"{result.total_planned} planned vs {result.total_actual} actual plug operations."
        )

        if result.overall_status == "compliant":
            lines.append(
                f"All {result.matches} plug(s) placed within tolerance. "
                "Field operations are compliant with the NOI plan."
            )
        else:
            parts: List[str] = []
            if result.matches:
                parts.append(f"{result.matches} compliant")
            if result.minor_deviations:
                parts.append(f"{result.minor_deviations} minor deviation(s)")
            if result.major_deviations:
                parts.append(f"{result.major_deviations} major deviation(s)")
            if result.missing_plugs:
                parts.append(f"{result.missing_plugs} missing plug(s)")
            if result.added_plugs:
                parts.append(f"{result.added_plugs} added plug(s) not in plan")
            lines.append("Summary: " + ", ".join(parts) + ".")

        if result.major_deviations or result.missing_plugs:
            lines.append(
                "Action required: major deviations and/or missing plugs must be explained "
                "in the subsequent report narrative before submission to NMOCD."
            )
        elif result.minor_deviations or result.added_plugs:
            lines.append(
                "Note: minor deviations and/or added plugs should be documented "
                "in the subsequent report narrative."
            )

        return " ".join(lines)

    @staticmethod
    def search_variance_approvals(well, comparison: PlugComparison) -> PlugComparison:
        """Search well's ExtractedDocument records for sundry notices or amended W-3As
        that could justify a depth/procedure deviation."""
        try:
            if well is None:
                return comparison

            from apps.public_core.models import ExtractedDocument

            docs = ExtractedDocument.objects.filter(
                well=well,
                document_type__in=("sundry", "w3a_amendment", "variance_approval"),
            )
            if not docs.exists():
                return comparison

            plug_num = comparison.plug_number
            top = comparison.planned_top_ft
            bottom = comparison.planned_bottom_ft

            for doc in docs:
                data = doc.extracted_data or {}

                # Check if the document references this plug number or depth range
                doc_text = str(data).lower()
                plug_match = f"plug {plug_num}" in doc_text or f"plug_{plug_num}" in doc_text

                depth_match = False
                if top is not None or bottom is not None:
                    for depth in [top, bottom]:
                        if depth is not None and str(int(depth)) in doc_text:
                            depth_match = True
                            break

                if plug_match or depth_match:
                    comparison.variance_approval_found = True
                    ref = getattr(doc, "reference_number", None) or str(doc.pk)
                    comparison.variance_approval_reference = (
                        f"{doc.doc_type}:{ref}"
                    )
                    logger.info(
                        "search_variance_approvals: found approval doc %s for plug %s on well %s",
                        ref,
                        plug_num,
                        well,
                    )
                    break

        except Exception:
            logger.exception(
                "search_variance_approvals: error searching docs for plug %s",
                comparison.plug_number,
            )

        return comparison


# ------------------------------------------------------------------
# Module-level helper
# ------------------------------------------------------------------

_DEVIATION_ORDER = [
    DeviationLevel.MATCH,
    DeviationLevel.MINOR,
    DeviationLevel.MAJOR,
]


def _escalate(current: DeviationLevel, candidate: DeviationLevel) -> DeviationLevel:
    """Return the more severe of two deviation levels.

    ADDED and MISSING are handled separately and should not be escalated into
    via this helper (they are set directly on comparisons with one-sided data).
    """
    try:
        if _DEVIATION_ORDER.index(candidate) > _DEVIATION_ORDER.index(current):
            return candidate
    except ValueError:
        pass
    return current
