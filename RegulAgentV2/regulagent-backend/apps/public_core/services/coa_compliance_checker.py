"""
COA Compliance Checker

Validates as-executed plugging operations against 7 regulatory compliance rules.
Reports pass/fail per rule with details.

No Django model dependencies — operates on pure dict inputs for easy unit testing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RuleStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    SKIPPED = "skipped"


@dataclass
class RuleResult:
    rule_id: str
    rule_label: str
    status: RuleStatus
    detail: str
    applicable_plugs: List[int] = field(default_factory=list)
    data_source: str = ""


@dataclass
class ComplianceResult:
    api_number: str
    rules_checked: int
    passed: int
    failed: int
    warnings: int
    skipped: int
    rule_results: List[RuleResult] = field(default_factory=list)
    overall_status: str = ""
    narrative: str = ""


def _safe_float(val: Any) -> Optional[float]:
    """Safely convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _get_plugs_from_reconciliation(reconciliation_result: dict) -> List[Dict[str, Any]]:
    """Extract actual plug data from reconciliation comparisons.

    Reconciliation comparisons use flat keys (actual_top_ft, actual_bottom_ft,
    planned_top_ft, etc.) rather than nested dicts. We normalize into a
    consistent structure for the rule checkers.
    """
    plugs = []
    for comp in reconciliation_result.get("comparisons", []):
        if not isinstance(comp, dict):
            continue
        # Skip milestones
        if comp.get("comparison_type") == "milestone":
            continue
        plugs.append({
            "plug_number": comp.get("plug_number"),
            "plug_type": comp.get("actual_type") or comp.get("planned_type", ""),
            "step_type": comp.get("actual_type") or comp.get("planned_type", ""),
            "deviation_level": comp.get("deviation_level"),
            # Actual data (flat keys from reconciliation)
            "depth_top_ft": comp.get("actual_top_ft") or comp.get("planned_top_ft"),
            "depth_bottom_ft": comp.get("actual_bottom_ft") or comp.get("planned_bottom_ft"),
            "top_ft": comp.get("actual_top_ft") or comp.get("planned_top_ft"),
            "bottom_ft": comp.get("actual_bottom_ft") or comp.get("planned_bottom_ft"),
            "cement_class": comp.get("actual_cement_class") or comp.get("planned_cement_class"),
            "sacks": comp.get("actual_sacks") or comp.get("planned_sacks"),
            "woc_hours": comp.get("actual_woc_hours"),
            "woc_tagged": comp.get("actual_woc_tagged"),
            "tagged_depth_ft": comp.get("actual_tagged_depth_ft"),
            "placement_method": comp.get("actual_placement_method"),
        })
    return plugs


# -----------------------------------------------------------------------
# Rule 1: Cement class vs depth
# -----------------------------------------------------------------------
def _r1_cement_class_depth(plugs: List[Dict[str, Any]]) -> RuleResult:
    """Check cement class is appropriate for depth.

    Class A/C acceptable for shallow (< 6000 ft).
    Class G/H required for deep (>= 6000 ft).
    """
    violations = []
    applicable = []
    shallow_classes = {"A", "C", "G", "H"}  # All OK for shallow
    deep_classes = {"G", "H"}  # Only these for deep

    for plug in plugs:
        plug_num = plug.get("plug_number")
        cement_class = None

        cement_class = plug.get("cement_class")
        depth = _safe_float(plug.get("depth_bottom_ft") or plug.get("bottom_ft"))

        if cement_class and depth is not None:
            applicable.append(plug_num)
            cc = cement_class.upper().strip()
            if depth >= 6000 and cc not in deep_classes:
                violations.append(
                    f"Plug #{plug_num}: Class {cc} at {depth}' (need G/H for ≥6000')"
                )
            elif cc not in shallow_classes:
                violations.append(
                    f"Plug #{plug_num}: Unrecognized cement class '{cc}'"
                )

    if not applicable:
        return RuleResult(
            rule_id="r1_cement_class_depth",
            rule_label="Cement Class vs Depth",
            status=RuleStatus.SKIPPED,
            detail="No plugs with both cement class and depth data",
            applicable_plugs=[],
            data_source="reconciliation",
        )

    if violations:
        return RuleResult(
            rule_id="r1_cement_class_depth",
            rule_label="Cement Class vs Depth",
            status=RuleStatus.FAIL,
            detail="; ".join(violations),
            applicable_plugs=applicable,
            data_source="reconciliation",
        )

    return RuleResult(
        rule_id="r1_cement_class_depth",
        rule_label="Cement Class vs Depth",
        status=RuleStatus.PASS,
        detail=f"All {len(applicable)} plug(s) have appropriate cement class for depth",
        applicable_plugs=applicable,
        data_source="reconciliation",
    )


