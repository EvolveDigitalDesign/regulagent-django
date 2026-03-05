"""
NM-Specific Integration Tests for New Mexico Formula Engine.

Tests coverage:
- Jurisdiction detection from state field, API-14, and policy
- NM cement excess calculations (flat 50% cased, 100% open vs TX depth-based)
- NM CIBP cap requirements (100 ft vs TX 20 ft)
- Cross-jurisdiction validation (TX vs NM different calculations)
- NM constraint enforcement (max plug spacing, minimum requirements)
- Regression testing (ensure TX wells unchanged)

Related to:
- POL-003 - NM Formula Engine Implementation
- BE2-025 - NM Kernel Integration
- QA-002 - NM-Specific Test Cases

Primary Sources:
- NMAC 19.15.25 (Well Plugging and Abandonment)
- NMAC 19.15.16 (Drilling and Production - Casing/Cementing)
"""

import pytest
from apps.policy.services.formula_engine import (
    get_formula_engine,
    TexasFormulas,
    NewMexicoFormulas,
)
from apps.kernel.services.policy_kernel import _get_jurisdiction, get_cement_yield


class TestJurisdictionDetection:
    """Test jurisdiction detection from different data sources."""

    def test_jurisdiction_from_state_nm(self):
        """NM state field returns NM jurisdiction."""
        resolved_facts = {
            "state": {"value": "NM"},
            "api14": {"value": "30123456780000"},
        }
        policy = {}

        jurisdiction = _get_jurisdiction(resolved_facts, policy)
        assert jurisdiction == "NM"

    def test_jurisdiction_from_state_tx(self):
        """TX state field returns TX jurisdiction."""
        resolved_facts = {
            "state": {"value": "TX"},
            "api14": {"value": "42123456780000"},
        }
        policy = {}

        jurisdiction = _get_jurisdiction(resolved_facts, policy)
        assert jurisdiction == "TX"

    def test_jurisdiction_from_api14_nm(self):
        """API-14 starting with 30 returns NM (New Mexico state code)."""
        resolved_facts = {
            "api14": {"value": "30025123450000"},
        }
        policy = {}

        jurisdiction = _get_jurisdiction(resolved_facts, policy)
        assert jurisdiction == "NM"

    def test_jurisdiction_from_api14_tx(self):
        """API-14 starting with 42 returns TX (Texas state code)."""
        resolved_facts = {
            "api14": {"value": "42123456780000"},
        }
        policy = {}

        jurisdiction = _get_jurisdiction(resolved_facts, policy)
        assert jurisdiction == "TX"

    def test_jurisdiction_from_policy_takes_precedence(self):
        """Policy jurisdiction field takes precedence over facts."""
        resolved_facts = {
            "state": {"value": "TX"},
            "api14": {"value": "42123456780000"},
        }
        policy = {
            "jurisdiction": "NM",  # Override TX data with NM policy
        }

        jurisdiction = _get_jurisdiction(resolved_facts, policy)
        assert jurisdiction == "NM"

    def test_jurisdiction_case_insensitive(self):
        """Jurisdiction detection should handle lowercase input."""
        resolved_facts = {
            "state": {"value": "nm"},
        }
        policy = {}

        jurisdiction = _get_jurisdiction(resolved_facts, policy)
        assert jurisdiction == "NM"

    def test_jurisdiction_with_string_state(self):
        """Handle state as string (not dict with 'value' key)."""
        resolved_facts = {
            "state": "NM",
        }
        policy = {}

        jurisdiction = _get_jurisdiction(resolved_facts, policy)
        assert jurisdiction == "NM"

    def test_jurisdiction_defaults_to_tx(self):
        """Unknown jurisdiction defaults to TX for backward compatibility."""
        resolved_facts = {}
        policy = {}

        jurisdiction = _get_jurisdiction(resolved_facts, policy)
        assert jurisdiction == "TX"


