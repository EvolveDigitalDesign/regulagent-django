"""
Unit tests for the Formula Engine abstraction layer.

Tests coverage:
- TexasFormulas cement depth excess calculations
- TexasFormulas coverage requirements
- TexasFormulas cement class selection
- NewMexicoFormulas NMAC 19.15 implementation
- Factory function and registration
- Configuration overrides

Related to:
- POL-002 - Refactor TX hardcoding to policy packs
- BE2-024 - Update NewMexicoFormulas with NMAC values
"""

import pytest
from typing import Dict, Any

from apps.policy.services.formula_engine import (
    RegulatoryFormulas,
    TexasFormulas,
    NewMexicoFormulas,
    get_formula_engine,
    register_formula_engine,
    list_supported_jurisdictions,
)


class TestTexasFormulasDepthExcess:
    """Test Texas depth excess calculations per TAC 3.14(d)(11)."""

    def test_depth_excess_at_surface(self):
        """At surface (0 ft), multiplier should be 1.0 (no excess)."""
        engine = TexasFormulas()
        assert engine.cement_depth_excess(0) == 1.0

    def test_depth_excess_at_1000ft(self):
        """At 1000 ft: 1.0 + (0.10 * 1.0) = 1.1x (10% excess)."""
        engine = TexasFormulas()
        result = engine.cement_depth_excess(1000)
        assert abs(result - 1.1) < 0.001

    def test_depth_excess_at_5000ft(self):
        """At 5000 ft: 1.0 + (0.10 * 5.0) = 1.5x (50% excess)."""
        engine = TexasFormulas()
        result = engine.cement_depth_excess(5000)
        assert abs(result - 1.5) < 0.001

    def test_depth_excess_at_10000ft(self):
        """At 10000 ft: 1.0 + (0.10 * 10.0) = 2.0x (100% excess)."""
        engine = TexasFormulas()
        result = engine.cement_depth_excess(10000)
        assert abs(result - 2.0) < 0.001

    def test_depth_excess_at_14000ft(self):
        """At 14000 ft (deep Permian well): 1.0 + (0.10 * 14.0) = 2.4x."""
        engine = TexasFormulas()
        result = engine.cement_depth_excess(14000)
        assert abs(result - 2.4) < 0.001

    def test_depth_excess_negative_depth(self):
        """Negative depth should return base multiplier (1.0)."""
        engine = TexasFormulas()
        assert engine.cement_depth_excess(-100) == 1.0

    def test_depth_excess_fractional_depth(self):
        """At 5500 ft: 1.0 + (0.10 * 5.5) = 1.55x."""
        engine = TexasFormulas()
        result = engine.cement_depth_excess(5500)
        assert abs(result - 1.55) < 0.001


class TestTexasFormulasCoverageRequirements:
    """Test Texas coverage requirements per TAC 3.14."""

    def test_casing_shoe_coverage(self):
        """Casing shoe requires 50 ft coverage per TAC 3.14(e)(2)."""
        engine = TexasFormulas()
        assert engine.coverage_requirement_ft("casing_shoe") == 50

    def test_duqw_coverage(self):
        """DUQW isolation requires 50 ft coverage per TAC 3.14(g)(1)."""
        engine = TexasFormulas()
        assert engine.coverage_requirement_ft("duqw") == 50

    def test_production_horizon_coverage(self):
        """Production horizon requires 50 ft coverage per TAC 3.14(k)."""
        engine = TexasFormulas()
        assert engine.coverage_requirement_ft("production_horizon") == 50

    def test_intermediate_shoe_coverage(self):
        """Intermediate casing shoe requires 50 ft coverage per TAC 3.14(f)(1)."""
        engine = TexasFormulas()
        assert engine.coverage_requirement_ft("intermediate_shoe") == 50

    def test_top_plug_no_coverage(self):
        """Top plug at surface has no coverage requirement."""
        engine = TexasFormulas()
        assert engine.coverage_requirement_ft("top_plug") == 0

    def test_cibp_cap_no_coverage(self):
        """CIBP cap sits on bridge plug, no coverage needed."""
        engine = TexasFormulas()
        assert engine.coverage_requirement_ft("cibp_cap") == 0


class TestTexasFormulasCementClass:
    """Test Texas cement class selection."""

    def test_shallow_well_class_c(self):
        """Wells shallower than 6500 ft use Class C cement."""
        engine = TexasFormulas()
        assert engine.cement_class_for_depth(3000) == "C"
        assert engine.cement_class_for_depth(5000) == "C"
        assert engine.cement_class_for_depth(6499) == "C"

    def test_deep_well_class_h(self):
        """Wells at or deeper than 6500 ft use Class H cement."""
        engine = TexasFormulas()
        assert engine.cement_class_for_depth(6500) == "H"
        assert engine.cement_class_for_depth(8000) == "H"
        assert engine.cement_class_for_depth(14000) == "H"

    def test_cutoff_boundary(self):
        """Test exact boundary condition at 6500 ft."""
        engine = TexasFormulas()
        # Just below cutoff
        assert engine.cement_class_for_depth(6499.9) == "C"
        # At cutoff
        assert engine.cement_class_for_depth(6500.0) == "H"


