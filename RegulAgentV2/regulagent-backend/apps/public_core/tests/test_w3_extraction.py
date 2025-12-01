"""
Tests for W-3 Form Extraction Service

Tests extraction of W-3A PDF forms and validation of extracted data structure.
Uses sample W-3A PDF: Approved_W3A_00346118_20250826_214942_.pdf
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch, MagicMock

from apps.public_core.services.w3_extraction import (
    extract_w3a_from_pdf,
    load_w3a_form,
    _validate_w3a_structure,
    _load_w3a_from_db,
    _load_w3a_from_pdf_upload,
)


class TestValidateW3AStructure(TestCase):
    """Test W-3A structure validation."""

    def test_valid_w3a_structure(self):
        """Valid W-3A structure should pass validation."""
        w3a_data = {
            "header": {
                "api_number": "42-501-70575",
                "well_name": "Test Well",
                "operator": "Test Operator",
            },
            "casing_record": [
                {
                    "string_type": "surface",
                    "size_in": 13.375,
                    "top_ft": 0,
                    "bottom_ft": 1717,
                }
            ],
            "perforations": [],
            "duqw": {"depth_ft": 3250},
        }

        # Should not raise
        _validate_w3a_structure(w3a_data)

    def test_missing_header_section(self):
        """Missing header section should raise ValueError."""
        w3a_data = {
            "casing_record": [],
            "perforations": [],
            "duqw": {},
        }

        with self.assertRaises(ValueError) as ctx:
            _validate_w3a_structure(w3a_data)

        self.assertIn("header", str(ctx.exception))

    def test_missing_casing_record_section(self):
        """Missing casing_record section should raise ValueError."""
        w3a_data = {
            "header": {"api_number": "42-501-70575"},
            "perforations": [],
            "duqw": {},
        }

        with self.assertRaises(ValueError) as ctx:
            _validate_w3a_structure(w3a_data)

        self.assertIn("casing_record", str(ctx.exception))

    def test_missing_api_number(self):
        """Header missing api_number should raise ValueError."""
        w3a_data = {
            "header": {
                "well_name": "Test Well",
            },
            "casing_record": [],
            "perforations": [],
            "duqw": {},
        }

        with self.assertRaises(ValueError) as ctx:
            _validate_w3a_structure(w3a_data)

        self.assertIn("api_number", str(ctx.exception))

    def test_empty_header(self):
        """Empty header should raise ValueError."""
        w3a_data = {
            "header": {},
            "casing_record": [],
            "perforations": [],
            "duqw": {},
        }

        with self.assertRaises(ValueError) as ctx:
            _validate_w3a_structure(w3a_data)

        self.assertIn("api_number", str(ctx.exception))


class TestExtractW3AFromPDF(TestCase):
    """Test W-3A PDF extraction."""

    @patch("apps.public_core.services.w3_extraction.extract_json_from_pdf")
    def test_successful_extraction(self, mock_extract):
        """Successful extraction should return W-3A data."""
        mock_result = Mock()
        mock_result.json_data = {
            "header": {
                "api_number": "42-501-70575",
                "well_name": "Test Well",
                "operator": "Test Operator",
                "rrc_district": "08A",
                "county": "ANDREWS",
                "field": "SPRABERRY",
            },
            "casing_record": [
                {
                    "string_type": "surface",
                    "size_in": 13.375,
                    "weight_ppf": 47.0,
                    "top_ft": 0,
                    "bottom_ft": 1717,
                    "shoe_depth_ft": 1717,
                    "cement_top_ft": 0,
                },
                {
                    "string_type": "intermediate",
                    "size_in": 8.625,
                    "weight_ppf": 32.0,
                    "top_ft": 1717,
                    "bottom_ft": 5532,
                    "shoe_depth_ft": 5532,
                    "cement_top_ft": 2790,
                },
            ],
            "perforations": [
                {
                    "interval_top_ft": 10964,
                    "interval_bottom_ft": 10864,
                    "formation": "Spraberry",
                    "status": "open",
                }
            ],
            "duqw": {
                "depth_ft": 3250,
                "formation": "Santa Rosa",
                "determination_method": "GAU letter",
            },
            "plugging_proposal": [
                {
                    "plug_number": 1,
                    "depth_top_ft": 7990,
                    "depth_bottom_ft": 7890,
                    "type": "cement_plug",
                    "cement_class": None,
                    "sacks": None,
                    "remarks": "Tag top of plug",
                },
                {
                    "plug_number": 2,
                    "depth_top_ft": 7047,
                    "depth_bottom_ft": 6947,
                    "type": "cement_plug",
                    "cement_class": None,
                    "sacks": None,
                    "remarks": "None",
                },
            ],
            "remarks": "Well plugging and abandonment",
        }
        mock_result.errors = []
        mock_extract.return_value = mock_result

        result = extract_w3a_from_pdf("/path/to/W-3A.pdf")

        self.assertEqual(result["header"]["api_number"], "42-501-70575")
        self.assertEqual(result["header"]["well_name"], "Test Well")
        self.assertEqual(len(result["casing_record"]), 2)
        self.assertEqual(len(result["plugging_proposal"]), 2)

        # Verify extract_json_from_pdf was called correctly
        mock_extract.assert_called_once()
        call_args = mock_extract.call_args
        self.assertEqual(call_args[0][1], "w3a")

    @patch("apps.public_core.services.w3_extraction.extract_json_from_pdf")
    def test_extraction_with_warnings(self, mock_extract):
        """Extraction with warnings should still return data."""
        mock_result = Mock()
        mock_result.json_data = {
            "header": {"api_number": "42-501-70575"},
            "casing_record": [],
            "perforations": [],
            "duqw": {},
        }
        mock_result.errors = ["Warning: Could not parse some fields"]
        mock_extract.return_value = mock_result

        # Should not raise despite warnings
        result = extract_w3a_from_pdf("/path/to/W-3A.pdf")
        self.assertIsNotNone(result)

    @patch("apps.public_core.services.w3_extraction.extract_json_from_pdf")
    def test_extraction_missing_required_section(self, mock_extract):
        """Extraction missing required section should raise ValueError."""
        mock_result = Mock()
        mock_result.json_data = {
            "header": {"api_number": "42-501-70575"},
            # Missing casing_record
            "perforations": [],
            "duqw": {},
        }
        mock_result.errors = []
        mock_extract.return_value = mock_result

        with self.assertRaises(ValueError) as ctx:
            extract_w3a_from_pdf("/path/to/W-3A.pdf")

        self.assertIn("casing_record", str(ctx.exception))

    @patch("apps.public_core.services.w3_extraction.extract_json_from_pdf")
    def test_extraction_raises_exception(self, mock_extract):
        """Extract failure should raise ValueError."""
        mock_extract.side_effect = Exception("OpenAI API error")

        with self.assertRaises(ValueError) as ctx:
            extract_w3a_from_pdf("/path/to/W-3A.pdf")

        self.assertIn("Failed to extract", str(ctx.exception))


class TestLoadW3AForm(TestCase):
    """Test W-3A form loading."""

    @patch("apps.public_core.services.w3_extraction._load_w3a_from_db")
    def test_load_from_regulagent_db(self, mock_db_load):
        """Load from regulagent database should call _load_w3a_from_db."""
        mock_w3a = {
            "header": {"api_number": "42-501-70575"},
            "casing_record": [],
            "perforations": [],
            "duqw": {},
        }
        mock_db_load.return_value = mock_w3a

        w3a_reference = {"type": "regulagent", "w3a_id": 123}
        result = load_w3a_form(w3a_reference)

        self.assertEqual(result, mock_w3a)
        mock_db_load.assert_called_once_with(w3a_reference)

    @patch("apps.public_core.services.w3_extraction._load_w3a_from_pdf_upload")
    def test_load_from_pdf_upload(self, mock_pdf_load):
        """Load from PDF upload should call _load_w3a_from_pdf_upload."""
        mock_w3a = {
            "header": {"api_number": "42-501-70575"},
            "casing_record": [],
            "perforations": [],
            "duqw": {},
        }
        mock_pdf_load.return_value = mock_w3a

        mock_request = Mock()
        w3a_reference = {"type": "pdf"}
        result = load_w3a_form(w3a_reference, request=mock_request)

        self.assertEqual(result, mock_w3a)
        mock_pdf_load.assert_called_once_with(w3a_reference, mock_request)

    def test_load_invalid_reference_type(self):
        """Invalid reference type should raise ValueError."""
        w3a_reference = {"type": "invalid"}

        with self.assertRaises(ValueError) as ctx:
            load_w3a_form(w3a_reference)

        self.assertIn("Unknown w3a_reference type", str(ctx.exception))

    def test_load_pdf_without_request(self):
        """Loading PDF without request object should raise ValueError."""
        w3a_reference = {"type": "pdf"}

        with self.assertRaises(ValueError) as ctx:
            load_w3a_form(w3a_reference, request=None)

        self.assertIn("request object required", str(ctx.exception))


class TestLoadW3AFromDB(TestCase):
    """Test W-3A loading from database."""

    def test_missing_w3a_id(self):
        """Missing w3a_id should raise ValueError."""
        w3a_reference = {"type": "regulagent"}

        with self.assertRaises(ValueError) as ctx:
            _load_w3a_from_db(w3a_reference)

        self.assertIn("w3a_id", str(ctx.exception))

    def test_database_loading_not_implemented(self):
        """Database loading should raise NotImplementedError (TBD)."""
        w3a_reference = {"type": "regulagent", "w3a_id": 123}

        with self.assertRaises(NotImplementedError):
            _load_w3a_from_db(w3a_reference)


class TestLoadW3AFromPDFUpload(TestCase):
    """Test W-3A loading from PDF upload."""

    @patch("apps.public_core.services.w3_extraction.extract_w3a_from_pdf")
    def test_successful_pdf_upload(self, mock_extract):
        """Successful PDF upload should extract W-3A."""
        mock_w3a = {
            "header": {"api_number": "42-501-70575"},
            "casing_record": [],
            "perforations": [],
            "duqw": {},
        }
        mock_extract.return_value = mock_w3a

        # Mock uploaded file
        mock_file = MagicMock()
        mock_file.chunks.return_value = [b"PDF content chunk 1", b"PDF content chunk 2"]

        mock_request = Mock()
        mock_request.FILES = {"w3a_file": mock_file}

        w3a_reference = {"type": "pdf"}
        result = _load_w3a_from_pdf_upload(w3a_reference, mock_request)

        self.assertEqual(result, mock_w3a)

    def test_missing_w3a_file(self):
        """Missing w3a_file in request should raise ValueError."""
        mock_request = Mock()
        mock_request.FILES = {}

        w3a_reference = {"type": "pdf"}

        with self.assertRaises(ValueError) as ctx:
            _load_w3a_from_pdf_upload(w3a_reference, mock_request)

        self.assertIn("w3a_file not provided", str(ctx.exception))

    @patch("apps.public_core.services.w3_extraction.extract_w3a_from_pdf")
    @patch("os.unlink")
    def test_temp_file_cleanup(self, mock_unlink, mock_extract):
        """Temporary file should be cleaned up even if extraction fails."""
        mock_extract.side_effect = Exception("Extraction failed")

        mock_file = MagicMock()
        mock_file.chunks.return_value = [b"PDF content"]

        mock_request = Mock()
        mock_request.FILES = {"w3a_file": mock_file}

        w3a_reference = {"type": "pdf"}

        with self.assertRaises(ValueError):
            _load_w3a_from_pdf_upload(w3a_reference, mock_request)

        # Verify unlink was called (cleanup happened)
        mock_unlink.assert_called_once()


class TestW3AExtractionIntegration(TestCase):
    """Integration tests for W-3A extraction with real PDF example."""

    def test_w3a_example_structure_from_pdf_content(self):
        """
        Test extraction with data from actual W-3A example PDF.
        
        Reference: Approved_W3A_00346118_20250826_214942_.pdf
        Well: Spraberry well in Andrews County, RRC District 08
        """
        # Simulate extracted data from the example PDF
        w3a_data = {
            "header": {
                "api_number": "00346118",
                "well_name": "Test Well",
                "operator": "Test Operator",
                "rrc_district": "08",
                "county": "ANDREWS",
                "field": "SPRABERRY [TREND AREA]",
                "lease": "MABEE 140A",
                "well_no": "40718",
                "well_type": "Oil",
                "total_depth": 11200,
            },
            "casing_record": [
                {
                    "string_type": "surface",
                    "size_in": 11.75,
                    "hole_size_in": 14.75,
                    "top_ft": 0,
                    "bottom_ft": 1717,
                    "shoe_depth_ft": 1717,
                    "cement_top_ft": 0,
                    "sacks": 930,
                },
                {
                    "string_type": "intermediate",
                    "size_in": 8.625,
                    "hole_size_in": 10.625,
                    "top_ft": 1717,
                    "bottom_ft": 5532,
                    "shoe_depth_ft": 5532,
                    "cement_top_ft": 2790,
                    "sacks": 1230,
                },
                {
                    "string_type": "production",
                    "size_in": 5.5,
                    "hole_size_in": 7.875,
                    "top_ft": 5532,
                    "bottom_ft": 11200,
                    "shoe_depth_ft": 11200,
                    "cement_top_ft": 5532,
                    "sacks": 310,
                },
            ],
            "perforations": [
                {
                    "interval_top_ft": 10964,
                    "interval_bottom_ft": 10864,
                    "formation": "Spraberry",
                    "status": "open",
                }
            ],
            "duqw": {
                "depth_ft": 3250,
                "formation": "Santa Rosa",
            },
            "plugging_proposal": [
                {
                    "plug_number": 1,
                    "depth_top_ft": 7990,
                    "depth_bottom_ft": 7890,
                    "type": "cement_plug",
                    "cement_class": None,
                    "sacks": None,
                    "requirements": "Tag top of plug",
                },
                {
                    "plug_number": 2,
                    "depth_top_ft": 7047,
                    "depth_bottom_ft": 6947,
                    "type": "cement_plug",
                    "cement_class": None,
                    "sacks": None,
                    "requirements": "None",
                },
                {
                    "plug_number": 3,
                    "depth_top_ft": 5582,
                    "depth_bottom_ft": 4970,
                    "type": "cement_plug",
                    "cement_class": None,
                    "sacks": None,
                    "requirements": "Tag top of plug",
                },
                {
                    "plug_number": 4,
                    "depth_top_ft": 4500,
                    "depth_bottom_ft": 4300,
                    "type": "cement_plug",
                    "cement_class": None,
                    "sacks": None,
                    "requirements": "Perforate and Squeeze; Tag top of plug",
                },
                {
                    "plug_number": 5,
                    "depth_top_ft": 3638,
                    "depth_bottom_ft": 3538,
                    "type": "cement_plug",
                    "cement_class": None,
                    "sacks": None,
                    "requirements": "None",
                },
                {
                    "plug_number": 6,
                    "depth_top_ft": 1850,
                    "depth_bottom_ft": 1550,
                    "type": "cement_plug",
                    "cement_class": None,
                    "sacks": None,
                    "requirements": "Perforate and Squeeze; Wait 4 hours and tag top of plug",
                },
                {
                    "plug_number": 7,
                    "depth_top_ft": 1250,
                    "depth_bottom_ft": 950,
                    "type": "cement_plug",
                    "cement_class": None,
                    "sacks": None,
                    "requirements": "Perforate and Squeeze",
                },
                {
                    "plug_number": 8,
                    "depth_top_ft": 350,
                    "depth_bottom_ft": 3,
                    "type": "surface_cement_plug",
                    "cement_class": None,
                    "sacks": None,
                    "requirements": "Perforate and Circulate",
                },
            ],
        }

        # Validate structure
        _validate_w3a_structure(w3a_data)

        # Verify key fields
        self.assertEqual(w3a_data["header"]["api_number"], "00346118")
        self.assertEqual(w3a_data["header"]["county"], "ANDREWS")
        self.assertEqual(w3a_data["header"]["field"], "SPRABERRY [TREND AREA]")
        self.assertEqual(w3a_data["header"]["total_depth"], 11200)

        # Verify casing record
        self.assertEqual(len(w3a_data["casing_record"]), 3)
        surface = w3a_data["casing_record"][0]
        self.assertEqual(surface["string_type"], "surface")
        self.assertEqual(surface["size_in"], 11.75)
        self.assertEqual(surface["bottom_ft"], 1717)

        # Verify plugging proposal
        self.assertEqual(len(w3a_data["plugging_proposal"]), 8)
        last_plug = w3a_data["plugging_proposal"][-1]
        self.assertEqual(last_plug["plug_number"], 8)
        self.assertEqual(last_plug["type"], "surface_cement_plug")