class TestNMCementExcessFormulas:
    """Test NM cement excess calculations (flat rates vs TX depth-based)."""

    def test_nm_cement_excess_cased_hole(self):
        """NM uses 50% excess for cased hole (not depth-based like TX)."""
        nm_engine = NewMexicoFormulas()

        # NM should return 1.5x (50% excess) for all depths in cased hole
        assert nm_engine.cement_excess_for_hole_type("cased") == 1.5

        # Verify depth-agnostic behavior
        assert nm_engine.cement_depth_excess(1000) == 1.5
        assert nm_engine.cement_depth_excess(5000) == 1.5
        assert nm_engine.cement_depth_excess(10000) == 1.5

    def test_nm_cement_excess_open_hole(self):
        """NM uses 100% excess for open hole (2x multiplier)."""
        nm_engine = NewMexicoFormulas()

        # NM should return 2.0x (100% excess) for open hole
        assert nm_engine.cement_excess_for_hole_type("open") == 2.0

    def test_nm_vs_tx_cement_excess_comparison(self):
        """NM flat rate vs TX depth-based shows different results."""
        nm_engine = NewMexicoFormulas()
        tx_engine = TexasFormulas()

        # At 1000 ft:
        # - NM: 1.5x (flat 50% cased)
        # - TX: 1.1x (1.0 + 0.10 * 1.0 kft)
        nm_mult_1k = nm_engine.cement_depth_excess(1000)
        tx_mult_1k = tx_engine.cement_depth_excess(1000)
        assert nm_mult_1k == 1.5
        assert abs(tx_mult_1k - 1.1) < 0.001
        assert nm_mult_1k > tx_mult_1k  # NM requires more at shallow depth

        # At 5000 ft:
        # - NM: 1.5x (flat 50% cased)
        # - TX: 1.5x (1.0 + 0.10 * 5.0 kft)
        nm_mult_5k = nm_engine.cement_depth_excess(5000)
        tx_mult_5k = tx_engine.cement_depth_excess(5000)
        assert nm_mult_5k == 1.5
        assert abs(tx_mult_5k - 1.5) < 0.001
        assert abs(nm_mult_5k - tx_mult_5k) < 0.001  # Equal at 5000 ft

        # At 10000 ft:
        # - NM: 1.5x (flat 50% cased)
        # - TX: 2.0x (1.0 + 0.10 * 10.0 kft)
        nm_mult_10k = nm_engine.cement_depth_excess(10000)
        tx_mult_10k = tx_engine.cement_depth_excess(10000)
        assert nm_mult_10k == 1.5
        assert abs(tx_mult_10k - 2.0) < 0.001
        assert nm_mult_10k < tx_mult_10k  # TX requires more at deep depth

    def test_nm_cased_vs_open_hole_difference(self):
        """Verify NM open hole requires significantly more cement than cased."""
        nm_engine = NewMexicoFormulas()

        cased_mult = nm_engine.cement_excess_for_hole_type("cased")
        open_mult = nm_engine.cement_excess_for_hole_type("open")

        # Open hole should require 33% more than cased hole
        # 2.0 / 1.5 = 1.333...
        ratio = open_mult / cased_mult
        assert abs(ratio - 1.333) < 0.01


class TestNMCIBPCapRequirement:
    """Test NM CIBP cap coverage (100 ft vs TX 20 ft)."""

    def test_nm_cibp_cap_100ft(self):
        """NM requires 100 ft CIBP cap (not 20 ft like TX)."""
        nm_engine = NewMexicoFormulas()

        cibp_coverage = nm_engine.coverage_requirement_ft("cibp_cap")
        assert cibp_coverage == 100

    def test_tx_cibp_cap_0ft_baseline(self):
        """TX baseline: CIBP cap coverage is 0 ft (cap length handled separately)."""
        tx_engine = TexasFormulas()

        cibp_coverage = tx_engine.coverage_requirement_ft("cibp_cap")
        assert cibp_coverage == 0

    def test_nm_vs_tx_cibp_cap_difference(self):
        """NM requires 100 ft CIBP cap vs TX 0 ft coverage."""
        nm_engine = NewMexicoFormulas()
        tx_engine = TexasFormulas()

        nm_cibp = nm_engine.coverage_requirement_ft("cibp_cap")
        tx_cibp = tx_engine.coverage_requirement_ft("cibp_cap")

        assert nm_cibp == 100
        assert tx_cibp == 0
        assert nm_cibp > tx_cibp  # NM requires explicit coverage

    def test_nm_other_coverage_matches_tx(self):
        """Most NM coverage requirements match TX (50 ft standard)."""
        nm_engine = NewMexicoFormulas()
        tx_engine = TexasFormulas()

        # These should all be 50 ft in both jurisdictions
        plug_types = ["casing_shoe", "duqw", "production_horizon", "intermediate_shoe"]

        for plug_type in plug_types:
            nm_coverage = nm_engine.coverage_requirement_ft(plug_type)
            tx_coverage = tx_engine.coverage_requirement_ft(plug_type)
            assert nm_coverage == 50, f"NM {plug_type} should be 50 ft"
            assert tx_coverage == 50, f"TX {plug_type} should be 50 ft"
            assert nm_coverage == tx_coverage, f"{plug_type} coverage should match"


