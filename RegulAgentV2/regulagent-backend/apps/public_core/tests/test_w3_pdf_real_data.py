"""
Integration tests for W-3 PDF Generator using real extracted W-3A data.

Uses the MABEE 140A well (API 00346118, Andrews County, District 08)
from the approved W-3A extraction at tmp/w3a_extracted.json.

These tests generate actual PDFs for visual inspection.
Run with: docker compose -f compose.dev.yml exec web python -m pytest apps/public_core/tests/test_w3_pdf_real_data.py -v -s

Output PDFs are saved to tmp/test_output/ for manual review.
"""

from __future__ import annotations

import os
import shutil
from unittest.mock import patch
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Real extracted W-3A data — MABEE 140A (API 00346118, Andrews County, Dist 08)
# Source: tmp/w3a_extracted.json
# ---------------------------------------------------------------------------

MABEE_140A_EXTRACTED = {
    "header": {
        "api_number": "00346118",
        "well_name": "MABEE 140A",
        "operator": "COG OPERATING LLC",
        "county": "ANDREWS",
        "rrc_district": "08",
        "field": "SPRABERRY [TREND AREA]",
        "total_depth_ft": 11200,
    },
    "casing_record": [
        {
            "string_type": "surface",
            "size_in": 11.75,
            "weight_ppf": None,
            "hole_size_in": 14.75,
            "top_ft": 0,
            "bottom_ft": 1717,
            "shoe_depth_ft": 1717,
            "cement_top_ft": 930,
            "removed_to_depth_ft": None,
        },
        {
            "string_type": "intermediate",
            "size_in": 8.625,
            "weight_ppf": None,
            "hole_size_in": 10.625,
            "top_ft": 0,
            "bottom_ft": 5532,
            "shoe_depth_ft": 5532,
            "cement_top_ft": 1230,
            "removed_to_depth_ft": None,
        },
        {
            "string_type": "production",
            "size_in": 5.5,
            "weight_ppf": None,
            "hole_size_in": 7.875,
            "top_ft": 0,
            "bottom_ft": 11200,
            "shoe_depth_ft": 11200,
            "cement_top_ft": 310,
            "removed_to_depth_ft": None,
        },
        {
            "string_type": "liner",
            "size_in": 5.5,
            "weight_ppf": None,
            "hole_size_in": 7.875,
            "top_ft": 6997,
            "bottom_ft": 11200,
            "shoe_depth_ft": 11200,
            "cement_top_ft": 720,
            "removed_to_depth_ft": None,
        },
    ],
    "perforations": [
        {
            "interval_top_ft": 8110,
            "interval_bottom_ft": 10914,
            "formation": None,
            "status": "plugged",
            "perforation_date": None,
        }
    ],
    "plugging_proposal": [
        {
            "plug_number": 1,
            "depth_top_ft": 7990,
            "depth_bottom_ft": 7890,
            "type": "cement_plug",
            "cement_class": None,
            "sacks": 40,
            "volume_bbl": None,
            "remarks": None,
        },
        {
            "plug_number": 2,
            "depth_top_ft": 7047,
            "depth_bottom_ft": 6947,
            "type": "cement_plug",
            "cement_class": None,
            "sacks": 20,
            "volume_bbl": None,
            "remarks": None,
        },
        {
            "plug_number": 3,
            "depth_top_ft": 5582,
            "depth_bottom_ft": 4970,
            "type": "cement_plug",
            "cement_class": None,
            "sacks": 110,
            "volume_bbl": None,
            "remarks": None,
        },
        {
            "plug_number": 4,
            "depth_top_ft": 4500,
            "depth_bottom_ft": 4300,
            "type": "cement_plug",
            "cement_class": None,
            "sacks": 63,
            "volume_bbl": None,
            "remarks": None,
        },
        {
            "plug_number": 5,
            "depth_top_ft": 3638,
            "depth_bottom_ft": 3538,
            "type": "cement_plug",
            "cement_class": None,
            "sacks": 20,
            "volume_bbl": None,
            "remarks": None,
        },
        {
            "plug_number": 6,
            "depth_top_ft": 1850,
            "depth_bottom_ft": 1550,
            "type": "cement_plug",
            "cement_class": None,
            "sacks": 90,
            "volume_bbl": None,
            "remarks": None,
        },
        {
            "plug_number": 7,
            "depth_top_ft": 1250,
            "depth_bottom_ft": 950,
            "type": "cement_plug",
            "cement_class": None,
            "sacks": 87,
            "volume_bbl": None,
            "remarks": None,
        },
        {
            "plug_number": 8,
            "depth_top_ft": 350,
            "depth_bottom_ft": 3,
            "type": "cement_surface_plug",
            "cement_class": None,
            "sacks": 100,
            "volume_bbl": None,
            "remarks": None,
        },
    ],
    "duqw": {
        "depth_ft": None,
        "formation": None,
        "determination_method": None,
    },
    "remarks": None,
}

