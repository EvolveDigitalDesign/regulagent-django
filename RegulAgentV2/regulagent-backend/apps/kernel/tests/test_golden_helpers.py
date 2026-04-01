"""
Shared helper module for NM C-103 golden tests.

Provides reusable assertion functions and a policy loader. This module is NOT
a test file itself — it contains no test_* functions and no pytest fixtures.

Related to:
- NMAC 19.15.25 (Well Plugging and Abandonment)
- C-103 Form — NM plugging plan submission
"""

import yaml
from pathlib import Path


# ---------------------------------------------------------------------------
# Policy loader
# ---------------------------------------------------------------------------

def load_nm_policy() -> dict:
    """Load and stamp the NM OCD C-103 base policy pack.

    Reads ``apps/policy/packs/nm_ocd_c103_base_policy_pack.yaml`` relative to
    the Django project root (two levels above this file's ``tests/`` dir),
    stamps the required runtime fields, and returns the policy dict.

    Returns
    -------
    dict
        Policy dict with ``policy_id``, ``complete``, ``jurisdiction``, and
        ``form`` fields set.
    """
    # Resolve: tests/ -> kernel/ -> apps/ -> regulagent-backend/
    base_dir = Path(__file__).resolve().parent.parent.parent.parent
    pack_path = base_dir / "apps" / "policy" / "packs" / "nm_ocd_c103_base_policy_pack.yaml"

    with pack_path.open("r") as fh:
        policy = yaml.safe_load(fh)

    # Stamp required runtime fields.
    policy["policy_id"] = "nm.c103"
    policy["complete"] = True
    policy["jurisdiction"] = "NM"
    policy["form"] = "C-103"

    return policy


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------

def get_steps_by_type(out: dict, step_type: str) -> list:
    """Return all steps in *out* whose type matches *step_type*.

    Parameters
    ----------
    out:
        Plan output dict containing a ``steps`` list.
    step_type:
        Value to match against each step's ``step_type`` key.

    Returns
    -------
    list
        Possibly-empty list of matching step dicts.
    """
    return [s for s in out.get("steps", []) if (s.get("type") or s.get("step_type")) == step_type]


def get_sacks(step: dict) -> float:
    """Extract the sack count from a step dict.

    Tries ``step['materials']['slurry']['sacks']`` first, then falls back to
    ``step['details']['sacks_required']``. Returns ``0`` if neither path
    yields a value.

    Parameters
    ----------
    step:
        A single step dict from a plan output.

    Returns
    -------
    float
        Sack count, or 0 if unavailable.
    """
    slurry_sacks = (
        step.get("materials", {})
        .get("slurry", {})
        .get("sacks")
    )
    if slurry_sacks is not None:
        return float(slurry_sacks)

    details_sacks = step.get("details", {}).get("sacks_required")
    if details_sacks is not None:
        return float(details_sacks)

    return 0.0


# ---------------------------------------------------------------------------
# Citation helpers
# ---------------------------------------------------------------------------

def _step_has_citation(step: dict, *fragments: str) -> bool:
    """Return True if the step's regulatory_basis contains any of *fragments*."""
    basis = step.get("regulatory_basis") or ""
    # regulatory_basis may be a list of strings or a plain string
    if isinstance(basis, list):
        basis = " ".join(str(b) for b in basis)
    return any(frag in str(basis) for frag in fragments)


# ---------------------------------------------------------------------------
# Core invariant assertions
# ---------------------------------------------------------------------------

