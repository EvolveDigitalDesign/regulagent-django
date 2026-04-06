"""
Golden test for TX 08A Andrews County — existing CIBP cap scenario.

When cibp_cap_present=True and existing_cibp_cap_length_ft=25 (which meets or
exceeds the 08A overlay's required cap length of 20 ft), the kernel must NOT
generate a new cibp_cap step. All other base steps are still present.

Calibrated output: 4 steps (no cibp_cap generated)
  1. surface_casing_shoe_plug  (tag_required=True)
  2. uqw_isolation_plug        (tag_required=True)
  3. top_plug                  at 0-10 ft
  4. cut_casing_below_surface

Related to:
- TX SWR-14 — 20 ft CIBP cement cap requirement
- 16 Tex. Admin. Code (tx.tac) — plugging rules
"""

from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts
from apps.kernel.tests.test_golden_helpers import (
    get_steps_by_type,
    assert_tx_base_invariants,
)


# ---------------------------------------------------------------------------
# Policy factory
# ---------------------------------------------------------------------------

def _make_policy_08a():
    """Build a stamped 08A Andrews County policy dict."""
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
# Golden test
# ---------------------------------------------------------------------------

def test_existing_cibp_cap_skips_generation():
    """Existing 25 ft CIBP cap meets the 08A requirement — no new cap generated.

    SWR-14 requires a minimum 20 ft cement cap above the bridge plug. When the
    well already has a 25 ft cap (existing_cibp_cap_length_ft=25), the kernel
    must honour it and skip generating a new cibp_cap step.
    """
    facts = {
        'api14': {'value': '4200300009'},
        'state': {'value': 'TX'},
        'district': {'value': '08A'},
        'county': {'value': 'Andrews County'},
        'use_cibp': {'value': True},
        'has_uqw': {'value': False},
        'cibp_cap_present': {'value': True},
        'existing_cibp_cap_length_ft': {'value': 25},
    }
    policy = _make_policy_08a()

    out = plan_from_facts(facts, policy)

    # ------------------------------------------------------------------
    # 1. TX base invariants (jurisdiction='TX', no NMAC citations)
    # ------------------------------------------------------------------
    assert_tx_base_invariants(out)

    # ------------------------------------------------------------------
    # 2. District and jurisdiction correct
    # ------------------------------------------------------------------
    assert out.get('jurisdiction') == 'TX', (
        f"Expected jurisdiction='TX', got {out.get('jurisdiction')!r}"
    )
    assert out.get('district') == '08A', (
        f"Expected district='08A', got {out.get('district')!r}"
    )

    # ------------------------------------------------------------------
    # 3. No cibp_cap step generated (existing cap satisfies requirement)
    # ------------------------------------------------------------------
    cibp_caps = get_steps_by_type(out, 'cibp_cap')
    assert len(cibp_caps) == 0, (
        f"Expected 0 cibp_cap steps (existing 25 ft cap >= 20 ft requirement), "
        f"found {len(cibp_caps)}: {cibp_caps}"
    )

    # ------------------------------------------------------------------
    # 4. Base structural steps still present
    # ------------------------------------------------------------------
    shoe_plugs = get_steps_by_type(out, 'surface_casing_shoe_plug')
    assert len(shoe_plugs) >= 1, (
        "surface_casing_shoe_plug must still be generated when existing CIBP cap "
        "is present (it is unrelated to the cap check)"
    )

    top_plugs = get_steps_by_type(out, 'top_plug')
    assert len(top_plugs) >= 1, (
        "top_plug must still be generated when existing CIBP cap is present"
    )

    cut_casing = get_steps_by_type(out, 'cut_casing_below_surface')
    assert len(cut_casing) >= 1, (
        "cut_casing_below_surface must still be generated when existing CIBP cap "
        "is present"
    )

    # ------------------------------------------------------------------
    # 5. No perf_circulate (no perforations supplied)
    # ------------------------------------------------------------------
    perf_steps = get_steps_by_type(out, 'perf_circulate')
    assert len(perf_steps) == 0, (
        f"No perforations in facts — expected 0 perf_circulate steps, "
        f"found {len(perf_steps)}"
    )

    # ------------------------------------------------------------------
    # 6. Total step count == 4 (shoe, uqw_isolation, top_plug, cut_casing)
    # ------------------------------------------------------------------
    steps = out.get('steps', [])
    assert len(steps) == 4, (
        f"Expected 4 steps when existing CIBP cap present, got {len(steps)}: "
        f"{[s.get('type') for s in steps]}"
    )
