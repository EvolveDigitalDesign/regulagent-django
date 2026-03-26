"""
Tests for NMRegionRulesEngine — NM COA figure region-based plugging rules.

Covers:
- County-to-region mapping (all major counties, split counties)
- Sub-area detection (Hobbs NW Shelf, Central Basin Platform)
- Formation plug generation (cross-reference with well data, coverage, tags)
- Cement class and sack count chart lookups
- Special requirements (potash region cement rules)
- Mandatory procedures retrieval

Related to:
- POL-NM-001 — NM Region Rules Engine
- NMAC 19.15.25 — Well Plugging and Abandonment

API numbers use NM state code prefix: 30-
"""

import pytest
from apps.policy.services.nm_region_rules import NMRegionRulesEngine


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

def _engine(region=None, county=None, township=None, range_=None):
    """Convenience factory to reduce boilerplate in tests."""
    return NMRegionRulesEngine(
        region=region,
        county=county,
        township=township,
        range_=range_,
    )


# ---------------------------------------------------------------------------
# TestRegionDetection — county-to-region mapping
# ---------------------------------------------------------------------------

class TestRegionDetection:
    """Test county-to-region mapping for all major NM counties."""

    def test_san_juan_maps_to_north(self):
        """San Juan County (San Juan Basin gas) -> north (Figure A)."""
        engine = _engine(county="san_juan")
        assert engine.region == "north", (
            "San Juan County should map to north region (Figure A)"
        )

    def test_san_juan_detect_region_returns_figure_a(self):
        """detect_region() returns coa_figure 'A' for San Juan."""
        engine = _engine(county="san_juan")
        result = engine.detect_region("san_juan")
        assert result["region"] == "north"
        assert result["coa_figure"] == "A"
        assert "nm_figure_a_north.json" in result["plugging_book"]

    def test_chaves_maps_to_south_artesia(self):
        """Chaves County -> south_artesia (Figure B)."""
        engine = _engine(county="chaves")
        assert engine.region == "south_artesia", (
            "Chaves County should map to south_artesia region (Figure B)"
        )

    def test_chaves_detect_region_returns_figure_b(self):
        """detect_region() returns coa_figure 'B' for Chaves."""
        engine = _engine(county="chaves")
        result = engine.detect_region("chaves")
        assert result["region"] == "south_artesia"
        assert result["coa_figure"] == "B"

    def test_roosevelt_maps_to_south_hobbs(self):
        """Roosevelt County -> south_hobbs (Figure D)."""
        engine = _engine(county="roosevelt")
        assert engine.region == "south_hobbs", (
            "Roosevelt County should map to south_hobbs region (Figure D)"
        )

    def test_roosevelt_detect_region_returns_figure_d(self):
        """detect_region() returns coa_figure 'D' for Roosevelt."""
        engine = _engine(county="roosevelt")
        result = engine.detect_region("roosevelt")
        assert result["region"] == "south_hobbs"
        assert result["coa_figure"] == "D"

    def test_lea_default_maps_to_south_hobbs(self):
        """Lea County without township/range -> south_hobbs (default)."""
        engine = _engine(county="lea")
        assert engine.region == "south_hobbs", (
            "Lea County with no township/range should default to south_hobbs"
        )

    def test_eddy_default_maps_to_south_artesia(self):
        """Eddy County without township/range -> south_artesia (default)."""
        engine = _engine(county="eddy")
        assert engine.region == "south_artesia", (
            "Eddy County with no township/range should default to south_artesia"
        )

    def test_split_county_eddy_potash(self):
        """Eddy County with township/range inside potash enclave -> potash."""
        # Eddy potash boundary: T16S-T24S, R28E-R31E
        engine = _engine(county="eddy", township="T20S", range_="R30E")
        assert engine.region == "potash", (
            "Eddy County at T20S R30E should resolve to potash (Figure C)"
        )

    def test_split_county_lea_potash(self):
        """Lea County with township/range inside potash enclave -> potash."""
        # Lea potash boundary: T16S-T22S, R28E-R31E
        engine = _engine(county="lea", township="T18S", range_="R29E")
        assert engine.region == "potash", (
            "Lea County at T18S R29E should resolve to potash (Figure C)"
        )

    def test_split_county_eddy_hobbs(self):
        """Eddy County with east township/range -> south_hobbs."""
        # Eddy hobbs boundary: T16S-T26S, R32E-R36E
        engine = _engine(county="eddy", township="T20S", range_="R33E")
        assert engine.region == "south_hobbs", (
            "Eddy County at T20S R33E (east) should resolve to south_hobbs (Figure D)"
        )

    def test_county_case_insensitive(self):
        """County names are normalised case-insensitively."""
        engine_lower = _engine(county="san_juan")
        engine_upper = _engine(county="San Juan")
        assert engine_lower.region == engine_upper.region == "north"

    def test_unknown_county_defaults_to_north(self):
        """Completely unknown county defaults to north."""
        engine = _engine(county="atlantis_county")
        assert engine.region == "north", (
            "Unknown county should fall back to north region"
        )

    def test_bernalillo_maps_to_north(self):
        """Bernalillo County (Albuquerque area) -> north."""
        engine = _engine(county="bernalillo")
        assert engine.region == "north"

    def test_rio_arriba_maps_to_north(self):
        """Rio Arriba County (San Juan Basin) -> north."""
        engine = _engine(county="rio_arriba")
        assert engine.region == "north"

    def test_explicit_region_overrides_county(self):
        """Explicitly passing region= ignores county detection."""
        engine = NMRegionRulesEngine(region="potash")
        assert engine.region == "potash"

    def test_explicit_region_normalised_to_lowercase(self):
        """Region string is normalised to lowercase."""
        engine = NMRegionRulesEngine(region="SOUTH_HOBBS")
        assert engine.region == "south_hobbs"


