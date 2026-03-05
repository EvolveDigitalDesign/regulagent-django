"""
Tests for NM well orchestration flow.

Tests the integration of NM wells into the W-3A plan generation workflow,
including jurisdiction detection, extraction mapping, and orchestrator routing.

Uses plain pytest functions (no Django TestCase) to avoid database setup issues.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from apps.public_core.services.w3a_orchestrator import (
    _detect_jurisdiction,
    fetch_nm_extraction_data,
)
from apps.public_core.services.nm_extraction_mapper import (
    map_nm_well_to_extractions,
    map_nm_well_to_geometry,
    _parse_well_name,
    _extract_county,
    format_nm_api_for_display,
)


# =============================================================================
# Jurisdiction Detection Tests
# =============================================================================

def test_detect_nm_from_api_prefix_30():
    """NM wells start with state code 30."""
    assert _detect_jurisdiction("3001528692") == "NM"
    assert _detect_jurisdiction("30-015-28692") == "NM"
    assert _detect_jurisdiction("30015286920000") == "NM"


def test_detect_tx_from_api_prefix_42():
    """TX wells start with state code 42."""
    assert _detect_jurisdiction("4250170575") == "TX"
    assert _detect_jurisdiction("42-501-70575") == "TX"
    assert _detect_jurisdiction("42501705750000") == "TX"


def test_explicit_jurisdiction_overrides_detection():
    """Explicit jurisdiction parameter takes precedence."""
    # NM API but TX explicitly specified
    assert _detect_jurisdiction("3001528692", "TX") == "TX"
    # TX API but NM explicitly specified
    assert _detect_jurisdiction("4250170575", "NM") == "NM"


def test_defaults_to_tx_for_unknown_prefix():
    """Unknown state codes default to TX."""
    assert _detect_jurisdiction("9999999999") == "TX"
    assert _detect_jurisdiction("") == "TX"


# =============================================================================
# NM Extraction Mapper Tests
# =============================================================================

def _get_sample_well_data():
    """Create sample NM well data for testing (without casing/completion data)."""
    return {
        "api10": "30-015-28692",
        "api14": "30015286920000",
        "well_name": "FEDERAL 1-30H",
        "operator_name": "EOG RESOURCES INC",
        "operator_number": "7377",
        "status": "Active",
        "well_type": "Oil",
        "direction": "Horizontal",
        "surface_location": "320 FNL, 660 FWL, SEC 30 T16S R33E, LEA COUNTY",
        "latitude": 32.7574387,
        "longitude": -104.0298615,
        "elevation_ft": 3450.0,
        "proposed_depth_ft": 12500,
        "tvd_ft": 10200,
        "formation": "Bone Spring",
        "spud_date": "01/15/2024",
        "completion_date": "03/20/2024",
    }


def _get_sample_well_data_with_casing():
    """Create sample NM well data with casing and completion data."""
    return {
        "api10": "30-015-28692",
        "api14": "30015286920000",
        "well_name": "FEDERAL 1-30H",
        "operator_name": "EOG RESOURCES INC",
        "operator_number": "7377",
        "status": "Active",
        "well_type": "Oil",
        "direction": "Horizontal",
        "surface_location": "320 FNL, 660 FWL, SEC 30 T16S R33E, LEA COUNTY",
        "latitude": 32.7574387,
        "longitude": -104.0298615,
        "elevation_ft": 3450.0,
        "proposed_depth_ft": 12500,
        "tvd_ft": 10200,
        "formation": "Bone Spring",
        "spud_date": "01/15/2024",
        "completion_date": "03/20/2024",
        "casing_records": [
            {
                "string_type": "Surface Casing",
                "diameter_in": 13.375,
                "top_ft": 0,
                "bottom_ft": 1200,
                "cement_top_ft": 0,
                "cement_bottom_ft": 1200,
                "cement_sacks": 500,
                "grade": "K-55",
                "weight_ppf": 54.5,
            },
            {
                "string_type": "Production Casing",
                "diameter_in": 5.5,
                "top_ft": 0,
                "bottom_ft": 10500,
                "cement_top_ft": 8000,
                "cement_bottom_ft": 10500,
                "cement_sacks": 800,
                "grade": "P-110",
                "weight_ppf": 23.0,
            },
        ],
        "completions": [
            {
                "completion_id": "84872",
                "completion_name": "BONE SPRING (OIL)",
                "status": "Active",
                "production_method": "Flowing",
                "perforations": [
                    {
                        "top_md_ft": 10200,
                        "bottom_md_ft": 10350,
                        "top_vd_ft": 9800,
                        "bottom_vd_ft": 9900,
                    },
                    {
                        "top_md_ft": 10400,
                        "bottom_md_ft": 10500,
                        "top_vd_ft": 9950,
                        "bottom_vd_ft": 10050,
                    },
                ],
            },
        ],
    }


def test_map_nm_well_to_extractions_structure():
    """Test that extraction mapping creates expected structure."""
    well_data = _get_sample_well_data()
    result = map_nm_well_to_extractions(well_data)

    # Should have c105 key (NM equivalent of W-2)
    assert "c105" in result
    c105 = result["c105"]

    # Header should have API info
    assert c105["header"]["api"] == "30-015-28692"
    assert c105["header"]["api14"] == "30015286920000"

    # Operator info
    assert c105["operator_info"]["name"] == "EOG RESOURCES INC"
    assert c105["operator_info"]["operator_number"] == "7377"

    # Well info
    assert c105["well_info"]["well_name"] == "FEDERAL 1-30H"
    assert c105["well_info"]["status"] == "Active"
    assert c105["well_info"]["direction"] == "Horizontal"

    # Location
    assert c105["well_info"]["location"]["lat"] == 32.7574387
    assert c105["well_info"]["location"]["lon"] == -104.0298615

    # Depths
    assert c105["depths"]["proposed_depth"] == 12500
    assert c105["depths"]["tvd"] == 10200

    # Empty arrays when no casing/completion data is provided
    assert c105["casing_record"] == []
    assert c105["producing_injection_disposal_interval"] == []
    assert c105["completions"] == []

    # Metadata indicates manual entry required when no data
    assert c105["_metadata"]["requires_manual_casing_entry"] is True
    assert c105["_metadata"]["has_scraped_casing"] is False
    assert c105["_metadata"]["has_scraped_perforations"] is False


def test_map_nm_well_to_geometry_empty_structures():
    """NM geometry should have empty structures when no casing data provided."""
    well_data = _get_sample_well_data()
    result = map_nm_well_to_geometry(well_data)

    assert result["casing_strings"] == []
    assert result["formation_tops"] == []
    assert result["perforations"] == []
    assert result["mechanical_barriers"] == []
    assert result["_nm_metadata"]["requires_manual_entry"] is True
    assert result["_nm_metadata"]["has_scraped_casing"] is False
    assert result["_nm_metadata"]["has_scraped_perforations"] is False


def test_map_nm_well_to_extractions_with_casing():
    """Test extraction mapping with casing and completion data."""
    well_data = _get_sample_well_data_with_casing()
    result = map_nm_well_to_extractions(well_data)
    c105 = result["c105"]

    # Should have casing records
    assert len(c105["casing_record"]) == 2
    assert c105["casing_record"][0]["casing_type"] == "Surface Casing"
    assert c105["casing_record"][0]["diameter"] == 13.375
    assert c105["casing_record"][0]["bottom"] == 1200
    assert c105["casing_record"][0]["sacks"] == 500

    assert c105["casing_record"][1]["casing_type"] == "Production Casing"
    assert c105["casing_record"][1]["diameter"] == 5.5

    # Should have perforations collected from completions
    assert len(c105["producing_injection_disposal_interval"]) == 2
    assert c105["producing_injection_disposal_interval"][0]["top_md"] == 10200
    assert c105["producing_injection_disposal_interval"][0]["bottom_md"] == 10350
    assert c105["producing_injection_disposal_interval"][0]["completion_name"] == "BONE SPRING (OIL)"

    # Should have completions
    assert len(c105["completions"]) == 1
    assert c105["completions"][0]["id"] == "84872"
    assert c105["completions"][0]["name"] == "BONE SPRING (OIL)"
    assert len(c105["completions"][0]["perforations"]) == 2

    # Metadata should indicate scraped data available
    assert c105["_metadata"]["requires_manual_casing_entry"] is False
    assert c105["_metadata"]["has_scraped_casing"] is True
    assert c105["_metadata"]["has_scraped_perforations"] is True


def test_map_nm_well_to_geometry_with_casing():
    """Test geometry mapping with casing and completion data."""
    well_data = _get_sample_well_data_with_casing()
    result = map_nm_well_to_geometry(well_data)

    # Should have casing strings
    assert len(result["casing_strings"]) == 2
    assert result["casing_strings"][0]["type"] == "Surface Casing"
    assert result["casing_strings"][0]["od_in"] == 13.375
    assert result["casing_strings"][0]["bottom_ft"] == 1200
    assert result["casing_strings"][0]["cement_top_ft"] == 0
    assert result["casing_strings"][0]["cement_bottom_ft"] == 1200

    # Should have perforations
    assert len(result["perforations"]) == 2
    assert result["perforations"][0]["top_md"] == 10200
    assert result["perforations"][0]["bottom_md"] == 10350
    assert result["perforations"][0]["top_vd"] == 9800
    assert result["perforations"][0]["bottom_vd"] == 9900

    # Metadata should indicate scraped data available
    assert result["_nm_metadata"]["requires_manual_entry"] is False
    assert result["_nm_metadata"]["has_scraped_casing"] is True
    assert result["_nm_metadata"]["has_scraped_perforations"] is True


# =============================================================================
# Well Name Parsing Tests
# =============================================================================

def test_parse_simple_well_name():
    """Parse 'FEDERAL 1' format."""
    lease, well_no = _parse_well_name("FEDERAL 1")
    assert lease == "FEDERAL"
    assert well_no == "1"


def test_parse_horizontal_well_name():
    """Parse 'FEDERAL 1-30H' format."""
    lease, well_no = _parse_well_name("FEDERAL 1-30H")
    assert lease == "FEDERAL"
    assert well_no == "1-30H"


def test_parse_multi_word_lease():
    """Parse 'STATE FEDERAL 1' format."""
    lease, well_no = _parse_well_name("STATE FEDERAL 1")
    assert lease == "STATE FEDERAL"
    assert well_no == "1"


def test_parse_name_with_hash():
    """Parse 'BLM 1-30H #1' format."""
    lease, well_no = _parse_well_name("BLM 1-30H #1")
    assert lease == "BLM 1-30H"
    assert well_no == "#1"


def test_parse_empty_name():
    """Handle empty well name."""
    lease, well_no = _parse_well_name("")
    assert lease == ""
    assert well_no == ""


# =============================================================================
# County Extraction Tests
# =============================================================================

def test_extract_county_from_location():
    """Extract county from typical NM surface location."""
    location = "320 FNL, 660 FWL, SEC 30 T16S R33E, LEA COUNTY"
    county = _extract_county(location)
    assert county == "Lea"


def test_extract_county_with_nm_suffix():
    """Extract county when location includes state."""
    location = "LOT 1, SEC 1 T1N R1E, EDDY COUNTY, NM"
    county = _extract_county(location)
    assert county == "Eddy"


def test_extract_county_empty_location():
    """Handle empty location string."""
    county = _extract_county("")
    assert county == ""


def test_extract_county_no_county_keyword():
    """Handle location without COUNTY keyword."""
    location = "320 FNL, 660 FWL, SEC 30 T16S R33E"
    county = _extract_county(location)
    assert county == ""


# =============================================================================
# API Formatting Tests
# =============================================================================

def test_format_10_digit_api():
    """Format 10-digit API."""
    assert format_nm_api_for_display("3001528692") == "30-015-28692"


def test_format_14_digit_api():
    """Format 14-digit API (truncates to 10)."""
    assert format_nm_api_for_display("30015286920000") == "30-015-28692"


def test_format_already_formatted_api():
    """Return as-is if already formatted."""
    assert format_nm_api_for_display("30-015-28692") == "30-015-28692"


# =============================================================================
# Fetch NM Extraction Data Tests (mocked)
# =============================================================================

@patch('apps.public_core.services.nm_document_fetcher.NMDocumentFetcher')
@patch('apps.public_core.services.nm_well_scraper.fetch_nm_well')
def test_fetch_nm_extraction_data_success(mock_fetch_well, mock_fetcher_class):
    """Test successful NM data fetch."""
    # Mock the scraper response
    mock_well = Mock()
    mock_well.to_dict.return_value = {
        "api10": "30-015-28692",
        "api14": "30015286920000",
        "well_name": "FEDERAL 1",
        "operator_name": "EOG",
    }
    mock_fetch_well.return_value = mock_well

    # Mock the document fetcher
    mock_fetcher = MagicMock()
    mock_fetcher.list_documents.return_value = []
    mock_fetcher.get_combined_pdf_url.return_value = "https://example.com/combined.pdf"
    mock_fetcher.__enter__ = Mock(return_value=mock_fetcher)
    mock_fetcher.__exit__ = Mock(return_value=False)
    mock_fetcher_class.return_value = mock_fetcher

    result = fetch_nm_extraction_data("30-015-28692")

    assert result["status"] == "success"
    assert result["source"] == "nm_ocd_scraper"
    assert "well_data" in result
    assert "extraction" in result
    assert result["combined_pdf_url"] == "https://example.com/combined.pdf"


@patch('apps.public_core.services.nm_well_scraper.fetch_nm_well')
def test_fetch_nm_extraction_data_scrape_error(mock_fetch_well):
    """Test handling of scraping errors."""
    mock_fetch_well.side_effect = Exception("Scraping failed")

    with pytest.raises(Exception) as exc_info:
        fetch_nm_extraction_data("30-015-28692")

    assert "Scraping failed" in str(exc_info.value)
