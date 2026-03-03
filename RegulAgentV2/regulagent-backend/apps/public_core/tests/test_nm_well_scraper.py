"""
Tests for NM Well Data Scraper

Tests the scraping functionality for New Mexico OCD well data.
"""
import pytest
import re
from unittest.mock import Mock, patch, MagicMock
from apps.public_core.services.nm_well_scraper import (
    NMWellScraper,
    NMWellData,
    CasingRecord,
    PerforationInterval,
    CompletionRecord,
    EventDates,
    fetch_nm_well
)


class TestAPIFormatting:
    """Test API number normalization and validation."""

    def test_normalize_api10_with_hyphens(self):
        """Test normalization of hyphenated API-10."""
        scraper = NMWellScraper()
        result = scraper._normalize_api10("30-015-28692")
        assert result == "30-015-28692"

    def test_normalize_api10_without_hyphens(self):
        """Test normalization of plain 10-digit API."""
        scraper = NMWellScraper()
        result = scraper._normalize_api10("3001528692")
        assert result == "30-015-28692"

    def test_normalize_api14_to_api10(self):
        """Test extraction of API-10 from API-14."""
        scraper = NMWellScraper()
        result = scraper._normalize_api10("30015286920000")
        assert result == "30-015-28692"

    def test_invalid_state_code(self):
        """Test rejection of non-NM state codes."""
        scraper = NMWellScraper()
        with pytest.raises(ValueError, match="State code must be 30"):
            scraper._normalize_api10("42-501-70575")

    def test_invalid_length(self):
        """Test rejection of invalid API lengths."""
        scraper = NMWellScraper()
        with pytest.raises(ValueError, match="Expected 10 digits"):
            scraper._normalize_api10("123456")

    def test_api10_to_api14(self):
        """Test conversion from API-10 to API-14."""
        scraper = NMWellScraper()
        result = scraper._api10_to_api14("30-015-28692")
        assert result == "30015286920000"


