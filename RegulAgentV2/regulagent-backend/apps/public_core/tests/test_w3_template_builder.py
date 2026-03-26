"""
Tests for W-3 Template Builder Service.

Tests cover:
- build_annotated_template() — creates annotated PDF with widgets
- verify_template() — reports widget inventory
- Widget naming, uniqueness, and type correctness
- Fill-and-bake roundtrip — values are extractable after bake
"""

from __future__ import annotations

import os
import pytest
from pathlib import Path
from unittest.mock import patch


class TestBuildAnnotatedTemplate:
    """Tests for build_annotated_template()."""

    def test_build_creates_annotated_pdf(self, tmp_path):
        """build_annotated_template() should produce a PDF file at the output path."""
        from apps.public_core.services.w3_template_builder import build_annotated_template

        output = tmp_path / "test_annotated.pdf"
        result = build_annotated_template(output_path=output)

        assert result == output
        assert output.exists()
        assert output.stat().st_size > 0

    def test_widget_count(self, tmp_path):
        """Annotated template should have ~162 widgets across 2 pages."""
        from apps.public_core.services.w3_template_builder import (
            build_annotated_template,
            verify_template,
        )

        output = tmp_path / "test_annotated.pdf"
        build_annotated_template(output_path=output)
        info = verify_template(output)

        # 23 header + 72 plugs + 30 casing + 20 perforations = 145 on page 1
        # 10 text + 6 checkbox + 1 remarks = 17 on page 2
        assert info["total_widgets"] == 162
        assert info["page_1_widgets"] == 145
        assert info["page_2_widgets"] == 17

    def test_widget_names_unique(self, tmp_path):
        """All widget field_names must be unique (no duplicates)."""
        from apps.public_core.services.w3_template_builder import (
            build_annotated_template,
            verify_template,
        )

        output = tmp_path / "test_annotated.pdf"
        build_annotated_template(output_path=output)
        info = verify_template(output)

        names = info["widget_names"]
        assert len(names) == len(set(names)), f"Duplicate widget names found: {[n for n in names if names.count(n) > 1]}"

    def test_checkbox_widgets_are_checkbox_type(self, tmp_path):
        """Checkbox widgets should have field_type == PDF_WIDGET_TYPE_CHECKBOX."""
        import fitz
        from apps.public_core.services.w3_template_builder import build_annotated_template

        output = tmp_path / "test_annotated.pdf"
        build_annotated_template(output_path=output)

        doc = fitz.open(str(output))
        checkbox_names = {
            "mud_filled_yes", "mud_filled_no",
            "all_wells_plugged_yes", "all_wells_plugged_no",
            "notice_given_yes", "notice_given_no",
        }

        found_checkboxes = {}
        for page in doc:
            for widget in page.widgets():
                if widget.field_name in checkbox_names:
                    found_checkboxes[widget.field_name] = widget.field_type
        doc.close()

        assert set(found_checkboxes.keys()) == checkbox_names, f"Missing checkboxes: {checkbox_names - set(found_checkboxes.keys())}"
        for name, ftype in found_checkboxes.items():
            assert ftype == fitz.PDF_WIDGET_TYPE_CHECKBOX, f"{name} is type {ftype}, expected checkbox"

    def test_fill_and_bake_roundtrip(self, tmp_path):
        """Fill widgets, bake, and verify values are extractable via get_text()."""
        import fitz
        from apps.public_core.services.w3_template_builder import build_annotated_template

        output = tmp_path / "test_annotated.pdf"
        build_annotated_template(output_path=output)

        doc = fitz.open(str(output))

        # Fill some test values
        test_values = {
            "api_number": "501-70575",
            "operator": "TEST OPERATOR LLC",
            "total_depth": "10000",
            "plug_1_sacks": "100",
            "casing_1_od_in": "9.625",
        }

        for page in doc:
            for widget in page.widgets():
                if widget.field_name in test_values:
                    widget.field_value = test_values[widget.field_name]
                    widget.update()

        # Save and reopen
        baked_path = tmp_path / "baked.pdf"
        doc.save(str(baked_path))
        doc.close()

        # Verify text is extractable
        doc2 = fitz.open(str(baked_path))
        page1_text = doc2[0].get_text()
        doc2.close()

        assert "501-70575" in page1_text
        assert "TEST OPERATOR LLC" in page1_text
        assert "10000" in page1_text

    def test_missing_blank_template_raises(self, tmp_path):
        """build_annotated_template() with missing input raises FileNotFoundError."""
        from apps.public_core.services.w3_template_builder import build_annotated_template

        with pytest.raises(FileNotFoundError):
            build_annotated_template(
                input_path=Path("/nonexistent/template.pdf"),
                output_path=tmp_path / "out.pdf",
            )

    def test_verify_missing_template_raises(self):
        """verify_template() with missing path raises FileNotFoundError."""
        from apps.public_core.services.w3_template_builder import verify_template

        with pytest.raises(FileNotFoundError):
            verify_template(Path("/nonexistent/template.pdf"))

    def test_plug_widget_names_pattern(self, tmp_path):
        """Plug widgets should follow the plug_{1-8}_{field} naming pattern."""
        from apps.public_core.services.w3_template_builder import (
            build_annotated_template,
            verify_template,
        )

        output = tmp_path / "test_annotated.pdf"
        build_annotated_template(output_path=output)
        info = verify_template(output)

        plug_names = [n for n in info["widget_names"] if n.startswith("plug_")]
        assert len(plug_names) == 72  # 8 columns × 9 rows

        # Check all 8 plugs have all 9 fields
        expected_fields = [
            "cementing_date", "hole_size_in", "depth_bottom_ft", "sacks",
            "slurry_volume_cf", "calculated_top_of_plug", "measured_top_of_plug",
            "slurry_weight_ppg", "cement_class",
        ]
        for plug_num in range(1, 9):
            for field in expected_fields:
                assert f"plug_{plug_num}_{field}" in plug_names
