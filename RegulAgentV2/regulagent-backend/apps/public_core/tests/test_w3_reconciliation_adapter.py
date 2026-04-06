"""Unit tests for W3 reconciliation adapter and PlugReconciliationEngine.

All DB operations are avoided — adapter functions accept plain dicts/mocks.
"""

from __future__ import annotations

from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from apps.public_core.services.w3_reconciliation_adapter import (
    build_w3_reconciliation,
    extract_actual_events_from_parse_result,
    extract_planned_plugs_from_snapshot,
)
from apps.public_core.services.plug_reconciliation import (
    DeviationLevel,
    PlugComparison,
    PlugReconciliationEngine,
    ReconciliationResult,
)


# ---------------------------------------------------------------------------
# extract_planned_plugs_from_snapshot
# ---------------------------------------------------------------------------

class TestExtractPlannedPlugs:
    def test_maps_steps_to_planned_plugs(self):
        """PlanSnapshot steps correctly map to planned plug format."""
        snapshot = MagicMock()
        snapshot.payload = {
            "steps": [
                {
                    "step_number": 1,
                    "step_type": "cement_plug",
                    "top_depth_ft": 5000,
                    "bottom_depth_ft": 5100,
                    "sacks": 50,
                    "cement_class": "A",
                    "formation": "Ellenburger",
                }
            ]
        }

        result = extract_planned_plugs_from_snapshot(snapshot)

        assert len(result) == 1
        plug = result[0]
        assert plug["plug_number"] == 1
        assert plug["plug_type"] == "cement_plug"
        assert plug["top_ft"] == 5000
        assert plug["bottom_ft"] == 5100
        assert plug["sacks"] == 50
        assert plug["cement_class"] == "A"
        assert plug["formation"] == "Ellenburger"

    def test_handles_empty_steps(self):
        """Empty steps list returns empty result."""
        snapshot = MagicMock()
        snapshot.payload = {"steps": []}
        result = extract_planned_plugs_from_snapshot(snapshot)
        assert result == []

    def test_handles_missing_steps_key(self):
        """Missing 'steps' key returns empty result."""
        snapshot = MagicMock()
        snapshot.payload = {}
        result = extract_planned_plugs_from_snapshot(snapshot)
        assert result == []

    def test_handles_none_payload(self):
        """None payload returns empty result without raising."""
        snapshot = MagicMock()
        snapshot.payload = None
        result = extract_planned_plugs_from_snapshot(snapshot)
        assert result == []

    def test_multiple_steps_all_mapped(self):
        """All steps in the payload are mapped."""
        snapshot = MagicMock()
        snapshot.payload = {
            "steps": [
                {"step_number": 1, "step_type": "cement_plug", "top_depth_ft": 8000, "bottom_depth_ft": 8100, "sacks": 80, "cement_class": "H"},
                {"step_number": 2, "step_type": "bridge_plug", "top_depth_ft": 5000, "bottom_depth_ft": 5005, "sacks": 0, "cement_class": None},
            ]
        }
        result = extract_planned_plugs_from_snapshot(snapshot)
        assert len(result) == 2
        assert result[0]["plug_number"] == 1
        assert result[1]["plug_number"] == 2

    def test_skips_non_dict_steps(self):
        """Non-dict entries in steps list are silently skipped."""
        snapshot = MagicMock()
        snapshot.payload = {"steps": ["invalid", None, {"step_number": 1, "step_type": "cement_plug"}]}
        result = extract_planned_plugs_from_snapshot(snapshot)
        assert len(result) == 1

    def test_formation_defaults_to_empty_string_when_absent(self):
        """formation field defaults to empty string if not in step."""
        snapshot = MagicMock()
        snapshot.payload = {"steps": [{"step_number": 1, "step_type": "cement_plug"}]}
        result = extract_planned_plugs_from_snapshot(snapshot)
        assert result[0]["formation"] == ""