def assert_nm_base_invariants(out: dict) -> None:
    """Assert that *out* satisfies all NM C-103 plan invariants.

    Parameters
    ----------
    out:
        Serialised plan output dict. Must contain at minimum:
        ``jurisdiction``, ``district``, ``form``, and ``steps`` keys.

    Raises
    ------
    AssertionError
        On the first violated invariant, with a descriptive message.
    """
    # --- Top-level fields ---------------------------------------------------
    assert out.get("jurisdiction") == "NM", (
        f"Expected jurisdiction='NM', got {out.get('jurisdiction')!r}"
    )
    assert out.get("district") is None, (
        f"NM plans must not populate 'district', got {out.get('district')!r}"
    )
    assert out.get("form") == "C-103", (
        f"Expected form='C-103', got {out.get('form')!r}"
    )

    steps = out.get("steps", [])

    # --- Structural plugs ---------------------------------------------------
    formation_plugs = get_steps_by_type(out, "formation_plug")
    shoe_plugs = get_steps_by_type(out, "shoe_plug")
    assert len(formation_plugs) > 0 or len(shoe_plugs) > 0, (
        "NM plans must contain at least one formation_plug or shoe_plug step "
        "(NMAC 19.15.25 — formation isolation is mandatory)"
    )

    # --- Surface plug -------------------------------------------------------
    surface_plugs = get_steps_by_type(out, "surface_plug")
    assert len(surface_plugs) == 1, (
        f"Expected exactly 1 surface_plug step, found {len(surface_plugs)}"
    )

    # --- Mechanical plug (CIBP) ---------------------------------------------
    mechanical_plugs = get_steps_by_type(out, "mechanical_plug")
    assert len(mechanical_plugs) == 1, (
        f"Expected exactly 1 mechanical_plug (CIBP) step, found {len(mechanical_plugs)}"
    )

    # --- CIBP cap -----------------------------------------------------------
    cibp_caps = get_steps_by_type(out, "cibp_cap")
    assert len(cibp_caps) == 1, (
        f"Expected exactly 1 cibp_cap step, found {len(cibp_caps)}"
    )

    cap = cibp_caps[0]
    top_ft = cap.get("top_ft", 0)
    bottom_ft = cap.get("bottom_ft", 0)
    cap_length = bottom_ft - top_ft
    assert cap_length == 100, (
        f"CIBP cap must be exactly 100 ft (NM requirement), got {cap_length} ft "
        f"(top={top_ft}, bottom={bottom_ft})"
    )

    # --- Cement plug sacks (all non-mechanical plugs) -----------------------
    MECHANICAL_TYPES = {"mechanical_plug"}
    cement_steps = [s for s in steps if (s.get("type") or s.get("step_type")) not in MECHANICAL_TYPES]
    for step in cement_steps:
        sacks = get_sacks(step)
        assert sacks >= 25, (
            f"Cement plug '{step.get('type') or step.get('step_type')}' must have >= 25 sacks "
            f"(NMAC 19.15.25 minimum), got {sacks}"
        )

    # --- Wait-on-cement hours -----------------------------------------------
    # Surface plug: wait_hours must be 0.
    # All other cement plugs with a wait_hours field: must be 4.
    surface_plug = surface_plugs[0]
    sp_wait = surface_plug.get("details", {}).get("wait_hours")
    if sp_wait is not None:
        assert sp_wait == 0, (
            f"Surface plug wait_hours must be 0, got {sp_wait}"
        )

    non_surface = [s for s in cement_steps if (s.get("type") or s.get("step_type")) != "surface_plug"]
    for step in non_surface:
        wait = step.get("details", {}).get("wait_hours")
        if wait is None:
            # Also accept wait_hours at the top level of the step dict
            wait = step.get("wait_hours")
        if wait is not None:
            assert wait == 4, (
                f"Cement plug '{step.get('type') or step.get('step_type')}' wait_hours must be 4, got {wait}"
            )

    # --- No TX citations ----------------------------------------------------
    for step in steps:
        assert not _step_has_citation(step, "SWR", "tx.tac"), (
            f"NM plan step '{step.get('type') or step.get('step_type')}' contains a TX citation in "
            f"regulatory_basis: {step.get('regulatory_basis')!r}"
        )

    # --- Surface plug specifics ---------------------------------------------
    assert surface_plug.get("operation_type") == "circulate", (
        f"Surface plug operation_type must be 'circulate', "
        f"got {surface_plug.get('operation_type')!r}"
    )

    sp_top = surface_plug.get("top_ft", -1)
    sp_bottom = surface_plug.get("bottom_ft", -1)
    assert sp_top == 0, (
        f"Surface plug top_ft must be 0, got {sp_top}"
    )
    assert sp_bottom == 50, (
        f"Surface plug bottom_ft must be 50, got {sp_bottom}"
    )

    sp_cement_class = surface_plug.get("details", {}).get("cement_class")
    assert sp_cement_class == "C", (
        f"Surface plug cement_class must be 'C', got {sp_cement_class!r}"
    )


def assert_tx_base_invariants(out: dict) -> None:
    """Assert that *out* satisfies all TX plan invariants.

    Parameters
    ----------
    out:
        Serialised plan output dict.

    Raises
    ------
    AssertionError
        On the first violated invariant.
    """
    assert out.get("jurisdiction") == "TX", (
        f"Expected jurisdiction='TX', got {out.get('jurisdiction')!r}"
    )

    for step in out.get("steps", []):
        assert not _step_has_citation(step, "NMAC", "nmac"), (
            f"TX plan step '{step.get('type') or step.get('step_type')}' contains an NM citation in "
            f"regulatory_basis: {step.get('regulatory_basis')!r}"
        )


def assert_no_citation_leakage(out: dict, jurisdiction: str) -> None:
    """Assert that plan steps contain no cross-jurisdiction citations.

    Parameters
    ----------
    out:
        Serialised plan output dict.
    jurisdiction:
        ``'NM'`` or ``'TX'``. Determines which foreign citations are forbidden.

    Raises
    ------
    AssertionError
        If any step references a citation belonging to the foreign jurisdiction.
    ValueError
        If *jurisdiction* is not ``'NM'`` or ``'TX'``.
    """
    if jurisdiction == "NM":
        for step in out.get("steps", []):
            assert not _step_has_citation(step, "SWR", "tx.tac"), (
                f"NM plan step '{step.get('type') or step.get('step_type')}' leaks TX citation: "
                f"{step.get('regulatory_basis')!r}"
            )
    elif jurisdiction == "TX":
        for step in out.get("steps", []):
            assert not _step_has_citation(step, "NMAC", "nmac"), (
                f"TX plan step '{step.get('type') or step.get('step_type')}' leaks NM citation: "
                f"{step.get('regulatory_basis')!r}"
            )
    else:
        raise ValueError(
            f"Unknown jurisdiction {jurisdiction!r}; expected 'NM' or 'TX'"
        )
