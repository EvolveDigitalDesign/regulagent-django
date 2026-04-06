"""
Deterministic semantic tagger for document segments.

Assigns tags based on form type (static mapping) plus keyword scanning
of raw text for context-specific additions. No LLM calls.
"""

import re
import logging

logger = logging.getLogger(__name__)

# Static tag mapping by normalized form type (lowercase, no hyphens)
FORM_TAG_MAP = {
    # --- Currently extracted TX forms ---
    "w1":     ["permit", "drilling", "surface_casing", "location"],
    "w2":     ["completion", "geometry", "cement", "perforations", "casing", "tubing", "formation_record"],
    "w3":     ["plugging", "cement", "geometry", "casing_disposition", "plug_record", "mud_data"],
    "w3a":    ["plugging_proposal", "casing", "perforations", "plugging_procedure"],
    "w15":    ["cement_job", "squeeze", "casing"],
    "g1":     ["gas_test", "deliverability"],
    "gau":    ["groundwater", "surface_casing"],
    "schematic":       ["geometry", "visual_diagram"],
    "formation_tops":  ["formation_record", "geology"],
    "pa_procedure":    ["plugging_procedure", "formation_record"],

    # --- TX forms (classified but not yet extracted) ---
    "w1d":    ["permit", "drilling", "directional"],
    "w1h":    ["permit", "drilling", "horizontal", "lateral"],
    "w1a":    ["permit", "acreage", "pooling"],
    "w3c":    ["plugging", "surface_equipment", "removal"],
    "w3x":    ["plugging", "extension", "deadline"],
    "w10":    ["well_status", "oil"],
    "g10":    ["well_status", "gas"],
    "h1":     ["injection", "disposal", "permit"],
    "h5":     ["injection", "pressure_test"],
    "h8":     ["spill", "loss_report"],
    "h9":     ["compliance", "h2s", "certificate"],
    "h10":    ["injection", "disposal", "annual_report"],
    "h15":    ["inactive_well", "testing"],
    "p4":     ["gatherer", "purchaser", "transport"],
    "p5":     ["operator", "organization", "license"],
    "p13":    ["casing", "pressure_test"],
    "pr":     ["production", "monthly_report"],
    "swr13":  ["casing", "cementing", "exception"],
    "swr32":  ["flaring", "venting", "exception"],
    "st1":    ["severance_tax", "incentive"],
    "t4":     ["pipeline", "permit", "construction"],
    "electric_log": ["well_log", "subsurface"],
    "plat":   ["location", "survey"],
    "letter": ["correspondence", "determination"],

    # --- NM forms ---
    "c_101":  ["permit", "drilling", "casing", "cement"],
    "c_103":  ["plugging", "casing", "cement", "plugging_procedure"],
    "c_105":  ["completion", "casing", "perforations", "cement", "production_test"],
    "c_115":  ["sundry", "workover"],
    "sundry": ["sundry", "workover"],
}

# Contextual keyword patterns: (regex, tag_to_add)
CONTEXTUAL_PATTERNS = [
    (re.compile(r"\bSQUEEZE\b", re.IGNORECASE), "squeeze"),
    (re.compile(r"\bH2S\b", re.IGNORECASE), "h2s"),
    (re.compile(r"\bCIBP\b", re.IGNORECASE), "bridge_plug"),
    (re.compile(r"\bCAST\s*IRON\s*BRIDGE\s*PLUG\b", re.IGNORECASE), "bridge_plug"),
    (re.compile(r"\bRETAINER\b", re.IGNORECASE), "retainer"),
    (re.compile(r"\bDIRECTIONAL\s+SURVEY\b", re.IGNORECASE), "directional"),
    (re.compile(r"\bHORIZONTAL\b", re.IGNORECASE), "horizontal"),
    (re.compile(r"\bCO2\b", re.IGNORECASE), "co2"),
    (re.compile(r"\bSALT\s*WATER\s*DISPOSAL\b", re.IGNORECASE), "disposal"),
    (re.compile(r"\bWATERFLOOD\b", re.IGNORECASE), "waterflood"),
    (re.compile(r"\bFRAC\b", re.IGNORECASE), "frac"),
    (re.compile(r"\bACIDIZE\b", re.IGNORECASE), "acidize"),
    (re.compile(r"\bWORKOVER\b", re.IGNORECASE), "workover"),
    (re.compile(r"\bRECOMPLETION\b", re.IGNORECASE), "recompletion"),
    (re.compile(r"\bSIDETRACK\b", re.IGNORECASE), "sidetrack"),
]

# Depth threshold for "deep_well" tag
DEEP_WELL_THRESHOLD_FT = 10000
_DEPTH_PATTERN = re.compile(r"(\d{5,})\s*(?:feet|ft|')", re.IGNORECASE)


def _normalize_form_type(form_type: str) -> str:
    """Normalize form type to match FORM_TAG_MAP keys."""
    ft = form_type.lower().strip()
    # Remove hyphens: W-2 -> w2, W-3A -> w3a, SWR-13 -> swr13
    ft = ft.replace("-", "").replace(" ", "")
    # Handle underscore variants (c_101 stays c_101)
    return ft


def tag_segment(form_type: str, raw_text: str = "") -> list:
    """
    Assign semantic tags to a document segment.

    Args:
        form_type: The classified form type (e.g., 'W-2', 'c_103')
        raw_text: Raw text extracted from the segment pages

    Returns:
        Deduplicated list of semantic tags
    """
    normalized = _normalize_form_type(form_type)
    tags = list(FORM_TAG_MAP.get(normalized, []))

    if not tags:
        logger.debug(f"No static tags for form_type={form_type} (normalized={normalized})")

    if raw_text:
        # Contextual keyword scanning
        for pattern, tag in CONTEXTUAL_PATTERNS:
            if pattern.search(raw_text) and tag not in tags:
                tags.append(tag)

        # Deep well detection
        for match in _DEPTH_PATTERN.finditer(raw_text):
            depth = int(match.group(1))
            if depth >= DEEP_WELL_THRESHOLD_FT:
                if "deep_well" not in tags:
                    tags.append("deep_well")
                break

    return tags
