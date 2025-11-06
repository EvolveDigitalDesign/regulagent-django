from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, List

from django.db.models import QuerySet

from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts

# Optional imports for from_extractions()
try:  # pragma: no cover
    from apps.public_core.models import ExtractedDocument
except Exception:  # pragma: no cover
    ExtractedDocument = None  # type: ignore


class PolicyApplicator:
    """Applies a YAML policy pack to normalized facts to produce a deterministic plan."""

    def __init__(self, pack_rel_path: str = 'tx_rrc_w3a_base_policy_pack.yaml') -> None:
        self.pack_rel_path = pack_rel_path

    @staticmethod
    def _wrap(value: Any) -> Dict[str, Any]:
        return {"value": value}

    def _wrap_facts(self, facts: Dict[str, Any]) -> Dict[str, Any]:
        """Wrap only keys the kernel reads for summaries/flow while leaving others as-is."""
        out: Dict[str, Any] = dict(facts)
        for k in ("api14", "state", "district", "county", "field", "lease", "well_no", "has_uqw", "uqw_base_ft", "surface_shoe_ft", "use_cibp"):
            if k in facts and not isinstance(facts[k], dict):
                out[k] = self._wrap(facts[k])
        return out

    def load_policy(self, district: Optional[str] = None, county: Optional[str] = None) -> Dict[str, Any]:
        policy = get_effective_policy(district=district, county=county, pack_rel_path=self.pack_rel_path)
        policy.setdefault("policy_id", "tx.w3a")
        return policy

    def apply(self, facts: Dict[str, Any], district: Optional[str] = None, county: Optional[str] = None) -> Dict[str, Any]:
        policy = self.load_policy(district=district, county=county)
        # Mark complete so the kernel emits steps; loader ensures required knobs
        policy["complete"] = True
        policy.setdefault("preferences", {}).setdefault("rounding_policy", "nearest")
        wrapped = self._wrap_facts(facts)
        return plan_from_facts(wrapped, policy)

    # --- Optional convenience: derive facts from latest extractions ---
    def from_extractions(self, api: str) -> Dict[str, Any]:  # pragma: no cover - integration path
        if ExtractedDocument is None:
            raise RuntimeError("apps.public_core.models.ExtractedDocument not available")

        def latest(doc_type: str) -> Optional[Dict[str, Any]]:
            qs: QuerySet = ExtractedDocument.objects.filter(api_number=api, document_type=doc_type).order_by("-created_at")
            row = qs.first()
            return (row and row.json_data) or None

        w2 = latest("w2") or {}
        gau = latest("gau") or {}

        wi = (w2.get("well_info") or {})
        api14 = (wi.get("api") or str(api)).replace("-", "")
        county = wi.get("county") or ""
        field = wi.get("field") or ""
        lease = wi.get("lease") or ""
        well_no = wi.get("well_no") or ""
        rrc = (wi.get("district") or "").strip()
        district = "08A" if (rrc in ("08", "8") and ("andrews" in (county or "").lower())) else (rrc or None)

        # UQW
        uqw_depth = None
        if gau:
            uqw_depth = (gau.get("surface_casing_determination") or {}).get("gau_groundwater_protection_determination_depth")
        if not uqw_depth:
            uqw_depth = (w2.get("surface_casing_determination") or {}).get("gau_groundwater_protection_determination_depth")

        # Surface shoe depth and sizes from normalized casing_record rows
        surface_shoe_ft: Optional[float] = None
        casing_record: List[Dict[str, Any]] = (w2.get("casing_record") or [])
        for row in casing_record:
            kind = (row.get("string") or row.get("type_of_casing") or "").lower()
            if kind.startswith("surface"):
                surface_shoe_ft = row.get("shoe_depth_ft") or row.get("setting_depth_ft") or row.get("bottom_ft")
                break

        facts = {
            "api14": api14,
            "state": "TX",
            "district": district,
            "county": county,
            "field": field,
            "lease": lease,
            "well_no": well_no,
            "has_uqw": bool(gau or uqw_depth),
            "uqw_base_ft": uqw_depth,
            "use_cibp": True,
            "surface_shoe_ft": surface_shoe_ft,
        }
        return self.apply(facts, district=district, county=county)

