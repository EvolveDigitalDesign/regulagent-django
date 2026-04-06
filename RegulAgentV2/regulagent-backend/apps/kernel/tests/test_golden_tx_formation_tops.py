"""
Golden test: TX 7C Coke County — formation top plugs from district overlay.

The 7C district overlay for Coke County injects 11 formation_top_plug steps
automatically (Canyon, Coleman Junction, Problem Zone, Capps Lime, Gardner,
Gray, Cisco, Harris, Strawn, Palo Pinto, Cisco Sd.).  All of these must
satisfy the Texas 25-sack minimum; none have perf_circulate overrides in this
fixture so the only override-driven step is the productive_horizon_isolation_plug
from producing_interval_ft.

Regulatory basis: tx.tac.16.3.14, rrc.district.7c.coke county.
"""

from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts
from apps.kernel.tests.test_golden_helpers import (
    get_steps_by_type,
    get_sacks,
    assert_tx_base_invariants,
)


# Formation plugs injected by the 7C Coke County district overlay.
# Each tuple is (formation_name_fragment_in_regulatory_basis, top_ft, bottom_ft, sacks).
_EXPECTED_FORMATION_PLUGS = [
    ('Canyon',           4950.0, 4850.0, 25),
    ('Coleman Junction', 2500.0, 2300.0, 25),
    ('Problem Zone',     2000.0, 1900.0, 25),
    ('Capps Lime',       4500.0, 4400.0, 25),
    ('Gardner',          5200.0, 5100.0, 25),
    ('Gray',             5000.0, 4900.0, 25),
    ('Cisco',            3400.0, 3300.0, 25),
    ('Harris',           5500.0, 5400.0, 25),
    ('Strawn',           5700.0, 5600.0, 25),
    ('Palo Pinto',       4900.0, 4800.0, 25),
    ('Cisco Sd.',        3700.0, 3600.0, 25),
]


def test_formation_top_plugs_7c():
    """7C Coke County district overlay generates 11 formation_top_plug steps.

    Verifies that:
    - All 11 overlay-driven formation_top_plug steps are present with correct
      depths and the Texas 25-sack minimum
    - The surface_casing_shoe_plug carries the 7C operational instruction
      "Pump via tubing/drill pipe only"
    - A productive_horizon_isolation_plug is generated from producing_interval_ft
    - All TX base invariants pass (jurisdiction, no NM citations)
    """
    facts = {
        'api14': {'value': '4200000006'},
        'state': {'value': 'TX'},
        'district': {'value': '7C'},
        'county': {'value': 'Coke County'},
        'use_cibp': {'value': True},
        'has_uqw': {'value': False},
        'producing_interval_ft': {'value': [9000, 9500]},
        'formation_tops_map': {
            'San Angelo': {'depth_ft': 2000, 'plug_required': True},
            'Clear Fork':  {'depth_ft': 5000, 'plug_required': True},
        },
    }

    policy = get_effective_policy(district='7C', county='Coke County')
    policy['policy_id'] = 'tx.w3a'
    policy['complete'] = True
    policy.setdefault('preferences', {})['rounding_policy'] = 'nearest'
    policy['preferences'].update({
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
    })

    out = plan_from_facts(facts, policy)

    # --- TX base invariants -------------------------------------------------
    assert_tx_base_invariants(out)

    steps = out['steps']

    # --- Surface casing shoe: 7C operational instruction -------------------
    shoe = next(
        (s for s in steps if s.get('type') == 'surface_casing_shoe_plug'), None
    )
    assert shoe is not None, "Expected a surface_casing_shoe_plug step"
    instr = shoe.get('special_instructions', '') or ''
    assert 'Pump via tubing/drill pipe only' in instr, (
        f"surface_casing_shoe_plug missing '7C pump instruction'; "
        f"got special_instructions={instr!r}"
    )

    # --- Formation top plugs ------------------------------------------------
    ft_steps = get_steps_by_type(out, 'formation_top_plug')
    assert len(ft_steps) == len(_EXPECTED_FORMATION_PLUGS), (
        f"Expected {len(_EXPECTED_FORMATION_PLUGS)} formation_top_plug steps, "
        f"got {len(ft_steps)}"
    )

    # Build a lookup keyed by (top_ft, bottom_ft) for O(1) matching
    ft_by_interval = {
        (float(s['top_ft']), float(s['bottom_ft'])): s
        for s in ft_steps
    }

    mismatches = []
    for name, top, bot, exp_sacks in _EXPECTED_FORMATION_PLUGS:
        key = (top, bot)
        s = ft_by_interval.get(key)
        if s is None:
            mismatches.append(
                f"{name}: no step at interval {top}–{bot} ft"
            )
            continue

        actual_sacks = int(get_sacks(s))
        if actual_sacks != exp_sacks:
            mismatches.append(
                f"{name} ({top}–{bot} ft): sacks {actual_sacks} != {exp_sacks}"
            )

        # Each formation_top_plug must have sacks >= 25 (TX minimum)
        if actual_sacks < 25:
            mismatches.append(
                f"{name}: sacks {actual_sacks} < 25 (TX 25-sack minimum violated)"
            )

    assert not mismatches, (
        "7C formation_top_plug golden mismatches:\n" + "\n".join(mismatches)
    )

    # All formation_top_plug steps must have sacks >= 1
    for s in ft_steps:
        assert get_sacks(s) >= 1, (
            f"formation_top_plug at {s.get('top_ft')}–{s.get('bottom_ft')} ft "
            f"has 0 sacks"
        )

    # --- Productive horizon isolation plug ----------------------------------
    phip_steps = get_steps_by_type(out, 'productive_horizon_isolation_plug')
    assert len(phip_steps) >= 1, (
        "Expected at least 1 productive_horizon_isolation_plug from producing_interval_ft"
    )
    # Calibrated: top=8950, bottom=9000 for producing_interval_ft=[9000, 9500]
    phip = phip_steps[0]
    assert float(phip['top_ft']) == 8950.0, (
        f"productive_horizon_isolation_plug top_ft: expected 8950.0, got {phip.get('top_ft')}"
    )
    assert float(phip['bottom_ft']) == 9000.0, (
        f"productive_horizon_isolation_plug bottom_ft: expected 9000.0, got {phip.get('bottom_ft')}"
    )
