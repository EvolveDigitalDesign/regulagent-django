from __future__ import annotations

from typing import Any, Dict, List

import logging

logger = logging.getLogger(__name__)

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

    logger.info("kernel.plan_from_facts: start policy_id=%s district=%s", policy.get("policy_id"), district)
    logger.info("Policy ID: %s, Version: %s", policy.get("policy_id"), policy.get("policy_version"))
    logger.info("Applying policy with the following effective preferences: %s", policy.get("preferences"))
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
        logger.warning("kernel.plan_from_facts: policy incomplete; constraints=%s", constraints)
        return plan

    # Deterministic step generation (scaffold for W-3A)
    if is_complete and policy.get("policy_id") == "tx.w3a":
        logger.debug("kernel.plan_from_facts: generating W3A steps")
        generated = generate_w3a_steps(resolved_facts, policy.get("effective") or {})
        plan["violations"] = generated.get("violations", [])
        steps = generated.get("steps", [])
        # --- Mechanical awareness: suppress conflicting ops when barriers exist ---
        try:
            mech = resolved_facts.get("existing_mechanical_barriers") or []
            if isinstance(mech, dict):
                mech = mech.get("value") or []
            mech_set = set([str(x).upper() for x in (mech or [])])
            existing_cibp_ft = None
            ec = resolved_facts.get("existing_cibp_ft") or {}
            existing_cibp_ft = ec.get("value") if isinstance(ec, dict) else ec
            if mech_set:
                filtered: List[Dict[str, Any]] = []
                for s in steps:
                    t = s.get("type")
                    # Do not suggest perf/circulate through a CIBP
                    if t == "perf_circulate" and ("CIBP" in mech_set):
                        basis = s.get("regulatory_basis") or []
                        plan.setdefault("violations", []).append({
                            "code": "blocked_by_existing_cibp",
                            "severity": "info",
                            "message": "Perf/circulate suppressed due to existing CIBP",
                            "regulatory_basis": basis,
                        })
                        continue
                    filtered.append(s)
                steps = filtered
                # Ensure a cap is present above existing CIBP
                if ("CIBP" in mech_set) and existing_cibp_ft not in (None, ""):
                    has_cap = any(s.get("type") in ("cibp_cap", "bridge_plug_cap") for s in steps)
                    if not has_cap:
                        steps.append({
                            "type": "cibp_cap",
                            "cap_length_ft": 20.0,
                            "geometry_context": "cased_production",
                            "top_ft": float(existing_cibp_ft) - 20.0,  # Cap extends upward (shallower)
                            "bottom_ft": float(existing_cibp_ft),      # Bottom sits on plug
                            "regulatory_basis": ["tx.tac.16.3.14(g)(3)"],
                        })
                # Add mechanical barrier isolation plugs around PACKER / DV tool when present
                def _add_isolation(depth_ft: float, label: str) -> None:
                    if depth_ft in (None, ""):
                        return
                    try:
                        d = float(depth_ft)
                    except Exception:
                        return
                    # avoid duplicates if a cement plug already spans this depth
                    for s in steps:
                        if s.get("type") == "cement_plug":
                            t = s.get("top_ft"); b = s.get("bottom_ft")
                            try:
                                if t is not None and b is not None and float(b) <= d <= float(t):
                                    return
                            except Exception:
                                pass
                    steps.append({
                        "type": "cement_plug",
                        "geometry_context": "cased_production",
                        "top_ft": d + 50.0,
                        "bottom_ft": d - 50.0,
                        "annular_excess": 0.4,
                        "regulatory_basis": [f"rrc.district.{str(district).lower()}:mechanical.isolation:{label.lower()}"],
                        "placement_basis": f"Mechanical barrier isolation: {label}",
                    })
                # depths from facts
                pk = resolved_facts.get("packer_ft") or {}
                dv = resolved_facts.get("dv_tool_ft") or {}
                _add_isolation(pk.get("value") if isinstance(pk, dict) else pk, "PACKER")
                _add_isolation(dv.get("value") if isinstance(dv, dict) else dv, "DV_TOOL")
        except Exception:
            logger.exception("mechanical-awareness: failed")

        # --- CIBP detector: emit bridge plug + cap when producing interval is exposed below production shoe, no existing CIBP, and no cap present ---
        try:
            # Skip if an existing CIBP is already recorded
            mech = resolved_facts.get("existing_mechanical_barriers") or []
            if isinstance(mech, dict):
                mech = mech.get("value") or []
            mech_set = set([str(x).upper() for x in (mech or [])])
            has_existing_cibp = ("CIBP" in mech_set)

            # If a cap already exists in steps, do not add another
            has_cap_step = any(s.get("type") in ("cibp_cap", "bridge_plug_cap") for s in steps)

            # Producing interval from facts (primary)
            piv = resolved_facts.get("producing_interval_ft") or {}
            interval = piv.get("value") if isinstance(piv, dict) else piv
            shallowest_perf_top_ft = None
            if isinstance(interval, (list, tuple)) and len(interval) == 2:
                try:
                    a, b = float(interval[0]), float(interval[1])
                    # Use top-of-interval (shallower) for CIBP placement policy
                    top_iv = min(a, b)
                    shallowest_perf_top_ft = top_iv
                except Exception:
                    shallowest_perf_top_ft = None
            
            # PRIORITY: Use perforations array from W-2 if available
            # Per requirement: CIBP should be placed 50 ft shallower than SHALLOWEST perforation top
            perforations_list = resolved_facts.get("perforations") or []
            if isinstance(perforations_list, list) and len(perforations_list) > 0:
                logger.critical(f"🔧 CIBP: Found {len(perforations_list)} perforations in facts")
                temp_shallowest = None
                for perf in perforations_list:
                    if isinstance(perf, dict) and perf.get("interval_top_ft") is not None:
                        try:
                            top = float(perf.get("interval_top_ft"))
                            if temp_shallowest is None or top < temp_shallowest:
                                temp_shallowest = top
                                logger.critical(f"   Perf {perf['interval_top_ft']}-{perf['interval_bottom_ft']} ft, top={top}")
                        except (ValueError, TypeError):
                            pass
                
                if temp_shallowest is not None:
                    shallowest_perf_top_ft = temp_shallowest
                    logger.critical(f"🔧 CIBP: Using shallowest perforation top for placement: {shallowest_perf_top_ft} ft")
            
            # Fallback: infer from formations (treat all provided formations as producing; use deepest top)
            if shallowest_perf_top_ft is None:
                try:
                    tops_map = resolved_facts.get("formation_tops_map") or {}
                    if isinstance(tops_map, dict) and tops_map:
                        vals = []
                        for _, v in tops_map.items():
                            try:
                                vals.append(float(v))
                            except Exception:
                                continue
                        if vals:
                            shallowest_perf_top_ft = max(vals)
                except Exception:
                    shallowest_perf_top_ft = None

            # Production shoe (exposure check): require exposure for CIBP+cap
            prod_shoe_obj = resolved_facts.get("production_shoe_ft") or {}
            production_shoe_ft = prod_shoe_obj.get("value") if isinstance(prod_shoe_obj, dict) else prod_shoe_obj
            try:
                production_shoe_ft = float(production_shoe_ft) if production_shoe_ft not in (None, "") else None
            except Exception:
                production_shoe_ft = None

            # Exposure-based trigger: producing interval is ABOVE (shallower than) production shoe
            # For cased-hole completions, perforations above the shoe = exposed and need CIBP
            # Per SWR-14(g)(3): "when plugging back through casing perforations, a CIBP shall be set and capped"
            # CORRECTED: Was using >=, but should be <= for cased-hole completions
            exposed = (shallowest_perf_top_ft is not None) and (production_shoe_ft is not None) and (float(shallowest_perf_top_ft) <= float(production_shoe_ft))
            
            # If a squeeze/perf step will fully isolate at shallowest_perf_top_ft, skip emitting new CIBP+cap
            def _covered_by_ops(existing_steps: List[Dict[str, Any]], depth_ft: float) -> bool:
                """
                Check if a depth is already covered by isolation operations.
                
                Returns True if:
                - A perforate_and_squeeze_plug covers this depth (provides mechanical + cement isolation)
                - A cement_plug, formation_plug, or formation_top_plug covers this depth
                - A squeeze or perf_circulate operation covers this depth
                - A bridge_plug or existing CIBP is at or near this depth
                """
                try:
                    for s in existing_steps:
                        step_type = s.get("type")
                        
                        # Check mechanical plugs (bridge_plug, cibp)
                        if step_type in ("bridge_plug", "cibp"):
                            plug_depth = s.get("depth_ft")
                            if plug_depth is not None:
                                # Consider covered if bridge plug within 100 ft of target depth
                                if abs(float(plug_depth) - depth_ft) <= 100.0:
                                    return True
                        
                        # Check interval-based operations
                        if step_type in ("squeeze", "perf_circulate", "perforate_and_squeeze_plug", 
                                        "cement_plug", "formation_plug", "formation_top_plug",
                                        "uqw_isolation_plug", "surface_casing_shoe_plug"):
                            # For perforate_and_squeeze_plug, check total coverage (both perf + cap)
                            if step_type == "perforate_and_squeeze_plug":
                                total_top = s.get("total_top_ft") or s.get("top_ft")
                                total_bottom = s.get("total_bottom_ft") or s.get("bottom_ft")
                                if total_top is not None and total_bottom is not None:
                                    lo, hi = min(float(total_top), float(total_bottom)), max(float(total_top), float(total_bottom))
                                    if lo <= depth_ft <= hi:
                                        return True
                            else:
                                t = s.get("top_ft"); b = s.get("bottom_ft")
                                if t is not None and b is not None:
                                    lo, hi = min(float(t), float(b)), max(float(t), float(b))
                                    if lo <= depth_ft <= hi:
                                        return True
                except Exception:
                    return False
                return False

            covered = _covered_by_ops(steps, float(shallowest_perf_top_ft)) if shallowest_perf_top_ft is not None else True
            
            logger.critical(f"🔧 CIBP DETECTOR: exposed={exposed}, has_existing_cibp={has_existing_cibp}, has_cap_step={has_cap_step}, shallowest_perf_top_ft={shallowest_perf_top_ft}, covered_by_ops={covered}")
            logger.critical(f"🔧 CIBP DETECTOR: production_shoe_ft={production_shoe_ft}")
            
            if exposed and (not has_existing_cibp) and (not has_cap_step) and (not covered):
                logger.critical("🔧🔧🔧 CIBP DETECTOR: ENTERED CIBP GENERATION BLOCK")
                # Emit a mechanical plug (bridge plug) and a cement cap above it.
                # Represent the bridge plug explicitly so downstream UIs can show both operations.
                
                try:
                    # Determine CIBP placement: consider both perforations and KOP (kick-off point)
                    # Rule: Shallowest depth wins (min of perf-50 and kop-50)
                    # Per requirement: Place 50 ft shallower than shallowest perforation top
                    logger.critical(f"🔧 CIBP: Step 1 - Calculating perf-based depth from shallowest_perf_top_ft={shallowest_perf_top_ft}")
                    plug_depth_from_perfs = max(float(shallowest_perf_top_ft) - 50.0, 0.0)
                    placement_reason = "perforations (50 ft above shallowest perf top)"
                    logger.critical(f"🔧 CIBP: Step 1 COMPLETE - plug_depth_from_perfs={plug_depth_from_perfs} (50 ft above shallowest perf top)")
                    
                    # Check for KOP (Kick-Off Point) - horizontal well consideration
                    logger.critical(f"🔧 CIBP: Step 2 - Checking for KOP in resolved_facts: {list(resolved_facts.keys())}")
                    kop_data = resolved_facts.get("kop") or {}
                    logger.critical(f"🔧 CIBP: Step 2 - kop_data={kop_data}")
                    kop_md_ft = kop_data.get("kop_md_ft") if isinstance(kop_data, dict) else None
                    logger.critical(f"🔧 CIBP: Step 2 COMPLETE - kop_md_ft={kop_md_ft}")
                    
                    plug_depth = plug_depth_from_perfs  # Default
                    
                    if kop_md_ft is not None:
                        logger.critical(f"🔧 CIBP: Step 3 - KOP FOUND, processing kop_md_ft={kop_md_ft}")
                        try:
                            kop_md = float(kop_md_ft)
                            plug_depth_from_kop = max(kop_md - 50.0, 0.0)
                            logger.critical(f"🔧 CIBP: Step 3a - plug_depth_from_kop={plug_depth_from_kop}, plug_depth_from_perfs={plug_depth_from_perfs}")
                            
                            # Shallowest wins (Option A)
                            if plug_depth_from_kop < plug_depth_from_perfs:
                                plug_depth = plug_depth_from_kop
                                placement_reason = f"KOP (50 ft above KOP at {kop_md} ft MD)"
                                logger.critical(f"🔧 CIBP: Step 3b - KOP depth is shallower → using KOP-based depth {plug_depth}")
                            else:
                                plug_depth = plug_depth_from_perfs
                                logger.critical(f"🔧 CIBP: Step 3b - Perf depth is shallower → using perf-based depth {plug_depth}")
                        except (ValueError, TypeError) as e:
                            logger.error(f"🔧 CIBP: Step 3 ERROR - Invalid KOP value {kop_md_ft}: {e}", exc_info=True)
                            plug_depth = plug_depth_from_perfs
                        logger.critical(f"🔧 CIBP: Step 3 COMPLETE - Final plug_depth={plug_depth}")
                    else:
                        logger.critical(f"🔧 CIBP: Step 3 SKIPPED - No KOP found, using perf-based depth={plug_depth}")
                    
                    logger.critical(f"🔧 CIBP: Step 4 - Creating bridge_plug step at depth={plug_depth}, reason='{placement_reason}'")
                    bridge_plug_step = {
                    "type": "bridge_plug",
                    "depth_ft": plug_depth,
                    "regulatory_basis": ["tx.tac.16.3.14(g)(3)"],
                        "details": {
                            "new_cibp_required": True,
                            "placement_reason": placement_reason,
                            "kop_considered": kop_md_ft is not None,
                        },
                    }
                    logger.critical(f"🔧 CIBP: Step 4a - About to append bridge_plug_step to steps (current steps count: {len(steps)})")
                    steps.append(bridge_plug_step)
                    logger.critical(f"🔧 CIBP: Step 4b - bridge_plug appended successfully (new steps count: {len(steps)})")
                    logger.critical(f"🔧 CIBP: Step 4 COMPLETE - ✅ bridge_plug added")
                    
                    # Step 5: Create bridge plug cap
                    logger.critical("🔧 CIBP: Step 5 - Creating bridge_plug_cap")
                    # Default cap length 100 ft above the plug (policy preference may adjust this via overrides)
                    cap_len = 100.0
                    try:
                        # Use policy knob when available
                        eff = policy.get("effective") or {}
                        reqs = eff.get("requirements") or {}
                        knob = reqs.get("cement_above_cibp_min_ft") or {}
                        val = knob.get("value") if isinstance(knob, dict) else knob
                        if val not in (None, ""):
                            cap_len = float(val)
                            logger.critical(f"🔧 CIBP: Step 5a - Policy override cap_len={cap_len}")
                    except Exception as e:
                        logger.warning(f"🔧 CIBP: Step 5a - Failed to get policy cap length: {e}")

                    # Include plug size hint from geometry defaults (production casing ID)
                    try:
                        prefs = policy.get("preferences") or {}
                        gdefs = (prefs.get("geometry_defaults") or {})
                        cp = gdefs.get("cement_plug") or {}
                        casing_id_in = cp.get("casing_id_in")
                        if casing_id_in is not None:
                            steps[-1].setdefault("details", {})["casing_id_in"] = float(casing_id_in)
                            # Recommend standard CIBP size near casing ID (simple mapping/rounding)
                            cid = float(casing_id_in)
                            # Typical match: 5.5" casing (4.778" ID) → 4.50" CIBP; apply ~0.25" safety delta
                            steps[-1]["details"]["recommended_cibp_size_in"] = max(round(cid - 0.25, 2), 1.0)
                            logger.critical(f"🔧 CIBP: Step 5b - Added CIBP size hint: {steps[-1]['details']['recommended_cibp_size_in']}\"")
                    except Exception as e:
                        logger.warning(f"🔧 CIBP: Step 5b - Failed to add CIBP size hint: {e}")

                    # Build bridge_plug_cap with geometry fields for materials calculation
                    logger.critical(f"🔧 CIBP: Step 5c - Building cap_step (cap_len={cap_len}, plug_depth={plug_depth})")
                    cap_step = {
                        "type": "bridge_plug_cap",  # alias accepted by materials compute
                        "cap_length_ft": cap_len,
                        "geometry_context": "cased_production",
                        "top_ft": plug_depth - cap_len,  # Cap extends UPWARD (shallower) from plug
                        "bottom_ft": plug_depth,         # Bottom of cap sits on top of plug
                        "regulatory_basis": ["tx.tac.16.3.14(g)(3)"],
                        "details": {"above_bridge_plug": plug_depth},
                    }
                    
                    # Add geometry fields (casing_id_in, stinger_od_in) for materials computation
                    try:
                        prefs = policy.get("preferences") or {}
                        gdefs = (prefs.get("geometry_defaults") or {})
                        cp = gdefs.get("cement_plug") or {}
                        casing_id_in = cp.get("casing_id_in")
                        stinger_od_in = cp.get("stinger_od_in")
                        annular_excess = cp.get("annular_excess")
                        
                        if casing_id_in is not None:
                            cap_step["casing_id_in"] = float(casing_id_in)
                        if stinger_od_in is not None:
                            cap_step["stinger_od_in"] = float(stinger_od_in)
                        if annular_excess is not None:
                            cap_step["annular_excess"] = float(annular_excess)
                        
                        # Add recipe if available
                        default_recipe = prefs.get("default_recipe")
                        if default_recipe:
                            cap_step["recipe"] = default_recipe
                        logger.critical(f"🔧 CIBP: Step 5d - Added geometry to cap: casing_id={cap_step.get('casing_id_in')}, stinger_od={cap_step.get('stinger_od_in')}")
                    except Exception as e:
                        logger.warning(f"🔧 CIBP: Step 5d - Failed to add cap geometry: {e}")
                    
                    logger.critical(f"🔧 CIBP: Step 5e - About to append cap_step (current steps count: {len(steps)})")
                    steps.append(cap_step)
                    logger.critical(f"🔧 CIBP: Step 5f - cap appended successfully (new steps count: {len(steps)})")
                    logger.critical(f"🔧 CIBP: Step 5 COMPLETE - ✅ bridge_plug_cap added with length {cap_len} ft")
                    
                    # Step 6: Remove productive_horizon_isolation_plug when CIBP is used
                    # CIBP mechanically isolates productive zones, making separate horizon plug redundant
                    logger.critical("🔧 CIBP: Step 6 - Checking for redundant productive_horizon_isolation_plug")
                    original_count = len(steps)
                    steps = [s for s in steps if s.get("type") != "productive_horizon_isolation_plug"]
                    removed_count = original_count - len(steps)
                    if removed_count > 0:
                        logger.critical(f"🔧 CIBP: Step 6 COMPLETE - ✅ Removed {removed_count} productive_horizon_isolation_plug(s) (CIBP provides isolation)")
                    else:
                        logger.critical("🔧 CIBP: Step 6 COMPLETE - No productive_horizon_isolation_plug found to remove")
                    
                except Exception as e:
                    logger.error(f"🔧🔧🔧 CIBP DETECTOR: CRITICAL ERROR during CIBP generation: {e}", exc_info=True)
                    raise
            else:
                logger.critical(f"🔧 CIBP DETECTOR: ❌ SKIPPED - One or more conditions not met")
        except Exception:
            logger.exception("cibp-detector: failed")

        logger.debug("kernel.plan_from_facts: generated %d steps", len(steps))
        steps = _dedup_step_citations(steps)
        # Apply default geometry/recipe from preferences when present
        plan_steps = _apply_step_defaults(steps, policy.get("preferences") if isinstance(policy.get("preferences"), dict) else {}, resolved_facts)
        # Apply district/county overrides (e.g., 08A tagging; 7C operational instructions)
        plan_steps = _apply_district_overrides(
            plan_steps,
            policy.get("effective") or {},
            policy.get("preferences") or {},
            district,
            policy.get("county"),
        )
        # Apply explicit step overrides provided by caller/payload (cap length, squeeze intervals, etc.)
        plan_steps = _apply_steps_overrides(
            plan_steps,
            policy.get("effective") or {},
            policy.get("preferences") or {},
        )
        logger.debug("kernel.plan_from_facts: after overrides %d steps", len(plan_steps))
        # Suppress formation/cement plugs fully contained within perf_circulate cemented intervals
        try:
            perf_intervals: List[Tuple[float, float]] = []
            for s in plan_steps:
                if s.get("type") == "perf_circulate" and s.get("top_ft") is not None and s.get("bottom_ft") is not None:
                    t, b = float(s.get("top_ft")), float(s.get("bottom_ft"))
                    low, high = min(t, b), max(t, b)
                    perf_intervals.append((low, high))
            if perf_intervals:
                filtered: List[Dict[str, Any]] = []
                for s in plan_steps:
                    if s.get("type") in ("formation_top_plug", "cement_plug") and s.get("top_ft") is not None and s.get("bottom_ft") is not None:
                        t, b = float(s.get("top_ft")), float(s.get("bottom_ft"))
                        low, high = min(t, b), max(t, b)
                        covered = any(low >= pl and high <= ph for (pl, ph) in perf_intervals)
                        if covered:
                            # Skip subsumed plug; annotate if needed (dropped from execution)
                            continue
                    filtered.append(s)
                plan_steps = filtered
        except Exception:
            logger.exception("kernel.plan_from_facts: perf overlap suppression failed")
        # Annotate cement class based on base pack cutoff (shallow vs deep)
        try:
            base_pack = policy.get("base") or {}
            cement_cls = base_pack.get("cement_class") or {}
            cutoff = float(cement_cls.get("cutoff_ft")) if cement_cls.get("cutoff_ft") not in (None, "") else None
            shallow = str(cement_cls.get("shallow_class") or "").strip()
            deep = str(cement_cls.get("deep_class") or "").strip()
            if cutoff is not None and (shallow or deep):
                annotated: List[Dict[str, Any]] = []
                for s in plan_steps:
                    t = s.get("type")
                    if t in ("cement_plug", "surface_casing_shoe_plug", "uqw_isolation_plug", "cibp_cap", "bridge_plug_cap", "squeeze", "top_plug", "perforate_and_squeeze_plug"):
                        top_v = s.get("top_ft")
                        bot_v = s.get("bottom_ft")
                        mid = None
                        try:
                            if top_v is not None and bot_v is not None:
                                mid = (float(top_v) + float(bot_v)) / 2.0
                            elif top_v is not None and s.get("min_length_ft") not in (None, ""):
                                mid = float(top_v) - float(s.get("min_length_ft")) / 2.0
                        except Exception:
                            mid = None
                        if mid is not None:
                            cls = deep if mid >= cutoff else shallow if shallow else deep
                            s2 = dict(s)
                            s2.setdefault("details", {})["cement_class"] = cls
                            s2["details"]["depth_mid_ft"] = mid
                            annotated.append(s2)
                            continue
                    annotated.append(s)
                plan_steps = annotated
        except Exception:
            logger.exception("cement-class annotation failed")
        # Inject tagging/verification details where required
        try:
            tag_wait = None
            try:
                eff = policy.get("effective") or {}
                op = (eff.get("preferences") or {}).get("operational") or {}
                tag_wait = op.get("tag_wait_hours")
            except Exception:
                tag_wait = None
            if tag_wait in (None, ""):
                tag_wait = 4  # default field practice
            enriched: List[Dict[str, Any]] = []
            for s in plan_steps:
                s2 = dict(s)
                if s2.get("tag_required") is True:
                    s2.setdefault("details", {})["verification"] = {"action": "TAG", "required_wait_hr": tag_wait}
                enriched.append(s2)
            plan_steps = enriched
        except Exception:
            logger.exception("tagging/verification enrichment failed")

        # Plan-level notes (existing conditions and operations)
        try:
            notes: Dict[str, Any] = {}
            cond: List[str] = []
            if "CIBP" in mech_set and existing_cibp_ft not in (None, ""):
                cond.append(f"Existing CIBP at {existing_cibp_ft} ft; cap required")
            pk = resolved_facts.get("packer_ft") or {}
            dv = resolved_facts.get("dv_tool_ft") or {}
            pval = pk.get("value") if isinstance(pk, dict) else pk
            dval = dv.get("value") if isinstance(dv, dict) else dv
            if pval not in (None, ""):
                cond.append(f"Packer present at {pval} ft")
            if dval not in (None, ""):
                cond.append(f"DV tool present at {dval} ft")
            if cond:
                notes["existing_conditions"] = cond
            # Operations summary derived from steps
            ops_list: List[str] = []
            try:
                for s in plan_steps:
                    t = s.get("type")
                    if t == "surface_casing_shoe_plug":
                        top = s.get("top_ft"); bot = s.get("bottom_ft")
                        if top is not None and bot is not None:
                            ops_list.append(f"Set surface shoe plug from {bot} to {top} ft")
                    elif t == "cibp_cap":
                        cap = s.get("cap_length_ft") or 20
                        ops_list.append(f"Place {cap} ft cement cap above CIBP")
                    elif t == "uqw_isolation_plug":
                        top = s.get("top_ft"); bot = s.get("bottom_ft")
                        if top is not None and bot is not None:
                            ops_list.append(f"Isolate UQW from {bot} to {top} ft; tag after wait")
                    elif t == "formation_top_plug":
                        fm = s.get("formation") or "formation"
                        top = s.get("top_ft")
                        if top is not None:
                            ops_list.append(f"Spot 100 ft plug at {fm} top around {int(float(top))} ft")
                    elif t == "cement_plug":
                        top = s.get("top_ft"); bot = s.get("bottom_ft")
                        if top is not None and bot is not None:
                            ops_list.append(f"Spot cement plug from {bot} to {top} ft")
                    elif t == "squeeze":
                        top = s.get("top_ft"); bot = s.get("bottom_ft")
                        if top is not None and bot is not None:
                            ops_list.append(f"Perform squeeze from {bot} to {top} ft")
                    elif t == "top_plug":
                        ops_list.append("Set 10 ft top plug")
                    elif t == "cut_casing_below_surface":
                        depth = s.get("depth_ft") or 3
                        ops_list.append(f"Cut casing {depth} ft below surface")
            except Exception:
                pass
            if ops_list:
                notes["operations"] = ops_list
            
            # Check if no formation plugs were generated - warn user to add them manually
            has_formation_plugs = any(s.get("type") == "formation_top_plug" for s in plan_steps)
            if not has_formation_plugs:
                county_name = resolved_facts.get("county") or {}
                county_val = county_name.get("value") if isinstance(county_name, dict) else county_name
                field_name = resolved_facts.get("field") or {}
                field_val = field_name.get("value") if isinstance(field_name, dict) else field_name
                
                warning_msg = f"⚠️ No formation plugs generated for {county_val or 'this'} County"
                if field_val:
                    warning_msg += f" / {field_val} field"
                warning_msg += ". Formation tops may not be available in the policy database for this location. "
                warning_msg += "You can add formation plugs manually using the chat: "
                warning_msg += "'Add formation plugs for [Formation Name] at [depth] ft, [Formation 2] at [depth] ft'"
                
                notes.setdefault("warnings", []).append(warning_msg)
                logger.warning(f"No formation plugs generated for {county_val} County, {field_val} field")
            
            if notes:
                plan["notes"] = notes
        except Exception:
            logger.exception("plan-level notes aggregation failed")

        # CRITICAL: Assign plug_type BEFORE merge so incompatibility checks work!
        prod_toc_val = resolved_facts.get('production_casing_toc_ft') or {}
        production_toc_ft = prod_toc_val.get('value') if isinstance(prod_toc_val, dict) else prod_toc_val
        try:
            production_toc_ft = float(production_toc_ft) if production_toc_ft not in (None, "") else None
        except (ValueError, TypeError):
            production_toc_ft = None
        logger.debug("kernel.plan_from_facts: assigning plug_type and plug_purpose BEFORE merge")
        plan_steps = _assign_plug_types_and_purposes(plan_steps, production_toc_ft)

        # Optionally merge adjacent formation plugs into longer plugs to minimize wait cycles
        try:
            prefs = policy.get("preferences") or {}
            lp = (prefs.get("long_plug_merge") or {}) if isinstance(prefs, dict) else {}
            enabled = bool(lp.get("enabled"))
            threshold_ft = float(lp.get("threshold_ft", 0) or 0)  # Deprecated, kept for backward compat
            types = lp.get("types") or ["formation_top_plug"]
            preserve_tagging = True if lp.get("preserve_tagging", True) is not False else False
            
            # Extract sack limits from policy (NEW: sack-based merging)
            sack_limit_no_tag = float(lp.get("sack_limit_no_tag", 50.0) or 50.0)
            sack_limit_with_tag = float(lp.get("sack_limit_with_tag", 150.0) or 150.0)
            
            logger.info(
                f"Long plug merge config: enabled={enabled}, types={types}, "
                f"sack_limit_no_tag={sack_limit_no_tag}, sack_limit_with_tag={sack_limit_with_tag}"
            )
            
            if enabled:
                plan_steps = _merge_adjacent_plugs(
                    plan_steps,
                    types=types,
                    threshold_ft=threshold_ft,  # Deprecated but kept for compat
                    preserve_tagging=preserve_tagging,
                    sack_limit_no_tag=sack_limit_no_tag,
                    sack_limit_with_tag=sack_limit_with_tag,
                )
        except Exception:
            logger.exception("kernel.long_plug_merge: merge failed; continuing with unmerged steps")

        plan["steps"] = plan_steps
        # If steps exist, compute materials
        if plan["steps"]:
            logger.debug("kernel.plan_from_facts: computing materials for %d steps", len(plan["steps"]))
            
            logger.debug("kernel.plan_from_facts: computing materials for %d steps", len(plan["steps"]))
            plan["steps"] = _compute_materials_for_steps(plan["steps"])  # type: ignore
            
            # Final validation and cleanup pass
            logger.debug("kernel.plan_from_facts: running final validation")
            plan["steps"] = _validate_and_cleanup_steps(plan["steps"], resolved_facts, policy)
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
    logger.debug("Steps generated: %s", plan.get("steps"))
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