# -----------------------------------------------------------------------
# Rule 2: Minimum plug length
# -----------------------------------------------------------------------
def _r2_min_plug_length(plugs: List[Dict[str, Any]]) -> RuleResult:
    """Each cement plug must be ≥ 50 ft. CIBP exempt."""
    violations = []
    applicable = []

    for plug in plugs:
        plug_num = plug.get("plug_number")
        plug_type = (plug.get("plug_type") or plug.get("step_type") or "").lower()

        # CIBP (bridge plugs) exempt from length check
        if "cibp" in plug_type or "bridge" in plug_type:
            continue

        top = _safe_float(plug.get("depth_top_ft") or plug.get("top_ft"))
        bottom = _safe_float(plug.get("depth_bottom_ft") or plug.get("bottom_ft"))

        if top is not None and bottom is not None:
            length = abs(bottom - top)
            applicable.append(plug_num)
            if length < 50:
                violations.append(
                    f"Plug #{plug_num}: {length:.0f}' (minimum 50')"
                )

    if not applicable:
        return RuleResult(
            rule_id="r2_min_plug_length",
            rule_label="Minimum Plug Length (≥50 ft)",
            status=RuleStatus.SKIPPED,
            detail="No cement plugs with depth data to evaluate",
            applicable_plugs=[],
            data_source="reconciliation",
        )

    if violations:
        return RuleResult(
            rule_id="r2_min_plug_length",
            rule_label="Minimum Plug Length (≥50 ft)",
            status=RuleStatus.FAIL,
            detail="; ".join(violations),
            applicable_plugs=applicable,
            data_source="reconciliation",
        )

    return RuleResult(
        rule_id="r2_min_plug_length",
        rule_label="Minimum Plug Length (≥50 ft)",
        status=RuleStatus.PASS,
        detail=f"All {len(applicable)} cement plug(s) meet minimum 50' length",
        applicable_plugs=applicable,
        data_source="reconciliation",
    )


# -----------------------------------------------------------------------
# Rule 3: WOC time
# -----------------------------------------------------------------------
def _r3_woc_time(plugs: List[Dict[str, Any]], parse_result: dict) -> RuleResult:
    """Wait on cement ≥ 8 hours before tag."""
    violations = []
    applicable = []

    for plug in plugs:
        plug_num = plug.get("plug_number")
        woc_hours = _safe_float(plug.get("woc_hours"))

        if woc_hours is not None:
            applicable.append(plug_num)
            if woc_hours < 8:
                violations.append(
                    f"Plug #{plug_num}: WOC {woc_hours:.1f}hrs (minimum 8hrs)"
                )

    if not applicable:
        return RuleResult(
            rule_id="r3_woc_time",
            rule_label="Wait on Cement (≥8 hrs)",
            status=RuleStatus.WARNING,
            detail="No WOC time data found — cannot verify",
            applicable_plugs=[],
            data_source="parse_result",
        )

    if violations:
        return RuleResult(
            rule_id="r3_woc_time",
            rule_label="Wait on Cement (≥8 hrs)",
            status=RuleStatus.FAIL,
            detail="; ".join(violations),
            applicable_plugs=applicable,
            data_source="parse_result",
        )

    return RuleResult(
        rule_id="r3_woc_time",
        rule_label="Wait on Cement (≥8 hrs)",
        status=RuleStatus.PASS,
        detail=f"All {len(applicable)} plug(s) have ≥8hr WOC",
        applicable_plugs=applicable,
        data_source="parse_result",
    )


