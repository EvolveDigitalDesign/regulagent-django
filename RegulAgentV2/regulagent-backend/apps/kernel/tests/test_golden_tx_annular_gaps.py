"""
Golden tests for TX annular-gap isolation plugs.

Tests the ``annular_gaps`` code path in ``w3a_rules.generate_steps()``:
- An annular gap with ``requires_isolation=True`` (top=3000, bottom=4000 ft)
  generates a plug step centered at the gap midpoint.
- When no existing casing-string depth data (``bottom_ft``/``shoe_depth_ft``)
  is available to ``_get_casing_strings_at_depth()``, the function treats the
  interval as open-hole and ``_requires_perforation_at_depth()`` returns False.
  This produces a plain ``cement_plug`` (not ``perforate_and_squeeze_plug``).
- The step carries ``details.annular_gap_covered`` with the original gap bounds.
- placement_basis identifies both the outer and inner casing strings.

No DB access required — policy is built inline via ``get_effective_policy()``.
"""

from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts
from apps.kernel.tests.test_golden_helpers import get_steps_by_type, assert_tx_base_invariants


# ---------------------------------------------------------------------------
# Shared policy factory
# ---------------------------------------------------------------------------

def _make_policy():
    policy = get_effective_policy(district='08A', county='Andrews County')
    policy['policy_id'] = 'tx.w3a'
    policy['complete'] = True
    policy['preferences'] = {
        'default_recipe': {
            'id': 'class_h_neat_15_8',
            'class': 'H',
            'density_ppg': 15.8,
            'yield_ft3_per_sk': 1.18,
            'water_gal_per_sk': 5.2,
            'additives': [],
        },
        'geometry_defaults': {
            'cement_plug': {
                'casing_id_in': 4.778,
                'stinger_od_in': 2.875,
                'annular_excess': 0.4,
            },
            'squeeze': {
                'casing_id_in': 4.778,
                'stinger_od_in': 2.875,
                'squeeze_factor': 1.5,
                'annular_excess': 0.4,
            },
            'cibp_cap': {
                'casing_id_in': 4.778,
                'stinger_od_in': 2.875,
                'annular_excess': 0.4,
            },
        },
    }
    return policy


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_annular_gap_cement_plug():
    """Annular gap (3000–4000 ft) generates a cement_plug at 3550–3450 ft.

    The fixture uses ``shoe_ft`` keys in ``casing_strings`` which are not
    recognised by ``_get_casing_strings_at_depth()`` (expects ``bottom_ft`` /
    ``shoe_depth_ft``).  The function therefore returns ``open_hole`` context,
    so ``_requires_perforation_at_depth()`` returns False and the kernel emits
    a standard ``cement_plug`` rather than a ``perforate_and_squeeze_plug``.

    Calibration:
    - gap midpoint  = 3000 + (4000 - 3000) / 2 = 3500 ft
    - plug length   = min(1000, 100) = 100 ft
    - plug_top      = 3500 + 50 = 3550 ft
    - plug_bottom   = 3500 - 50 = 3450 ft
    - sacks         >= 1 (TX 25-sack minimum applied by kernel)
    """
    facts = {
        'api14': {'value': '4200300004'},
        'state': {'value': 'TX'},
        'district': {'value': '08A'},
        'county': {'value': 'Andrews County'},
        'use_cibp': {'value': True},
        'has_uqw': {'value': False},
        'annular_gaps': [
            {
                'top_md_ft': 3000,
                'bottom_md_ft': 4000,
                'requires_isolation': True,
                'outer_string': 'surface_casing',
                'inner_string': 'production_casing',
            },
        ],
        'casing_strings': [
            {
                'name': 'surface_casing',
                'od_in': 8.625,
                'id_in': 8.097,
                'shoe_ft': 1500,
                'cement_top_ft': 0,
            },
            {
                'name': 'production_casing',
                'od_in': 5.5,
                'id_in': 4.778,
                'shoe_ft': 10000,
                'cement_top_ft': 5000,
            },
        ],
    }

    out = plan_from_facts(facts, _make_policy())

    # TX jurisdiction invariants
    assert_tx_base_invariants(out)

    # At least one step must be generated from the annular gap
    all_steps = out.get('steps', [])
    gap_steps = [
        s for s in all_steps
        if (s.get('details') or {}).get('annular_gap_covered') is not None
    ]
    assert len(gap_steps) >= 1, (
        f"Expected at least 1 step with details.annular_gap_covered, "
        f"found none among step types: {[s.get('type') for s in all_steps]}"
    )

    # The gap-derived step must be a cement_plug (not perforate_and_squeeze)
    gap_step = gap_steps[0]
    assert gap_step.get('type') == 'cement_plug', (
        f"Expected gap step type='cement_plug', got {gap_step.get('type')!r}"
    )

    # details.annular_gap_covered must reference the original gap bounds
    covered = gap_step['details']['annular_gap_covered']
    assert covered['top_ft'] == 3000, (
        f"Expected annular_gap_covered.top_ft=3000, got {covered.get('top_ft')}"
    )
    assert covered['bottom_ft'] == 4000, (
        f"Expected annular_gap_covered.bottom_ft=4000, got {covered.get('bottom_ft')}"
    )

    # Plug must be centered in the gap: top=3550, bottom=3450
    assert gap_step.get('top_ft') == 3550.0, (
        f"Expected plug top_ft=3550 (gap midpoint+50), got {gap_step.get('top_ft')}"
    )
    assert gap_step.get('bottom_ft') == 3450.0, (
        f"Expected plug bottom_ft=3450 (gap midpoint-50), got {gap_step.get('bottom_ft')}"
    )

    # placement_basis must identify the casing strings
    pb = gap_step.get('placement_basis') or ''
    assert 'Annular gap' in pb, (
        f"Expected 'Annular gap' in placement_basis, got: {pb!r}"
    )
    assert 'surface_casing' in pb, (
        f"Expected 'surface_casing' in placement_basis, got: {pb!r}"
    )
    assert 'production_casing' in pb, (
        f"Expected 'production_casing' in placement_basis, got: {pb!r}"
    )

    # Materials must be computed (TX minimum = 25 sacks)
    sacks = (gap_step.get('materials') or {}).get('slurry', {}).get('sacks')
    assert sacks is not None and float(sacks) >= 1, (
        f"Expected sacks >= 1 in materials.slurry.sacks, got {sacks}"
    )