# ---------------------------------------------------------------------------
# TestSubAreaDetection — Hobbs sub-area detection
# ---------------------------------------------------------------------------

class TestSubAreaDetection:
    """Test Hobbs sub-area detection for Lea/Eddy counties."""

    def test_hobbs_northwest_shelf(self):
        """Township/range in NW Shelf area -> northwest_shelf."""
        # northwest_shelf: T16S-T20S, R32E-R36E
        engine = _engine(county="lea", township="T18S", range_="R34E")
        sub = engine.detect_sub_area("lea", "T18S", "R34E")
        assert sub == "northwest_shelf", (
            "T18S R34E in Lea County should be northwest_shelf sub-area"
        )

    def test_hobbs_central_basin_platform(self):
        """Township/range in Central Basin Platform -> central_basin_platform."""
        # central_basin_platform: T20S-T26S, R32E-R38E
        engine = _engine(county="lea", township="T22S", range_="R35E")
        sub = engine.detect_sub_area("lea", "T22S", "R35E")
        assert sub == "central_basin_platform", (
            "T22S R35E in Lea County should be central_basin_platform sub-area"
        )

    def test_non_hobbs_returns_none(self):
        """Non-Hobbs region returns None for sub-area."""
        engine = _engine(county="san_juan")
        sub = engine.detect_sub_area("san_juan", "T28N", "R8W")
        assert sub is None, (
            "san_juan (north region) should return None for sub-area"
        )

    def test_south_artesia_returns_none(self):
        """Artesia region (south_artesia) returns None for sub-area."""
        engine = _engine(county="chaves")
        sub = engine.detect_sub_area("chaves", "T10S", "R25E")
        assert sub is None

    def test_hobbs_no_township_returns_none(self):
        """Hobbs county with no township/range returns None (ambiguous)."""
        engine = _engine(county="lea")
        sub = engine.detect_sub_area("lea")
        assert sub is None, (
            "Sub-area cannot be determined without township/range"
        )

    def test_sub_area_cached_on_init(self):
        """Sub-area detected at init is cached in _sub_area."""
        engine = _engine(county="lea", township="T18S", range_="R34E")
        assert engine._sub_area == "northwest_shelf"


# ---------------------------------------------------------------------------
# TestFormationPlugGeneration — plug generation logic
# ---------------------------------------------------------------------------

