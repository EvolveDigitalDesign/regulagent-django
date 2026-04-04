"""
Golden test: TX 08A Andrews County — three-string well with intermediate casing shoe.

A surface / intermediate / production string well.  The kernel must:
  - Generate an intermediate_casing_shoe_plug at the intermediate shoe depth
    (top=4050, bottom=3950, min_length=100 ft) citing tx.tac.16.3.14(f)(1)
  - Generate a perf_and_circulate_to_surface step with perforation_depth_ft=550
    (50 ft below the 500 ft surface shoe)
  - NOT generate a surface_casing_shoe_plug or top_plug (both are superseded by
    the perf_and_circulate_to_surface step in a 3-string well)

Casing programme:
  - Surface:       13.375" OD / 12.415" ID, shoe @ 500 ft,  TOC 0 ft
  - Intermediate:   9.625" OD /  8.681" ID, shoe @ 4000 ft, TOC 0 ft
  - Production:      5.5"  OD /  4.778" ID, shoe @ 10000 ft, TOC 6000 ft

Regulatory basis: tx.tac.16.3.14(f)(1), tx.tac.16.3.14(e)(2).
"""

from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts
from apps.kernel.tests.test_golden_helpers import (
    get_steps_by_type,
    assert_tx_base_invariants,
)


def test_three_string_well_intermediate_shoe():
    """Three-string well generates intermediate shoe plug + perf_and_circulate_to_surface.

    Verifies that:
    - Exactly 1 intermediate_casing_shoe_plug step is generated
    - Its depth is top=4050, bottom=3950, min_length=100 ft
    - Its regulatory_basis cites tx.tac.16.3.14(f)(1)
    - Exactly 1 perf_and_circulate_to_surface step is generated with
      perforation_depth_ft == 550
    - No surface_casing_shoe_plug step is emitted (removed by perf-to-surface)
    - No top_plug step is emitted (removed by perf-to-surface)
    - All TX base invariants pass
    """
    facts = {
        'api14': {'value': '4200300007'},
        'state': {'value': 'TX'},
        'district': {'value': '08A'},
        'county': {'value': 'Andrews County'},
        'use_cibp': {'value': True},
        'has_uqw': {'value': False},
        'surface_shoe_ft': {'value': 500},
        'intermediate_shoe_ft': {'value': 4000},
        'casing_strings': [
            {
                'name': 'surface_casing',
                'od_in': 13.375,
                'id_in': 12.415,
                'shoe_ft': 500,
                'cement_top_ft': 0,
            },
            {
                'name': 'intermediate_casing',
                'od_in': 9.625,
                'id_in': 8.681,
                'shoe_ft': 4000,
                'cement_top_ft': 0,
            },
            {
                'name': 'production_casing',
                'od_in': 5.5,
                'id_in': 4.778,
                'shoe_ft': 10000,
                'cement_top_ft': 6000,
            },
        ],
    }

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

    out = plan_from_facts(facts, policy)

    # --- TX base invariants -------------------------------------------------
    assert_tx_base_invariants(out)

    steps = out['steps']

    # --- Intermediate casing shoe plug --------------------------------------
    int_shoe_steps = get_steps_by_type(out, 'intermediate_casing_shoe_plug')
    assert len(int_shoe_steps) == 1, (
        f"Expected exactly 1 intermediate_casing_shoe_plug, got {len(int_shoe_steps)}"
    )

    int_shoe = int_shoe_steps[0]
    assert float(int_shoe['top_ft']) == 4050.0, (
        f"intermediate_casing_shoe_plug top_ft: expected 4050.0, got {int_shoe.get('top_ft')}"
    )
    assert float(int_shoe['bottom_ft']) == 3950.0, (
        f"intermediate_casing_shoe_plug bottom_ft: expected 3950.0, got {int_shoe.get('bottom_ft')}"
    )
    assert float(int_shoe['min_length_ft']) == 100.0, (
        f"intermediate_casing_shoe_plug min_length_ft: expected 100.0, "
        f"got {int_shoe.get('min_length_ft')}"
    )

    # Regulatory citation check
    basis = int_shoe.get('regulatory_basis') or ''
    if isinstance(basis, list):
        basis = ' '.join(str(b) for b in basis)
    assert 'tx.tac.16.3.14(f)(1)' in str(basis), (
        f"intermediate_casing_shoe_plug regulatory_basis must contain "
        f"'tx.tac.16.3.14(f)(1)', got: {basis!r}"
    )

    # --- Perf and circulate to surface --------------------------------------
    pac_steps = get_steps_by_type(out, 'perf_and_circulate_to_surface')
    assert len(pac_steps) == 1, (
        f"Expected exactly 1 perf_and_circulate_to_surface step, got {len(pac_steps)}"
    )

    pac = pac_steps[0]
    assert float(pac['perforation_depth_ft']) == 550.0, (
        f"perf_and_circulate_to_surface perforation_depth_ft: expected 550.0, "
        f"got {pac.get('perforation_depth_ft')}"
    )

    # --- Steps removed by perf_and_circulate_to_surface --------------------
    # No surface_casing_shoe_plug (superseded)
    surf_shoe_steps = get_steps_by_type(out, 'surface_casing_shoe_plug')
    assert len(surf_shoe_steps) == 0, (
        f"surface_casing_shoe_plug should be absent in 3-string well "
        f"(superseded by perf_and_circulate_to_surface), found {len(surf_shoe_steps)}"
    )

    # No top_plug (superseded)
    top_plug_steps = get_steps_by_type(out, 'top_plug')
    assert len(top_plug_steps) == 0, (
        f"top_plug should be absent in 3-string well "
        f"(superseded by perf_and_circulate_to_surface), found {len(top_plug_steps)}"
    )
