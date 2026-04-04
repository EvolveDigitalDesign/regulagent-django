"""
Golden test: TX 08A Andrews County — multiple perforation zones.

Tests the kernel's handling of three perf_circulate overrides interleaved
with three cement_plug overrides, plus a productive_horizon_isolation_plug
generated from the producing_interval_ft fact.

Regulatory basis: SWR-14 (Texas W-3A plugging plan requirements).
"""

from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts
from apps.kernel.tests.test_golden_helpers import (
    get_steps_by_type,
    assert_tx_base_invariants,
)


def test_three_perf_zones():
    """Three stacked perf intervals with separating cement plugs.

    Verifies that the kernel:
    - Emits exactly 3 perf_circulate steps (sacks == 0 each)
    - Emits exactly 3 cement_plug steps (sacks > 0 each, 9 sacks at 0.4 excess)
    - Generates a productive_horizon_isolation_plug from producing_interval_ft
    - Passes all TX base invariants (jurisdiction, no NM citations)
    """
    facts = {
        'api14': {'value': '4200300005'},
        'state': {'value': 'TX'},
        'district': {'value': '08A'},
        'county': {'value': 'Andrews County'},
        'use_cibp': {'value': True},
        'has_uqw': {'value': False},
        'producing_interval_ft': {'value': [4000, 4500]},
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

    # Three perf zones (deep → shallow) and one cement plug between each
    policy.setdefault('effective', {}).setdefault('steps_overrides', {})
    eff = policy['effective']['steps_overrides']
    eff['perf_circulate'] = [
        {'top_ft': 8000, 'bottom_ft': 8500, 'citations': ['SWR-14']},
        {'top_ft': 6000, 'bottom_ft': 6500, 'citations': ['SWR-14']},
        {'top_ft': 4000, 'bottom_ft': 4500, 'citations': ['SWR-14']},
    ]
    eff['cement_plugs'] = [
        {
            'top_ft': 7500, 'bottom_ft': 7400,
            'geometry_context': 'cased_production',
            'casing_id_in': 4.778, 'stinger_od_in': 2.875, 'annular_excess': 0.4,
            'citations': ['SWR-14'],
        },
        {
            'top_ft': 5500, 'bottom_ft': 5400,
            'geometry_context': 'cased_production',
            'casing_id_in': 4.778, 'stinger_od_in': 2.875, 'annular_excess': 0.4,
            'citations': ['SWR-14'],
        },
        {
            'top_ft': 3500, 'bottom_ft': 3400,
            'geometry_context': 'cased_production',
            'casing_id_in': 4.778, 'stinger_od_in': 2.875, 'annular_excess': 0.4,
            'citations': ['SWR-14'],
        },
    ]

    out = plan_from_facts(facts, policy)

    # --- TX base invariants -------------------------------------------------
    assert_tx_base_invariants(out)

    steps = out['steps']

    # --- Perf_circulate steps -----------------------------------------------
    perfs = get_steps_by_type(out, 'perf_circulate')
    assert len(perfs) == 3, f"Expected 3 perf_circulate steps, got {len(perfs)}"

    expected_perfs = [
        (8000, 8500),
        (6000, 6500),
        (4000, 4500),
    ]
    for i, (exp_top, exp_bot) in enumerate(expected_perfs):
        s = perfs[i]
        assert int(s['top_ft']) == exp_top, (
            f"perf {i} top_ft: expected {exp_top}, got {s.get('top_ft')}"
        )
        assert int(s['bottom_ft']) == exp_bot, (
            f"perf {i} bottom_ft: expected {exp_bot}, got {s.get('bottom_ft')}"
        )
        sacks = (s.get('materials') or {}).get('slurry', {}).get('sacks') or 0
        assert sacks == 0, (
            f"perf_circulate step {i} should have 0 sacks, got {sacks}"
        )

    # --- Cement_plug steps --------------------------------------------------
    plugs = get_steps_by_type(out, 'cement_plug')
    assert len(plugs) == 3, f"Expected 3 cement_plug steps, got {len(plugs)}"

    expected_plugs = [
        (7500, 7400, 9),
        (5500, 5400, 9),
        (3500, 3400, 9),
    ]
    for i, (exp_top, exp_bot, exp_sacks) in enumerate(expected_plugs):
        s = plugs[i]
        assert int(s['top_ft']) == exp_top, (
            f"plug {i} top_ft: expected {exp_top}, got {s.get('top_ft')}"
        )
        assert int(s['bottom_ft']) == exp_bot, (
            f"plug {i} bottom_ft: expected {exp_bot}, got {s.get('bottom_ft')}"
        )
        sacks = (s.get('materials') or {}).get('slurry', {}).get('sacks') or 0
        assert sacks > 0, f"cement_plug step {i} must have sacks > 0, got {sacks}"
        assert sacks == exp_sacks, (
            f"cement_plug step {i} sacks: expected {exp_sacks}, got {sacks}"
        )

    # --- Productive horizon isolation plug ----------------------------------
    phip_steps = get_steps_by_type(out, 'productive_horizon_isolation_plug')
    assert len(phip_steps) >= 1, (
        "Expected at least 1 productive_horizon_isolation_plug from producing_interval_ft"
    )