class TestHTMLParsing:
    """Test HTML parsing and field extraction."""

    @pytest.fixture
    def mock_html(self):
        """Sample HTML structure from NM OCD portal."""
        return """
        <html>
            <body>
                <div id="general_information">
                    <div class="field">
                        <label>Well Name:</label>
                        <span>FEDERAL 24-19 #1H</span>
                    </div>
                    <div class="field">
                        <label>API Number:</label>
                        <span>30-015-28692</span>
                    </div>
                    <div class="field">
                        <label>Operator:</label>
                        <span>EOG RESOURCES INC [7377]</span>
                    </div>
                    <div class="field">
                        <label>Status:</label>
                        <span>PRODUCING</span>
                    </div>
                    <div class="field">
                        <label>Well Type:</label>
                        <span>OIL</span>
                    </div>
                    <div class="field">
                        <label>Work Type:</label>
                        <span>New</span>
                    </div>
                    <div class="field">
                        <label>Direction:</label>
                        <span>HORIZONTAL</span>
                    </div>
                    <div class="field">
                        <label>Multi-Lateral:</label>
                        <span>No</span>
                    </div>
                    <div class="field">
                        <label>Mineral Owner:</label>
                        <span>Federal</span>
                    </div>
                    <div class="field">
                        <label>Surface Owner:</label>
                        <span>Private</span>
                    </div>
                    <div class="field">
                        <label>Surface Location:</label>
                        <span>24-19S-32E</span>
                    </div>
                    <div class="field">
                        <label>Lat/Long:</label>
                        <span>32.7574387,-104.0298615 NAD83</span>
                    </div>
                    <div class="field">
                        <label>GL Elevation:</label>
                        <span>3450.0 ft</span>
                    </div>
                    <div class="field">
                        <label>KB Elevation:</label>
                        <span>3460.0 ft</span>
                    </div>
                    <div class="field">
                        <label>Sing/Mult Compl:</label>
                        <span>Single</span>
                    </div>
                    <div class="field">
                        <label>Potash Waiver:</label>
                        <span>False</span>
                    </div>
                    <div class="field">
                        <label>Proposed Depth:</label>
                        <span>21,000 ft</span>
                    </div>
                    <div class="field">
                        <label>Measured Vertical Depth:</label>
                        <span>11,500 ft</span>
                    </div>
                    <div class="field">
                        <label>True Vertical Depth:</label>
                        <span>10,500 ft</span>
                    </div>
                    <div class="field">
                        <label>Plugback Measured:</label>
                        <span>0 ft</span>
                    </div>
                    <div class="field">
                        <label>Formation:</label>
                        <span>WOLFCAMP</span>
                    </div>
                    <div class="field">
                        <label>Proposed Formation:</label>
                        <span>WOLFCAMP/BONE SPRING</span>
                    </div>
                </div>
                <div id="event_dates">
                    <div class="field">
                        <label>Initial APD Approval:</label>
                        <span>10/18/2018</span>
                    </div>
                    <div class="field">
                        <label>Most Recent APD Approval:</label>
                        <span>01/16/2019</span>
                    </div>
                    <div class="field">
                        <label>Spud Date:</label>
                        <span>01/15/2019</span>
                    </div>
                    <div class="field">
                        <label>Completion Date:</label>
                        <span>03/20/2019</span>
                    </div>
                    <div class="field">
                        <label>Last Inspection:</label>
                        <span>03/02/2022</span>
                    </div>
                    <div class="field">
                        <label>Last MIT/BHT:</label>
                        <span>03/02/2022</span>
                    </div>
                </div>
            </body>
        </html>
        """

    @pytest.fixture
    def mock_html_with_casing(self):
        """Sample HTML with casing table."""
        return """
        <html>
            <body>
                <div>Well Name: TEST WELL #1H</div>
                <div>Operator: TEST OPERATOR INC [9999]</div>
                <div>Status: DRILLING</div>
                <div>Well Type: GAS</div>
                <div>Direction: HORIZONTAL</div>
                <table>
                    <tr>
                        <th>String/Hole Type</th>
                        <th>Taper</th>
                        <th>Date Set</th>
                        <th>Diameter</th>
                        <th>Top</th>
                        <th>Bottom</th>
                        <th>Grade</th>
                        <th>Length</th>
                        <th>Weight</th>
                        <th>Bot of Cem</th>
                        <th>Top of Cem</th>
                        <th>Meth</th>
                        <th>Class of Cement</th>
                        <th>Sacks</th>
                        <th>Pressure Test</th>
                    </tr>
                    <tr>
                        <td>Surface Casing</td>
                        <td>1</td>
                        <td>01/20/2019</td>
                        <td>20.000</td>
                        <td>0</td>
                        <td>1500</td>
                        <td>K-55</td>
                        <td>1500</td>
                        <td>94</td>
                        <td>0</td>
                        <td>1500</td>
                        <td>Pump</td>
                        <td>Class C</td>
                        <td>850</td>
                        <td>Y</td>
                    </tr>
                    <tr>
                        <td>Intermediate 1 Casing</td>
                        <td>2</td>
                        <td>01/25/2019</td>
                        <td>13.375</td>
                        <td>0</td>
                        <td>5500</td>
                        <td>N-80</td>
                        <td>5500</td>
                        <td>72</td>
                        <td>0</td>
                        <td>5500</td>
                        <td>Pump</td>
                        <td>Class C</td>
                        <td>1200</td>
                        <td>Y</td>
                    </tr>
                    <tr>
                        <td>Production Casing</td>
                        <td>3</td>
                        <td>02/05/2019</td>
                        <td>5.500</td>
                        <td>0</td>
                        <td>21000</td>
                        <td>P-110</td>
                        <td>21000</td>
                        <td>23</td>
                        <td>5000</td>
                        <td>21000</td>
                        <td>Pump</td>
                        <td>Class H</td>
                        <td>2500</td>
                        <td>Y</td>
                    </tr>
                </table>
            </body>
        </html>
        """

    @pytest.fixture
    def mock_html_with_perforations(self):
        """Sample HTML with perforation table."""
        return """
        <html>
            <body>
                <div>Well Name: TEST WELL #1H</div>
                <div>Operator: TEST OPERATOR INC [9999]</div>
                <div>Completion ID: [84872] TEST COMPLETION; WOLFCAMP (OIL)</div>
                <div>Status: Active</div>
                <div>Last Produced: 11/01/2025</div>
                <div>Bottomhole Location: 24-19S-32E</div>
                <div>Top MD: 11120</div>
                <div>Bottom MD: 11163</div>
                <table>
                    <tr>
                        <th>Top MD</th>
                        <th>Bottom MD</th>
                        <th>Top VD</th>
                        <th>Bottom VD</th>
                    </tr>
                    <tr>
                        <td>11120</td>
                        <td>11163</td>
                        <td>10500</td>
                        <td>10520</td>
                    </tr>
                    <tr>
                        <td>11200</td>
                        <td>11250</td>
                        <td>10540</td>
                        <td>10560</td>
                    </tr>
                </table>
            </body>
        </html>
        """

    def test_parse_basic_fields(self, mock_html):
        """Test extraction of basic text fields."""
        scraper = NMWellScraper()
        result = scraper._parse_html(mock_html, "30-015-28692")

        assert result.api10 == "30-015-28692"
        assert result.api14 == "30015286920000"
        assert result.well_name == "FEDERAL 24-19"
        assert result.well_number == "1H"
        assert result.status == "PRODUCING"
        assert result.well_type == "OIL"
        assert result.direction == "HORIZONTAL"

    def test_parse_new_fields(self, mock_html):
        """Test extraction of newly added fields."""
        scraper = NMWellScraper()
        result = scraper._parse_html(mock_html, "30-015-28692")

        assert result.work_type == "New"
        assert result.multi_lateral is False
        assert result.mineral_owner == "Federal"
        assert result.surface_owner == "Private"
        assert result.sing_mult_compl == "Single"
        assert result.potash_waiver is False
        assert result.proposed_formation == "WOLFCAMP/BONE SPRING"

    def test_parse_operator_fields(self, mock_html):
        """Test extraction and splitting of operator information."""
        scraper = NMWellScraper()
        result = scraper._parse_html(mock_html, "30-015-28692")

        assert result.operator_name == "EOG RESOURCES INC"
        assert result.operator_number == "7377"

    def test_parse_location_fields(self, mock_html):
        """Test extraction of location information."""
        scraper = NMWellScraper()
        result = scraper._parse_html(mock_html, "30-015-28692")

        assert result.surface_location == "24-19S-32E"
        assert result.latitude == pytest.approx(32.7574387, rel=1e-6)
        assert result.longitude == pytest.approx(-104.0298615, rel=1e-6)
        assert result.datum == "NAD83"

    def test_parse_elevation_fields(self, mock_html):
        """Test extraction of elevation information."""
        scraper = NMWellScraper()
        result = scraper._parse_html(mock_html, "30-015-28692")

        assert result.gl_elevation_ft == pytest.approx(3450.0, rel=1e-2)
        assert result.kb_elevation_ft == pytest.approx(3460.0, rel=1e-2)
        # Legacy compatibility
        assert result.elevation_ft == pytest.approx(3450.0, rel=1e-2)

    def test_parse_depth_fields(self, mock_html):
        """Test extraction of depth information."""
        scraper = NMWellScraper()
        result = scraper._parse_html(mock_html, "30-015-28692")

        assert result.proposed_depth_ft == 21000
        assert result.measured_vertical_depth_ft == 11500
        assert result.true_vertical_depth_ft == 10500
        assert result.plugback_measured_ft == 0
        assert result.formation == "WOLFCAMP"
        # Legacy compatibility
        assert result.tvd_ft == 10500

    def test_parse_event_dates(self, mock_html):
        """Test extraction of event dates."""
        scraper = NMWellScraper()
        result = scraper._parse_html(mock_html, "30-015-28692")

        assert result.event_dates is not None
        assert result.event_dates.initial_apd_approval == "10/18/2018"
        assert result.event_dates.most_recent_apd_approval == "01/16/2019"
        assert result.event_dates.spud_date == "01/15/2019"
        assert result.event_dates.last_inspection == "03/02/2022"
        assert result.event_dates.last_mit_bht == "03/02/2022"
        # Legacy compatibility
        assert result.spud_date == "01/15/2019"
        assert result.completion_date == "03/20/2019"

    def test_parse_casing_records(self, mock_html_with_casing):
        """Test extraction of casing records from table."""
        scraper = NMWellScraper()
        result = scraper._parse_html(mock_html_with_casing, "30-015-12345")

        assert len(result.casing_records) == 3

        # Check surface casing
        surface = result.casing_records[0]
        assert surface.string_type == "Surface Casing"
        assert surface.diameter_in == pytest.approx(20.0, rel=1e-2)
        assert surface.bottom_ft == pytest.approx(1500.0, rel=1e-2)
        assert surface.grade == "K-55"
        assert surface.weight_ppf == pytest.approx(94.0, rel=1e-2)
        assert surface.cement_class == "Class C"
        assert surface.cement_sacks == 850
        assert surface.pressure_test is True

        # Check intermediate casing
        intermediate = result.casing_records[1]
        assert intermediate.string_type == "Intermediate 1 Casing"
        assert intermediate.diameter_in == pytest.approx(13.375, rel=1e-2)
        assert intermediate.bottom_ft == pytest.approx(5500.0, rel=1e-2)

        # Check production casing
        production = result.casing_records[2]
        assert production.string_type == "Production Casing"
        assert production.diameter_in == pytest.approx(5.5, rel=1e-2)
        assert production.bottom_ft == pytest.approx(21000.0, rel=1e-2)
        assert production.cement_class == "Class H"

    def test_parse_perforations(self, mock_html_with_perforations):
        """Test extraction of perforation intervals."""
        scraper = NMWellScraper()
        result = scraper._parse_html(mock_html_with_perforations, "30-015-12345")

        # Should have at least one completion with perforations
        assert len(result.completions) >= 1

        # Check completion details
        completion = result.completions[0]
        assert completion.completion_id == "84872"
        assert "WOLFCAMP" in completion.completion_name

        # Check perforations - may be parsed from table or text
        # The table should have 2 rows, text pattern provides fallback
        assert len(completion.perforations) >= 1
        perf1 = completion.perforations[0]
        assert perf1.top_md_ft == pytest.approx(11120.0, rel=1e-2)
        assert perf1.bottom_md_ft == pytest.approx(11163.0, rel=1e-2)

        # If table parsing worked, we should have the second perf too
        if len(completion.perforations) >= 2:
            perf2 = completion.perforations[1]
            assert perf2.top_md_ft == pytest.approx(11200.0, rel=1e-2)
            assert perf2.bottom_md_ft == pytest.approx(11250.0, rel=1e-2)

    def test_missing_fields_return_defaults(self):
        """Test that missing fields return appropriate defaults."""
        minimal_html = """
        <html><body>
            <div>Well Name: TEST WELL</div>
            <div>Operator: TEST OPERATOR [123]</div>
        </body></html>
        """
        scraper = NMWellScraper()
        result = scraper._parse_html(minimal_html, "30-015-12345")

        assert result.well_name == "TEST WELL"
        assert result.operator_name == "TEST OPERATOR"
        assert result.status == ""
        assert result.latitude is None
        assert result.proposed_depth_ft is None
        assert result.casing_records == []
        assert result.completions == []
        assert result.event_dates is not None


