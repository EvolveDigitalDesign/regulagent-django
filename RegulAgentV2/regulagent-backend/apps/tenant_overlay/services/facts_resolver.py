from __future__ import annotations

from typing import Dict, Any

from apps.public_core.models import PublicFacts, WellRegistry
from apps.tenant_overlay.models import WellEngagement, CanonicalFacts


def _pack(value: Any, units: str = "", source_layer: str = "", provenance=None, confidence=None) -> Dict[str, Any]:
    return {
        "value": value,
        "units": units or "",
        "source_layer": source_layer,
        "provenance": provenance or [],
        "confidence": confidence,
    }


def resolve_engagement_facts(engagement_id: int) -> Dict[str, Dict[str, Any]]:
    """
    Merge facts with precedence CanonicalFacts → PublicFacts → WellRegistry identity.

    Returns a dict keyed by fact_key with payload:
      { value, units, source_layer, provenance, confidence }
    """
    resolved: Dict[str, Dict[str, Any]] = {}

    engagement = (
        WellEngagement.objects.select_related("well")
        .only("id", "tenant_id", "mode", "well_id")
        .get(id=engagement_id)
    )
    well: WellRegistry = engagement.well

    # 1) CanonicalFacts (tenant overlay)
    for cf in CanonicalFacts.objects.filter(engagement=engagement).only(
        "fact_key", "value", "units", "provenance", "confidence"
    ):
        resolved[cf.fact_key] = _pack(
            value=cf.value,
            units=cf.units,
            source_layer="canonical",
            provenance=cf.provenance or [],
            confidence=cf.confidence,
        )

    # 2) PublicFacts (only if not already set)
    for pf in PublicFacts.objects.filter(well=well).only(
        "fact_key", "value", "units", "provenance", "source", "as_of"
    ):
        if pf.fact_key not in resolved:
            provenance = pf.provenance or {}
            # ensure list provenance downstream
            provenance_list = provenance if isinstance(provenance, list) else [provenance]
            resolved[pf.fact_key] = _pack(
                value=pf.value,
                units=pf.units,
                source_layer="public",
                provenance=provenance_list,
                confidence=None,
            )

    # 3) WellRegistry identity (only if not already set)
    registry_fallbacks = {
        "api14": well.api14,
        "state": well.state,
        "county": well.county,
        "lat": float(well.lat) if well.lat is not None else None,
        "lon": float(well.lon) if well.lon is not None else None,
    }
    for key, val in registry_fallbacks.items():
        if val is None:
            continue
        if key not in resolved:
            resolved[key] = _pack(value=val, units="", source_layer="registry", provenance=[], confidence=None)

    return resolved


