"""
Cross-jurisdiction isolation golden test.

Runs an IDENTICAL well geometry through both TX (tx.w3a) and NM (nm.c103)
planning paths and asserts that the outputs diverge in the documented ways:

  - TX produces 4 steps; NM produces 9 steps.
  - TX CIBP cap is 20 ft; NM CIBP cap is >= 100 ft (>= 5x TX).
  - TX step-type vocabulary differs from NM step-type vocabulary.
  - TX citations reference SWR/tx.tac only; NM citations reference NMAC only.
  - NM plan always contains at least one formation_plug step.

Related regulations:
  - TX: SWR-14, 16 Tex. Admin. Code (tx.tac)
  - NM: NMAC 19.15.25 (Well Plugging and Abandonment), C-103 form
"""

import pytest

from apps.kernel.services.policy_kernel import plan_from_facts
from apps.policy.services.loader import get_effective_policy
from apps.kernel.tests.test_golden_helpers import (
    load_nm_policy,
    assert_nm_base_invariants,
    assert_tx_base_invariants,
    assert_no_citation_leakage,
    get_steps_by_type,
)


# ---------------------------------------------------------------------------
# Shared well geometry — identical for both jurisdictions
# ---------------------------------------------------------------------------

SHARED_CASING = [
    {'type': 'surface', 'size_in': 13.375, 'depth_ft': 500},
    {'type': 'production', 'size_in': 5.5, 'depth_ft': 8000},
]
SHARED_PERFS = [{'top_ft': 7000, 'bottom_ft': 7500}]
SHARED_FORMATIONS = [
    {'name': 'San Andres', 'depth_ft': 3500},
    {'name': 'Wolfcamp', 'depth_ft': 7200},
]


# ---------------------------------------------------------------------------
# Helper — run both jurisdictions against shared geometry
# ---------------------------------------------------------------------------