class TestFormationPlugGeneration:
    """Test formation plug generation for each region."""

    def test_north_region_formations_returned(self):
        """North region generates formation plugs from plugging book."""
        engine = _engine(region="north")
        # No actual formation tops -> falls back to typical depth midpoints
        plugs = engine.generate_formation_plugs(
            well_data={},
            sub_area="San Juan Basin",
        )
        assert len(plugs) > 0, "North region should produce formation plugs"

    def test_hobbs_nw_shelf_formations(self):
        """Hobbs NW Shelf generates plugs including San Andres, Glorieta."""
        engine = _engine(region="south_hobbs")
        # Provide formation tops that match D1 NW Shelf formations
        well_data = {
            "formation_tops": [
                {"name": "San Andres", "depth_ft": 4000.0},
                {"name": "Glorieta", "depth_ft": 4800.0},
                {"name": "Yeso", "depth_ft": 5200.0},
            ],
            "hole_type": "casing",
            "diameter": 7.0,
        }
        plugs = engine.generate_formation_plugs(
            well_data=well_data,
            sub_area="northwest_shelf",
        )
        formation_names = [p["formation"] for p in plugs]
        assert "San Andres" in formation_names, "San Andres should be in NW Shelf plugs"
        assert "Glorieta" in formation_names, "Glorieta should be in NW Shelf plugs"

    def test_formation_cross_reference_skips_missing(self):
        """Only generates plugs for formations present in well data."""
        engine = _engine(region="south_hobbs")
        # Only provide San Andres; Glorieta/Yeso are in book but not in well
        well_data = {
            "formation_tops": [
                {"name": "San Andres", "depth_ft": 4500.0},
            ],
            "hole_type": "casing",
            "diameter": 7.0,
        }
        plugs = engine.generate_formation_plugs(
            well_data=well_data,
            sub_area="northwest_shelf",
        )
        formation_names = [p["formation"] for p in plugs]
        assert "San Andres" in formation_names
        # Glorieta not in well_data so should be skipped
        assert "Glorieta" not in formation_names, (
            "Glorieta not in well formation_tops should be skipped"
        )

    def test_plug_coverage_50ft(self):
        """Each formation plug covers ±50 ft around formation top."""
        engine = _engine(region="south_hobbs")
        formation_depth = 5000.0
        well_data = {
            "formation_tops": [
                {"name": "San Andres", "depth_ft": formation_depth},
            ],
            "hole_type": "casing",
            "diameter": 7.0,
        }
        plugs = engine.generate_formation_plugs(
            well_data=well_data,
            sub_area="northwest_shelf",
        )
        assert len(plugs) == 1
        plug = plugs[0]
        expected_top = formation_depth - 50.0
        expected_bottom = formation_depth + 50.0
        assert plug["top_ft"] == pytest.approx(expected_top, abs=1.0), (
            f"Plug top should be {expected_top} ft (formation - 50 ft)"
        )
        assert plug["bottom_ft"] == pytest.approx(expected_bottom, abs=1.0), (
            f"Plug bottom should be {expected_bottom} ft (formation + 50 ft)"
        )

    def test_tag_required_on_formation_plugs(self):
        """All formation plugs have tag_required=True."""
        engine = _engine(region="south_hobbs")
        well_data = {
            "formation_tops": [
                {"name": "San Andres", "depth_ft": 4000.0},
                {"name": "Glorieta", "depth_ft": 5000.0},
            ],
            "hole_type": "casing",
            "diameter": 7.0,
        }
        plugs = engine.generate_formation_plugs(
            well_data=well_data,
            sub_area="northwest_shelf",
        )
        for plug in plugs:
            assert plug["tag_required"] is True, (
                f"Formation plug for {plug['formation']} should have tag_required=True"
            )

    def test_formation_isolation_always_mandatory(self):
        """should_use_formation_based_plugging() always True for NM."""
        for region in ("north", "south_artesia", "potash", "south_hobbs"):
            engine = _engine(region=region)
            assert engine.should_use_formation_based_plugging() is True, (
                f"Formation isolation must be mandatory for region '{region}'"
            )

    def test_plugs_sorted_deepest_first(self):
        """Plugs are returned deepest first (sequence 1 = deepest)."""
        engine = _engine(region="south_hobbs")
        well_data = {
            "formation_tops": [
                {"name": "San Andres", "depth_ft": 3500.0},
                {"name": "Abo", "depth_ft": 6000.0},
            ],
            "hole_type": "casing",
            "diameter": 7.0,
        }
        plugs = engine.generate_formation_plugs(
            well_data=well_data,
            sub_area="northwest_shelf",
        )
        assert len(plugs) == 2
        assert plugs[0]["bottom_ft"] > plugs[1]["top_ft"], (
            "First plug (seq 1) should be deeper than second plug"
        )

    def test_plugs_have_required_keys(self):
        """Every plug dict contains all required keys."""
        required_keys = {
            "sequence", "top_ft", "bottom_ft", "step_type", "cement_class",
            "formation", "tag_required", "sack_count", "excess_factor",
            "special_instructions", "basis", "notes",
        }
        engine = _engine(region="south_hobbs")
        well_data = {
            "formation_tops": [
                {"name": "San Andres", "depth_ft": 4000.0},
            ],
            "hole_type": "casing",
            "diameter": 7.0,
        }
        plugs = engine.generate_formation_plugs(
            well_data=well_data,
            sub_area="northwest_shelf",
        )
        assert len(plugs) == 1
        for key in required_keys:
            assert key in plugs[0], f"Plug is missing required key: '{key}'"

    def test_step_type_is_cement_plug(self):
        """All generated formation plugs have step_type='cement_plug'."""
        engine = _engine(region="south_hobbs")
        well_data = {
            "formation_tops": [
                {"name": "San Andres", "depth_ft": 4000.0},
            ],
            "hole_type": "casing",
            "diameter": 7.0,
        }
        plugs = engine.generate_formation_plugs(
            well_data=well_data,
            sub_area="northwest_shelf",
        )
        assert plugs[0]["step_type"] == "cement_plug"

    def test_no_formation_tops_uses_typical_depth(self):
        """Without formation_tops, plugs use typical depth midpoint."""
        engine = _engine(region="south_hobbs")
        # Pass empty well_data (no formation_tops) — should fall back to typical
        plugs = engine.generate_formation_plugs(
            well_data={},
            sub_area="northwest_shelf",
        )
        assert len(plugs) > 0, (
            "Should generate plugs using typical depths when no formation_tops provided"
        )

    def test_open_hole_excess_factor(self):
        """Open hole plugs have excess_factor=1.0 (100%)."""
        engine = _engine(region="south_hobbs")
        well_data = {
            "formation_tops": [
                {"name": "San Andres", "depth_ft": 3500.0},
            ],
            "hole_type": "openHole",
            "diameter": 8.75,
        }
        plugs = engine.generate_formation_plugs(
            well_data=well_data,
            sub_area="northwest_shelf",
        )
        assert plugs[0]["excess_factor"] == pytest.approx(1.0), (
            "Open hole plugs should use 100% excess factor"
        )

    def test_cased_hole_excess_factor(self):
        """Cased hole plugs have excess_factor=0.5 (50%)."""
        engine = _engine(region="south_hobbs")
        well_data = {
            "formation_tops": [
                {"name": "San Andres", "depth_ft": 4000.0},
            ],
            "hole_type": "casing",
            "diameter": 7.0,
        }
        plugs = engine.generate_formation_plugs(
            well_data=well_data,
            sub_area="northwest_shelf",
        )
        assert plugs[0]["excess_factor"] == pytest.approx(0.5), (
            "Cased hole plugs should use 50% excess factor"
        )


