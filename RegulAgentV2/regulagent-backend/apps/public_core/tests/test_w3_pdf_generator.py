"""
Tests for W-3 PDF Generator Service.

Tests cover:
- _safe_str() helper for None-safe formatting
- generate_w3_pdf() main function (happy path, edge cases, error conditions)
- draw_coordinate_grid() dev helper
"""

from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_sample_form_data(num_plugs=3):
    """Create sample W-3 form data for testing."""
    plugs = []
    for i in range(num_plugs):
        plugs.append({
            "plug_number": i + 1,
            "depth_top_ft": 1000 * (i + 1),
            "depth_bottom_ft": 1000 * (i + 2),
            "type": "cement_plug",
            "cement_class": "H",
            "sacks": 100,
            "slurry_weight_ppg": 14.8,
            "hole_size_in": 8.75,
            "calculated_top_of_plug_ft": 900 * (i + 1),
            "measured_top_of_plug_ft": None,
            "remarks": f"Plug {i + 1}",
            "cementing_date": "01/20/2025",
        })

    return {
        "header": {
            "api_number": "42-501-70575",
            "rrc_district": "03",
            "field_name": "TEST FIELD",
            "lease_name": "TEST LEASE",
            "well_number": "1",
            "operator": "Test Operator LLC",
            "county": "Test County",
            "total_depth": "10000",
            "date_well_plugged": "01/20/2025",
        },
        "plugs": plugs,
        "casing_record": [
            {
                "od_in": 9.625,
                "weight_ppf": 40,
                "top_ft": 0,
                "bottom_ft": 8500,
                "hole_size_in": 12.25,
            },
        ],
        "perforations": [
            {"from_ft": 7000, "to_ft": 7050},
        ],
        "duqw": {"depth_ft": 500},
        "remarks": "Test remarks",
    }


# ---------------------------------------------------------------------------
# _safe_str tests
# ---------------------------------------------------------------------------

class TestSafeStr:
    """Test _safe_str() helper for None-safe value formatting."""

    def test_none_returns_empty(self):
        """None input should return an empty string."""
        from apps.public_core.services.w3_pdf_generator import _safe_str
        assert _safe_str(None) == ""

    def test_string_passthrough(self):
        """Plain string should pass through unchanged."""
        from apps.public_core.services.w3_pdf_generator import _safe_str
        assert _safe_str("hello") == "hello"

    def test_int_to_string(self):
        """Integer should be converted to its string representation."""
        from apps.public_core.services.w3_pdf_generator import _safe_str
        assert _safe_str(42) == "42"

    def test_float_drops_zero_decimal(self):
        """Float with zero fractional part should omit the decimal."""
        from apps.public_core.services.w3_pdf_generator import _safe_str
        assert _safe_str(14.0) == "14"

    def test_float_keeps_decimal(self):
        """Float with non-zero fractional part should preserve the decimal."""
        from apps.public_core.services.w3_pdf_generator import _safe_str
        assert _safe_str(14.8) == "14.8"

    def test_format_string(self):
        """A fmt string should be applied to the value."""
        from apps.public_core.services.w3_pdf_generator import _safe_str
        assert _safe_str(123.456, "{:.0f}") == "123"

    def test_format_with_none(self):
        """fmt string with None value should still return empty string."""
        from apps.public_core.services.w3_pdf_generator import _safe_str
        assert _safe_str(None, "{:.0f}") == ""

    def test_empty_string(self):
        """Empty string input should return empty string."""
        from apps.public_core.services.w3_pdf_generator import _safe_str
        assert _safe_str("") == ""

    def test_zero(self):
        """Numeric zero should return '0', not empty string."""
        from apps.public_core.services.w3_pdf_generator import _safe_str
        assert _safe_str(0) == "0"


# ---------------------------------------------------------------------------
# generate_w3_pdf tests
# ---------------------------------------------------------------------------

