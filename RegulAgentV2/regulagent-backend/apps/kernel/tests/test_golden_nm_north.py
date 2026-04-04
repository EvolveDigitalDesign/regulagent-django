"""
Golden test for NM North (Figure A) — San Juan Basin, shallow well.

Calibrated output: 6 steps, all Class C cement (well < 6500 ft), no fill plugs.

Related to:
- NMAC 19.15.25 — Well Plugging and Abandonment
- C-103 Form — NM plugging plan submission
- NM Figure A (north region) — San Juan County
"""

from apps.kernel.services.policy_kernel import plan_from_facts
from apps.kernel.tests.test_golden_helpers import (
    load_nm_policy,
    assert_nm_base_invariants,
    assert_no_citation_leakage,
    get_steps_by_type,
    get_sacks,
)

# ---------------------------------------------------------------------------
# Well fixture — San Juan Basin, shallow gas well (Figure A / north region)
# ---------------------------------------------------------------------------

_FACTS = {
    'api14': {'value': '30-045-00001'},
    'state': {'value': 'NM'},
    'county': {'value': 'San Juan'},
    'casing_strings': [
        {'type': 'surface',    'size_in': 9.625, 'depth_ft': 400},
        {'type': 'production', 'size_in': 5.5,   'depth_ft': 3200},
    ],
    'perforations': [
        {'top_ft': 2500, 'bottom_ft': 2900},
    ],
    'formation_tops': [
        {'name': 'Fruitland',       'depth_ft': 1800},
        {'name': 'Pictured Cliffs', 'depth_ft': 2400},
    ],
    'total_depth_ft': {'value': 3200},
}

# TX step-type names — none of these should appear in an NM plan
_TX_STEP_TYPES = frozenset({
    'perf_circulate',
    'surface_casing_shoe_plug',
    'top_plug',
    'cut_casing_below_surface',
})


# ---------------------------------------------------------------------------
# Golden test
# ---------------------------------------------------------------------------