# ---------------------------------------------------------------------------
# TestCementClassAndSackCount — cement class and sack lookups
# ---------------------------------------------------------------------------

class TestCementClassAndSackCount:
    """Test cement class selection and sack count chart lookups."""

    # --- Cement class ---

    def test_cement_class_shallow(self):
        """Depth < 6500 ft -> Class C."""
        engine = _engine(region="south_hobbs")
        assert engine.get_cement_class(5000.0) == "C", (
            "Depth 5000 ft should return Class C cement"
        )

    def test_cement_class_deep(self):
        """Depth > 6500 ft -> Class H."""
        engine = _engine(region="south_hobbs")
        assert engine.get_cement_class(8000.0) == "H", (
            "Depth 8000 ft should return Class H cement"
        )

    def test_cement_class_boundary_at_cutoff(self):
        """Depth exactly 6500 ft -> Class H (boundary is inclusive for H)."""
        engine = _engine(region="south_hobbs")
        assert engine.get_cement_class(6500.0) == "H", (
            "Depth 6500 ft (at cutoff) should return Class H"
        )

    def test_cement_class_just_below_cutoff(self):
        """Depth just below 6500 ft -> Class C."""
        engine = _engine(region="south_hobbs")
        assert engine.get_cement_class(6499.9) == "C"

    def test_cement_class_consistent_across_regions(self):
        """Same depth gives same cement class across all NM regions."""
        for region in ("north", "south_artesia", "potash", "south_hobbs"):
            engine = _engine(region=region)
            assert engine.get_cement_class(4000.0) == "C", (
                f"4000 ft should be Class C in region '{region}'"
            )
            assert engine.get_cement_class(7000.0) == "H", (
                f"7000 ft should be Class H in region '{region}'"
            )

    # --- Sack count chart lookups ---

    def test_sack_count_cased_hole_7in(self):
        """Cased hole 7\" at 5000 ft returns chart value >= 25."""
        engine = _engine(region="south_hobbs")
        sacks = engine.get_sack_count_from_chart(
            depth_ft=5000.0,
            hole_type="casing",
            diameter=7.0,
        )
        # From chart: depth 5000, 7" -> 55 sacks
        assert sacks == pytest.approx(55.0, abs=1.0), (
            "Cased 7\" @ 5000 ft should return 55 sacks per chart"
        )

    def test_sack_count_open_hole_8in75(self):
        """Open hole 8.75\" at 5000 ft returns chart value >= 25."""
        engine = _engine(region="south_hobbs")
        sacks = engine.get_sack_count_from_chart(
            depth_ft=5000.0,
            hole_type="openHole",
            diameter=8.75,
        )
        # From chart: depth 5000, 8.75" -> 70 sacks
        assert sacks == pytest.approx(70.0, abs=1.0), (
            "Open hole 8.75\" @ 5000 ft should return 70 sacks per chart"
        )

    def test_minimum_25_sacks_enforced(self):
        """Even at very shallow depth, minimum 25 sacks is enforced."""
        engine = _engine(region="south_hobbs")
        sacks = engine.get_sack_count_from_chart(
            depth_ft=500.0,
            hole_type="casing",
            diameter=4.5,
        )
        assert sacks >= 25.0, "Minimum 25 sacks must always be enforced"

    def test_sack_count_interpolation_between_rows(self):
        """Depth between chart rows returns value from next row >= depth."""
        engine = _engine(region="south_hobbs")
        # Chart has rows at 3000 and 4000; depth 3500 should use 4000 ft row
        sacks_at_3500 = engine.get_sack_count_from_chart(
            depth_ft=3500.0,
            hole_type="casing",
            diameter=7.0,
        )
        sacks_at_4000 = engine.get_sack_count_from_chart(
            depth_ft=4000.0,
            hole_type="casing",
            diameter=7.0,
        )
        assert sacks_at_3500 == pytest.approx(sacks_at_4000, abs=1.0), (
            "Depth 3500 (between rows) should use the 4000 ft row value"
        )

    def test_sack_count_exact_depth_match(self):
        """Lookup at an exact chart row depth returns that row's value."""
        engine = _engine(region="south_hobbs")
        # Chart row at 3000 ft, 7" -> 45 sacks
        sacks = engine.get_sack_count_from_chart(
            depth_ft=3000.0,
            hole_type="casing",
            diameter=7.0,
        )
        assert sacks == pytest.approx(45.0, abs=1.0)

    def test_sack_count_deeper_than_deepest_row_uses_last(self):
        """Depth beyond deepest chart row falls back to last row value."""
        engine = _engine(region="south_hobbs")
        # Chart deepest row: 14000 ft, 7" -> 95 sacks
        sacks_at_15000 = engine.get_sack_count_from_chart(
            depth_ft=15000.0,
            hole_type="casing",
            diameter=7.0,
        )
        sacks_at_14000 = engine.get_sack_count_from_chart(
            depth_ft=14000.0,
            hole_type="casing",
            diameter=7.0,
        )
        assert sacks_at_15000 == pytest.approx(sacks_at_14000, abs=1.0), (
            "Depth beyond deepest chart row should fall back to last row"
        )

    def test_sack_count_diameter_closest_match(self):
        """Diameter matching rounds to nearest available diameter."""
        engine = _engine(region="south_hobbs")
        # 7.1\" should match 7\" column
        sacks_exact = engine.get_sack_count_from_chart(
            depth_ft=4000.0,
            hole_type="casing",
            diameter=7.0,
        )
        sacks_approx = engine.get_sack_count_from_chart(
            depth_ft=4000.0,
            hole_type="casing",
            diameter=7.1,
        )
        assert sacks_exact == pytest.approx(sacks_approx, abs=1.0)

    def test_sack_count_no_book_returns_minimum(self):
        """When no plugging book available, returns minimum 25 sacks."""
        engine = NMRegionRulesEngine()  # No region, no county
        # Clear all book-loading paths so the minimum-sack fallback is exercised.
        # get_sack_count_from_chart falls back to _load_plugging_book(self.region or "north")
        # when _plugging_book is None, so we also clear region and county_map.
        engine._plugging_book = None
        engine.region = None
        engine._county_map = None
        sacks = engine.get_sack_count_from_chart(
            depth_ft=5000.0,
            hole_type="casing",
            diameter=7.0,
        )
        assert sacks == 25.0


