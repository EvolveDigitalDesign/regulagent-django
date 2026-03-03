"""
Formula Engine - Regulatory Calculation Abstraction Layer

This module provides a pluggable abstraction for state-specific regulatory formulas
used in well plugging calculations. It enables multi-state expansion by separating
hardcoded calculations from business logic.

Primary Sources:
- Texas: 16 TAC Section 3.14 (TX RRC Statewide Rule 14)
- New Mexico: NMAC 19.15.25, 19.15.16 (NMOCD plugging and cementing requirements)

Version History:
- 2026.02.0: Initial implementation (POL-002)

Usage:
    from apps.policy.services.formula_engine import get_formula_engine

    # Get engine for a jurisdiction
    engine = get_formula_engine("TX")

    # Calculate depth excess multiplier
    multiplier = engine.cement_depth_excess(5000)  # Returns 1.5 for Texas

    # Get coverage requirement
    coverage = engine.coverage_requirement_ft("casing_shoe")  # Returns 50 for Texas

    # Determine cement class
    cement_class = engine.cement_class_for_depth(7000)  # Returns "H" for Texas
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Literal
import logging

logger = logging.getLogger(__name__)


# Type aliases for clarity
PlugType = Literal[
    "casing_shoe",
    "duqw",
    "production_horizon",
    "intermediate_shoe",
    "top_plug",
    "cibp_cap",
]

CementClass = Literal["C", "H", "A", "G"]


class RegulatoryFormulas(ABC):
    """
    Base class for regulatory formula calculations.

    Each jurisdiction (state) should implement this interface with their
    specific regulatory requirements. This abstraction enables:

    1. Multi-state expansion without modifying core business logic
    2. Version-controlled formula changes tied to effective dates
    3. Clear documentation of regulatory citations
    4. Unit testing of calculations independent of business logic

    Attributes:
        jurisdiction: Two-letter state code (e.g., "TX", "NM")
        effective_date: ISO date string when this formula set became effective
        primary_citation: Primary regulatory citation (e.g., "16 TAC 3.14")
    """

    jurisdiction: str = ""
    effective_date: str = ""
    primary_citation: str = ""

    @abstractmethod
    def cement_depth_excess(self, depth_ft: float) -> float:
        """
        Calculate the depth-based cement excess multiplier.

        Many jurisdictions require additional cement volume as depth increases
        to account for:
        - Increased hydrostatic pressure
        - Temperature effects on cement setting
        - Longer pump times and potential slurry degradation

        Args:
            depth_ft: True vertical depth in feet from surface to plug bottom

        Returns:
            float: Multiplier to apply to base cement volume (e.g., 1.5 = 50% excess)

        Example:
            # Texas at 5000 ft: 1.0 + (0.10 * 5.0) = 1.5x
            multiplier = engine.cement_depth_excess(5000)
            total_volume = base_volume * multiplier
        """
        pass

    @abstractmethod
    def coverage_requirement_ft(self, plug_type: PlugType) -> int:
        """
        Get the cement coverage requirement for a specific plug type.

        Different plug types may have different coverage requirements based on
        what they're isolating (water zones, productive horizons, casing shoes).

        Args:
            plug_type: Type of plug (see PlugType for valid values):
                - "casing_shoe": Surface or intermediate casing shoe plug
                - "duqw": Deepest usable quality water isolation
                - "production_horizon": Productive zone isolation
                - "intermediate_shoe": Intermediate casing shoe
                - "top_plug": Surface safety plug
                - "cibp_cap": Cast iron bridge plug cement cap

        Returns:
            int: Coverage requirement in feet (typically above and below target depth)

        Example:
            # Get coverage for casing shoe plug
            coverage = engine.coverage_requirement_ft("casing_shoe")
            plug_top = shoe_depth - coverage  # 50 ft above shoe
            plug_bottom = shoe_depth + coverage  # 50 ft below shoe
        """
        pass

    @abstractmethod
    def cement_class_for_depth(self, depth_ft: float) -> CementClass:
        """
        Determine the appropriate cement class based on depth.

        Cement class selection is based on:
        - Bottomhole temperature (increases with depth)
        - Pressure requirements
        - Setting time requirements

        Common classes:
        - Class C: Standard for shallow wells (fast-setting)
        - Class H: High-temperature wells (thixotropic, slower-setting)

        Args:
            depth_ft: True vertical depth in feet

        Returns:
            CementClass: Recommended cement class ("C", "H", "A", or "G")

        Regulatory Citations:
            Texas: API oil well cement per 16 TAC 3.14(d)(4)
        """
        pass

    def get_formula_parameters(self) -> Dict[str, Any]:
        """
        Return all formula parameters for transparency and debugging.

        Returns:
            Dict containing all formula parameters with their values and citations.
        """
        return {
            "jurisdiction": self.jurisdiction,
            "effective_date": self.effective_date,
            "primary_citation": self.primary_citation,
        }


class TexasFormulas(RegulatoryFormulas):
    """
    Texas-specific regulatory formulas per 16 TAC Section 3.14.

    Texas RRC (Railroad Commission) Statewide Rule 14 defines plugging
    requirements for oil and gas wells in Texas.

    Key Formulas:
    - Depth Excess: +10% per 1000 ft per TAC 3.14(d)(11)
    - Coverage: 50 ft above/below for most plug types per TAC 3.14(e)(2)
    - Cement Class: C above 6500 ft, H below per industry practice

    Primary Source:
        https://www.law.cornell.edu/regulations/texas/title-16/part-1/chapter-3/section-3.14
    """

    jurisdiction = "TX"
    effective_date = "2025-07-01"  # Latest TAC 3.14 amendment
    primary_citation = "16 TAC 3.14"

    # Configuration parameters (can be overridden by policy pack)
    _depth_excess_base: float = 1.0
    _depth_excess_per_kft: float = 0.10  # 10% per 1000 ft

    _coverage_defaults: Dict[PlugType, int] = {
        "casing_shoe": 50,  # TAC 3.14(e)(2): 50 ft above and below shoe
        "duqw": 50,  # TAC 3.14(g)(1): 50 ft above and 50 ft below base
        "production_horizon": 50,  # TAC 3.14(k): 50 ft coverage
        "intermediate_shoe": 50,  # TAC 3.14(f)(1): same as surface
        "top_plug": 0,  # No coverage needed - plug IS at surface
        "cibp_cap": 0,  # CIBP cap sits on top of bridge plug
    }

    _cement_class_cutoff_ft: float = 6500.0
    _shallow_class: CementClass = "C"
    _deep_class: CementClass = "H"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize Texas formulas with optional configuration overrides.

        Args:
            config: Optional dict to override default parameters:
                - depth_excess_per_kft: Override 10% per 1000 ft
                - coverage_defaults: Dict of plug_type -> coverage_ft overrides
                - cement_class_cutoff_ft: Override 6500 ft cutoff
                - shallow_class: Override shallow cement class
                - deep_class: Override deep cement class
        """
        # Copy class-level defaults to instance to avoid mutation
        self._coverage_defaults = dict(self.__class__._coverage_defaults)

        if config:
            if "depth_excess_per_kft" in config:
                self._depth_excess_per_kft = float(config["depth_excess_per_kft"])
            if "coverage_defaults" in config:
                self._coverage_defaults.update(config["coverage_defaults"])
            if "cement_class_cutoff_ft" in config:
                self._cement_class_cutoff_ft = float(config["cement_class_cutoff_ft"])
            if "shallow_class" in config:
                self._shallow_class = config["shallow_class"]
            if "deep_class" in config:
                self._deep_class = config["deep_class"]

    def cement_depth_excess(self, depth_ft: float) -> float:
        """
        Calculate Texas depth-based cement excess multiplier.

        Per 16 TAC 3.14(d)(11):
            "All cement plugs, except the top plug, shall have sufficient slurry
            volume to fill 100 feet of hole, plus 10% for each 1,000 feet of depth
            from the ground surface to the bottom of the plug."

        Formula: multiplier = 1.0 + (0.10 * depth_in_thousands)

        Examples:
            - At 0 ft: 1.0 + (0.10 * 0) = 1.0x (no excess)
            - At 5000 ft: 1.0 + (0.10 * 5.0) = 1.5x (50% excess)
            - At 10000 ft: 1.0 + (0.10 * 10.0) = 2.0x (100% excess)

        Args:
            depth_ft: Depth from surface to bottom of plug in feet

        Returns:
            float: Multiplier to apply to base cement volume

        Citation:
            tx.tac.16.3.14(d)(11)
        """
        if depth_ft <= 0:
            return self._depth_excess_base

        depth_kft = depth_ft / 1000.0
        multiplier = self._depth_excess_base + (self._depth_excess_per_kft * depth_kft)

        logger.debug(
            f"TX cement depth excess: {depth_ft:.0f} ft -> {depth_kft:.2f} kft -> "
            f"{multiplier:.4f}x ({(multiplier - 1) * 100:.1f}% excess)"
        )

        return multiplier

    def coverage_requirement_ft(self, plug_type: PlugType) -> int:
        """
        Get Texas cement coverage requirement for plug type.

        Per 16 TAC 3.14(e)(2):
            "a cement plug shall be placed across the shoe of the surface casing.
            This plug shall be a minimum of 100 feet in length and shall extend
            at least 50 feet above the shoe and at least 50 feet below the shoe."

        Per 16 TAC 3.14(g)(1):
            "This plug shall be a minimum of 100 feet in length and shall extend
            at least 50 feet below and 50 feet above the base of the deepest
            usable quality water stratum."

        Args:
            plug_type: Type of plug (casing_shoe, duqw, production_horizon, etc.)

        Returns:
            int: Coverage requirement in feet

        Citation:
            tx.tac.16.3.14(e)(2), tx.tac.16.3.14(g)(1)
        """
        coverage = self._coverage_defaults.get(plug_type, 50)  # Default 50 ft

        logger.debug(f"TX coverage requirement for {plug_type}: {coverage} ft")

        return coverage

    def cement_class_for_depth(self, depth_ft: float) -> CementClass:
        """
        Determine cement class based on depth for Texas wells.

        Texas uses API oil well cement per 16 TAC 3.14(d)(4). Class selection
        is based on bottomhole temperature which correlates with depth:

        - Class C: Standard cement for shallow/moderate depth wells
          - Suitable for temperatures up to ~230F (110C)
          - Faster setting time

        - Class H: High-temperature/high-sulfate-resistant cement
          - Suitable for temperatures up to ~300F (149C)
          - Slower setting, better for deep wells
          - Thixotropic properties

        Default cutoff: 6500 ft (industry practice, not explicitly in TAC)

        Args:
            depth_ft: True vertical depth in feet

        Returns:
            CementClass: "C" for shallow, "H" for deep wells

        Citation:
            tx.tac.16.3.14(d)(4), API RP 65
        """
        if depth_ft < self._cement_class_cutoff_ft:
            cement_class = self._shallow_class
        else:
            cement_class = self._deep_class

        logger.debug(
            f"TX cement class for {depth_ft:.0f} ft: {cement_class} "
            f"(cutoff: {self._cement_class_cutoff_ft:.0f} ft)"
        )

        return cement_class

    def get_formula_parameters(self) -> Dict[str, Any]:
        """Return all Texas formula parameters for transparency."""
        return {
            "jurisdiction": self.jurisdiction,
            "effective_date": self.effective_date,
            "primary_citation": self.primary_citation,
            "depth_excess": {
                "formula": "1.0 + (0.10 * depth_kft)",
                "base": self._depth_excess_base,
                "per_kft": self._depth_excess_per_kft,
                "citation": "tx.tac.16.3.14(d)(11)",
            },
            "coverage": {
                "defaults": dict(self._coverage_defaults),
                "citation": "tx.tac.16.3.14(e)(2), tx.tac.16.3.14(g)(1)",
            },
            "cement_class": {
                "cutoff_ft": self._cement_class_cutoff_ft,
                "shallow_class": self._shallow_class,
                "deep_class": self._deep_class,
                "citation": "tx.tac.16.3.14(d)(4)",
            },
        }


