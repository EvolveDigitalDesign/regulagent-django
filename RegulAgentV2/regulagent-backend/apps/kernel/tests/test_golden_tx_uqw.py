"""
Golden tests for TX UQW (Usable Quality Water) isolation plugs.

Tests the ``has_uqw`` / ``uqw_base_ft`` code path in ``w3a_rules.generate_steps()``:
- When ``has_uqw=True`` and no GAU interval is present, the kernel generates a
  ``uqw_isolation_plug`` step centered on ``uqw_base_ft`` ± 50 ft.
- The step is tagged with ``tx.tac.16.3.14(g)(1)`` in ``regulatory_basis``.
- When no ``gau_protect_intervals`` are provided, no GAU-derived cement_plug
  appears (i.e., no plug with "GAU" in its placement_basis).

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

def test_uqw_isolation_plug_08a():
    """UQW base at 500 ft generates a uqw_isolation_plug at 550–450 ft (±50 ft).

    With no GAU intervals present, a dedicated uqw_isolation_plug step is emitted
    and no GAU-tagged cement_plug appears.
    """
    facts = {
        'api14': {'value': '4200300003'},
        'state': {'value': 'TX'},
        'district': {'value': '08A'},
        'county': {'value': 'Andrews County'},
        'use_cibp': {'value': True},
        'has_uqw': {'value': True},
        'uqw_base_ft': {'value': 500},
    }

    out = plan_from_facts(facts, _make_policy())

    # TX jurisdiction invariants
    assert_tx_base_invariants(out)

    # Exactly one uqw_isolation_plug step
    uqw_steps = get_steps_by_type(out, 'uqw_isolation_plug')
    assert len(uqw_steps) == 1, (
        f"Expected exactly 1 uqw_isolation_plug step, found {len(uqw_steps)}"
    )

    uqw_step = uqw_steps[0]

    # Step must be centered on uqw_base_ft ± 50 ft → top=550, bottom=450
    assert uqw_step.get('top_ft') == 550.0, (
        f"Expected uqw_isolation_plug top_ft=550, got {uqw_step.get('top_ft')}"
    )
    assert uqw_step.get('bottom_ft') == 450.0, (
        f"Expected uqw_isolation_plug bottom_ft=450, got {uqw_step.get('bottom_ft')}"
    )

    # Minimum plug length must be at least 100 ft
    min_len = uqw_step.get('min_length_ft')
    assert min_len is not None and float(min_len) >= 100.0, (
        f"Expected min_length_ft >= 100, got {min_len}"
    )

    # regulatory_basis must cite the UQW TAC requirement
    reg_basis = uqw_step.get('regulatory_basis') or ''
    if isinstance(reg_basis, list):
        reg_basis_str = ' '.join(str(b) for b in reg_basis)
    else:
        reg_basis_str = str(reg_basis)
    assert 'tx.tac.16.3.14(g)(1)' in reg_basis_str, (
        f"Expected 'tx.tac.16.3.14(g)(1)' in regulatory_basis, "
        f"got: {uqw_step.get('regulatory_basis')!r}"
    )

    # No GAU-tagged cement_plug should appear (no gau_protect_intervals in facts)
    cement_plugs = get_steps_by_type(out, 'cement_plug')
    gau_cement_plugs = [
        s for s in cement_plugs
        if 'GAU' in (s.get('placement_basis') or '')
    ]
    assert len(gau_cement_plugs) == 0, (
        f"Expected no GAU-derived cement_plug steps, found {len(gau_cement_plugs)}: "
        f"{[s.get('placement_basis') for s in gau_cement_plugs]}"
    )
