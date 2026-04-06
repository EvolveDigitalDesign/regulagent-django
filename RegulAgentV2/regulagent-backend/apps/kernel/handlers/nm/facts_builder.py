"""NM-specific facts builder for the kernel pipeline."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_nm_facts(well_info: dict, geometry: dict, extractions: list[dict]) -> dict:
    """Build kernel-ready facts dict from NM well data.

    NM differs from TX:
    - No district (uses regions via NMRegionRulesEngine)
    - Township/Range instead of district/field
    - County-based regional rules
    """
    # Extract formation tops from c105 extraction data only
    from apps.kernel.handlers.nm.handler import _find_c105
    c105 = _find_c105(extractions)
    formation_tops = (c105.get("formation_record") or []) if c105 else []

    # Convert formation_record format to formation_tops format for c103 rules
    # formation_record: [{"formation": "Dakota", "top_ft": null}, ...]
    # formation_tops for kernel: [{"name": "Dakota", "depth_ft": null}, ...]
    kernel_formation_tops = []
    for ft in formation_tops:
        name = ft.get("formation") or ft.get("name")
        depth = ft.get("top_ft") or ft.get("depth_ft")
        if name:
            kernel_formation_tops.append({"name": name, "depth_ft": depth})

    # Infer total depth from well_info depths or geometry
    total_depth = None
    depths = well_info.get("depths") or {}
    if isinstance(depths, dict):
        total_depth = depths.get("proposed_depth") or depths.get("tvd")
    if total_depth is None:
        total_depth = well_info.get("total_depth") or well_info.get("proposed_depth_ft") or well_info.get("tvd_ft")
    if total_depth is None:
        total_depth = _infer_total_depth(geometry)

    facts = {
        "state": "NM",
        "api_number": well_info.get("api_number", "") or well_info.get("api14", "") or well_info.get("api", ""),
        "well_name": well_info.get("well_name", ""),
        "operator": well_info.get("operator", ""),
        "county": well_info.get("county", ""),
        "township": well_info.get("township"),
        "range": well_info.get("range"),
        "field": well_info.get("field", ""),
        # NM doesn't use district - uses regions
        "district": None,
        # Geometry facts
        "casing_strings": geometry.get("casing_strings", []),
        "perforations": geometry.get("perforations", []),
        "formation_tops": kernel_formation_tops,
        "mechanical_barriers": geometry.get("mechanical_barriers", []),
        "tubing": geometry.get("tubing", []),
        "total_depth_ft": total_depth,
        # KOP from geometry (derive_geometry extracts it from c105)
        # or from well_info if provided directly
        "kop": geometry.get("kop_ft") or well_info.get("kop") or well_info.get("kop_ft"),
    }
    return facts


def _infer_total_depth(geometry: dict) -> float | None:
    """Infer total depth from deepest casing shoe or perforation."""
    depths = []
    for cs in geometry.get("casing_strings", []):
        d = cs.get("shoe_depth_ft")
        if d is not None:
            try:
                depths.append(float(d))
            except (ValueError, TypeError):
                pass
    for perf in geometry.get("perforations", []):
        d = perf.get("bottom_ft")
        if d is not None:
            try:
                depths.append(float(d))
            except (ValueError, TypeError):
                pass
    return max(depths) if depths else None