# -----------------------------------------------------------------------
# Rule 4: Tag requirement
# -----------------------------------------------------------------------
def _r4_tag_requirement(plugs: List[Dict[str, Any]]) -> RuleResult:
    """All cement plugs must be tagged. Surface/topoff exempt."""
    violations = []
    applicable = []

    for plug in plugs:
        plug_num = plug.get("plug_number")
        plug_type = (plug.get("plug_type") or plug.get("step_type") or "").lower()

        # Surface plugs and topoffs are exempt from tagging
        if any(kw in plug_type for kw in ("surface", "topoff", "top_off")):
            continue
        # CIBP exempt
        if "cibp" in plug_type or "bridge" in plug_type:
            continue

        tagged = (
            plug.get("woc_tagged") is True
            or plug.get("tagged_depth_ft") is not None
        )

        applicable.append(plug_num)
        if not tagged:
            violations.append(f"Plug #{plug_num}: Not tagged")

    if not applicable:
        return RuleResult(
            rule_id="r4_tag_requirement",
            rule_label="Tag Requirement",
            status=RuleStatus.SKIPPED,
            detail="No taggable plugs found",
            applicable_plugs=[],
            data_source="reconciliation",
        )

    if violations:
        return RuleResult(
            rule_id="r4_tag_requirement",
            rule_label="Tag Requirement",
            status=RuleStatus.WARNING,
            detail="; ".join(violations),
            applicable_plugs=applicable,
            data_source="reconciliation",
        )

    return RuleResult(
        rule_id="r4_tag_requirement",
        rule_label="Tag Requirement",
        status=RuleStatus.PASS,
        detail=f"All {len(applicable)} applicable plug(s) tagged",
        applicable_plugs=applicable,
        data_source="reconciliation",
    )


# -----------------------------------------------------------------------
# Rule 5: Formation tops placement
# -----------------------------------------------------------------------
def _r5_formation_tops(
    plugs: List[Dict[str, Any]],
    formation_audit: dict,
) -> RuleResult:
    """Plug within 100ft of each formation top."""
    requirements = formation_audit.get("requirements", [])
    if not requirements:
        return RuleResult(
            rule_id="r5_formation_tops",
            rule_label="Formation Tops Placement",
            status=RuleStatus.SKIPPED,
            detail="No formation audit data available",
            applicable_plugs=[],
            data_source="formation_audit",
        )

    unsatisfied = [
        r for r in requirements
        if isinstance(r, dict) and r.get("status") in ("unsatisfied", "insufficient")
    ]

    if unsatisfied:
        details = [
            f"{r.get('label', 'Unknown')}: {r.get('notes', '')}"
            for r in unsatisfied
        ]
        return RuleResult(
            rule_id="r5_formation_tops",
            rule_label="Formation Tops Placement",
            status=RuleStatus.FAIL,
            detail="; ".join(details),
            applicable_plugs=[],
            data_source="formation_audit",
        )

    return RuleResult(
        rule_id="r5_formation_tops",
        rule_label="Formation Tops Placement",
        status=RuleStatus.PASS,
        detail=f"All {len(requirements)} formation requirements satisfied",
        applicable_plugs=[],
        data_source="formation_audit",
    )


# -----------------------------------------------------------------------
# Rule 6: Max plug spacing
# -----------------------------------------------------------------------
def _r6_max_spacing(plugs: List[Dict[str, Any]]) -> RuleResult:
    """No gap > 1000 ft without shoe/formation between plugs."""
    # Collect all plug depths, sorted by depth (shallowest first)
    depths = []
    for plug in plugs:
        top = _safe_float(plug.get("depth_top_ft") or plug.get("top_ft"))
        bottom = _safe_float(plug.get("depth_bottom_ft") or plug.get("bottom_ft"))

        if top is not None and bottom is not None:
            depths.append((min(top, bottom), max(top, bottom), plug.get("plug_number")))

    if len(depths) < 2:
        return RuleResult(
            rule_id="r6_max_spacing",
            rule_label="Maximum Plug Spacing",
            status=RuleStatus.SKIPPED,
            detail="Fewer than 2 plugs with depth data — cannot check spacing",
            applicable_plugs=[],
            data_source="reconciliation",
        )

    # Sort by top depth (shallowest first)
    depths.sort(key=lambda x: x[0])

    violations = []
    for i in range(len(depths) - 1):
        gap = depths[i + 1][0] - depths[i][1]  # top of next - bottom of current
        if gap > 1000:
            violations.append(
                f"Gap of {gap:.0f}' between Plug #{depths[i][2]} "
                f"(bottom {depths[i][1]:.0f}') and Plug #{depths[i + 1][2]} "
                f"(top {depths[i + 1][0]:.0f}')"
            )

    if violations:
        return RuleResult(
            rule_id="r6_max_spacing",
            rule_label="Maximum Plug Spacing",
            status=RuleStatus.WARNING,
            detail="; ".join(violations),
            applicable_plugs=[d[2] for d in depths],
            data_source="reconciliation",
        )

    return RuleResult(
        rule_id="r6_max_spacing",
        rule_label="Maximum Plug Spacing",
        status=RuleStatus.PASS,
        detail=f"All gaps between {len(depths)} plugs are ≤1000'",
        applicable_plugs=[d[2] for d in depths],
        data_source="reconciliation",
    )