class TestCrossJurisdictionCalculations:
    """Test that same well data gives different calculations for TX vs NM."""

    def test_tx_vs_nm_different_cement_volumes(self):
        """Same well data with TX vs NM gives different cement volumes."""
        # Shared well parameters
        depth_ft = 8000.0
        plug_length_ft = 100.0
        casing_id_in = 6.094  # 7" casing

        # Calculate base volume (simplified capacity calculation)
        base_capacity_bbl_per_ft = (casing_id_in ** 2) / 1029.4
        base_volume_bbl = plug_length_ft * base_capacity_bbl_per_ft

        # TX calculation (depth-based excess)
        tx_engine = TexasFormulas()
        tx_multiplier = tx_engine.cement_depth_excess(depth_ft)
        tx_total_volume = base_volume_bbl * tx_multiplier

        # NM calculation (flat 50% cased hole excess)
        nm_engine = NewMexicoFormulas()
        nm_multiplier = nm_engine.cement_depth_excess(depth_ft)  # Defaults to cased
        nm_total_volume = base_volume_bbl * nm_multiplier

        # TX at 8000 ft: 1.0 + (0.10 * 8.0) = 1.8x
        assert abs(tx_multiplier - 1.8) < 0.001

        # NM: flat 1.5x
        assert abs(nm_multiplier - 1.5) < 0.001

        # Volumes should differ
        assert abs(tx_total_volume - nm_total_volume) > 0.1
        assert tx_total_volume > nm_total_volume  # TX requires more at 8000 ft

    def test_shallow_well_nm_requires_more(self):
        """At shallow depths, NM requires more cement than TX."""
        depth_ft = 2000.0

        tx_engine = TexasFormulas()
        nm_engine = NewMexicoFormulas()

        tx_mult = tx_engine.cement_depth_excess(depth_ft)
        nm_mult = nm_engine.cement_depth_excess(depth_ft)

        # TX: 1.0 + (0.10 * 2.0) = 1.2x
        assert abs(tx_mult - 1.2) < 0.001

        # NM: 1.5x
        assert abs(nm_mult - 1.5) < 0.001

        # NM requires more at shallow depth
        assert nm_mult > tx_mult

    def test_deep_well_tx_requires_more(self):
        """At deep depths, TX requires more cement than NM."""
        depth_ft = 12000.0

        tx_engine = TexasFormulas()
        nm_engine = NewMexicoFormulas()

        tx_mult = tx_engine.cement_depth_excess(depth_ft)
        nm_mult = nm_engine.cement_depth_excess(depth_ft)

        # TX: 1.0 + (0.10 * 12.0) = 2.2x
        assert abs(tx_mult - 2.2) < 0.001

        # NM: 1.5x
        assert abs(nm_mult - 1.5) < 0.001

        # TX requires more at deep depth
        assert tx_mult > nm_mult

    def test_tx_wells_unchanged_after_nm_implementation(self):
        """Ensure TX wells still use TX formulas (no regression)."""
        tx_engine = get_formula_engine("TX")

        # Verify TX formulas still work as expected
        assert isinstance(tx_engine, TexasFormulas)

        # Test depth excess
        assert abs(tx_engine.cement_depth_excess(5000) - 1.5) < 0.001
        assert abs(tx_engine.cement_depth_excess(10000) - 2.0) < 0.001

        # Test coverage
        assert tx_engine.coverage_requirement_ft("casing_shoe") == 50
        assert tx_engine.coverage_requirement_ft("cibp_cap") == 0

        # Test cement class
        assert tx_engine.cement_class_for_depth(5000) == "C"
        assert tx_engine.cement_class_for_depth(7000) == "H"