class TestTexasFormulasConfiguration:
    """Test configuration override functionality."""

    def test_custom_depth_excess(self):
        """Custom depth excess rate should be applied."""
        config = {"depth_excess_per_kft": 0.15}  # 15% per 1000 ft
        engine = TexasFormulas(config)
        # At 5000 ft: 1.0 + (0.15 * 5.0) = 1.75x
        result = engine.cement_depth_excess(5000)
        assert abs(result - 1.75) < 0.001

    def test_custom_cement_cutoff(self):
        """Custom cement class cutoff should be applied."""
        config = {"cement_class_cutoff_ft": 7000}
        engine = TexasFormulas(config)
        assert engine.cement_class_for_depth(6800) == "C"  # Now shallow
        assert engine.cement_class_for_depth(7000) == "H"  # Now deep

    def test_custom_coverage(self):
        """Custom coverage requirements should be applied."""
        config = {"coverage_defaults": {"casing_shoe": 75}}
        engine = TexasFormulas(config)
        assert engine.coverage_requirement_ft("casing_shoe") == 75
        # Unmodified values should remain default
        assert engine.coverage_requirement_ft("duqw") == 50

    def test_get_formula_parameters(self):
        """Formula parameters should be retrievable for transparency."""
        engine = TexasFormulas()
        params = engine.get_formula_parameters()

        assert params["jurisdiction"] == "TX"
        assert "depth_excess" in params
        assert params["depth_excess"]["per_kft"] == 0.10
        assert "coverage" in params
        assert "cement_class" in params
        assert params["cement_class"]["cutoff_ft"] == 6500


class TestNewMexicoFormulas:
    """Test New Mexico implementation per NMAC 19.15.25."""

    def test_flat_cement_excess_cased_hole(self):
        """NM uses flat 50% excess for cased hole, not depth-based."""
        engine = NewMexicoFormulas()

        # All depths should return 1.5x (50% excess) for cased hole default
        assert engine.cement_depth_excess(1000) == 1.5
        assert engine.cement_depth_excess(5000) == 1.5
        assert engine.cement_depth_excess(10000) == 1.5

    def test_cement_excess_by_hole_type(self):
        """NM provides hole-type specific excess calculation."""
        engine = NewMexicoFormulas()

        # Cased hole: 50% excess (1.5x multiplier)
        assert engine.cement_excess_for_hole_type("cased") == 1.5

        # Open hole: 100% excess (2.0x multiplier)
        assert engine.cement_excess_for_hole_type("open") == 2.0

    def test_cibp_cap_coverage_100ft(self):
        """NM requires 100 ft CIBP cap vs TX 20 ft."""
        nm_engine = NewMexicoFormulas()
        tx_engine = TexasFormulas()

        # NM: 100 ft
        assert nm_engine.coverage_requirement_ft("cibp_cap") == 100

        # TX: 0 ft (for comparison - TX doesn't specify in coverage)
        assert tx_engine.coverage_requirement_ft("cibp_cap") == 0

    def test_coverage_requirements_match_tx_except_cibp(self):
        """Most NM coverage requirements match TX except CIBP cap."""
        nm_engine = NewMexicoFormulas()
        tx_engine = TexasFormulas()

        # These should match TX
        assert nm_engine.coverage_requirement_ft("casing_shoe") == 50
        assert nm_engine.coverage_requirement_ft("duqw") == 50
        assert nm_engine.coverage_requirement_ft("production_horizon") == 50
        assert nm_engine.coverage_requirement_ft("intermediate_shoe") == 50
        assert nm_engine.coverage_requirement_ft("top_plug") == 0

    def test_jurisdiction_is_nm(self):
        """NM engine should identify as New Mexico."""
        engine = NewMexicoFormulas()
        assert engine.jurisdiction == "NM"

    def test_effective_date_is_2018(self):
        """NM effective date should be 2018-06-26 (last NMAC 19.15.16 amendment)."""
        engine = NewMexicoFormulas()
        assert engine.effective_date == "2018-06-26"

    def test_nm_specific_parameters_exist(self):
        """NM-specific parameters should be defined."""
        engine = NewMexicoFormulas()
        params = engine.get_formula_parameters()

        # Check for NM-specific section
        assert "nm_specific_parameters" in params
        nm_params = params["nm_specific_parameters"]

        # Verify NM-specific values
        assert nm_params["min_sacks"] == 25
        assert nm_params["max_cased_spacing_ft"] == 3000
        assert nm_params["max_open_spacing_ft"] == 2000
        assert nm_params["woc_hours"] == 4
        assert nm_params["cement_standing_hours_min"] == 8
        assert nm_params["cement_standing_hours_max"] == 18

    def test_parameters_show_real_implementation(self):
        """Parameters should indicate real NMAC implementation, not placeholder."""
        engine = NewMexicoFormulas()
        params = engine.get_formula_parameters()

        # Should NOT have placeholder note
        assert "PLACEHOLDER" not in params.get("note", "")

        # Should have cement_excess section with flat rates
        assert "cement_excess" in params
        assert params["cement_excess"]["type"] == "flat (hole-type based)"
        assert params["cement_excess"]["cased_multiplier"] == 1.5
        assert params["cement_excess"]["open_multiplier"] == 2.0


