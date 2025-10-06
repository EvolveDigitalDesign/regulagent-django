import json
import os
from typing import Dict, List

import yaml


BASE_DIR = os.path.dirname(os.path.dirname(__file__))  # apps/policy
PACKS_DIR = os.path.join(BASE_DIR, 'packs')


def load_yaml(path: str) -> Dict:
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def validate_knobs_have_citations(policy: Dict) -> List[str]:
    errors: List[str] = []
    base = policy.get('base') or {}
    req = base.get('requirements') or {}
    # knobs we expect to exist in TX/W-3A
    expected = [
        'surface_casing_shoe_plug_min_ft',
        'uqw_isolation_min_len_ft',
        'uqw_below_base_ft',
        'uqw_above_base_ft',
        'cement_above_cibp_min_ft',
        'never_below_cibp',
        'duqw_isolation_required',
        'squeeze_factor_default',
        'open_hole_excess_default',
    ]
    for k in expected:
        if k not in req:
            errors.append(f"missing knob: base.requirements.{k}")
        else:
            knob = req.get(k)
            cites = []
            if isinstance(knob, dict):
                cites = knob.get('citation_keys') or []
            if not cites:
                errors.append(f"missing citation for knob: base.requirements.{k}")
    return errors


def validate_policy_file(rel_path: str) -> List[str]:
    p = load_yaml(os.path.join(PACKS_DIR, rel_path))
    errors: List[str] = []
    errors.extend(validate_knobs_have_citations(p))
    return errors