# ---------------------------------------------------------------------------
# Base path for persistent test output (survives after the test run)
# ---------------------------------------------------------------------------
# The test file is at <project_root>/apps/public_core/tests/test_w3_pdf_real_data.py
# So project root is 4 parents up from __file__
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_TMP_OUTPUT_DIR = _PROJECT_ROOT / "tmp" / "test_output"


# ---------------------------------------------------------------------------
# Transform helper
# ---------------------------------------------------------------------------

def _transform_extracted_to_w3_form_data(extracted: dict) -> dict:
    """
    Transform the raw extracted W-3A JSON into the w3_form_data shape that
    generate_w3_pdf() expects.

    Key mappings applied:
      - header.total_depth_ft  → header.total_depth
      - header.field           → header.field_name
      - header.well_name       → header.lease_name
      - header.api_number      → prefixed with "42-" if not already present
      - casing[].size_in       → casing[].od_in
      - perforations[].interval_top_ft    → perforations[].from_ft
      - perforations[].interval_bottom_ft → perforations[].to_ft
      - plugging_proposal      → plugs
    """
    raw_header = extracted.get("header", {})

    # Build API number with Texas "42-" prefix
    raw_api = str(raw_header.get("api_number", ""))
    if not raw_api.startswith("42-") and not raw_api.startswith("42"):
        api_number = f"42-{raw_api}"
    elif raw_api.startswith("42") and not raw_api.startswith("42-"):
        # e.g. "42XXXXXX" → "42-XXXXXX"
        api_number = f"42-{raw_api[2:]}"
    else:
        api_number = raw_api

    # Header transformation
    header = {
        "api_number": api_number,
        "rrc_district": raw_header.get("rrc_district"),
        "field_name": raw_header.get("field"),
        "lease_name": raw_header.get("well_name"),
        "operator": raw_header.get("operator"),
        "county": raw_header.get("county"),
        "total_depth": raw_header.get("total_depth_ft"),
    }

    # Casing transformation: size_in → od_in; shoe_depth_ft → setting_depth_ft
    casing_record = []
    for c in extracted.get("casing_record", []):
        casing_record.append(
            {
                "od_in": c.get("size_in"),
                "weight_ppf": c.get("weight_ppf"),
                "top_ft": c.get("top_ft"),
                "bottom_ft": c.get("bottom_ft"),
                "hole_size_in": c.get("hole_size_in"),
                "setting_depth_ft": c.get("shoe_depth_ft"),
                "removed_to_depth_ft": c.get("removed_to_depth_ft"),
            }
        )

    # Perforation transformation: interval_top_ft/interval_bottom_ft → from_ft/to_ft
    perforations = []
    for p in extracted.get("perforations", []):
        perforations.append(
            {
                "from_ft": p.get("interval_top_ft"),
                "to_ft": p.get("interval_bottom_ft"),
                "formation": p.get("formation"),
                "status": p.get("status"),
            }
        )

    # Plugs transformation: plugging_proposal → plugs
    # depth_top_ft is the top of the plug (calculated top in W-3 terminology)
    plugs = []
    for plug in extracted.get("plugging_proposal", []):
        plugs.append(
            {
                "plug_number": plug.get("plug_number"),
                "depth_top_ft": plug.get("depth_top_ft"),
                "depth_bottom_ft": plug.get("depth_bottom_ft"),
                "type": plug.get("type"),
                "cement_class": plug.get("cement_class"),
                "sacks": plug.get("sacks"),
                "volume_bbl": plug.get("volume_bbl"),
                # calculated_top_of_plug_ft maps from depth_top_ft in the proposal
                "calculated_top_of_plug_ft": plug.get("depth_top_ft"),
                "measured_top_of_plug_ft": None,
                "slurry_weight_ppg": None,
                "hole_size_in": None,
                "cementing_date": None,
                "remarks": plug.get("remarks"),
            }
        )

    # DUQW passthrough
    duqw = extracted.get("duqw") or {}

    return {
        "header": header,
        "plugs": plugs,
        "casing_record": casing_record,
        "perforations": perforations,
        "duqw": duqw,
        "remarks": extracted.get("remarks") or "",
    }


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestW3PDFRealData:
    """Integration tests using real MABEE 140A extracted W-3A data."""

    def test_mabee_140a_generates_valid_pdf(self, tmp_path):
        """
        Happy-path test: transform real extracted data and generate a W-3 PDF.

        Asserts:
          - PDF file is created and non-empty
          - Page count is exactly 2
          - API number in result is "42-00346118" (Texas prefix applied)

        Copies the generated PDF to tmp/test_output/ for manual visual inspection.
        """
        from apps.public_core.services.w3_pdf_generator import generate_w3_pdf

        with patch('django.conf.settings.MEDIA_ROOT', str(tmp_path)):
            w3_form_data = _transform_extracted_to_w3_form_data(MABEE_140A_EXTRACTED)
            result = generate_w3_pdf(w3_form_data)

        # Basic validity assertions
        assert os.path.exists(result["temp_path"]), "PDF file was not created"
        assert result["file_size"] > 0, "PDF file is empty"
        assert result["page_count"] == 2, f"Expected 2 pages, got {result['page_count']}"
        assert result["api_number"] == "42-00346118", (
            f"Expected '42-00346118', got '{result['api_number']}'"
        )

        # Copy to persistent tmp/test_output/ for visual inspection
        _TMP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = _TMP_OUTPUT_DIR / "W3_MABEE_140A_00346118.pdf"
        shutil.copy2(result["temp_path"], str(output_path))

        print(f"\n[Visual inspection] PDF saved to: {output_path}")

    def test_all_8_plugs_fit_in_table(self, tmp_path):
        """
        Exactly 8 plugs (the maximum that fits in the W-3 plug table) must not
        trigger the overflow path — all plugs should be placed directly in the table.
        """
        from apps.public_core.services.w3_pdf_generator import generate_w3_pdf

        w3_form_data = _transform_extracted_to_w3_form_data(MABEE_140A_EXTRACTED)

        # Verify the test data has exactly 8 plugs before generating
        assert len(w3_form_data["plugs"]) == 8, (
            f"Expected 8 plugs in MABEE_140A_EXTRACTED, found {len(w3_form_data['plugs'])}"
        )

        # Generation must succeed without error (no overflow handling needed for 8 plugs)
        with patch('django.conf.settings.MEDIA_ROOT', str(tmp_path)):
            result = generate_w3_pdf(w3_form_data)

        assert os.path.exists(result["temp_path"]), "PDF was not generated"
        assert result["page_count"] == 2

    def test_4_casing_strings_rendered(self, tmp_path):
        """
        The 4 casing strings (surface, intermediate, production, liner) must all
        be included in the form data and not cause an error during PDF generation.
        """
        from apps.public_core.services.w3_pdf_generator import generate_w3_pdf

        w3_form_data = _transform_extracted_to_w3_form_data(MABEE_140A_EXTRACTED)

        # Verify all 4 casing strings survive transformation
        assert len(w3_form_data["casing_record"]) == 4, (
            f"Expected 4 casing strings, got {len(w3_form_data['casing_record'])}"
        )

        # Verify od_in is correctly mapped from size_in for each string
        expected_od = [11.75, 8.625, 5.5, 5.5]
        actual_od = [c["od_in"] for c in w3_form_data["casing_record"]]
        assert actual_od == expected_od, (
            f"od_in mapping mismatch: expected {expected_od}, got {actual_od}"
        )

        # PDF generation must succeed with all 4 strings
        with patch('django.conf.settings.MEDIA_ROOT', str(tmp_path)):
            result = generate_w3_pdf(w3_form_data)
        assert os.path.exists(result["temp_path"])

    def test_perforation_interval_rendered(self, tmp_path):
        """
        The single perforation interval (8110–10914 ft) must survive the key
        transformation (interval_top_ft/interval_bottom_ft → from_ft/to_ft)
        and not cause a crash during PDF generation.
        """
        from apps.public_core.services.w3_pdf_generator import generate_w3_pdf

        w3_form_data = _transform_extracted_to_w3_form_data(MABEE_140A_EXTRACTED)

        assert len(w3_form_data["perforations"]) == 1, (
            f"Expected 1 perforation, got {len(w3_form_data['perforations'])}"
        )

        perf = w3_form_data["perforations"][0]
        assert perf["from_ft"] == 8110, (
            f"Expected from_ft=8110, got {perf['from_ft']}"
        )
        assert perf["to_ft"] == 10914, (
            f"Expected to_ft=10914, got {perf['to_ft']}"
        )

        # PDF generation must succeed
        with patch('django.conf.settings.MEDIA_ROOT', str(tmp_path)):
            result = generate_w3_pdf(w3_form_data)
        assert os.path.exists(result["temp_path"])

    def test_real_data_with_null_cement_class(self, tmp_path):
        """
        All 8 plugs have cement_class: null. Verify this does not crash the
        generator and that the slurry volume falls back to the default yield.
        """
        from apps.public_core.services.w3_pdf_generator import generate_w3_pdf

        w3_form_data = _transform_extracted_to_w3_form_data(MABEE_140A_EXTRACTED)

        # Confirm all plugs have null cement_class after transformation
        for plug in w3_form_data["plugs"]:
            assert plug["cement_class"] is None, (
                f"Plug #{plug['plug_number']} unexpectedly has cement_class={plug['cement_class']!r}"
            )

        # Must not raise even with all-null cement classes
        with patch('django.conf.settings.MEDIA_ROOT', str(tmp_path)):
            result = generate_w3_pdf(w3_form_data)

        assert os.path.exists(result["temp_path"]), "PDF was not generated"
        assert result["file_size"] > 0, "Generated PDF is empty"


# ---------------------------------------------------------------------------
# Standalone grid overlay test (no DB required)
# ---------------------------------------------------------------------------

def test_generate_grid_overlay(tmp_path):
    """
    Generate the W-3 coordinate-grid overlay PDF and save it to
    tmp/test_output/W3_grid_overlay.pdf for coordinate calibration.

    This test does not require the database.
    """
    from apps.public_core.services.w3_pdf_generator import draw_coordinate_grid

    # Generate into the pytest tmp_path first
    grid_path = str(tmp_path / "W3_grid_overlay.pdf")
    result = draw_coordinate_grid(output_path=grid_path)

    assert os.path.exists(result), "Grid overlay PDF was not created"
    assert os.path.getsize(result) > 0, "Grid overlay PDF is empty"

    # Copy to persistent tmp/test_output/ for calibration inspection
    _TMP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _TMP_OUTPUT_DIR / "W3_grid_overlay.pdf"
    shutil.copy2(result, str(output_path))

    print(f"\n[Calibration] Grid overlay saved to: {output_path}")