# ---------------------------------------------------------------------------
# TestSpecialRequirements — region-specific special requirements
# ---------------------------------------------------------------------------

class TestSpecialRequirements:
    """Test region-specific special requirements."""

    def test_potash_region_salt_water_cement_in_procedures(self):
        """Potash region procedure text references saturated salt water cement."""
        engine = _engine(region="potash")
        procedures = engine.get_mandatory_procedures()
        # Concatenate all procedure text and check for potash cement requirement
        all_text = " ".join(procedures).lower()
        assert "saturated salt water" in all_text, (
            "Potash region procedures must mention saturated salt water cement"
        )

    def test_potash_special_requirements_cement_mixing(self):
        """Potash special requirements include cement_mixing key."""
        engine = _engine(region="potash")
        reqs = engine.get_special_requirements()
        potash_protection = reqs.get("potash_protection", {})
        assert potash_protection.get("cement_mixing") == "saturated_salt_water", (
            "Potash region must require saturated_salt_water cement mixing"
        )

    def test_potash_cacl_limit_3_percent(self):
        """Potash region CaCl limited to 3% per specialRequirements."""
        engine = _engine(region="potash")
        reqs = engine.get_special_requirements()
        potash_protection = reqs.get("potash_protection", {})
        cacl_limit = potash_protection.get("cacl_limit_pct")
        assert cacl_limit == pytest.approx(3.0), (
            "Potash region CaCl limit should be 3.0%"
        )

    def test_non_potash_no_potash_protection_key(self):
        """Non-potash regions do not have potash_protection in requirements."""
        for region in ("north", "south_artesia", "south_hobbs"):
            engine = _engine(region=region)
            reqs = engine.get_special_requirements()
            assert "potash_protection" not in reqs, (
                f"Region '{region}' should not have potash_protection requirements"
            )

    def test_all_regions_have_formation_isolation_mandatory(self):
        """Every region declares formation_isolation_mandatory=True."""
        for region in ("north", "south_artesia", "potash", "south_hobbs"):
            engine = _engine(region=region)
            reqs = engine.get_special_requirements()
            assert reqs.get("formation_isolation_mandatory") is True, (
                f"Region '{region}' should have formation_isolation_mandatory=True"
            )

    def test_special_requirements_contain_nm_constants(self):
        """Base requirements always include NM regulatory constants."""
        engine = _engine(region="south_hobbs")
        reqs = engine.get_special_requirements()
        assert reqs["woc_min_hours"] == 4
        assert reqs["min_sacks"] == 25
        assert reqs["cibp_cap_min_ft"] == 100
        assert reqs["max_plug_spacing_cased_ft"] == 3000
        assert reqs["max_plug_spacing_open_ft"] == 2000
        assert reqs["cement_class_cutoff_ft"] == pytest.approx(6500.0)
        assert reqs["excess_cased"] == pytest.approx(0.50)
        assert reqs["excess_open"] == pytest.approx(1.00)


