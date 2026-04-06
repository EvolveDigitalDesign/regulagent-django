"""Tests for the OperationalMilestoneReconciler."""
import pytest
from apps.public_core.services.milestone_reconciler import (
    MilestoneStatus,
    MilestoneComparison,
    reconcile,
    MILESTONE_KEYWORDS,
)


class TestMilestoneKeywords:
    def test_all_expected_types_present(self):
        expected = {"miru", "cleanout", "run_cbl", "pressure_test", "casing_cut", "cut_wellhead"}
        assert expected.issubset(set(MILESTONE_KEYWORDS.keys()))


class TestReconcile:
    def _make_parse_result(self, days):
        return {"days": days}

    def _make_day(self, date, narrative="", events=None):
        return {
            "work_date": date,
            "daily_narrative": narrative,
            "events": events or [],
        }

    def test_empty_milestones_returns_empty(self):
        result = reconcile([], {"days": []})
        assert result == []

    def test_found_by_keyword_in_narrative(self):
        milestones = [{"step_number": 1, "step_type": "miru", "description": "Move in rig"}]
        parse_result = self._make_parse_result([
            self._make_day("2024-01-01", narrative="Rigged up on location at 0800"),
        ])
        results = reconcile(milestones, parse_result)
        assert len(results) == 1
        assert results[0].status == MilestoneStatus.FOUND
        assert "2024-01-01" in results[0].matched_work_dates

    def test_found_by_keyword_in_event(self):
        milestones = [{"step_number": 2, "step_type": "cleanout", "description": "Clean out to TD"}]
        parse_result = self._make_parse_result([
            self._make_day("2024-01-02", events=[
                {"description": "Circulate and clean out hole to 7500'"}
            ]),
        ])
        results = reconcile(milestones, parse_result)
        assert len(results) == 1
        assert results[0].status == MilestoneStatus.FOUND

    def test_not_found(self):
        milestones = [{"step_number": 3, "step_type": "run_cbl", "description": "Run CBL"}]
        parse_result = self._make_parse_result([
            self._make_day("2024-01-01", narrative="Set plug at 5000'"),
        ])
        results = reconcile(milestones, parse_result)
        assert len(results) == 1
        assert results[0].status == MilestoneStatus.NOT_FOUND

    def test_partial_by_depth_fallback(self):
        milestones = [
            {"step_number": 4, "step_type": "pressure_test", "description": "Test",
             "depth_top_ft": 5000}
        ]
        parse_result = self._make_parse_result([
            self._make_day("2024-01-03", narrative="Worked at 5000' interval"),
        ])
        results = reconcile(milestones, parse_result)
        assert len(results) == 1
        assert results[0].status == MilestoneStatus.PARTIAL

    def test_multiple_milestones(self):
        milestones = [
            {"step_number": 1, "step_type": "miru", "description": "Rig up"},
            {"step_number": 2, "step_type": "cleanout", "description": "Clean out"},
            {"step_number": 3, "step_type": "run_cbl", "description": "Run CBL"},
        ]
        parse_result = self._make_parse_result([
            self._make_day("2024-01-01", narrative="Move in and rig up"),
            self._make_day("2024-01-02", narrative="Set plugs"),
        ])
        results = reconcile(milestones, parse_result)
        assert len(results) == 3
        assert results[0].status == MilestoneStatus.FOUND  # miru
        assert results[1].status == MilestoneStatus.NOT_FOUND  # cleanout
        assert results[2].status == MilestoneStatus.NOT_FOUND  # cbl

    def test_comparison_type_is_milestone(self):
        milestones = [{"step_number": 1, "step_type": "miru", "description": "Rig up"}]
        parse_result = self._make_parse_result([
            self._make_day("2024-01-01", narrative="Rigged up"),
        ])
        results = reconcile(milestones, parse_result)
        assert results[0].comparison_type == "milestone"