# -----------------------------------------------------------------------
# Rule 7: Surface plug depth
# -----------------------------------------------------------------------
def _r7_surface_plug(plugs: List[Dict[str, Any]]) -> RuleResult:
    """Surface plug top must be ≤ 50 ft from surface."""
    surface_plugs = []
    for plug in plugs:
        plug_type = (plug.get("plug_type") or plug.get("step_type") or "").lower()
        if "surface" in plug_type or "topoff" in plug_type or "top_off" in plug_type:
            surface_plugs.append(plug)

    if not surface_plugs:
        # Check if any plug has a top near surface
        for plug in plugs:
            top = _safe_float(plug.get("depth_top_ft") or plug.get("top_ft"))
            if top is not None and top <= 50:
                surface_plugs.append(plug)

    if not surface_plugs:
        return RuleResult(
            rule_id="r7_surface_plug",
            rule_label="Surface Plug Depth",
            status=RuleStatus.FAIL,
            detail="No surface plug found (top ≤ 50' from surface required)",
            applicable_plugs=[],
            data_source="reconciliation",
        )

    # Check that at least one surface plug has top ≤ 50'
    for plug in surface_plugs:
        top = _safe_float(plug.get("depth_top_ft") or plug.get("top_ft"))

        if top is not None and top <= 50:
            return RuleResult(
                rule_id="r7_surface_plug",
                rule_label="Surface Plug Depth",
                status=RuleStatus.PASS,
                detail=f"Surface plug top at {top:.0f}' (≤50' requirement met)",
                applicable_plugs=[plug.get("plug_number")],
                data_source="reconciliation",
            )

    return RuleResult(
        rule_id="r7_surface_plug",
        rule_label="Surface Plug Depth",
        status=RuleStatus.WARNING,
        detail="Surface plug found but top depth not confirmed ≤50'",
        applicable_plugs=[p.get("plug_number") for p in surface_plugs],
        data_source="reconciliation",
    )


# -----------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------

def check(
    reconciliation_result: dict,
    parse_result: dict,
    formation_audit: dict,
    payload: dict,
    api_number: str = "",
) -> ComplianceResult:
    """Run all 7 COA compliance rules.

    Args:
        reconciliation_result: Full reconciliation result dict
        parse_result: Full DWR parse result dict
        formation_audit: Formation audit result dict (from FormationIsolationAuditor)
        payload: Plan snapshot payload dict
        api_number: Well API number

    Returns:
        ComplianceResult with per-rule status.
    """
    plugs = _get_plugs_from_reconciliation(reconciliation_result)

    rules = [
        _r1_cement_class_depth(plugs),
        _r2_min_plug_length(plugs),
        _r3_woc_time(plugs, parse_result),
        _r4_tag_requirement(plugs),
        _r5_formation_tops(plugs, formation_audit),
        _r6_max_spacing(plugs),
        _r7_surface_plug(plugs),
    ]

    passed = sum(1 for r in rules if r.status == RuleStatus.PASS)
    failed = sum(1 for r in rules if r.status == RuleStatus.FAIL)
    warnings = sum(1 for r in rules if r.status == RuleStatus.WARNING)
    skipped = sum(1 for r in rules if r.status == RuleStatus.SKIPPED)

    if failed > 0:
        overall = "non_compliant"
    elif warnings > 0:
        overall = "warnings"
    elif skipped == len(rules):
        overall = "insufficient_data"
    else:
        overall = "compliant"

    # Build narrative
    narrative_parts = [f"COA Compliance Check for {api_number or 'well'}:"]
    narrative_parts.append(f"  {len(rules)} rules checked: {passed} passed, {failed} failed, {warnings} warnings, {skipped} skipped")
    for r in rules:
        if r.status == RuleStatus.FAIL:
            narrative_parts.append(f"  FAIL: {r.rule_label} — {r.detail}")
        elif r.status == RuleStatus.WARNING:
            narrative_parts.append(f"  WARNING: {r.rule_label} — {r.detail}")

    result = ComplianceResult(
        api_number=api_number,
        rules_checked=len(rules),
        passed=passed,
        failed=failed,
        warnings=warnings,
        skipped=skipped,
        rule_results=rules,
        overall_status=overall,
        narrative="\n".join(narrative_parts),
    )

    logger.info(
        "coa_compliance_checker: api=%s passed=%d failed=%d warnings=%d skipped=%d status=%s",
        api_number, passed, failed, warnings, skipped, overall,
    )

    return result
