"""
Multi-state jurisdiction handler abstraction.

Defines the JurisdictionHandler Protocol and PolicyPackConfig dataclass,
which form the core abstraction layer for plugging in per-state logic
(TX, NM, etc.) into the shared regulatory kernel pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class PolicyPackConfig:
    """Configuration for a jurisdiction's policy pack."""

    pack_rel_path: str
    """Relative path to the YAML policy pack (e.g. 'nm_ocd_c103_base_policy_pack.yaml')."""

    policy_id: str
    """Dot-namespaced policy identifier (e.g. 'nm.c103', 'tx.w3a')."""

    form_name: str
    """Human-readable form name (e.g. 'C-103', 'W-3A')."""


@runtime_checkable
class JurisdictionHandler(Protocol):
    """Protocol for per-state jurisdiction handlers.

    Each state (TX, NM, …) implements this protocol to provide state-specific
    document expectations, geometry derivation, fact resolution, policy loading,
    step post-processing, and final plan payload construction.
    """

    @property
    def jurisdiction_code(self) -> str:
        """Two-letter state code (e.g. 'TX', 'NM')."""
        ...

    @property
    def policy_pack_config(self) -> PolicyPackConfig:
        """Policy pack configuration for this jurisdiction."""
        ...

    def get_expected_doc_types(self) -> list[str]:
        """Return the list of expected document type slugs for this jurisdiction.

        Examples:
            TX: ["w2", "w15", "gau"]
            NM: ["c105"]
        """
        ...

    def derive_geometry(
        self,
        extractions: list[dict],
        scraped_data: dict | None = None,
    ) -> dict:
        """Build the canonical geometry dict from raw extractions and optional scraped data."""
        ...

    def build_resolved_facts(
        self,
        well_info: dict,
        geometry: dict,
        extractions: list[dict],
    ) -> dict:
        """Build the kernel-ready resolved facts dict."""
        ...

    def load_effective_policy(
        self,
        facts: dict,
        geometry: dict | None = None,
    ) -> dict:
        """Load and return the merged effective policy (base pack + overlays)."""
        ...

    def post_process_steps(
        self,
        steps: list[dict],
        facts: dict,
        policy: dict,
    ) -> list[dict]:
        """Apply state-specific post-processing to the generated compliance steps."""
        ...

    def build_plan_payload(
        self,
        steps: list[dict],
        facts: dict,
        policy: dict,
        geometry: dict,
    ) -> dict:
        """Assemble and return the final plan snapshot payload."""
        ...
