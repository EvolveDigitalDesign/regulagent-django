"""NM jurisdiction handler — implements JurisdictionHandler protocol for New Mexico."""
from __future__ import annotations

import logging
import os

import yaml

from apps.kernel.services.jurisdiction_handler import JurisdictionHandler, PolicyPackConfig
from .facts_builder import build_nm_facts

logger = logging.getLogger(__name__)

# Path resolution: handler.py lives at apps/kernel/handlers/nm/handler.py
# PACKS_DIR is at apps/policy/packs/
_HANDLER_DIR = os.path.dirname(os.path.abspath(__file__))          # .../handlers/nm/
_KERNEL_HANDLERS_DIR = os.path.dirname(_HANDLER_DIR)               # .../handlers/
_KERNEL_DIR = os.path.dirname(_KERNEL_HANDLERS_DIR)                # .../kernel/
_APPS_DIR = os.path.dirname(_KERNEL_DIR)                           # .../apps/
_PACKS_DIR = os.path.join(_APPS_DIR, "policy", "packs")


class NMJurisdictionHandler:
    """Jurisdiction handler for New Mexico (NM OCD C-103 plugging reports).

    Implements the JurisdictionHandler protocol.  NM differs from TX in:
    - Uses C-103 form (not W-3A)
    - Source doc type is "c105" (NM completion report)
    - No district overlays — NMRegionRulesEngine handles regional variation
    - Township/Range replaces district/field location identifiers
    """

    jurisdiction_code: str = "NM"

    policy_pack_config: PolicyPackConfig = PolicyPackConfig(
        pack_rel_path="nm_ocd_c103_base_policy_pack.yaml",
        policy_id="nm.c103",
        form_name="C-103",
    )

    # ------------------------------------------------------------------
    # Document expectations
    # ------------------------------------------------------------------

    def get_expected_doc_types(self) -> list[str]:
        return ["c105"]

    # ------------------------------------------------------------------
    # Geometry derivation
    # ------------------------------------------------------------------

    def derive_geometry(
        self,
        extractions: list[dict],
        scraped_data: dict | None = None,
    ) -> dict:
        """Build canonical geometry dict from c105 extraction data.

        Processes casing_record entries by type:
        - tubing* → geometry["tubing"]
        - packer* → geometry["mechanical_barriers"] as PACKER type
        - everything else → geometry["casing_strings"]

        Also maps perforations (top_md/bottom_md) and formation_record.
        """
        geometry: dict = {
            "casing_strings": [],
            "mechanical_barriers": [],
            "tubing": [],
            "perforations": [],
            "formation_tops": [],
        }

        c105 = _find_c105(extractions)
        if not c105:
            logger.warning("nm_handler.derive_geometry: no c105 extraction found")
            return geometry

        # --- Casing records ---
        for rec in c105.get("casing_record") or []:
            casing_type = (rec.get("casing_type") or "").lower()
            mapped = {
                "string": rec.get("casing_type"),
                "size_in": rec.get("diameter"),
                "shoe_depth_ft": rec.get("bottom"),
                "cement_top_ft": rec.get("cement_top"),
                "top_ft": rec.get("top"),
                "cement_bottom_ft": rec.get("cement_bottom"),
                "sacks": rec.get("sacks"),
                "grade": rec.get("grade"),
                "weight_ppf": rec.get("weight"),
            }
            if casing_type.startswith("tubing"):
                geometry["tubing"].append(mapped)
            elif casing_type.startswith("packer"):
                geometry["mechanical_barriers"].append({
                    **mapped,
                    "type": "PACKER",
                })
            else:
                geometry["casing_strings"].append(mapped)

        # --- Perforations ---
        for perf in c105.get("producing_injection_disposal_interval") or []:
            geometry["perforations"].append({
                "top_ft": perf.get("top_md"),
                "bottom_ft": perf.get("bottom_md"),
            })

        # --- Formation tops ---
        for ft in c105.get("formation_record") or []:
            geometry["formation_tops"].append({
                "name": ft.get("formation"),
                "depth_ft": ft.get("top_ft"),
            })

        return geometry

    # ------------------------------------------------------------------
    # Resolved facts
    # ------------------------------------------------------------------

    def build_resolved_facts(
        self,
        well_info: dict,
        geometry: dict,
        extractions: list[dict],
    ) -> dict:
        """Build kernel-ready facts dict from NM well data."""
        # well_info may come from the c105 well_info sub-key — try to enrich it
        c105 = _find_c105(extractions)
        if c105:
            c105_well_info = c105.get("well_info") or {}
            # Merge: c105_well_info is the authoritative source; well_info may add extras
            merged_well_info = {**c105_well_info, **well_info}
        else:
            merged_well_info = well_info

        return build_nm_facts(merged_well_info, geometry, extractions)

    # ------------------------------------------------------------------
    # Policy loading
    # ------------------------------------------------------------------

    def load_effective_policy(
        self,
        facts: dict,
        geometry: dict | None = None,
    ) -> dict:
        """Load NM policy pack directly from YAML.

        NM has no district overlays — NMRegionRulesEngine handles regional
        variation at runtime during step generation.
        """
        pack_path = os.path.join(_PACKS_DIR, self.policy_pack_config.pack_rel_path)
        with open(pack_path, "r", encoding="utf-8") as f:
            policy = yaml.safe_load(f)

        # Stamp with canonical policy_id
        policy["policy_id"] = self.policy_pack_config.policy_id
        return policy

    # ------------------------------------------------------------------
    # Step post-processing
    # ------------------------------------------------------------------

    def post_process_steps(
        self,
        steps: list[dict],
        facts: dict,
        policy: dict,
    ) -> list[dict]:
        """NM post-processing — return steps as-is.

        TX requires mechanical awareness, CIBP detector, and district overrides.
        NM handles all regional variation inside C103PluggingRules / NMRegionRulesEngine,
        so no additional post-processing is needed at this layer.
        """
        return steps

    # ------------------------------------------------------------------
    # Plan payload
    # ------------------------------------------------------------------

    def build_plan_payload(
        self,
        steps: list[dict],
        facts: dict,
        policy: dict,
        geometry: dict,
    ) -> dict:
        raise NotImplementedError("NM payload building not yet extracted from view")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_c105(extractions: list[dict]) -> dict | None:
    """Return the first c105 extraction data dict found in the extractions list."""
    for ext in extractions or []:
        # Each extraction may be a raw dict with doc_type or already keyed by type
        if "c105" in ext:
            return ext["c105"]
        doc_type = ext.get("document_type") or ext.get("doc_type")
        if doc_type == "c105":
            return ext.get("json_data") or ext
    return None
