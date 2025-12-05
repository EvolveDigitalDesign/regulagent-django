from typing import Any, Dict, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

from .violations import VCodes, MAJOR, make_violation


def _determine_plug_type(
    step: Dict[str, Any],
    production_toc_ft: Optional[float],
    is_perf_and_circ: bool = False
) -> str:
    """
    Determine the mechanical plug TYPE based on step depth vs production TOC.
    
    PLUG TYPES (4 types only):
    - "spot_plug" - cement INSIDE casing ONLY (used when BELOW production TOC)
    - "perf_and_squeeze_plug" - perforate & squeeze behind pipe (used when ABOVE production TOC)
    - "perf_and_circulate_plug" - perf & squeeze to surface (TAKES PRECEDENCE over perf & squeeze)
    - "dumbell_plug" - 3 sacks of cement on top of CIBP/retainer (special case)
    
    CONSTRAINT: Spot plugs and perf & squeeze plugs CANNOT be combined/merged.
    
    Args:
        step: Step dict with 'top_ft', 'bottom_ft', 'type' (purpose), etc.
        production_toc_ft: Production casing TOC depth (ft). If None, assumes perf & squeeze (conservative).
        is_perf_and_circ: Force perf_and_circulate type (reaches surface)
    
    Returns:
        Plug type string ("spot_plug", "perf_and_squeeze_plug", "perf_and_circulate_plug", "dumbell_plug")
    """
    # Perf and circulate takes absolute precedence (reaches surface, special operation)
    if is_perf_and_circ or step.get("type") == "perf_and_circulate_to_surface":
        logger.debug(f"Plug type: PERF_AND_CIRCULATE (reaches surface)")
        return "perf_and_circulate_plug"
    
    # Dumbell plug is special (mechanical cap on CIBP/retainer, not independent)
    if step.get("type") in ("cibp_cap", "bridge_plug_cap"):
        logger.debug(f"Plug type: DUMBELL (mechanical cap on tool)")
        return "dumbell_plug"
    
    # Spot plug at surface (top plug)
    if step.get("type") == "top_plug":
        logger.debug(f"Plug type: SPOT (surface safety plug)")
        return "spot_plug"
    
    # If no production TOC available, assume perf & squeeze (conservative/safe)
    if production_toc_ft is None:
        logger.warning(f"⚠️  No production TOC available; assuming perf_and_squeeze for safety")
        return "perf_and_squeeze_plug"
    
    # Determine by depth comparison to production TOC
    step_depth = step.get("bottom_ft") or step.get("top_ft") or 0
    try:
        step_depth_ft = float(step_depth)
        production_toc = float(production_toc_ft)
        
        # ABOVE TOC (shallower) = must perforate & squeeze behind pipe
        # BELOW TOC (deeper) = spot plug (cement inside casing only)
        if step_depth_ft < production_toc:
            logger.debug(f"Plug at {step_depth_ft:.1f} ft is ABOVE TOC ({production_toc:.1f} ft) → perf_and_squeeze_plug")
            return "perf_and_squeeze_plug"
        else:
            logger.debug(f"Plug at {step_depth_ft:.1f} ft is BELOW TOC ({production_toc:.1f} ft) → spot_plug")
            return "spot_plug"
    
    except (ValueError, TypeError):
        logger.warning(f"Could not parse depths for plug type determination; defaulting to perf_and_squeeze")
        return "perf_and_squeeze_plug"  # Conservative fallback


def _get_casing_strings_at_depth(facts: Dict[str, Any], target_depth_ft: float) -> Dict[str, Any]:
    """
    Determine what casing strings surround the target depth.
    
    Decision tree per plugging best practices:
    - Two strings present (e.g., 5½" production inside 8⅝" intermediate) → P-I annulus
      → Perforate inner string and squeeze annulus (cement between strings)
    - One string present (below next outer shoe) → outside is open hole/formation
      → Perforate casing and squeeze into formation (open-hole squeeze)
    - No casing present → bare open hole
      → Spot open-hole plugs (or CIBP + cap where allowed)
    
    Returns:
        {
            "inner_string": {"name": "production", "size_in": 5.5, "id_in": 4.778, "shoe_ft": 14233},
            "outer_string": {"name": "intermediate", "size_in": 9.625, "id_in": 8.681, "shoe_ft": 5377},
            "count": 2,  # Number of strings at depth
            "context": "annulus_squeeze" | "open_hole_squeeze" | "open_hole"
        }
    """
    result = {
        "inner_string": None,
        "outer_string": None,
        "count": 0,
        "context": "open_hole"  # Default: no casing
    }
    
    # Map OD to nominal ID for common casing sizes
    NOMINAL_ID = {
        13.375: 12.515,  # 13 3/8" intermediate
        11.75: 10.965,   # 11 3/4" intermediate
        9.625: 8.681,    # 9 5/8" intermediate (47 lb/ft)
        8.625: 7.921,    # 8 5/8" production
        7.0: 6.094,      # 7" production
        5.5: 4.778       # 5 1/2" production
    }
    
    def _get_id(od: float) -> Optional[float]:
        """Get nominal ID from OD using lookup table with tolerance."""
        if od in NOMINAL_ID:
            return NOMINAL_ID[od]
        for k, v in NOMINAL_ID.items():
            if abs(od - k) < 0.02:
                return v
        # Fallback estimate: ID ≈ OD - 0.875" for intermediate, OD - 0.72" for production
        if od >= 9.0:
            return od - 0.875
        else:
            return od - 0.72
    
    # Get casing strings from facts
    casing_strings = facts.get("casing_strings", [])
    if not isinstance(casing_strings, list):
        return result
    
    # Find all strings that extend to or below target depth
    strings_at_depth = []
    for casing in casing_strings:
        string_type = (casing.get("string") or "").lower()
        bottom_ft = casing.get("bottom_ft") or casing.get("shoe_depth_ft") or casing.get("setting_depth_ft")
        
        if bottom_ft is None:
            continue
            
        try:
            bottom = float(bottom_ft)
            # String extends to or below target depth
            if bottom >= target_depth_ft:
                size_in = casing.get("size_in")
                if size_in:
                    od = float(size_in)
                    id_in = _get_id(od)
                    strings_at_depth.append({
                        "name": string_type,
                        "size_in": od,
                        "id_in": id_in,
                        "shoe_ft": bottom
                    })
        except (ValueError, TypeError):
            continue
    
    # Sort by size (smallest OD is innermost string)
    strings_at_depth.sort(key=lambda x: x["size_in"])
    
    result["count"] = len(strings_at_depth)
    
    if len(strings_at_depth) == 0:
        # No casing at target depth → bare open hole
        result["context"] = "open_hole"
        logger.info(f"Depth {target_depth_ft} ft: No casing strings → OPEN HOLE")
        
    elif len(strings_at_depth) == 1:
        # One string present (below outer shoe) → open-hole squeeze
        result["inner_string"] = strings_at_depth[0]
        result["context"] = "open_hole_squeeze"
        logger.info(
            f"Depth {target_depth_ft} ft: One string ({result['inner_string']['name']} "
            f"{result['inner_string']['size_in']}\") → OPEN-HOLE SQUEEZE (cement into formation)"
        )
        
    elif len(strings_at_depth) >= 2:
        # Two+ strings present → annulus squeeze (cement between strings)
        result["inner_string"] = strings_at_depth[0]  # Smallest (innermost)
        result["outer_string"] = strings_at_depth[1]  # Next larger (outer)
        result["context"] = "annulus_squeeze"
        logger.info(
            f"Depth {target_depth_ft} ft: Two strings ({result['inner_string']['name']} "
            f"{result['inner_string']['size_in']}\" inside {result['outer_string']['name']} "
            f"{result['outer_string']['size_in']}\") → ANNULUS SQUEEZE (cement between strings)"
        )
    
    return result