class TestNMConstraintEnforcement:
    """Test NM-specific constraint parameters."""

    def test_nm_max_plug_spacing_cased(self):
        """NM enforces 3000 ft max between plugs (cased hole)."""
        nm_engine = NewMexicoFormulas()
        params = nm_engine.get_formula_parameters()

        nm_specific = params.get("nm_specific_parameters", {})
        assert nm_specific.get("max_cased_spacing_ft") == 3000

    def test_nm_max_plug_spacing_open(self):
        """NM enforces 2000 ft max between plugs (open hole)."""
        nm_engine = NewMexicoFormulas()
        params = nm_engine.get_formula_parameters()

        nm_specific = params.get("nm_specific_parameters", {})
        assert nm_specific.get("max_open_spacing_ft") == 2000

    def test_nm_min_sacks_requirement(self):
        """NM requires minimum 25 sacks OR 100 ft (whichever greater)."""
        nm_engine = NewMexicoFormulas()
        params = nm_engine.get_formula_parameters()

        nm_specific = params.get("nm_specific_parameters", {})
        assert nm_specific.get("min_sacks") == 25

    def test_nm_woc_time_requirement(self):
        """NM requires 4 hour WOC (wait on cement) minimum."""
        nm_engine = NewMexicoFormulas()
        params = nm_engine.get_formula_parameters()

        nm_specific = params.get("nm_specific_parameters", {})
        assert nm_specific.get("woc_hours") == 4

    def test_nm_cement_standing_time(self):
        """NM requires 8-18 hour cement standing time depending on method."""
        nm_engine = NewMexicoFormulas()
        params = nm_engine.get_formula_parameters()

        nm_specific = params.get("nm_specific_parameters", {})
        assert nm_specific.get("cement_standing_hours_min") == 8
        assert nm_specific.get("cement_standing_hours_max") == 18

    def test_nm_spacing_stricter_for_open_hole(self):
        """NM open hole spacing (2000 ft) is stricter than cased (3000 ft)."""
        nm_engine = NewMexicoFormulas()
        params = nm_engine.get_formula_parameters()

        nm_specific = params.get("nm_specific_parameters", {})
        max_cased = nm_specific.get("max_cased_spacing_ft")
        max_open = nm_specific.get("max_open_spacing_ft")

        assert max_open < max_cased
        assert max_cased - max_open == 1000  # 1000 ft difference


class TestNMFormulaMetadata:
    """Test NM formula metadata and citations."""

    def test_nm_jurisdiction_identifier(self):
        """NM engine should identify as New Mexico."""
        nm_engine = NewMexicoFormulas()
        assert nm_engine.jurisdiction == "NM"

    def test_nm_effective_date(self):
        """NM effective date should be 2018-06-26 (last NMAC amendment)."""
        nm_engine = NewMexicoFormulas()
        assert nm_engine.effective_date == "2018-06-26"

    def test_nm_primary_citation(self):
        """NM primary citation should reference NMAC 19.15.25."""
        nm_engine = NewMexicoFormulas()
        assert nm_engine.primary_citation == "NMAC 19.15.25"

    def test_nm_parameters_documentation(self):
        """NM parameters should include clear documentation."""
        nm_engine = NewMexicoFormulas()
        params = nm_engine.get_formula_parameters()

        # Verify key sections exist
        assert "jurisdiction" in params
        assert "effective_date" in params
        assert "primary_citation" in params
        assert "cement_excess" in params
        assert "coverage" in params
        assert "cement_class" in params
        assert "nm_specific_parameters" in params

    def test_nm_cement_excess_notes(self):
        """NM cement excess should note it's flat rate, not depth-based."""
        nm_engine = NewMexicoFormulas()
        params = nm_engine.get_formula_parameters()

        cement_excess = params.get("cement_excess", {})
        assert cement_excess.get("type") == "flat (hole-type based)"
        assert "note" in cement_excess
        assert "depth-based" in cement_excess.get("note", "").lower()

    def test_nm_cibp_cap_note(self):
        """NM coverage parameters should note CIBP cap is 100 ft vs TX 20 ft."""
        nm_engine = NewMexicoFormulas()
        params = nm_engine.get_formula_parameters()

        coverage = params.get("coverage", {})
        assert "note" in coverage
        note = coverage.get("note", "")
        assert "100" in note
        assert "Texas" in note or "TX" in note


