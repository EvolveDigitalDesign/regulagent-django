"""
Golden test for TX 08A Andrews County — no perforations, no CIBP.

Verifies that when use_cibp=False and has_uqw=False, the kernel generates only
the minimal required structural steps without any CIBP-related or perforation-
related steps.

Calibrated output: 4 steps
  1. surface_casing_shoe_plug  (tag_required=True — Andrews County overlay)
  2. uqw_isolation_plug        (tag_required=True)
  3. top_plug                  at 0-10 ft
  4. cut_casing_below_surface

No cibp_cap, no perf_circulate, no cement_plug overrides.

Related to:
- TX SWR-14, 16 Tex. Admin. Code (tx.tac)
- 08A district overlay: Andrews County county-level tag requirement
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
    """Build a stamped 08A Andrews County policy dict (no steps_overrides)."""
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

def test_no_perfs_no_cibp():
    """No CIBP and no perforations produce only the 4 minimal structural steps.

    With use_cibp=False, the kernel omits the CIBP mechanical plug and the
    cement cap above it. No perf_circulate or cement_plug overrides are
    provided, so neither of those step types appears in the output.
    """
    facts = {
        'api14': {'value': '4200300011'},
        'state': {'value': 'TX'},
        'district': {'value': '08A'},
        'county': {'value': 'Andrews County'},
        'use_cibp': {'value': False},
        'has_uqw': {'value': False},
    }
    policy = _make_policy_08a()
    # No steps_overrides added to policy — bare structural run.

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
    # 3. No cibp_cap generated (use_cibp=False)
    # ------------------------------------------------------------------
    cibp_caps = get_steps_by_type(out, 'cibp_cap')
    assert len(cibp_caps) == 0, (
        f"Expected 0 cibp_cap steps when use_cibp=False, found {len(cibp_caps)}"
    )

    # ------------------------------------------------------------------
    # 4. No perf_circulate generated (no perforations supplied)
    # ------------------------------------------------------------------
    perf_steps = get_steps_by_type(out, 'perf_circulate')
    assert len(perf_steps) == 0, (
        f"Expected 0 perf_circulate steps when no perforations, "
        f"found {len(perf_steps)}"
    )

    # ------------------------------------------------------------------
    # 5. Base structural steps still present
    # ------------------------------------------------------------------
    shoe_plugs = get_steps_by_type(out, 'surface_casing_shoe_plug')
    assert len(shoe_plugs) >= 1, (
        "surface_casing_shoe_plug must be generated even when use_cibp=False"
    )

    top_plugs = get_steps_by_type(out, 'top_plug')
    assert len(top_plugs) >= 1, (
        "top_plug must be generated even when use_cibp=False"
    )

    cut_casing = get_steps_by_type(out, 'cut_casing_below_surface')
    assert len(cut_casing) >= 1, (
        "cut_casing_below_surface must be generated even when use_cibp=False"
    )

    # ------------------------------------------------------------------
    # 6. Total step count == 4 (shoe, uqw_isolation, top_plug, cut_casing)
    # ------------------------------------------------------------------
    steps = out.get('steps', [])
    assert len(steps) == 4, (
        f"Expected 4 steps when use_cibp=False and no perforations, "
        f"got {len(steps)}: {[s.get('type') for s in steps]}"
    )

    # ------------------------------------------------------------------
    # 7. No cement_plug steps (no steps_overrides provided)
    # ------------------------------------------------------------------
    cement_plugs = get_steps_by_type(out, 'cement_plug')
    assert len(cement_plugs) == 0, (
        f"Expected 0 cement_plug steps (no overrides provided), "
        f"found {len(cement_plugs)}"
    )

    # ------------------------------------------------------------------
    # 8. top_plug at expected depth (0-10 ft)
    # ------------------------------------------------------------------
    top_plug = top_plugs[0]
    assert top_plug.get('top_ft') == 0, (
        f"top_plug top_ft should be 0, got {top_plug.get('top_ft')}"
    )
    assert top_plug.get('bottom_ft') == 10, (
        f"top_plug bottom_ft should be 10, got {top_plug.get('bottom_ft')}"
    )
