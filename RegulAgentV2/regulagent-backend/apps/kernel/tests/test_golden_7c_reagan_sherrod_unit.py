import json
import pytest

from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts


def _find(steps, pred):
    return [s for s in steps if pred(s)]


def _interval_covers(s, top, bot, tol=0):
    st, sb = float(s.get("top_ft", 0)), float(s.get("bottom_ft", 0))
    return abs(st - top) <= tol and abs(sb - bot) <= tol


def _has_tag(s):
    instr = s.get("special_instructions", "")
    if isinstance(instr, list):
        instr = " ".join(map(str, instr))
    return bool(s.get("tag_required")) or ("TAG" in str(instr).upper())


@pytest.mark.golden
def test_golden_7c_reagan_sherrod_unit_plan_minimums():
    facts = {
        # From W-3A header (page 1)
        "api14":    {"value": "38335681"},
        "state":    {"value": "TX"},
        "district": {"value": "7C"},
        "county":   {"value": "Reagan"},
        "field":    {"value": "SPRABERRY [TREND AREA]"},
        "lease":    {"value": "SHERROD UNIT"},
        "well_no":  {"value": "2206"},
        # From GAU GW-2 (base UQW)
        "uqw_base_ft": {"value": 400},
        # Hints to kernel
        "has_uqw":  {"value": True},
        "use_cibp": {"value": True},
    }

    # Load effective 7C policy
    policy = get_effective_policy(district="7C", county="Reagan")
    policy["policy_id"] = "tx.w3a"
    policy["complete"] = True
    policy.setdefault("preferences", {})["rounding_policy"] = "nearest"

    out = plan_from_facts(facts, policy)

    # Basic header
    assert out["jurisdiction"] == "TX"
    assert out["district"] == "7C"
    assert out["inputs_summary"].get("api14") in ("38335681", "038335681")

    steps = out.get("steps", [])

    # UQW isolation plug around 350–450 ft
    uqw_candidates = _find(
        steps,
        lambda s: s.get("type") in ("uqw_isolation_plug", "cement_plug") and 300 <= min(s.get("top_ft", 0), s.get("bottom_ft", 0)) <= 450 and 350 <= max(s.get("top_ft", 0), s.get("bottom_ft", 0)) <= 500,
    )
    assert uqw_candidates, "Expected a UQW isolation plug spanning ~350–450 ft"
    uqw = sorted(uqw_candidates, key=lambda s: abs(abs(s["top_ft"] - s["bottom_ft"]) - 100))[0]
    assert _interval_covers(uqw, 450, 350, tol=5) or _interval_covers(uqw, 350, 450, tol=5)
    assert _has_tag(uqw), "UQW plug should require TAG in District 7C"
    basis = " ".join(uqw.get("regulatory_basis", []))
    assert "3.14" in basis or "SWR-14" in basis

    # Surface-region plug checks (≤600 ft) or shoe plug coverage
    surf_candidates = _find(
        steps,
        lambda s: (s.get("type") == "cement_plug" and max(s.get("top_ft", 0), s.get("bottom_ft", 0)) <= 600)
        or (s.get("type") == "surface_casing_shoe_plug"),
    )
    assert surf_candidates, "Expected at least one near-surface plug/coverage step"
    assert any(_has_tag(s) for s in surf_candidates), "7C requires tagging at surface-region plugs"

    # 9.5 ppg mud requirement present (instruction or findings)
    findings = out.get("findings", []) + sum([s.get("findings", []) for s in steps], [])
    msg_blob = json.dumps(findings)
    text_blob = " ".join((s.get("special_instructions", "") or "") for s in steps)
    combined = (msg_blob + " " + text_blob).lower()
    assert ("9.5" in combined and "mud" in combined) or ("9.5 ppg" in combined)

    # District overlay provenance present on at least one step
    some_step_basis = " ".join(" ".join(s.get("regulatory_basis", []) or []) for s in steps)
    assert "7c" in some_step_basis.lower() or "district" in some_step_basis.lower()