def _assign_plug_types_and_purposes(steps: List[Dict[str, Any]], production_toc_ft: Optional[float] = None) -> List[Dict[str, Any]]:
    """
    Assign plug_type (mechanical) and plug_purpose (regulatory) to all steps.
    
    plug_type: One of "spot_plug", "perf_and_squeeze_plug", "perf_and_circulate_plug", "dumbell_plug"
    plug_purpose: Original step type (formation_top_plug, bridge_plug, cement_plug, etc.)
    
    For cement_plug and bridge_plug steps that result from merging, calculate plug_type
    based on the merged interval's depth vs production TOC.
    """
    from .w3a_rules import _determine_plug_type
    
    logger.info(f"Assigning plug_type and plug_purpose to {len(steps)} steps")
    
    for step in steps:
        step_type = step.get("type")
        
        # Preserve original purpose
        if "plug_purpose" not in step:
            step["plug_purpose"] = step_type
        
        # Assign plug_type if not already set
        if "plug_type" not in step or step.get("plug_type") is None:
            # For merged cement_plugs, use the deepest point to determine type
            if step_type == "cement_plug" and step.get("details", {}).get("merged"):
                # Use deepest bottom_ft to determine if spot or perf & squeeze
                step["plug_type"] = _determine_plug_type(step, production_toc_ft)
            elif step_type == "bridge_plug":
                # Bridge plugs are static - just set to None or spot_plug (not a cement type)
                step["plug_type"] = None  # Bridge plugs don't map to cement types
            elif step_type in ("cibp_cap", "bridge_plug_cap"):
                # These are dumbells (3 sacks on tool)
                step["plug_type"] = "dumbell_plug"
            elif step_type == "cut_casing_below_surface":
                # Not a plug type
                step["plug_type"] = None
            elif step_type == "top_plug":
                # Top plugs are spot plugs (at surface)
                step["plug_type"] = "spot_plug"
            elif step_type == "perf_and_circulate_to_surface":
                step["plug_type"] = "perf_and_circulate_plug"
            elif step_type in ("uqw_isolation_plug", "intermediate_casing_shoe_plug", "surface_casing_shoe_plug", "productive_horizon_isolation_plug"):
                # These are determined by depth vs TOC
                step["plug_type"] = _determine_plug_type(step, production_toc_ft)
            elif step_type == "perforate_and_squeeze_plug":
                step["plug_type"] = "perf_and_squeeze_plug"
            # formation_top_plug should already have plug_type from _apply_district_overrides
    
    return steps


