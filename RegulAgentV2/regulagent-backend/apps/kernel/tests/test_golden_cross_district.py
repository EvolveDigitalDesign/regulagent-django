"""
Cross-district golden test — TX 08A (Andrews County) vs TX 7C (Coke County).

Runs identical base facts through both district policies and asserts the
documented district-specific divergences:

  - 08A produces 5 steps; 7C produces 16 steps (11 formation_top_plug added).
  - 7C has formation_top_plug steps derived from the Coke County plugging book;
    08A has none for the same base facts (no formation tops supplied).
  - 7C shoe plug carries "Pump via tubing/drill pipe only" in special_instructions;
    08A shoe plug does not.
  - 08A shoe plug has tag_required=True (Andrews County county overlay).
  - Both plans share surface_casing_shoe_plug, cibp_cap, top_plug,
    cut_casing_below_surface and uqw_isolation_plug.

Related regulations:
  - TX SWR-14, 16 Tex. Admin. Code (tx.tac)
  - 08A district overlay: apps/policy/packs/tx/w3a/district_overlays/08a__auto.yml
  - 7C district overlay: apps/policy/packs/tx/w3a/district_overlays/07c__auto.yml
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

def _make_policy(district: str, county: str) -> dict:
    """Build a stamped TX policy dict for the given district/county."""
    policy = get_effective_policy(district=district, county=county)
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
# Helper — run both districts against identical base facts
# ---------------------------------------------------------------------------

def _run_both():
    """Run plan_from_facts for 08A and 7C with identical base well facts.

    Returns
    -------
    tuple[dict, dict]
        ``(out_08a, out_7c)`` — serialised plan output dicts.
    """
    base_facts = {
        'api14': {'value': '4200300010'},
        'state': {'value': 'TX'},
        'use_cibp': {'value': True},
        'has_uqw': {'value': False},
    }

    facts_08a = {**base_facts, 'district': {'value': '08A'}, 'county': {'value': 'Andrews County'}}
    policy_08a = _make_policy('08A', 'Andrews County')
    out_08a = plan_from_facts(facts_08a, policy_08a)

    facts_7c = {**base_facts, 'district': {'value': '7C'}, 'county': {'value': 'Coke County'}}
    policy_7c = _make_policy('7C', 'Coke County')
    out_7c = plan_from_facts(facts_7c, policy_7c)

    return out_08a, out_7c


# ---------------------------------------------------------------------------
# Golden tests
# ---------------------------------------------------------------------------

def test_same_well_08a_vs_7c():
    """08A and 7C produce the same base steps but diverge in formation_top_plug count.

    Calibrated counts: 08A = 5 steps, 7C = 16 steps.
    7C injects 11 formation_top_plug steps from the Coke County plugging book;
    08A injects none because Andrews County has no formation tops in its overlay.
    """
    out_08a, out_7c = _run_both()

    # --- Both plans satisfy TX base invariants ---
    assert_tx_base_invariants(out_08a)
    assert_tx_base_invariants(out_7c)

    # --- Both plans are TX jurisdiction ---
    assert out_08a.get('jurisdiction') == 'TX', (
        f"08A plan jurisdiction must be 'TX', got {out_08a.get('jurisdiction')!r}"
    )
    assert out_7c.get('jurisdiction') == 'TX', (
        f"7C plan jurisdiction must be 'TX', got {out_7c.get('jurisdiction')!r}"
    )

    # --- District fields correct ---
    assert out_08a.get('district') == '08A', (
        f"Expected out_08a.district='08A', got {out_08a.get('district')!r}"
    )
    assert out_7c.get('district') == '7C', (
        f"Expected out_7c.district='7C', got {out_7c.get('district')!r}"
    )

    # --- Calibrated step counts ---
    steps_08a = out_08a.get('steps', [])
    steps_7c = out_7c.get('steps', [])

    assert len(steps_08a) == 5, (
        f"Expected 5 steps for 08A Andrews County, got {len(steps_08a)}: "
        f"{[s.get('type') for s in steps_08a]}"
    )
    assert len(steps_7c) == 16, (
        f"Expected 16 steps for 7C Coke County, got {len(steps_7c)}: "
        f"{[s.get('type') for s in steps_7c]}"
    )

    # --- 7C has more steps due to formation tops ---
    assert len(steps_7c) > len(steps_08a), (
        f"7C must produce more steps than 08A for the same well facts; "
        f"7C={len(steps_7c)}, 08A={len(steps_08a)}"
    )


def test_7c_has_formation_top_plugs_08a_does_not():
    """7C Coke County injects 11 formation_top_plug steps; 08A Andrews County has none.

    The 7C Coke County overlay loads formation tops from the plugging book
    (07c_plugging_book.json), producing one formation_top_plug per listed
    formation. The 08A Andrews County overlay has no formation tops configured.
    """
    out_08a, out_7c = _run_both()

    ftp_08a = get_steps_by_type(out_08a, 'formation_top_plug')
    ftp_7c = get_steps_by_type(out_7c, 'formation_top_plug')

    assert len(ftp_08a) == 0, (
        f"08A Andrews County must have 0 formation_top_plug steps, "
        f"found {len(ftp_08a)}"
    )
    assert len(ftp_7c) == 11, (
        f"7C Coke County must have 11 formation_top_plug steps (from plugging book), "
        f"found {len(ftp_7c)}"
    )


def test_7c_shoe_has_pump_via_tubing_instruction():
    """7C Coke County shoe plug carries 'Pump via tubing/drill pipe only' instruction.

    The 7C district overlay injects this special instruction into all cement
    steps. 08A does not include this instruction.
    """
    out_08a, out_7c = _run_both()

    shoe_7c = get_steps_by_type(out_7c, 'surface_casing_shoe_plug')
    assert len(shoe_7c) >= 1, (
        "7C plan must contain a surface_casing_shoe_plug step"
    )
    shoe_7c_instructions = shoe_7c[0].get('special_instructions') or ''
    assert 'Pump via tubing/drill pipe only' in shoe_7c_instructions, (
        f"7C Coke County shoe plug must have 'Pump via tubing/drill pipe only' in "
        f"special_instructions, got {shoe_7c_instructions!r}"
    )

    shoe_08a = get_steps_by_type(out_08a, 'surface_casing_shoe_plug')
    assert len(shoe_08a) >= 1, (
        "08A plan must contain a surface_casing_shoe_plug step"
    )
    shoe_08a_instructions = shoe_08a[0].get('special_instructions') or ''
    assert 'Pump via tubing/drill pipe only' not in shoe_08a_instructions, (
        f"08A Andrews County shoe plug must NOT have 'Pump via tubing/drill pipe only' "
        f"in special_instructions, got {shoe_08a_instructions!r}"
    )


def test_08a_shoe_tag_required():
    """08A Andrews County shoe plug has tag_required=True (county overlay requirement).

    Andrews County requires a tagged cement plug at the surface casing shoe.
    This tag requirement is set by the county-level overlay in the 08A YAML.
    """
    out_08a, out_7c = _run_both()

    shoe_08a = get_steps_by_type(out_08a, 'surface_casing_shoe_plug')
    assert len(shoe_08a) >= 1, (
        "08A plan must contain a surface_casing_shoe_plug step"
    )
    tag = shoe_08a[0].get('tag_required')
    assert tag is True, (
        f"08A Andrews County surface_casing_shoe_plug must have tag_required=True, "
        f"got {tag!r}"
    )


def test_both_share_base_step_types():
    """Both 08A and 7C produce the same set of base step types for this well.

    Shared base steps: surface_casing_shoe_plug, cibp_cap, uqw_isolation_plug,
    top_plug, cut_casing_below_surface.
    """
    out_08a, out_7c = _run_both()

    SHARED_TYPES = {
        'surface_casing_shoe_plug',
        'cibp_cap',
        'uqw_isolation_plug',
        'top_plug',
        'cut_casing_below_surface',
    }

    types_08a = {s.get('type') for s in out_08a.get('steps', [])}
    types_7c = {s.get('type') for s in out_7c.get('steps', [])}

    for step_type in SHARED_TYPES:
        assert step_type in types_08a, (
            f"08A plan must contain a '{step_type}' step"
        )
        assert step_type in types_7c, (
            f"7C plan must contain a '{step_type}' step"
        )
