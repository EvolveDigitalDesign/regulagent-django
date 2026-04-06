"""
Unit tests for NM well integration (no database required).

These tests focus on logic and mapping without database dependencies.
"""
from decimal import Decimal
from apps.public_core.services.nm_well_import import _map_nm_data_to_well_registry
from apps.public_core.services.nm_well_scraper import NMWellData


def _get_mock_nm_well_data(api10="30-015-28692"):
    """Create mock NMWellData for testing."""
    return NMWellData(
        api10=api10,
        api14="30015286920000",
        well_name="STATE FEDERAL 1",
        operator_name="EOG RESOURCES INC",
        operator_number="7377",
        status="ACTIVE",
        well_type="OIL",
        direction="HORIZONTAL",
        surface_location="320 FNL, 660 FWL, SEC 30 T16S R33E, LEA COUNTY",
        latitude=32.7574387,
        longitude=-104.0298615,
        elevation_ft=3450.0,
        proposed_depth_ft=12500,
        tvd_ft=8500,
        formation="BONE SPRING",
        spud_date="01/15/2024",
        completion_date="03/20/2024",
        raw_html=None
    )


def test_map_nm_data_to_well_registry():
    """Test mapping NM data to WellRegistry fields."""
    nm_data = _get_mock_nm_well_data()
    well_data = _map_nm_data_to_well_registry(nm_data)

    # Assertions
    assert well_data["api14"] == "30015286920000"
    assert well_data["state"] == "NM"
    assert well_data["county"] == "LEA"
    assert well_data["operator_name"] == "EOG RESOURCES INC"
    assert well_data["field_name"] == "BONE SPRING"
    assert well_data["lease_name"] == "STATE FEDERAL"
    assert well_data["well_number"] == "1"
    assert well_data["lat"] == Decimal("32.7574387")
    assert well_data["lon"] == Decimal("-104.0298615")


def test_map_nm_data_parses_well_name():
    """Test well name parsing extracts lease and well number."""
    test_cases = [
        ("STATE FEDERAL 1", "STATE FEDERAL", "1"),
        ("FEDERAL 1-30H", "FEDERAL", "1-30H"),
        ("UNIT 2H", "UNIT", "2H"),
        ("LEASE NAME ONLY", "LEASE NAME ONLY", ""),
    ]

    for well_name, expected_lease, expected_well_no in test_cases:
        nm_data = _get_mock_nm_well_data()
        nm_data.well_name = well_name
        well_data = _map_nm_data_to_well_registry(nm_data)

        assert well_data["lease_name"] == expected_lease, f"Failed for {well_name}"
        assert well_data["well_number"] == expected_well_no, f"Failed for {well_name}"


def test_kernel_detects_nm_jurisdiction():
    """Test that kernel correctly identifies NM jurisdiction from API."""
    from apps.kernel.services.policy_kernel import _get_jurisdiction

    resolved_facts = {
        "api14": {"value": "30015286920000"},
        "state": {"value": "NM"}
    }
    policy = {}

    jurisdiction = _get_jurisdiction(resolved_facts, policy)
    assert jurisdiction == "NM"


def test_formula_engine_supports_nm():
    """Test that formula engine supports NM jurisdiction."""
    from apps.policy.services.formula_engine import get_formula_engine, list_supported_jurisdictions

    supported = list_supported_jurisdictions()
    assert "NM" in supported

    engine = get_formula_engine("NM")
    assert engine.jurisdiction == "NM"
    assert engine.primary_citation == "NMAC 19.15.25"


def test_nm_formula_engine_calculations():
    """Test NM-specific formula calculations."""
    from apps.policy.services.formula_engine import get_formula_engine

    engine = get_formula_engine("NM")

    # Test cement excess (flat rate, not depth-based)
    cased_excess = engine.cement_excess_for_hole_type("cased")
    assert cased_excess == 1.5

    open_excess = engine.cement_excess_for_hole_type("open")
    assert open_excess == 2.0

    # Test coverage requirements
    casing_coverage = engine.coverage_requirement_ft("casing_shoe")
    assert casing_coverage == 50

    cibp_coverage = engine.coverage_requirement_ft("cibp_cap")
    assert cibp_coverage == 100

    # Test cement class
    shallow_class = engine.cement_class_for_depth(5000)
    assert shallow_class == "C"

    deep_class = engine.cement_class_for_depth(8000)
    assert deep_class == "H"