def _validate_and_cleanup_steps(steps: List[Dict[str, Any]], facts: Dict[str, Any], policy: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Final validation pass to catch and fix discrepancies before returning plan.
    
    Checks:
    1. Remove duplicate plugs (same type, formation, and overlapping depths)
    2. Enforce Texas 25-sack minimum on all formation plugs
    3. Prevent new user-added plugs below CIBP or cement retainer
    4. Flag violations and auto-correct when possible
    """
    if not steps:
        return steps
    
    logger.info("🔍 VALIDATION: Starting final validation pass")
    validated_steps = []
    removed_count = 0
    corrected_count = 0
    
    # Find CIBP and cement retainer depths (barriers that nothing new should go below)
    cibp_depth = None
    retainer_depth = None
    for step in steps:
        if step.get("type") == "bridge_plug" and not step.get("details", {}).get("existing"):
            cibp_depth = step.get("depth_ft") or step.get("bottom_ft")
        elif step.get("type") == "cement_retainer":
            retainer_depth = step.get("depth_ft") or step.get("bottom_ft")
    
    barrier_depth = None
    if cibp_depth is not None and retainer_depth is not None:
        barrier_depth = min(float(cibp_depth), float(retainer_depth))
    elif cibp_depth is not None:
        barrier_depth = float(cibp_depth)
    elif retainer_depth is not None:
        barrier_depth = float(retainer_depth)
    
    logger.info(f"🔍 VALIDATION: Barrier depth = {barrier_depth}")
    
    # Track seen plugs to detect duplicates
    seen_plugs = {}
    
    for step in steps:
        step_type = step.get("type")
        user_added = step.get("details", {}).get("user_added") or step.get("details", {}).get("batch_added")
        
        # Check 1: Remove duplicate formation plugs
        if step_type == "formation_top_plug":
            formation = step.get("formation") or step.get("details", {}).get("formation")
            top_ft = step.get("top_ft")
            bottom_ft = step.get("bottom_ft")
            
            # Create a key for duplicate detection
            if formation and top_ft is not None and bottom_ft is not None:
                # Round to nearest 10 ft to catch near-duplicates
                top_rounded = round(float(top_ft) / 10) * 10
                bottom_rounded = round(float(bottom_ft) / 10) * 10
                plug_key = (formation, top_rounded, bottom_rounded)
                
                if plug_key in seen_plugs:
                    logger.warning(
                        f"🔍 VALIDATION: Removing duplicate formation plug - "
                        f"{formation} at {top_ft}-{bottom_ft} ft (already have plug at {seen_plugs[plug_key]})"
                    )
                    removed_count += 1
                    continue  # Skip this duplicate
                else:
                    seen_plugs[plug_key] = f"{top_ft}-{bottom_ft}"
        
        # Check 2: Texas 25-sack minimum for formation plugs
        if step_type == "formation_top_plug":
            sacks = step.get("sacks")
            if sacks is not None and sacks < 25:
                logger.warning(
                    f"🔍 VALIDATION: Formation plug has {sacks} sacks (< 25). "
                    f"This should have been caught earlier. Marking for correction."
                )
                step.setdefault("details", {})["validation_issue"] = "below_25_sack_minimum"
                corrected_count += 1
                # Don't remove it, but flag it - materials calculation should have expanded it
        
        # Check 3: New plugs below barrier
        if user_added and barrier_depth is not None:
            plug_bottom = step.get("bottom_ft") or step.get("depth_ft")
            if plug_bottom is not None and float(plug_bottom) > barrier_depth:
                logger.warning(
                    f"🔍 VALIDATION: Removing user-added {step_type} at {plug_bottom} ft - "
                    f"it's below barrier at {barrier_depth} ft"
                )
                removed_count += 1
                continue  # Skip this step
        
        validated_steps.append(step)
    
    logger.info(
        f"🔍 VALIDATION: Complete - Removed {removed_count} duplicates/invalid, "
        f"Flagged {corrected_count} for correction, {len(validated_steps)} steps remain"
    )
    
    return validated_steps


def _estimate_sacks_for_step(step: Dict[str, Any]) -> Optional[float]:
    """
    Estimate sack count for a single step (preliminary calculation for merge decisions).
    
    This runs BEFORE full materials computation to provide sack estimates for merge logic.
    Returns estimated sacks, or None if cannot estimate.
    """
    step_type = step.get("type")
    
    try:
        # Use existing sacks if already calculated
        if step.get("materials", {}).get("slurry", {}).get("sacks"):
            return float(step["materials"]["slurry"]["sacks"])
        
        # For steps without full materials, use geometry-based estimation
        top_ft = step.get("top_ft")
        bottom_ft = step.get("bottom_ft")
        
        if top_ft is None or bottom_ft is None:
            return None
        
        interval_ft = abs(float(bottom_ft) - float(top_ft))
        
        # Get recipe (default to Class H 15.8 ppg)
        recipe_dict = step.get("recipe") or {}
        yield_ft3_per_sk = float(recipe_dict.get("yield_ft3_per_sk", 1.18) or 1.18)
        
        if step_type in ("spot_plug", "cement_plug", "formation_top_plug", "uqw_isolation_plug", "intermediate_casing_shoe_plug"):
            # Cased-hole plug: estimate annular volume
            casing_id_in = step.get("casing_id_in")
            stinger_od_in = step.get("stinger_od_in")
            ann_excess = float(step.get("annular_excess", 0.4) or 0.4)
            
            if casing_id_in is not None and stinger_od_in is not None:
                try:
                    casing_id = float(casing_id_in)
                    stinger_od = float(stinger_od_in)
                    
                    # Annular area ≈ (casing_id² - stinger_od²) / 1029
                    annulus_area = ((casing_id ** 2) - (stinger_od ** 2)) / 1029.0
                    annulus_bbl = annulus_area * interval_ft
                    total_bbl = annulus_bbl * (1.0 + ann_excess)
                    
                    estimated_sacks = total_bbl / yield_ft3_per_sk
                    logger.debug(f"Estimated sacks for {step_type}: {estimated_sacks:.1f} (interval {interval_ft} ft, annulus {annulus_bbl:.1f} bbl)")
                    return estimated_sacks
                except Exception:
                    pass
        
        elif step_type in ("perf_and_squeeze_plug", "squeeze"):
            # Perf & squeeze: estimate annular volume with squeeze factor
            casing_id_in = step.get("casing_id_in")
            stinger_od_in = step.get("stinger_od_in")
            ann_excess = float(step.get("annular_excess", 0.4) or 0.4)
            squeeze_factor = float(step.get("squeeze_factor", 1.5) or 1.5)
            
            if casing_id_in is not None and stinger_od_in is not None:
                try:
                    casing_id = float(casing_id_in)
                    stinger_od = float(stinger_od_in)
                    
                    annulus_area = ((casing_id ** 2) - (stinger_od ** 2)) / 1029.0
                    annulus_bbl = annulus_area * interval_ft
                    total_bbl = annulus_bbl * (1.0 + ann_excess) * squeeze_factor
                    
                    estimated_sacks = total_bbl / yield_ft3_per_sk
                    logger.debug(f"Estimated sacks for {step_type}: {estimated_sacks:.1f} (squeeze factor {squeeze_factor})")
                    return estimated_sacks
                except Exception:
                    pass
    
    except Exception as e:
        logger.warning(f"Could not estimate sacks for {step_type}: {e}")
    
    return None


def _estimate_sacks_for_merged_interval(
    bottom_ft: float,
    top_ft: float,
    casing_id_in: Optional[float],
    stinger_od_in: Optional[float],
    ann_excess: float = 0.4,
    yield_ft3_per_sk: float = 1.18,
) -> Optional[float]:
    """
    Estimate sack count for a merged interval (bottom_ft to top_ft).
    
    Used to calculate sacks required to fill a gap between plugs when determining merge feasibility.
    """
    try:
        if casing_id_in is None or stinger_od_in is None:
            return None
        
        interval_ft = abs(float(top_ft) - float(bottom_ft))
        if interval_ft <= 0:
            return None
        
        casing_id = float(casing_id_in)
        stinger_od = float(stinger_od_in)
        
        annulus_area = ((casing_id ** 2) - (stinger_od ** 2)) / 1029.0
        annulus_bbl = annulus_area * interval_ft
        total_bbl = annulus_bbl * (1.0 + ann_excess)
        
        estimated_sacks = total_bbl / yield_ft3_per_sk
        return estimated_sacks
    
    except Exception as e:
        logger.warning(f"Could not estimate merged interval sacks: {e}")
        return None


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
            logger.debug("materials.compute: type=%s step_keys=%s", step_type, list(step.keys()))
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
                # CRITICAL: If this is a merged perf_and_squeeze plug, use perf_and_squeeze calculation!
                plug_type = step.get("plug_type")
                is_perf_and_squeeze_merged = (plug_type == "perf_and_squeeze_plug" and 
                                               step.get("details", {}).get("merged") is True)
                
                if is_perf_and_squeeze_merged:
                    # Merged perf & squeeze plugs spanning multiple geometries
                    # Segment by casing shoe boundaries and calculate each section separately
                    logger.info(f"📊 Computing materials for MERGED perf_and_squeeze_plug (depths {step.get('top_ft')}-{step.get('bottom_ft')} ft)")
                    
                    top_ft = float(step.get("top_ft", 0) or 0)
                    bottom_ft = float(step.get("bottom_ft", 0) or 0)
                    
                    ex = float(step.get("annular_excess", 0.4))
                    squeeze_factor = float(step.get("squeeze_factor", 1.5) or 1.5)
                    rounding_mode = (step.get("recipe", {}) or {}).get("rounding") or "up"
                    
                    total_bbl = 0.0
                    segments_calc: List[Dict[str, Any]] = []
                    
                    # Get casing strings to identify shoe boundaries
                    casing_strings = resolved_facts.get("casing_strings") or []
                    prod_casing = next((c for c in casing_strings if c.get("string", "").lower().startswith("production")), None)
                    inter_casing = next((c for c in casing_strings if c.get("string", "").lower().startswith("intermediate")), None)
                    surf_casing = next((c for c in casing_strings if c.get("string", "").lower().startswith("surface")), None)
                    
                    prod_toc = prod_casing.get("cement_top_ft") if prod_casing else None
                    inter_shoe = inter_casing.get("bottom_ft") if inter_casing else None
                    surf_shoe = surf_casing.get("bottom_ft") if surf_casing else None
                    
                    logger.info(f"Well geometry: Prod TOC={prod_toc}, Inter shoe={inter_shoe}, Surf shoe={surf_shoe}")
                    
                    # Identify segment boundaries (shoes and TOC)
                    boundaries = sorted(set([b for b in [prod_toc, inter_shoe, surf_shoe] if b is not None and bottom_ft < b < top_ft]))
                    logger.info(f"Segment boundaries within interval: {boundaries}")
                    
                    # Build segment intervals
                    seg_tops = [top_ft] + boundaries
                    seg_bots = boundaries + [bottom_ft]
                    
                    from .w3a_rules import _get_casing_strings_at_depth
                    
                    for seg_top, seg_bot in zip(seg_tops, seg_bots):
                        if seg_top <= seg_bot:  # Skip if invalid
                            continue
                        
                        seg_len = seg_top - seg_bot
                        if seg_len <= 0:
                            continue
                        
                        # Determine casing context at this segment
                        casing_context = _get_casing_strings_at_depth(resolved_facts, seg_bot)
                        logger.info(f"Segment {seg_top}→{seg_bot} ft: {casing_context.get('context')}")
                        
                        seg_bbl = 0.0
                        seg_info: Dict[str, Any] = {
                            "top_ft": seg_top,
                            "bottom_ft": seg_bot,
                            "length_ft": seg_len,
                        }
                        
                        if casing_context.get("context") == "annulus_squeeze":
                            # Two strings: annulus squeeze (cement between casings)
                            inner_str = casing_context.get("inner_string", {})
                            outer_str = casing_context.get("outer_string", {})
                            inner_id = inner_str.get("id_in")
                            outer_id = outer_str.get("id_in")
                            
                            if inner_id and outer_id:
                                ann_cap = annulus_capacity_bbl_per_ft(outer_id, inner_id)
                                base_bbl = seg_len * ann_cap
                                seg_bbl = base_bbl * (1.0 + ex) * squeeze_factor
                                
                                depth_kft = int((seg_bot + 999.0) / 1000.0)
                                texas_excess = 1.0 + (0.10 * depth_kft)
                                seg_bbl *= texas_excess
                                
                                seg_info.update({
                                    "context": "annulus_squeeze",
                                    "inner_casing": inner_str.get("name"),
                                    "inner_size_in": inner_str.get("size_in"),
                                    "outer_casing": outer_str.get("name"),
                                    "outer_size_in": outer_str.get("size_in"),
                                    "annular_capacity_bbl_per_ft": ann_cap,
                                    "base_bbl": base_bbl,
                                    "squeeze_factor": squeeze_factor,
                                    "texas_excess": texas_excess,
                                    "segment_bbl": seg_bbl,
                                })
                        
                        elif casing_context.get("context") == "open_hole_squeeze":
                            # One string: open-hole squeeze (cement into formation around casing)
                            inner_str = casing_context.get("inner_string", {})
                            inner_od = inner_str.get("size_in")
                            
                            # Get hole size from casing record
                            hole_size = prod_casing.get("hole_size_in") if prod_casing else None
                            
                            if hole_size and inner_od:
                                ann_cap = annulus_capacity_bbl_per_ft(float(hole_size), float(inner_od))
                                base_bbl = seg_len * ann_cap
                                seg_bbl = base_bbl * (1.0 + ex) * squeeze_factor
                                
                                depth_kft = int((seg_bot + 999.0) / 1000.0)
                                texas_excess = 1.0 + (0.10 * depth_kft)
                                seg_bbl *= texas_excess
                                
                                seg_info.update({
                                    "context": "open_hole_squeeze",
                                    "casing_name": inner_str.get("name"),
                                    "casing_od_in": inner_od,
                                    "hole_size_in": hole_size,
                                    "annular_capacity_bbl_per_ft": ann_cap,
                                    "base_bbl": base_bbl,
                                    "squeeze_factor": squeeze_factor,
                                    "texas_excess": texas_excess,
                                    "segment_bbl": seg_bbl,
                                })
                        
                        total_bbl += seg_bbl
                        segments_calc.append(seg_info)
                    
                    # Add formation info to details
                    segments_calc.append({
                        "merged_formations": [m.get("formation") for m in step.get("details", {}).get("merged_steps", [])],
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
                    step["sacks"] = int(vb.sacks)
                    step.setdefault("details", {})["segments_calc"] = segments_calc
                else:
                    # Generic cement plug (not perf_and_squeeze merged)
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
                # surface sacks at top-level for convenience
                try:
                    step["sacks"] = int(vb.sacks)
                except Exception:
                    pass
                if segments_calc:
                    materials["segments"] = segments_calc
            elif step_type in ("perforate_and_squeeze_plug",):
                # Two-part compound plug: squeeze behind casing + cement cap inside casing
                # Calculate materials for both components
                details = step.get("details", {})
                perf_interval = details.get("perforation_interval", {})
                cap_interval = details.get("cement_cap_inside_casing", {})
                casing_id = step.get("casing_id_in")
                stinger_od = step.get("stinger_od_in")
                
                if casing_id is not None and stinger_od is not None:
                    squeeze_bbl = 0.0
                    cap_bbl = 0.0
                    
                    # Squeeze portion (behind casing) - use decision tree for context
                    if perf_interval:
                        perf_top = perf_interval.get("top_ft", 0)
                        perf_bot = perf_interval.get("bottom_ft", 0)
                        perf_len = abs(perf_top - perf_bot)
                        if perf_len > 0:
                            # Import decision tree function
                            try:
                                from apps.kernel.services.w3a_rules import _get_casing_strings_at_depth
                                casing_context = _get_casing_strings_at_depth(facts, perf_bot)
                            except Exception as e:
                                logger.warning(f"Failed to get casing context: {e}")
                                casing_context = {"context": "unknown", "count": 0}
                            
                            # Texas TAC §3.14(d)(11): 1 + 10% per 1000 ft of depth
                            # At 5000 ft: 1 + (10% × 5) = 1.5x
                            # At 10,000 ft: 1 + (10% × 10) = 2.0x
                            depth_kft = int((perf_bot + 999.0) / 1000.0)  # Round up to next kft
                            texas_excess_factor = 1.0 + (0.10 * depth_kft)
                            
                            # Store context in details for transparency
                            details["squeeze_context"] = casing_context.get("context", "unknown")
                            details["texas_excess_factor"] = texas_excess_factor
                            details["depth_kft"] = depth_kft
                            
                            # Calculate annular capacity based on context
                            if casing_context["context"] == "annulus_squeeze":
                                # TWO STRINGS: Cement between inner string OD and outer string ID
                                inner = casing_context.get("inner_string", {})
                                outer = casing_context.get("outer_string", {})
                                
                                if outer.get("id_in") and inner.get("size_in"):
                                    # Use outer string ID (annulus) vs inner string OD
                                    outer_id = float(outer["id_in"])
                                    inner_od = float(inner["size_in"])
                                    ann_cap = annulus_capacity_bbl_per_ft(outer_id, inner_od)
                                    details["geometry_for_squeeze"] = {
                                        "outer_string": outer["name"],
                                        "outer_id_in": outer_id,
                                        "inner_string": inner["name"],
                                        "inner_od_in": inner_od,
                                        "context": "annulus_squeeze"
                                    }
                                    logger.info(
                                        f"Annulus squeeze: {inner['name']} {inner_od}\" OD inside "
                                        f"{outer['name']} {outer_id}\" ID at {perf_bot} ft"
                                    )
                                else:
                                    # Fallback: use production casing ID
                                    ann_cap = annulus_capacity_bbl_per_ft(float(casing_id), float(stinger_od))
                                    details["geometry_for_squeeze"] = {
                                        "casing_id_in": float(casing_id),
                                        "tubing_od_in": float(stinger_od),
                                        "context": "annulus_squeeze_fallback"
                                    }
                                    
                            elif casing_context["context"] == "open_hole_squeeze":
                                # ONE STRING: Cement into formation (use hole diameter)
                                inner = casing_context.get("inner_string", {})
                                
                                # Try to get hole size from casing record
                                prod_casing = next((c for c in (facts.get("casing_strings") or []) if c.get("string") == "production"), None)
                                hole_size = prod_casing.get("hole_size_in") if prod_casing else None
                                
                                if hole_size and inner.get("size_in"):
                                    inner_od = float(inner["size_in"])
                                    ann_cap = annulus_capacity_bbl_per_ft(float(hole_size), inner_od)
                                    details["geometry_for_squeeze"] = {
                                        "hole_size_in": float(hole_size),
                                        "casing_od_in": inner_od,
                                        "context": "open_hole_squeeze"
                                    }
                                    logger.info(
                                        f"Open-hole squeeze: {inner['name']} {inner_od}\" OD in "
                                        f"{hole_size}\" hole at {perf_bot} ft"
                                    )
                                else:
                                    # Fallback: estimate hole size as casing OD + 2"
                                    inner_od = float(inner.get("size_in", casing_id)) if inner.get("size_in") else float(casing_id)
                                    estimated_hole = inner_od + 2.0
                                    ann_cap = annulus_capacity_bbl_per_ft(estimated_hole, inner_od)
                                    details["geometry_for_squeeze"] = {
                                        "estimated_hole_size_in": estimated_hole,
                                        "casing_od_in": inner_od,
                                        "context": "open_hole_squeeze_estimated"
                                    }
                            else:
                                # Unknown or open hole (no casing) - use default casing geometry
                                ann_cap = annulus_capacity_bbl_per_ft(float(casing_id), float(stinger_od))
                                details["geometry_for_squeeze"] = {
                                    "casing_id_in": float(casing_id),
                                    "tubing_od_in": float(stinger_od),
                                    "context": "default"
                                }
                            
                            # Calculate squeeze volume with Texas depth-based excess
                            base_volume = perf_len * ann_cap
                            squeeze_bbl = base_volume * texas_excess_factor
                            
                            details["squeeze_calculation"] = {
                                "perf_length_ft": perf_len,
                                "annular_capacity_bbl_per_ft": ann_cap,
                                "base_volume_bbl": base_volume,
                                "texas_excess_factor": texas_excess_factor,
                                "final_volume_bbl": squeeze_bbl
                            }
                    
                    # Cement cap portion (inside casing above perfs) - typically 50 ft
                    if cap_interval:
                        cap_top = cap_interval.get("top_ft", 0)
                        cap_bot = cap_interval.get("bottom_ft", 0)
                        cap_len = abs(cap_top - cap_bot)
                        if cap_len > 0:
                            # Cap uses standard cased excess
                            cap_excess = float(step.get("annular_excess", 0.4))
                            ann_cap = annulus_capacity_bbl_per_ft(float(casing_id), float(stinger_od))
                            cap_bbl = cap_len * ann_cap * (1.0 + cap_excess)
                    
                    total_bbl = squeeze_bbl + cap_bbl
                    rounding_mode = (step.get("recipe", {}) or {}).get("rounding") or "up"  # Round up for safety
                    vb = compute_sacks(total_bbl, recipe, rounding=rounding_mode)
                    materials["slurry"] = {
                        "total_bbl": total_bbl,
                        "squeeze_bbl": squeeze_bbl,
                        "cap_bbl": cap_bbl,
                        "ft3": vb.ft3,
                        "sacks": vb.sacks,
                        "water_bbl": vb.water_bbl,
                        "additives": vb.additives,
                        "explain": vb.explain,
                    }
                    # surface sacks at top-level for convenience
                    try:
                        step["sacks"] = int(vb.sacks)
                    except Exception:
                        pass
            elif step_type in ("surface_casing_shoe_plug", "intermediate_casing_shoe_plug", "uqw_isolation_plug", "formation_top_plug", "productive_horizon_isolation_plug"):
                # Treat as cased-hole interval calculation using casing ID vs stinger OD
                top_ft = step.get("top_ft")
                bottom_ft = step.get("bottom_ft")
                casing_id = step.get("casing_id_in")
                stinger_od = step.get("stinger_od_in")
                if top_ft is not None and bottom_ft is not None and casing_id is not None and stinger_od is not None:
                    t = float(top_ft)
                    b = float(bottom_ft)
                    interval_ft = abs(b - t)
                    # For these special cased steps, default to cased excess (0.4) unless explicitly provided
                    ex = float(step.get("annular_excess", 0.4))
                    cap = annulus_capacity_bbl_per_ft(float(casing_id), float(stinger_od))
                    base_volume_bbl = interval_ft * cap * (1.0 + ex)
                    
                    # TAC §3.14(d)(11): +10% per 1000 ft of DEPTH (not length!)
                    # Use bottom depth (deeper point) for calculating the depth-based excess
                    depth_kft = int((b + 999.0) / 1000.0)
                    texas_excess_factor = 1.0 + (0.10 * depth_kft)
                    total_bbl = base_volume_bbl * texas_excess_factor
                    rounding_mode = (step.get("recipe", {}) or {}).get("rounding") or "nearest"
                    vb = compute_sacks(total_bbl, recipe, rounding=rounding_mode)
                    materials["slurry"] = {
                        "total_bbl": total_bbl,
                        "ft3": vb.ft3,
                        "sacks": vb.sacks,
                        "water_bbl": vb.water_bbl,
                        "additives": vb.additives,
                        "explain": vb.explain,
                    }
                    # surface sacks at top-level for convenience
                    try:
                        step["sacks"] = int(vb.sacks)
                    except Exception:
                        pass
                    step.setdefault("explain", {}).update({
                        "path": "cased_annulus",
                        "cap_bbl_per_ft": cap,
                        "interval_ft": interval_ft,
                        "excess_used": ex,
                        "texas_excess_factor": texas_excess_factor,
                        "depth_kft": depth_kft,
                        "rounding": rounding_mode,
                    })
                    # annotate geometry used
                    step.setdefault("details", {})["geometry_used"] = {
                        "annulus": "production_casing_id_vs_stinger_od",
                        "casing_id_in": float(casing_id),
                        "stinger_od_in": float(stinger_od),
                    }
            elif step_type == "perf_and_circulate_to_surface":
                # Perforate inner string, circulate cement up outer annulus to surface
                # Geometry: outer casing ID vs inner casing OD
                top_ft = step.get("top_ft")
                bottom_ft = step.get("bottom_ft")
                outer_id = step.get("outer_casing_id_in")
                inner_od = step.get("inner_casing_od_in")
                
                if top_ft is not None and bottom_ft is not None and outer_id is not None and inner_od is not None:
                    t = float(top_ft)
                    b = float(bottom_ft)
                    interval_ft = abs(b - t)
                    
                    # Annulus capacity between outer ID and inner OD
                    ann_cap = annulus_capacity_bbl_per_ft(float(outer_id), float(inner_od))
                    
                    # Texas depth excess using bottom depth (shoe)
                    depth_kft = int((b + 999.0) / 1000.0)
                    texas_excess_factor = 1.0 + (0.10 * depth_kft)
                    
                    # Operational top-off for circulation to ensure surface returns
                    operational_topoff = float(step.get("operational_topoff", 1.05))  # Default 5%
                    
                    # Total volume
                    base_volume_bbl = interval_ft * ann_cap
                    total_bbl = base_volume_bbl * texas_excess_factor * operational_topoff
                    
                    # Round to nearest 5 or 10 sacks for surface jobs
                    rounding_mode = (step.get("recipe", {}) or {}).get("rounding") or "nearest"
                    vb = compute_sacks(total_bbl, recipe, rounding=rounding_mode)
                    
                    # Round to nearest 5 sacks for operational convenience
                    sacks_rounded = int(round(vb.sacks / 5.0) * 5)
                    
                    materials["slurry"] = {
                        "total_bbl": total_bbl,
                        "base_volume_bbl": base_volume_bbl,
                        "ft3": vb.ft3,
                        "sacks": sacks_rounded,
                        "sacks_unrounded": vb.sacks,
                        "water_bbl": vb.water_bbl,
                        "additives": vb.additives,
                        "explain": vb.explain,
                    }
                    
                    step["sacks"] = sacks_rounded
                    
                    step.setdefault("explain", {}).update({
                        "path": "annulus_circulation_to_surface",
                        "annular_capacity_bbl_per_ft": ann_cap,
                        "interval_ft": interval_ft,
                        "texas_excess_factor": texas_excess_factor,
                        "depth_kft": depth_kft,
                        "operational_topoff": operational_topoff,
                        "rounding": "nearest_5_sacks",
                    })
                    
                    step.setdefault("details", {})["geometry_used"] = {
                        "annulus": f"{step.get('outer_string')}_id_vs_{step.get('inner_string')}_od",
                        "outer_casing_id_in": float(outer_id),
                        "inner_casing_od_in": float(inner_od),
                    }
                    
                    logger.info(
                        f"Calculated perf_and_circulate_to_surface: {interval_ft:.1f} ft × {ann_cap:.4f} bbl/ft "
                        f"× {texas_excess_factor:.2f} × {operational_topoff:.2f} = {total_bbl:.2f} bbl = {sacks_rounded} sacks"
                    )
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
                # surface sacks at top-level for convenience unless explicitly overridden
                try:
                    if step.get("sacks") in (None, ""):
                        step["sacks"] = int(vb.sacks)
                except Exception:
                    pass
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
            logger.exception("materials.compute: error type=%s err=%s", step_type, e)
            step.setdefault("errors", []).append(str(e))

        step["materials"] = materials
        
        # TEXAS RULE: 25-sack minimum for cement-based plugs (excluding CIBP caps and large operations)
        # Apply to all cement plugs, formation plugs, squeeze plugs (except bridge_plug_cap/cibp_cap/large surface ops)
        if step_type not in ("bridge_plug_cap", "cibp_cap", "bridge_plug", "cement_retainer", "perf_and_circulate_to_surface"):
            try:
                calculated_sacks = materials.get("slurry", {}).get("sacks")
                if calculated_sacks is not None and isinstance(calculated_sacks, (int, float)):
                    if calculated_sacks < 25:
                        original_sacks = calculated_sacks
                        materials["slurry"]["sacks"] = 25
                        step["sacks"] = 25
                        step.setdefault("details", {})["texas_25_sack_minimum_applied"] = True
                        step["details"]["original_calculated_sacks"] = original_sacks
                        logger.warning(
                            f"Texas 25-sack minimum applied to {step_type} at {step.get('top_ft')}-{step.get('bottom_ft')} ft: "
                            f"calculated {original_sacks:.1f} sacks, bumped to 25 sacks"
                        )
            except Exception as e:
                logger.warning(f"Failed to apply 25-sack minimum to {step_type}: {e}")
        
        out.append(step)
    return out


def _merge_adjacent_plugs(
    steps: List[Dict[str, Any]],
    types: List[str],
    threshold_ft: float = None,  # Deprecated: kept for backward compatibility
    preserve_tagging: bool = True,
    sack_limit_no_tag: float = 50.0,  # Max sacks to merge plugs WITHOUT tag
    sack_limit_with_tag: float = 150.0,  # Max sacks to merge plugs WITH tag
) -> List[Dict[str, Any]]:
    """Merge adjacent cement-bearing steps based on sack count requirements.
    
    NEW logic (sack-based instead of depth-based):
    - Calculate estimated sacks for each step
    - When considering merge: sum sacks of plugs + sacks needed to fill gap between them
    - If total ≤ sack_limit (50 for no tag, 150 with tag), merge is allowed
    - If any plug in group has tag_required=True, merged plug inherits tag_required=True
    
    CRITICAL CONSTRAINT: Spot plugs and perf & squeeze plugs CANNOT be merged together.
    They represent fundamentally different mechanical operations:
    - Spot plugs: cement injected INSIDE casing only (below TOC)
    - Perf & squeeze plugs: perforate + squeeze behind pipe (above TOC)
    """
    if not steps or (threshold_ft is not None and threshold_ft <= 0):
        return steps
    # Work on a shallow copy to avoid mutating input
    src: List[Dict[str, Any]] = list(steps)
    # Partition by mergeable vs non-mergeable
    mergeable: List[Dict[str, Any]] = []
    fixed: List[Dict[str, Any]] = []
    for s in src:
        # Treat surface shoe and top plug as mergeable when cross-type enabled
        merge_ok = s.get("type") in types or s.get("type") in ("surface_casing_shoe_plug", "top_plug")
        if merge_ok and (s.get("top_ft") is not None) and (s.get("bottom_ft") is not None):
            mergeable.append(s)
        else:
            fixed.append(s)
    if not mergeable:
        return steps
    # Sort mergeable by depth (ascending by bottom)
    def _key(s: Dict[str, Any]) -> float:
        try:
            b = float(s.get("bottom_ft"))
            t = float(s.get("top_ft"))
            # sort by deep (low) first for predictable grouping
            return min(b, t)
        except Exception:
            return 0.0
    ordered = sorted(mergeable, key=_key)
    merged: List[Dict[str, Any]] = []
    buf: List[Dict[str, Any]] = []

    def _flush(buf: List[Dict[str, Any]]) -> None:
        if not buf:
            return
        if len(buf) == 1:
            merged.append(buf[0])
            return
        # Merge buffer into one long plug
        tops = [float(x.get("top_ft")) for x in buf]
        bots = [float(x.get("bottom_ft")) for x in buf]
        top_ft = max(tops)  # shallower top (smaller depth) is larger number if top>bottom; we keep numeric max
        bottom_ft = min(bots)
        # Choose canonical merged type and plug_type with precedence rules
        out: Dict[str, Any] = dict(buf[0])
        out_type = "cement_plug" if out.get("type") != "cement_plug" else out.get("type")
        out["type"] = out_type
        
        # Determine merged plug_type with dominance rules:
        # Precedence order: perf_and_circulate > perf_and_squeeze > spot > dumbell
        plug_types_in_buf = [x.get("plug_type") for x in buf]
        merged_plug_type = None
        
        # Check for highest precedence first
        if "perf_and_circulate_plug" in plug_types_in_buf:
            merged_plug_type = "perf_and_circulate_plug"
            logger.info("Merge: perf_and_circulate_plug takes precedence (reaches surface)")
        elif "perf_and_squeeze_plug" in plug_types_in_buf:
            merged_plug_type = "perf_and_squeeze_plug"
            logger.info("Merge: perf_and_squeeze_plug takes precedence over spot/dumbell")
        elif "spot_plug" in plug_types_in_buf:
            merged_plug_type = "spot_plug"
            logger.info("Merge: spot_plug takes precedence over dumbell")
        elif "dumbell_plug" in plug_types_in_buf:
            merged_plug_type = "dumbell_plug"
            logger.info("Merge: all plugs are dumbell type")
        
        if merged_plug_type:
            out["plug_type"] = merged_plug_type
        # If any member is a surface shoe or top plug, treat as surface-cased geometry for capacity
        ctx = "cased_production"
        if any(x.get("type") in ("surface_casing_shoe_plug", "top_plug") for x in buf):
            ctx = "cased_surface"
        out["geometry_context"] = out.get("geometry_context") or ctx
        out["top_ft"] = top_ft
        out["bottom_ft"] = bottom_ft
        # Merge citations and details
        rb: List[str] = []
        for x in buf:
            for c in (x.get("regulatory_basis") or []):
                if isinstance(c, str):
                    rb.append(c)
        out["regulatory_basis"] = sorted(list({r for r in rb if r}))
        # Tag propagation: if ANY plug in merged group has tag_required, merged plug inherits it
        if preserve_tagging and any(x.get("tag_required") is True for x in buf):
            out["tag_required"] = True
            logger.info(f"Merged plug inherits tag_required=True (one or more plugs in group required tagging)")
        # Record merged sources
        out.setdefault("details", {})["merged"] = True
        out["details"]["merged_steps"] = [
            {"formation": x.get("formation"), "top_ft": x.get("top_ft"), "bottom_ft": x.get("bottom_ft")}
            for x in buf
        ]
        # Remove per-formation placement_basis; keep a compact note
        out["placement_basis"] = out.get("placement_basis") or "Merged adjacent formation plugs"
        
        # Preserve geometry fields from any of the merged plugs (for materials calculation)
        # Iterate through all merged plugs and use first available geometry value
        if "casing_id_in" not in out or out.get("casing_id_in") is None:
            for x in buf:
                if x.get("casing_id_in") is not None:
                    out["casing_id_in"] = x.get("casing_id_in")
                    break
        if "stinger_od_in" not in out or out.get("stinger_od_in") is None:
            for x in buf:
                if x.get("stinger_od_in") is not None:
                    out["stinger_od_in"] = x.get("stinger_od_in")
                    break
        if "annular_excess" not in out or out.get("annular_excess") is None:
            for x in buf:
                if x.get("annular_excess") is not None:
                    out["annular_excess"] = x.get("annular_excess")
                    break
        if "recipe" not in out or out.get("recipe") is None:
            for x in buf:
                if x.get("recipe") is not None:
                    out["recipe"] = x.get("recipe")
                    break
        
        merged.append(out)

    # Sweep and group when sack count ≤ threshold
    prev: Dict[str, Any] | None = None
    for s in ordered:
        if prev is None:
            buf = [s]
            prev = s
            continue
        try:
            p_top = float(prev.get("top_ft")); p_bot = float(prev.get("bottom_ft"))
            s_top = float(s.get("top_ft")); s_bot = float(s.get("bottom_ft"))
            # Represent intervals as [low, high] where low = deep, high = shallow
            p_low, p_high = min(p_top, p_bot), max(p_top, p_bot)
            s_low, s_high = min(s_top, s_bot), max(s_top, s_bot)
            
            # CRITICAL: Check plug_type compatibility FIRST
            prev_plug_type = prev.get("plug_type")
            s_plug_type = s.get("plug_type")
            
            # Define incompatible combinations (bidirectional)
            incompatible_pairs = {
                ("spot_plug", "perf_and_squeeze_plug"),
                ("perf_and_squeeze_plug", "spot_plug"),
                ("perf_and_squeeze_plug", "dumbell_plug"),
                ("dumbell_plug", "perf_and_squeeze_plug"),
                ("spot_plug", "perf_and_circulate_plug"),
                ("perf_and_circulate_plug", "spot_plug"),
                ("perf_and_circulate_plug", "dumbell_plug"),
                ("dumbell_plug", "perf_and_circulate_plug"),
            }
            
            # Check if combination is blocked
            if (prev_plug_type, s_plug_type) in incompatible_pairs:
                logger.warning(
                    f"Cannot merge {prev_plug_type} and {s_plug_type} plugs - "
                    f"incompatible mechanical operations. Flushing buffer and starting new group."
                )
                _flush(buf)
                buf = [s]
                prev = s
                continue
            
            # NEW: Check if tag is required in buffer
            has_tag_required = any(x.get("tag_required") is True for x in buf) or s.get("tag_required") is True
            applicable_sack_limit = sack_limit_with_tag if has_tag_required else sack_limit_no_tag
            
            # Calculate sacks for current buffer + gap + new step
            buf_total_sacks = sum(_estimate_sacks_for_step(x) or 0 for x in buf)
            s_sacks = _estimate_sacks_for_step(s) or 0
            
            # Sacks needed to fill gap between deepest plug in buffer and this step
            # Use first step in buffer for geometry (should be consistent for formation plugs)
            gap_sacks = 0.0
            if buf and p_high < s_low:  # There is a gap
                gap_size = s_low - p_high
                # Use geometry from first plug in buffer
                first_in_buf = buf[0]
                casing_id = first_in_buf.get("casing_id_in")
                stinger_od = first_in_buf.get("stinger_od_in")
                ann_excess = float(first_in_buf.get("annular_excess", 0.4) or 0.4)
                recipe_dict = first_in_buf.get("recipe") or {}
                yield_ft3_per_sk = float(recipe_dict.get("yield_ft3_per_sk", 1.18) or 1.18)
                
                gap_sacks = _estimate_sacks_for_merged_interval(
                    s_low, p_high, casing_id, stinger_od, ann_excess, yield_ft3_per_sk
                ) or 0.0
            
            total_merge_sacks = buf_total_sacks + gap_sacks + s_sacks
            
            # NEW: Check max plug length (1250 ft) and max plugs per combination (3)
            # Calculate merged interval if we were to merge
            merged_buf_top = max(float(x.get("top_ft", 0)) for x in buf + [s])
            merged_buf_bot = min(float(x.get("bottom_ft", 0)) for x in buf + [s])
            merged_length = abs(merged_buf_top - merged_buf_bot)
            merged_count = len(buf) + 1
            
            logger.debug(
                f"Merge decision: tag_required={has_tag_required}, limit={applicable_sack_limit}, "
                f"buf={buf_total_sacks:.0f} + gap={gap_sacks:.0f} + new={s_sacks:.0f} = {total_merge_sacks:.0f} sacks, "
                f"length={merged_length:.0f} ft, count={merged_count}"
            )
            
            # Check all merge constraints
            merge_blocked = False
            block_reason = ""
            
            # Constraint 1: Max sack limit
            if total_merge_sacks > applicable_sack_limit:
                merge_blocked = True
                block_reason = f"sacks {total_merge_sacks:.0f} > limit {applicable_sack_limit}"
            
            # Constraint 2: Max plug length (1250 ft)
            elif merged_length > 1250.0:
                merge_blocked = True
                block_reason = f"length {merged_length:.0f} ft > 1250 ft max"
            
            # Constraint 3: Max plugs per combination (3)
            elif merged_count > 3:
                merge_blocked = True
                block_reason = f"count {merged_count} plugs > 3 max"
            
            if not merge_blocked:
                buf.append(s)
                prev = s
                logger.debug(f"✅ Merged (all constraints passed)")
                continue
            else:
                logger.debug(f"❌ Cannot merge ({block_reason})")
        except Exception as e:
            logger.exception(f"Error in merge logic: {e}")
        
        # Flush current group and start new
        _flush(buf)
        buf = [s]
        prev = s
    _flush(buf)

    # Return fixed + merged, preserving relative order where possible
    # Place merged blocks where the first member originally appeared
    # For simplicity v1: append merged after fixed and let materials compute reorder by depth
    out_all = fixed + merged
    return out_all


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


def _apply_step_defaults(steps: List[Dict[str, Any]], preferences: Dict[str, Any], resolved_facts: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    geometry_defaults: Dict[str, Dict[str, Any]] = preferences.get("geometry_defaults", {}) if isinstance(preferences, dict) else {}
    default_recipe: Dict[str, Any] = preferences.get("default_recipe", {}) if isinstance(preferences, dict) else {}
    FALLBACK_RECIPE: Dict[str, Any] = {
        "id": "class_h_neat_15_8",
        "class": "H",
        "density_ppg": 15.8,
        "yield_ft3_per_sk": 1.18,
        "water_gal_per_sk": 5.2,
        "additives": [],
    }
    
    out: List[Dict[str, Any]] = []
    for s in steps:
        logger.debug("defaults.apply: type=%s before=%s", s.get("type"), {k: s.get(k) for k in ("geometry_context","casing_id_in","stinger_od_in","hole_d_in","recipe")})
        # attach geometry defaults per step type, context-aware to avoid leaking cased keys into open-hole
        g = geometry_defaults.get(s.get("type"), {})
        
        # Apply geometry defaults to all step types except cement_plug (which has special context handling)
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
        if "recipe" not in s:
            if default_recipe:
                s["recipe"] = default_recipe
            else:
                # attach deterministic fallback and record finding for visibility
                s["recipe"] = dict(FALLBACK_RECIPE)
                s.setdefault("findings", []).append({
                    "code": "MISSING_RECIPE",
                    "severity": "major",
                    "message": "No step.recipe and no preferences.default_recipe; applied fallback recipe",
                })
        # propagate rounding preference onto step.recipe if missing
        rounding_pref = (preferences.get("rounding_policy") or "nearest") if isinstance(preferences, dict) else "nearest"
        if isinstance(s.get("recipe"), dict) and "rounding" not in s["recipe"]:
            s["recipe"]["rounding"] = rounding_pref
        logger.debug("defaults.apply: type=%s after=%s", s.get("type"), {k: s.get(k) for k in ("geometry_context","casing_id_in","stinger_od_in","hole_d_in","recipe")})
        out.append(s)
    return out


def _apply_district_overrides(
    steps: List[Dict[str, Any]],
    policy_effective: Dict[str, Any],
    preferences: Dict[str, Any],
    district: Any,
    county: Any,
) -> List[Dict[str, Any]]:
    overrides = policy_effective.get("district_overrides") or {}
    # use preferences provided by caller (policy.preferences with W-2 geometry), not from effective
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
            center_ft = float(ft.get("top_ft"))
            plug_required = ft.get("plug_required") is True
            if not plug_required or formation is None:
                continue
            # Use symmetric interval around the formation top based on required min length
            min_len = float(reqs.get("surface_casing_shoe_plug_min_ft", {}).get("value", 50)) if isinstance(reqs.get("surface_casing_shoe_plug_min_ft"), dict) else 50.0
            half = max(min_len / 2.0, 0.0)
            s_top = center_ft + half
            s_bot = center_ft - half
            step = {
                "type": "formation_top_plug",
                "plug_purpose": "formation_top_plug",  # NEW: Preserve original purpose
                "formation": formation,
                "top_ft": s_top,
                "bottom_ft": s_bot,
                "min_length_ft": min_len,
                "regulatory_basis": [
                    f"rrc.district.{str(district).lower()}.{str(county).lower() if county else 'unknown'}:formation_top:{formation}"
                ],
                "placement_basis": f"Formation transition: {formation} (±{int(half)} ft)",
                "details": {"center_ft": center_ft},
            }
            
            # Determine plug_type based on depth vs production TOC
            # Import locally to avoid circular dependency
            from .w3a_rules import _determine_plug_type
            prod_toc_val = resolved_facts.get('production_casing_toc_ft') or {}
            production_toc_ft = prod_toc_val.get('value') if isinstance(prod_toc_val, dict) else prod_toc_val
            try:
                production_toc_ft = float(production_toc_ft) if production_toc_ft not in (None, "") else None
            except (ValueError, TypeError):
                production_toc_ft = None
            
            step["plug_type"] = _determine_plug_type(step, production_toc_ft)
            
            if ft.get("tag_required") is True or formation in ("San Andres", "Coleman Junction"):
                step["tag_required"] = True
            # Attach available W-2-derived geometry to enable materials computation downstream
            try:
                gdefs = (preferences.get("geometry_defaults") or {})
                # prefer explicit formation_top_plug geometry; else reuse cement_plug or squeeze geometry
                g = gdefs.get("formation_top_plug") or gdefs.get("cement_plug") or gdefs.get("squeeze") or {}
                if g.get("casing_id_in") is not None and g.get("stinger_od_in") is not None:
                    step["casing_id_in"] = float(g.get("casing_id_in"))
                    step["stinger_od_in"] = float(g.get("stinger_od_in"))
                if step.get("annular_excess") is None and g.get("annular_excess") is not None:
                    step["annular_excess"] = float(g.get("annular_excess"))
                # Ensure a usable slurry recipe is present so materials can compute sacks
                default_recipe = (preferences.get("default_recipe") or {})
                if default_recipe and "recipe" not in step:
                    step["recipe"] = default_recipe
                # Pre-populate explanatory fields for transparency before compute
                step.setdefault("details", {})["geometry_used"] = {
                    "annulus": "production_casing_id_vs_stinger_od",
                    "casing_id_in": float(step.get("casing_id_in")) if step.get("casing_id_in") is not None else None,
                    "stinger_od_in": float(step.get("stinger_od_in")) if step.get("stinger_od_in") is not None else None,
                }
                step.setdefault("explain", {}).update({
                    "path": "cased_annulus",
                    # interval/excess/capacity values are filled during compute; marker keys improve debuggability
                })
            except Exception:
                pass
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
            "interval_ft": length if length > 0 else float(geom_default.get("interval_ft", 100.0)),
            "casing_id_in": float(geom_default.get("casing_id_in")) if geom_default.get("casing_id_in") is not None else None,
            "stinger_od_in": float(geom_default.get("stinger_od_in")) if geom_default.get("stinger_od_in") is not None else None,
            "squeeze_factor": squeeze_factor,
            "regulatory_basis": sqz.get("citations") or [],
        }
        # Include placement interval for clarity
        step["top_ft"] = top
        step["bottom_ft"] = bottom
        if default_recipe:
            step["recipe"] = default_recipe
        # If extraction provided an explicit sacks count, attach it as a direct override for transparency
        if sqz.get("sacks_override") not in (None, ""):
            try:
                step.setdefault("materials", {}).setdefault("slurry", {})["sacks"] = int(sqz.get("sacks_override"))
                step.setdefault("explain", {})["sacks_override_from_extraction"] = True
            except Exception:
                pass
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
