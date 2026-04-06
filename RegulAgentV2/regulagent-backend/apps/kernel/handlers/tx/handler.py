from __future__ import annotations
import logging
from apps.kernel.services.jurisdiction_handler import JurisdictionHandler, PolicyPackConfig

logger = logging.getLogger(__name__)


class TXJurisdictionHandler:
    """Texas jurisdiction handler — wraps existing TX pipeline functions."""

    jurisdiction_code = "TX"
    policy_pack_config = PolicyPackConfig(
        pack_rel_path="tx_rrc_w3a_base_policy_pack.yaml",
        policy_id="tx.w3a",
        form_name="W-3A",
    )

    def get_expected_doc_types(self) -> list[str]:
        return ["w2", "w15", "gau"]

    def derive_geometry(self, extractions, scraped_data=None):
        # TX geometry is derived in w3a_segmented._derive_geometry
        # This wrapper exists for protocol completeness — actual call
        # happens in the view pipeline for now (Phase 4 will migrate)
        raise NotImplementedError("TX geometry derivation still lives in w3a_segmented views")

    def build_resolved_facts(self, well_info, geometry, extractions):
        # TX facts building lives in w3a_segmented._build_plan_from_snapshot
        # Will be extracted in Phase 4
        raise NotImplementedError("TX facts building still lives in w3a_segmented views")

    def load_effective_policy(self, facts, geometry=None):
        from apps.policy.services.loader import get_effective_policy
        return get_effective_policy(facts)

    def post_process_steps(self, steps, facts, policy):
        # TX-specific post-processing (mechanical awareness, CIBP, district overrides)
        # Currently lives in policy_kernel.py — will be extracted in Phase 3
        return steps

    def build_plan_payload(self, steps, facts, policy, geometry):
        raise NotImplementedError("TX payload building still lives in w3a_segmented views")