# ---------------------------------------------------------------------------
# extract_actual_events_from_parse_result
# ---------------------------------------------------------------------------

class TestExtractActualEvents:
    def test_filters_to_plug_events_only(self):
        """Only plug-placement events are extracted; other types are filtered out."""
        parse_result = {
            "days": [
                {
                    "events": [
                        {
                            "event_type": "set_cement_plug",
                            "plug_number": 1,
                            "depth_top_ft": 5000,
                            "depth_bottom_ft": 5100,
                            "sacks": 48,
                            "cement_class": "A",
                        },
                        {
                            "event_type": "circulate",
                            "description": "Circulated 3 bbl",
                        },
                        {
                            "event_type": "squeeze",
                            "plug_number": 2,
                            "depth_top_ft": 3000,
                            "depth_bottom_ft": 3050,
                            "sacks": 30,
                            "cement_class": "A",
                        },
                    ]
                }
            ]
        }

        result = extract_actual_events_from_parse_result(parse_result)

        assert len(result) == 2
        event_types = {e["event_type"] for e in result}
        assert "set_cement_plug" in event_types
        assert "squeeze" in event_types
        assert "circulate" not in event_types

    def test_handles_empty_parse_result(self):
        """Empty dict and empty days list both return empty list."""
        assert extract_actual_events_from_parse_result({}) == []
        assert extract_actual_events_from_parse_result({"days": []}) == []

    def test_handles_none_parse_result(self):
        """None parse_result returns empty list."""
        assert extract_actual_events_from_parse_result(None) == []

    def test_handles_non_dict_parse_result(self):
        """Non-dict value returns empty list."""
        assert extract_actual_events_from_parse_result("bad") == []

    def test_all_supported_plug_types_extracted(self):
        """set_cement_plug, set_surface_plug, set_bridge_plug, squeeze, pump_cement are all included."""
        plug_types = [
            "set_cement_plug",
            "set_surface_plug",
            "set_bridge_plug",
            "squeeze",
            "pump_cement",
        ]
        events = [{"event_type": t, "plug_number": i} for i, t in enumerate(plug_types, 1)]
        parse_result = {"days": [{"events": events}]}

        result = extract_actual_events_from_parse_result(parse_result)
        assert len(result) == len(plug_types)

    def test_events_across_multiple_days_collected(self):
        """Events from multiple days are all collected."""
        parse_result = {
            "days": [
                {"events": [{"event_type": "set_cement_plug", "plug_number": 1, "depth_top_ft": 8000}]},
                {"events": [{"event_type": "set_bridge_plug", "plug_number": 2, "depth_top_ft": 5000}]},
            ]
        }
        result = extract_actual_events_from_parse_result(parse_result)
        assert len(result) == 2

    def test_event_fields_preserved(self):
        """The returned event dicts include expected fields."""
        parse_result = {
            "days": [
                {
                    "events": [
                        {
                            "event_type": "set_cement_plug",
                            "plug_number": 1,
                            "depth_top_ft": 5000.0,
                            "depth_bottom_ft": 5100.0,
                            "sacks": 48.0,
                            "cement_class": "A",
                            "tagged_depth_ft": 4990.0,
                        }
                    ]
                }
            ]
        }
        result = extract_actual_events_from_parse_result(parse_result)
        assert len(result) == 1
        event = result[0]
        assert event["depth_top_ft"] == 5000.0
        assert event["depth_bottom_ft"] == 5100.0
        assert event["sacks"] == 48.0
        assert event["cement_class"] == "A"
        assert event["tagged_depth_ft"] == 4990.0
        assert event["plug_number"] == 1


# ---------------------------------------------------------------------------
# PlugComparison justification fields
# ---------------------------------------------------------------------------

