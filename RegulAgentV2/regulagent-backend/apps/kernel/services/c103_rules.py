"""
NM C-103 Plugging Rules Engine

Deterministic plugging plan generation for New Mexico wells per NMAC 19.15.25.
Mirrors TX W3APluggingRules in RegulatoryAgent/RegulAgent/policies/oil_gas/tx/rrc/w3a/rules.py
but implements NM NMOCD C-103 requirements.

Key NM differences from TX W-3A:
- Formation isolation is MANDATORY for every well (TX is field-specific)
- CIBP cap = 100 ft (TX = 20 ft)
- Excess = 50% cased, 100% open (TX = depth-based)
- Minimum 25 sacks per plug
- Max spacing: 3000' cased, 2000' open — auto fill-plug insertion
- WOC = 4 hours all plugs
- Surface plug requires 30-min static observation
- Operation classification: spot/squeeze/circulate based on CBL data

Version History:
- 2026.03.0: Initial implementation
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from apps.kernel.services.c103_models import (
    C103PlugRow,
    C103PluggingPlan,
    NM_CASED_EXCESS,
    NM_CEMENT_CLASS_CUTOFF_FT,
    NM_CIBP_CAP_FT,
    NM_MAX_CASED_SPACING_FT,
    NM_MAX_OPEN_SPACING_FT,
    NM_MIN_SACKS,
    NM_WOC_HOURS,
    NM_OPEN_EXCESS,
)
from apps.policy.services.nm_region_rules import NMRegionRulesEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NM-specific constants (NMAC 19.15.25)
# ---------------------------------------------------------------------------

_COVERAGE_FT = 50                   # ±50 ft around formation/shoe tops
_CIBP_SET_OFFSET_FT = 50           # Set CIBP 50 ft above shallowest perf top
_CIBP_CAP_BAILER_FT = 35           # Bailer method CIBP cap (vs 100' standard)
_FILL_PLUG_LENGTH_FT = 100         # Fill plugs are 100' cement plugs
_SURFACE_PLUG_TOP_FT = 0.0         # Surface plug — top at grade
_SURFACE_PLUG_BOTTOM_FT = 50.0     # Surface plug — minimum 50' depth
_SQUEEZE_INSIDE_RATIO = 0.70       # 70% inside casing for squeeze
_SQUEEZE_OUTSIDE_RATIO = 0.30      # 30% annular for squeeze


class C103PluggingRules:
    """Deterministic NM C-103 plugging rules engine.

    Mirrors W3APluggingRules but implements NM NMAC 19.15.25 requirements.
    Formation isolation is mandatory for every NM well.

    Usage::

        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(well, options)
        errors = rules.validate_plan(well, plan)
    """

    def __init__(self, region_engine: NMRegionRulesEngine = None):
        """Initialize with optional pre-configured region engine.

        Args:
            region_engine: Pre-configured NMRegionRulesEngine. If None, a new
                engine will be instantiated per-well during plan generation.
        """
        self._region_engine = region_engine

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def generate_plugging_plan(
        self,
        well: Dict[str, Any],
        options: Dict[str, Any] = None,
    ) -> C103PluggingPlan:
        """Generate complete C-103 plugging plan.

        Algorithm (Architecture doc §7):
          1. Detect region & load plugging book
          2. Gather required formations (cross-reference well tops vs book)
          3. Place CIBP at shallowest perf top - 50'
          4. Generate CIBP cap (100' — NM-specific)
          5. Generate formation plugs (±50' per formation top, all tagged)
          6. Generate shoe plugs (surface, intermediate, production)
          7. Generate DUQW plug if applicable
          8. Generate surface plug (30-min static observation)
          9. Enforce spacing — walk bottom-to-top, insert fill plugs
         10. Calculate volumes — 50% excess cased, 100% open, min 25 sacks
         11. Classify operations — spot/squeeze/circulate based on CBL
         12. Generate narrative — numbered procedure steps for C-103 attachment

        Args:
            well: Well data dict. See module docstring for expected keys.
            options: Optional overrides. Keys:
                plugs_mode       – "isolated" | "combined" | "both"
                include_narrative – bool (default True)
                bailer_method    – bool; if True CIBP cap = 35' (default False)

        Returns:
            Populated C103PluggingPlan.
        """
        options = options or {}
        include_narrative: bool = options.get("include_narrative", True)
        bailer_method: bool = options.get("bailer_method", False)

        api_number: str = well.get("api_number", "unknown")
        logger.info("Generating C-103 plugging plan for API %s", api_number)

        # Step 1 — detect region
        region, sub_area, coa_figure = self._detect_region_and_load(well)
        logger.info(
            "API %s: region=%s sub_area=%s coa_figure=%s",
            api_number, region, sub_area, coa_figure,
        )

        # Initialise plan
        plan = C103PluggingPlan(
            api_number=api_number,
            region=region,
            sub_area=sub_area,
            coa_figure=coa_figure,
            field_name=well.get("field_name"),
            lease_name=well.get("lease_name"),
            operator=well.get("operator"),
            lease_type=well.get("lease_type"),
            duqw_ft=well.get("duqw_ft"),
            duqw_plug_required=bool(well.get("duqw_ft")),
        )

        sequence: List[C103PlugRow] = []

        # Steps 3–4 — CIBP + cap
        self._generate_cibp(well, sequence)
        self._generate_cibp_cap(well, sequence, bailer_method=bailer_method)

        # Step 5 — formation plugs (mandatory NM)
        self._generate_formation_plugs(well, sequence, region, sub_area)

        # Step 6 — casing shoe plugs
        self._generate_shoe_plugs(well, sequence)

        # Step 7 — DUQW
        self._generate_duqw_plug(well, sequence)

        # Step 8 — surface plug
        self._generate_surface_plug(well, sequence)

        plan.steps = sequence

        # Step 9 — spacing enforcement
        self._enforce_spacing(plan)

        # Step 10 — volume calculation
        self._calculate_volumes(plan, well)

        # Step 11 — operation classification
        self._classify_operations(plan, well)

        # Aggregate totals
        plan.calculate_totals()

        # Step 12 — narrative
        if include_narrative:
            self._generate_plan_narrative(plan)

        logger.info(
            "C-103 plan complete for API %s: %d plugs, %.0f total sacks",
            api_number,
            len(plan.steps),
            plan.total_cement_sacks or 0.0,
        )
        return plan

    # ------------------------------------------------------------------
    # Private — region detection
    # ------------------------------------------------------------------

    def _detect_region_and_load(
        self, well: Dict[str, Any]
    ) -> Tuple[str, Optional[str], Optional[str]]:
        """Detect region from well data (county, township, range).

        Returns:
            Tuple of (region, sub_area, coa_figure).
        """
        county: str = well.get("county", "")
        township: str = well.get("township", "")
        range_: str = well.get("range", "")

        if self._region_engine:
            engine = self._region_engine
        else:
            engine = NMRegionRulesEngine(
                county=county or None,
                township=township or None,
                range_=range_ or None,
            )

        if county:
            detection = engine.detect_region(county, township or None, range_ or None)
            region: str = detection.get("region", "north")
            coa_figure: str = detection.get("coa_figure", "")
        else:
            region = engine.region or "north"
            coa_figure = ""

        sub_area: Optional[str] = None
        if county:
            sub_area = engine.detect_sub_area(county, township or None, range_ or None)

        return region, sub_area, coa_figure or None

    # ------------------------------------------------------------------
    # Private — CIBP + cap
    # ------------------------------------------------------------------

    def _generate_cibp(
        self, well: Dict[str, Any], sequence: List[C103PlugRow]
    ) -> None:
        """Place CIBP at shallowest perforation top - 50'.

        The CIBP itself is a mechanical bridge plug (not cement). We add it
        to the sequence as a mechanical_plug step so that the narrative and
        spacing logic acknowledge its presence.
        """
        perforations = well.get("perforations", [])
        if not perforations:
            logger.debug("No perforations; skipping CIBP placement.")
            return

        shallowest_perf_top = min(p["top_ft"] for p in perforations)
        cibp_depth = max(shallowest_perf_top - _CIBP_SET_OFFSET_FT, 0.0)

        plug = C103PlugRow(
            top_ft=cibp_depth,
            bottom_ft=cibp_depth + 1.0,  # Mechanical plug — nominal 1' span
            cement_class="C",            # Placeholder; no cement for mech plug
            step_type="mechanical_plug",
            operation_type="spot",
            hole_type="cased",
            sacks_required=0.0,
            tag_required=False,
            wait_hours=0,
            regulatory_basis=self._get_regulatory_basis("cibp", cibp_depth),
            special_instructions=(
                f"Set CIBP at {cibp_depth:,.0f} ft "
                f"(shallowest perf top {shallowest_perf_top:,.0f}' - {_CIBP_SET_OFFSET_FT}')"
            ),
        )
        sequence.append(plug)
        logger.debug("CIBP placed at %.0f ft", cibp_depth)

    def _generate_cibp_cap(
        self,
        well: Dict[str, Any],
        sequence: List[C103PlugRow],
        bailer_method: bool = False,
    ) -> None:
        """100' cement cap above CIBP.

        NM CRITICAL: 100 ft (TX is only 20 ft).
        Bailer method reduces cap to 35 ft.
        """
        perforations = well.get("perforations", [])
        if not perforations:
            return

        shallowest_perf_top = min(p["top_ft"] for p in perforations)
        cibp_depth = max(shallowest_perf_top - _CIBP_SET_OFFSET_FT, 0.0)

        cap_length = _CIBP_CAP_BAILER_FT if bailer_method else NM_CIBP_CAP_FT
        cap_top = max(cibp_depth - cap_length, 0.0)
        cap_bottom = cibp_depth

        if cap_bottom <= cap_top:
            logger.warning(
                "CIBP cap would have zero/negative length (cibp_depth=%.0f); skipping.",
                cibp_depth,
            )
            return

        plug = C103PlugRow(
            top_ft=cap_top,
            bottom_ft=cap_bottom,
            cement_class=self._get_cement_class(cap_bottom),
            step_type="cibp_cap",
            operation_type="spot",
            hole_type="cased",
            sacks_required=NM_MIN_SACKS,  # Will be recalculated in _calculate_volumes
            tag_required=True,
            wait_hours=NM_WOC_HOURS,
            regulatory_basis=self._get_regulatory_basis("cibp_cap", cap_bottom),
            special_instructions=(
                f"{'Bailer' if bailer_method else 'Standard'} CIBP cap "
                f"{cap_length:.0f}' above bridge plug. "
                "NM: 100' minimum (NMAC 19.15.25.14.A.1)."
            ),
        )
        sequence.append(plug)
        logger.debug("CIBP cap: %.0f' - %.0f'", cap_top, cap_bottom)

    # ------------------------------------------------------------------
    # Private — formation plugs
    # ------------------------------------------------------------------

    def _generate_formation_plugs(
        self,
        well: Dict[str, Any],
        sequence: List[C103PlugRow],
        region: str,
        sub_area: Optional[str] = None,
    ) -> None:
        """Generate formation isolation plugs.

        NM CRITICAL: MANDATORY for every well (TX is field-specific).

        For each required formation in region:
          1. Find matching formation top in well data
          2. Place plug ±50' around formation top
          3. Set cement class based on depth
          4. Tag required = True
          5. Calculate sack count from chart (with NM excess)
        """
        # Build engine scoped to this region/sub-area
        engine = self._region_engine or NMRegionRulesEngine(region=region)

        formation_tops = well.get("formation_tops", [])
        casing_strings = well.get("casing_strings", [])

        # Determine dominant casing OD for sack chart lookup
        prod_casing = next(
            (c for c in casing_strings if c.get("type") == "production"), None
        )
        diameter_in = prod_casing["size_in"] if prod_casing else 7.0

        plug_specs = engine.generate_formation_plugs(
            well_data={
                "formation_tops": formation_tops,
                "hole_type": "casing",
                "diameter": diameter_in,
            },
            region=region,
            sub_area=sub_area,
        )

        if not plug_specs:
            logger.warning(
                "No formation plug specs returned for region=%s sub_area=%s. "
                "NM compliance will fail without formation plugs.",
                region,
                sub_area,
            )
            return

        for spec in plug_specs:
            formation_name: str = spec.get("formation", "Unknown Formation")
            top_ft: float = spec["top_ft"]
            bottom_ft: float = spec["bottom_ft"]
            cement_class: str = spec.get("cement_class", self._get_cement_class(bottom_ft))
            sacks: float = max(float(spec.get("sack_count", NM_MIN_SACKS)), float(NM_MIN_SACKS))

            # Determine hole type at this depth (below production shoe = open)
            hole_type = self._get_hole_type_at_depth(bottom_ft, casing_strings)

            plug = C103PlugRow(
                top_ft=top_ft,
                bottom_ft=bottom_ft,
                cement_class=cement_class,
                step_type="formation_plug",
                operation_type="spot",       # Will be overridden in _classify_operations
                hole_type=hole_type,
                sacks_required=sacks,
                formation_name=formation_name,
                tag_required=True,
                wait_hours=NM_WOC_HOURS,
                regulatory_basis=self._get_regulatory_basis("formation_plug", bottom_ft),
                special_instructions=f"Wait {NM_WOC_HOURS} hrs & Tag TOC. {spec.get('special_instructions', '')}".strip(),
                region_requirements=[spec.get("basis", "NMAC 19.15.25")],
            )
            sequence.append(plug)
            logger.debug(
                "Formation plug: %s @ %.0f'-%.0f'", formation_name, top_ft, bottom_ft
            )

    # ------------------------------------------------------------------
    # Private — shoe plugs
    # ------------------------------------------------------------------

    def _generate_shoe_plugs(
        self, well: Dict[str, Any], sequence: List[C103PlugRow]
    ) -> None:
        """Generate casing shoe plugs.

        ±50' around each casing shoe (surface, intermediate, production).
        """
        casing_strings = well.get("casing_strings", [])
        seen: set = set()

        for casing in casing_strings:
            casing_type: str = casing.get("type", "unknown")
            shoe_ft: float = float(casing.get("depth_ft", 0))
            size_in: float = float(casing.get("size_in", 7.0))

            dedup_key = (casing_type, round(shoe_ft, 1))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            top_ft = max(shoe_ft - _COVERAGE_FT, 0.0)
            bottom_ft = shoe_ft + _COVERAGE_FT

            hole_type = self._get_hole_type_at_depth(bottom_ft, casing_strings)

            plug = C103PlugRow(
                top_ft=top_ft,
                bottom_ft=bottom_ft,
                cement_class=self._get_cement_class(shoe_ft),
                step_type="shoe_plug",
                operation_type="spot",  # Overridden in _classify_operations
                hole_type=hole_type,
                sacks_required=NM_MIN_SACKS,  # Recalculated in _calculate_volumes
                casing_size_in=size_in,
                conduit_id=f"casing:{size_in}",
                tag_required=True,
                wait_hours=NM_WOC_HOURS,
                regulatory_basis=self._get_regulatory_basis("casing_shoe", shoe_ft),
                special_instructions=f"Wait {NM_WOC_HOURS} hrs & Tag TOC.",
            )
            sequence.append(plug)
            logger.debug(
                "Shoe plug: %s casing @ %.0f' -> %.0f'-%.0f'",
                casing_type, shoe_ft, top_ft, bottom_ft,
            )

    # ------------------------------------------------------------------
    # Private — DUQW plug
    # ------------------------------------------------------------------

    def _generate_duqw_plug(
        self, well: Dict[str, Any], sequence: List[C103PlugRow]
    ) -> None:
        """Generate DUQW protection plug if applicable.

        ±50' around DUQW depth per NMAC 19.15.25.
        """
        duqw_ft = well.get("duqw_ft")
        if not duqw_ft:
            return

        duqw_depth = float(duqw_ft)
        top_ft = max(duqw_depth - _COVERAGE_FT, 0.0)
        bottom_ft = duqw_depth + _COVERAGE_FT

        casing_strings = well.get("casing_strings", [])
        hole_type = self._get_hole_type_at_depth(bottom_ft, casing_strings)
        size_in = self._get_casing_od_at_depth(duqw_depth, casing_strings)

        plug = C103PlugRow(
            top_ft=top_ft,
            bottom_ft=bottom_ft,
            cement_class=self._get_cement_class(duqw_depth),
            step_type="duqw_plug",
            operation_type="spot",  # Overridden in _classify_operations
            hole_type=hole_type,
            sacks_required=NM_MIN_SACKS,  # Recalculated in _calculate_volumes
            casing_size_in=size_in,
            tag_required=True,
            wait_hours=NM_WOC_HOURS,
            regulatory_basis=self._get_regulatory_basis("duqw_plug", duqw_depth),
            special_instructions=(
                f"Protect DUQW at {duqw_depth:,.0f}'. "
                f"Wait {NM_WOC_HOURS} hrs & Tag TOC."
            ),
        )
        sequence.append(plug)
        logger.debug("DUQW plug: %.0f'-%.0f'", top_ft, bottom_ft)

    # ------------------------------------------------------------------
    # Private — surface plug
    # ------------------------------------------------------------------

    def _generate_surface_plug(
        self, well: Dict[str, Any], sequence: List[C103PlugRow]
    ) -> None:
        """Generate surface plug (always required).

        30-min static observation per NMAC 19.15.25.
        Operation type: circulate.
        """
        casing_strings = well.get("casing_strings", [])
        surface_casing = next(
            (c for c in casing_strings if c.get("type") == "surface"), None
        )
        size_in = float(surface_casing["size_in"]) if surface_casing else 13.375

        plug = C103PlugRow(
            top_ft=_SURFACE_PLUG_TOP_FT,
            bottom_ft=_SURFACE_PLUG_BOTTOM_FT,
            cement_class="C",           # Surface plug always Class C
            step_type="surface_plug",
            operation_type="circulate",  # NM: surface plug by circulation
            hole_type="cased",
            sacks_required=NM_MIN_SACKS,  # Recalculated in _calculate_volumes
            casing_size_in=size_in,
            conduit_id="surface_plug",
            tag_required=False,          # Surface plugs don't require tagging
            wait_hours=0,               # No WOC — 30-min static instead
            regulatory_basis=self._get_regulatory_basis("surface_plug", _SURFACE_PLUG_BOTTOM_FT),
            special_instructions=(
                "Hold 30-min static pressure observation after placing surface plug. "
                "Cut & cap all casing strings 3 ft below grade."
            ),
        )
        sequence.append(plug)
        logger.debug("Surface plug: %.0f'-%.0f'", _SURFACE_PLUG_TOP_FT, _SURFACE_PLUG_BOTTOM_FT)

    # ------------------------------------------------------------------
    # Private — spacing enforcement
    # ------------------------------------------------------------------

    def _enforce_spacing(self, plan: C103PluggingPlan) -> None:
        """Walk bottom-to-top, insert fill plugs where gap exceeds limits.

        Limits:
          - 3000' for cased hole
          - 2000' for open hole

        Fill plugs are 100' cement plugs centered in the gap.
        """
        spacing_types = {
            "cement_plug", "formation_plug", "shoe_plug",
            "surface_plug", "duqw_plug", "fill_plug",
        }

        inserted = True  # Loop until no more violations
        max_iterations = 20  # Guard against infinite loop
        iterations = 0

        while inserted and iterations < max_iterations:
            inserted = False
            iterations += 1

            # Re-sort each pass
            candidates = sorted(
                [s for s in plan.steps if s.step_type in spacing_types],
                key=lambda s: s.bottom_ft,
                reverse=True,
            )

            for i in range(len(candidates) - 1):
                lower = candidates[i]    # deeper plug
                upper = candidates[i + 1]  # shallower plug

                gap_ft = lower.top_ft - upper.bottom_ft
                if gap_ft <= 0:
                    continue  # Overlapping or adjacent — no gap

                # Use open-hole limit if either plug is in open hole
                if lower.hole_type == "open" or upper.hole_type == "open":
                    max_gap = NM_MAX_OPEN_SPACING_FT
                    gap_hole_type: str = "open"
                else:
                    max_gap = NM_MAX_CASED_SPACING_FT
                    gap_hole_type = "cased"

                if gap_ft > max_gap:
                    # Insert 100' fill plug centered in the gap
                    gap_center = upper.bottom_ft + gap_ft / 2.0
                    fill_top = gap_center - _FILL_PLUG_LENGTH_FT / 2.0
                    fill_bottom = gap_center + _FILL_PLUG_LENGTH_FT / 2.0

                    # Shift fill plug upward if it overlaps any existing plug
                    # (e.g. cibp_cap, mechanical_plug not in spacing_types)
                    for existing in plan.steps:
                        if existing is lower or existing is upper:
                            continue
                        # Check overlap: fill [fill_top, fill_bottom] vs existing [top, bottom]
                        if fill_top < existing.bottom_ft and fill_bottom > existing.top_ft:
                            # Place fill plug immediately above the existing plug
                            fill_bottom = existing.top_ft
                            fill_top = fill_bottom - _FILL_PLUG_LENGTH_FT

                    fill_plug = C103PlugRow(
                        top_ft=fill_top,
                        bottom_ft=fill_bottom,
                        cement_class=self._get_cement_class(fill_bottom),
                        step_type="fill_plug",
                        operation_type="spot",
                        hole_type=gap_hole_type,
                        sacks_required=NM_MIN_SACKS,  # Recalculated later
                        tag_required=True,
                        wait_hours=NM_WOC_HOURS,
                        regulatory_basis=self._get_regulatory_basis("fill_plug", fill_bottom),
                        special_instructions=(
                            f"Fill plug: gap of {gap_ft:,.0f}' exceeds "
                            f"{max_gap:,}' {gap_hole_type} maximum (NMAC 19.15.25). "
                            f"Wait {NM_WOC_HOURS} hrs & Tag TOC."
                        ),
                    )
                    plan.steps.append(fill_plug)
                    inserted = True
                    logger.debug(
                        "Inserted fill plug at %.0f'-%.0f' (gap=%.0f')",
                        fill_top, fill_bottom, gap_ft,
                    )
                    break  # Restart scan with new plug in place

        if iterations >= max_iterations:
            logger.warning(
                "Spacing enforcement hit max iterations (%d); plan may still have violations.",
                max_iterations,
            )

    # ------------------------------------------------------------------
    # Private — volume calculation
    # ------------------------------------------------------------------

    def _calculate_volumes(
        self, plan: C103PluggingPlan, well: Dict[str, Any]
    ) -> None:
        """Calculate cement volumes for each plug.

        Rules (NMAC 19.15.25):
          - 50% excess for cased hole
          - 100% excess for open hole
          - Minimum 25 sacks per plug
          - For squeeze: split inside/outside sacks (70/30)

        Sack count formula (approximate industry standard):
          base_sacks = interval_ft * pi/4 * (id_in^2 - pipe_od_in^2) / 144 / 1.18
          with_excess = base_sacks * (1 + excess_factor)
          sacks = max(with_excess, NM_MIN_SACKS)

        where 1.18 ft³/sack is approximate yield for Class C/H cement.
        """
        casing_strings = well.get("casing_strings", [])

        for plug in plan.steps:
            if plug.step_type == "mechanical_plug":
                continue  # No cement volume for mechanical plugs

            interval_ft = plug.bottom_ft - plug.top_ft
            mid_depth = (plug.top_ft + plug.bottom_ft) / 2.0

            # Casing OD and ID at plug mid-depth
            casing_od = self._get_casing_od_at_depth(mid_depth, casing_strings) or 7.0
            casing_id = self._estimate_casing_id(casing_od)

            # Tubing/drill-pipe OD assumed for cement placement (2.375" = standard tubing)
            pipe_od_in = 2.375

            excess_factor = NM_OPEN_EXCESS if plug.hole_type == "open" else NM_CASED_EXCESS
            plug.excess_factor = excess_factor

            # Approximate annular volume in cubic feet
            annular_area_sqin = (math.pi / 4.0) * (casing_id**2 - pipe_od_in**2)
            annular_area_sqft = annular_area_sqin / 144.0
            base_volume_cuft = interval_ft * annular_area_sqft

            # Cement yield: ~1.18 ft³/sack for Class C; ~1.15 for Class H
            yield_cuft_per_sack = 1.18 if plug.cement_class == "C" else 1.15
            base_sacks = base_volume_cuft / yield_cuft_per_sack

            total_sacks = base_sacks * (1.0 + excess_factor)
            plug.sacks_required = max(total_sacks, float(NM_MIN_SACKS))

            # Squeeze split
            if plug.operation_type == "squeeze":
                plug.inside_sacks = round(plug.sacks_required * _SQUEEZE_INSIDE_RATIO, 1)
                plug.outside_sacks = round(plug.sacks_required * _SQUEEZE_OUTSIDE_RATIO, 1)

            # Attach casing info if not already set
            if not plug.casing_size_in:
                plug.casing_size_in = casing_od
            if not plug.conduit_id:
                plug.conduit_id = f"casing:{casing_od}"

    # ------------------------------------------------------------------
    # Private — operation classification
    # ------------------------------------------------------------------

    def _classify_operations(
        self, plan: C103PluggingPlan, well: Dict[str, Any]
    ) -> None:
        """Classify each plug's operation type based on CBL data.

        Logic:
          - surface_plug -> circulate (always)
          - mechanical_plug -> spot (no cement)
          - Plug interval covered by 'poor_cement_intervals' -> squeeze
          - Otherwise -> spot
        """
        cbl_data: Dict[str, Any] = well.get("cbl_data") or {}
        poor_intervals: List[Tuple[float, float]] = [
            (float(lo), float(hi))
            for lo, hi in cbl_data.get("poor_cement_intervals", [])
        ]

        for plug in plan.steps:
            if plug.step_type == "surface_plug":
                plug.operation_type = "circulate"
                continue
            if plug.step_type == "mechanical_plug":
                plug.operation_type = "spot"
                continue

            # Check if plug interval overlaps a poor cement zone
            if self._overlaps_poor_cement(plug.top_ft, plug.bottom_ft, poor_intervals):
                plug.operation_type = "squeeze"
                # Recalculate squeeze split
                plug.inside_sacks = round(plug.sacks_required * _SQUEEZE_INSIDE_RATIO, 1)
                plug.outside_sacks = round(plug.sacks_required * _SQUEEZE_OUTSIDE_RATIO, 1)
            else:
                plug.operation_type = "spot"

    @staticmethod
    def _overlaps_poor_cement(
        top: float, bottom: float, poor_intervals: List[Tuple[float, float]]
    ) -> bool:
        """Return True if [top, bottom] overlaps any poor cement interval."""
        for lo, hi in poor_intervals:
            if top < hi and bottom > lo:
                return True
        return False

    # ------------------------------------------------------------------
    # Private — narrative generation
    # ------------------------------------------------------------------

    def _generate_plan_narrative(self, plan: C103PluggingPlan) -> None:
        """Generate numbered procedure narrative for C-103 attachment.

        Delegates to the plan's built-in generate_narrative() method, which
        produces readable prose sentences ordered deepest-to-shallowest.

        Example output:
          "1. Run in hole with tubing to 7,050 ft. Set CIBP at 7,050 ft."
          "2. Set cement plug from 6,950' to 7,050'. Class H cement, 38 sacks..."
        """
        plan.generate_narrative()

    # ------------------------------------------------------------------
    # Private — helpers
    # ------------------------------------------------------------------

    def _get_cement_class(self, depth_ft: float) -> str:
        """Class C above 6500', Class H at/below 6500'."""
        return "H" if depth_ft >= NM_CEMENT_CLASS_CUTOFF_FT else "C"

    def _get_regulatory_basis(self, plug_type: str, depth_ft: float) -> str:
        """Return NMAC citation for the plug type."""
        _BASIS_MAP: Dict[str, str] = {
            "cibp": "NMAC 19.15.25.14.A — CIBP placement required above perforations",
            "cibp_cap": (
                "NMAC 19.15.25.14.A.1 — 100 ft minimum cement cap above CIBP; "
                "cement class: " + self._get_cement_class(depth_ft)
            ),
            "formation_plug": (
                "NMAC 19.15.25 — Formation isolation mandatory for all NM wells; "
                "cement class: " + self._get_cement_class(depth_ft)
            ),
            "casing_shoe": (
                "NMAC 19.15.25 Surface Casing §(2) — ±50 ft cement plug at each casing shoe; "
                "cement class: " + self._get_cement_class(depth_ft)
            ),
            "duqw_plug": (
                "NMAC 19.15.25 Surface Casing §(1) — ±50 ft cement plug at DUQW; "
                "cement class: " + self._get_cement_class(depth_ft)
            ),
            "surface_plug": (
                "NMAC 19.15.25 — Surface plug required; 30-min static observation; "
                "Class C cement; cut & cap all strings 3 ft below grade"
            ),
            "fill_plug": (
                "NMAC 19.15.25 — Fill plug required: max spacing 3000' cased / 2000' open; "
                "cement class: " + self._get_cement_class(depth_ft)
            ),
        }
        return _BASIS_MAP.get(plug_type, "NMAC 19.15.25")

    @staticmethod
    def _get_hole_type_at_depth(
        depth_ft: float, casing_strings: List[Dict[str, Any]]
    ) -> str:
        """Return 'open' if depth is below all casing shoes, 'cased' otherwise."""
        if not casing_strings:
            return "cased"
        deepest_shoe = max(float(c.get("depth_ft", 0)) for c in casing_strings)
        return "open" if depth_ft > deepest_shoe else "cased"

    @staticmethod
    def _get_casing_od_at_depth(
        depth_ft: float, casing_strings: List[Dict[str, Any]]
    ) -> Optional[float]:
        """Return the OD of the innermost casing string covering depth_ft."""
        if not casing_strings:
            return None
        # Casings that reach or pass depth_ft, sorted by OD ascending (innermost last)
        covering = [
            c for c in casing_strings if float(c.get("depth_ft", 0)) >= depth_ft
        ]
        if not covering:
            return None
        # Return smallest OD — innermost string
        return min(float(c["size_in"]) for c in covering)

    @staticmethod
    def _estimate_casing_id(od_in: float) -> float:
        """Estimate casing ID from OD using typical wall-thickness ratios.

        This is a simplified approximation. Accurate calculations should use
        the actual casing weight/grade from the well's casing tally.
        """
        # Approximate API casing nominal wall thickness (inches)
        wall_thickness_map = {
            4.5: 0.205,
            5.0: 0.220,
            5.5: 0.244,
            7.0: 0.272,
            7.625: 0.328,
            8.625: 0.352,
            9.625: 0.395,
            10.75: 0.400,
            13.375: 0.430,
            16.0: 0.500,
            20.0: 0.635,
        }
        # Find closest OD in map
        best_od = min(wall_thickness_map.keys(), key=lambda k: abs(k - od_in))
        if abs(best_od - od_in) <= 1.0:
            wall = wall_thickness_map[best_od]
        else:
            wall = 0.30  # Conservative fallback
        return od_in - 2.0 * wall

    # ------------------------------------------------------------------
    # Public — validation
    # ------------------------------------------------------------------

    def validate_plan(
        self, well: Dict[str, Any], plan: C103PluggingPlan
    ) -> List[str]:
        """Post-generation validation.

        Delegates to plan.validate_c103_compliance() and adds well-specific
        checks (all required formation tops covered, CIBP not violated, etc.).

        Args:
            well: Original well data dict.
            plan: Generated C103PluggingPlan.

        Returns:
            List of violation/warning strings (empty if fully compliant).
        """
        errors: List[str] = plan.validate_c103_compliance()

        # All formation tops in well data should be covered by a formation plug
        for top in well.get("formation_tops", []):
            fm_name = top.get("name", "")
            fm_depth = float(top.get("depth_ft", 0))
            covered = any(
                p.step_type == "formation_plug"
                and p.top_ft <= fm_depth <= p.bottom_ft
                for p in plan.steps
            )
            if not covered:
                errors.append(
                    f"Formation top '{fm_name}' at {fm_depth:,.0f}' not covered by "
                    f"any formation plug (NMAC 19.15.25 — mandatory isolation)."
                )

        # All casing shoes should be covered by a shoe or cement plug
        for casing in well.get("casing_strings", []):
            shoe_ft = float(casing.get("depth_ft", 0))
            shoe_covered = any(
                p.top_ft <= shoe_ft <= p.bottom_ft
                for p in plan.steps
                if p.step_type in {"shoe_plug", "cement_plug", "formation_plug"}
            )
            if not shoe_covered:
                errors.append(
                    f"{casing.get('type', 'unknown').title()} casing shoe at "
                    f"{shoe_ft:,.0f}' not covered by any cement plug."
                )

        # DUQW coverage check
        duqw_ft = well.get("duqw_ft")
        if duqw_ft:
            duqw_depth = float(duqw_ft)
            duqw_covered = any(
                p.top_ft <= duqw_depth <= p.bottom_ft
                for p in plan.steps
                if p.step_type == "duqw_plug"
            )
            if not duqw_covered:
                errors.append(
                    f"DUQW at {duqw_depth:,.0f}' not covered by any DUQW plug."
                )

        if errors:
            logger.warning(
                "Plan validation for API %s: %d issues found.",
                well.get("api_number", "unknown"),
                len(errors),
            )
        else:
            logger.info(
                "Plan validation for API %s: PASS.",
                well.get("api_number", "unknown"),
            )

        return errors