# ---------------------------------------------------------------------------
# TestMandatoryProcedures — general procedures retrieval
# ---------------------------------------------------------------------------

class TestMandatoryProcedures:
    """Test general procedures retrieval for each region."""

    def test_procedures_returned_as_list(self):
        """get_mandatory_procedures() returns a list."""
        engine = _engine(region="south_hobbs")
        procedures = engine.get_mandatory_procedures()
        assert isinstance(procedures, list), (
            "get_mandatory_procedures() must return a list"
        )

    def test_procedures_not_empty_hobbs(self):
        """south_hobbs region has at least 1 procedure."""
        engine = _engine(region="south_hobbs")
        procedures = engine.get_mandatory_procedures()
        assert len(procedures) > 0, "south_hobbs should have mandatory procedures"

    def test_procedures_not_empty_north(self):
        """north region has at least 1 procedure."""
        engine = _engine(region="north")
        procedures = engine.get_mandatory_procedures()
        assert len(procedures) > 0, "north region should have mandatory procedures"

    def test_procedures_not_empty_artesia(self):
        """south_artesia region has at least 1 procedure."""
        engine = _engine(region="south_artesia")
        procedures = engine.get_mandatory_procedures()
        assert len(procedures) > 0, "south_artesia should have mandatory procedures"

    def test_procedures_not_empty_potash(self):
        """potash region has at least 1 procedure."""
        engine = _engine(region="potash")
        procedures = engine.get_mandatory_procedures()
        assert len(procedures) > 0, "potash region should have mandatory procedures"

    def test_procedures_are_numbered_strings(self):
        """Each procedure is a non-empty string in format 'N. <text>'."""
        engine = _engine(region="south_hobbs")
        procedures = engine.get_mandatory_procedures()
        for proc in procedures:
            assert isinstance(proc, str) and len(proc) > 0, (
                "Each procedure must be a non-empty string"
            )
            assert ". " in proc, (
                f"Procedure should be formatted as 'N. text': {proc!r}"
            )

    def test_procedures_reference_woc_time(self):
        """Procedures include WOC time requirement (4 hours)."""
        for region in ("north", "south_artesia", "potash", "south_hobbs"):
            engine = _engine(region=region)
            procedures = engine.get_mandatory_procedures()
            all_text = " ".join(procedures).lower()
            assert "4 hours" in all_text or "4-hour" in all_text or "four" in all_text, (
                f"Region '{region}' procedures should reference 4-hour WOC requirement"
            )

    def test_no_book_returns_empty_list(self):
        """Engine with no plugging book returns empty procedures list."""
        engine = NMRegionRulesEngine()  # No region specified
        engine._plugging_book = None
        procedures = engine.get_mandatory_procedures()
        assert procedures == []


