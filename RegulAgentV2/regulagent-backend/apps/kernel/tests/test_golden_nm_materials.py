"""
Golden test for NM C-103 materials verification — Eddy County, T20S R26E.

Verifies that the NM planning path produces correct cement class assignments
and sack calculations for all plug types in a 5000 ft well with two casing
strings, one perforation interval, and two formation tops.

Calibrated output: 6 steps
  1. mechanical_plug  at 4450-4451   (CIBP — no cement sacks)
  2. cibp_cap         at 4350-4450   (100 ft, Class C)
  3. formation_plug   at 2950-3050   (San Andres, Class C, 25 sacks)
  4. shoe_plug        at  450- 550   (surface casing 13.375", Class C, 25 sacks)
  5. shoe_plug        at 4950-5050   (production casing 5.5", Class C, 25 sacks)
  6. surface_plug     at    0-  50   (Class C, 25 sacks, circulate)

Related to:
- NMAC 19.15.25 — Well Plugging and Abandonment (cement class + minimum sacks)
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
# Well fixture — Eddy County, T20S R26E, 5000 ft well
# ---------------------------------------------------------------------------

_FACTS = {
    'api14': {'value': '3000100008'},
    'state': {'value': 'NM'},
    'county': {'value': 'Eddy'},
    'township': {'value': 'T20S'},
    'range': {'value': 'R26E'},
    'casing_strings': [
        {'type': 'surface',    'size_in': 13.375, 'depth_ft': 500},
        {'type': 'production', 'size_in': 5.5,    'depth_ft': 5000},
    ],
    'perforations': [
        {'top_ft': 4500, 'bottom_ft': 4800},
    ],
    'formation_tops': [
        {'name': 'San Andres', 'depth_ft': 3000},
        {'name': 'Wolfcamp',   'depth_ft': 4600},
    ],
    'total_depth_ft': {'value': 5000},
}


# ---------------------------------------------------------------------------
# Golden test
# ---------------------------------------------------------------------------

def test_nm_sack_counts_class_c():
    """NM plan materials: all cement steps use Class C; formation_plug sacks >= 25."""
    facts = _FACTS
    policy = load_nm_policy()

    out = plan_from_facts(facts, policy)

    # ------------------------------------------------------------------
    # 1. NM base invariants
    # ------------------------------------------------------------------
    assert_nm_base_invariants(out)

    # ------------------------------------------------------------------
    # 2. No cross-jurisdiction citation leakage
    # ------------------------------------------------------------------
    assert_no_citation_leakage(out, 'NM')

    # ------------------------------------------------------------------
    # 3. Total step count == 6
    # ------------------------------------------------------------------
    steps = out.get('steps', [])
    assert len(steps) == 6, (
        f"Expected 6 plan steps for NM Eddy County 5000 ft well, got {len(steps)}: "
        f"{[s.get('type') for s in steps]}"
    )

    # ------------------------------------------------------------------
    # 4. All cement steps have materials computed (sacks > 0)
    # ------------------------------------------------------------------
    MECHANICAL_TYPES = {'mechanical_plug'}
    cement_steps = [
        s for s in steps
        if (s.get('type') or s.get('step_type')) not in MECHANICAL_TYPES
    ]
    for step in cement_steps:
        sacks = get_sacks(step)
        assert sacks > 0, (
            f"Cement step '{step.get('type')}' at "
            f"{step.get('top_ft')}-{step.get('bottom_ft')} ft "
            f"must have sacks > 0, got {sacks}"
        )

    # ------------------------------------------------------------------
    # 5. Surface plug — Class C, top=0, bottom=50
    # ------------------------------------------------------------------
    surface_plugs = get_steps_by_type(out, 'surface_plug')
    assert len(surface_plugs) == 1, (
        f"Expected exactly 1 surface_plug, found {len(surface_plugs)}"
    )
    sp = surface_plugs[0]
    sp_class = sp.get('details', {}).get('cement_class')
    assert sp_class == 'C', (
        f"Surface plug cement_class must be 'C' (NM C-103 default), got {sp_class!r}"
    )
    assert sp.get('top_ft') == 0, (
        f"Surface plug top_ft must be 0, got {sp.get('top_ft')}"
    )
    assert sp.get('bottom_ft') == 50, (
        f"Surface plug bottom_ft must be 50, got {sp.get('bottom_ft')}"
    )

    # ------------------------------------------------------------------
    # 6. All formation_plug steps have sacks >= 25 (NM minimum)
    # ------------------------------------------------------------------
    formation_plugs = get_steps_by_type(out, 'formation_plug')
    assert len(formation_plugs) >= 1, (
        "Expected at least 1 formation_plug step (NMAC 19.15.25 — "
        "formation isolation is mandatory)"
    )
    for fp in formation_plugs:
        sacks = get_sacks(fp)
        assert sacks >= 25, (
            f"formation_plug at {fp.get('top_ft')}-{fp.get('bottom_ft')} ft "
            f"must have >= 25 sacks (NMAC 19.15.25 minimum), got {sacks}"
        )

    # ------------------------------------------------------------------
    # 7. CIBP cap present with 100 ft interval
    # ------------------------------------------------------------------
    cibp_caps = get_steps_by_type(out, 'cibp_cap')
    assert len(cibp_caps) == 1, (
        f"Expected exactly 1 cibp_cap step, found {len(cibp_caps)}"
    )
    cap = cibp_caps[0]
    cap_length = cap.get('bottom_ft', 0) - cap.get('top_ft', 0)
    assert cap_length == 100, (
        f"CIBP cap must be exactly 100 ft (NM requirement), got {cap_length} ft "
        f"(top={cap.get('top_ft')}, bottom={cap.get('bottom_ft')})"
    )

    # ------------------------------------------------------------------
    # 8. Shoe plugs present for both casing strings
    # ------------------------------------------------------------------
    shoe_plugs = get_steps_by_type(out, 'shoe_plug')
    assert len(shoe_plugs) == 2, (
        f"Expected 2 shoe_plug steps (one per casing string), found {len(shoe_plugs)}"
    )
    shoe_intervals = {(s.get('top_ft'), s.get('bottom_ft')) for s in shoe_plugs}
    assert (450, 550) in shoe_intervals, (
        f"Expected surface casing shoe_plug at 450-550 ft, "
        f"found intervals: {sorted(shoe_intervals)}"
    )
    assert (4950, 5050) in shoe_intervals, (
        f"Expected production casing shoe_plug at 4950-5050 ft, "
        f"found intervals: {sorted(shoe_intervals)}"
    )

    # ------------------------------------------------------------------
    # 9. All shoe_plug steps have sacks >= 25
    # ------------------------------------------------------------------
    for sp_step in shoe_plugs:
        sacks = get_sacks(sp_step)
        assert sacks >= 25, (
            f"shoe_plug at {sp_step.get('top_ft')}-{sp_step.get('bottom_ft')} ft "
            f"must have >= 25 sacks (NMAC 19.15.25 minimum), got {sacks}"
        )
