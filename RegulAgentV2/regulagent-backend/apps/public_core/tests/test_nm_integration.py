"""
Tests for NM well integration.

Tests the complete workflow of importing NM wells from scraper to WellRegistry
and plan generation.
"""
import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock

from apps.public_core.models import WellRegistry
from apps.public_core.services.nm_well_import import _map_nm_data_to_well_registry, batch_import_nm_wells, import_nm_well
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


@pytest.mark.django_db
class TestNMWellImport:
    """Test NM well import service."""

    @patch('apps.public_core.services.nm_well_import.NMWellScraper')
    def test_import_nm_well_creates_new_well(self, mock_scraper_class):
        """Test importing a new NM well creates WellRegistry entry."""
        # Mock scraper
        mock_scraper = MagicMock()
        mock_scraper.__enter__ = MagicMock(return_value=mock_scraper)
        mock_scraper.__exit__ = MagicMock(return_value=False)
        mock_scraper.fetch_well = MagicMock(return_value=_get_mock_nm_well_data())
        mock_scraper_class.return_value = mock_scraper

        # Import well
        result = import_nm_well("30-015-28692")

        # Assertions
        assert result["status"] == "created"
        assert result["well"].api14 == "30015286920000"
        assert result["well"].state == "NM"
        assert result["well"].operator_name == "EOG RESOURCES INC"
        assert result["well"].field_name == "BONE SPRING"
        assert result["well"].county == "LEA"
        assert result["well"].lat == Decimal("32.7574387")
        assert result["well"].lon == Decimal("-104.0298615")

    @patch('apps.public_core.services.nm_well_import.NMWellScraper')
    def test_import_nm_well_updates_existing_well(self, mock_scraper_class, db):
        """Test importing existing NM well updates fields."""
        # Create existing well with incomplete data
        existing_well = WellRegistry.objects.create(
            api14="30015286920000",
            state="NM",
            operator_name=""  # Empty, should be updated
        )

        # Mock scraper
        mock_scraper = MagicMock()
        mock_scraper.__enter__ = MagicMock(return_value=mock_scraper)
        mock_scraper.__exit__ = MagicMock(return_value=False)
        mock_scraper.fetch_well = MagicMock(return_value=_get_mock_nm_well_data())
        mock_scraper_class.return_value = mock_scraper

        # Import well with update_existing=True
        result = import_nm_well("30-015-28692", update_existing=True)

        # Assertions
        assert result["status"] == "updated"
        assert result["well"].id == existing_well.id
        assert result["well"].operator_name == "EOG RESOURCES INC"
        assert result["well"].field_name == "BONE SPRING"

    @patch('apps.public_core.services.nm_well_import.NMWellScraper')
    def test_import_nm_well_skips_existing_when_update_false(self, mock_scraper_class, db):
        """Test that existing well is not updated when update_existing=False."""
        # Create existing well
        existing_well = WellRegistry.objects.create(
            api14="30015286920000",
            state="NM",
            operator_name="OLD OPERATOR"
        )

        # Mock scraper
        mock_scraper = MagicMock()
        mock_scraper.__enter__ = MagicMock(return_value=mock_scraper)
        mock_scraper.__exit__ = MagicMock(return_value=False)
        mock_scraper.fetch_well = MagicMock(return_value=_get_mock_nm_well_data())
        mock_scraper_class.return_value = mock_scraper

        # Import well with update_existing=False
        result = import_nm_well("30-015-28692", update_existing=False)

        # Assertions
        assert result["status"] == "exists"
        assert result["well"].operator_name == "OLD OPERATOR"

    def test_map_nm_data_to_well_registry(self):
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

    def test_map_nm_data_parses_well_name(self):
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


@pytest.mark.django_db
class TestNMWellPlanGeneration:
    """Test plan generation for NM wells."""

    def test_nm_well_has_correct_state(self, db):
        """Test NM well has correct state."""
        nm_well = WellRegistry.objects.create(
            api14="30015286920000",
            state="NM",
            county="LEA"
        )
        assert nm_well.state == "NM"
        assert nm_well.api14.startswith("30")

    def test_kernel_detects_nm_jurisdiction(self):
        """Test that kernel correctly identifies NM jurisdiction from API."""
        from apps.kernel.services.policy_kernel import _get_jurisdiction

        resolved_facts = {
            "api14": {"value": "30015286920000"},
            "state": {"value": "NM"}
        }
        policy = {}

        jurisdiction = _get_jurisdiction(resolved_facts, policy)
        assert jurisdiction == "NM"

    def test_formula_engine_supports_nm(self):
        """Test that formula engine supports NM jurisdiction."""
        from apps.policy.services.formula_engine import get_formula_engine, list_supported_jurisdictions

        supported = list_supported_jurisdictions()
        assert "NM" in supported

        engine = get_formula_engine("NM")
        assert engine.jurisdiction == "NM"
        assert engine.primary_citation == "NMAC 19.15.25"

    def test_nm_formula_engine_calculations(self):
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
