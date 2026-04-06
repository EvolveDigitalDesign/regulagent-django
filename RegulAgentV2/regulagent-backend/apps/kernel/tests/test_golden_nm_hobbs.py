"""
Golden test: NM Hobbs (COA Figure D) — Lea County deep well.

Calibrated expected output (10 steps):
  1. mechanical_plug  10450-10451  CIBP
  2. cibp_cap         10350-10450  100 ft, Class H
  3. formation_plug   10950-11050  Wolfcamp, Class H, ~25.6 sacks
  4. formation_plug    4750-4850   San Andres, Class C, 25 sacks
  5. shoe_plug          550-650    surface 13.375", Class C, 25 sacks
  6. shoe_plug         4150-4250   intermediate 9.625", Class C, 25 sacks
  7. shoe_plug        12450-12550  production 7.0", Class H, open hole, ~34.2 sacks
  8. surface_plug         0-50     Class C, 25 sacks, circulate
  9. fill_plug         7850-7950   gap 6100' cased, Class H, ~25.6 sacks
 10. fill_plug         2350-2450   gap 3500' cased, Class C, 25 sacks

Related to:
- NMAC 19.15.25 (Well Plugging and Abandonment)
- NM COA Figure D (south_hobbs / central_basin_platform region)
- C-103 Form — NM plugging plan submission
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
# Well fixture
# ---------------------------------------------------------------------------

_HOBBS_FACTS = {
    'api14': {'value': '30-025-37129'},
    'state': {'value': 'NM'},
    'county': {'value': 'Lea'},
    'township': {'value': 'T22S'},
    'range': {'value': 'R36E'},
    'casing_strings': [
        {'type': 'surface',      'size_in': 13.375, 'depth_ft': 600},
        {'type': 'intermediate', 'size_in': 9.625,  'depth_ft': 4200},
        {'type': 'production',   'size_in': 7.0,    'depth_ft': 12500},
    ],
    'perforations': [
        {'top_ft': 10500, 'bottom_ft': 11500},
    ],
    'formation_tops': [
        {'name': 'San Andres', 'depth_ft': 4800},
        {'name': 'Bone Spring', 'depth_ft': 9500},
        {'name': 'Wolfcamp',   'depth_ft': 11000},
    ],
    'total_depth_ft': {'value': 12500},
}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_golden_nm_hobbs_figure_d():
    """Golden test: NM Hobbs (Figure D) Lea County deep well produces 10 correct steps."""
    policy = load_nm_policy()
    out = plan_from_facts(_HOBBS_FACTS, policy)

    # 1. Base NM invariants (jurisdiction, form, structure)
    assert_nm_base_invariants(out)

    # 2. API-14 round-trip
    assert out['inputs_summary']['api14'] == '30-025-37129', (
        f"inputs_summary.api14 mismatch: {out['inputs_summary'].get('api14')!r}"
    )

    # 3. Total step count
    steps = out.get('steps', [])
    assert len(steps) == 10, (
        f"Expected 10 steps for Hobbs Figure D well, got {len(steps)}: "
        f"{[s.get('type') for s in steps]}"
    )

    # 4. Deep well (12500 ft) triggers fill plug insertion — at least 2 fill_plug steps
    fill_plugs = get_steps_by_type(out, 'fill_plug')
    assert len(fill_plugs) >= 2, (
        f"Expected >= 2 fill_plug steps for 12500 ft deep well (NM max cased spacing 3000 ft), "
        f"got {len(fill_plugs)}"
    )

    # 5. Formation plugs: Wolfcamp and San Andres
    formation_plugs = get_steps_by_type(out, 'formation_plug')
    assert len(formation_plugs) == 2, (
        f"Expected 2 formation_plug steps (Wolfcamp + San Andres), got {len(formation_plugs)}: "
        f"{[s.get('formation') for s in formation_plugs]}"
    )

    # 6. Wolfcamp formation plug uses Class H (11000 ft >= 6500 ft cutoff)
    wolfcamp_plugs = [s for s in formation_plugs if 'Wolfcamp' in (s.get('formation') or '')]
    assert len(wolfcamp_plugs) == 1, (
        f"Expected 1 Wolfcamp formation plug, found {len(wolfcamp_plugs)}"
    )
    wolfcamp = wolfcamp_plugs[0]
    wolfcamp_class = (wolfcamp.get('details') or {}).get('cement_class')
    assert wolfcamp_class == 'H', (
        f"Wolfcamp plug at 11000 ft should use Class H (>= 6500 ft cutoff), got {wolfcamp_class!r}"
    )

    # 7. San Andres formation plug uses Class C (4800 ft < 6500 ft cutoff)
    san_andres_plugs = [s for s in formation_plugs if 'San Andres' in (s.get('formation') or '')]
    assert len(san_andres_plugs) == 1, (
        f"Expected 1 San Andres formation plug, found {len(san_andres_plugs)}"
    )
    san_andres = san_andres_plugs[0]
    san_andres_class = (san_andres.get('details') or {}).get('cement_class')
    assert san_andres_class == 'C', (
        f"San Andres plug at 4800 ft should use Class C (< 6500 ft cutoff), got {san_andres_class!r}"
    )

    # 8. Production shoe plug at 12450-12550 is open hole with Class H
    shoe_plugs = get_steps_by_type(out, 'shoe_plug')
    prod_shoe = next(
        (s for s in shoe_plugs if s.get('top_ft') == 12450 and s.get('bottom_ft') == 12550),
        None,
    )
    assert prod_shoe is not None, (
        f"Expected production shoe plug at 12450-12550 ft; "
        f"found shoe plugs at: {[(s.get('top_ft'), s.get('bottom_ft')) for s in shoe_plugs]}"
    )
    assert prod_shoe.get('hole_type') == 'open', (
        f"Production shoe plug (12450-12550) should be open hole, got hole_type={prod_shoe.get('hole_type')!r}"
    )
    prod_shoe_class = (prod_shoe.get('details') or {}).get('cement_class')
    assert prod_shoe_class == 'H', (
        f"Production shoe plug at 12450-12550 ft should use Class H, got {prod_shoe_class!r}"
    )

    # 9. Production shoe sacks > 25 (open hole 100% excess inflates volume)
    prod_shoe_sacks = get_sacks(prod_shoe)
    assert prod_shoe_sacks > 25, (
        f"Production shoe (open hole) sacks should exceed 25 due to 100% open-hole excess, "
        f"got {prod_shoe_sacks}"
    )

    # 10. All cement steps have >= 25 sacks (NM minimum)
    MECHANICAL_TYPES = {'mechanical_plug'}
    cement_steps = [
        s for s in steps
        if (s.get('type')) not in MECHANICAL_TYPES
    ]
    sack_violations = []
    for s in cement_steps:
        sacks = get_sacks(s)
        if sacks < 25:
            sack_violations.append(
                f"{s.get('type')} "
                f"at {s.get('top_ft')}-{s.get('bottom_ft')} ft: {sacks} sacks"
            )
    assert not sack_violations, (
        f"Steps below 25-sack NM minimum: {sack_violations}"
    )

    # 11. Total sack count > 200 (deep 12500 ft well = more cement)
    total_sacks = sum(get_sacks(s) for s in cement_steps)
    assert total_sacks > 200, (
        f"Expected > 200 total sacks for 12500 ft deep well, got {total_sacks:.1f}"
    )

    # 12. No TX citation leakage
    assert_no_citation_leakage(out, 'NM')

    # 13. No TX-specific step types in the plan
    tx_only_types = {'perf_circulate', 'surface_casing_shoe_plug', 'top_plug', 'cut_casing_below_surface'}
    tx_steps_found = [
        s for s in steps
        if (s.get('type')) in tx_only_types
    ]
    assert not tx_steps_found, (
        f"NM plan must not contain TX-specific step types; found: "
        f"{[(s.get('type')) for s in tx_steps_found]}"
    )
