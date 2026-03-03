import json
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional


_NUM_WORDS = {
    'one': 1,
    'two': 2,
    'three': 3,
    'four': 4,
    'five': 5,
}


def _to_int(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _NUM_WORDS.get(token)


def normalize_7c_to_standard(json_data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert 7C JSON format to standard format expected by the builder.
    
    7C format differs from the standard (8A-style) format:
    - County-level: has 'additionalRequirements' (free text) instead of 'notes' (list)
    - Field specs: flat list with 'field', 'formation', 'tops' instead of 'field_name', 'formations', 'fm_tops'
    - Field specs: optional 'use_when' (scope annotation) and 'additional_requirements' fields
    
    This normalizer converts 7C to standard format, preserving scoping annotations as separate fields
    so the builder can handle them explicitly without parsing ambiguity.
    """
    normalized: Dict[str, Any] = {
        "metadata": json_data.get("metadata", {}),
        "generalProcedures": json_data.get("generalProcedures", []),
        "pluggingChart": json_data.get("pluggingChart", {}),
        "counties": {}
    }
    
    for county_key, county_data in (json_data.get("counties") or {}).items():
        # Convert additionalRequirements text → notes list by splitting on newlines
        req_text: str = county_data.get("additionalRequirements", "")
        notes: List[str] = []
        if req_text:
            # Split on newlines, keeping non-empty lines
            lines = [s.strip() for s in req_text.split("\n") if s.strip()]
            notes = lines
        
        # Process fieldSpecs: normalize key names, preserve optional fields separately
        # IMPORTANT: Some counties use state machine structure where entries with ONLY use_when
        # are headers that set context for subsequent formations
        field_specs: List[Dict[str, Any]] = county_data.get("fieldSpecs", [])
        normalized_specs: List[Dict[str, Any]] = []
        
        # State machine: track current use_when context
        current_use_when = ""
        
        for spec in field_specs:
            # Check if this is a use_when HEADER (has use_when but NO formations)
            has_formation = bool(spec.get("formation") or spec.get("formations"))
            has_use_when = bool(spec.get("use_when"))
            
            if has_use_when and not has_formation:
                # This is a HEADER - update context for subsequent formations
                current_use_when = spec.get("use_when", "")
                print(f"🔍 NORMALIZER: use_when context switched to '{current_use_when}'", flush=True)
                continue  # Don't create a formation entry for headers
            
            # Skip entries with no formation data
            if not has_formation:
                continue
            
            # Convert tops to string; handle both float and int inputs
            tops_val = spec.get("tops", "")
            if isinstance(tops_val, (int, float)) and tops_val != "":
                fm_tops_str = str(int(tops_val))  # Convert float to int first, then string
            else:
                fm_tops_str = str(tops_val)
            
            # Handle both formats: some counties use "field"/"formation", others use "field_name"/"formations"
            field_val = spec.get("field") or spec.get("field_name", "Unknown")
            formation_val = spec.get("formation") or spec.get("formations", "")
            
            normalized_spec: Dict[str, Any] = {
                "field_name": field_val,
                "formations": formation_val,
                "fm_tops": fm_tops_str,
                "remarks": spec.get("remarks", ""),  # Standard remarks if present
            }
            
            # use_when: prioritize explicit use_when on formation, else use current context
            effective_use_when = spec.get("use_when") or current_use_when
            if effective_use_when:
                normalized_spec["use_when"] = effective_use_when
            
            # Preserve additional_requirements as a separate field (field-level requirements)
            if spec.get("additional_requirements"):
                normalized_spec["additional_requirements"] = spec["additional_requirements"]
            
            normalized_specs.append(normalized_spec)
        
        normalized["counties"][county_key] = {
            "name": county_data.get("name", ""),
            "notes": notes,
            "fieldSpecs": normalized_specs
        }
    
    return normalized


def _derive_requirements_from_notes(notes: List[str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    text = " ".join(notes).lower()
    req: Dict[str, Any] = {}
    prefs: Dict[str, Any] = {}

    # Cap above perforations guidance
    if re.search(r"\b50\s*ft\.?\s*above\s+the\s+perfs", text) or re.search(r"\b50\s*feet\s*above\s+the\s+perfs", text):
        req["cap_above_highest_perf_ft"] = {
            "value": 50,
            "citation_keys": [],
            "policy_section_ids": [],
            "source": "district_plugging_book",
        }

    # Surface casing shoe symmetry (50 below to 50 above shoe)
    if re.search(r"50\s*ft\.?\s*below\s+the\s+shoe.*50\s*ft\.?\s*above\s+the\s+shoe", text):
        req["surface_shoe_symmetry_ft"] = {
            "value": 50,
            "citation_keys": [],
            "policy_section_ids": [],
            "source": "district_plugging_book",
        }

    # CIBP cap minimum cement above plug (20 ft)
    if re.search(r"cibp\s*plus\s*20\s*ft\.?\s*cement", text) or re.search(r"20\s*ft\.?\s*of\s*cement\s*on\s*top\s*of\s*the\s*plug", text):
        req["cement_above_cibp_min_ft"] = {
            "value": 20,
            "citation_keys": [],
            "policy_section_ids": [],
            "source": "district_plugging_book",
        }

    # Tagging requirement hint (store as note metadata)
    if "must be tagged" in text or re.search(r"\btag(?:ged)?\b", text):
        req["tagging_required_hint"] = {
            "value": True,
            "citation_keys": [],
            "policy_section_ids": [],
            "source": "district_plugging_book",
        }

    # District notice hours before setting plugs
    m = re.search(r"minimum of\s+(\w+)\s+hours\s+notice", text)
    if m:
        hours = _to_int(m.group(1))
        if hours:
            prefs.setdefault("operational", {})["notice_hours_min"] = hours

    # Pump path preferences/requirements
    if "cement plugs must be pumped through tubing or drill pipe" in text and "casing is not allowed" in text:
        req["pump_through_tubing_or_drillpipe_only"] = {
            "value": True,
            "citation_keys": [],
            "policy_section_ids": [],
            "source": "district_plugging_book",
        }

    if "casing must be perforated" in text and ("packer" in text or "cement retainer" in text):
        req["perforate_and_pump_under_packer_if_casing_not_recovered"] = {
            "value": True,
            "citation_keys": [],
            "policy_section_ids": [],
            "source": "district_plugging_book",
        }

    # Cement class guidance
    if "class c cement must be used for all plugs" in text:
        prefs.setdefault("cement", {})["required_class"] = "C"
    m_c = re.search(r"class\s*c\s.*?slurry\s*weight\s*of\s*([0-9]+(?:\.[0-9]+)?)\s*lbs\./gal", text)
    if m_c:
        prefs.setdefault("cement", {}).setdefault("classes", {}).setdefault("C", {})["slurry_weight_ppg"] = float(m_c.group(1))
    m_h = re.search(r"class\s*h\s.*?slurry\s*weight\s*of\s*([0-9]+(?:\.[0-9]+)?)\s*lbs\./gal", text)
    if m_h:
        prefs.setdefault("cement", {}).setdefault("classes", {}).setdefault("H", {})["slurry_weight_ppg"] = float(m_h.group(1))

    # UQW additional inside plug if surface casing much deeper than DUQW
    m_duqw = re.search(r"surface casing has been set deeper than\s*(\d+)\s*ft\.?\s*below\s+the\s+base\s+of\s+the\s+deepest\s+usable\s+quality\s+water.*?(an additional\s*(\d+)\s*ft\.?\s*cement plug must be placed inside)", text)
    if m_duqw:
        threshold = int(m_duqw.group(1))
        addlen = _to_int(m_duqw.group(3)) or int(m_duqw.group(3))
        req["additional_surface_inside_plug_len_ft_if_below_duqw"] = {
            "value": int(addlen),
            "applies_if": {">": [{"var": "surface_below_duqw_ft"}, int(threshold)]},
            "citation_keys": [],
            "policy_section_ids": [],
            "source": "district_plugging_book",
        }

    return req, prefs


def _derive_overrides_from_notes(notes: List[str]) -> Dict[str, Any]:
    text = " ".join(notes)
    text_l = text.lower()
    overrides: Dict[str, Any] = {}

    # WBL patterns: "WBL 250", "WBL 400-450", "WBL 300 + SR (1200-1700)"
    m_wbl = re.search(r"\bWBL\s*(\d+)(?:\s*[-–]\s*(\d+))?", text, flags=re.IGNORECASE)
    if m_wbl:
        wbl: Dict[str, Any] = {"min_ft": int(m_wbl.group(1))}
        if m_wbl.group(2):
            wbl["max_ft"] = int(m_wbl.group(2))
        overrides["wbl"] = wbl
    # WBL + formation window e.g. SR (1200-1700)
    m_wbl_sr = re.search(r"WBL\s*\d+\s*\+\s*(SR|SA|Santa\s*Rosa|San\s*Andres)\s*\((\d+)\s*[-–]\s*(\d+)\)", text, re.IGNORECASE)
    if m_wbl_sr:
        formation = m_wbl_sr.group(1)
        top_ft = int(m_wbl_sr.group(2))
        bot_ft = int(m_wbl_sr.group(3))
        overrides.setdefault("protect_intervals", []).append({
            "formation": formation if formation.isupper() else formation.title(),
            "top_ft": top_ft,
            "bottom_ft": bot_ft,
            "tag_required": True,
        })

    # Protect intervals generic: "protect Santa Rosa 2100-3000 ft"
    for m in re.finditer(r"protect\s+(Santa\s*Rosa|San\s*Andres|Yates|Rustler)\s*(\d{3,5})\s*[-–]\s*(\d{3,5})", text, re.IGNORECASE):
        overrides.setdefault("protect_intervals", []).append({
            "formation": m.group(1).title(),
            "top_ft": int(m.group(2)),
            "bottom_ft": int(m.group(3)),
            "tag_required": True if re.search(r"tag", text_l) else False,
        })

    # Mandatory TAG operations
    if re.search(r"TAG\s+ALL\s+Surface\s+Casing\s+Shoe\s+Plugs\s+in\s+Open\s+Hole", text, re.IGNORECASE):
        overrides.setdefault("tag", {})["surface_shoe_in_oh"] = True
    tag_forms: List[str] = []
    for abbr in ["SA", "SR", "Yates", "Rustler"]:
        if re.search(rf"TAG\s+{abbr}(?:\s+top)?", text, re.IGNORECASE):
            tag_forms.append(abbr)
    if tag_forms:
        overrides.setdefault("tag", {})["formations"] = sorted(set(tag_forms))

    # Perf & squeeze mandates
    if re.search(r"perf\s*(?:and|&)\s*(?:sqz|squeeze).*even if", text, re.IGNORECASE):
        overrides.setdefault("squeeze", {})["policy"] = "always"
    for m in re.finditer(r"(Yates|Rustler)\D{0,20}(\d{3,5})\'?", text, re.IGNORECASE):
        overrides.setdefault("squeeze", {}).setdefault("formations", [])
        if m.group(1) not in overrides["squeeze"]["formations"]:
            overrides["squeeze"]["formations"].append(m.group(1))
        overrides["squeeze"].setdefault("target_depths", []).append({"ft": int(m.group(2)), "tolerance_ft": 50})

    # Combined/stacked formation plug policies
    m_comb = re.search(r"Combine\s+([A-Za-z/\s,]+)\s+plugs\s+(?:acceptable|OK)", text, re.IGNORECASE)
    if m_comb:
        raw = m_comb.group(1)
        # Split by common separators into groups like "Ell/Fuss/Dev" or "Penn/Wolfcamp/WA/Leon/Clfk"
        groups: List[List[str]] = []
        for grp in re.split(r"\s*(?:and|,|;)\s*", raw):
            tokens = [t.strip() for t in re.split(r"/", grp) if t.strip()]
            if tokens:
                groups.append(tokens)
        if groups:
            overrides["combine_formations"] = {"allow": True, "groups": groups}

    # CO2 flood / waterflood handling
    if re.search(r"CO2\s*flood", text, re.IGNORECASE) or re.search(r"waterflood", text, re.IGNORECASE):
        overrides["enhanced_recovery_zone"] = {"behavior": {"require_tag": True, "require_protect": ["SA"]}}

    # Yates migration risk to SA
    if re.search(r"Yates.*migrat.*Santa\s*Rosa", text, re.IGNORECASE):
        overrides["migration_risk"] = {"from_to": [{"from": "Yates", "to": "SA"}], "actions": ["separate_WBL_plugs", "require_tag"]}

    # === County-Level Procedure Extractions (Patterns 1-24) ===
    
    # Pattern 1: cibp_required (CIBP plus 20 FT. cement)
    if re.search(r"CIBP\s*plus\s*(\d+)\s*FT\.\s*cement", text, re.IGNORECASE):
        m_cibp = re.search(r"CIBP\s*plus\s*(\d+)\s*FT\.\s*cement", text, re.IGNORECASE)
        if m_cibp:
            overrides.setdefault("county_procedures", {})["cibp_required"] = {
                "offset_above_perf_ft": 50,
                "cement_cap_ft": int(m_cibp.group(1))
            }
    
    # Pattern 3 & 12: surface_shoe_straddle / surface_intermediate_casing_straddle
    if re.search(r"50\s*FT\.\s*below the shoe.*?50\s*FT\.\s*above", text, re.IGNORECASE):
        overrides.setdefault("county_procedures", {})["surface_shoe_straddle"] = {
            "offset_below_ft": 50,
            "offset_above_ft": 50
        }
    
    # Pattern 4: straddle_uqw (200 FT. below DUQW)
    m_duqw_threshold = re.search(r"surface casing.*?deeper than\s*(\d+)\s*FT\.\s*below.*?deepest.*?water.*?additional\s*(\d+)\s*FT\.\s*cement", text, re.IGNORECASE)
    if m_duqw_threshold:
        overrides.setdefault("county_procedures", {})["straddle_uqw"] = {
            "threshold_below_duqw_ft": int(m_duqw_threshold.group(1)),
            "plug_length_ft": int(m_duqw_threshold.group(2))
        }
    
    # Pattern 5: coleman_junction_formation_required (200 FT. plug)
    m_cj_plug = re.search(r"We require a\s*(\d+)\s*FT\.\s*plug above the Coleman Junction", text, re.IGNORECASE)
    if m_cj_plug:
        overrides.setdefault("county_procedures", {})["coleman_junction_formation_required"] = {
            "plug_length_ft": int(m_cj_plug.group(1))
        }
    
    # Pattern 6: coleman_junction_specs (3x3 matrix by section)
    cj_sections = {}
    for section_name in ["Northern", "Central", "Southern"]:
        m_section = re.search(
            rf"{section_name}:\s*Western[^0-9]*(\d+)\s*FT[.,]?\s*[,;]?\s*Middle[^0-9]*(\d+)\s*FT[.,]?\s*[,;]?\s*Eastern[^0-9]*(\d+)",
            text, re.IGNORECASE
        )
        if m_section:
            cj_sections[section_name.lower()] = {
                "western": int(m_section.group(1)),
                "central": int(m_section.group(2)),
                "eastern": int(m_section.group(3))
            }
    if cj_sections:
        overrides.setdefault("county_procedures", {})["coleman_junction_specs"] = cj_sections
    
    # Pattern 7: dry_holes_non_producing_procedures
    if re.search(r"For dry holes in a non-producing", text, re.IGNORECASE):
        dry_hole_procs = {}
        m_shallow = re.search(r"well is\s*(\d+)-(\d+)'\s*deep\s+(.*?)(?:2\.|If the well)", text, re.IGNORECASE | re.DOTALL)
        if m_shallow:
            dry_hole_procs["shallow"] = {
                "depth_range": f"{m_shallow.group(1)}-{m_shallow.group(2)}'",
                "procedure": m_shallow.group(3).strip()
            }
        m_deep = re.search(r"well is\s*~(\d+)'\s*deep\s+(.*?)(?:\n|$)", text, re.IGNORECASE | re.DOTALL)
        if m_deep:
            dry_hole_procs["deep"] = {
                "depth_range": f"~{m_deep.group(1)}'",
                "procedure": m_deep.group(2).strip()
            }
        if dry_hole_procs:
            overrides.setdefault("county_procedures", {})["dry_holes_non_producing_procedures"] = dry_hole_procs
    
    # Pattern 8: waterboard_letter_exception (cement to surface from Coleman Junction)
    if re.search(r"do not require a waterboard letter if cement is brought to the surface from the Coleman Junction", text, re.IGNORECASE):
        overrides.setdefault("county_procedures", {})["waterboard_letter_exception"] = {
            "condition": "cement_to_surface_from_coleman_junction"
        }
    
    # Pattern 8b: waterboard_letter_exception (cement to surface from problem zone)
    if re.search(r"do not require a waterboard letter if cement is brought to the surface from the problem zone", text, re.IGNORECASE):
        overrides.setdefault("county_procedures", {})["problem_zone_cement_exception"] = {
            "condition": "cement_to_surface_from_problem_zone"
        }
    
    # Pattern 9: waterboard_protection_depth_range
    m_wbd = re.search(r"waterboard protection depth is between\s*(?:feet)?\s*(\d+)\s*(?:and|to)\s*(\d+)\s*feet?", text, re.IGNORECASE)
    if m_wbd:
        overrides.setdefault("county_procedures", {})["waterboard_protection_depth_range"] = {
            "min_ft": int(m_wbd.group(1)),
            "max_ft": int(m_wbd.group(2))
        }
    
    # Pattern 10: specific_plug_volumes (sacks for dry holes)
    if re.search(r"(\d+)\s*sks? above Coleman Junction", text, re.IGNORECASE):
        plug_vols = {}
        m_cj_sks = re.search(r"(\d+)\s*sks? above Coleman Junction", text, re.IGNORECASE)
        if m_cj_sks:
            plug_vols["above_coleman_junction_sks"] = int(m_cj_sks.group(1))
        m_sc_sks = re.search(r"(\d+)\s*sks? plug across Surface Csg", text, re.IGNORECASE)
        if m_sc_sks:
            plug_vols["surface_casing_sks"] = int(m_sc_sks.group(1))
        m_surf_sks = re.search(r"(\d+)\s*sks? at surface", text, re.IGNORECASE)
        if m_surf_sks:
            plug_vols["surface_sks"] = int(m_surf_sks.group(1))
        if plug_vols:
            overrides.setdefault("county_procedures", {})["specific_plug_volumes"] = plug_vols
    
    # Pattern 11: conditional_fill_procedure
    if re.search(r"filling to surface from Coleman Junction should be acceptable", text, re.IGNORECASE):
        overrides.setdefault("county_procedures", {})["conditional_fill_procedure"] = {
            "condition": "no_water_flow",
            "remedy": "fill_to_surface_from_coleman_junction"
        }
    
    # Pattern 13: formation_specific_tagging (San Andres, Santa Rosa, San Angelo, etc.)
    tagging_formations = []
    for form in ["San Andres", "Santa Rosa", "San Angelo", "Yates", "Rustler"]:
        if re.search(rf"Tag all.*?{form}\s+plugs", text, re.IGNORECASE):
            tagging_formations.append(form.lower().replace(" ", "_"))
    if tagging_formations:
        overrides.setdefault("county_procedures", {})["formation_specific_tagging"] = tagging_formations
    
    # Pattern 15: san_andres_formation_required
    m_sa_plug = re.search(r"We require a\s*(\d+)\s*FT\.\s*plug above the San Andres", text, re.IGNORECASE)
    if m_sa_plug:
        overrides.setdefault("county_procedures", {})["san_andres_formation_required"] = {
            "plug_length_ft": int(m_sa_plug.group(1))
        }
    
    # Pattern 18: problem_zone_formation_required
    m_pz_plug = re.search(r"We require a\s*(\d+)\s*FT\.\s*plug above a.*?problem Zone", text, re.IGNORECASE)
    if m_pz_plug:
        overrides.setdefault("county_procedures", {})["problem_zone_formation_required"] = {
            "plug_length_ft": int(m_pz_plug.group(1))
        }
    
    # Pattern 19: problem_zone_description
    m_pz_desc = re.search(r"The problem formation will produce a saltwater flow.*?The zone\s+is present in the (.+?)\.?(?:\n|$)", text, re.IGNORECASE)
    if m_pz_desc:
        overrides.setdefault("county_procedures", {})["problem_zone_description"] = {
            "location": m_pz_desc.group(1).strip()
        }
    
    # Pattern 20: localized_uqw_protection
    m_loc_uqw = re.search(
        r"About\s*(\d+)\s*miles\s*(.+?)\s+of\s+(\w+)\s+the\s+upper\s+(\w+)\s+from\s+(\d+)\s+to\s+(\d+)\s+is usable quality water",
        text, re.IGNORECASE
    )
    if m_loc_uqw:
        overrides.setdefault("county_procedures", {})["localized_uqw_protection"] = {
            "location": f"{m_loc_uqw.group(1)} miles {m_loc_uqw.group(2)} of {m_loc_uqw.group(3)}",
            "formation": m_loc_uqw.group(4),
            "depth_range": {
                "top_ft": int(m_loc_uqw.group(6)),
                "bottom_ft": int(m_loc_uqw.group(5))
            }
        }
    
    # Pattern 22: hickory_formation_protection
    if re.search(r"The Hickory must be protected if penetrated", text, re.IGNORECASE):
        m_hickory = re.search(r"The Hickory is located immediately below the (\w+)", text, re.IGNORECASE)
        below_formation = m_hickory.group(1) if m_hickory else "Ellen"
        overrides.setdefault("county_procedures", {})["hickory_formation_protection"] = {
            "located_below": below_formation
        }
    
    # Pattern 23: localized_waterboard_zones
    m_loc_zone = re.search(
        r"A zone located\s*(\d+)\s*miles\s*(.+?)\s+of\s+(\w+)\s+between\s+(\d+)\s*FT\.\s+and\s+(\d+)\s*FT\.\s+must be\s+isolated.*?cement plug from\s*(\d+)\s*FT\.\s+to\s+(\d+)\s*FT\..*?and\s*(\d+)\s*FT\.\s+to\s+(\d+)\s*FT\.",
        text, re.IGNORECASE | re.DOTALL
    )
    if m_loc_zone:
        overrides.setdefault("county_procedures", {})["localized_waterboard_zones"] = {
            "location": f"{m_loc_zone.group(1)} miles {m_loc_zone.group(2)} of {m_loc_zone.group(3)}",
            "zone_depth_range": {
                "top_ft": int(m_loc_zone.group(5)),
                "bottom_ft": int(m_loc_zone.group(4))
            },
            "isolation_plugs": [
                {
                    "top_ft": int(m_loc_zone.group(6)),
                    "bottom_ft": int(m_loc_zone.group(7)),
                    "tag_required": True
                },
                {
                    "top_ft": int(m_loc_zone.group(8)),
                    "bottom_ft": int(m_loc_zone.group(9)),
                    "tag_required": True
                }
            ]
        }
    
    # Pattern 24: localized_protection_depth
    m_loc_depth = re.search(
        r"The protection depth\s*(\d+)\s*miles\s*(.+?)\s+of\s+(\w+)\s+is\s*(\d+)\s*FT\.",
        text, re.IGNORECASE
    )
    if m_loc_depth:
        overrides.setdefault("county_procedures", {})["localized_protection_depth"] = {
            "location": f"{m_loc_depth.group(1)} miles {m_loc_depth.group(2)} of {m_loc_depth.group(3)}",
            "protection_depth_ft": int(m_loc_depth.group(4))
        }

    return overrides


_FORMATION_MAP = {
    'ell': 'Ellenburger',
    'ellen': 'Ellenburger',
    'penn': 'Penn',
    'bend': 'Bend',
    'wfcp': 'Wolfcamp',
    'wa': 'Wichita Albany',
    'wich alb': 'Wichita Albany',
    'wich': 'Wichita',
    'leon': 'Leonard',
    'clfk': 'Clearfork',
    'glor': 'Glorieta',
    'str': 'Strawn',
    'cany': 'Canyon',
    'dev': 'Devonian',
    'miss': 'Mississippian',
    'spra': 'Spraberry',
    'sa': 'San Andres',
    'sr': 'Santa Rosa',
    '7 riv': 'Seven Rivers',
    'q': 'Queen',
    'abo': 'Abo',
}


def _norm_formation(token: str) -> str:
    t = token.strip().strip('.')
    tl = t.lower()
    # Normalize multi-word hints
    if tl in _FORMATION_MAP:
        return _FORMATION_MAP[tl]
    # Map simple aliases
    if tl in ('ellen',):
        return 'Ellenburger'
    if tl in ('palo', 'pinto'):
        # handled via multi-word detection elsewhere
        return tl.title()
    return t


def _merge_dict(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            _merge_dict(dst[k], v)
        elif k in dst and isinstance(dst[k], list) and isinstance(v, list):
            dst[k].extend(v)
        else:
            dst[k] = v
    return dst


def _derive_overrides_from_fields(field_specs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Derive overrides from per-field specs.

    Backward-compatible behavior: we still aggregate per-field findings into
    county-level overrides, but we also emit a nested map under
    overrides['fields'][field_name] preserving the original field grouping.
    """
    overrides: Dict[str, Any] = {}
    fields_map: Dict[str, Any] = {}
    last_field_name: str | None = None

    def _field_name(fs: Dict[str, Any]) -> str:
        cand = (
            fs.get('field_name')
            or fs.get('field')
            or fs.get('Field Name')
            or fs.get('Field')
            or fs.get('name')
            or ''
        )
        name = str(cand).strip()
        nonlocal last_field_name
        # If this looks like a real field name, update carry-forward state
        if name and not _is_header_like(name):
            last_field_name = name
            return name
        # Otherwise, carry forward prior real field
        return last_field_name or 'Unknown Field'

    def _is_header_like(name: str) -> bool:
        nl = (name or '').strip().lower()
        if not nl:
            return False
        keywords = [
            'possible', 'surface casing', 'separate', 'cases', 'notice', 'waterboard',
            'through shallow', 'in these cases', 'field name', 'formations', 'fm tops',
        ]
        return any(k in nl for k in keywords)

    for fs in field_specs or []:
        remarks = str(fs.get('remarks') or '')
        formations = str(fs.get('formations') or '')
        fm_top = fs.get('fm_tops') if fs.get('fm_tops') not in (None, "") else fs.get('tops')
        
        # Extract optional 7C-format fields (use_when, additional_requirements)
        # These are preserved as separate fields for clear semantics
        use_when: Optional[str] = fs.get('use_when')
        additional_requirements: Optional[str] = fs.get('additional_requirements')

        # If this row has no formations, no fm_tops/tops, and no remarks, treat it
        # as a contentless header/remark line and attach to the previous valid field.
        raw_name = str(
            fs.get('field_name')
            or fs.get('field')
            or fs.get('Field Name')
            or fs.get('Field')
            or fs.get('name')
            or ''
        ).strip()
        is_contentless = (formations.strip() == '' and (fm_top in (None, "")) and remarks.strip() == '')
        if is_contentless and raw_name:
            if last_field_name:
                remarks_list = fields_map.setdefault(last_field_name, {}).setdefault('field_remarks', [])
                if raw_name not in remarks_list:
                    remarks_list.append(raw_name)
            # Do not process further
            continue

        per_field: Dict[str, Any] = {}

        # WBL in remarks
        m_wbl = re.search(r"\bWBL\s*(\d+)(?:\s*(?:to|[-–])\s*(\d+))?", remarks, flags=re.IGNORECASE)
        if m_wbl:
            w = {"min_ft": int(m_wbl.group(1))}
            if m_wbl.group(2):
                w["max_ft"] = int(m_wbl.group(2))
            _merge_dict(per_field.setdefault('wbl', {}), w)

        # WBL + SR/SA window
        m_wbl_sr = re.search(r"WBL\s*\d+\s*\+\s*(SR|SA)[^\d]*(\d+)\s*(?:to|[-–])\s*(\d+)", remarks, re.IGNORECASE)
        if m_wbl_sr:
            form = 'SA' if m_wbl_sr.group(1).upper() == 'SA' else 'SR'
            top_ft = int(m_wbl_sr.group(2))
            bot_ft = int(m_wbl_sr.group(3))
            per_field.setdefault('protect_intervals', []).append({
                'formation': form,
                'top_ft': top_ft,
                'bottom_ft': bot_ft,
                'tag_required': True,
            })

        # Tagging (from remarks)
        if re.search(r"TAG\b", remarks, re.IGNORECASE):
            fmatch = re.search(r"\b(SA|SR|Yates|Rustler)\b", formations, re.IGNORECASE)
            if fmatch:
                form = 'SA' if fmatch.group(1).upper() == 'SA' else fmatch.group(1).title()
                per_field.setdefault('tag', {}).setdefault('formations', [])
                if form not in per_field['tag']['formations']:
                    per_field['tag']['formations'].append(form)
            else:
                per_field.setdefault('tag', {})['surface_shoe_in_oh'] = True
        
        # Tagging (from additional_requirements field, e.g. "Tag" annotation in 7C format)
        if additional_requirements and additional_requirements.lower().strip() == 'tag':
            fmatch = re.search(r"\b(SA|SR|Yates|Rustler)\b", formations, re.IGNORECASE)
            if fmatch:
                form = 'SA' if fmatch.group(1).upper() == 'SA' else fmatch.group(1).title()
                per_field.setdefault('tag', {}).setdefault('formations', [])
                if form not in per_field['tag']['formations']:
                    per_field['tag']['formations'].append(form)
            else:
                per_field.setdefault('tag', {})['surface_shoe_in_oh'] = True

        if re.search(r"TAG\s+ALL\s+YATES", remarks, re.IGNORECASE):
            per_field.setdefault('tag', {}).setdefault('formations', [])
            if 'Yates' not in per_field['tag']['formations']:
                per_field['tag']['formations'].append('Yates')

        # Perf & squeeze
        if re.search(r"perf\s+and\s+squeeze\s+even\s+if\s+circulated", remarks, re.IGNORECASE):
            per_field.setdefault('squeeze', {})['policy'] = 'always'
            f2 = re.search(r"\b(Yates|Rustler)\b", formations, re.IGNORECASE)
            if f2:
                per_field['squeeze'].setdefault('formations', [])
                if f2.group(1).title() not in per_field['squeeze']['formations']:
                    per_field['squeeze']['formations'].append(f2.group(1).title())

        # Combination groups
        m_combo = re.search(r"Combo\s+plugs\s+([A-Za-z/\s,]+)\s+is\s+OK", remarks, re.IGNORECASE)
        if m_combo:
            raw = m_combo.group(1)
            tokens = [t.strip() for t in re.split(r"[,\s]+", raw) if t.strip()]
            buf: List[str] = []
            for tok in tokens:
                if '/' in tok:
                    parts = [p.strip() for p in tok.split('/') if p.strip()]
                    buf.extend(parts)
                else:
                    buf.append(tok)
            groups = [_norm_formation(t) for t in buf]
            if groups:
                per_field['combine_formations'] = {'allow': True, 'groups': [groups]}

        # ER hints
        if re.search(r"active\s+CO2\s+flood", remarks, re.IGNORECASE) or re.search(r"active\s+waterflood", remarks, re.IGNORECASE):
            per_field['enhanced_recovery_zone'] = {'behavior': {'require_tag': True, 'require_protect': ['SA']}}

        # Migration risk Yates -> SA
        if re.search(r"Yates\s+Gas\s+Pressure\s+migrating\s+to\s+the\s+Santa\s+Rosa", remarks, re.IGNORECASE) or \
           re.search(r"Possible\s+Yates\s+Gas\s+Pressure\s+migrating\s+to\s+the\s+Santa\s+Rosa", remarks, re.IGNORECASE):
            per_field['migration_risk'] = {'from_to': [{'from': 'Yates', 'to': 'SA'}], 'actions': ['separate_WBL_plugs', 'require_tag']}

        # Formation tops
        if fm_top not in (None, "") and formations:
            try:
                if isinstance(fm_top, str):
                    num = ''.join(ch for ch in fm_top if ch.isdigit())
                    if num == '':
                        raise ValueError
                    top_ft = int(num)
                else:
                    top_ft = int(fm_top)
                added_multi = False
                if re.search(r"Coleman\s+Junction", formations, re.IGNORECASE):
                    per_field.setdefault('formation_tops', []).append({
                        'formation': 'Coleman Junction', 'top_ft': top_ft, 'plug_required': True
                    })
                    added_multi = True
                if re.search(r"Palo\s+Pinto", formations, re.IGNORECASE):
                    per_field.setdefault('formation_tops', []).append({
                        'formation': 'Palo Pinto', 'top_ft': top_ft, 'plug_required': True
                    })
                    added_multi = True
                if re.search(r"Cross\s*cut", formations, re.IGNORECASE):
                    per_field.setdefault('formation_tops', []).append({
                        'formation': 'Crosscut', 'top_ft': top_ft, 'plug_required': True
                    })
                    added_multi = True
                raw_forms = [t.strip('* ').strip() for t in re.split(r"[\s/]+", formations) if t.strip()]
                for rf in raw_forms:
                    norm = _norm_formation(rf)
                    if not norm or norm.lower() in {"see", "also", "casings", "gaps", "in", "cement", "have", "proven", "problem", "zone", "sd", "sd."}:
                        continue
                    if added_multi and norm.lower() in {"coleman", "junction"}:
                        continue
                    item = {"formation": norm, "top_ft": top_ft, "plug_required": True}
                    if re.search(r"TAG", str(remarks), re.IGNORECASE):
                        item["tag_required"] = True
                    per_field.setdefault('formation_tops', []).append(item)
            except Exception:
                pass

        # Deduplicate formation_tops within this field
        if 'formation_tops' in per_field and isinstance(per_field['formation_tops'], list):
            seen_ft: set = set()
            unique_list: List[Dict[str, Any]] = []
            for it in per_field['formation_tops']:
                try:
                    key = (it.get('formation'), int(it.get('top_ft')))
                except Exception:
                    key = (it.get('formation'), it.get('top_ft'))
                if key in seen_ft:
                    continue
                seen_ft.add(key)
                unique_list.append(it)
            per_field['formation_tops'] = unique_list

        # Merge into per-county fields map only under a valid field key
        # raw_name already computed above
        # Header-like rows with no actionable content become remarks on last real field
        if _is_header_like(raw_name) and not per_field:
            if last_field_name:
                remarks_list = fields_map.setdefault(last_field_name, {}).setdefault('field_remarks', [])
                if raw_name not in remarks_list:
                    remarks_list.append(raw_name)
            continue
        # Determine final field key (carry-forward for empty/headers)
        fname = _field_name(fs)
        # If original looked like header but we did extract content, attach to last field
        if _is_header_like(raw_name) and per_field and last_field_name:
            fname = last_field_name
        # Only add when we have content to attach
        if per_field:
            fields_map[fname] = _merge_dict(fields_map.get(fname, {}), per_field)

    if fields_map:
        overrides['fields'] = fields_map

    return overrides


def build_overlay_from_plugging_book(json_data: Dict[str, Any], district_code: str) -> Dict[str, Any]:
    counties = json_data.get("counties") or {}
    overlay_counties: Dict[str, Any] = {}
    for key, county in counties.items():
        name = county.get("name") or key.title()
        notes: List[str] = county.get("notes") or []
        req, prefs = _derive_requirements_from_notes(notes)
        overrides = _derive_overrides_from_notes(notes)
        # Field-level extraction
        field_specs = county.get('fieldSpecs') or []
        fld_overrides = _derive_overrides_from_fields(field_specs)
        if fld_overrides:
            overrides = _merge_dict(overrides or {}, fld_overrides)
        overlay_counties[name] = {
            "notes": notes,
            "requirements": req,
            "preferences": prefs,
            "overrides": overrides,
        }

    overlay: Dict[str, Any] = {
        "district": district_code,
        "extracted_at": datetime.utcnow().isoformat() + "Z",
        "counties": overlay_counties,
    }
    # Derive district-wide requirements/preferences from generalProcedures
    gen = json_data.get("generalProcedures") or []
    gtext = " ".join([gp.get("text") for gp in gen if isinstance(gp, dict) and gp.get("text")])
    gtext_l = (gtext or "").lower()
    if gtext:
        requirements: Dict[str, Any] = {}
        preferences: Dict[str, Any] = {}
        if re.search(r"minimum of\s*(\w+)\s*hours\s*notice", gtext_l):
            m = re.search(r"minimum of\s*(\w+)\s*hours\s*notice", gtext_l)
            hours = _to_int(m.group(1)) if m else None
            if hours:
                preferences.setdefault("operational", {})["notice_hours_min"] = int(hours)
        if "must be circulated with mud" in gtext_l:
            m_w = re.search(r"minimum\s*weight\s*of\s*([0-9]+(?:\.[0-9]+)?)\s*lbs\./gal", gtext, re.IGNORECASE)
            m_f = re.search(r"funnel\s*viscosity\s*of\s*at\s*least\s*([0-9]+)\s*seconds", gtext, re.IGNORECASE)
            if m_w:
                preferences.setdefault("operational", {})["mud_min_weight_ppg"] = float(m_w.group(1))
            if m_f:
                preferences.setdefault("operational", {})["funnel_min_s"] = int(m_f.group(1))
        if "must be pumped through tubing or drill pipe" in gtext_l and "casing is not allowed" in gtext_l:
            requirements["pump_through_tubing_or_drillpipe_only"] = {"value": True, "source": "district_plugging_book"}
        if "casing must be perforated" in gtext_l and ("packer" in gtext_l or "cement retainer" in gtext_l):
            requirements["perforate_and_pump_under_packer_if_casing_not_recovered"] = {"value": True, "source": "district_plugging_book"}
        if "must be tagged" in gtext_l:
            requirements["tagging_required_hint"] = {"value": True, "source": "district_plugging_book"}
        if re.search(r"Class\s*C\s*cement\s*must\s*be\s*used", gtext, re.IGNORECASE):
            preferences.setdefault("cement", {})["required_class"] = "C"
            m_c = re.search(r"Class\s*C.*?(\d+\.?\d*)\s*Lbs\./Gal", gtext, re.IGNORECASE)
            if m_c:
                preferences.setdefault("cement", {}).setdefault("classes", {}).setdefault("C", {})["slurry_weight_ppg"] = float(m_c.group(1))
            m_h = re.search(r"Class\s*H.*?(\d+\.?\d*)\s*Lbs\./Gal", gtext, re.IGNORECASE)
            if m_h:
                preferences.setdefault("cement", {}).setdefault("classes", {}).setdefault("H", {})["slurry_weight_ppg"] = float(m_h.group(1))
        if re.search(r"cast\s*iron\s*bridge\s*plugs.*minimum\s*of\s*20[’']?\s*of\s*cement\s*on\s*top", gtext, re.IGNORECASE):
            requirements["cibp_top_cement_min_ft"] = {"value": 20, "source": "district_plugging_book"}
        if requirements:
            overlay["requirements"] = requirements
        if preferences:
            overlay["preferences"] = preferences

    # Plugging chart ingestion for advisory/sanity checks
    chart = json_data.get("pluggingChart") or {}
    if chart:
        plug_prefs: Dict[str, Any] = {}
        def _ingest_table(tbl_key: str, out_key: str) -> None:
            t = chart.get(tbl_key) or {}
            if not t:
                return
            entry: Dict[str, Any] = {}
            if 'diameters' in t:
                entry['diameters'] = t['diameters']
            if 'combinations' in t:
                entry['combinations'] = t['combinations']
            rows = []
            for row in t.get('data') or []:
                if isinstance(row, dict):
                    rows.append({k: row.get(k) for k in row.keys()})
            entry['data'] = rows
            plug_prefs[out_key] = entry

        _ingest_table('openHole', 'open_hole')
        _ingest_table('casing', 'casing')
        _ingest_table('casingOpenHole', 'casing_open_hole')
        if plug_prefs:
            overlay.setdefault('preferences', {})['plugging_chart'] = plug_prefs
    # Add a conservative district-wide default if a majority of counties derive the same knob
    cap_values = [c.get("requirements", {}).get("cap_above_highest_perf_ft", {}).get("value") for c in overlay_counties.values()]
    if cap_values:
        try:
            # If >= 60% of counties say 50 ft, set district default
            pct_50 = sum(1 for v in cap_values if v == 50) / max(1, len(cap_values))
            if pct_50 >= 0.6:
                overlay.setdefault("requirements", {})["cap_above_highest_perf_ft"] = {
                    "value": 50,
                    "citation_keys": [],
                    "policy_section_ids": [],
                    "source": "district_plugging_book_majority",
                }
        except Exception:
            pass

    return overlay


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

