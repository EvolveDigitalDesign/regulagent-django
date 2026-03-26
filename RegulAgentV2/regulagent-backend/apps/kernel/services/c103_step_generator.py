"""C-103 step generator — bridges policy kernel to C103PluggingRules."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _extract_fact_value(facts: Dict[str, Any], key: str) -> Any:
    """Extract a fact value from the kernel resolved_facts dict.

    Kernel facts may be plain values or dicts with a "value" key.
    """
    raw = facts.get(key)
    if isinstance(raw, dict):
        return raw.get("value")
    return raw


def _build_well_dict(resolved_facts: Dict[str, Any]) -> Dict[str, Any]:
    """Map kernel resolved_facts to the well dict expected by C103PluggingRules."""
    well: Dict[str, Any] = {}

    # Well identification
    well["api_number"] = _extract_fact_value(resolved_facts, "api14") or _extract_fact_value(resolved_facts, "api_number")
    well["operator"] = _extract_fact_value(resolved_facts, "operator")
    well["lease_name"] = _extract_fact_value(resolved_facts, "lease_name")
    well["lease_type"] = _extract_fact_value(resolved_facts, "lease_type")
    well["field_name"] = _extract_fact_value(resolved_facts, "field")

    # Location
    well["county"] = _extract_fact_value(resolved_facts, "county")
    well["township"] = _extract_fact_value(resolved_facts, "township")
    well["range"] = _extract_fact_value(resolved_facts, "range")
    well["state"] = _extract_fact_value(resolved_facts, "state")

    # Well geometry
    total_depth = _extract_fact_value(resolved_facts, "total_depth_ft")
    if total_depth is not None:
        try:
            well["total_depth_ft"] = float(total_depth)
        except (ValueError, TypeError):
            pass

    duqw = _extract_fact_value(resolved_facts, "duqw_ft")
    if duqw is not None:
        try:
            well["duqw_ft"] = float(duqw)
        except (ValueError, TypeError):
            pass

    # Formation tops — accept both dict map and list formats
    formation_tops_map = _extract_fact_value(resolved_facts, "formation_tops_map")
    formation_tops = _extract_fact_value(resolved_facts, "formation_tops")
    if formation_tops_map and isinstance(formation_tops_map, dict):
        well["formation_tops"] = formation_tops_map
    elif formation_tops:
        well["formation_tops"] = formation_tops

    # Normalize formation_tops: convert dict format to list format if needed
    ft = well.get("formation_tops")
    if isinstance(ft, dict):
        well["formation_tops"] = [{"name": k, "depth_ft": v} for k, v in ft.items()]

    # Casing strings
    casing_strings = _extract_fact_value(resolved_facts, "casing_strings")
    if casing_strings:
        well["casing_strings"] = casing_strings

    # Perforations
    perforations = resolved_facts.get("perforations")
    if isinstance(perforations, list):
        well["perforations"] = perforations
    elif perforations is not None:
        perf_val = _extract_fact_value(resolved_facts, "perforations")
        if perf_val:
            well["perforations"] = perf_val

    # CBL data (optional — drives operation type classification)
    cbl_data = _extract_fact_value(resolved_facts, "cbl_data")
    if cbl_data:
        well["cbl_data"] = cbl_data

    return well


def _c103_plug_row_to_kernel_step(plug_row: Any) -> Dict[str, Any]:
    """Convert a C103PlugRow dataclass instance to a kernel step dict.

    Maps C103PlugRow fields to the step schema the frontend expects,
    mirroring the dict structure used by w3a_rules.generate_steps().
    """
    step: Dict[str, Any] = {
        "type": plug_row.step_type,
        "top_ft": plug_row.top_ft,
        "bottom_ft": plug_row.bottom_ft,
        "regulatory_basis": [plug_row.regulatory_basis] if plug_row.regulatory_basis else ["nmac.19.15.25"],
        "operation_type": plug_row.operation_type,
        "hole_type": plug_row.hole_type,
        "tag_required": plug_row.tag_required,
    }

    # Formation name for formation_plug and shoe_plug steps
    if plug_row.formation_name:
        step["formation"] = plug_row.formation_name

    # Casing size
    if plug_row.casing_size_in is not None:
        step["casing_id_in"] = plug_row.casing_size_in

    # Details sub-dict — carries NM-specific fields
    details: Dict[str, Any] = {
        "cement_class": plug_row.cement_class,
        "sacks_required": plug_row.sacks_required,
        "excess_factor": plug_row.excess_factor,
        "wait_hours": plug_row.wait_hours,
        "nmac_compliant": plug_row.nmac_compliant,
    }

    if plug_row.inside_sacks is not None:
        details["inside_sacks"] = plug_row.inside_sacks
    if plug_row.outside_sacks is not None:
        details["outside_sacks"] = plug_row.outside_sacks
    if plug_row.procedure_narrative:
        details["procedure_narrative"] = plug_row.procedure_narrative
    if plug_row.region_requirements:
        details["region_requirements"] = plug_row.region_requirements
    if plug_row.special_instructions:
        details["special_instructions"] = plug_row.special_instructions

    step["details"] = details

    # Add recipe for materials computation
    cement_class = plug_row.cement_class or "H"
    yield_map = {"A": 1.18, "B": 1.18, "C": 1.32, "G": 1.15, "H": 1.15}
    density_map = {"A": 15.6, "B": 15.6, "C": 14.8, "G": 15.8, "H": 16.4}
    water_map = {"A": 5.2, "B": 5.2, "C": 6.3, "G": 5.0, "H": 4.3}
    step["recipe"] = {
        "id": f"nm_class_{cement_class.lower()}",
        "class": cement_class,
        "density_ppg": density_map.get(cement_class, 16.4),
        "yield_ft3_per_sk": yield_map.get(cement_class, 1.15),
        "water_gal_per_sk": water_map.get(cement_class, 4.3),
        "additives": [],
    }

    return step


def generate_c103_steps(
    resolved_facts: Dict[str, Any],
    effective_policy: Dict[str, Any],
    formula_engine: Any = None,
) -> Dict[str, Any]:
    """Generate C-103 plugging plan steps from resolved kernel facts.

    Translates kernel fact format into well dict expected by C103PluggingRules,
    runs the rules engine, and converts output back to kernel step format.

    Args:
        resolved_facts: Kernel resolved facts dict with well data.
        effective_policy: Policy configuration from nm.c103 pack.
        formula_engine: NM formula engine instance (optional, unused currently).

    Returns:
        dict with "steps" list in kernel format and optional "violations" list.
    """
    from apps.kernel.services.c103_rules import C103PluggingRules
    from apps.policy.services.nm_region_rules import NMRegionRulesEngine

    result: Dict[str, Any] = {"steps": [], "violations": []}

    try:
        well_data = _build_well_dict(resolved_facts)

        county = well_data.get("county") or ""
        township = well_data.get("township") or ""
        range_ = well_data.get("range") or ""

        region_engine = NMRegionRulesEngine(
            county=county or None,
            township=township or None,
            range_=range_ or None,
        )

        # Build options from effective_policy (bailer_method, narrative, etc.)
        options: Dict[str, Any] = {}
        if effective_policy:
            prefs = effective_policy.get("preferences") or {}
            ops = (prefs.get("operational") or {}) if isinstance(prefs, dict) else {}
            bailer = ops.get("bailer_method")
            if bailer is not None:
                options["bailer_method"] = bool(bailer)

        rules = C103PluggingRules(region_engine=region_engine)
        plan = rules.generate_plugging_plan(well_data, options)

        steps: List[Dict[str, Any]] = []
        for plug_row in plan.steps:
            try:
                step = _c103_plug_row_to_kernel_step(plug_row)
                steps.append(step)
            except Exception:
                logger.exception("c103_step_generator: failed to convert plug row %r", plug_row)

        result["steps"] = steps

        logger.info(
            "c103_step_generator: generated %d steps for API %s (region=%s)",
            len(steps),
            well_data.get("api_number", "unknown"),
            plan.region,
        )

    except Exception:
        logger.exception("c103_step_generator: plan generation failed")
        result["violations"].append({
            "code": "c103_generation_failed",
            "severity": "error",
            "message": "C-103 step generation failed — check logs for details.",
        })

    return result
