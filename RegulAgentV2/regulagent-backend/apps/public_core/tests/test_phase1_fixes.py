"""Tests for Phase 1 data foundation fixes."""
import pytest


class TestJsonSchemaArrayTypes:
    """Verify _json_schema_for returns array type for formation_data, etc."""

    def test_pa_procedure_formation_data_is_array(self):
        from apps.public_core.services.openai_extraction import _json_schema_for
        schema = _json_schema_for("pa_procedure")
        props = schema["schema"]["properties"]
        assert props["formation_data"]["type"] == "array", f"Got {props['formation_data']}"

    def test_pa_procedure_existing_perforations_is_array(self):
        from apps.public_core.services.openai_extraction import _json_schema_for
        schema = _json_schema_for("pa_procedure")
        props = schema["schema"]["properties"]
        assert props["existing_perforations"]["type"] == "array"

    def test_pa_procedure_pa_procedure_steps_is_array(self):
        from apps.public_core.services.openai_extraction import _json_schema_for
        schema = _json_schema_for("pa_procedure")
        props = schema["schema"]["properties"]
        assert props["pa_procedure_steps"]["type"] == "array"

    def test_pa_procedure_required_sections_no_w3a_reference(self):
        from apps.public_core.services.openai_extraction import SUPPORTED_TYPES
        sections = SUPPORTED_TYPES["pa_procedure"]["required_sections"]
        assert "w3a_reference" not in sections

    def test_pa_procedure_required_sections_has_new_fields(self):
        from apps.public_core.services.openai_extraction import SUPPORTED_TYPES
        sections = SUPPORTED_TYPES["pa_procedure"]["required_sections"]
        assert "notice_info" in sections
        assert "existing_wellbore_condition" in sections
        assert "existing_perforations" in sections
        assert "casing_record" in sections


class TestCategorizeStep:
    """Verify _categorize_step() classifies correctly."""

    def test_plug_types(self):
        from apps.public_core.services.operator_packet_importer import _categorize_step
        for st in ["cibp", "cement_plug", "surface_plug", "topoff", "perf_squeeze", "squeeze"]:
            assert _categorize_step(st) == "plug", f"{st} should be plug"

    def test_milestone_types(self):
        from apps.public_core.services.operator_packet_importer import _categorize_step
        for st in ["miru", "cleanout", "run_cbl", "pressure_test", "casing_cut", "pooh"]:
            assert _categorize_step(st) == "milestone", f"{st} should be milestone"

    def test_unknown_type(self):
        from apps.public_core.services.operator_packet_importer import _categorize_step
        assert _categorize_step("some_random_thing") == "unknown"
        assert _categorize_step("") == "unknown"

    def test_case_insensitive(self):
        from apps.public_core.services.operator_packet_importer import _categorize_step
        assert _categorize_step("CIBP") == "plug"
        assert _categorize_step("Miru") == "milestone"


class TestNormalizePaSteps:
    """Verify _normalize_pa_steps_to_plan_format preserves new fields."""

    def test_preserves_category(self):
        from apps.public_core.services.operator_packet_importer import _normalize_pa_steps_to_plan_format
        json_data = {
            "pa_procedure_steps": [
                {"operation": "CIBP", "step_number": 1, "depth_top_ft": 100},
                {"operation": "miru", "step_number": 2},
            ]
        }
        result = _normalize_pa_steps_to_plan_format(json_data)
        steps = result["steps"]
        assert steps[0]["category"] == "plug"
        assert steps[1]["category"] == "milestone"

    def test_preserves_extra_fields(self):
        from apps.public_core.services.operator_packet_importer import _normalize_pa_steps_to_plan_format
        json_data = {
            "pa_procedure_steps": [
                {
                    "operation": "cement_plug",
                    "step_number": 1,
                    "perf_depth_ft": 5000,
                    "pressure_test_psi": 1500,
                    "pressure_test_duration_min": 30,
                    "woc_required": True,
                    "formations_referenced": ["Morrow"],
                }
            ]
        }
        result = _normalize_pa_steps_to_plan_format(json_data)
        step = result["steps"][0]
        assert step["perf_depth_ft"] == 5000
        assert step["pressure_test_psi"] == 1500
        assert step["pressure_test_duration_min"] == 30
        assert step["woc_required"] is True
        assert step["formations_referenced"] == ["Morrow"]


class TestFormationReader:
    """Verify extract_formations_from_payload reads direct formations first."""

    def test_direct_formations_list(self):
        from apps.public_core.services.well_geometry_builder import extract_formations_from_payload
        payload = {
            "formations": [
                {"formation_name": "Morrow", "top_ft": 13619},
                {"formation_name": "Atoka", "top_ft": 12500},
            ]
        }
        result = extract_formations_from_payload(payload)
        assert len(result) == 2
        assert result[0]["formation"] == "Morrow"
        assert result[0]["top_ft"] == 13619.0
        assert result[1]["formation"] == "Atoka"
        assert result[1]["top_ft"] == 12500.0

    def test_direct_formations_with_none_depth(self):
        from apps.public_core.services.well_geometry_builder import extract_formations_from_payload
        payload = {
            "formations": [
                {"formation_name": "Unknown", "top_ft": None},
            ]
        }
        result = extract_formations_from_payload(payload)
        assert len(result) == 1
        assert result[0]["formation"] == "Unknown"
        assert result[0]["top_ft"] is None

    def test_fallback_to_legacy(self):
        from apps.public_core.services.well_geometry_builder import extract_formations_from_payload
        # No "formations" key — should fall back to formation_tops_detected
        payload = {
            "formation_tops_detected": ["Spraberry"],
            "steps": [
                {"top_ft": 6750, "regulatory_basis": ["rrc.d.08A.county:formation_top:Spraberry"]}
            ],
        }
        result = extract_formations_from_payload(payload)
        assert len(result) == 1
        assert result[0]["formation"] == "Spraberry"

    def test_empty_formations_falls_through(self):
        from apps.public_core.services.well_geometry_builder import extract_formations_from_payload
        # Empty formations list should fall through to legacy
        payload = {
            "formations": [],
            "formation_tops_detected": ["Dean"],
            "steps": [
                {"top_ft": 7000, "regulatory_basis": ["rrc.d.08A.county:formation_top:Dean"]}
            ],
        }
        result = extract_formations_from_payload(payload)
        assert len(result) >= 1