class TestFormulaEngineFactory:
    """Test factory function and registration."""

    def test_get_texas_engine(self):
        """Factory should return TexasFormulas for TX."""
        engine = get_formula_engine("TX")
        assert isinstance(engine, TexasFormulas)

    def test_get_new_mexico_engine(self):
        """Factory should return NewMexicoFormulas for NM."""
        engine = get_formula_engine("NM")
        assert isinstance(engine, NewMexicoFormulas)

    def test_case_insensitive(self):
        """Jurisdiction codes should be case-insensitive."""
        engine_lower = get_formula_engine("tx")
        engine_upper = get_formula_engine("TX")
        assert type(engine_lower) == type(engine_upper)

    def test_unsupported_jurisdiction(self):
        """Unsupported jurisdiction should raise ValueError."""
        with pytest.raises(ValueError) as excinfo:
            get_formula_engine("XX")
        assert "Unsupported jurisdiction" in str(excinfo.value)

    def test_factory_with_config(self):
        """Factory should pass config to engine."""
        config = {"cement_class_cutoff_ft": 8000}
        engine = get_formula_engine("TX", config)
        assert engine.cement_class_for_depth(7500) == "C"

    def test_list_supported_jurisdictions(self):
        """Should list all registered jurisdictions."""
        jurisdictions = list_supported_jurisdictions()
        assert "TX" in jurisdictions
        assert "NM" in jurisdictions


class TestCustomEngineRegistration:
    """Test custom engine registration."""

    def test_register_custom_engine(self):
        """Custom engines should be registrable."""

        class ColoradoFormulas(RegulatoryFormulas):
            jurisdiction = "CO"
            effective_date = "2026-01-01"
            primary_citation = "COGCC Rule 317A"

            def __init__(self, config=None):
                # Accept config parameter (ignored for this simple implementation)
                pass

            def cement_depth_excess(self, depth_ft: float) -> float:
                # Colorado uses flat 20% excess (hypothetical)
                return 1.2

            def coverage_requirement_ft(self, plug_type: str) -> int:
                return 100  # 100 ft coverage (hypothetical)

            def cement_class_for_depth(self, depth_ft: float) -> str:
                return "H"  # Always Class H (hypothetical)

        register_formula_engine("CO", ColoradoFormulas)

        engine = get_formula_engine("CO")
        assert isinstance(engine, ColoradoFormulas)
        assert engine.cement_depth_excess(5000) == 1.2
        assert engine.coverage_requirement_ft("casing_shoe") == 100

    def test_register_invalid_class_raises(self):
        """Registering non-RegulatoryFormulas class should raise TypeError."""

        class NotAFormulaEngine:
            pass

        with pytest.raises(TypeError):
            register_formula_engine("XX", NotAFormulaEngine)


class TestRealWorldScenarios:
    """Test with real-world well scenarios."""

    def test_permian_deep_well(self):
        """Test formulas for typical Permian Basin deep well (14,000 ft)."""
        engine = TexasFormulas()

        # Depth excess at 14,000 ft
        depth_excess = engine.cement_depth_excess(14000)
        assert abs(depth_excess - 2.4) < 0.001  # 140% excess

        # Should use Class H cement
        assert engine.cement_class_for_depth(14000) == "H"

        # Standard coverage
        assert engine.coverage_requirement_ft("casing_shoe") == 50

    def test_shallow_spraberry_well(self):
        """Test formulas for shallow Spraberry well (5,000 ft)."""
        engine = TexasFormulas()

        # Depth excess at 5,000 ft
        depth_excess = engine.cement_depth_excess(5000)
        assert abs(depth_excess - 1.5) < 0.001  # 50% excess

        # Should use Class C cement
        assert engine.cement_class_for_depth(5000) == "C"

    def test_volume_calculation_example(self):
        """
        Example: Calculate cement volume for 100 ft plug at 10,000 ft.

        Given:
        - Plug length: 100 ft
        - Casing ID: 6.094 in (7" casing)
        - Base capacity: (6.094^2 / 1029.4) = 0.0361 bbl/ft
        - Base volume: 100 ft * 0.0361 bbl/ft = 3.61 bbl

        With Texas depth excess at 10,000 ft:
        - Multiplier: 1.0 + (0.10 * 10) = 2.0x
        - Total volume: 3.61 bbl * 2.0 = 7.22 bbl
        """
        engine = TexasFormulas()

        # Calculate base volume (simplified)
        casing_id = 6.094
        plug_length = 100.0
        base_capacity = (casing_id ** 2) / 1029.4
        base_volume = plug_length * base_capacity

        # Apply depth excess
        multiplier = engine.cement_depth_excess(10000)
        total_volume = base_volume * multiplier

        # Verify multiplier
        assert abs(multiplier - 2.0) < 0.001

        # Verify total volume (approximately)
        expected_volume = 3.61 * 2.0  # ~7.22 bbl
        assert abs(total_volume - expected_volume) < 0.1
