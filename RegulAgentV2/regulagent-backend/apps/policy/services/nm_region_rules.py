"""
NM Region Rules Engine — region-based rules for New Mexico plugging operations.

Mirrors the TX DistrictRulesEngine interface but adapted for NM's COA figure
region/sub-area model per NMAC 19.15.25 and NMOCD plugging requirements.

Key NM differences from TX:
- Formation isolation is MANDATORY for every well (TX is field-specific)
- Excess: 50% flat (cased), 100% flat (open) — NOT depth-based like TX
- CIBP cap: 100 ft minimum cement (not TX 20 ft)
- Max plug spacing: 3000' cased, 2000' open
- Min sacks: 25
- WOC: 4 hours minimum
- Cement class cutoff: 6500' (Class C above, Class H at/below)

Version History:
- 2026.03.0: Initial implementation (POL-NM-001)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Path to NM plugging book packs relative to this file's package root
_PACKS_DIR = Path(__file__).parent.parent / "packs" / "nm" / "ocd"

# NM regulatory constants
_CEMENT_CLASS_CUTOFF_FT = 6500.0
_MIN_SACKS = 25
_CIBP_CAP_MIN_FT = 100
_MAX_SPACING_CASED_FT = 3000
_MAX_SPACING_OPEN_FT = 2000
_WOC_MIN_HOURS = 4
_COVERAGE_FT = 50  # ±50 ft around each formation top
_EXCESS_CASED = 0.50
_EXCESS_OPEN = 1.00


class NMRegionRulesEngine:
    """NM region-based rules engine mirroring TX DistrictRulesEngine.

    NM uses COA figure regions instead of TX districts.
    Formation isolation is MANDATORY for every NM well (unlike TX).

    Regions map to COA figures:
      north         -> Figure A  (nm_figure_a_north.json)
      south_artesia -> Figure B  (nm_figure_b_artesia.json)
      potash        -> Figure C  (nm_figure_c_potash.json)
      south_hobbs   -> Figure D  (nm_figure_d_hobbs.json)
    """

    def __init__(
        self,
        region: str = None,
        county: str = None,
        township: str = None,
        range_: str = None,
    ):
        """Initialize with region or auto-detect from county/township/range.

        Args:
            region: Explicit region key (e.g. 'north', 'south_hobbs').
                    If None, auto-detected from county/township/range.
            county: County name (lowercase, underscores for spaces).
            township: Township string (e.g. 'T20S') for split-county disambiguation.
            range_: Range string (e.g. 'R35E') for split-county disambiguation.
        """
        self._county_map: Optional[Dict[str, Any]] = None
        self._plugging_book: Optional[Dict[str, Any]] = None

        self._county_map = self._load_county_map()

        if region:
            self.region = region.lower()
        elif county:
            detection = self.detect_region(county, township, range_)
            self.region = detection["region"]
        else:
            self.region = None

        if self.region:
            self._plugging_book = self._load_plugging_book(self.region)

        # Resolve min_sacks from plugging book JSON; fall back to module default
        self._min_sacks = _MIN_SACKS
        if self._plugging_book:
            for section_key in ("pluggingChart", "plugging_chart"):
                section = self._plugging_book.get(section_key, {})
                for sub_key in ("casing", "openHole", "open_hole"):
                    sub = section.get(sub_key, {})
                    if "min_sacks" in sub:
                        self._min_sacks = sub["min_sacks"]
                        break
                if self._min_sacks != _MIN_SACKS:
                    break

        # Cache sub-area if determinable at init time
        self._sub_area: Optional[str] = None
        if county and self.region:
            self._sub_area = self.detect_sub_area(county, township, range_)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_county_map(self) -> Optional[Dict[str, Any]]:
        """Load the NM county-to-region mapping JSON."""
        path = _PACKS_DIR / "nm_county_region_map.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.warning("NM county region map not found at %s", path)
            return None
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse county region map: %s", exc)
            return None

    def _load_plugging_book(self, region: str) -> Optional[Dict[str, Any]]:
        """Load the plugging book JSON for the given region key."""
        if not self._county_map:
            return None

        region_meta = self._county_map.get("regions", {}).get(region)
        if not region_meta:
            logger.warning("Unknown NM region '%s'; no plugging book loaded.", region)
            return None

        book_filename = region_meta.get("plugging_book")
        if not book_filename:
            logger.warning("Region '%s' has no plugging_book entry in county map.", region)
            return None

        path = _PACKS_DIR / book_filename
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.warning("NM plugging book not found at %s", path)
            return None
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse plugging book %s: %s", book_filename, exc)
            return None

    # ------------------------------------------------------------------
    # Region / sub-area detection
    # ------------------------------------------------------------------

    def detect_region(
        self,
        county: str,
        township: str = None,
        range_: str = None,
    ) -> Dict[str, Any]:
        """Detect COA figure/region from county and optional township/range.

        Handles split counties (Eddy, Lea) that span multiple regions.  When
        township and range are provided they are used to disambiguate; otherwise
        the county default region is returned.

        Args:
            county: County name, case-insensitive. Spaces may be underscores.
            township: Optional township string, e.g. 'T20S'.
            range_: Optional range string, e.g. 'R35E'.

        Returns:
            Dict with keys:
              region       – region key (e.g. 'south_hobbs')
              coa_figure   – COA figure label (e.g. 'D')
              plugging_book – filename (e.g. 'nm_figure_d_hobbs.json')
        """
        if not self._county_map:
            logger.warning("County map unavailable; defaulting to 'north' region.")
            return self._build_region_result("north")

        county_key = county.lower().replace(" ", "_")
        county_data = self._county_map.get("county_map", {}).get(county_key)

        if not county_data:
            logger.warning(
                "County '%s' not found in NM county map; defaulting to 'north'.", county
            )
            return self._build_region_result("north")

        if county_data.get("region") == "split":
            resolved = self._resolve_split_county(county_key, county_data, township, range_)
        else:
            resolved = county_data["region"]

        return self._build_region_result(resolved)

    def _resolve_split_county(
        self,
        county_key: str,
        county_data: Dict[str, Any],
        township: Optional[str],
        range_: Optional[str],
    ) -> str:
        """Resolve region for a split county using township/range boundaries."""
        sub_regions = county_data.get("sub_regions", {})
        default_region = sub_regions.get("default", "north")

        if not township and not range_:
            logger.info(
                "Split county '%s' without township/range; using default region '%s'.",
                county_key,
                default_region,
            )
            return default_region

        twp_num = self._parse_township_number(township)
        rng_num = self._parse_range_number(range_)

        # Check each sub-region boundary (skip the 'default' key)
        for sub_key, sub_data in sub_regions.items():
            if sub_key == "default":
                continue
            if not isinstance(sub_data, dict):
                continue
            boundary = sub_data.get("boundary", {})
            if self._within_boundary(twp_num, rng_num, boundary):
                resolved = sub_data.get("region", default_region)
                logger.debug(
                    "Split county '%s' T%s R%s -> sub_region '%s' -> region '%s'",
                    county_key, township, range_, sub_key, resolved,
                )
                return resolved

        return default_region

    def _build_region_result(self, region: str) -> Dict[str, Any]:
        """Build the standardised detect_region return dict."""
        regions_meta = (self._county_map or {}).get("regions", {})
        meta = regions_meta.get(region, {})
        coa_figure_raw = meta.get("coa_figure", "")
        # Normalise to single letter, e.g. "Figure A" -> "A"
        coa_figure = coa_figure_raw.replace("Figure ", "").strip() if coa_figure_raw else ""
        return {
            "region": region,
            "coa_figure": coa_figure,
            "plugging_book": meta.get("plugging_book", ""),
        }

    def detect_sub_area(
        self,
        county: str,
        township: str = None,
        range_: str = None,
    ) -> Optional[str]:
        """Detect sub-area within a region (e.g. 'northwest_shelf' within Hobbs).

        Currently only the south_hobbs region has defined sub-areas.

        Args:
            county: County name.
            township: Optional township string for disambiguation.
            range_: Optional range string for disambiguation.

        Returns:
            Sub-area key string or None if not applicable.
        """
        region_result = self.detect_region(county, township, range_)
        region = region_result["region"]

        if region != "south_hobbs":
            return None

        if not self._county_map:
            return None

        hobbs_sub_areas = self._county_map.get("hobbs_sub_areas", {})
        if not township and not range_:
            return None

        twp_num = self._parse_township_number(township)
        rng_num = self._parse_range_number(range_)

        for sub_key, sub_data in hobbs_sub_areas.items():
            if not isinstance(sub_data, dict):
                continue
            if sub_key in ("description",):
                continue
            boundary = {
                "townships": sub_data.get("townships", []),
                "ranges": sub_data.get("ranges", []),
            }
            if self._within_boundary(twp_num, rng_num, boundary):
                logger.debug(
                    "Detected Hobbs sub-area '%s' for T%s R%s", sub_key, township, range_
                )
                return sub_key

        return None

    # ------------------------------------------------------------------
    # Township / range helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_township_number(township: Optional[str]) -> Optional[float]:
        """Extract numeric value from township string, e.g. 'T20S' -> 20.0."""
        if not township:
            return None
        cleaned = township.upper().replace("T", "").replace("S", "").replace("N", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _parse_range_number(range_: Optional[str]) -> Optional[float]:
        """Extract numeric value from range string, e.g. 'R35E' -> 35.0."""
        if not range_:
            return None
        cleaned = range_.upper().replace("R", "").replace("E", "").replace("W", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _within_boundary(
        twp_num: Optional[float],
        rng_num: Optional[float],
        boundary: Dict[str, Any],
    ) -> bool:
        """Check whether a township/range falls within a boundary spec.

        Boundary ranges are expressed as lists of strings like ['T16S-T24S'],
        ['R28E-R31E'].  Both township and range must match for a hit.
        """
        townships = boundary.get("townships", [])
        ranges = boundary.get("ranges", [])

        twp_match = True
        rng_match = True

        if townships and twp_num is not None:
            twp_match = False
            for spec in townships:
                if "-" in spec:
                    parts = spec.replace("T", "").replace("S", "").replace("N", "").split("-")
                    try:
                        lo, hi = sorted([float(parts[0]), float(parts[1])])
                        if lo <= twp_num <= hi:
                            twp_match = True
                            break
                    except (ValueError, IndexError):
                        continue

        if ranges and rng_num is not None:
            rng_match = False
            for spec in ranges:
                if "-" in spec:
                    parts = spec.replace("R", "").replace("E", "").replace("W", "").split("-")
                    try:
                        lo, hi = sorted([float(parts[0]), float(parts[1])])
                        if lo <= rng_num <= hi:
                            rng_match = True
                            break
                    except (ValueError, IndexError):
                        continue

        return twp_match and rng_match

    # ------------------------------------------------------------------
    # Formation isolation
    # ------------------------------------------------------------------

    def should_use_formation_based_plugging(self, **kwargs) -> bool:
        """Always True for NM — formation isolation is mandatory per NMAC 19.15.25."""
        return True

    # ------------------------------------------------------------------
    # Cement class
    # ------------------------------------------------------------------

    def get_cement_class(self, depth_ft: float) -> str:
        """Return NM cement class based on depth.

        Class C above 6,500 ft; Class H at or below 6,500 ft.
        Per NMAC 19.15.25 and all NM plugging books General Procedure 4.
        """
        return "H" if depth_ft >= _CEMENT_CLASS_CUTOFF_FT else "C"

    # ------------------------------------------------------------------
    # Sack count
    # ------------------------------------------------------------------

    def get_sack_count_from_chart(
        self,
        depth_ft: float,
        hole_type: str,
        diameter: float,
    ) -> float:
        """Lookup sack count from the NM plugging chart for this region.

        NM uses flat excess (50% cased, 100% open), not TX depth-based.
        Minimum 25 sacks enforced.

        Args:
            depth_ft: Mid-point depth of the plug in feet.
            hole_type: 'casing' or 'openHole'.
            diameter: Hole/casing diameter in inches.

        Returns:
            Sack count (float), minimum 25.
        """
        book = self._plugging_book or self._load_plugging_book(self.region or "north")
        if not book:
            logger.warning("No plugging book available; returning minimum %d sacks.", self._min_sacks)
            return float(self._min_sacks)

        chart_section = book.get("pluggingChart", {}).get(hole_type)
        if not chart_section:
            logger.warning(
                "No chart section '%s' in plugging book for region '%s'.",
                hole_type, self.region,
            )
            return float(self._min_sacks)

        diameter_str = self._match_diameter(diameter, chart_section.get("diameters", []))
        if not diameter_str:
            logger.warning(
                "Could not match diameter %.3f\" in %s chart; returning minimum sacks.",
                diameter, hole_type,
            )
            return float(self._min_sacks)

        diameters = chart_section.get("diameters", [])
        try:
            dia_index = diameters.index(diameter_str)
        except ValueError:
            return float(self._min_sacks)

        data_rows = chart_section.get("data", [])
        sacks = None

        # Find the first row whose depth_ft >= requested depth
        for row in data_rows:
            row_depth = float(row.get("depth_ft", 0))
            if row_depth >= depth_ft:
                values = row.get("values", [])
                if dia_index < len(values) and values[dia_index] is not None:
                    sacks = float(values[dia_index])
                break

        # Fallback: use deepest available row
        if sacks is None and data_rows:
            last_row = data_rows[-1]
            values = last_row.get("values", [])
            if dia_index < len(values) and values[dia_index] is not None:
                sacks = float(values[dia_index])

        if sacks is None:
            logger.warning(
                "No sack value found for %.3f\" @ %.0f ft in %s chart; returning minimum.",
                diameter, depth_ft, hole_type,
            )
            return float(self._min_sacks)

        return max(sacks, float(self._min_sacks))

    @staticmethod
    def _match_diameter(target: float, available: List[str]) -> Optional[str]:
        """Find the closest matching diameter string in the chart."""
        if not available:
            return None

        # Try exact string match first
        for s in available:
            try:
                if abs(float(s) - target) < 0.01:
                    return s
            except ValueError:
                continue

        # Find closest by numeric difference
        best = None
        best_diff = float("inf")
        for s in available:
            try:
                diff = abs(float(s) - target)
                if diff < best_diff:
                    best_diff = diff
                    best = s
            except ValueError:
                continue

        # Accept if within 1" of target
        return best if best_diff <= 1.0 else None

    # ------------------------------------------------------------------
    # Formation requirements
    # ------------------------------------------------------------------

    def get_region_formation_requirements(
        self,
        region: str = None,
        sub_area: str = None,
    ) -> List[Dict[str, Any]]:
        """Get required formations for the region/sub-area.

        Each returned formation dict contains:
          name, isolationOrder, tagRequired, typicalDepthRange, plugSpecification

        Args:
            region: Region key override; uses self.region if None.
            sub_area: Sub-area key override; uses self._sub_area if None.

        Returns:
            List of formation requirement dicts, sorted by isolationOrder.
        """
        effective_region = region or self.region
        effective_sub_area = sub_area or self._sub_area

        book = self._plugging_book
        if effective_region and effective_region != self.region:
            book = self._load_plugging_book(effective_region)

        if not book:
            logger.warning(
                "No plugging book for region '%s'; returning empty formation list.",
                effective_region,
            )
            return []

        sub_areas = book.get("subAreas", [])
        if not sub_areas:
            return []

        # Match sub-area by name fragment or key
        target_sub = effective_sub_area or ""
        matched_area = self._match_sub_area(sub_areas, target_sub)

        if not matched_area and sub_areas:
            # Default to first sub-area when no match
            matched_area = sub_areas[0]
            logger.debug(
                "No sub-area match for '%s' in region '%s'; using '%s'.",
                effective_sub_area, effective_region, matched_area.get("name"),
            )

        formations = matched_area.get("formations", []) if matched_area else []
        return sorted(formations, key=lambda f: f.get("isolationOrder", 99))

    @staticmethod
    def _match_sub_area(
        sub_areas: List[Dict[str, Any]],
        sub_area_key: str,
    ) -> Optional[Dict[str, Any]]:
        """Find a subArea entry by key or name fragment (case-insensitive)."""
        if not sub_area_key:
            return None

        key_lower = sub_area_key.lower().replace("_", " ")
        for area in sub_areas:
            name_lower = area.get("name", "").lower()
            figure = area.get("applicableFigure", "").lower()
            if key_lower in name_lower or key_lower in figure:
                return area

        return None

    # ------------------------------------------------------------------
    # Formation plug generation
    # ------------------------------------------------------------------

    def generate_formation_plugs(
        self,
        well_data: Dict[str, Any],
        region: str = None,
        sub_area: str = None,
    ) -> List[Dict[str, Any]]:
        """Generate formation plug specifications for the well.

        Algorithm:
          1. Load required formations for region/sub-area.
          2. Cross-reference with well's actual formation tops (if provided).
          3. Generate plug spec: ±50' around each formation top, tagged,
             correct cement class per depth.
          4. Apply NM excess (50% cased, 100% open) via sack chart lookup.
          5. Enforce minimum 25 sacks.

        When well_data includes 'formation_tops' (list of {name, depth_ft}),
        only formations that were actually penetrated are included and the
        actual drilled depths take precedence over typical depth ranges.

        Args:
            well_data: Dict that may contain:
              - formation_tops: list of {name: str, depth_ft: float}
              - hole_type: 'casing' or 'openHole' (default 'casing')
              - diameter: hole diameter in inches (default 7.0)
            region: Region override.
            sub_area: Sub-area override.

        Returns:
            List of plug spec dicts, deepest first, each containing:
              sequence, top_ft, bottom_ft, step_type, cement_class,
              formation, tag_required, sack_count, excess_factor,
              special_instructions, basis, notes
        """
        required_formations = self.get_region_formation_requirements(region, sub_area)
        if not required_formations:
            logger.warning(
                "No formation requirements found for region='%s' sub_area='%s'.",
                region or self.region, sub_area or self._sub_area,
            )
            # Don't return early — well-specific formation tops can still generate plugs below

        # Build a lookup from formation name -> actual depth from well data
        actual_tops: Dict[str, float] = {}
        for top in well_data.get("formation_tops", []):
            name = top.get("name", "")
            depth = top.get("depth_ft")
            if name and depth is not None:
                actual_tops[name.lower()] = float(depth)

        hole_type = well_data.get("hole_type", "casing")
        diameter = float(well_data.get("diameter", 7.0))

        # Build plug candidates with resolved depths
        candidates: List[Dict[str, Any]] = []
        for formation in required_formations:
            fm_name: str = formation.get("name", "")
            tag_required: bool = formation.get("tagRequired", True)
            plug_spec: Dict[str, Any] = formation.get("plugSpecification", {})
            depth_range: Dict[str, Any] = formation.get("typicalDepthRange", {})

            # Resolve depth: actual top > typical midpoint
            actual_depth = actual_tops.get(fm_name.lower())
            if actual_depth is not None:
                depth_ft = actual_depth
            elif actual_tops:
                # Well data exists but this formation not in it — skip
                logger.debug(
                    "Formation '%s' not found in well formation_tops; skipping.", fm_name
                )
                continue
            else:
                # No formation tops provided; use typical depth midpoint
                min_d = depth_range.get("min_ft", 1000.0)
                max_d = depth_range.get("max_ft", 2000.0)
                depth_ft = (min_d + max_d) / 2.0

            cement_class = self.get_cement_class(depth_ft)
            plug_top = max(depth_ft - _COVERAGE_FT, 50.0)
            plug_bottom = depth_ft + _COVERAGE_FT

            sack_count = self.get_sack_count_from_chart(depth_ft, hole_type, diameter)
            excess_factor = _EXCESS_CASED if hole_type == "casing" else _EXCESS_OPEN

            candidates.append({
                "_depth_ft": depth_ft,
                "formation": fm_name,
                "top_ft": plug_top,
                "bottom_ft": plug_bottom,
                "cement_class": cement_class,
                "tag_required": tag_required,
                "sack_count": sack_count,
                "excess_factor": excess_factor,
                "isolation_order": formation.get("isolationOrder", 99),
            })

        # Add plugs for any well formation tops NOT already covered by required formations
        # NM requires isolation of ALL penetrated formations, not just plugging-book ones
        covered_formations = {c["formation"].lower() for c in candidates}
        for fm_name, depth_ft in actual_tops.items():
            if fm_name.lower() not in covered_formations:
                cement_class = self.get_cement_class(depth_ft)
                plug_top = max(depth_ft - _COVERAGE_FT, 50.0)
                plug_bottom = depth_ft + _COVERAGE_FT
                sack_count = self.get_sack_count_from_chart(depth_ft, hole_type, diameter)
                excess_factor = _EXCESS_CASED if hole_type == "casing" else _EXCESS_OPEN
                candidates.append({
                    "_depth_ft": depth_ft,
                    "formation": fm_name.title(),
                    "top_ft": plug_top,
                    "bottom_ft": plug_bottom,
                    "cement_class": cement_class,
                    "tag_required": True,
                    "sack_count": sack_count,
                    "excess_factor": excess_factor,
                    "isolation_order": 99,
                })
                logger.info(
                    "Added formation plug for well-specific top: %s @ %.0f ft (not in plugging book)",
                    fm_name.title(), depth_ft,
                )

        # Sort deepest first
        candidates.sort(key=lambda c: c["_depth_ft"], reverse=True)

        plugs = []
        for seq, candidate in enumerate(candidates, start=1):
            depth_ft = candidate.pop("_depth_ft")
            candidate["sequence"] = seq
            candidate["step_type"] = "cement_plug"
            candidate["special_instructions"] = (
                f"Wait {_WOC_MIN_HOURS} hrs & Tag TOC"
                if candidate["tag_required"]
                else f"Wait {_WOC_MIN_HOURS} hrs"
            )
            candidate["basis"] = (
                f"NM COA Figure — NMAC 19.15.25 formation isolation requirement "
                f"(region: {region or self.region}, "
                f"sub_area: {sub_area or self._sub_area or 'default'})"
            )
            candidate["notes"] = (
                f"Formation plug for {candidate['formation']} @ {depth_ft:.0f} ft. "
                f"Cement: {candidate['cement_class']}, "
                f"{candidate['sack_count']:.0f} sacks, "
                f"{int(candidate['excess_factor'] * 100)}% excess."
            )
            plugs.append(candidate)

        logger.info(
            "Generated %d formation plugs for region='%s' sub_area='%s'.",
            len(plugs), region or self.region, sub_area or self._sub_area,
        )
        return plugs

    # ------------------------------------------------------------------
    # Procedures and special requirements
    # ------------------------------------------------------------------

    def get_mandatory_procedures(self) -> List[str]:
        """Return region-specific general procedures from the plugging book.

        Returns:
            List of procedure strings in format "N. <text>".
        """
        book = self._plugging_book
        if not book:
            return []

        procedures = []
        for proc in book.get("generalProcedures", []):
            number = proc.get("number", "")
            text = proc.get("text", "")
            if text:
                procedures.append(f"{number}. {text}")

        return procedures

    def get_special_requirements(self) -> Dict[str, Any]:
        """Return special requirements dict from the plugging book.

        Includes WOC time, max plug spacing, formation isolation rules,
        surface casing requirements, and region-specific items (e.g. potash
        cement rules for Figure C, or D1/D2 sub-figure selection for Figure D).

        Returns:
            Dict mirroring the plugging book's specialRequirements section,
            supplemented with derived NM constants.
        """
        book = self._plugging_book
        base: Dict[str, Any] = {
            "woc_min_hours": _WOC_MIN_HOURS,
            "min_sacks": self._min_sacks,
            "cibp_cap_min_ft": _CIBP_CAP_MIN_FT,
            "max_plug_spacing_cased_ft": _MAX_SPACING_CASED_FT,
            "max_plug_spacing_open_ft": _MAX_SPACING_OPEN_FT,
            "cement_class_cutoff_ft": _CEMENT_CLASS_CUTOFF_FT,
            "excess_cased": _EXCESS_CASED,
            "excess_open": _EXCESS_OPEN,
            "formation_isolation_mandatory": True,
        }

        if book:
            book_reqs = book.get("specialRequirements", {})
            # Merge book requirements, letting book values supplement the base
            merged = {**base, **book_reqs}
            return merged

        return base
