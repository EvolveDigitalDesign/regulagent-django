import os
import yaml
import json
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
import math
import re

BASE_DIR = os.path.dirname(os.path.dirname(__file__))  # apps/policy
PACKS_DIR = os.path.join(BASE_DIR, 'packs')

REQUIRED_BASE_KEYS = ['citations', 'requirements', 'cement_class']


class PolicyIncomplete(Exception):
    """Raised when required policy knobs are missing."""


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def _load_formation_data(district: str, county: str, field: Optional[str] = None) -> Dict[str, Any]:
    """Load formation data from JSON for districts that use hybrid approach (7C).
    
    Returns formations with their tops, use_when, and additional_requirements.
    """
    json_path = os.path.join(PACKS_DIR, 'tx', 'w3a', 'district_overlays', f'{district.lower()}_plugging_book.json')
    
    if not os.path.exists(json_path):
        return {}
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}
    
    counties = data.get('counties', {})
    county_key = county.lower().replace(' ', '_').replace(' county', '')
    county_data = counties.get(county_key, {})
    
    if not county_data:
        # Try with "county" suffix
        county_key_with_suffix = f"{county_key}_county"
        county_data = counties.get(county_key_with_suffix, {})
    
    if not county_data:
        return {}
    
    field_specs = county_data.get('fieldSpecs', [])
    
    # Filter by field if specified
    if field:
        field_norm = field.lower().strip()
        field_specs = [fs for fs in field_specs if fs.get('field', '').lower().strip() == field_norm]
    
    # Convert to formations dict
    formations = {}
    for spec in field_specs:
        formation = spec.get('formation')
        if formation:
            formations[formation] = {
                'top_ft': spec.get('tops'),
                'use_when': spec.get('use_when'),
                'additional_requirements': spec.get('additional_requirements'),
                'plug_required': True
            }
    
    return {'formations': formations}