def test_golden_nm_north_figure_a():
    """Golden test: NM North (Figure A), San Juan County, shallow well."""

    policy = load_nm_policy()
    out = plan_from_facts(_FACTS, policy)

    # ------------------------------------------------------------------
    # 1. NM base invariants (jurisdiction, form, surface plug, CIBP, etc.)
    # ------------------------------------------------------------------
    assert_nm_base_invariants(out)

    # ------------------------------------------------------------------
    # 2. API14 echoed correctly in inputs_summary
    # ------------------------------------------------------------------
    assert out['inputs_summary']['api14'] == '30-045-00001', (
        f"Expected api14='30-045-00001', got {out['inputs_summary'].get('api14')!r}"
    )

    # ------------------------------------------------------------------
    # 3. Total step count == 6 (shallow well, no fill plugs needed)
    # ------------------------------------------------------------------
    steps = out.get('steps', [])
    assert len(steps) == 6, (
        f"Expected exactly 6 steps for shallow NM North well, got {len(steps)}. "
        f"Step types: {[s.get('type') for s in steps]}"
    )

    # ------------------------------------------------------------------
    # 4. ALL cement plugs use Class C — entire well < 6500 ft
    # ------------------------------------------------------------------
    cement_step_types = {'cibp_cap', 'formation_plug', 'shoe_plug', 'surface_plug'}
    for step in steps:
        st = step.get('type')
        if st in cement_step_types:
            cement_class = step.get('details', {}).get('cement_class')
            assert cement_class == 'C', (
                f"Step '{st}' should use Class C cement (well < 6500 ft), "
                f"got {cement_class!r}"
            )

    # ------------------------------------------------------------------
    # 5. No fill_plug steps present (shallow well, gaps within spacing limits)
    # ------------------------------------------------------------------
    fill_plugs = get_steps_by_type(out, 'fill_plug')
    assert len(fill_plugs) == 0, (
        f"Shallow well should have 0 fill_plug steps, found {len(fill_plugs)}"
    )

    # ------------------------------------------------------------------
    # 6. Formation plug for Pictured Cliffs at 2350-2450
    # ------------------------------------------------------------------
    formation_plugs = get_steps_by_type(out, 'formation_plug')
    pictured_cliffs_plugs = [
        s for s in formation_plugs
        if 'Pictured Cliffs' in (s.get('formation') or '')
        or 'Pictured Cliffs' in (s.get('formation_name') or '')
        or 'Pictured Cliffs' in (s.get('details', {}).get('formation') or '')
    ]
    assert len(pictured_cliffs_plugs) == 1, (
        f"Expected exactly 1 formation_plug for Pictured Cliffs, "
        f"found {len(pictured_cliffs_plugs)}"
    )
    pc = pictured_cliffs_plugs[0]
    assert pc.get('top_ft') == 2350, (
        f"Pictured Cliffs formation_plug top_ft should be 2350, "
        f"got {pc.get('top_ft')}"
    )
    assert pc.get('bottom_ft') == 2450, (
        f"Pictured Cliffs formation_plug bottom_ft should be 2450, "
        f"got {pc.get('bottom_ft')}"
    )

    # ------------------------------------------------------------------
    # 7. Region "north" — formation_plug details.region_requirements contains 'north'
    # ------------------------------------------------------------------
    pc_region_reqs = str(pc.get('details', {}).get('region_requirements', ''))
    assert 'north' in pc_region_reqs, (
        f"Pictured Cliffs formation_plug details.region_requirements should contain "
        f"'north' (San Juan Basin / Figure A), got {pc_region_reqs!r}"
    )

    # ------------------------------------------------------------------
    # 8. Fewer total steps than deep wells (exactly 6 steps confirmed above)
    # ------------------------------------------------------------------
    # Already asserted in assertion 3 — guard here for readability.
    assert len(steps) == 6, (
        "NM North shallow well should have exactly 6 steps (fewer than deep wells)"
    )

    # ------------------------------------------------------------------
    # 9. All cement sacks >= 25 (NM minimum; mechanical_plug is exempted)
    # ------------------------------------------------------------------
    for step in steps:
        if step.get('type') == 'mechanical_plug':
            continue
        sacks = get_sacks(step)
        assert sacks >= 25, (
            f"Step '{step.get('type')}' must have >= 25 sacks "
            f"(NMAC 19.15.25 minimum), got {sacks}"
        )

    # ------------------------------------------------------------------
    # 10. No cross-jurisdiction citation leakage (no TX SWR / tx.tac refs)
    # ------------------------------------------------------------------
    assert_no_citation_leakage(out, 'NM')

    # ------------------------------------------------------------------
    # 11. No TX step types present
    # ------------------------------------------------------------------
    tx_steps_found = [
        s.get('type') for s in steps if s.get('type') in _TX_STEP_TYPES
    ]
    assert len(tx_steps_found) == 0, (
        f"NM plan must not contain TX step types, found: {tx_steps_found}"
    )

    # ------------------------------------------------------------------
    # 12. Production shoe plug at 3150-3250 is open hole
    # ------------------------------------------------------------------
    shoe_plugs = get_steps_by_type(out, 'shoe_plug')
    production_shoes = [
        s for s in shoe_plugs
        if s.get('top_ft') == 3150 and s.get('bottom_ft') == 3250
    ]
    assert len(production_shoes) == 1, (
        f"Expected exactly 1 shoe_plug at 3150-3250 (production 5.5\"), "
        f"found {len(production_shoes)}. "
        f"All shoe_plugs: {[(s.get('top_ft'), s.get('bottom_ft')) for s in shoe_plugs]}"
    )
    prod_shoe = production_shoes[0]
    hole_type = prod_shoe.get('hole_type') or prod_shoe.get('details', {}).get('hole_type')
    assert hole_type == 'open', (
        f"Production shoe_plug at 3150-3250 should have hole_type='open', "
        f"got hole_type={hole_type!r}"
    )