class NewMexicoFormulas(RegulatoryFormulas):
    """
    New Mexico-specific regulatory formulas per NMAC 19.15.25.

    NMOCD (New Mexico Oil Conservation Division) plugging requirements.

    Key Differences from Texas:
    - NM uses flat excess rates (50% cased, 100% open) vs TX depth-based formula
    - CIBP cap requires 100 ft of cement vs TX 20 ft
    - Alternative minimum: 25 sacks OR 100 ft (whichever greater)
    - Maximum plug spacing: 3000 ft (cased) / 2000 ft (open hole)
    - Required WOC time: 4 hours minimum
    - Cement standing time: 8-18 hours depending on method

    Primary Sources:
        - NMAC 19.15.25 (Well Plugging and Abandonment)
        - NMAC 19.15.16 (Drilling and Production - Casing/Cementing)
        https://www.srca.nm.gov/parts/title19/19.015.0025.html
    """

    jurisdiction = "NM"
    effective_date = "2018-06-26"  # Last NMAC 19.15.16 amendment
    primary_citation = "NMAC 19.15.25"

    # NM uses flat excess based on hole type, not depth-based like Texas
    # Per NMAC 19.15.25: 50% excess for cased hole, 100% for open hole
    _cased_hole_excess: float = 0.50  # 50% excess
    _open_hole_excess: float = 1.00   # 100% excess (double volume)

    _coverage_defaults: Dict[PlugType, int] = {
        "casing_shoe": 50,       # NMAC 19.15.25: 50 ft above and below
        "duqw": 50,              # NMAC 19.15.25: 50 ft above and below
        "production_horizon": 50, # NMAC 19.15.25: standard coverage
        "intermediate_shoe": 50,  # NMAC 19.15.25: same as surface casing
        "top_plug": 0,           # No coverage needed at surface
        "cibp_cap": 100,         # NMAC 19.15.25: 100 ft cap (vs TX 20 ft!)
    }

    _cement_class_cutoff_ft: float = 6500.0  # NM doesn't specify - using industry standard
    _shallow_class: CementClass = "C"
    _deep_class: CementClass = "H"

    # NM-specific parameters per NMAC 19.15.25
    _min_sacks: int = 25                    # Alternative to 100 ft minimum
    _max_cased_spacing_ft: int = 3000       # Maximum spacing between plugs in cased hole
    _max_open_spacing_ft: int = 2000        # Maximum spacing between plugs in open hole
    _woc_hours: int = 4                     # Wait on cement time minimum
    _cement_standing_hours_min: int = 8     # Minimum cement standing (500 psi method)
    _cement_standing_hours_max: int = 18    # Maximum cement standing (standard method)

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize New Mexico formulas with optional configuration overrides.

        Args:
            config: Optional dict to override default parameters:
                - cased_hole_excess: Override 50% cased hole excess
                - open_hole_excess: Override 100% open hole excess
                - coverage_defaults: Dict of plug_type -> coverage_ft overrides
                - cement_class_cutoff_ft: Override 6500 ft cutoff
                - shallow_class: Override shallow cement class
                - deep_class: Override deep cement class
                - min_sacks: Override 25 sack minimum
                - max_cased_spacing_ft: Override 3000 ft max spacing
                - max_open_spacing_ft: Override 2000 ft max spacing
                - woc_hours: Override 4 hour WOC minimum
        """
        # Copy class-level defaults to instance to avoid mutation
        self._coverage_defaults = dict(self.__class__._coverage_defaults)

        if config:
            if "cased_hole_excess" in config:
                self._cased_hole_excess = float(config["cased_hole_excess"])
            if "open_hole_excess" in config:
                self._open_hole_excess = float(config["open_hole_excess"])
            if "coverage_defaults" in config:
                self._coverage_defaults.update(config["coverage_defaults"])
            if "cement_class_cutoff_ft" in config:
                self._cement_class_cutoff_ft = float(config["cement_class_cutoff_ft"])
            if "shallow_class" in config:
                self._shallow_class = config["shallow_class"]
            if "deep_class" in config:
                self._deep_class = config["deep_class"]
            if "min_sacks" in config:
                self._min_sacks = int(config["min_sacks"])
            if "max_cased_spacing_ft" in config:
                self._max_cased_spacing_ft = int(config["max_cased_spacing_ft"])
            if "max_open_spacing_ft" in config:
                self._max_open_spacing_ft = int(config["max_open_spacing_ft"])
            if "woc_hours" in config:
                self._woc_hours = int(config["woc_hours"])

    def cement_excess_for_hole_type(self, hole_type: Literal["cased", "open"]) -> float:
        """
        Calculate cement excess multiplier based on hole type.

        New Mexico uses a flat excess rate based on hole type rather than
        depth-based calculation like Texas.

        Per NMAC 19.15.25:
        - Cased hole: 50% calculated cement excess
        - Open hole: 100% calculated cement excess (double volume)

        Args:
            hole_type: "cased" for inside casing, "open" for open hole

        Returns:
            float: Multiplier to apply to base cement volume
                - 1.5 for cased hole (50% excess)
                - 2.0 for open hole (100% excess)

        Citation:
            nmac.19.15.25

        Example:
            # Cased hole plug
            multiplier = engine.cement_excess_for_hole_type("cased")
            total_volume = base_volume * multiplier  # 1.5x base

            # Open hole plug
            multiplier = engine.cement_excess_for_hole_type("open")
            total_volume = base_volume * multiplier  # 2.0x base
        """
        if hole_type == "open":
            multiplier = 1.0 + self._open_hole_excess  # 1.0 + 1.0 = 2.0x
            logger.debug(
                f"NM cement excess (open hole): {multiplier:.2f}x "
                f"({self._open_hole_excess * 100:.0f}% excess)"
            )
        else:
            multiplier = 1.0 + self._cased_hole_excess  # 1.0 + 0.5 = 1.5x
            logger.debug(
                f"NM cement excess (cased hole): {multiplier:.2f}x "
                f"({self._cased_hole_excess * 100:.0f}% excess)"
            )

        return multiplier

    def cement_depth_excess(self, depth_ft: float) -> float:
        """
        Calculate NM cement excess multiplier (defaults to cased hole).

        NOTE: New Mexico uses flat excess rates based on hole type, NOT depth.
        This method exists for interface compatibility and defaults to cased
        hole excess. Use cement_excess_for_hole_type() for explicit control.

        Per NMAC 19.15.25:
        - Inside casing: 50% excess (1.5x multiplier)
        - Open hole: 100% excess (2.0x multiplier)

        Args:
            depth_ft: Depth parameter (ignored in NM - kept for interface compatibility)

        Returns:
            float: Multiplier for cased hole (1.5x default)

        Citation:
            nmac.19.15.25

        See Also:
            cement_excess_for_hole_type() - Preferred method for NM calculations
        """
        # NM doesn't use depth-based excess - return cased hole default
        multiplier = 1.0 + self._cased_hole_excess

        logger.debug(
            f"NM cement excess (depth-agnostic): {multiplier:.2f}x "
            f"(cased hole default, {self._cased_hole_excess * 100:.0f}% excess)"
        )

        return multiplier

    def coverage_requirement_ft(self, plug_type: PlugType) -> int:
        """
        Get NM cement coverage requirement for plug type.

        Per NMAC 19.15.25, most plugs require 50 ft above and below the target
        depth (same as Texas). Notable exception: CIBP cap requires 100 ft of
        cement above the bridge plug (vs Texas 20 ft).

        Coverage Requirements:
        - Casing shoe: 50 ft above and 50 ft below
        - DUQW: 50 ft above and 50 ft below
        - Production horizon: 50 ft coverage
        - Intermediate shoe: 50 ft above and below
        - CIBP cap: 100 ft above bridge plug (NOT 20 ft like Texas!)
        - Top plug: 0 ft (plug is at surface)

        Args:
            plug_type: Type of plug (see PlugType for valid values)

        Returns:
            int: Coverage requirement in feet

        Citation:
            nmac.19.15.25
        """
        coverage = self._coverage_defaults.get(plug_type, 50)

        logger.debug(f"NM coverage requirement for {plug_type}: {coverage} ft")

        return coverage

    def cement_class_for_depth(self, depth_ft: float) -> CementClass:
        """
        Determine cement class based on depth for NM wells.

        Per NMAC 19.15.16, New Mexico requires "conventional hard-setting cement"
        but does not specify API class selection criteria. Following industry
        standard practice similar to Texas:

        - Class C: Standard cement for shallow/moderate depth wells
          - Suitable for temperatures up to ~230F (110C)
          - Faster setting time

        - Class H: High-temperature/high-sulfate-resistant cement
          - Suitable for temperatures up to ~300F (149C)
          - Slower setting, better for deep wells
          - Thixotropic properties

        Default cutoff: 6500 ft (industry practice, not specified in NMAC)

        Args:
            depth_ft: True vertical depth in feet

        Returns:
            CementClass: "C" for shallow, "H" for deep wells

        Citation:
            nmac.19.15.16.10(D), API RP 65 (industry standard)
        """
        if depth_ft < self._cement_class_cutoff_ft:
            cement_class = self._shallow_class
        else:
            cement_class = self._deep_class

        logger.debug(
            f"NM cement class for {depth_ft:.0f} ft: {cement_class} "
            f"(cutoff: {self._cement_class_cutoff_ft:.0f} ft)"
        )

        return cement_class

    def get_formula_parameters(self) -> Dict[str, Any]:
        """Return all NM formula parameters for transparency."""
        return {
            "jurisdiction": self.jurisdiction,
            "effective_date": self.effective_date,
            "primary_citation": self.primary_citation,
            "cement_excess": {
                "type": "flat (hole-type based)",
                "cased_hole_excess": self._cased_hole_excess,
                "open_hole_excess": self._open_hole_excess,
                "cased_multiplier": 1.0 + self._cased_hole_excess,
                "open_multiplier": 1.0 + self._open_hole_excess,
                "citation": "nmac.19.15.25",
                "note": "NM uses flat excess rates, not depth-based like Texas",
            },
            "coverage": {
                "defaults": dict(self._coverage_defaults),
                "citation": "nmac.19.15.25",
                "note": "CIBP cap is 100 ft (vs Texas 20 ft)",
            },
            "cement_class": {
                "cutoff_ft": self._cement_class_cutoff_ft,
                "shallow_class": self._shallow_class,
                "deep_class": self._deep_class,
                "citation": "nmac.19.15.16.10(D), API RP 65",
                "note": "NM requires 'conventional hard-setting cement' but doesn't specify class cutoff",
            },
            "nm_specific_parameters": {
                "min_sacks": self._min_sacks,
                "max_cased_spacing_ft": self._max_cased_spacing_ft,
                "max_open_spacing_ft": self._max_open_spacing_ft,
                "woc_hours": self._woc_hours,
                "cement_standing_hours_min": self._cement_standing_hours_min,
                "cement_standing_hours_max": self._cement_standing_hours_max,
                "citation": "nmac.19.15.25, nmac.19.15.16",
            },
        }


# Registry of available formula engines
_FORMULA_ENGINES: Dict[str, type[RegulatoryFormulas]] = {
    "TX": TexasFormulas,
    "NM": NewMexicoFormulas,
}


def get_formula_engine(
    jurisdiction: str,
    config: Optional[Dict[str, Any]] = None
) -> RegulatoryFormulas:
    """
    Factory function to get the appropriate formula engine for a jurisdiction.

    Args:
        jurisdiction: Two-letter state code (e.g., "TX", "NM")
        config: Optional configuration overrides for the engine

    Returns:
        RegulatoryFormulas: Configured formula engine for the jurisdiction

    Raises:
        ValueError: If jurisdiction is not supported

    Example:
        # Get Texas engine with default config
        engine = get_formula_engine("TX")

        # Get Texas engine with custom config
        engine = get_formula_engine("TX", {
            "cement_class_cutoff_ft": 7000,
            "depth_excess_per_kft": 0.12,
        })
    """
    jurisdiction = jurisdiction.upper().strip()

    if jurisdiction not in _FORMULA_ENGINES:
        supported = ", ".join(sorted(_FORMULA_ENGINES.keys()))
        raise ValueError(
            f"Unsupported jurisdiction: {jurisdiction}. "
            f"Supported jurisdictions: {supported}"
        )

    engine_class = _FORMULA_ENGINES[jurisdiction]
    return engine_class(config)


def register_formula_engine(
    jurisdiction: str,
    engine_class: type[RegulatoryFormulas]
) -> None:
    """
    Register a custom formula engine for a jurisdiction.

    This allows plugins or extensions to add support for additional
    jurisdictions without modifying this module.

    Args:
        jurisdiction: Two-letter state code
        engine_class: Class implementing RegulatoryFormulas

    Example:
        class ColoradoFormulas(RegulatoryFormulas):
            # ... implementation ...
            pass

        register_formula_engine("CO", ColoradoFormulas)
    """
    jurisdiction = jurisdiction.upper().strip()

    if not issubclass(engine_class, RegulatoryFormulas):
        raise TypeError(
            f"engine_class must be a subclass of RegulatoryFormulas, "
            f"got {type(engine_class)}"
        )

    _FORMULA_ENGINES[jurisdiction] = engine_class
    logger.info(f"Registered formula engine for jurisdiction: {jurisdiction}")


def list_supported_jurisdictions() -> list[str]:
    """
    List all supported jurisdictions.

    Returns:
        List of two-letter state codes with registered formula engines
    """
    return sorted(_FORMULA_ENGINES.keys())