def _has_cement_at_depth(facts: Dict[str, Any], target_depth_ft: float) -> bool:
    """
    Check if cement is present behind casing at the target depth.
    
    Per SWR-14(g)(2): "Where the hole is cased and cement is not found behind 
    the casing at the depth required for isolation..."
    
    Returns True if cement is confirmed at or above target depth.
    """
    # Check W-2 casing record for cement tops
    casing_record = facts.get('casing_record', [])
    if isinstance(casing_record, list):
        for casing in casing_record:
            if not isinstance(casing, dict):
                continue
            
            # Check if this casing string covers the target depth
            bottom_ft = casing.get('bottom_ft')
            cement_top_ft = casing.get('cement_top_ft')
            
            try:
                bottom_ft = float(bottom_ft) if bottom_ft not in (None, '') else None
                cement_top_ft = float(cement_top_ft) if cement_top_ft not in (None, '') else None
            except:
                continue
            
            # If casing covers target depth and cement top is at or above target
            if bottom_ft is not None and target_depth_ft <= bottom_ft:
                if cement_top_ft is not None and cement_top_ft <= target_depth_ft:
                    # Cement is present at this depth
                    return True
    
    # Check W-15 cementing data
    cementing_data = facts.get('cementing_data', [])
    if isinstance(cementing_data, list):
        for job in cementing_data:
            if not isinstance(job, dict):
                continue
            
            cement_top_ft = job.get('cement_top_ft')
            interval_bottom_ft = job.get('interval_bottom_ft')
            
            try:
                cement_top_ft = float(cement_top_ft) if cement_top_ft not in (None, '') else None
                interval_bottom_ft = float(interval_bottom_ft) if interval_bottom_ft not in (None, '') else None
            except:
                continue
            
            # If cement extends to or above target depth
            if cement_top_ft is not None and cement_top_ft <= target_depth_ft:
                # And the job covers this depth
                if interval_bottom_ft is None or target_depth_ft <= interval_bottom_ft:
                    return True
    
    # Check W-15 cement_tops_per_string
    cement_tops_per_string = facts.get('cement_tops_per_string', [])
    if isinstance(cement_tops_per_string, list):
        for string_data in cement_tops_per_string:
            if not isinstance(string_data, dict):
                continue
            
            cement_top_ft = string_data.get('cement_top_ft')
            try:
                cement_top_ft = float(cement_top_ft) if cement_top_ft not in (None, '') else None
            except:
                continue
            
            if cement_top_ft is not None and cement_top_ft <= target_depth_ft:
                return True
    
    # No cement data found or cement doesn't reach target depth
    return False


