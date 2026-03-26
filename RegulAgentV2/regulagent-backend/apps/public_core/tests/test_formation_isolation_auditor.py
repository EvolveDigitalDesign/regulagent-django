"""Tests for FormationIsolationAuditor."""
import json
import os
import pytest
from unittest.mock import patch
from apps.public_core.services.formation_isolation_auditor import (
    IsolationStatus,
    IsolationRequirement,
    FormationAuditResult,
    audit,
    _trigger_matches,
    _load_conditions,
)


class TestLoadConditions:
    def test_loads_conditions_json(self):
        conditions = _load_conditions()
        assert len(conditions) > 0
        assert any(c["condition_id"] == "nm_fig_d_surface_plug" for c in conditions)


class TestTriggerMatches:
    def test_always_trigger(self):
        assert _trigger_matches({"type": "always"}, [], [], []) is True

    def test_has_perforations_true(self):
        assert _trigger_matches(
            {"type": "has_perforations"}, [], [{"top_ft": 5000}], []
        ) is True

    def test_has_perforations_false(self):
        assert _trigger_matches({"type": "has_perforations"}, [], [], []) is False

    def test_casing_shoe_exists_true(self):
        assert _trigger_matches(
            {"type": "casing_shoe_exists"}, [], [], [{"shoe_depth_ft": 3000}]
        ) is True

    def test_casing_shoe_exists_false(self):
        assert _trigger_matches({"type": "casing_shoe_exists"}, [], [], []) is False

    def test_keyword_match_true(self):
        assert _trigger_matches(
            {"type": "keyword_match", "keywords": ["duqw"]},
            [{"formation": "DUQW Zone", "top_ft": 1000}], [], [],
        ) is True

    def test_keyword_match_false(self):
        assert _trigger_matches(
            {"type": "keyword_match", "keywords": ["duqw"]},
            [{"formation": "Morrow", "top_ft": 13000}], [], [],
        ) is False


class TestAudit:
    def test_basic_compliant_well(self):
        """Well with surface plug that satisfies the always-on surface requirement."""
        result = audit(
            formation_tops=[],
            existing_perforations=[],
            casing_record=[{"shoe_depth_ft": 5000}],
            actual_plugs=[
                {"plug_number": 1, "top_ft": 0, "bottom_ft": 100},
                {"plug_number": 2, "top_ft": 4950, "bottom_ft": 5050},
            ],
            api_number="30-000-00001",
        )
        assert isinstance(result, FormationAuditResult)
        assert result.api_number == "30-000-00001"
        # Should have at least some requirements evaluated
        assert result.total_requirements > 0

    def test_no_plugs_returns_unsatisfied(self):
        result = audit(
            formation_tops=[{"formation": "Morrow", "top_ft": 13000}],
            existing_perforations=[{"top_ft": 12900, "bottom_ft": 13100}],
            casing_record=[{"shoe_depth_ft": 5000}],
            actual_plugs=[],
            api_number="30-000-00002",
        )
        assert result.unsatisfied > 0
        assert result.overall_status in ("deficient", "indeterminate")

    def test_empty_well_data(self):
        result = audit([], [], [], [], "30-000-00003")
        # With no perforations, no casing — only "always" triggers fire
        assert isinstance(result, FormationAuditResult)

    def test_result_has_narrative(self):
        result = audit([], [], [], [], "30-000-00004")
        assert "30-000-00004" in result.narrative
