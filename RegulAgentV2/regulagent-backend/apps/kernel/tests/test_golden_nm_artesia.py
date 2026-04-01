"""
Golden test for NM Artesia (Figure B) region — Eddy County, T20S R26E.

Calibrated output: 8 steps covering a 7376 ft well with surface (13.375"),
intermediate (9.625"), and production (5.5") casing strings, one perforation
interval (6800-7200 ft), and three formation tops.

Expected plan shape (8 steps):
  1. mechanical_plug  at 6750-6751   (CIBP)
  2. cibp_cap         at 6650-6750   (100 ft, Class H)
  3. formation_plug   at 3150-3250   (San Andres, Class C, 25 sacks)
  4. shoe_plug        at  827- 927   (surface casing 13.375", Class C, 25 sacks)
  5. shoe_plug        at 3338-3438   (intermediate casing 9.625", Class C, 25 sacks)
  6. shoe_plug        at 7326-7426   (production casing 5.5", Class H, open hole, 25 sacks)
  7. surface_plug     at    0-  50   (Class C, 25 sacks, circulate)
  8. fill_plug        at 5332-5432   (gap 3888 ft, open hole, Class C, 25 sacks)

Related to:
- NMAC 19.15.25 — Well Plugging and Abandonment
- C-103 Form — NM plugging plan submission
- NM COA Figure B (south_artesia region) — Eddy County
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
# Well fixture — Artesia region, Eddy County, T20S R26E
# ---------------------------------------------------------------------------

_ARTESIA_FACTS = {
    'api14': {'value': '30-015-41267'},
    'state': {'value': 'NM'},
    'county': {'value': 'Eddy'},
    'township': {'value': 'T20S'},
    'range': {'value': 'R26E'},
    'casing_strings': [
        {'type': 'surface',      'size_in': 13.375, 'depth_ft': 877},
        {'type': 'intermediate', 'size_in': 9.625,  'depth_ft': 3388},
        {'type': 'production',   'size_in': 5.5,    'depth_ft': 7376},
    ],
    'perforations': [
        {'top_ft': 6800, 'bottom_ft': 7200},
    ],
    'formation_tops': [
        {'name': 'San Andres', 'depth_ft': 3200},
        {'name': 'Bone Spring', 'depth_ft': 6500},
        {'name': 'Wolfcamp',   'depth_ft': 7100},
    ],
    'total_depth_ft': {'value': 7376},
}

# TX-specific step type names — none of these should appear in an NM plan
_TX_STEP_TYPES = frozenset({
    'perf_circulate',
    'surface_casing_shoe_plug',
    'top_plug',
    'cut_casing_below_surface',
})


# ---------------------------------------------------------------------------
# Golden test
# ---------------------------------------------------------------------------

def test_golden_nm_artesia_figure_b():
    """Golden plan for NM Artesia (Figure B) — Eddy County, T20S R26E, 7376 ft well."""
    facts = _ARTESIA_FACTS
    policy = load_nm_policy()

    out = plan_from_facts(facts, policy)

    # ------------------------------------------------------------------
    # 1. NM base invariants (jurisdiction, district=None, form, CIBP cap,
    #    surface plug, min sacks, no TX citations, etc.)
    # ------------------------------------------------------------------
    assert_nm_base_invariants(out)

    # ------------------------------------------------------------------
    # 2. API14 echoed correctly in inputs_summary
    # ------------------------------------------------------------------
    assert out['inputs_summary']['api14'] == '30-015-41267', (
        f"inputs_summary.api14 should be '30-015-41267', "
        f"got {out['inputs_summary'].get('api14')!r}"
    )

    # ------------------------------------------------------------------
    # 3. Total step count == 8
    # ------------------------------------------------------------------
    steps = out.get('steps', [])
    assert len(steps) == 8, (
        f"Expected 8 plan steps for NM Artesia well, got {len(steps)}: "
        f"{[s.get('type') for s in steps]}"
    )

    # ------------------------------------------------------------------
    # 4. Required step types present
    # ------------------------------------------------------------------
    step_types_present = {s.get('type') for s in steps}
    for required_type in (
        'mechanical_plug', 'cibp_cap', 'formation_plug',
        'shoe_plug', 'surface_plug', 'fill_plug',
    ):
        assert required_type in step_types_present, (
            f"Expected step type '{required_type}' in plan, "
            f"found types: {sorted(step_types_present)}"
        )

    # ------------------------------------------------------------------
    # 5. CIBP cap at 6650-6750 (100 ft interval)
    # ------------------------------------------------------------------
    cibp_caps = get_steps_by_type(out, 'cibp_cap')
    assert len(cibp_caps) == 1, (
        f"Expected exactly 1 cibp_cap step, found {len(cibp_caps)}"
    )
    cap = cibp_caps[0]
    assert cap.get('top_ft') == 6650, (
        f"cibp_cap top_ft should be 6650, got {cap.get('top_ft')}"
    )
    assert cap.get('bottom_ft') == 6750, (
        f"cibp_cap bottom_ft should be 6750, got {cap.get('bottom_ft')}"
    )
    cap_length = cap.get('bottom_ft', 0) - cap.get('top_ft', 0)
    assert cap_length == 100, (
        f"cibp_cap interval must be 100 ft (NM requirement), got {cap_length} ft"
    )

    # ------------------------------------------------------------------
    # 6. Formation plug for San Andres at 3150-3250, Class C
    # ------------------------------------------------------------------
    formation_plugs = get_steps_by_type(out, 'formation_plug')
    san_andres_plug = next(
        (
            s for s in formation_plugs
            if 'San Andres' in (s.get('details', {}).get('formation') or '')
            or 'San Andres' in (s.get('formation') or '')
            or 'San Andres' in (s.get('formation_name') or '')
        ),
        None,
    )
    assert san_andres_plug is not None, (
        "Expected a formation_plug for San Andres; "
        f"found formation_plugs: {formation_plugs}"
    )
    assert san_andres_plug.get('top_ft') == 3150, (
        f"San Andres formation_plug top_ft should be 3150, "
        f"got {san_andres_plug.get('top_ft')}"
    )
    assert san_andres_plug.get('bottom_ft') == 3250, (
        f"San Andres formation_plug bottom_ft should be 3250, "
        f"got {san_andres_plug.get('bottom_ft')}"
    )
    san_andres_class = san_andres_plug.get('details', {}).get('cement_class')
    assert san_andres_class == 'C', (
        f"San Andres formation_plug cement_class should be 'C' (depth 3200 ft < 6500 ft), "
        f"got {san_andres_class!r}"
    )

    # ------------------------------------------------------------------
    # 7. Shoe plugs at 827-927, 3338-3438, 7326-7426
    # ------------------------------------------------------------------
    shoe_plugs = get_steps_by_type(out, 'shoe_plug')
    shoe_intervals = {
        (s.get('top_ft'), s.get('bottom_ft')) for s in shoe_plugs
    }
    for expected_top, expected_bottom in ((827, 927), (3338, 3438), (7326, 7426)):
        assert (expected_top, expected_bottom) in shoe_intervals, (
            f"Expected shoe_plug at {expected_top}-{expected_bottom} ft, "
            f"found intervals: {sorted(shoe_intervals)}"
        )

    # ------------------------------------------------------------------
    # 8. Production shoe plug (7326-7426) is open hole
    # ------------------------------------------------------------------
    prod_shoe = next(
        (s for s in shoe_plugs if s.get('top_ft') == 7326 and s.get('bottom_ft') == 7426),
        None,
    )
    assert prod_shoe is not None, (
        "Expected a shoe_plug at 7326-7426 ft (production casing, open hole)"
    )
    hole_type = (
        prod_shoe.get('hole_type')
        or prod_shoe.get('details', {}).get('hole_type')
        or prod_shoe.get('geometry_context')
        or prod_shoe.get('details', {}).get('geometry_context')
    )
    assert hole_type == 'open', (
        f"Production shoe plug at 7326-7426 ft should have hole_type='open', "
        f"got {hole_type!r}"
    )

    # ------------------------------------------------------------------
    # 9. Production shoe plug has Class H cement (depth >= 6500 ft)
    # ------------------------------------------------------------------
    prod_shoe_class = prod_shoe.get('details', {}).get('cement_class')
    assert prod_shoe_class == 'H', (
        f"Production shoe plug at 7326-7426 ft should use Class H cement "
        f"(depth >= 6500 ft), got {prod_shoe_class!r}"
    )

    # ------------------------------------------------------------------
    # 10. All other plugs shallower than 6500 ft use Class C cement
    # ------------------------------------------------------------------
    CEMENT_STEP_TYPES = {'cibp_cap', 'formation_plug', 'shoe_plug', 'surface_plug', 'fill_plug'}
    for step in steps:
        st = step.get('type')
        if st not in CEMENT_STEP_TYPES:
            continue
        bottom_depth = step.get('bottom_ft') or 0
        if bottom_depth >= 6500:
            continue
        cement_class = step.get('details', {}).get('cement_class')
        assert cement_class == 'C', (
            f"Step '{st}' at {step.get('top_ft')}-{step.get('bottom_ft')} ft "
            f"(below 6500 ft cutoff) should use Class C cement, got {cement_class!r}"
        )

    # ------------------------------------------------------------------
    # 11. Surface plug at 0-50, Class C, operation_type circulate
    # ------------------------------------------------------------------
    surface_plugs = get_steps_by_type(out, 'surface_plug')
    assert len(surface_plugs) == 1, (
        f"Expected exactly 1 surface_plug, found {len(surface_plugs)}"
    )
    sp = surface_plugs[0]
    assert sp.get('top_ft') == 0, (
        f"Surface plug top_ft should be 0, got {sp.get('top_ft')}"
    )
    assert sp.get('bottom_ft') == 50, (
        f"Surface plug bottom_ft should be 50, got {sp.get('bottom_ft')}"
    )
    sp_class = sp.get('details', {}).get('cement_class')
    assert sp_class == 'C', (
        f"Surface plug cement_class should be 'C', got {sp_class!r}"
    )
    sp_op_type = sp.get('operation_type')
    assert sp_op_type == 'circulate', (
        f"Surface plug operation_type should be 'circulate', got {sp_op_type!r}"
    )

    # ------------------------------------------------------------------
    # 12. Fill plug present (gap enforcement triggered by large open-hole span)
    # ------------------------------------------------------------------
    fill_plugs = get_steps_by_type(out, 'fill_plug')
    assert len(fill_plugs) >= 1, (
        "Expected at least 1 fill_plug step (gap enforcement for large open-hole span)"
    )

    # ------------------------------------------------------------------
    # 13. All sacks >= 25 (NM minimum; mechanical_plug is exempted)
    # ------------------------------------------------------------------
    for step in steps:
        if step.get('type') == 'mechanical_plug':
            continue
        if step.get('type') not in CEMENT_STEP_TYPES:
            continue
        sacks = get_sacks(step)
        assert sacks >= 25, (
            f"Step '{step.get('type')}' at "
            f"{step.get('top_ft')}-{step.get('bottom_ft')} ft "
            f"has {sacks} sacks — below NM minimum of 25 (NMAC 19.15.25)"
        )

    # ------------------------------------------------------------------
    # 14. No cross-jurisdiction citation leakage
    # ------------------------------------------------------------------
    assert_no_citation_leakage(out, 'NM')

    # ------------------------------------------------------------------
    # 15. No TX-specific step types present
    # ------------------------------------------------------------------
    tx_steps_found = [
        s.get('type') for s in steps if s.get('type') in _TX_STEP_TYPES
    ]
    assert len(tx_steps_found) == 0, (
        f"NM plan must not contain TX-specific step types, found: {tx_steps_found}"
    )