class TestFactoryFunctionNM:
    """Test factory function for NM engine retrieval."""

    def test_get_nm_engine_via_factory(self):
        """Factory should return NewMexicoFormulas for NM."""
        engine = get_formula_engine("NM")
        assert isinstance(engine, NewMexicoFormulas)

    def test_get_nm_engine_case_insensitive(self):
        """Factory should handle lowercase 'nm'."""
        engine = get_formula_engine("nm")
        assert isinstance(engine, NewMexicoFormulas)

    def test_get_nm_engine_with_config(self):
        """Factory should pass config to NM engine."""
        config = {
            "cased_hole_excess": 0.60,  # Override to 60% excess
        }
        engine = get_formula_engine("NM", config)

        # Verify override was applied
        assert engine.cement_excess_for_hole_type("cased") == 1.6  # 1.0 + 0.6


class TestNMCementClass:
    """Test NM cement class selection (uses industry standard like TX)."""

    def test_nm_shallow_well_class_c(self):
        """NM shallow wells (<6500 ft) use Class C."""
        nm_engine = NewMexicoFormulas()

        assert nm_engine.cement_class_for_depth(3000) == "C"
        assert nm_engine.cement_class_for_depth(5000) == "C"
        assert nm_engine.cement_class_for_depth(6499) == "C"

    def test_nm_deep_well_class_h(self):
        """NM deep wells (>=6500 ft) use Class H."""
        nm_engine = NewMexicoFormulas()

        assert nm_engine.cement_class_for_depth(6500) == "H"
        assert nm_engine.cement_class_for_depth(8000) == "H"
        assert nm_engine.cement_class_for_depth(12000) == "H"

    def test_nm_cement_class_matches_tx_default(self):
        """NM uses same cement class cutoff as TX (industry standard)."""
        nm_engine = NewMexicoFormulas()
        tx_engine = TexasFormulas()

        test_depths = [3000, 5000, 6500, 8000, 10000]

        for depth in test_depths:
            nm_class = nm_engine.cement_class_for_depth(depth)
            tx_class = tx_engine.cement_class_for_depth(depth)
            assert nm_class == tx_class, f"Cement class should match at {depth} ft"


class TestCementYield:
    """Test cement yield calculations (shared between jurisdictions)."""

    def test_class_c_yield(self):
        """Class C cement yield is 1.32 ft³/sack."""
        yield_c = get_cement_yield("C")
        assert abs(yield_c - 1.32) < 0.001

    def test_class_h_yield(self):
        """Class H cement yield is 1.06 ft³/sack."""
        yield_h = get_cement_yield("H")
        assert abs(yield_h - 1.06) < 0.001

    def test_cement_yield_case_insensitive(self):
        """Cement yield lookup should be case-insensitive."""
        assert get_cement_yield("c") == get_cement_yield("C")
        assert get_cement_yield("h") == get_cement_yield("H")

    def test_unknown_cement_class_defaults_to_c(self):
        """Unknown cement classes default to Class C yield."""
        yield_unknown = get_cement_yield("UNKNOWN")
        yield_c = get_cement_yield("C")
        assert yield_unknown == yield_c