# ---------------------------------------------------------------------------
# TestTownshipRangeParsing — helper method unit tests
# ---------------------------------------------------------------------------

class TestTownshipRangeParsing:
    """Unit tests for township/range parsing helpers."""

    def test_parse_township_standard(self):
        """T20S -> 20.0."""
        assert NMRegionRulesEngine._parse_township_number("T20S") == pytest.approx(20.0)

    def test_parse_township_north(self):
        """T28N -> 28.0."""
        assert NMRegionRulesEngine._parse_township_number("T28N") == pytest.approx(28.0)

    def test_parse_township_none(self):
        """None -> None."""
        assert NMRegionRulesEngine._parse_township_number(None) is None

    def test_parse_township_invalid(self):
        """Non-numeric string -> None."""
        assert NMRegionRulesEngine._parse_township_number("invalid") is None

    def test_parse_range_standard(self):
        """R35E -> 35.0."""
        assert NMRegionRulesEngine._parse_range_number("R35E") == pytest.approx(35.0)

    def test_parse_range_west(self):
        """R12W -> 12.0."""
        assert NMRegionRulesEngine._parse_range_number("R12W") == pytest.approx(12.0)

    def test_parse_range_none(self):
        """None -> None."""
        assert NMRegionRulesEngine._parse_range_number(None) is None

    def test_within_boundary_match(self):
        """Township/range inside boundary spec returns True."""
        boundary = {
            "townships": ["T16S-T24S"],
            "ranges": ["R28E-R31E"],
        }
        assert NMRegionRulesEngine._within_boundary(20.0, 30.0, boundary) is True

    def test_within_boundary_no_match_range(self):
        """Range outside boundary returns False."""
        boundary = {
            "townships": ["T16S-T24S"],
            "ranges": ["R28E-R31E"],
        }
        assert NMRegionRulesEngine._within_boundary(20.0, 35.0, boundary) is False

    def test_within_boundary_no_match_township(self):
        """Township outside boundary returns False."""
        boundary = {
            "townships": ["T16S-T24S"],
            "ranges": ["R28E-R31E"],
        }
        assert NMRegionRulesEngine._within_boundary(30.0, 30.0, boundary) is False

    def test_within_boundary_boundary_edge_inclusive(self):
        """Boundary edges are inclusive."""
        boundary = {
            "townships": ["T16S-T24S"],
            "ranges": ["R28E-R31E"],
        }
        assert NMRegionRulesEngine._within_boundary(16.0, 28.0, boundary) is True
        assert NMRegionRulesEngine._within_boundary(24.0, 31.0, boundary) is True

    def test_within_boundary_empty_spec_matches_all(self):
        """Empty boundary spec (no constraints) matches everything."""
        boundary: dict = {}
        # Both twp and rng provided but no boundary -> twp_match=True, rng_match=True
        assert NMRegionRulesEngine._within_boundary(20.0, 30.0, boundary) is True