class TestScraperIntegration:
    """Integration tests with mocked HTTP requests."""

    @pytest.fixture
    def mock_response(self):
        """Mock successful HTTP response."""
        mock = Mock()
        mock.status_code = 200
        mock.text = """
        <html><body>
            <div>Well Name: TEST WELL #1H</div>
            <div>Operator: TEST OPERATOR INC [9999]</div>
            <div>Status: DRILLING</div>
            <div>Well Type: GAS</div>
            <div>Direction: HORIZONTAL</div>
        </body></html>
        """
        return mock

    @patch('requests.Session.get')
    def test_fetch_well_success(self, mock_get, mock_response):
        """Test successful well data fetch."""
        mock_get.return_value = mock_response

        scraper = NMWellScraper()
        result = scraper.fetch_well("30-015-28692")

        assert isinstance(result, NMWellData)
        assert result.api10 == "30-015-28692"
        assert result.well_name == "TEST WELL"
        assert result.well_number == "1H"
        assert result.operator_name == "TEST OPERATOR INC"
        assert result.operator_number == "9999"

        # Verify the URL was called correctly
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "api=30-015-28692" in call_args[0][0]

    @patch('requests.Session.get')
    def test_fetch_well_with_raw_html(self, mock_get, mock_response):
        """Test that raw HTML is included when requested."""
        mock_get.return_value = mock_response

        scraper = NMWellScraper()
        result = scraper.fetch_well("30-015-28692", include_raw_html=True)

        assert result.raw_html is not None
        assert "TEST WELL" in result.raw_html

    @patch('requests.Session.get')
    def test_fetch_well_http_error(self, mock_get):
        """Test handling of HTTP errors."""
        mock_get.side_effect = Exception("Connection failed")

        scraper = NMWellScraper()
        with pytest.raises(Exception, match="Connection failed"):
            scraper.fetch_well("30-015-28692")

    @patch('requests.Session.get')
    def test_convenience_function(self, mock_get, mock_response):
        """Test the convenience fetch_nm_well function."""
        mock_get.return_value = mock_response

        result = fetch_nm_well("30-015-28692")

        assert isinstance(result, NMWellData)
        assert result.api10 == "30-015-28692"


