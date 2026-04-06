"""
NM C-103 Plugging Plan Models

Dataclass models for New Mexico NMOCD C-103 plugging plans.
Mirrors the TX W-3A models in RegulatoryAgent/RegulAgent/policies/oil_gas/tx/rrc/w3a/models.py
but adapted for NM's COA figure region model and NMAC 19.15.25 requirements.

Key NM differences from TX W-3A:
- Formation isolation is MANDATORY for every well (TX is field-specific)
- Excess: 50% flat (cased), 100% flat (open) — NOT depth-based like TX
- CIBP cap: 100 ft minimum cement (TX uses 20 ft)
- Max plug spacing: 3000' cased, 2000' open
- Min sacks: 25
- WOC: 4 hours minimum
- Cement class cutoff: 6500' (Class C above, Class H at/below)
- Region uses COA figures (A/B/C/D) instead of TX RRC districts

Version History:
- 2026.03.0: Initial implementation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal, Optional

# ---------------------------------------------------------------------------
# NM-specific module-level constants (NMAC 19.15.25)
# ---------------------------------------------------------------------------

NM_CASED_EXCESS = 0.50          # 50% excess for cased hole
NM_OPEN_EXCESS = 1.00           # 100% excess for open hole
NM_MIN_SACKS = 25               # Minimum 25 sacks per plug
NM_MAX_CASED_SPACING_FT = 3000  # Maximum spacing between plugs in cased hole
NM_MAX_OPEN_SPACING_FT = 2000   # Maximum spacing between plugs in open hole
NM_CIBP_CAP_FT = 100            # Minimum cement above CIBP (100 ft, vs TX 20 ft)
NM_WOC_HOURS = 4                # Minimum wait-on-cement hours
NM_CEMENT_CLASS_CUTOFF_FT = 6500  # Class C above, Class H at/below


# ---------------------------------------------------------------------------
# C103PlugRow
# ---------------------------------------------------------------------------

@dataclass
class C103PlugRow:
    """NM C-103 plugging step with NMOCD requirements.

    Mirrors W3APlugRow but uses NM-specific step types, operation types,
    flat excess percentages, and NMAC 19.15.25 compliance fields.
    """

    # Core dimensions
    top_ft: float
    bottom_ft: float
    cement_class: Literal["A", "B", "C", "G", "H"]

    # Step classification
    step_type: Literal[
        "cement_plug",
        "formation_plug",
        "cibp_cap",
        "shoe_plug",
        "surface_plug",
        "duqw_plug",
        "fill_plug",
        "mechanical_plug",
    ]

    # NM-specific: operation type for C-103 form
    # spot                = good cement behind casing per CBL
    # squeeze             = annular cement needed (perforations or casing defect)
    # circulate           = surface plug placed by circulation
    # perforate_and_squeeze = perforate production casing then squeeze cement behind pipe
    operation_type: Literal["spot", "squeeze", "circulate", "perforate_and_squeeze"]

    # NM-specific: hole type at plug interval
    hole_type: Literal["cased", "open"]

    # Cement volumes
    sacks_required: float

    inside_sacks: Optional[float] = None   # Squeeze: inside casing volume
    outside_sacks: Optional[float] = None  # Squeeze: annular (outside casing) volume

    # NM default: 50% cased, 100% open
    excess_factor: float = NM_CASED_EXCESS

    # NM-specific: numbered procedure narrative for C-103 form
    procedure_narrative: str = ""

    # Formation info (for formation_plug and shoe_plug steps)
    formation_name: Optional[str] = None

    # Casing / conduit
    casing_size_in: Optional[float] = None
    conduit_id: Optional[str] = None

    # Tag & wait requirements
    tag_required: bool = True   # NM: all plugs must be tagged
    wait_hours: int = NM_WOC_HOURS

    # Perforation interval (for perforate_and_squeeze operation)
    perf_top_ft: Optional[float] = None
    perf_bottom_ft: Optional[float] = None

    # Compliance
    nmac_compliant: bool = True
    region_requirements: List[str] = field(default_factory=list)
    regulatory_basis: str = ""    # NMAC citation (e.g. "NMAC 19.15.25.14.A.1")
    special_instructions: str = ""

    def __post_init__(self) -> None:
        if self.bottom_ft <= self.top_ft:
            raise ValueError(
                f"bottom_ft ({self.bottom_ft}) must be greater than top_ft ({self.top_ft})"
            )
        if self.sacks_required < 0:
            raise ValueError(f"sacks_required must be non-negative, got {self.sacks_required}")

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    @property
    def interval_length_ft(self) -> float:
        """Length of the plug interval in feet."""
        return self.bottom_ft - self.top_ft

    @property
    def plug_context(self) -> str:
        """Readable context description for this plug step."""
        contexts = {
            "cement_plug": (
                f"Cement plug from {self.top_ft:,.0f}' to {self.bottom_ft:,.0f}'"
            ),
            "formation_plug": (
                f"Formation plug across {self.formation_name or 'formation'} "
                f"from {self.top_ft:,.0f}' to {self.bottom_ft:,.0f}'"
            ),
            "cibp_cap": (
                f"CIBP cap at {self.top_ft:,.0f}' "
                f"(cement to {self.bottom_ft:,.0f}')"
            ),
            "shoe_plug": (
                f"Casing shoe plug from {self.top_ft:,.0f}' to {self.bottom_ft:,.0f}'"
            ),
            "surface_plug": (
                f"Surface plug from {self.top_ft:,.0f}' to {self.bottom_ft:,.0f}'"
            ),
            "duqw_plug": (
                f"DUQW plug from {self.top_ft:,.0f}' to {self.bottom_ft:,.0f}'"
            ),
            "fill_plug": (
                f"Fill plug from {self.top_ft:,.0f}' to {self.bottom_ft:,.0f}'"
            ),
            "mechanical_plug": (
                f"Mechanical plug at {self.top_ft:,.0f}'"
            ),
        }
        return contexts.get(
            self.step_type,
            f"Plug from {self.top_ft:,.0f}' to {self.bottom_ft:,.0f}'",
        )


# ---------------------------------------------------------------------------
# C103PluggingPlan
# ---------------------------------------------------------------------------

@dataclass
class C103PluggingPlan:
    """Complete C-103 plugging plan with NMOCD compliance.

    Mirrors W3APluggingPlan but uses NM's COA figure region model instead of
    TX RRC districts, and enforces NMAC 19.15.25 spacing/sack/CIBP rules.
    """

    # Well identification
    api_number: str
    region: str  # NM COA figure region key (e.g. 'north', 'south_hobbs')

    sub_area: Optional[str] = None      # NM sub-area within region (Hobbs sub-areas)
    coa_figure: Optional[str] = None    # COA figure label: 'A', 'B', 'C', or 'D'
    field_name: Optional[str] = None
    lease_name: Optional[str] = None
    operator: Optional[str] = None

    # NM-specific: surface ownership type
    lease_type: Optional[Literal["state", "fee", "federal", "indian"]] = None

    # Plan steps (ordered shallow-to-deep or deep-to-shallow; validation handles both)
    steps: List[C103PlugRow] = field(default_factory=list)

    # NM spacing constraints (from NMAC 19.15.25)
    max_plug_spacing_cased_ft: int = NM_MAX_CASED_SPACING_FT
    max_plug_spacing_open_ft: int = NM_MAX_OPEN_SPACING_FT

    # Well flags
    duqw_ft: Optional[float] = None       # Depth of usable quality water (ft)
    never_below_cibp: bool = True
    surface_plug_required: bool = True
    duqw_plug_required: bool = False

    # Computed totals (populated by calculate_totals())
    total_cement_sacks: Optional[float] = None
    total_cement_cost: Optional[float] = None

    # Numbered procedure narrative (populated by generate_narrative())
    procedure_narrative: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    @property
    def deepest_plug_ft(self) -> float:
        """Depth of the deepest plug bottom in feet."""
        if not self.steps:
            return 0.0
        return max(step.bottom_ft for step in self.steps)

    @property
    def shallowest_plug_ft(self) -> float:
        """Depth of the shallowest plug top in feet."""
        if not self.steps:
            return 0.0
        return min(step.top_ft for step in self.steps)

    @property
    def cement_plugs(self) -> List[C103PlugRow]:
        """All cement-type plug steps (excludes mechanical plugs)."""
        cement_types = {
            "cement_plug",
            "formation_plug",
            "shoe_plug",
            "surface_plug",
            "duqw_plug",
            "fill_plug",
        }
        return [s for s in self.steps if s.step_type in cement_types]

    @property
    def formation_plugs(self) -> List[C103PlugRow]:
        """All formation isolation plug steps."""
        return [s for s in self.steps if s.step_type == "formation_plug"]

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_plugs_by_type(self, plug_type: str) -> List[C103PlugRow]:
        """Return all steps matching the given step_type."""
        return [s for s in self.steps if s.step_type == plug_type]

    def validate_plug_spacing(self) -> List[str]:
        """Validate max plug spacing: 3000' cased, 2000' open.

        Walks cement plugs sorted bottom-to-top and checks the gap between
        the top of each plug and the bottom of the next shallower plug.
        Mechanical plugs and CIBP caps are excluded from spacing checks.

        Returns:
            List of violation message strings (empty if compliant).
        """
        violations: List[str] = []

        # Only spacing-relevant plug types
        spacing_types = {
            "cement_plug",
            "formation_plug",
            "shoe_plug",
            "surface_plug",
            "duqw_plug",
            "fill_plug",
        }
        candidates = [s for s in self.steps if s.step_type in spacing_types]

        if len(candidates) < 2:
            return violations

        # Sort bottom-to-top (deepest bottom_ft first)
        sorted_plugs = sorted(candidates, key=lambda s: s.bottom_ft, reverse=True)

        for i in range(len(sorted_plugs) - 1):
            lower = sorted_plugs[i]    # deeper plug
            upper = sorted_plugs[i + 1]  # shallower plug

            # Gap = bottom of the shallower plug minus top of the deeper plug
            gap_ft = upper.bottom_ft - lower.top_ft

            # Use the more restrictive open-hole limit if either plug is open
            if lower.hole_type == "open" or upper.hole_type == "open":
                max_spacing = self.max_plug_spacing_open_ft
                hole_label = "open hole"
            else:
                max_spacing = self.max_plug_spacing_cased_ft
                hole_label = "cased hole"

            if gap_ft > max_spacing:
                violations.append(
                    f"Plug spacing violation ({hole_label}): gap of {gap_ft:,.0f}' "
                    f"between {lower.plug_context} (top {lower.top_ft:,.0f}') "
                    f"and {upper.plug_context} (bottom {upper.bottom_ft:,.0f}') "
                    f"exceeds {max_spacing:,}' maximum (NMAC 19.15.25)."
                )

        return violations

    def validate_c103_compliance(self) -> List[str]:
        """Full NMAC 19.15.25 compliance validation.

        Checks:
        - Surface plug present (if required)
        - Formation plugs present (mandatory for NM)
        - Plug spacing within limits
        - Minimum sack count per plug (25 sacks)
        - CIBP cap >= 100 ft of cement above CIBP
        - DUQW plug present (if required)

        Returns:
            List of violation/warning message strings (empty if fully compliant).
        """
        errors: List[str] = []

        # 1. Surface plug
        if self.surface_plug_required and not self.get_plugs_by_type("surface_plug"):
            errors.append(
                "Surface plug required but not present in plan (NMAC 19.15.25)."
            )

        # 2. Formation plugs — mandatory for every NM well
        if not self.formation_plugs:
            errors.append(
                "Formation isolation plugs are mandatory for all NM wells "
                "(NMAC 19.15.25 — formation isolation required)."
            )

        # 3. Plug spacing
        errors.extend(self.validate_plug_spacing())

        # 4. Minimum sack count
        for step in self.cement_plugs:
            if step.sacks_required < NM_MIN_SACKS:
                errors.append(
                    f"Minimum sack violation: {step.plug_context} has "
                    f"{step.sacks_required:.0f} sacks; minimum is {NM_MIN_SACKS} "
                    f"(NMAC 19.15.25)."
                )

        # 5. CIBP cap cement requirement: >= 100 ft above each CIBP
        for cap in self.get_plugs_by_type("cibp_cap"):
            if cap.interval_length_ft < NM_CIBP_CAP_FT:
                errors.append(
                    f"CIBP cap at {cap.top_ft:,.0f}' has only "
                    f"{cap.interval_length_ft:.0f}' of cement; "
                    f"minimum is {NM_CIBP_CAP_FT}' (NMAC 19.15.25.14.A.1)."
                )

        # 6. DUQW plug
        if self.duqw_plug_required and not self.get_plugs_by_type("duqw_plug"):
            errors.append(
                "DUQW plug required (duqw_plug_required=True) but not present in plan."
            )

        # 7. Plug overlap check
        errors.extend(self._validate_plug_overlaps())

        return errors

    def _validate_plug_overlaps(self) -> List[str]:
        """Check for overlapping plugs within the same conduit.

        Surface plugs are excluded (they intentionally cover the full
        near-surface interval).  Overlaps are checked per conduit_id so
        that plugs inside different casing strings don't falsely trigger.

        Returns:
            List of overlap violation message strings.
        """
        errors: List[str] = []

        def _normalized(top: float, bot: float):
            return (min(top, bot), max(top, bot))

        def _overlaps(a: C103PlugRow, b: C103PlugRow, tol: float = 0.0) -> bool:
            at, ab = _normalized(a.top_ft, a.bottom_ft)
            bt, bb = _normalized(b.top_ft, b.bottom_ft)
            return not (ab <= bt + tol or bb <= at + tol)

        # Exclude surface plugs from overlap checks
        candidates = [
            s for s in self.steps if s.step_type != "surface_plug"
        ]

        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                plug_a = candidates[i]
                plug_b = candidates[j]

                # Only compare plugs in the same conduit
                if plug_a.conduit_id != plug_b.conduit_id:
                    continue

                if _overlaps(plug_a, plug_b):
                    conduit = plug_a.conduit_id or "unknown conduit"
                    errors.append(
                        f"Plug overlap in {conduit}: "
                        f"{plug_a.plug_context} ({plug_a.top_ft:,.0f}'-{plug_a.bottom_ft:,.0f}') "
                        f"overlaps {plug_b.plug_context} "
                        f"({plug_b.top_ft:,.0f}'-{plug_b.bottom_ft:,.0f}')."
                    )

        return errors

    def calculate_totals(self) -> None:
        """Sum all sack counts across cement plug steps."""
        total = 0.0
        for step in self.cement_plugs:
            total += step.sacks_required
        self.total_cement_sacks = total if total > 0 else None

    def generate_narrative(self) -> List[str]:
        """Generate numbered procedure narrative from plan steps.

        Each cement step becomes a readable prose sentence describing the
        plug interval, formation (if applicable), cement class, sack count,
        tag requirement, and WOC time.

        Returns:
            List of numbered procedure strings, e.g.:
            ["1. Set cement plug from 7,050' to 6,950' ...", ...]
        """
        narrative: List[str] = []
        # Sort deep-to-shallow for procedural order
        ordered = sorted(self.steps, key=lambda s: s.bottom_ft, reverse=True)

        for seq, step in enumerate(ordered, start=1):
            parts: List[str] = []

            # Opening: describe placement
            if step.step_type == "formation_plug" and step.formation_name:
                parts.append(
                    f"Set cement plug from {step.top_ft:,.0f}' to {step.bottom_ft:,.0f}' "
                    f"across {step.formation_name} formation"
                )
            elif step.step_type == "cibp_cap":
                parts.append(
                    f"Place {step.interval_length_ft:.0f}' cement cap above CIBP "
                    f"from {step.top_ft:,.0f}' to {step.bottom_ft:,.0f}'"
                )
            elif step.step_type == "shoe_plug":
                parts.append(
                    f"Set casing shoe plug from {step.top_ft:,.0f}' to {step.bottom_ft:,.0f}'"
                )
            elif step.step_type == "surface_plug":
                parts.append(
                    f"Set surface plug from {step.top_ft:,.0f}' to {step.bottom_ft:,.0f}'"
                )
            elif step.step_type == "duqw_plug":
                parts.append(
                    f"Set DUQW isolation plug from {step.top_ft:,.0f}' to {step.bottom_ft:,.0f}'"
                )
            elif step.step_type == "mechanical_plug":
                parts.append(f"Set mechanical plug at {step.top_ft:,.0f}'")
            else:
                parts.append(
                    f"Set cement plug from {step.top_ft:,.0f}' to {step.bottom_ft:,.0f}'"
                )

            # Cement class and sacks
            if step.step_type != "mechanical_plug":
                parts.append(
                    f"Class {step.cement_class} cement, {step.sacks_required:.0f} sacks"
                )
                if step.operation_type == "squeeze":
                    inside = f"{step.inside_sacks:.0f}" if step.inside_sacks is not None else "N/A"
                    outside = f"{step.outside_sacks:.0f}" if step.outside_sacks is not None else "N/A"
                    parts.append(
                        f"squeeze operation ({inside} sacks inside, {outside} sacks outside)"
                    )

            # Tag and WOC
            if step.tag_required:
                parts.append(
                    f"Tag cement at {step.top_ft:,.0f}'"
                )
            parts.append(f"WOC {step.wait_hours} hours")

            # Special instructions
            if step.special_instructions:
                parts.append(step.special_instructions)

            # Regulatory basis
            if step.regulatory_basis:
                parts.append(f"Per {step.regulatory_basis}")

            narrative.append(f"{seq}. {'. '.join(parts)}.")

        self.procedure_narrative = narrative
        return narrative
