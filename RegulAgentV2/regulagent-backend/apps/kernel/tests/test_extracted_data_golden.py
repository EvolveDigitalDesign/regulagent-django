import json
import pytest

from apps.public_core.models import ExtractedDocument
from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts


def _n(v):
    return {"value": v}


def _district_from_w2(w2: dict) -> str:
    d = ((w2.get("well_info") or {}).get("rrc_district") or "").strip()
    county = ((w2.get("well_info") or {}).get("county") or "").strip().lower()
    # Normalize subcode based on county when RRC district is ambiguous (08 vs 08A)
    if d in ("08", "8") and "andrews" in county:
        return "08A"
    return d or "08A"


@pytest.mark.golden
@pytest.mark.django_db
def test_extracted_data_golden_builds_kernel_plan():
    """
    Build kernel facts from latest GAU/W-2/W-15 extractions for a known API and
    assert deterministic plan shape and key invariants.
    """
    # Choose the most recent API we have extractions for (prefer the one used in earlier goldens)
    cand = (
        ExtractedDocument.objects
        .filter(document_type__in=["w2", "w15", "gau"])  # measured set
        .order_by("-created_at")
        .values_list("api_number", flat=True)
        .distinct()
    )
    if not cand:
        pytest.skip("No extracted documents available for golden test")

    api = cand[0]
    docs = {
        d.document_type: d
        for d in ExtractedDocument.objects.filter(api_number=api, document_type__in=["w2", "w15", "gau"]).order_by("document_type", "-created_at")
    }
    if "w2" not in docs:
        pytest.skip("W-2 extraction not found for API %s" % api)

    w2 = docs["w2"].json_data or {}
    w15 = (docs.get("w15") and docs["w15"].json_data) or {}
    gau = (docs.get("gau") and docs["gau"].json_data) or {}

    # Identity & context
    api14 = (w2.get("well_info") or {}).get("api", "").replace("-", "")
    district = _district_from_w2(w2)
    county = (w2.get("well_info") or {}).get("county") or ""
    field = (w2.get("well_info") or {}).get("field") or ""
    lease = (w2.get("well_info") or {}).get("lease") or ""
    well_no = (w2.get("well_info") or {}).get("well_no") or ""

    # GAU depth (preferred from GAU doc, fallback to W-2 surface_casing_determination)
    uqw_depth = (
        (gau.get("surface_casing_determination") or {}).get("gau_groundwater_protection_determination_depth")
        if gau else None
    )
    if uqw_depth in (None, ""):
        uqw_depth = (w2.get("surface_casing_determination") or {}).get("gau_groundwater_protection_determination_depth")

    # Surface shoe depth from W-2 casing record
    surface_shoe_ft = None
    for row in (w2.get("casing_record") or []):
        if (row.get("type_of_casing") or "").lower().startswith("surface"):
            surface_shoe_ft = row.get("setting_depth_ft")
            break

    facts = {
        "api14": _n(api14),
        "state": _n("TX"),
        "district": _n(district or "08A"),
        "county": _n(county),
        "field": _n(field),
        "lease": _n(lease),
        "well_no": _n(well_no),
        # Regulatory drivers
        "has_uqw": _n(True if (gau or uqw_depth not in (None, "")) else False),
        "uqw_base_ft": _n(uqw_depth) if uqw_depth not in (None, "") else _n(None),
        # Prefer CIBP-based isolation pattern unless policy forbids
        "use_cibp": _n(True),
        # Provide shoe depth for surface casing shoe plug interval derivation
        "surface_shoe_ft": _n(surface_shoe_ft) if surface_shoe_ft not in (None, "") else _n(None),
    }

    policy = get_effective_policy(district=facts["district"]["value"], county=facts["county"]["value"] or None)
    policy["policy_id"] = "tx.w3a"
    policy["complete"] = True
    policy.setdefault("preferences", {})["rounding_policy"] = "nearest"

    out = plan_from_facts(facts, policy)

    # Basic header invariants
    assert out["jurisdiction"] == "TX"
    assert out["district"].upper().startswith((facts["district"]["value"] or "").upper()[:2])
    assert (out.get("materials_policy") or {}).get("rounding") == "nearest"

    steps = out.get("steps", [])
    types = [s.get("type") for s in steps]

    # Expect a surface shoe coverage step if surface shoe depth is known
    if surface_shoe_ft not in (None, ""):
        assert "surface_casing_shoe_plug" in types

    # If UQW present, expect an isolation plug spanning ~100 ft around base
    if facts["has_uqw"]["value"]:
        assert any(t == "uqw_isolation_plug" for t in types)

    # Materials sanity: any cement step must compute non-negative sacks; open-hole plugs must not carry casing_id_in
    for s in steps:
        if s.get("type") in ("cement_plug", "cibp_cap", "squeeze", "surface_casing_shoe_plug", "uqw_isolation_plug"):
            slurry = (s.get("materials") or {}).get("slurry", {})
            sacks = slurry.get("sacks")
            if sacks is not None:
                assert int(sacks) >= 0
        if s.get("type") == "cement_plug" and s.get("geometry_context") == "open_hole":
            assert "casing_id_in" not in s