def _merge(a: Dict[str, Any], b: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not b:
        return dict(a)
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _validate_minimal(policy: Dict[str, Any]) -> Dict[str, Any]:
    missing: list[str] = []

    def _check_scope(scope_name: str, data: Dict[str, Any], annotate: str | None = None) -> None:
        scope_missing: list[str] = []
        for k in REQUIRED_BASE_KEYS:
            if k not in data:
                scope_missing.append(f"{scope_name}.{k}")
        req = data.get('requirements') or {}
        numerics = ['casing_shoe_coverage_ft', 'duqw_coverage_ft', 'tag_wait_hours']
        for n in numerics:
            if req.get(n) in (None, ''):
                scope_missing.append(f"{scope_name}.requirements.{n}")
        cement = data.get('cement_class') or {}
        for ck in ['cutoff_ft', 'shallow_class', 'deep_class']:
            if cement.get(ck) in (None, ''):
                scope_missing.append(f"{scope_name}.cement_class.{ck}")
        if annotate:
            scope_missing = [f"{m} [district:{annotate}]" for m in scope_missing]
        missing.extend(scope_missing)

    # Validate base always
    base = policy.get('base') or {}
    _check_scope('base', base)

    # If a district was requested, validate the merged effective overlay as well
    district = policy.get('district')
    if district:
        effective = policy.get('effective') or {}
        _check_scope('effective', effective, annotate=str(district))

    policy['incomplete_reasons'] = missing
    policy['complete'] = len(missing) == 0
    return policy


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


def _load_centroids() -> Dict[str, Tuple[float, float]]:
    centroids_path = os.path.join(PACKS_DIR, 'tx', 'w3a', 'district_overlays', 'texas_county_centroids.json')
    if not os.path.exists(centroids_path):
        return {}
    import json
    with open(centroids_path, 'r', encoding='utf-8') as f:
        data = json.load(f) or []
    out: Dict[str, Tuple[float, float]] = {}
    for row in data:
        name = str(row.get('county', '')).strip().lower()
        # normalize whitespace and remove trailing "county"
        name = re.sub(r"\s+", " ", name)
        lat = row.get('latitude')
        lon = row.get('longitude')
        if name and lat is not None and lon is not None:
            coord = (float(lat), float(lon))
            # Store both base (without 'county') and with suffix, with collapsed whitespace
            base = re.sub(r"\s+county$", "", name).strip()
            with_suffix = f"{base} county"
            out[base] = coord
            out[with_suffix] = coord
            out[name] = coord
    return out


def _mentions_field(config: Any, term_norm: str) -> bool:
    """Recursively check if a county config mentions the requested field in either
    a field key name or a formation name under any nested structure.
    """
    if config is None:
        return False
    if isinstance(config, dict):
        for k, v in config.items():
            k_norm = str(k).strip().lower()
            # Match on field key names, e.g., "Spraberry", "Spraberry Deep"
            if term_norm in k_norm or k_norm in term_norm:
                return True
            # Match on explicit formation names
            if str(k).strip().lower() == 'formation':
                v_norm = str(v).strip().lower()
                if term_norm in v_norm or v_norm in term_norm:
                    return True
            # Recurse
            if _mentions_field(v, term_norm):
                return True
        return False
    if isinstance(config, list):
        for item in config:
            if _mentions_field(item, term_norm):
                return True
        return False
    # Primitive
    return False


def _normalize_field_name(name: str) -> str:
    """Normalize field names for comparison: lower, strip parentheticals and extra spaces."""
    s = str(name or '').lower().strip()
    # remove parenthetical content
    s = re.sub(r"\([^\)]*\)", "", s)
    # normalize unicode dashes to hyphen-minus
    s = re.sub(r"[‐‑‒–—−]", "-", s)
    # collapse spaces around hyphens
    s = re.sub(r"\s*-\s*", "-", s)
    # collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _load_7c_formations_for_field(formation_json: Dict[str, Any], county: str, field: str) -> list[Dict[str, Any]]:
    """
    Load formation_tops from 7C plugging book JSON for a specific county and field.
    
    Returns list of formation_tops in the format expected by the kernel:
    [
        {
            'formation': 'Penn',
            'top_ft': 5100,
            'plug_required': True,
            'tag_required': True if additional_requirements == 'Tag',
            'use_when': '10 East Silver',
        },
        ...
    ]
    """
    counties = formation_json.get('counties', {})
    
    # Normalize county name for lookup
    county_norm = str(county).lower().strip()
    county_data = None
    
    # Try exact match first
    for ck, cv in counties.items():
        if str(cv.get('name', '')).lower().strip() == county_norm:
            county_data = cv
            break
    
    if not county_data:
        # Try partial match
        for ck, cv in counties.items():
            name = str(cv.get('name', '')).lower().strip()
            if county_norm in name or name in county_norm:
                county_data = cv
                break
    
    if not county_data:
        print(f"⚠️  LOADER: County {county} not found in formation JSON", flush=True)
        return []
    
    # Get field specs
    field_specs = county_data.get('fieldSpecs', [])
    field_norm = _normalize_field_name(field)
    
    print(f"🔍 _load_7c_formations: county={county}, field={field}, field_norm={field_norm}", flush=True)
    print(f"🔍 _load_7c_formations: {len(field_specs)} total fieldSpecs for county", flush=True)
    
    formation_tops = []
    matched_count = 0
    for spec in field_specs:
        spec_field = _normalize_field_name(spec.get('field', ''))
        # Match field (exact or contains)
        is_match = (spec_field == field_norm or field_norm in spec_field or spec_field in field_norm)
        if is_match:
            matched_count += 1
        if not is_match:
            continue
        
        formation = spec.get('formation')
        tops = spec.get('tops')
        use_when = spec.get('use_when', '')
        additional_req = spec.get('additional_requirements', '')
        
        if not formation or tops is None:
            print(f"⚠️  _load_7c_formations: Skipping spec - formation={formation}, tops={tops}", flush=True)
            continue
        
        formation_top = {
            'formation': formation,
            'top_ft': float(tops),
            'plug_required': True,  # All formations in 7C JSON require plugs
        }
        
        if use_when:
            formation_top['use_when'] = use_when
        
        if additional_req:
            formation_top['additional_requirements'] = additional_req
            # If additional_requirements == "Tag", set tag_required
            if str(additional_req).strip().lower() == 'tag':
                formation_top['tag_required'] = True
        
        formation_tops.append(formation_top)
    
    print(f"🔍 _load_7c_formations: {matched_count} specs matched field, {len(formation_tops)} formations extracted", flush=True)
    
    return formation_tops


def _normalize_county_key(name: str) -> Tuple[str, str]:
    """Return (base, with_suffix) normalized county keys for lookups.
    Collapses whitespace and strips a trailing 'county'."""
    s = str(name or '').lower().strip()
    s = re.sub(r"\s+", " ", s)
    base = re.sub(r"\s+county$", "", s).strip()
    with_suffix = f"{base} county"
    return base, with_suffix


def _normalize_district(district: str) -> str:
    """
    Normalize district code to standard format for policy lookups.
    
    Handles variations: "08", "8", "08A", "8A" all normalize to "08a"
    
    Examples:
        "08" → "08a"
        "8" → "08a"  
        "08A" → "08a"
        "8A" → "08a"
        "7C" → "07c"
        "7" → "07a"
    
    Returns lowercase, zero-padded, with letter suffix.
    """
    if not district:
        return ""
    
    d = str(district).strip().upper()
    
    # Extract numeric and letter parts
    match = re.match(r'^(\d+)([A-Z]?)$', d)
    if not match:
        # Not a standard district format, return lowercase as-is
        return district.lower()
    
    num_part, letter_part = match.groups()
    
    # Zero-pad single digit
    if len(num_part) == 1:
        num_part = f"0{num_part}"
    
    # Default to 'A' if no letter (most TX RRC districts use A)
    if not letter_part:
        letter_part = 'A'
    
    return f"{num_part}{letter_part}".lower()


def get_effective_policy(district: Optional[str] = None, county: Optional[str] = None, field: Optional[str] = None, as_of: Optional[datetime] = None, pack_rel_path: str = 'tx_rrc_w3a_base_policy_pack.yaml') -> Dict[str, Any]:
    pack_path = os.path.join(PACKS_DIR, pack_rel_path)
    policy = _load_yaml(pack_path)
    base = policy.get('base') or {}
    merged = dict(base)
    # Track field provenance for transparency
    field_resolution: Dict[str, Any] = {
        'requested_field': field,
        'matched_field': None,
        'matched_in_county': None,
        'method': None,
        'nearest_distance_km': None,
    }

    if district:
        overlays = policy.get('district_overlays', {})
        # Try both original and normalized district for backward compatibility
        d_ov = overlays.get(str(district)) or overlays.get(district)
        if not d_ov:
            d_normalized = _normalize_district(district)
            d_ov = overlays.get(d_normalized)
        if d_ov:
            merged = _merge(merged, d_ov)
        # External district county overlays, if present alongside pack
        ext_dir = os.path.join(PACKS_DIR, 'tx', 'w3a', 'district_overlays')
        # Merge district-wide combined overlay requirements/preferences if available
        # Use normalized district for file lookups (handles "08" → "08a", "8" → "08a", etc.)
        d_normalized = _normalize_district(district)
        combined_name = f"{d_normalized}__auto.yml"
        combined_path = os.path.join(ext_dir, combined_name)
        print(f"🔍 LOADER: district={district} → normalized={d_normalized}", flush=True)
        print(f"🔍 LOADER: combined_path={combined_path}", flush=True)
        print(f"🔍 LOADER: file exists={os.path.exists(combined_path)}", flush=True)
        combined: Dict[str, Any] | None = None
        formation_json: Dict[str, Any] | None = None
        
        # For 7C districts, try hybrid approach: load both YAML procedures and JSON formations
        is_7c_hybrid = d_normalized in ['07c']
        if is_7c_hybrid:
            yaml_procedures_path = os.path.join(ext_dir, f"{d_normalized}_county_procedures.yml")
            json_formations_path = os.path.join(ext_dir, f"{d_normalized}_plugging_book.json")
            print(f"🔍 LOADER: 7C Hybrid mode - checking for:")
            print(f"  YAML procedures: {yaml_procedures_path} (exists={os.path.exists(yaml_procedures_path)})")
            print(f"  JSON formations: {json_formations_path} (exists={os.path.exists(json_formations_path)})")
            
            if os.path.exists(yaml_procedures_path):
                combined = _load_yaml(yaml_procedures_path)
                print(f"🔍 LOADER: Loaded 7C procedures YAML, keys={list(combined.keys())}", flush=True)
                load_path = yaml_procedures_path
            
            if os.path.exists(json_formations_path):
                import json
                with open(json_formations_path, 'r', encoding='utf-8') as f:
                    formation_json = json.load(f)
                print(f"🔍 LOADER: Loaded 7C formations JSON, {len(formation_json.get('counties', {}))} counties", flush=True)
        else:
            # Standard approach for 8A and others: single combined YAML
            load_path = combined_path if os.path.exists(combined_path) else None
            if load_path:
                combined = _load_yaml(load_path)
                print(f"🔍 LOADER: Loaded combined YAML, keys={list(combined.keys())}", flush=True)
                print(f"🔍 LOADER: combined['overrides'] keys={list((combined.get('overrides') or {}).keys())}", flush=True)
        
        # Merge district-level data (works for both 8A and 7C)
        if combined:
            if isinstance(combined.get('requirements'), dict):
                merged = _merge(merged, {'requirements': combined['requirements']})
                print(f"🔍 LOADER: Merged requirements", flush=True)
            if isinstance(combined.get('preferences'), dict):
                merged = _merge(merged, {'preferences': combined['preferences']})
                print(f"🔍 LOADER: Merged preferences", flush=True)
            if isinstance(combined.get('overrides'), dict):
                merged = _merge(merged, {'district_overrides': combined['overrides']})
                print(f"🔍 LOADER: Merged overrides into district_overrides", flush=True)
                print(f"🔍 LOADER: After merge, merged['district_overrides'] keys={list((merged.get('district_overrides') or {}).keys())}", flush=True)
        if county:
            # Normalize county name to file-safe
            safe_county = county.lower().replace(' ', '_')
            # Use normalized district for county file lookups (e.g., "08" → "08a")
            d_normalized_for_county = _normalize_district(district) if district else ""
            file_name = f"{d_normalized_for_county}__{safe_county}.yml"
            ext_path = os.path.join(ext_dir, file_name)
            county_req: Dict[str, Any] | None = None
            county_overrides: Dict[str, Any] | None = None
            county_prefs: Dict[str, Any] | None = None
            county_proposal: Dict[str, Any] | None = None
            county_fields: Dict[str, Any] | None = None
            if os.path.exists(ext_path):
                ext_overlay = _load_yaml(ext_path)
                county_req = (ext_overlay.get('requirements') or {})
                county_overrides = (ext_overlay.get('overrides') or {})
                county_prefs = (ext_overlay.get('preferences') or {})
                county_proposal = (ext_overlay.get('proposal') or {})
                # fields may be stored at top-level or under overrides.fields depending on overlay builder
                county_fields = (ext_overlay.get('fields') or (ext_overlay.get('overrides') or {}).get('fields') or {})
            else:
                # Fallback: combined overlay file per district (e.g., 7c__auto.yml)
                if combined:
                    counties = combined.get('counties') or {}
                    print(f"🔍 LOADER: Looking up county={county} in combined YAML", flush=True)
                    print(f"🔍 LOADER: Available counties={list(counties.keys())[:5]}", flush=True)  # First 5 to avoid spam
                    # lookup by multiple aliases
                    aliases = [
                        str(county),
                        f"{county} County" if not str(county).lower().endswith(" county") else str(county)[:-7],
                    ]
                    print(f"🔍 LOADER: Trying aliases={aliases}", flush=True)
                    cdata = None
                    for alias in aliases:
                        cdata = counties.get(alias) or counties.get(str(alias).strip())
                        if cdata:
                            print(f"🔍 LOADER: Found county data via alias={alias}", flush=True)
                            break
                    if not cdata:
                        # fallback: case-insensitive contains/equals
                        lc = str(county).lower()
                        for k, v in counties.items():
                            kl = str(k).lower()
                            if kl == lc or kl.startswith(lc) or lc in kl:
                                cdata = v
                                break
                    if not cdata:
                        # final fallback: normalize county keys (handles odd spacing/casing)
                        try:
                            c_base, c_with = _normalize_county_key(str(county))
                            for k, v in counties.items():
                                kb, kw = _normalize_county_key(str(k))
                                if kb == c_base or kw == c_base or kb == c_with or kw == c_with:
                                    cdata = v
                                    break
                        except Exception:
                            pass
                    if cdata:
                        print(f"🔍 LOADER: County data found, keys={list(cdata.keys())}", flush=True)
                        county_req = (cdata.get('requirements') or {})
                        county_overrides = (cdata.get('overrides') or {})
                        county_prefs = (cdata.get('preferences') or {})
                        county_proposal = (cdata.get('proposal') or {})
                        # 7C hybrid: county_procedures at top level (not under overrides)
                        county_procedures = (cdata.get('county_procedures') or {})
                        if county_procedures:
                            county_overrides.setdefault('county_procedures', {})
                            county_overrides['county_procedures'].update(county_procedures)
                        # combined overlay stores per-field specs commonly under overrides.fields
                        county_fields = (cdata.get('fields') or (cdata.get('overrides') or {}).get('fields') or {})
                        print(f"🔍 LOADER: county_overrides keys={list(county_overrides.keys())}", flush=True)
                        formation_tops_in_overrides = county_overrides.get('formation_tops') or []
                        print(f"🔍 LOADER: formation_tops in county_overrides={len(formation_tops_in_overrides)}", flush=True)
            if county_req:
                merged = _merge(merged, {'requirements': county_req})
            if county_overrides:
                merged = _merge(merged, {'district_overrides': county_overrides})
            if county_prefs:
                merged = _merge(merged, {'preferences': county_prefs})
            if county_proposal:
                merged = _merge(merged, {'proposal': county_proposal})

            # Field-level merge (county → field, else nearest county’s field)
            if field:
                field_norm = _normalize_field_name(str(field))
                def _skeleton(s: str) -> str:
                    """Aggressive normalizer: keep letters/digits only for fuzzy matching."""
                    return re.sub(r"[^a-z0-9]", "", s)

                def _match_field(d: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                    if not d:
                        return None
                    fld_norm = field_norm
                    fld_skel = _skeleton(fld_norm)
                    for fk, fv in (d.get('fields') or d).items():
                        fk_norm = _normalize_field_name(str(fk))
                        if fk_norm == fld_norm:
                            return fv
                        # loose contains fallback (both directions to handle parentheticals / punctuation variants)
                        if fld_norm in fk_norm or fk_norm in fld_norm:
                            return fv
                        # skeleton match ignoring all punctuation/spaces/dashes
                        fk_skel = _skeleton(fk_norm)
                        if fk_skel and (fk_skel == fld_skel or fld_skel in fk_skel or fk_skel in fld_skel):
                            return fv
                    return None

                chosen_field_cfg: Optional[Dict[str, Any]] = None
                # Try current county field first
                if county_fields:
                    chosen_field_cfg = _match_field({'fields': county_fields})
                    if chosen_field_cfg:
                        field_resolution['matched_field'] = field
                        field_resolution['matched_in_county'] = county
                        field_resolution['method'] = 'exact_in_county'
                # If not found, search nearest county’s field within combined
                chosen_field_county: Optional[str] = None
                if not chosen_field_cfg and combined:
                    counties = combined.get('counties') or {}
                    centroids = _load_centroids()
                    # Robust source centroid lookup (with and without "county" suffix)
                    c_key = str(county).strip().lower()
                    c_base = c_key.replace(' county', '')
                    src = centroids.get(c_key) or centroids.get(c_base) or centroids.get(f"{c_base} county")
                    best_dist = float('inf')
                    best_cfg = None
                    best_name = None
                    # Nearest county that has a matching field key under fields/overrides.fields
                    for ck, cv in counties.items():
                        # Skip current county
                        if str(ck).strip().lower().replace(' county','') == c_base:
                            continue
                        c_fields_map = cv.get('fields') or (cv.get('overrides') or {}).get('fields') or {}
                        # Look for a field key that matches (contains either way) the requested field
                        key_match = None
                        for fk in c_fields_map.keys():
                            fk_norm = _normalize_field_name(str(fk))
                            if fk_norm == field_norm or (field_norm in fk_norm or fk_norm in field_norm):
                                key_match = fk
                                break
                            # skeleton fallback
                            if _skeleton(fk_norm) and (_skeleton(fk_norm) == _skeleton(field_norm) or _skeleton(field_norm) in _skeleton(fk_norm) or _skeleton(fk_norm) in _skeleton(field_norm)):
                                key_match = fk
                                break
                        if not key_match:
                            continue
                        ck_key = str(ck).strip().lower()
                        ck_base = ck_key.replace(' county', '')
                        cand = centroids.get(ck_key) or centroids.get(ck_base) or centroids.get(f"{ck_base} county")
                        if not (src and cand):
                            continue
                        dist = _haversine_km(src[0], src[1], cand[0], cand[1])
                        if dist < best_dist:
                            best_dist = dist
                            best_cfg = c_fields_map.get(key_match)
                            best_name = ck
                    if best_cfg:
                        chosen_field_cfg = best_cfg
                        chosen_field_county = str(best_name)
                        field_resolution['matched_field'] = field
                        field_resolution['matched_in_county'] = chosen_field_county
                        field_resolution['method'] = 'nearest_county'
                        field_resolution['nearest_distance_km'] = best_dist
                    else:
                        # Fallback: nearest county where the requested field occurs either as a field key
                        # or within any formation name under that county's nested configs.
                        c_key = str(county).strip().lower()
                        c_base = c_key.replace(' county', '')
                        src = centroids.get(c_key) or centroids.get(c_base) or centroids.get(f"{c_base} county")
                        best_dist2 = float('inf')
                        best_name2: Optional[str] = None
                        for ck, cv in counties.items():
                            # Exclude current county
                            ck_key = str(ck).strip().lower()
                            ck_base = ck_key.replace(' county', '')
                            if ck_base == c_base:
                                continue
                            # Look for field key occurrence
                            c_fields_map = cv.get('fields') or (cv.get('overrides') or {}).get('fields') or {}
                            key_has_match = any(
                                (_normalize_field_name(str(fk)) == field_norm) or
                                (field_norm in _normalize_field_name(str(fk)) or _normalize_field_name(str(fk)) in field_norm) or
                                (_skeleton(_normalize_field_name(str(fk))) == _skeleton(field_norm)) or
                                (_skeleton(field_norm) in _skeleton(_normalize_field_name(str(fk))) or _skeleton(_normalize_field_name(str(fk))) in _skeleton(field_norm))
                                for fk in c_fields_map.keys()
                            )
                            # Or formation-name occurrence anywhere
                            has_mention = key_has_match or _mentions_field(cv, field_norm)
                            if not has_mention:
                                continue
                            cand = centroids.get(ck_key) or centroids.get(ck_base) or centroids.get(f"{ck_base} county")
                            if not (src and cand):
                                continue
                            dist2 = _haversine_km(src[0], src[1], cand[0], cand[1])
                            if dist2 < best_dist2:
                                best_dist2 = dist2
                                best_name2 = ck
                        if best_name2 is not None and best_dist2 < float('inf'):
                            field_resolution['matched_field'] = field
                            field_resolution['matched_in_county'] = str(best_name2)
                            field_resolution['method'] = 'nearest_county_occurrence'
                            field_resolution['nearest_distance_km'] = best_dist2
                # Merge selected field config
                # For 7C hybrid: load formation data from JSON instead of YAML
                print(f"🔍 LOADER: Checking 7C formation loading: is_7c_hybrid={is_7c_hybrid}, has_formation_json={formation_json is not None}, field={field}", flush=True)
                if is_7c_hybrid and formation_json and field:
                    print(f"🔍 LOADER: Loading formations from JSON for county={county}, field={field}", flush=True)
                    formation_tops_from_json = _load_7c_formations_for_field(
                        formation_json, county, field
                    )
                    print(f"🔍 LOADER: _load_7c_formations_for_field returned {len(formation_tops_from_json)} formations", flush=True)
                    if formation_tops_from_json:
                        print(f"🔍 LOADER: Loaded {len(formation_tops_from_json)} formations from JSON for {field}", flush=True)
                        merged.setdefault('district_overrides', {})
                        merged['district_overrides']['formation_tops'] = formation_tops_from_json
                    else:
                        print(f"⚠️ LOADER: formation_tops_from_json is empty!", flush=True)
                
                if chosen_field_cfg:
                    if isinstance(chosen_field_cfg.get('requirements'), dict):
                        merged = _merge(merged, {'requirements': chosen_field_cfg['requirements']})
                    if isinstance(chosen_field_cfg.get('preferences'), dict):
                        merged = _merge(merged, {'preferences': chosen_field_cfg['preferences']})
                    if isinstance(chosen_field_cfg.get('overrides'), dict):
                        merged = _merge(merged, {'district_overrides': chosen_field_cfg['overrides']})
                    if isinstance(chosen_field_cfg.get('proposal'), dict):
                        merged = _merge(merged, {'proposal': chosen_field_cfg['proposal']})
                    if isinstance(chosen_field_cfg.get('steps_overrides'), dict):
                        merged = _merge(merged, {'steps_overrides': chosen_field_cfg['steps_overrides']})
                    # Surface field-level formation_tops into district_overrides so rules can see them
                    # For 8A and others (not 7C hybrid): read from YAML
                    if isinstance(chosen_field_cfg.get('formation_tops'), list) and not is_7c_hybrid:
                        # Prefer field-level tops exclusively (do not mix with district-level defaults)
                        merged.setdefault('district_overrides', {})
                        merged['district_overrides']['formation_tops'] = list(chosen_field_cfg['formation_tops'])
                    # Optionally propagate tagging hints if present
                    if isinstance(chosen_field_cfg.get('tag'), dict):
                        merged.setdefault('district_overrides', {})
                        # Shallow merge tag (last-in wins for simple keys)
                        merged['district_overrides']['tag'] = _merge(merged['district_overrides'].get('tag') or {}, chosen_field_cfg['tag'])
                # If no field config in current county but we found a nearest county field config, merge it
                elif chosen_field_county and chosen_field_county != str(county):
                    # already set best_cfg into chosen_field_cfg above; since it's None here, re-resolve now for merge
                    counties = combined.get('counties') or {}
                    src_cfg = counties.get(chosen_field_county) or {}
                    # Resolve under overrides.fields
                    fields_map = src_cfg.get('fields') or (src_cfg.get('overrides') or {}).get('fields') or {}
                    chosen_field_cfg = _match_field({'fields': fields_map}) or {}
                    if isinstance(chosen_field_cfg.get('requirements'), dict):
                        merged = _merge(merged, {'requirements': chosen_field_cfg['requirements']})
                    if isinstance(chosen_field_cfg.get('preferences'), dict):
                        merged = _merge(merged, {'preferences': chosen_field_cfg['preferences']})
                    if isinstance(chosen_field_cfg.get('overrides'), dict):
                        merged = _merge(merged, {'district_overrides': chosen_field_cfg['overrides']})
                    if isinstance(chosen_field_cfg.get('proposal'), dict):
                        merged = _merge(merged, {'proposal': chosen_field_cfg['proposal']})
                    if isinstance(chosen_field_cfg.get('steps_overrides'), dict):
                        merged = _merge(merged, {'steps_overrides': chosen_field_cfg['steps_overrides']})
                    # If this looks like a pure overrides object (as produced under overrides.fields),
                    # merge it directly into district_overrides
                    if not any(k in chosen_field_cfg for k in ('requirements','preferences','overrides','proposal','steps_overrides')) and isinstance(chosen_field_cfg, dict):
                        merged = _merge(merged, {'district_overrides': chosen_field_cfg})
                    # If the resolved nearest-county field config provides formation_tops, prefer them exclusively
                    if isinstance(chosen_field_cfg.get('formation_tops'), list):
                        merged.setdefault('district_overrides', {})
                        merged['district_overrides']['formation_tops'] = list(chosen_field_cfg['formation_tops'])

    # FINAL DEBUG: Check what's in merged before returning
    final_district_overrides = merged.get('district_overrides') or {}
    final_formation_tops = final_district_overrides.get('formation_tops') or []
    print(f"🔍 LOADER FINAL: merged['district_overrides'] has {len(final_formation_tops)} formation_tops", flush=True)
    if final_formation_tops:
        print(f"🔍 LOADER FINAL: Formation names={[ft.get('formation') for ft in final_formation_tops[:5]]}", flush=True)
    
    out = {
        'policy_id': policy.get('policy_id'),
        'policy_version': policy.get('policy_version'),
        'jurisdiction': policy.get('jurisdiction'),
        'form': policy.get('form'),
        'effective_from': policy.get('effective_from'),
        'as_of': as_of.isoformat() if as_of else None,
        'base': base,
        'effective': merged,
        'district': district,
        'county': county,
    }
    # Attach field provenance if any field was requested
    if field_resolution.get('requested_field'):
        out['field_resolution'] = field_resolution
    out = _validate_minimal(out)
    
    # ONE MORE CHECK: After validation
    validated_effective = out.get('effective') or {}
    validated_overrides = validated_effective.get('district_overrides') or {}
    validated_ftops = validated_overrides.get('formation_tops') or []
    print(f"🔍 LOADER POST-VALIDATION: out['effective']['district_overrides'] has {len(validated_ftops)} formation_tops", flush=True)
    
    return out
