from __future__ import annotations

from typing import Any, Dict, List

KERNEL_VERSION = "0.1.0"
from .w3a_rules import generate_steps as generate_w3a_steps


def _collect_constraints(policy: Dict[str, Any]) -> List[Dict[str, Any]]:
    constraints: List[Dict[str, Any]] = []
    if not policy.get("complete"):
        constraints.append({
            "code": "policy_incomplete",
            "severity": "error",
            "message": "Executable policy overlay is incomplete; required knobs are missing.",
            "missing": policy.get("incomplete_reasons", []),
        })
    return constraints


def plan_from_facts(resolved_facts: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic kernel entrypoint (stub).
    - If policy is incomplete, emit constraints and no steps.
    - When complete, this function will emit a compliant plan and steps with citations.
    """
    district = None
    if isinstance(resolved_facts.get("district"), dict):
        district = resolved_facts["district"].get("value")
    elif isinstance(resolved_facts.get("district"), str):
        district = resolved_facts.get("district")

    constraints = _collect_constraints(policy)
    is_complete = policy.get("complete", False)

    plan: Dict[str, Any] = {
        "kernel_version": KERNEL_VERSION,
        "overlay_version": policy.get("policy_version"),
        "jurisdiction": policy.get("jurisdiction"),
        "form": policy.get("form"),
        "district": district,
        "policy_complete": is_complete,
        "constraints": constraints,
        "steps": [],  # to be populated when policy is complete
        "citations": [],  # keys like tx.tac.16.3.14(b) will be added per-step
        "inputs_summary": {
            "api14": (resolved_facts.get("api14") or {}).get("value"),
            "state": (resolved_facts.get("state") or {}).get("value"),
        },
    }

    # Early return when policy is incomplete
    if not is_complete:
        return plan

    # Deterministic step generation (scaffold for W-3A)
    if is_complete and policy.get("policy_id") == "tx.w3a":
        generated = generate_w3a_steps(resolved_facts, policy.get("effective") or {})
        plan["violations"] = generated.get("violations", [])
        steps = generated.get("steps", [])
        steps = _dedup_step_citations(steps)
        # Apply default geometry/recipe from preferences when present
        plan_steps = _apply_step_defaults(steps, policy.get("preferences") if isinstance(policy.get("preferences"), dict) else {})
        # Apply district/county overrides (e.g., 08A tagging; 7C operational instructions)
        plan_steps = _apply_district_overrides(
            plan_steps,
            policy.get("effective") or {},
            district,
            policy.get("county"),
        )
        # Apply explicit step overrides provided by caller/payload (cap length, squeeze intervals, etc.)
        plan_steps = _apply_steps_overrides(
            plan_steps,
            policy.get("effective") or {},
            policy.get("preferences") or {},
        )
        plan["steps"] = plan_steps
        # If steps exist, compute materials
        if plan["steps"]:
            plan["steps"] = _compute_materials_for_steps(plan["steps"])  # type: ignore
        # plan-level rounding policy and safety stock
        rounding_pref = None
        try:
            prefs = policy.get("preferences") or {}
            rounding_pref = (prefs.get("rounding_policy") or "nearest") if isinstance(prefs, dict) else "nearest"
        except Exception:
            rounding_pref = "nearest"
        plan["rounding_policy"] = {"sacks": rounding_pref}
        plan["materials_policy"] = {"rounding": rounding_pref}
        plan["safety_stock_sacks"] = int(policy.get("preferences", {}).get("safety_stock_sacks", 0)) if isinstance(policy.get("preferences"), dict) else 0
    return plan


# --- Materials integration helpers (pure wiring; step schema to be formalized) ---
try:
    from apps.materials.services.material_engine import (
        annulus_capacity_bbl_per_ft,
        cylinder_capacity_bbl_per_ft,
        balanced_plug_bbl,
        bridge_plug_cap_bbl,
        squeeze_bbl,
        SlurryRecipe,
        compute_sacks,
        spacer_bbl_for_interval,
        balanced_displacement_bbl,
    )
except Exception:  # pragma: no cover - materials optional in early boot
    annulus_capacity_bbl_per_ft = None  # type: ignore
    cylinder_capacity_bbl_per_ft = None  # type: ignore
    balanced_plug_bbl = None  # type: ignore
    bridge_plug_cap_bbl = None  # type: ignore
    squeeze_bbl = None  # type: ignore
    SlurryRecipe = None  # type: ignore
    compute_sacks = None  # type: ignore
    spacer_bbl_for_interval = None  # type: no cover
    balanced_displacement_bbl = None  # type: ignore


def _compute_materials_for_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for step in steps:
        step_type = step.get("type")
        recipe_dict = step.get("recipe") or {}
        recipe = SlurryRecipe(
            recipe_id=recipe_dict.get("id", "unknown"),
            cement_class=recipe_dict.get("class", ""),
            density_ppg=float(recipe_dict.get("density_ppg", 0) or 0),
            yield_ft3_per_sk=float(recipe_dict.get("yield_ft3_per_sk", 0) or 0),
            water_gal_per_sk=float(recipe_dict.get("water_gal_per_sk", 0) or 0),
            additives=recipe_dict.get("additives", []) or [],
        )
        materials: Dict[str, Any] = {"slurry": {}, "fluids": {}}

        try:
            if step_type == "balanced_plug":
                top_ft = float(step.get("top_ft"))
                bottom_ft = float(step.get("bottom_ft"))
                interval_ft = max(bottom_ft - top_ft, 0)
                ann_excess = float(step.get("annular_excess", 0))
                # geometry
                hole_d = step.get("hole_d_in")
                casing_id = step.get("casing_id_in")
                stinger_od = step.get("stinger_od_in")
                stinger_id = float(step.get("stinger_id_in"))
                if hole_d is not None:
                    ann_cap = annulus_capacity_bbl_per_ft(float(hole_d), float(stinger_od))
                else:
                    ann_cap = annulus_capacity_bbl_per_ft(float(casing_id), float(stinger_od))
                id_cap = cylinder_capacity_bbl_per_ft(stinger_id)
                vols = balanced_plug_bbl(interval_ft, ann_cap, id_cap, ann_excess)
                vb = compute_sacks(vols["total_bbl"], recipe)
                disp_margin = float(step.get("displacement_margin_bbl", 0))
                disp_bbl = balanced_displacement_bbl(interval_ft, id_cap, margin_bbl=disp_margin)
                materials["slurry"] = {
                    "total_bbl": vols["total_bbl"],
                    "ft3": vb.ft3,
                    "sacks": vb.sacks,
                    "water_bbl": vb.water_bbl,
                    "additives": vb.additives,
                    "explain": vb.explain,
                }
                materials["fluids"]["displacement_bbl"] = disp_bbl
            elif step_type in ("bridge_plug_cap", "cibp_cap"):
                # Require casing/stinger geometry to compute; otherwise, leave materials empty
                if step.get("casing_id_in") is not None and step.get("stinger_od_in") is not None:
                    cap_len = float(step.get("cap_length_ft"))
                    casing_id = float(step.get("casing_id_in"))
                    stinger_od = float(step.get("stinger_od_in"))
                    ann_excess = float(step.get("annular_excess", 0))
                    vols = bridge_plug_cap_bbl(cap_len, casing_id, stinger_od, ann_excess)
                    vb = compute_sacks(vols["total_bbl"], recipe)
                    materials["slurry"] = {
                        "total_bbl": vols["total_bbl"],
                        "ft3": vb.ft3,
                        "sacks": vb.sacks,
                        "water_bbl": vb.water_bbl,
                        "additives": vb.additives,
                        "explain": vb.explain,
                    }
            elif step_type in ("cement_plug",):
                # Generic cement plug over an interval. Supports optional segmentation.
                context = (step.get("geometry_context") or "").lower()
                top_ft = float(step.get("top_ft", 0) or 0)
                bottom_ft = float(step.get("bottom_ft", 0) or 0)
                ann_excess_default = _infer_annular_excess(step)
                segments_calc: List[Dict[str, Any]] = []
                total_bbl = 0.0
                # Rounding policy: nearest by default unless overridden in step.recipe.rounding or policy
                rounding_mode = (step.get("recipe", {}) or {}).get("rounding") or "nearest"

                segments = step.get("segments") or []
                if isinstance(segments, list) and segments:
                    # Each segment provides its own geometry
                    for seg in segments:
                        try:
                            s_top = float(seg.get("top_ft"))
                            s_bot = float(seg.get("bottom_ft"))
                            length = abs(s_bot - s_top)
                            outer = seg.get("casing_id_in") or seg.get("hole_d_in")
                            inner = seg.get("stinger_od_in", step.get("stinger_od_in"))
                            if outer is None or inner is None:
                                continue
                            cap = annulus_capacity_bbl_per_ft(float(outer), float(inner))
                            ex = float(seg.get("annular_excess", ann_excess_default))
                            bbl = length * cap * (1.0 + ex)
                            total_bbl += bbl
                            segments_calc.append({
                                "top_ft": s_top,
                                "bottom_ft": s_bot,
                                "length_ft": length,
                                "outer_in": float(outer),
                                "inner_in": float(inner),
                                "cap_bbl_per_ft": cap,
                                "excess_used": ex,
                                "bbl": bbl,
                            })
                        except Exception:
                            continue
                else:
                    # Single segment using step-level geometry
                    interval_ft = abs(bottom_ft - top_ft)
                    stinger_od = step.get("stinger_od_in")
                    hole_d = step.get("hole_d_in")
                    casing_id = step.get("casing_id_in")
                    ex = float(step.get("annular_excess", ann_excess_default))
                    # Open-hole: use hole vs stinger OD exclusively (prefer OH when hole_d provided)
                    if (hole_d is not None and stinger_od is not None) and (context.startswith("open_hole") or casing_id is None):
                        cap = annulus_capacity_bbl_per_ft(float(hole_d), float(stinger_od))
                        total_bbl = interval_ft * cap * (1.0 + ex)
                        segments_calc.append({
                            "top_ft": top_ft,
                            "bottom_ft": bottom_ft,
                            "length_ft": interval_ft,
                            "outer_in": float(hole_d),
                            "inner_in": float(stinger_od),
                            "cap_bbl_per_ft": cap,
                            "excess_used": ex,
                            "bbl": total_bbl,
                        })
                    else:
                        # Cased: use casing ID vs stinger OD when available
                        outer = None
                        inner = stinger_od
                        if casing_id is not None and stinger_od is not None:
                            outer = float(casing_id)
                        elif hole_d is not None and stinger_od is not None:
                            outer = float(hole_d)
                        if outer is not None and inner is not None:
                            cap = annulus_capacity_bbl_per_ft(float(outer), float(inner))
                            total_bbl = interval_ft * cap * (1.0 + ex)
                            segments_calc.append({
                                "top_ft": top_ft,
                                "bottom_ft": bottom_ft,
                                "length_ft": interval_ft,
                                "outer_in": float(outer),
                                "inner_in": float(inner),
                                "cap_bbl_per_ft": cap,
                                "excess_used": ex,
                                "bbl": total_bbl,
                            })
                vb = compute_sacks(total_bbl, recipe, rounding=rounding_mode)
                materials["slurry"] = {
                    "total_bbl": total_bbl,
                    "ft3": vb.ft3,
                    "sacks": vb.sacks,
                    "water_bbl": vb.water_bbl,
                    "additives": vb.additives,
                    "explain": vb.explain,
                }
                if segments_calc:
                    materials["segments"] = segments_calc
            elif step_type in ("perf_circulate",):
                # Operational step: no cement sacks
                materials["slurry"] = {}
            elif step_type == "squeeze":
                interval_ft = float(step.get("interval_ft"))
                casing_id = float(step.get("casing_id_in"))
                stinger_od = float(step.get("stinger_od_in"))
                squeeze_factor = float(step.get("squeeze_factor", 1.0))
                vols = squeeze_bbl(interval_ft, casing_id, stinger_od, squeeze_factor)
                vb = compute_sacks(vols["total_bbl"], recipe)
                materials["slurry"] = {
                    "total_bbl": vols["total_bbl"],
                    "ft3": vb.ft3,
                    "sacks": vb.sacks,
                    "water_bbl": vb.water_bbl,
                    "additives": vb.additives,
                    "explain": vb.explain,
                }
                # optional spacer/preflush
                if step.get("spacer"):
                    ann_cap = annulus_capacity_bbl_per_ft(casing_id, stinger_od)
                    sp = step["spacer"]
                    spacer_bbl = spacer_bbl_for_interval(
                        interval_ft,
                        ann_cap,
                        float(sp.get("min_bbl", 5.0)),
                        float(sp.get("spacer_multiple", 1.5)),
                        float(sp.get("contact_minutes")) if sp.get("contact_minutes") is not None else None,
                        float(sp.get("pump_rate_bpm")) if sp.get("pump_rate_bpm") is not None else None,
                    )
                    materials["fluids"]["spacer_bbl"] = spacer_bbl
        except Exception as e:  # pragma: no cover - defensive; return step unchanged on error
            step.setdefault("errors", []).append(str(e))

        step["materials"] = materials
        out.append(step)
    return out


def _dedup_step_citations(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    for s in steps:
        cites = s.get("regulatory_basis")
        if isinstance(cites, list):
            # preserve order deterministically while deduping
            seen = set()
            unique: List[str] = []
            for c in cites:
                if c not in seen:
                    seen.add(c)
                    unique.append(c)
            s["regulatory_basis"] = unique
        deduped.append(s)
    return deduped


def _apply_step_defaults(steps: List[Dict[str, Any]], preferences: Dict[str, Any]) -> List[Dict[str, Any]]:
    geometry_defaults: Dict[str, Dict[str, Any]] = preferences.get("geometry_defaults", {}) if isinstance(preferences, dict) else {}
    default_recipe: Dict[str, Any] = preferences.get("default_recipe", {}) if isinstance(preferences, dict) else {}
    out: List[Dict[str, Any]] = []
    for s in steps:
        # attach geometry defaults per step type, context-aware to avoid leaking cased keys into open-hole
        g = geometry_defaults.get(s.get("type"), {})
        if s.get("type") == "cement_plug":
            ctx = (s.get("geometry_context") or "").lower()
            allowed_keys: List[str]
            if ctx.startswith("open_hole"):
                allowed_keys = ["stinger_od_in", "stinger_id_in", "annular_excess"]
            else:
                allowed_keys = ["casing_id_in", "stinger_od_in", "annular_excess"]
            for k, v in (g or {}).items():
                if k in allowed_keys:
                    s.setdefault(k, v)
        else:
            for k, v in (g or {}).items():
                s.setdefault(k, v)
        # attach default recipe if not provided and inject rounding preference
        if "recipe" not in s and default_recipe:
            s["recipe"] = default_recipe
        # propagate rounding preference onto step.recipe if missing
        rounding_pref = (preferences.get("rounding_policy") or "nearest") if isinstance(preferences, dict) else "nearest"
        if isinstance(s.get("recipe"), dict) and "rounding" not in s["recipe"]:
            s["recipe"]["rounding"] = rounding_pref
        out.append(s)
    return out


def _apply_district_overrides(
    steps: List[Dict[str, Any]],
    policy_effective: Dict[str, Any],
    district: Any,
    county: Any,
) -> List[Dict[str, Any]]:
    overrides = policy_effective.get("district_overrides") or {}
    preferences = policy_effective.get("preferences") or {}
    reqs = policy_effective.get("requirements") or {}
    out: List[Dict[str, Any]] = []
    formation_tops = overrides.get("formation_tops") or []
    for s in steps:
        s_out = dict(s)
        # District 08/08A: tag surface shoe in open hole when county override specifies
        if s_out.get("type") == "surface_casing_shoe_plug":
            tag_cfg = (overrides.get("tag") or {})
            if tag_cfg.get("surface_shoe_in_oh") is True:
                s_out["tag_required"] = True
                basis = s_out.get("regulatory_basis", []) or []
                if isinstance(basis, list):
                    basis.append(f"rrc.district.{str(district).lower()}.{str(county).lower() if county else 'unknown'}:tag.surface_shoe_in_oh")
                    s_out["regulatory_basis"] = basis
            # If protect_intervals exist or enhanced_recovery is present, require tagging on the shoe
            if (overrides.get("protect_intervals") or overrides.get("enhanced_recovery_zone")) and not s_out.get("tag_required"):
                s_out["tag_required"] = True
                basis = s_out.get("regulatory_basis", []) or []
                if isinstance(basis, list):
                    if overrides.get("protect_intervals"):
                        basis.append(f"rrc.district.{str(district).lower()}.{str(county).lower() if county else 'unknown'}:protect_intervals")
                    if overrides.get("enhanced_recovery_zone"):
                        basis.append(f"rrc.district.{str(district).lower()}.{str(county).lower() if county else 'unknown'}:enhanced_recovery_zone")
                    s_out["regulatory_basis"] = basis
        # District 7C: operational preferences and pump path requirements
        # Attach instructions for tubing-only, mud spec, funnel time
        op = (preferences.get("operational") or {})
        instr_parts: List[str] = []
        if reqs.get("pump_through_tubing_or_drillpipe_only", {}).get("value") is True:
            instr_parts.append("Pump via tubing/drill pipe only")
        if op.get("notice_hours_min"):
            instr_parts.append(f"Give district notice ≥{op['notice_hours_min']}h before plugs")
        if op.get("mud_min_weight_ppg"):
            instr_parts.append(f"Mud ≥{op['mud_min_weight_ppg']} ppg")
        if op.get("funnel_min_s"):
            instr_parts.append(f"Funnel ≥{op['funnel_min_s']} s")
        if instr_parts:
            existing = s_out.get("special_instructions")
            joined = "; ".join(instr_parts)
            s_out["special_instructions"] = f"{existing}; {joined}" if existing else joined

        # District 7C tagging: require TAG on UQW isolation and surface plugs when hint present
        if s_out.get("type") in ("uqw_isolation_plug", "surface_casing_shoe_plug"):
            tag_hint = (reqs.get("tagging_required_hint") or {}).get("value") is True
            if tag_hint and not s_out.get("tag_required"):
                s_out["tag_required"] = True
                basis = s_out.get("regulatory_basis", []) or []
                if isinstance(basis, list):
                    basis.append(f"rrc.district.{str(district).lower()}.{str(county).lower() if county else 'unknown'}:tag.required")
                    s_out["regulatory_basis"] = basis
        # Add formation-top plugs as separate steps if requested by overrides
        # We only add once at the end; defer adding here by collecting later
        out.append(s_out)
    # Append formation-top plug steps after base steps
    for ft in formation_tops:
        try:
            formation = ft.get("formation")
            top_ft = float(ft.get("top_ft"))
            plug_required = ft.get("plug_required") is True
            if not plug_required or formation is None:
                continue
            step = {
                "type": "formation_top_plug",
                "formation": formation,
                "top_ft": top_ft,
                "min_length_ft": float(reqs.get("surface_casing_shoe_plug_min_ft", {}).get("value", 50)) if isinstance(reqs.get("surface_casing_shoe_plug_min_ft"), dict) else 50.0,
                "regulatory_basis": [
                    f"rrc.district.{str(district).lower()}.{str(county).lower() if county else 'unknown'}:formation_top:{formation}"
                ],
            }
            if ft.get("tag_required") is True or formation in ("San Andres", "Coleman Junction"):
                step["tag_required"] = True
            out.append(step)
        except Exception:
            continue
    return out


def _apply_steps_overrides(
    steps: List[Dict[str, Any]],
    policy_effective: Dict[str, Any],
    preferences: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Allow callers (e.g., goldens) to override step parameters or add steps explicitly.
    Supported:
      - steps_overrides.cibp_cap.cap_length_ft: override cap length
      - steps_overrides.squeeze_via_perf.interval_ft: [top, bottom] -> add squeeze step
        Uses geometry_defaults.squeeze and squeeze_factor if provided.
    """
    overrides = (policy_effective.get("steps_overrides") or {}) if isinstance(policy_effective, dict) else {}
    out: List[Dict[str, Any]] = []
    # Override existing cibp_cap cap_length_ft
    cibp_override = overrides.get("cibp_cap") or {}
    new_cap_len = cibp_override.get("cap_length_ft")
    for s in steps:
        s_out = dict(s)
        if s_out.get("type") == "cibp_cap" and new_cap_len not in (None, ""):
            s_out["cap_length_ft"] = float(new_cap_len)
        out.append(s_out)

    # Add squeeze step if requested
    sqz = overrides.get("squeeze_via_perf") or {}
    interval = sqz.get("interval_ft") or []
    if isinstance(interval, (list, tuple)) and len(interval) == 2:
        top, bottom = float(interval[0]), float(interval[1])
        length = max(bottom - top, 0.0)
        geom_default = (preferences.get("geometry_defaults") or {}).get("squeeze") or {}
        default_recipe = preferences.get("default_recipe") or {}
        squeeze_factor = float(geom_default.get("squeeze_factor", 1.5))
        step = {
            "type": "squeeze",
            "interval_ft": length,
            "casing_id_in": float(geom_default.get("casing_id_in")) if geom_default.get("casing_id_in") is not None else None,
            "stinger_od_in": float(geom_default.get("stinger_od_in")) if geom_default.get("stinger_od_in") is not None else None,
            "squeeze_factor": squeeze_factor,
            "regulatory_basis": sqz.get("citations") or [],
        }
        if default_recipe:
            step["recipe"] = default_recipe
        out.append(step)

    # Add perf_circulate steps if provided
    pc_list = overrides.get("perf_circulate") or []
    if isinstance(pc_list, list):
        for pc in pc_list:
            try:
                top, bottom = float(pc.get("top_ft")), float(pc.get("bottom_ft"))
                step = {
                    "type": "perf_circulate",
                    "top_ft": top,
                    "bottom_ft": bottom,
                    "regulatory_basis": pc.get("citations") or [],
                }
                out.append(step)
            except Exception:
                continue

    # Add cement_plug steps if provided
    cp_list = overrides.get("cement_plugs") or []
    if isinstance(cp_list, list):
        geom_default_cp = (preferences.get("geometry_defaults") or {}).get("cement_plug") or {}
        default_recipe = preferences.get("default_recipe") or {}
        for cp in cp_list:
            try:
                top, bottom = float(cp.get("top_ft")), float(cp.get("bottom_ft"))
                ctx = (cp.get("geometry_context") or "").lower()
                step = {
                    "type": "cement_plug",
                    "top_ft": top,
                    "bottom_ft": bottom,
                    "geometry_context": cp.get("geometry_context"),
                    # geometry: respect context; do not leak cased defaults into open-hole
                    "stinger_od_in": float(cp.get("stinger_od_in", geom_default_cp.get("stinger_od_in"))) if (cp.get("stinger_od_in") or geom_default_cp.get("stinger_od_in")) is not None else None,
                    "annular_excess": float(cp.get("annular_excess", geom_default_cp.get("annular_excess", 0.4))),
                    "segments": cp.get("segments"),
                    "regulatory_basis": cp.get("citations") or [],
                }
                # Include hole_d_in when provided on open-hole steps
                if cp.get("hole_d_in") is not None:
                    step["hole_d_in"] = float(cp.get("hole_d_in"))
                # Only include casing_id_in for non-open-hole contexts
                if not ctx.startswith("open_hole"):
                    if (cp.get("casing_id_in") or geom_default_cp.get("casing_id_in")) is not None:
                        step["casing_id_in"] = float(cp.get("casing_id_in", geom_default_cp.get("casing_id_in")))
                if default_recipe:
                    step["recipe"] = default_recipe
                out.append(step)
            except Exception:
                continue

    return out


def _infer_annular_excess(step: Dict[str, Any]) -> float:
    # Prefer explicit on-step value
    if step.get("annular_excess") is not None:
        try:
            return float(step.get("annular_excess"))
        except Exception:
            pass
    # Heuristic by geometry_context and length
    context = (step.get("geometry_context") or "").lower()
    interval_ft = 0.0
    try:
        if step.get("top_ft") is not None and step.get("bottom_ft") is not None:
            t, b = float(step.get("top_ft")), float(step.get("bottom_ft"))
            interval_ft = abs(b - t)
        elif step.get("interval_ft") is not None:
            interval_ft = float(step.get("interval_ft"))
    except Exception:
        interval_ft = 0.0

    # Defaults (can later be lifted from overlays)
    if context.startswith("open_hole"):
        return 1.0
    if context.startswith("cased") and interval_ft >= 200:
        return 1.0
    if context.startswith("cased"):
        return 0.5
    # Fallback
    return 0.5


def _assumed_casing_id_in(context: str | None, step: Dict[str, Any], preferences: Dict[str, Any]) -> float | None:
    # Prefer explicit on step
    if step.get("casing_id_in") is not None:
        try:
            return float(step.get("casing_id_in"))
        except Exception:
            pass
    ctx = (context or "").lower()
    geom_defaults = (preferences.get("geometry_defaults") or {}) if isinstance(preferences, dict) else {}
    if ctx.startswith("cased_surface"):
        val = (geom_defaults.get("surface") or {}).get("casing_id_in")
        return float(val) if val is not None else 10.05  # 11-3/4" ~54.5 ppf
    if ctx.startswith("cased_intermediate"):
        val = (geom_defaults.get("intermediate") or {}).get("casing_id_in")
        return float(val) if val is not None else 8.097  # 8-5/8" 24 ppf
    if ctx.startswith("cased_production") or ctx.startswith("cased"):
        val = (geom_defaults.get("cement_plug") or {}).get("casing_id_in")
        return float(val) if val is not None else 4.778  # 5-1/2" 15.5 ppf
    return None