class TestJustificationFields:
    def test_plug_comparison_has_justification_fields(self):
        """PlugComparison includes justification tracking fields."""
        comp = PlugComparison(plug_number=1)
        assert comp.justification_note == ""
        assert comp.justification_resolved is False
        assert comp.variance_approval_found is False

    def test_plug_comparison_justification_can_be_set(self):
        """Justification fields can be updated on PlugComparison."""
        comp = PlugComparison(plug_number=1)
        comp.justification_note = "Depth adjusted due to WOC"
        comp.justification_resolved = True
        comp.justification_resolved_by = "engineer@example.com"
        comp.variance_approval_found = True
        comp.variance_approval_reference = "sundry:12345"

        assert comp.justification_note == "Depth adjusted due to WOC"
        assert comp.justification_resolved is True
        assert comp.variance_approval_reference == "sundry:12345"

    def test_reconciliation_result_has_divergence_counts(self):
        """ReconciliationResult includes resolved/unresolved count fields."""
        result = ReconciliationResult(api_number="42-501-70575")
        assert result.unresolved_divergences == 0
        assert result.resolved_divergences == 0

    def test_reconciliation_result_divergence_counts_settable(self):
        """Divergence counts can be updated on ReconciliationResult."""
        result = ReconciliationResult(api_number="42-501-70575")
        result.unresolved_divergences = 3
        result.resolved_divergences = 1
        assert result.unresolved_divergences == 3
        assert result.resolved_divergences == 1


# ---------------------------------------------------------------------------
# PlugReconciliationEngine
# ---------------------------------------------------------------------------

