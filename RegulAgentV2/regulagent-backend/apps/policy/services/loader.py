import os
import yaml
from datetime import datetime
from typing import Any, Dict, Optional

BASE_DIR = os.path.dirname(os.path.dirname(__file__))  # apps/policy
PACKS_DIR = os.path.join(BASE_DIR, 'packs')

REQUIRED_BASE_KEYS = ['citations', 'requirements', 'cement_class']


class PolicyIncomplete(Exception):
    """Raised when required policy knobs are missing."""


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


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


def get_effective_policy(district: Optional[str] = None, county: Optional[str] = None, as_of: Optional[datetime] = None, pack_rel_path: str = 'tx/w3a/draft.yml') -> Dict[str, Any]:
    pack_path = os.path.join(PACKS_DIR, pack_rel_path)
    policy = _load_yaml(pack_path)
    base = policy.get('base') or {}
    merged = dict(base)
    if district:
        overlays = policy.get('district_overlays', {})
        d_ov = overlays.get(str(district)) or overlays.get(district)
        if d_ov:
            merged = _merge(merged, d_ov)
        # External district county overlays, if present alongside pack
        ext_dir = os.path.join(PACKS_DIR, 'tx', 'w3a', 'district_overlays')
        # Merge district-wide combined overlay requirements/preferences if available
        combined_name = f"{str(district).lower()}__auto.yml"
        combined_path = os.path.join(ext_dir, combined_name)
        combined: Dict[str, Any] | None = None
        if os.path.exists(combined_path):
            combined = _load_yaml(combined_path)
            # merge district-level requirements/preferences
            if isinstance(combined.get('requirements'), dict):
                merged = _merge(merged, {'requirements': combined['requirements']})
            if isinstance(combined.get('preferences'), dict):
                merged = _merge(merged, {'preferences': combined['preferences']})
        if county:
            # Normalize county name to file-safe
            safe_county = county.lower().replace(' ', '_')
            file_name = f"{str(district).lower()}__{safe_county}.yml"
            ext_path = os.path.join(ext_dir, file_name)
            county_req: Dict[str, Any] | None = None
            county_overrides: Dict[str, Any] | None = None
            if os.path.exists(ext_path):
                ext_overlay = _load_yaml(ext_path)
                county_req = (ext_overlay.get('requirements') or {})
            else:
                # Fallback: combined overlay file per district (e.g., 7c__auto.yml)
                if combined:
                    counties = combined.get('counties') or {}
                    # lookup by multiple aliases
                    aliases = [
                        str(county),
                        f"{county} County" if not str(county).lower().endswith(" county") else str(county)[:-7],
                    ]
                    cdata = None
                    for alias in aliases:
                        cdata = counties.get(alias)
                        if cdata:
                            break
                    if not cdata:
                        # fallback: case-insensitive contains/equals
                        lc = str(county).lower()
                        for k, v in counties.items():
                            kl = str(k).lower()
                            if kl == lc or kl.startswith(lc) or lc in kl:
                                cdata = v
                                break
                    if cdata:
                        county_req = (cdata.get('requirements') or {})
                        county_overrides = (cdata.get('overrides') or {})
            if county_req:
                merged = _merge(merged, {'requirements': county_req})
            if county_overrides:
                merged = _merge(merged, {'district_overrides': county_overrides})
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
    out = _validate_minimal(out)
    return out
