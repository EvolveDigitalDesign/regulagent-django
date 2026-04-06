"""
Golden test for NM Potash (Figure C) region — Eddy County, T20S R30E.

Calibrated output covers a 5500 ft well with surface (13.375") and
production (7.0") casing strings, one perforation interval, and two
formation tops (Salado at 1200 ft, San Andres at 3500 ft).

Expected plan shape (9 steps):
  1. mechanical_plug  at 4750-4751   (CIBP)
  2. cibp_cap         at 4650-4750   (100 ft, Class C)
  3. formation_plug   at 1150-1250   (Salado, Class C, 25 sacks, potash region)
  4. shoe_plug        at  450- 550   (surface 13.375", Class C, 25 sacks)
  5. shoe_plug        at 5450-5550   (production 7.0", Class C, open hole, ~33.3 sacks)
  6. surface_plug     at    0-  50   (Class C, 25 sacks, circulate)
  7. fill_plug        at 3300-3400   (gap 4200 ft open, Class C, ~33.3 sacks)
  8. fill_plug        at 4375-4475   (gap 2050 ft open, Class C, ~33.3 sacks)
  9. fill_plug        at 2225-2325   (gap 2050 ft open, Class C, ~33.3 sacks)

Related to:
- NMAC 19.15.25 — Well Plugging and Abandonment
- NM COA Figure C — Potash Region Plugging Rules
- POL-NM-001 — NM Region Rules Engine
"""

import pytest

from apps.kernel.services.policy_kernel import plan_from_facts
from apps.kernel.tests.test_golden_helpers import (
    load_nm_policy,
    assert_nm_base_invariants,
    assert_no_citation_leakage,
    get_steps_by_type,
    get_sacks,
)


# ---------------------------------------------------------------------------
# Well fixture
# ---------------------------------------------------------------------------

_POTASH_FACTS = {
    'api14': {'value': '30-015-99999'},
    'state': {'value': 'NM'},
    'county': {'value': 'Eddy'},
    'township': {'value': 'T20S'},
    'range': {'value': 'R30E'},
    'casing_strings': [
        {'type': 'surface',    'size_in': 13.375, 'depth_ft': 500},
        {'type': 'production', 'size_in': 7.0,    'depth_ft': 5500},
    ],
    'perforations': [
        {'top_ft': 4800, 'bottom_ft': 5200},
    ],
    'formation_tops': [
        {'name': 'Salado',    'depth_ft': 1200},
        {'name': 'San Andres', 'depth_ft': 3500},
    ],
    'total_depth_ft': {'value': 5500},
}


# ---------------------------------------------------------------------------
# Golden test
# ---------------------------------------------------------------------------