class TestPlugReconciliationEngine:
    def _planned_plug(self, plug_number=1, top_ft=5000, bottom_ft=5100, sacks=50, cement_class="A"):
        return {
            "plug_number": plug_number,
            "step_type": "cement_plug",
            "top_ft": top_ft,
            "bottom_ft": bottom_ft,
            "sacks_required": sacks,
            "cement_class": cement_class,
            "formation_name": "Test Formation",
        }

    def _actual_event(self, event_type="set_cement_plug", top_ft=5000, bottom_ft=5100, sacks=50, cement_class="A", plug_number=1):
        return {
            "event_type": event_type,
            "depth_top_ft": top_ft,
            "depth_bottom_ft": bottom_ft,
            "sacks": sacks,
            "cement_class": cement_class,
            "tagged_depth_ft": None,
            "plug_number": plug_number,
        }

    def test_perfect_match_gives_compliant_status(self):
        """Planned and actual with matching depth/sacks/class → compliant."""
        engine = PlugReconciliationEngine()
        planned = [self._planned_plug()]
        actual = [self._actual_event()]

        result = engine.reconcile(planned, actual, api_number="42-501-70575")

        assert result.overall_status == "compliant"
        assert result.matches == 1
        assert result.major_deviations == 0
        assert result.minor_deviations == 0

    def test_missing_plug_gives_major_deviation(self):
        """Planned plug with no actual match → MISSING, major_deviations status."""
        engine = PlugReconciliationEngine()
        planned = [self._planned_plug(top_ft=5000, bottom_ft=5100)]
        actual = []

        result = engine.reconcile(planned, actual, api_number="42-501-70575")

        assert result.overall_status == "major_deviations"
        assert result.missing_plugs == 1
        assert result.comparisons[0].deviation_level == DeviationLevel.MISSING

    def test_added_plug_gives_minor_deviation_status(self):
        """Actual plug with no planned match → ADDED, minor_deviations status."""
        engine = PlugReconciliationEngine()
        planned = []
        actual = [self._actual_event(top_ft=3000, bottom_ft=3050)]

        result = engine.reconcile(planned, actual, api_number="42-501-70575")

        assert result.overall_status == "minor_deviations"
        assert result.added_plugs == 1
        assert result.comparisons[0].deviation_level == DeviationLevel.ADDED

    def test_large_depth_deviation_gives_major(self):
        """Depth deviation > 5x tolerance → MAJOR deviation."""
        engine = PlugReconciliationEngine(depth_tolerance_ft=20)
        # Planned at 5050', actual at 5200' → deviation 150', > 5 * 20 = 100'
        planned = [self._planned_plug(top_ft=5000, bottom_ft=5100)]
        actual = [self._actual_event(top_ft=5150, bottom_ft=5250)]

        result = engine.reconcile(planned, actual, api_number="42-501-70575")

        assert result.comparisons[0].deviation_level == DeviationLevel.MAJOR

    def test_small_depth_deviation_gives_minor(self):
        """Depth deviation between tolerance and 5x → MINOR deviation."""
        engine = PlugReconciliationEngine(depth_tolerance_ft=20)
        # Planned midpoint at 5050', actual midpoint at 5100' → deviation 50', <= 100' → MINOR
        planned = [self._planned_plug(top_ft=5000, bottom_ft=5100)]
        actual = [self._actual_event(top_ft=5050, bottom_ft=5150)]

        result = engine.reconcile(planned, actual, api_number="42-501-70575")

        assert result.comparisons[0].deviation_level == DeviationLevel.MINOR

    def test_cement_class_mismatch_is_always_major(self):
        """Cement class mismatch → MAJOR deviation regardless of depth."""
        engine = PlugReconciliationEngine()
        planned = [self._planned_plug(cement_class="A")]
        actual = [self._actual_event(cement_class="H")]

        result = engine.reconcile(planned, actual, api_number="42-501-70575")

        assert result.comparisons[0].deviation_level == DeviationLevel.MAJOR
        assert any("cement class" in n.lower() for n in result.comparisons[0].deviation_notes)

    def test_sack_count_within_tolerance_is_match(self):
        """Sack count within 10% → MATCH."""
        engine = PlugReconciliationEngine(sack_tolerance_pct=0.10)
        planned = [self._planned_plug(sacks=50)]
        actual = [self._actual_event(sacks=54)]  # 8% deviation

        result = engine.reconcile(planned, actual, api_number="42-501-70575")

        assert result.overall_status == "compliant"

    def test_large_sack_deviation_gives_major(self):
        """Sack deviation > 30% → MAJOR."""
        engine = PlugReconciliationEngine(sack_tolerance_pct=0.10)
        planned = [self._planned_plug(sacks=50)]
        actual = [self._actual_event(sacks=80)]  # 60% deviation

        result = engine.reconcile(planned, actual, api_number="42-501-70575")

        assert result.comparisons[0].deviation_level == DeviationLevel.MAJOR

    def test_summary_narrative_generated(self):
        """reconcile always produces a non-empty summary_narrative."""
        engine = PlugReconciliationEngine()
        result = engine.reconcile(
            [self._planned_plug()],
            [self._actual_event()],
            "42-501-70575",
        )
        assert len(result.summary_narrative) > 0

    def test_api_number_preserved_in_result(self):
        """api_number is passed through to ReconciliationResult."""
        engine = PlugReconciliationEngine()
        result = engine.reconcile([], [], "42-501-70575")
        assert result.api_number == "42-501-70575"

    def test_comparisons_sorted_by_plug_number(self):
        """Comparisons are sorted by plug_number ascending."""
        engine = PlugReconciliationEngine()
        planned = [
            self._planned_plug(plug_number=3, top_ft=3000, bottom_ft=3100),
            self._planned_plug(plug_number=1, top_ft=8000, bottom_ft=8100),
        ]
        actual = [
            self._actual_event(plug_number=3, top_ft=3000, bottom_ft=3100),
            self._actual_event(plug_number=1, top_ft=8000, bottom_ft=8100),
        ]
        result = engine.reconcile(planned, actual, "42-501-70575")
        plug_numbers = [c.plug_number for c in result.comparisons]
        assert plug_numbers == sorted(plug_numbers)


# ---------------------------------------------------------------------------
# build_w3_reconciliation
# ---------------------------------------------------------------------------