def _run_both():
    """Build facts/policies for TX and NM, run plan_from_facts, return both outputs.

    Returns
    -------
    tuple[dict, dict]
        ``(tx_out, nm_out)`` — serialised plan output dicts.
    """
    # --- TX facts and policy ------------------------------------------------
    tx_facts = {
        'api14': {'value': '42000099999'},
        'state': {'value': 'TX'},
        'district': {'value': '08A'},
        'county': {'value': 'Andrews County'},
        'use_cibp': {'value': True},
        'casing_strings': SHARED_CASING,
        'perforations': SHARED_PERFS,
        'formation_tops': SHARED_FORMATIONS,
        'total_depth_ft': {'value': 8000},
    }

    tx_policy = get_effective_policy(district='08A', county='Andrews County')
    tx_policy['policy_id'] = 'tx.w3a'
    tx_policy['complete'] = True
    tx_policy['preferences'] = {
        'rounding_policy': 'nearest',
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

    # --- NM facts and policy ------------------------------------------------
    nm_facts = {
        'api14': {'value': '30-015-99998'},
        'state': {'value': 'NM'},
        'county': {'value': 'Eddy'},
        'township': {'value': 'T20S'},
        'range': {'value': 'R26E'},
        'casing_strings': SHARED_CASING,
        'perforations': SHARED_PERFS,
        'formation_tops': SHARED_FORMATIONS,
        'total_depth_ft': {'value': 8000},
    }
    nm_policy = load_nm_policy()

    # --- Execute -------------------------------------------------------------
    tx_out = plan_from_facts(tx_facts, tx_policy)
    nm_out = plan_from_facts(nm_facts, nm_policy)

    return tx_out, nm_out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_same_well_different_cibp_cap():
    """TX CIBP cap is 20 ft; NM CIBP cap is >= 100 ft and >= 5x TX cap length.

    TX SWR-14 specifies a 20 ft cap above the mechanical bridge plug.
    NMAC 19.15.25 requires a minimum 100 ft cement cap above the CIBP.
    """
    tx_out, nm_out = _run_both()

    tx_caps = get_steps_by_type(tx_out, 'cibp_cap')
    assert len(tx_caps) == 1, (
        f"Expected exactly 1 TX cibp_cap step, found {len(tx_caps)}"
    )
    tx_cap = tx_caps[0]
    tx_cap_length = tx_cap.get('cap_length_ft')
    assert tx_cap_length == 20, (
        f"TX cibp_cap cap_length_ft must be 20 ft (SWR-14), got {tx_cap_length}"
    )

    nm_caps = get_steps_by_type(nm_out, 'cibp_cap')
    assert len(nm_caps) == 1, (
        f"Expected exactly 1 NM cibp_cap step, found {len(nm_caps)}"
    )
    nm_cap = nm_caps[0]
    nm_cap_length = nm_cap.get('bottom_ft', 0) - nm_cap.get('top_ft', 0)
    assert nm_cap_length >= 100, (
        f"NM cibp_cap must be >= 100 ft (NMAC 19.15.25), got {nm_cap_length} ft "
        f"(top={nm_cap.get('top_ft')}, bottom={nm_cap.get('bottom_ft')})"
    )

    assert nm_cap_length >= 5 * tx_cap_length, (
        f"NM cap length ({nm_cap_length} ft) must be >= 5x TX cap length "
        f"({tx_cap_length} ft); ratio={nm_cap_length / tx_cap_length:.1f}x"
    )


def test_same_well_different_step_types():
    """TX and NM produce mutually exclusive plug-type vocabularies.

    TX produces: surface_casing_shoe_plug, top_plug, cut_casing_below_surface.
    NM produces: formation_plug, surface_plug, shoe_plug, fill_plug.

    Key divergences documented here:
      - TX has surface_casing_shoe_plug; NM does NOT.
      - NM has formation_plug; TX may or may not.
      - NM has surface_plug; TX uses top_plug instead.
      - NM has shoe_plug; TX uses surface_casing_shoe_plug instead.
    """
    tx_out, nm_out = _run_both()

    tx_step_types = {s.get('type') for s in tx_out.get('steps', [])}
    nm_step_types = {s.get('type') for s in nm_out.get('steps', [])}

    # TX has surface_casing_shoe_plug; NM does NOT.
    assert 'surface_casing_shoe_plug' in tx_step_types, (
        "TX plan must contain a surface_casing_shoe_plug step"
    )
    assert 'surface_casing_shoe_plug' not in nm_step_types, (
        "NM plan must NOT contain surface_casing_shoe_plug (NM uses shoe_plug)"
    )

    # NM has formation_plug; TX may or may not — only assert NM side.
    assert 'formation_plug' in nm_step_types, (
        "NM plan must contain at least one formation_plug step "
        "(NMAC 19.15.25 — formation isolation is mandatory)"
    )

    # NM has surface_plug; TX uses top_plug instead.
    assert 'surface_plug' in nm_step_types, (
        "NM plan must contain a surface_plug step"
    )
    assert 'top_plug' in tx_step_types, (
        "TX plan must contain a top_plug step"
    )
    assert 'surface_plug' not in tx_step_types, (
        "TX plan must NOT contain surface_plug (TX uses top_plug)"
    )

    # NM has shoe_plug; TX uses surface_casing_shoe_plug instead.
    assert 'shoe_plug' in nm_step_types, (
        "NM plan must contain a shoe_plug step"
    )
    assert 'shoe_plug' not in tx_step_types, (
        "TX plan must NOT contain shoe_plug (TX uses surface_casing_shoe_plug)"
    )


def test_citations_never_leak():
    """TX steps cite SWR/tx.tac only; NM steps cite NMAC only.

    No cross-jurisdiction regulatory citations must appear in either plan.
    This ensures the policy engine correctly scopes citations to the active
    jurisdiction and never bleeds TX rules into NM output or vice versa.
    """
    tx_out, nm_out = _run_both()

    # TX steps must not reference NMAC.
    for step in tx_out.get('steps', []):
        basis = step.get('regulatory_basis') or ''
        basis_str = ' '.join(str(b) for b in basis) if isinstance(basis, list) else str(basis)
        assert 'NMAC' not in basis_str and 'nmac' not in basis_str, (
            f"TX plan step '{step.get('type')}' leaks NM citation in "
            f"regulatory_basis: {basis!r}"
        )

    # NM steps must not reference SWR or tx.tac.
    for step in nm_out.get('steps', []):
        basis = step.get('regulatory_basis') or ''
        basis_str = ' '.join(str(b) for b in basis) if isinstance(basis, list) else str(basis)
        assert 'SWR' not in basis_str and 'tx.tac' not in basis_str, (
            f"NM plan step '{step.get('type')}' leaks TX citation in "
            f"regulatory_basis: {basis!r}"
        )

    # Also exercise the shared helper to confirm it agrees.
    assert_no_citation_leakage(tx_out, 'TX')
    assert_no_citation_leakage(nm_out, 'NM')


def test_nm_always_has_formation_plugs():
    """NM plan must include at least one formation_plug step.

    NMAC 19.15.25 mandates isolation of all productive or potentially
    productive formations encountered during drilling. TX does not impose
    this requirement through the same mechanism.
    """
    tx_out, nm_out = _run_both()

    nm_formation_plugs = get_steps_by_type(nm_out, 'formation_plug')
    assert len(nm_formation_plugs) >= 1, (
        f"NM plan must have at least 1 formation_plug step "
        f"(NMAC 19.15.25), found {len(nm_formation_plugs)}"
    )

    # TX invariants via shared helper (does not assert formation_plug presence).
    assert_tx_base_invariants(tx_out)


def test_nm_has_more_steps():
    """NM plan produces more steps than TX for the same well geometry.

    Calibrated counts: NM = 9 steps, TX = 4 steps.
    NM requires mandatory formation isolation plugs, an extended CIBP cap,
    and additional fill plugs that TX does not require.
    """
    tx_out, nm_out = _run_both()

    tx_steps = tx_out.get('steps', [])
    nm_steps = nm_out.get('steps', [])

    assert len(nm_steps) > len(tx_steps), (
        f"NM plan must have more steps than TX plan for identical geometry; "
        f"NM={len(nm_steps)}, TX={len(tx_steps)}"
    )

    assert len(tx_steps) == 4, (
        f"TX plan must produce exactly 4 steps for this geometry, got {len(tx_steps)}"
    )
    assert len(nm_steps) == 9, (
        f"NM plan must produce exactly 9 steps for this geometry, got {len(nm_steps)}"
    )
