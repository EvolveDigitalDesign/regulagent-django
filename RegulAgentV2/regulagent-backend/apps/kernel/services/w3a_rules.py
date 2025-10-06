from typing import Any, Dict, List

from .violations import VCodes, MAJOR, make_violation


def generate_steps(facts: Dict[str, Any], policy_effective: Dict[str, Any]) -> Dict[str, Any]:
    violations: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []

    req = (policy_effective or {}).get('requirements') or {}

    # Surface casing shoe plug step (from §3.14(e)(2))
    shoe_knob = req.get('surface_casing_shoe_plug_min_ft')
    shoe_min = shoe_knob.get('value') if isinstance(shoe_knob, dict) else shoe_knob
    shoe_cites = shoe_knob.get('citation_keys') if isinstance(shoe_knob, dict) else []
    if shoe_min in (None, ""):
        violations.append(make_violation(VCodes.MISSING_CITATION, MAJOR, "surface_casing_shoe_plug_min_ft missing", citations=shoe_cites))
    else:
        # Derive interval from surface shoe depth if available
        shoe_step = {
            "type": "surface_casing_shoe_plug",
            "min_length_ft": float(shoe_min),
            "regulatory_basis": shoe_cites or ["tx.tac.16.3.14(e)(2)"],
        }
        surf_shoe_val = (facts.get('surface_shoe_ft') or {})
        surf_shoe = surf_shoe_val.get('value') if isinstance(surf_shoe_val, dict) else surf_shoe_val
        try:
            if surf_shoe not in (None, ""):
                c = float(surf_shoe)
                half = float(shoe_min) / 2.0
                shoe_step["top_ft"] = c + half
                shoe_step["bottom_ft"] = c - half
            else:
                violations.append(make_violation(VCodes.SURFACE_SHOE_DEPTH_UNKNOWN, MAJOR, "surface_shoe_ft is required to place shoe plug"))
        except Exception:
            violations.append(make_violation(VCodes.SURFACE_SHOE_DEPTH_UNKNOWN, MAJOR, "surface_shoe_ft invalid"))
        steps.append(shoe_step)
        # Optional: enforce additional shoe coverage requirement if specified separately
        coverage_knob = req.get('casing_shoe_coverage_ft')
        coverage_req = coverage_knob.get('value') if isinstance(coverage_knob, dict) else coverage_knob
        coverage_cites = coverage_knob.get('citation_keys') if isinstance(coverage_knob, dict) else []
        if coverage_req not in (None, ""):
            try:
                if float(shoe_min) < float(coverage_req):
                    violations.append(make_violation(
                        VCodes.INSUFFICIENT_SHOE_COVERAGE,
                        MAJOR,
                        f"Surface shoe plug {shoe_min}ft is below required coverage {coverage_req}ft",
                        citations=coverage_cites or ["tx.tac.16.3.14(e)(2)"],
                        context={"min_length_ft": shoe_min, "required_ft": coverage_req},
                    ))
            except Exception:
                pass

    # CIBP cap (from §3.14(g)(3)): if CIBP is used above each perforated interval, require ≥20 ft cement cap
    cibp_knob = req.get('cement_above_cibp_min_ft')
    cibp_min = cibp_knob.get('value') if isinstance(cibp_knob, dict) else cibp_knob
    cibp_cites = cibp_knob.get('citation_keys') if isinstance(cibp_knob, dict) else []
    use_cibp = facts.get('use_cibp') or (facts.get('use_cibp') or {}).get('value') if isinstance(facts.get('use_cibp'), dict) else facts.get('use_cibp')
    if use_cibp and cibp_min not in (None, ""):
        steps.append({
            "type": "cibp_cap",
            "cap_length_ft": float(cibp_min),
            "regulatory_basis": cibp_cites or ["tx.tac.16.3.14(g)(3)"],
        })

    # UQW isolation plug (from §3.14(g)(1))
    has_uqw = facts.get('has_uqw') or (facts.get('has_uqw') or {}).get('value') if isinstance(facts.get('has_uqw'), dict) else facts.get('has_uqw')
    uqw_len_knob = req.get('uqw_isolation_min_len_ft')
    uqw_len = uqw_len_knob.get('value') if isinstance(uqw_len_knob, dict) else uqw_len_knob
    uqw_below_knob = req.get('uqw_below_base_ft')
    uqw_below = uqw_below_knob.get('value') if isinstance(uqw_below_knob, dict) else uqw_below_knob
    uqw_above_knob = req.get('uqw_above_base_ft')
    uqw_above = uqw_above_knob.get('value') if isinstance(uqw_above_knob, dict) else uqw_above_knob
    # Fallback to SWR-14(g)(1) defaults when knobs missing
    if uqw_len in (None, ""):
        uqw_len = 100
    if uqw_below in (None, ""):
        uqw_below = 50
    if uqw_above in (None, ""):
        uqw_above = 50
    uqw_cites: List[str] = []
    if isinstance(uqw_len_knob, dict):
        uqw_cites.extend(uqw_len_knob.get('citation_keys') or [])
    if isinstance(uqw_below_knob, dict):
        uqw_cites.extend(uqw_below_knob.get('citation_keys') or [])
    if isinstance(uqw_above_knob, dict):
        uqw_cites.extend(uqw_above_knob.get('citation_keys') or [])
    if has_uqw:
        base_val = facts.get('uqw_base_ft') or {}
        base = base_val.get('value') if isinstance(base_val, dict) else base_val
        step = {
            "type": "uqw_isolation_plug",
            "min_length_ft": float(uqw_len),
            "below_ft": float(uqw_below),
            "above_ft": float(uqw_above),
            "regulatory_basis": (uqw_cites or ["tx.tac.16.3.14(g)(1)"]),
        }
        try:
            if base not in (None, ""):
                b = float(base)
                step["top_ft"] = b + float(uqw_above)
                step["bottom_ft"] = b - float(uqw_below)
        except Exception:
            pass
        steps.append(step)
    # DUQW isolation required but no UQW step planned
    duqw_required_knob = req.get('duqw_isolation_required')
    duqw_required = duqw_required_knob.get('value') if isinstance(duqw_required_knob, dict) else duqw_required_knob
    duqw_cites = duqw_required_knob.get('citation_keys') if isinstance(duqw_required_knob, dict) else []
    has_duqw = facts.get('has_duqw') or (facts.get('has_duqw') or {}).get('value') if isinstance(facts.get('has_duqw'), dict) else facts.get('has_duqw')
    if duqw_required and has_duqw and not any(s.get('type') == 'uqw_isolation_plug' for s in steps):
        violations.append(make_violation(
            VCodes.DUQW_ISOLATION_MISSING,
            MAJOR,
            "DUQW present but UQW isolation plug not planned",
            citations=duqw_cites or ["tx.tac.16.3.14(g)(1)"],
        ))

    # Return scaffold; further rules (CIBP cap, UQW isolation, DUQW, shoe coverage) next
    return {"steps": steps, "violations": violations}