class TestBuildW3Reconciliation:
    def _make_session(self, plan_steps=None, parse_events=None):
        session = MagicMock()
        session.api_number = "42-501-70575"
        session.well = None  # No well → variance search skipped

        if plan_steps is not None:
            session.plan_snapshot = MagicMock()
            session.plan_snapshot.payload = {"steps": plan_steps}
        else:
            session.plan_snapshot = None

        session.parse_result = {}
        if parse_events is not None:
            session.parse_result = {"days": [{"events": parse_events}]}

        return session

    def test_raises_if_no_plan_snapshot(self):
        """build_w3_reconciliation raises ValueError when plan_snapshot is None."""
        session = self._make_session(plan_steps=None)
        with pytest.raises(ValueError, match="No approved plan"):
            build_w3_reconciliation(session)

    def test_returns_dict_with_comparisons_key(self):
        """Returns a dict containing 'comparisons'."""
        session = self._make_session(
            plan_steps=[
                {
                    "step_number": 1,
                    "step_type": "cement_plug",
                    "top_depth_ft": 5000,
                    "bottom_depth_ft": 5100,
                    "sacks": 50,
                    "cement_class": "A",
                }
            ],
            parse_events=[
                {
                    "event_type": "set_cement_plug",
                    "plug_number": 1,
                    "depth_top_ft": 5000,
                    "depth_bottom_ft": 5100,
                    "sacks": 50,
                    "cement_class": "A",
                }
            ],
        )
        result = build_w3_reconciliation(session)

        assert isinstance(result, dict)
        assert "comparisons" in result
        assert "api_number" in result
        assert result["api_number"] == "42-501-70575"

    def test_unresolved_divergences_counted(self):
        """Missing plug increments unresolved_divergences."""
        session = self._make_session(
            plan_steps=[
                {
                    "step_number": 1,
                    "step_type": "cement_plug",
                    "top_depth_ft": 5000,
                    "bottom_depth_ft": 5100,
                    "sacks": 50,
                    "cement_class": "A",
                }
            ],
            parse_events=[],  # No actual events → plug is MISSING
        )
        result = build_w3_reconciliation(session)
        assert result["unresolved_divergences"] >= 1

    def test_compliant_when_plugs_match(self):
        """Perfect match → overall_status compliant."""
        session = self._make_session(
            plan_steps=[
                {
                    "step_number": 1,
                    "step_type": "cement_plug",
                    "top_depth_ft": 5000,
                    "bottom_depth_ft": 5100,
                    "sacks": 50,
                    "cement_class": "A",
                }
            ],
            parse_events=[
                {
                    "event_type": "set_cement_plug",
                    "plug_number": 1,
                    "depth_top_ft": 5000,
                    "depth_bottom_ft": 5100,
                    "sacks": 50,
                    "cement_class": "A",
                }
            ],
        )
        result = build_w3_reconciliation(session)
        assert result["overall_status"] == "compliant"

    def test_variance_approval_search_called_for_deviations(self):
        """search_variance_approvals is called when well is set and deviation is major/minor."""
        session = self._make_session(
            plan_steps=[
                {
                    "step_number": 1,
                    "step_type": "cement_plug",
                    "top_depth_ft": 5000,
                    "bottom_depth_ft": 5100,
                    "sacks": 50,
                    "cement_class": "A",
                }
            ],
            parse_events=[
                {
                    "event_type": "set_cement_plug",
                    "plug_number": 1,
                    "depth_top_ft": 5200,  # Large depth deviation → MAJOR
                    "depth_bottom_ft": 5300,
                    "sacks": 50,
                    "cement_class": "A",
                }
            ],
        )
        session.well = MagicMock()  # Set a well so variance search runs

        with patch(
            "apps.public_core.services.plug_reconciliation.PlugReconciliationEngine.search_variance_approvals"
        ) as mock_search:
            mock_search.side_effect = lambda well, comp: comp
            build_w3_reconciliation(session)

        mock_search.assert_called()