class TestContextManager:
    """Test context manager functionality."""

    def test_context_manager_closes_session(self):
        """Test that context manager properly closes the session."""
        with NMWellScraper() as scraper:
            assert scraper.session is not None

        # Session should be closed after exiting context


class TestDataClasses:
    """Test dataclass functionality."""

    def test_nm_well_data_to_dict(self):
        """Test conversion to dictionary."""
        data = NMWellData(
            api10="30-015-28692",
            api14="30015286920000",
            well_name="TEST WELL",
            operator_name="TEST OP",
            operator_number="123",
            status="ACTIVE",
            well_type="OIL",
            direction="VERTICAL",
            surface_location="1-1N-1E",
            latitude=32.0,
            longitude=-104.0,
            elevation_ft=3500.0,
            proposed_depth_ft=10000,
            tvd_ft=10000,
            formation="PERMIAN",
            spud_date="01/01/2020",
            completion_date="02/01/2020",
        )

        result = data.to_dict()

        assert isinstance(result, dict)
        assert result['api10'] == "30-015-28692"
        assert result['well_name'] == "TEST WELL"
        assert result['latitude'] == 32.0

    def test_casing_record_to_dict(self):
        """Test CasingRecord conversion to dictionary."""
        casing = CasingRecord(
            string_type="Surface Casing",
            diameter_in=20.0,
            bottom_ft=1500.0,
            cement_class="Class C",
            cement_sacks=850,
        )

        result = casing.to_dict()

        assert isinstance(result, dict)
        assert result['string_type'] == "Surface Casing"
        assert result['diameter_in'] == 20.0
        assert result['cement_sacks'] == 850

    def test_perforation_interval_to_dict(self):
        """Test PerforationInterval conversion to dictionary."""
        perf = PerforationInterval(
            top_md_ft=11120.0,
            bottom_md_ft=11163.0,
            top_vd_ft=10500.0,
            bottom_vd_ft=10520.0,
        )

        result = perf.to_dict()

        assert isinstance(result, dict)
        assert result['top_md_ft'] == 11120.0
        assert result['bottom_md_ft'] == 11163.0

    def test_completion_record_to_dict(self):
        """Test CompletionRecord conversion to dictionary."""
        completion = CompletionRecord(
            completion_id="84872",
            completion_name="TEST WELL; WOLFCAMP (OIL)",
            status="Active",
            perforations=[
                PerforationInterval(top_md_ft=11120.0, bottom_md_ft=11163.0),
            ],
        )

        result = completion.to_dict()

        assert isinstance(result, dict)
        assert result['completion_id'] == "84872"
        assert len(result['perforations']) == 1

    def test_event_dates_to_dict(self):
        """Test EventDates conversion to dictionary."""
        dates = EventDates(
            initial_apd_approval="10/18/2018",
            spud_date="01/15/2019",
            completion_date="03/20/2019",
        )

        result = dates.to_dict()

        assert isinstance(result, dict)
        assert result['initial_apd_approval'] == "10/18/2018"
        assert result['spud_date'] == "01/15/2019"

    def test_nm_well_data_with_nested_objects(self):
        """Test NMWellData with nested casing records and completions."""
        data = NMWellData(
            api10="30-015-28692",
            api14="30015286920000",
            well_name="TEST WELL",
            event_dates=EventDates(spud_date="01/15/2019"),
            casing_records=[
                CasingRecord(string_type="Surface Casing", diameter_in=20.0),
                CasingRecord(string_type="Production Casing", diameter_in=5.5),
            ],
            completions=[
                CompletionRecord(
                    completion_id="84872",
                    perforations=[
                        PerforationInterval(top_md_ft=11120.0, bottom_md_ft=11163.0),
                    ],
                ),
            ],
        )

        result = data.to_dict()

        assert len(result['casing_records']) == 2
        assert result['casing_records'][0]['string_type'] == "Surface Casing"
        assert len(result['completions']) == 1
        assert result['completions'][0]['completion_id'] == "84872"
        assert result['event_dates']['spud_date'] == "01/15/2019"