# ---------------------------------------------------------------------------
# TestRealWorldNMWells — integration-style tests with realistic data
# ---------------------------------------------------------------------------

class TestRealWorldNMWells:
    """Integration-style tests using realistic NM well data (API 30-)."""

    def test_lea_county_hobbs_well_nw_shelf(self):
        """
        Simulate a Lea County well in NW Shelf:
        API 30-025-..., T18S R34E -> south_hobbs / northwest_shelf.
        Formations: San Andres at 4200 ft, Glorieta at 5100 ft.
        """
        api14 = "30025123450000"  # NM (30) + Lea County FIPS (025)
        engine = _engine(county="lea", township="T18S", range_="R34E")

        assert engine.region == "south_hobbs"
        assert engine._sub_area == "northwest_shelf"

        well_data = {
            "api14": api14,
            "formation_tops": [
                {"name": "San Andres", "depth_ft": 4200.0},
                {"name": "Glorieta", "depth_ft": 5100.0},
            ],
            "hole_type": "casing",
            "diameter": 7.0,
        }
        plugs = engine.generate_formation_plugs(well_data=well_data)
        assert len(plugs) == 2

        # Deepest plug should be Glorieta (5100 ft)
        assert plugs[0]["formation"] == "Glorieta"
        assert plugs[0]["cement_class"] == "C"  # 5100 ft < 6500 ft cutoff

        # Shallower plug should be San Andres (4200 ft)
        assert plugs[1]["formation"] == "San Andres"

    def test_eddy_county_potash_well(self):
        """
        Simulate an Eddy County potash-area well:
        T20S R30E -> potash region.
        """
        engine = _engine(county="eddy", township="T20S", range_="R30E")

        assert engine.region == "potash"

        reqs = engine.get_special_requirements()
        assert reqs.get("potash_protection", {}).get("cement_mixing") == "saturated_salt_water"

    def test_san_juan_basin_shallow_gas_well(self):
        """
        Simulate San Juan Basin gas well (north region):
        API 30-045-... -> north, San Juan sub-area.
        Formations: Fruitland at 1800 ft.
        """
        api14 = "30045000010000"  # NM + San Juan County FIPS
        engine = _engine(county="san_juan")

        assert engine.region == "north"

        well_data = {
            "api14": api14,
            "formation_tops": [
                {"name": "Fruitland / Kirtland", "depth_ft": 1800.0},
            ],
            "hole_type": "casing",
            "diameter": 7.0,
        }
        plugs = engine.generate_formation_plugs(
            well_data=well_data,
            sub_area="San Juan Basin",
        )
        assert len(plugs) > 0
        # At 1800 ft, cement class should be C
        assert plugs[0]["cement_class"] == "C"

    def test_deep_cbp_well_class_h_cement(self):
        """
        Simulate a deep Central Basin Platform well:
        Lea County T22S R36E -> south_hobbs / central_basin_platform.
        Bone Spring at 9500 ft -> Class H cement.
        """
        engine = _engine(county="lea", township="T22S", range_="R36E")

        assert engine.region == "south_hobbs"

        well_data = {
            "formation_tops": [
                {"name": "San Andres", "depth_ft": 6000.0},
                {"name": "Bone Spring (1st, 2nd, 3rd)", "depth_ft": 9500.0},
            ],
            "hole_type": "casing",
            "diameter": 7.0,
        }
        plugs = engine.generate_formation_plugs(
            well_data=well_data,
            sub_area="central_basin_platform",
        )
        # Bone Spring at 9500 ft -> Class H
        bone_spring = next(p for p in plugs if "Bone Spring" in p["formation"])
        assert bone_spring["cement_class"] == "H", (
            "Bone Spring at 9500 ft must use Class H cement"
        )
