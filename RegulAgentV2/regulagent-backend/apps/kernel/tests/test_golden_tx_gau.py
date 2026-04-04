"""
Golden tests for TX GAU (Groundwater Advisory Unit) protect-interval plugs.

Tests the ``gau_protect_intervals`` code path in ``w3a_rules.generate_steps()``:
- A GAU interval from surface (bottom_ft=0) generates a ``cement_plug`` step
  tagged with ``tx.gau.protect_interval`` in ``regulatory_basis``.
- When ``has_uqw=True`` and the GAU interval starts at the surface, the single
  plug satisfies both the GAU and UQW isolation requirements.  No separate
  ``uqw_isolation_plug`` step is emitted in that case.

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

def test_gau_protect_interval_basic():
    """GAU interval 800–0 ft generates a cement_plug tagged tx.gau.protect_interval."""
    facts = {
        'api14': {'value': '4200300001'},
        'state': {'value': 'TX'},
        'district': {'value': '08A'},
        'county': {'value': 'Andrews County'},
        'use_cibp': {'value': True},
        'has_uqw': {'value': False},
        'gau_protect_intervals': [
            {'top_ft': 800, 'bottom_ft': 0, 'formation': 'Ogallala'},
        ],
    }

    out = plan_from_facts(facts, _make_policy())

    # TX jurisdiction invariants
    assert_tx_base_invariants(out)

    # Exactly one cement_plug step covering the GAU interval
    cement_plugs = get_steps_by_type(out, 'cement_plug')
    gau_plugs = [
        s for s in cement_plugs
        if s.get('top_ft') == 800.0 and s.get('bottom_ft') == 0.0
    ]
    assert len(gau_plugs) == 1, (
        f"Expected exactly 1 GAU cement_plug at 800–0 ft, "
        f"found {len(gau_plugs)} among {[(s.get('top_ft'), s.get('bottom_ft')) for s in cement_plugs]}"
    )

    gau_step = gau_plugs[0]

    # regulatory_basis must reference the GAU citation
    reg_basis = gau_step.get('regulatory_basis') or ''
    if isinstance(reg_basis, list):
        reg_basis_str = ' '.join(str(b) for b in reg_basis)
    else:
        reg_basis_str = str(reg_basis)
    assert 'tx.gau.protect_interval' in reg_basis_str, (
        f"Expected 'tx.gau.protect_interval' in regulatory_basis, got: {gau_step.get('regulatory_basis')!r}"
    )

    # placement_basis must identify the GAU interval
    pb = gau_step.get('placement_basis') or ''
    assert 'GAU protect interval' in pb, (
        f"Expected 'GAU protect interval' in placement_basis, got: {pb!r}"
    )

    # Materials calculated — expect 118 sacks (calibrated value for 800 ft cased plug)
    sacks = (gau_step.get('materials') or {}).get('slurry', {}).get('sacks')
    assert sacks is not None, "Expected sacks to be computed in materials.slurry.sacks"
    assert int(sacks) == 118, (
        f"Expected 118 sacks for GAU 800 ft plug, got {sacks}"
    )


def test_gau_satisfies_uqw():
    """GAU interval from surface (0 ft) with has_uqw=True satisfies UQW isolation.

    A single cement_plug is generated tagged with both tx.gau.protect_interval and
    tx.tac.16.3.14(g)(1).  No separate uqw_isolation_plug step should appear.
    """
    facts = {
        'api14': {'value': '4200300002'},
        'state': {'value': 'TX'},
        'district': {'value': '08A'},
        'county': {'value': 'Andrews County'},
        'use_cibp': {'value': True},
        'has_uqw': {'value': True},
        'uqw_base_ft': {'value': 600},
        'gau_protect_intervals': [
            {'top_ft': 800, 'bottom_ft': 0, 'formation': 'Ogallala'},
        ],
    }

    out = plan_from_facts(facts, _make_policy())

    # TX jurisdiction invariants
    assert_tx_base_invariants(out)

    # Exactly one cement_plug covering the GAU interval
    cement_plugs = get_steps_by_type(out, 'cement_plug')
    gau_plugs = [
        s for s in cement_plugs
        if s.get('top_ft') == 800.0 and s.get('bottom_ft') == 0.0
    ]
    assert len(gau_plugs) == 1, (
        f"Expected exactly 1 GAU cement_plug at 800–0 ft, "
        f"found {len(gau_plugs)} among {[(s.get('top_ft'), s.get('bottom_ft')) for s in cement_plugs]}"
    )

    gau_step = gau_plugs[0]

    # placement_basis must mention that UQW isolation is satisfied
    pb = gau_step.get('placement_basis') or ''
    assert 'satisfies UQW isolation' in pb, (
        f"Expected 'satisfies UQW isolation' substring in placement_basis, got: {pb!r}"
    )

    # regulatory_basis must include BOTH GAU citation AND UQW citation
    reg_basis = gau_step.get('regulatory_basis') or ''
    if isinstance(reg_basis, list):
        reg_basis_str = ' '.join(str(b) for b in reg_basis)
    else:
        reg_basis_str = str(reg_basis)

    assert 'tx.gau.protect_interval' in reg_basis_str, (
        f"Expected 'tx.gau.protect_interval' in regulatory_basis, got: {gau_step.get('regulatory_basis')!r}"
    )
    assert 'tx.tac.16.3.14(g)(1)' in reg_basis_str, (
        f"Expected 'tx.tac.16.3.14(g)(1)' in regulatory_basis, got: {gau_step.get('regulatory_basis')!r}"
    )

    # No separate uqw_isolation_plug should be generated
    uqw_plugs = get_steps_by_type(out, 'uqw_isolation_plug')
    assert len(uqw_plugs) == 0, (
        f"Expected 0 uqw_isolation_plug steps (GAU satisfies UQW), found {len(uqw_plugs)}"
    )