def test_golden_nm_potash_figure_c():
    """Golden plan for NM Potash (Figure C) — Eddy County, T20S R30E, 5500 ft well."""
    facts = _POTASH_FACTS
    policy = load_nm_policy()

    out = plan_from_facts(facts, policy)

    # ------------------------------------------------------------------
    # 1. NM base invariants (jurisdiction, district=None, form, CIBP cap,
    #    surface plug, min sacks, no TX citations, etc.)
    # ------------------------------------------------------------------
    assert_nm_base_invariants(out)

    # ------------------------------------------------------------------
    # 2. API14 echoed in inputs_summary
    # ------------------------------------------------------------------
    assert out['inputs_summary']['api14'] == '30-015-99999', (
        f"inputs_summary.api14 should be '30-015-99999', "
        f"got {out['inputs_summary'].get('api14')!r}"
    )

    # ------------------------------------------------------------------
    # 3. Total step count == 9
    # ------------------------------------------------------------------
    steps = out.get('steps', [])
    assert len(steps) == 9, (
        f"Expected 9 plan steps for NM Potash well, got {len(steps)}: "
        f"{[s.get('type') for s in steps]}"
    )

    # ------------------------------------------------------------------
    # 4. Region detected as "potash"
    #    The formation_plug for Salado should carry potash metadata in
    #    details.region_requirements.
    # ------------------------------------------------------------------
    formation_plugs = get_steps_by_type(out, 'formation_plug')
    assert len(formation_plugs) >= 1, "Expected at least one formation_plug step"

    salado_plug = next(
        (s for s in formation_plugs
         if (s.get('details', {}).get('formation') or '').lower() == 'salado'
         or (s.get('formation') or '').lower() == 'salado'),
        None,
    )
    assert salado_plug is not None, (
        "Expected a formation_plug for the Salado formation (potash marker)"
    )

    region_reqs = salado_plug.get('details', {}).get('region_requirements') or ''
    # Accept dict or string representation — just check for the substring 'potash'.
    if isinstance(region_reqs, dict):
        region_reqs_str = str(region_reqs).lower()
    else:
        region_reqs_str = str(region_reqs).lower()

    assert 'potash' in region_reqs_str, (
        f"formation_plug details.region_requirements should reference 'potash', "
        f"got {salado_plug.get('details', {}).get('region_requirements')!r}"
    )

    # ------------------------------------------------------------------
    # 5. ALL cement plugs use Class C (entire well < 6500 ft)
    # ------------------------------------------------------------------
    CEMENT_STEP_TYPES = {
        'cibp_cap', 'formation_plug', 'shoe_plug', 'surface_plug', 'fill_plug',
    }
    cement_steps = [s for s in steps if s.get('type') in CEMENT_STEP_TYPES]
    assert len(cement_steps) > 0, "Expected at least one cement-bearing step"

    for step in cement_steps:
        cement_class = step.get('details', {}).get('cement_class')
        assert cement_class == 'C', (
            f"Step '{step.get('type')}' at {step.get('top_ft')}-{step.get('bottom_ft')} ft "
            f"should use Class C cement (well < 6500 ft), got {cement_class!r}"
        )

    # ------------------------------------------------------------------
    # 6. Formation plug for Salado at 1150-1250 ft
    # ------------------------------------------------------------------
    assert salado_plug.get('top_ft') == 1150, (
        f"Salado formation_plug top_ft should be 1150, "
        f"got {salado_plug.get('top_ft')}"
    )
    assert salado_plug.get('bottom_ft') == 1250, (
        f"Salado formation_plug bottom_ft should be 1250, "
        f"got {salado_plug.get('bottom_ft')}"
    )

    # ------------------------------------------------------------------
    # 7. Exactly 3 fill plugs present (aggressive spacing enforcement in
    #    open hole sections)
    # ------------------------------------------------------------------
    fill_plugs = get_steps_by_type(out, 'fill_plug')
    assert len(fill_plugs) == 3, (
        f"Expected 3 fill_plug steps (open-hole spacing enforcement), "
        f"got {len(fill_plugs)}"
    )

    # ------------------------------------------------------------------
    # 8. All sacks >= 25 (NM minimum)
    # ------------------------------------------------------------------
    for step in cement_steps:
        sacks = get_sacks(step)
        assert sacks >= 25, (
            f"Step '{step.get('type')}' at "
            f"{step.get('top_ft')}-{step.get('bottom_ft')} ft "
            f"has {sacks} sacks, which is below the NM minimum of 25"
        )

    # ------------------------------------------------------------------
    # 9. No TX citation leakage
    # ------------------------------------------------------------------
    assert_no_citation_leakage(out, 'NM')

    # ------------------------------------------------------------------
    # 10. No TX-specific step types present
    # ------------------------------------------------------------------
    TX_STEP_TYPES = {'perf_circulate', 'squeeze', 'surface_casing_shoe_plug'}
    for step in steps:
        assert step.get('type') not in TX_STEP_TYPES, (
            f"TX step type '{step.get('type')}' found in NM plan"
        )

    # ------------------------------------------------------------------
    # 11. Production shoe plug at 5450-5550 is open hole
    # ------------------------------------------------------------------
    shoe_plugs = get_steps_by_type(out, 'shoe_plug')
    prod_shoe = next(
        (s for s in shoe_plugs
         if s.get('top_ft') == 5450 and s.get('bottom_ft') == 5550),
        None,
    )
    assert prod_shoe is not None, (
        "Expected a shoe_plug at 5450-5550 ft (production casing shoe, open hole)"
    )
    hole_type = prod_shoe.get('hole_type') or prod_shoe.get('details', {}).get('hole_type')
    assert hole_type == 'open', (
        f"Production shoe plug at 5450-5550 ft should have hole_type='open', "
        f"got {hole_type!r}"
    )