class TestGenerateW3PDF:
    """Test generate_w3_pdf() main function."""

    def test_happy_path_generates_pdf(self, tmp_path):
        """Happy path: produces a valid 2-page PDF file."""
        from apps.public_core.services.w3_pdf_generator import generate_w3_pdf

        form_data = _make_sample_form_data()

        with patch("django.conf.settings.MEDIA_ROOT", str(tmp_path)):
            result = generate_w3_pdf(form_data)

        assert os.path.exists(result["temp_path"])
        assert result["file_size"] > 0
        assert result["page_count"] == 2
        assert result["api_number"] == "42-501-70575"
        assert "ttl_expires_at" in result
        assert result["temp_path"].endswith(".pdf")

    def test_empty_plugs_still_generates(self, tmp_path):
        """Empty plugs list should still produce a valid 2-page PDF."""
        from apps.public_core.services.w3_pdf_generator import generate_w3_pdf

        form_data = _make_sample_form_data(num_plugs=0)

        with patch("django.conf.settings.MEDIA_ROOT", str(tmp_path)):
            result = generate_w3_pdf(form_data)

        assert os.path.exists(result["temp_path"])
        assert result["page_count"] == 2

    def test_overflow_plugs_no_error(self, tmp_path):
        """More than 8 plugs should not raise; overflow is handled gracefully."""
        from apps.public_core.services.w3_pdf_generator import generate_w3_pdf

        form_data = _make_sample_form_data(num_plugs=12)

        with patch("django.conf.settings.MEDIA_ROOT", str(tmp_path)):
            result = generate_w3_pdf(form_data)

        assert os.path.exists(result["temp_path"])
        assert result["page_count"] == 2

    def test_missing_template_raises_error(self, tmp_path):
        """Missing template PDF should raise W3PDFGeneratorError."""
        from apps.public_core.services.w3_pdf_generator import (
            generate_w3_pdf,
            W3PDFGeneratorError,
        )

        form_data = _make_sample_form_data()

        with patch("django.conf.settings.MEDIA_ROOT", str(tmp_path)):
            with patch(
                "apps.public_core.services.w3_template_builder.ANNOTATED_TEMPLATE_PATH",
                Path("/nonexistent/path/template.pdf"),
            ):
                with pytest.raises(W3PDFGeneratorError, match="template not found"):
                    generate_w3_pdf(form_data)

    def test_no_fitz_raises_error(self, tmp_path):
        """Missing PyMuPDF (fitz) should raise W3PDFGeneratorError."""
        from apps.public_core.services.w3_pdf_generator import (
            generate_w3_pdf,
            W3PDFGeneratorError,
        )

        form_data = _make_sample_form_data()

        with patch("django.conf.settings.MEDIA_ROOT", str(tmp_path)):
            with patch("apps.public_core.services.w3_pdf_generator.HAS_FITZ", False):
                with pytest.raises(W3PDFGeneratorError, match="PyMuPDF"):
                    generate_w3_pdf(form_data)

    def test_minimal_header_data(self, tmp_path):
        """Minimal/sparse header data should not cause an unhandled exception."""
        from apps.public_core.services.w3_pdf_generator import generate_w3_pdf

        form_data = {
            "header": {"api_number": "42-123-45678"},
            "plugs": [],
            "casing_record": [],
            "perforations": [],
            "duqw": {},
            "remarks": "",
        }

        with patch("django.conf.settings.MEDIA_ROOT", str(tmp_path)):
            result = generate_w3_pdf(form_data)

        assert os.path.exists(result["temp_path"])

    def test_output_file_in_temp_pdfs_dir(self, tmp_path):
        """Output PDF should be written inside MEDIA_ROOT/temp_pdfs/."""
        from apps.public_core.services.w3_pdf_generator import generate_w3_pdf

        form_data = _make_sample_form_data()

        with patch("django.conf.settings.MEDIA_ROOT", str(tmp_path)):
            result = generate_w3_pdf(form_data)

        assert "temp_pdfs" in result["temp_path"]

    def test_baked_text_is_extractable(self, tmp_path):
        """After generate_w3_pdf(), text should be extractable from the PDF."""
        import fitz
        from apps.public_core.services.w3_pdf_generator import generate_w3_pdf

        form_data = _make_sample_form_data()

        with patch("django.conf.settings.MEDIA_ROOT", str(tmp_path)):
            result = generate_w3_pdf(form_data)

        doc = fitz.open(result["temp_path"])
        page1_text = doc[0].get_text()
        doc.close()

        # Verify key values are present in extractable text
        assert "501-70575" in page1_text  # API number (42- stripped)
        assert "TEST FIELD" in page1_text
        assert "TEST LEASE" in page1_text
        assert "Test Operator LLC" in page1_text


# ---------------------------------------------------------------------------
# draw_coordinate_grid tests
# ---------------------------------------------------------------------------

class TestDrawCoordinateGrid:
    """Test draw_coordinate_grid() dev helper."""

    def test_generates_grid_pdf(self, tmp_path):
        """Should produce a coordinate-grid overlay PDF at the specified path."""
        from apps.public_core.services.w3_pdf_generator import draw_coordinate_grid

        output = os.path.join(str(tmp_path), "test_grid.pdf")

        with patch("django.conf.settings.MEDIA_ROOT", str(tmp_path)):
            result = draw_coordinate_grid(output_path=output)

        assert os.path.exists(result)
        assert result == output