class TestNMConfigurationOverrides:
    """Test NM configuration override functionality."""

    def test_custom_cased_hole_excess(self):
        """Custom cased hole excess should be applied."""
        config = {"cased_hole_excess": 0.75}  # 75% excess
        nm_engine = NewMexicoFormulas(config)

        assert nm_engine.cement_excess_for_hole_type("cased") == 1.75

    def test_custom_open_hole_excess(self):
        """Custom open hole excess should be applied."""
        config = {"open_hole_excess": 1.25}  # 125% excess
        nm_engine = NewMexicoFormulas(config)

        assert nm_engine.cement_excess_for_hole_type("open") == 2.25

    def test_custom_cibp_cap_coverage(self):
        """Custom CIBP cap coverage should be applied."""
        config = {"coverage_defaults": {"cibp_cap": 150}}
        nm_engine = NewMexicoFormulas(config)

        assert nm_engine.coverage_requirement_ft("cibp_cap") == 150

    def test_custom_max_spacing(self):
        """Custom max spacing parameters should be applied."""
        config = {
            "max_cased_spacing_ft": 3500,
            "max_open_spacing_ft": 2500,
        }
        nm_engine = NewMexicoFormulas(config)
        params = nm_engine.get_formula_parameters()

        nm_specific = params.get("nm_specific_parameters", {})
        assert nm_specific.get("max_cased_spacing_ft") == 3500
        assert nm_specific.get("max_open_spacing_ft") == 2500

    def test_custom_woc_time(self):
        """Custom WOC time should be applied."""
        config = {"woc_hours": 6}
        nm_engine = NewMexicoFormulas(config)
        params = nm_engine.get_formula_parameters()

        nm_specific = params.get("nm_specific_parameters", {})
        assert nm_specific.get("woc_hours") == 6


class TestRealWorldNMScenarios:
    """Test with real-world NM well scenarios."""

    def test_nm_permian_shallow_well(self):
        """Test NM formulas for shallow Permian well in SE NM (5000 ft)."""
        nm_engine = NewMexicoFormulas()
        depth_ft = 5000.0

        # Cement excess (cased hole)
        excess = nm_engine.cement_depth_excess(depth_ft)
        assert abs(excess - 1.5) < 0.001  # Flat 50% excess

        # Cement class
        cement_class = nm_engine.cement_class_for_depth(depth_ft)
        assert cement_class == "C"

        # Coverage requirements
        assert nm_engine.coverage_requirement_ft("casing_shoe") == 50
        assert nm_engine.coverage_requirement_ft("cibp_cap") == 100

    def test_nm_san_juan_deep_well(self):
        """Test NM formulas for deep San Juan Basin well (8000 ft)."""
        nm_engine = NewMexicoFormulas()
        depth_ft = 8000.0

        # Cement excess (cased hole)
        excess = nm_engine.cement_depth_excess(depth_ft)
        assert abs(excess - 1.5) < 0.001  # Still flat 50% regardless of depth

        # Cement class
        cement_class = nm_engine.cement_class_for_depth(depth_ft)
        assert cement_class == "H"

    def test_nm_open_hole_section_volume(self):
        """
        Calculate cement volume for NM open hole section.

        Given:
        - Open hole diameter: 8.5 in
        - Plug length: 100 ft
        - Depth: 6000 ft
        - Open hole capacity: (8.5^2 - 0^2) / 1029.4 = 0.0701 bbl/ft
        - Base volume: 100 ft * 0.0701 bbl/ft = 7.01 bbl

        With NM open hole excess (100%):
        - Multiplier: 2.0x
        - Total volume: 7.01 bbl * 2.0 = 14.02 bbl
        """
        nm_engine = NewMexicoFormulas()

        # Calculate base volume
        hole_diameter = 8.5
        plug_length = 100.0
        base_capacity = (hole_diameter ** 2) / 1029.4
        base_volume = plug_length * base_capacity

        # Apply open hole excess
        multiplier = nm_engine.cement_excess_for_hole_type("open")
        total_volume = base_volume * multiplier

        # Verify multiplier
        assert abs(multiplier - 2.0) < 0.001

        # Verify total volume
        expected_volume = 7.01 * 2.0  # ~14.02 bbl
        assert abs(total_volume - expected_volume) < 0.2