def _requires_perforation_at_depth(
    facts: Dict[str, Any],
    target_top_ft: float,
    target_bottom_ft: float
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Determine if perforation is required to isolate an interval.
    
    Per TAC §3.14(g)(2): "Where the hole is cased and cement is not found behind 
    the casing at the depth required for isolation, the casing shall be perforated 
    and cement squeezed behind the pipe to provide a seal."
    
    Uses decision tree to determine squeeze context:
    - Two strings at depth → annulus squeeze (cement between strings)
    - One string at depth → open-hole squeeze (cement into formation)
    - No strings → no perforation needed (use plugs)
    
    Returns:
        (requires_perf: bool, reason: str, casing_context: dict)
    """
    logger.critical(f"🔍 _requires_perforation_at_depth called: target_top={target_top_ft}, target_bottom={target_bottom_ft}")
    
    # Use decision tree to determine what's at target depth
    casing_context = _get_casing_strings_at_depth(facts, target_bottom_ft)
    
    logger.critical(f"🔍 Casing context: {casing_context['context']}, {casing_context['count']} strings at depth")
    
    # If open hole (no casing), no perforation needed
    if casing_context["context"] == "open_hole":
        logger.critical(f"🔍 Returning False - open hole (no casing at depth)")
        return False, None, casing_context
    
    # Check explicit open hole flag from W-2
    prod_interval = facts.get('producing_injection_disposal_interval', {})
    if isinstance(prod_interval, dict):
        open_hole_flag = str(prod_interval.get('open_hole', '')).strip().upper()
        if open_hole_flag in ('YES', 'Y', 'TRUE'):
            logger.critical(f"🔍 Returning False - open hole completion per W-2")
            return False, None, casing_context
    
    logger.critical(f"🔍 ✅ CONDITION 1 MET: Cased interval at depth")
    
    # Check condition 2: Is cement present behind casing at this depth?
    has_cement = _has_cement_at_depth(facts, target_bottom_ft)
    
    logger.critical(f"🔍 has_cement = {has_cement} (at depth {target_bottom_ft})")
    
    if has_cement:
        logger.critical(f"🔍 Returning False - cement already present")
        return False, None, casing_context
    
    logger.critical(f"🔍 ✅ CONDITION 2 MET: No cement behind casing")
    
    # Check condition 3: Are there existing perforations at target depth?
    existing_perfs = facts.get('perforations', [])
    logger.critical(f"🔍 Checking existing perforations - count: {len(existing_perfs) if isinstance(existing_perfs, list) else 0}")
    
    if isinstance(existing_perfs, list):
        for idx, perf in enumerate(existing_perfs):
            if not isinstance(perf, dict):
                continue
            
            perf_from = perf.get('from_ft')
            perf_to = perf.get('to_ft')
            
            try:
                perf_from = float(perf_from) if perf_from not in (None, '') else None
                perf_to = float(perf_to) if perf_to not in (None, '') else None
            except:
                continue
            
            if perf_from is None or perf_to is None:
                continue
            
            # Check if existing perforation overlaps with target interval
            perf_bottom = max(perf_from, perf_to)
            perf_top = min(perf_from, perf_to)
            
            logger.critical(f"🔍 Existing perf #{idx}: {perf_top}-{perf_bottom} ft, checking overlap with target {target_top_ft}-{target_bottom_ft} ft")
            
            # If there's any overlap, perforation already exists
            if not (target_top_ft < perf_top or target_bottom_ft > perf_bottom):
                logger.critical(f"🔍 Returning False - existing perforation overlaps with target")
                return False, None, casing_context
    
    logger.critical(f"🔍 ✅ CONDITION 3 MET: No existing perforations")
    
    # Build context-aware reason
    if casing_context["context"] == "annulus_squeeze":
        inner = casing_context["inner_string"]
        outer = casing_context["outer_string"]
        reason = (
            f"Annulus squeeze required: {inner['name']} {inner['size_in']}\" inside "
            f"{outer['name']} {outer['size_in']}\" at {target_bottom_ft:.0f} ft. "
            f"Cement will fill P-I annulus between strings (TAC §3.14(g)(2))"
        )
    else:  # open_hole_squeeze
        inner = casing_context["inner_string"]
        reason = (
            f"Open-hole squeeze required: {inner['name']} {inner['size_in']}\" with formation outside "
            f"at {target_bottom_ft:.0f} ft. Cement will enter formation behind casing (TAC §3.14(g)(2))"
        )
    
    logger.critical(f"🔍 ✅✅✅ ALL CONDITIONS MET - Returning True with reason: {reason}")
    return True, reason, casing_context


def generate_steps(facts: Dict[str, Any], policy_effective: Dict[str, Any]) -> Dict[str, Any]:
    logger.critical(f"🚨🚨🚨 KERNEL GENERATE_STEPS CALLED - facts has annular_gaps: {bool(facts.get('annular_gaps'))}, count: {len(facts.get('annular_gaps', []))}")
    violations: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []

    # Extract production TOC for plug type determination (spot vs perf & squeeze)
    prod_toc_val = facts.get('production_casing_toc_ft') or {}
    production_toc_ft = prod_toc_val.get('value') if isinstance(prod_toc_val, dict) else prod_toc_val
    try:
        production_toc_ft = float(production_toc_ft) if production_toc_ft not in (None, "") else None
    except (ValueError, TypeError):
        production_toc_ft = None
    
    logger.info(f"🎯 GENERATE_STEPS: production_casing_toc_ft = {production_toc_ft} ft")

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
            "geometry_context": "cased_production",
            "placement_basis": "Surface casing shoe coverage +50 ft shallower",
        }
        surf_shoe_val = (facts.get('surface_shoe_ft') or {})
        surf_shoe = surf_shoe_val.get('value') if isinstance(surf_shoe_val, dict) else surf_shoe_val
        try:
            if surf_shoe not in (None, ""):
                c = float(surf_shoe)
                plug_length = float(shoe_min)
                # Placement: AT shoe (bottom_ft) and 50 ft shallower (top_ft)
                shoe_step["bottom_ft"] = c  # At shoe depth
                shoe_step["top_ft"] = c - plug_length  # Shallower (shoe - 50)
                
                # Determine plug type (spot vs perf & squeeze based on TOC)
                shoe_step["plug_type"] = _determine_plug_type(shoe_step, production_toc_ft)
            else:
                violations.append(make_violation(VCodes.SURFACE_SHOE_DEPTH_UNKNOWN, MAJOR, "surface_shoe_ft is required to place shoe plug"))
        except Exception:
            violations.append(make_violation(VCodes.SURFACE_SHOE_DEPTH_UNKNOWN, MAJOR, "surface_shoe_ft invalid"))
        steps.append(shoe_step)
        logger.info("w3a: emit surface_shoe min=%s top=%s bottom=%s", shoe_min, shoe_step.get("top_ft"), shoe_step.get("bottom_ft"))
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
    # CIBP may be already present in-hole (from prior work) or requested to be used in this plan.
    def _bool(val: Any) -> bool:
        if isinstance(val, dict):
            return bool(val.get('value'))
        return bool(val)
    use_cibp = _bool(facts.get('use_cibp'))
    cibp_present = _bool(facts.get('cibp_present'))
    # If a CIBP is present OR the plan intends to use one, enforce the cement-above-CIBP requirement
    if (use_cibp or cibp_present) and cibp_min not in (None, ""):
        # If an existing cap is already present (from prior work), avoid stacking extra cap
        cap_present = _bool(facts.get('cibp_cap_present'))
        existing_cap_len_val = facts.get('existing_cibp_cap_length_ft') or {}
        try:
            existing_cap_len = existing_cap_len_val.get('value') if isinstance(existing_cap_len_val, dict) else existing_cap_len_val
            existing_cap_len_f = float(existing_cap_len) if existing_cap_len not in (None, "") else 0.0
        except Exception:
            existing_cap_len_f = 0.0

        try:
            required_cap = float(cibp_min)
        except Exception:
            required_cap = 0.0

        if cap_present and existing_cap_len_f >= required_cap:
            # Already compliant; do not add another cap step
            pass
        else:
            # If present but short, top up only the remaining footage; else plan full requirement
            remaining = max(required_cap - (existing_cap_len_f if cap_present else 0.0), 0.0)
            if remaining > 0.0:
                cibp_cap_step = {
                    "type": "cibp_cap",
                    "cap_length_ft": remaining,
                    "geometry_context": "cased_production",
                    "regulatory_basis": cibp_cites or ["tx.tac.16.3.14(g)(3)"],
                    "plug_type": "dumbell_plug",  # Dumbell is always 3 sacks on CIBP/retainer
                }
                steps.append(cibp_cap_step)
                logger.info("w3a: emit cibp_cap (dumbell_plug) length=%s present=%s existing_len=%s", remaining, cap_present, existing_cap_len_f)

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
    # GAU protect intervals → generate long cased-hole plugs (optional, controlled by presence of facts)
    # Check if GAU interval will satisfy UQW isolation requirement
    gau_satisfies_uqw = False
    uqw_base_ft = None
    if has_uqw:
        base_val = facts.get('uqw_base_ft') or {}
        uqw_base_ft = base_val.get('value') if isinstance(base_val, dict) else base_val
        try:
            if uqw_base_ft not in (None, ""):
                uqw_base_ft = float(uqw_base_ft)
        except Exception:
            uqw_base_ft = None
    
    try:
        gau_protect = facts.get('gau_protect_intervals') or []
        if isinstance(gau_protect, list):
            for gi in gau_protect:
                try:
                    top_ft = float(gi.get('top_ft'))
                    bottom_ft = float(gi.get('bottom_ft'))
                    if top_ft > 0 and bottom_ft >= 0 and top_ft > bottom_ft:
                        regulatory_basis = ["tx.gau.protect_interval"]
                        placement_basis = "GAU protect interval"
                        
                        # GAU protect intervals from surface inherently define and protect UQW zones
                        # If GAU interval starts at surface (bottom_ft == 0), it satisfies UQW isolation
                        if has_uqw and bottom_ft == 0:
                            gau_satisfies_uqw = True
                            regulatory_basis.append("tx.tac.16.3.14(g)(1)")
                            placement_basis = "GAU protect interval (satisfies UQW isolation requirement)"
                            logger.info(
                                "w3a: GAU interval from surface (0-%.1f ft) inherently protects UQW - "
                                "single cement plug satisfies both GAU and UQW isolation requirements",
                                top_ft
                            )
                        # If explicit UQW base depth is known, verify coverage
                        elif has_uqw and uqw_base_ft is not None:
                            required_top = uqw_base_ft + float(uqw_above)
                            required_bottom = uqw_base_ft - float(uqw_below)
                            
                            # If GAU interval covers the required UQW zone, it satisfies both requirements
                            if bottom_ft <= required_bottom and top_ft >= required_top:
                                gau_satisfies_uqw = True
                                regulatory_basis.append("tx.tac.16.3.14(g)(1)")
                                placement_basis = "GAU protect interval (covers UQW base)"
                                logger.info(
                                    "w3a: GAU interval (%.1f-%.1f ft) covers UQW base (%.1f ft ±%.1f/%.1f ft) - "
                                    "single cement plug satisfies both requirements",
                                    top_ft, bottom_ft, uqw_base_ft, uqw_above, uqw_below
                                )
                        
                        gau_step = {
                            "type": "cement_plug",
                            "geometry_context": "cased_production",
                            "top_ft": top_ft,
                            "bottom_ft": bottom_ft,
                            "annular_excess": 0.4,
                            "regulatory_basis": regulatory_basis,
                            "placement_basis": placement_basis,
                        }
                        gau_step["plug_type"] = _determine_plug_type(gau_step, production_toc_ft)
                        steps.append(gau_step)
                except Exception:
                    continue
    except Exception:
        logger.exception("w3a: gau protect intervals generation failed")
    
    # UQW isolation plug - only generate if NOT already satisfied by GAU interval
    if has_uqw and not gau_satisfies_uqw:
        step = {
            "type": "uqw_isolation_plug",
            "min_length_ft": float(uqw_len),
            "below_ft": float(uqw_below),
            "above_ft": float(uqw_above),
            "regulatory_basis": (uqw_cites or ["tx.tac.16.3.14(g)(1)"]),
            "placement_basis": "UQW base isolation ±50 ft",
        }
        try:
            if uqw_base_ft not in (None, ""):
                b = float(uqw_base_ft)
                step["top_ft"] = b + float(uqw_above)
                step["bottom_ft"] = b - float(uqw_below)
        except Exception:
            pass
        # Determine plug type (spot vs perf & squeeze based on TOC)
        step["plug_type"] = _determine_plug_type(step, production_toc_ft)
        steps.append(step)
        logger.info("w3a: emit uqw base=%s above=%s below=%s top=%s bottom=%s", uqw_base_ft, uqw_above, uqw_below, step.get("top_ft"), step.get("bottom_ft"))
    
    # DUQW isolation required but no UQW step planned (and GAU didn't satisfy it)
    duqw_required_knob = req.get('duqw_isolation_required')
    duqw_required = duqw_required_knob.get('value') if isinstance(duqw_required_knob, dict) else duqw_required_knob
    duqw_cites = duqw_required_knob.get('citation_keys') if isinstance(duqw_required_knob, dict) else []
    has_duqw = facts.get('has_duqw') or (facts.get('has_duqw') or {}).get('value') if isinstance(facts.get('has_duqw'), dict) else facts.get('has_duqw')
    if duqw_required and has_duqw and not gau_satisfies_uqw and not any(s.get('type') == 'uqw_isolation_plug' for s in steps):
        violations.append(make_violation(
            VCodes.DUQW_ISOLATION_MISSING,
            MAJOR,
            "DUQW present but UQW isolation plug not planned",
            citations=duqw_cites or ["tx.tac.16.3.14(g)(1)"],
        ))

    # Proposal generation: overlay-driven only (no heuristics)
    try:
        # Required inputs: producing interval fact and proposal knobs from overlay
        prod_iv = (facts.get("producing_interval_ft") or {}).get("value") if isinstance(facts.get("producing_interval_ft"), dict) else facts.get("producing_interval_ft")
        proposal = (policy_effective.get("proposal") or {})
        plug_count = int(proposal.get("plug_count", 0) or 0)
        seg_len = float(proposal.get("segment_length_ft", 0) or 0)
        spacing = float(proposal.get("spacing_ft", 0) or 0)
        if prod_iv and plug_count > 0 and seg_len > 0 and spacing > 0:
            p_top = float(min(prod_iv[0], prod_iv[1]))
            current_top = p_top
            added = 0
            while added < plug_count:
                seg_top = current_top
                seg_bot = current_top - seg_len
                if seg_bot <= 0:
                    break
                steps.append({
                    "type": "cement_plug",
                    "geometry_context": "cased_production",
                    "top_ft": seg_top,
                    "bottom_ft": seg_bot,
                    "annular_excess": float(proposal.get("cased_annular_excess", 0.4)),
                    "regulatory_basis": proposal.get("citations") or ["tx.tac.16.3.14(b)"],
                })
                added += 1
                current_top = current_top - spacing
            logger.info("w3a: proposal plugs added=%s top=%s seg_len=%s spacing=%s", added, p_top, seg_len, spacing)
    except Exception:
        logger.exception("w3a: proposal generation failed")

    # Top plug (from §3.14(d)(8)) and casing cut (from same subsection)
    # NOTE: Top plug will be removed later if perf_and_circulate_to_surface is generated
    # (since that operation brings cement to surface, making top plug redundant)
    try:
        top_knob = req.get('top_plug_length_ft')
        top_len = top_knob.get('value') if isinstance(top_knob, dict) else top_knob
        top_cites = top_knob.get('citation_keys') if isinstance(top_knob, dict) else []
        if top_len not in (None, ""):
            top_plug_step = {
                "type": "top_plug",
                "length_ft": float(top_len),
                "top_ft": 10.0,
                "bottom_ft": 0.0,
                "regulatory_basis": top_cites or ["tx.tac.16.3.14(d)(8)"],
                "plug_type": "spot_plug",  # Surface safety plug at surface
            }
            steps.append(top_plug_step)
        cut_knob = req.get('casing_cut_below_surface_ft')
        cut_val = cut_knob.get('value') if isinstance(cut_knob, dict) else cut_knob
        cut_cites = cut_knob.get('citation_keys') if isinstance(cut_knob, dict) else []
        if cut_val not in (None, ""):
            steps.append({
                "type": "cut_casing_below_surface",
                "depth_ft": float(cut_val),
                "regulatory_basis": cut_cites or ["tx.tac.16.3.14(d)(8)"],
            })
    except Exception:
        logger.exception("w3a: top plug / casing cut assembly failed")

    # Intermediate casing shoe plug (from §3.14(f)(1)) if intermediate shoe is known
    try:
        inter_shoe_val = facts.get('intermediate_shoe_ft') or {}
        inter_shoe = inter_shoe_val.get('value') if isinstance(inter_shoe_val, dict) else inter_shoe_val
        if inter_shoe not in (None, ""):
            plug_top = float(inter_shoe) + 50.0
            plug_bottom = float(inter_shoe) - 50.0
            
            # Check if perforation is required at this depth
            requires_perf, perf_reason, casing_context = _requires_perforation_at_depth(
                facts, plug_top, plug_bottom
            )
            
            step_dict = {
                "type": "intermediate_casing_shoe_plug",
                "min_length_ft": 100.0,
                "top_ft": plug_top,
                "bottom_ft": plug_bottom,
                "geometry_context": "cased_intermediate",
                "regulatory_basis": ["tx.tac.16.3.14(f)(1)"],
            }
            
            # If perforation is required, convert to perforate_and_squeeze_plug
            if requires_perf:
                step_dict["type"] = "perforate_and_squeeze_plug"
                step_dict["requires_perforation"] = True
                step_dict["details"] = {
                    "perforation_required_reason": perf_reason,
                    "perforation_interval": {
                        "top_ft": plug_bottom,
                        "bottom_ft": plug_bottom,
                        "length_ft": 100.0
                    },
                    "cement_cap_inside_casing": {
                        "top_ft": plug_top,
                        "bottom_ft": plug_bottom,
                        "height_ft": 100.0
                    }
                }
                if "tx.tac.16.3.14(g)(2)" not in step_dict["regulatory_basis"]:
                    step_dict["regulatory_basis"].append("tx.tac.16.3.14(g)(2)")
                step_dict["plug_type"] = "perf_and_squeeze_plug"
                logger.info(
                    f"Intermediate casing shoe plug at {inter_shoe} ft requires perforation: {perf_reason}"
                )
            else:
                # No perforation required - determine plug type (spot vs perf & squeeze by TOC)
                step_dict["plug_type"] = _determine_plug_type(step_dict, production_toc_ft)
            
            steps.append(step_dict)
    except Exception:
        logger.exception("w3a: intermediate shoe plug assembly failed")

    # Perf and circulate to surface (annulus fill from shoe to near-surface)
    # Generate when: intermediate inside surface, TOC unknown/poor, UQW protection needed
    try:
        inter_shoe_val = facts.get('intermediate_shoe_ft') or {}
        inter_shoe = inter_shoe_val.get('value') if isinstance(inter_shoe_val, dict) else inter_shoe_val
        surface_shoe_val = facts.get('surface_shoe_ft') or {}
        surface_shoe = surface_shoe_val.get('value') if isinstance(surface_shoe_val, dict) else surface_shoe_val
        
        # Check if we have both intermediate and surface casing
        if inter_shoe not in (None, "") and surface_shoe not in (None, ""):
            inter_shoe_ft = float(inter_shoe)
            surface_shoe_ft = float(surface_shoe)
            
            # Check if intermediate is inside surface (intermediate shoe deeper than surface shoe)
            if inter_shoe_ft > surface_shoe_ft:
                # Check if TOC is unknown/insufficient for the intermediate-surface annulus
                # TOC from facts, or assume needs protection if not specified
                intermediate_toc = facts.get('intermediate_toc_ft')
                toc_val = intermediate_toc.get('value') if isinstance(intermediate_toc, dict) else intermediate_toc
                
                # Generate perf & circulate if:
                # 1. TOC is None, 0, or > 100 ft (not cemented to near-surface)
                # 2. OR UQW protection is required and we need to ensure annulus seal
                needs_annulus_fill = False
                reason = ""
                
                if toc_val in (None, "", 0, "0"):
                    needs_annulus_fill = True
                    reason = "Intermediate casing TOC unknown or not cemented to surface"
                elif isinstance(toc_val, (int, float)) and float(toc_val) > 100:
                    needs_annulus_fill = True
                    reason = f"Intermediate casing TOC at {toc_val} ft - insufficient for surface protection"
                
                # Also check if UQW depth requires protection
                uqw_val = facts.get('uqw_depth_ft') or {}
                uqw_depth = uqw_val.get('value') if isinstance(uqw_val, dict) else uqw_val
                if uqw_depth not in (None, ""):
                    try:
                        uqw_ft = float(uqw_depth)
                        # If UQW is shallower than intermediate shoe, we need protection
                        if uqw_ft < inter_shoe_ft:
                            needs_annulus_fill = True
                            if not reason:
                                reason = f"UQW at {uqw_ft} ft requires surface protection via annulus cement"
                    except (ValueError, TypeError):
                        pass
                
                if needs_annulus_fill:
                    # Generate perf and circulate to surface operation
                    # Perforate just BELOW the surface casing shoe (not at intermediate shoe!)
                    # This fills the surface-to-intermediate annulus from surface down
                    perforation_depth_ft = surface_shoe_ft + 50.0  # 50 ft below surface shoe
                    
                    perf_circ_step = {
                        "type": "perf_and_circulate_to_surface",
                        "name": "Cement Surface Plug (Perforate and Circulate)",
                        "perforation_depth_ft": perforation_depth_ft,
                        "top_ft": 3.0,  # Near surface (cut depth)
                        "bottom_ft": perforation_depth_ft,
                        "outer_string": "surface",
                        "inner_string": "intermediate",
                        "geometry_context": "annulus_circulation",
                        "regulatory_basis": ["tx.tac.16.3.14(e)(2)"],
                        "placement_reason": reason,
                        "details": {
                            "method": "perforate_and_circulate",
                            "target_annulus": "surface_to_intermediate",
                            "perforation_location": f"Below surface shoe at {surface_shoe_ft} ft",
                            "circulation_target": "Returns to surface",
                        },
                        "plug_type": "perf_and_circulate_plug",  # Reaches surface
                    }
                    steps.append(perf_circ_step)
                    logger.info(
                        f"Generated perf_and_circulate_to_surface: {perforation_depth_ft}→3 ft (perf below surface shoe at {surface_shoe_ft} ft, {reason})"
                    )
                    
                    # Remove redundant plugs that are now covered by perf_and_circulate_to_surface
                    # 1. Surface casing shoe plug (covered by annulus circulation)
                    # 2. UQW isolation plugs in the circulation range (covered by annulus circulation)
                    # 3. Top plug (cement already returns to 3 ft, no need for separate top plug)
                    steps_to_remove = []
                    for idx, step in enumerate(steps):
                        step_type = step.get('type')
                        
                        # Remove surface shoe plugs
                        if step_type == 'surface_casing_shoe_plug':
                            steps_to_remove.append(idx)
                            logger.info(f"Removing redundant surface_casing_shoe_plug (covered by perf_and_circulate_to_surface)")
                        
                        # Remove UQW plugs in the circulation range (0-577 ft)
                        elif step_type == 'uqw_isolation_plug':
                            uqw_top = step.get('top_ft')
                            uqw_bottom = step.get('bottom_ft')
                            if uqw_top is not None and uqw_bottom is not None:
                                # If UQW plug overlaps with circulation range, remove it
                                if uqw_bottom >= 3.0 and uqw_top <= perforation_depth_ft:
                                    steps_to_remove.append(idx)
                                    logger.info(f"Removing redundant uqw_isolation_plug at {uqw_top}-{uqw_bottom} ft (covered by perf_and_circulate_to_surface)")
                        
                        # Remove top plug (cement already goes to surface via circulation)
                        elif step_type == 'top_plug':
                            steps_to_remove.append(idx)
                            logger.info(f"Removing redundant top_plug (cement returns to surface via perf_and_circulate_to_surface)")
                    
                    # Remove in reverse order to preserve indices
                    for idx in reversed(steps_to_remove):
                        steps.pop(idx)
                    
    except Exception:
        logger.exception("w3a: perf_and_circulate_to_surface assembly failed")

    # Productive horizon isolation plug (shallowest producing horizon) from producing interval (from_ft,to_ft)
    # Places plug ABOVE the top of the producing interval to isolate the productive horizon
    try:
        piv = facts.get('producing_interval_ft') or {}
        interval = piv.get('value') if isinstance(piv, dict) else piv
        if isinstance(interval, (list, tuple)) and len(interval) == 2:
            from_ft, to_ft = float(interval[0]), float(interval[1])
            # Find the top (shallowest point) of the producing interval
            top_of_interval = min(from_ft, to_ft)
            
            # Place plug ABOVE the producing interval to isolate the productive horizon
            # Per TAC §3.14(k) and §3.14(g)(2) for perforated completions
            
            # Check if perforation is required per TAC §3.14(g)(2)
            # Test perforation requirement at the producing interval depth
            requires_perf, perf_reason, casing_context = _requires_perforation_at_depth(facts, top_of_interval - 100.0, top_of_interval)
            
            if requires_perf:
                # PERFORATE & SQUEEZE PLUG (Compound operation per §3.14(g)(2))
                # Three components:
                # 1. Perforations: 50 ft above producing interval (9872 - 50 to 9872 - 100 = 9822-9772 ft)
                # 2. Squeeze: Cement pumped through perforations into annulus (behind pipe)
                # 3. Cement cap: 50 ft INSIDE casing from top of perfs to interval top (9822-9872 ft)
                
                perf_bottom_ft = top_of_interval - 50.0  # Bottom of perf interval (shallower, 9822 ft)
                perf_top_ft = top_of_interval - 100.0  # Top of perf interval (even shallower, 9772 ft)
                
                cap_bottom_ft = top_of_interval  # Bottom of cement cap = top of producing interval (9872 ft)
                cap_top_ft = perf_bottom_ft  # Top of cap = bottom of perfs (9822 ft)
                
                # Total plug interval reported on W-3A
                total_bottom_ft = cap_bottom_ft  # 9872 ft (deeper/bottom)
                total_top_ft = perf_top_ft  # 9772 ft (shallower/top)
                
                plug_step = {
                    "type": "perforate_and_squeeze_plug",
                    "min_length_ft": 100.0,
                    "top_ft": total_top_ft,
                    "bottom_ft": total_bottom_ft,
                    "geometry_context": "cased_production",
                    "regulatory_basis": ["tx.tac.16.3.14(k)", "tx.tac.16.3.14(g)(2)"],
                    "placement_basis": f"Perforate & squeeze above producing interval ({from_ft:.0f}-{to_ft:.0f} ft)",
                    "requires_perforation": True,
                    "plug_type": "perf_and_squeeze_plug",
                    "details": {
                        "perforation_reason": perf_reason,
                        "perforation_interval": {
                            "top_ft": perf_top_ft,
                            "bottom_ft": perf_bottom_ft,
                            "interval_ft": 50.0,
                            "description": "Perforations for squeeze behind pipe"
                        },
                        "cement_cap_inside_casing": {
                            "top_ft": cap_top_ft,
                            "bottom_ft": cap_bottom_ft,
                            "height_ft": 50.0,
                            "description": "50 ft cap below perforations per §3.14(g)(2)"
                        },
                        "squeeze_behind_pipe": True,
                        "total_interval_ft": 100.0
                    }
                }
                
                logger.info(
                    "w3a: perforate & squeeze plug generated - "
                    "perfs %.1f-%.1f ft, cap %.1f-%.1f ft, total %.1f-%.1f ft - %s",
                    perf_top_ft, perf_bottom_ft, cap_top_ft, cap_bottom_ft, 
                    total_top_ft, total_bottom_ft, perf_reason
                )
            else:
                # STANDARD CEMENT PLUG (No perforation required)
                # Simple 50 ft plug above producing interval
                plug_bottom = top_of_interval
                plug_top = plug_bottom - 50.0
                
                plug_step = {
                    "type": "productive_horizon_isolation_plug",
                    "min_length_ft": 50.0,
                    "top_ft": plug_top,
                    "bottom_ft": plug_bottom,
                    "geometry_context": "cased_production",
                    "regulatory_basis": ["tx.tac.16.3.14(k)"],
                    "placement_basis": f"Above producing interval ({from_ft:.0f}-{to_ft:.0f} ft)",
                }
                # Determine plug type (spot vs perf & squeeze by TOC)
                plug_step["plug_type"] = _determine_plug_type(plug_step, production_toc_ft)
                
                logger.info(
                    "w3a: standard productive horizon plug placed at %.1f-%.1f ft "
                    "(50 ft above producing interval top at %.1f ft) - type: %s",
                    plug_top, plug_bottom, top_of_interval, plug_step.get("plug_type")
                )
            
            steps.append(plug_step)
    except Exception:
        logger.exception("w3a: productive horizon plug assembly failed")

    # Inject operational instructions from preferences.operational if present (informational)
    try:
        ops = (policy_effective.get('preferences') or {}).get('operational') or {}
        mud_min = ops.get('mud_min_weight_ppg')
        funnel_min = ops.get('funnel_min_s')
        if mud_min or funnel_min:
            instr = []
            if mud_min:
                instr.append(f"Mud ≥{float(mud_min)} ppg")
            if funnel_min:
                instr.append(f"Funnel ≥{int(funnel_min)} s")
            for s in steps:
                if s.get('type') in ("surface_casing_shoe_plug", "uqw_isolation_plug", "cement_plug"):
                    existing = s.get("special_instructions")
                    note = "; ".join(instr)
                    s["special_instructions"] = f"{existing}; {note}" if existing else note
    except Exception:
        logger.exception("w3a: operational instruction injection failed")

    # Formation plugs are appended by the kernel from merged policy; do not generate here to avoid duplicates
    try:
        ft_over = (policy_effective.get('district_overrides') or {}).get('formation_tops') or []
        ft_map = facts.get('formation_tops_map') or {}
        # No-op; presence of ft_over is used by kernel later
        _ = (ft_over, ft_map)
    except Exception:
        logger.exception("w3a: formation_tops alignment inspection failed")

    # District-level default generation disabled: rely strictly on extraction and overlay policy
    
    # Generate perforate & squeeze plugs for annular gaps (from wellbore schematic)
    # This is the PRIMARY method for detecting perforate & squeeze requirements per SWR-14(g)(2)
    try:
        annular_gaps = facts.get('annular_gaps', [])
        logger.critical(f"🚨 KERNEL: annular_gaps from facts = {len(annular_gaps)} gaps")
        if annular_gaps:
            logger.critical(f"🚨 KERNEL: Processing {len(annular_gaps)} annular gaps from schematic")
            for idx, gap in enumerate(annular_gaps):
                logger.critical(f"🚨 KERNEL: Gap #{idx}: {gap}")
                
                if not gap.get('requires_isolation'):
                    logger.critical(f"🚨 KERNEL: Gap #{idx} SKIPPED - requires_isolation={gap.get('requires_isolation')}")
                    continue
                
                logger.critical(f"🚨 KERNEL: Gap #{idx} PASSED requires_isolation check")
                
                # Each annular gap represents a zone where cement is missing behind casing
                # Per SWR-14(g)(2): Must perforate and squeeze cement behind pipe
                gap_top = gap.get('top_md_ft')
                gap_bottom = gap.get('bottom_md_ft')
                outer_string = gap.get('outer_string', 'unknown')
                inner_string = gap.get('inner_string', 'unknown')
                
                logger.critical(f"🚨 KERNEL: Gap #{idx} depths - top={gap_top}, bottom={gap_bottom}")
                
                if gap_top is None or gap_bottom is None:
                    logger.critical(f"🚨 KERNEL: Gap #{idx} SKIPPED - missing depths")
                    continue
                
                logger.critical(f"🚨 KERNEL: Gap #{idx} PASSED depth check, generating plug...")
                
                # Generate perforate & squeeze plug spanning the gap
                # Place plug in middle of gap for isolation
                gap_size = gap_bottom - gap_top
                plug_length = min(gap_size, 100.0)  # Max 100 ft plug
                plug_center = gap_top + (gap_size / 2)
                plug_top = plug_center + (plug_length / 2)
                plug_bottom = plug_center - (plug_length / 2)
                
                # Check if perforation is required (cased, no existing perfs)
                requires_perf, perf_reason, casing_context = _requires_perforation_at_depth(facts, plug_top, plug_bottom)
                logger.critical(f"🚨 KERNEL: Gap #{idx} - requires_perf={requires_perf}, context={casing_context.get('context') if casing_context else 'unknown'}, reason={perf_reason}")
                
                if requires_perf:
                    # Perforate & squeeze compound plug
                    perf_interval_bottom = plug_bottom
                    perf_interval_top = plug_bottom + 50  # Perf 50 ft
                    cap_bottom = perf_interval_top
                    cap_top = cap_bottom + 50  # 50 ft cap above perfs
                    
                    step = {
                        "type": "perforate_and_squeeze_plug",
                        "total_top_ft": cap_top,
                        "total_bottom_ft": perf_interval_bottom,
                        "geometry_context": "cased_production",
                        "regulatory_basis": ["tx.tac.16.3.14(g)(2)"],
                        "placement_basis": f"Annular gap between {outer_string} and {inner_string} (schematic)",
                        "requires_perforation": True,
                        "requires_perforation_reason": perf_reason,
                        "plug_type": "perf_and_squeeze_plug",
                        "details": {
                            "perforation_interval": {
                                "top_ft": perf_interval_top,
                                "bottom_ft": perf_interval_bottom,
                                "length_ft": 50,
                            },
                            "cement_cap_inside_casing": {
                                "top_ft": cap_top,
                                "bottom_ft": cap_bottom,
                                "height_ft": 50,
                            },
                            "annular_gap_covered": {
                                "top_ft": gap_top,
                                "bottom_ft": gap_bottom,
                                "size_ft": gap_size,
                            }
                        }
                    }
                    steps.append(step)
                    logger.critical(
                        f"🚨 KERNEL: Gap #{idx} - ADDED PERFORATE & SQUEEZE PLUG to steps! "
                        f"{outer_string}/{inner_string} gap ({gap_top}-{gap_bottom} ft)"
                    )
                else:
                    # Standard cement plug (cement present or open hole)
                    step = {
                        "type": "cement_plug",
                        "top_ft": plug_top,
                        "bottom_ft": plug_bottom,
                        "geometry_context": "cased_production",
                        "regulatory_basis": ["tx.tac.16.3.14(g)(1)"],
                        "placement_basis": f"Annular gap between {outer_string} and {inner_string} (schematic)",
                        "details": {
                            "annular_gap_covered": {
                                "top_ft": gap_top,
                                "bottom_ft": gap_bottom,
                                "size_ft": gap_size,
                            }
                        }
                    }
                    # Determine plug type (spot vs perf & squeeze by TOC)
                    step["plug_type"] = _determine_plug_type(step, production_toc_ft)
                    steps.append(step)
                    logger.critical(
                        f"🚨 KERNEL: Gap #{idx} - ADDED CEMENT PLUG ({step.get('plug_type')}) to steps! "
                        f"{outer_string}/{inner_string} gap ({gap_top}-{gap_bottom} ft)"
                    )
    except Exception:
        logger.exception("w3a: annular gap processing failed")

    # Dedupe formation_top_plug steps: prefer county-specific entries if duplicates exist
    try:
        seen: Dict[str, Dict[str, Any]] = {}
        deduped: List[Dict[str, Any]] = []
        for s in steps:
            if s.get("type") != "formation_top_plug":
                deduped.append(s)
                continue
            formation_name = str(s.get("formation") or "").strip().lower()
            key = formation_name
            existing = seen.get(key)
            if not existing:
                seen[key] = s
                deduped.append(s)
                continue
            # Prefer county-specific regulatory basis (e.g., contains '.scurry:')
            def is_county_specific(step: Dict[str, Any]) -> bool:
                for rb in step.get("regulatory_basis") or []:
                    if ".scurry:" in str(rb).lower():
                        return True
                return False
            if is_county_specific(s) and not is_county_specific(existing):
                # Replace existing with county-specific
                seen[key] = s
                # Update deduped list: replace first occurrence
                for i, d in enumerate(deduped):
                    if d is existing:
                        deduped[i] = s
                        break
            # else keep existing
        steps = deduped
    except Exception:
        logger.exception("w3a: formation_top_plug dedupe failed")

    # Return scaffold and synthesized proposal steps
    return {"steps": steps, "violations": violations}