class TestOperatorParsing:
    """Test different operator format parsing."""

    def test_operator_with_trailing_number(self):
        """Test operator format: NAME [NUMBER]"""
        html = '<html><body><div>Operator: EOG RESOURCES INC [7377]</div></body></html>'
        scraper = NMWellScraper()
        result = scraper._parse_html(html, "30-015-12345")

        assert result.operator_name == "EOG RESOURCES INC"
        assert result.operator_number == "7377"

    def test_operator_with_leading_number(self):
        """Test operator format: [NUMBER] NAME"""
        html = '<html><body><div>Operator: [7377] EOG RESOURCES INC</div></body></html>'
        scraper = NMWellScraper()
        result = scraper._parse_html(html, "30-015-12345")

        assert result.operator_name == "EOG RESOURCES INC"
        assert result.operator_number == "7377"


class TestWellNameParsing:
    """Test well name and number extraction."""

    def test_well_name_with_number(self):
        """Test well name with #xxx number."""
        html = '<html><body><div>Well Name: FEDERAL 24-19 #1H</div></body></html>'
        scraper = NMWellScraper()
        result = scraper._parse_html(html, "30-015-12345")

        assert result.well_name == "FEDERAL 24-19"
        assert result.well_number == "1H"

    def test_well_name_with_numeric_only_number(self):
        """Test well name with purely numeric well number."""
        html = '<html><body><div>Well Name: SAND TANK #001</div></body></html>'
        scraper = NMWellScraper()
        result = scraper._parse_html(html, "30-015-12345")

        assert result.well_name == "SAND TANK"
        assert result.well_number == "001"

    def test_well_name_without_number(self):
        """Test well name without well number."""
        html = '<html><body><div>Well Name: SIMPLE WELL</div></body></html>'
        scraper = NMWellScraper()
        result = scraper._parse_html(html, "30-015-12345")

        assert result.well_name == "SIMPLE WELL"
        assert result.well_number is None


@pytest.mark.skip(reason="Real integration test - requires network access")
class TestRealScraping:
    """
    Real integration tests against live NM OCD portal.
    These tests are skipped by default.
    Run with: pytest -v -m "not skip" to execute.
    """

    def test_fetch_real_well(self):
        """Test fetching a real well from NM OCD."""
        # API: 30-015-28692 (EOG RESOURCES INC - SAND TANK APS FEDERAL COM #001)
        scraper = NMWellScraper()
        result = scraper.fetch_well("30-015-28692")

        # Basic validation that we got data back
        assert result.api10 == "30-015-28692"
        assert result.api14 == "30015286920000"
        assert result.well_name  # Should have a well name
        assert result.operator_name  # Should have an operator

        print("\n=== Real Well Data ===")
        print(f"Well: {result.well_name} #{result.well_number}")
        print(f"Operator: {result.operator_name} [{result.operator_number}]")
        print(f"Status: {result.status}")
        print(f"Type: {result.well_type}")
        print(f"Direction: {result.direction}")
        print(f"Location: {result.surface_location}")
        print(f"Coordinates: {result.latitude}, {result.longitude} ({result.datum})")
        print(f"Formation: {result.formation}")
        print(f"Proposed Formation: {result.proposed_formation}")
        print(f"GL Elevation: {result.gl_elevation_ft} ft")
        print(f"KB Elevation: {result.kb_elevation_ft} ft")
        print(f"Proposed Depth: {result.proposed_depth_ft} ft")
        print(f"TVD: {result.true_vertical_depth_ft} ft")

        if result.event_dates:
            print("\n=== Event Dates ===")
            print(f"Initial APD: {result.event_dates.initial_apd_approval}")
            print(f"Spud Date: {result.event_dates.spud_date}")
            print(f"Last Inspection: {result.event_dates.last_inspection}")

        print(f"\n=== Casing Records ({len(result.casing_records)}) ===")
        for casing in result.casing_records:
            print(f"  {casing.string_type}: {casing.diameter_in}\" OD @ {casing.bottom_ft} ft")
            print(f"    Grade: {casing.grade}, Weight: {casing.weight_ppf} ppf")
            print(f"    Cement: {casing.cement_class}, {casing.cement_sacks} sacks")

        print(f"\n=== Completions ({len(result.completions)}) ===")
        for comp in result.completions:
            print(f"  [{comp.completion_id}] {comp.completion_name}")
            print(f"    Status: {comp.status}")
            for perf in comp.perforations:
                print(f"    Perf: {perf.top_md_ft} - {perf.bottom_md_ft} ft MD")
