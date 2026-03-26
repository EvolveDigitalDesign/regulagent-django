"""
Phase 5 Tests — DWR Parser, Plug Reconciliation, and Subsequent Report Generator.

Coverage:
- DWRParser: keyword-based event detection, depth/sack/pressure/class extraction,
  dataclass field initialization, DWRDay event list.
- PlugReconciliationEngine: planned-vs-actual matching, tolerance thresholds,
  deviation classification, summary counts, overall status, narrative generation.
- SubsequentReportGenerator.create_subsequent_form: ORM persistence (requires DB).
"""

from __future__ import annotations

import pytest
from datetime import date, time
from unittest.mock import MagicMock, PropertyMock


# ---------------------------------------------------------------------------
# Helpers / shared factories
# ---------------------------------------------------------------------------

def _make_planned(plug_number=1, top_ft=7000.0, bottom_ft=6900.0,
                  sacks_required=50.0, cement_class="H", step_type="cement_plug",
                  formation_name=""):
    return {
        "plug_number": plug_number,
        "step_type": step_type,
        "top_ft": top_ft,
        "bottom_ft": bottom_ft,
        "sacks_required": sacks_required,
        "cement_class": cement_class,
        "formation_name": formation_name,
    }


def _make_actual(event_type="set_cement_plug", depth_top_ft=7000.0,
                 depth_bottom_ft=6900.0, sacks=50.0, cement_class="H",
                 tagged_depth_ft=None):
    return {
        "event_type": event_type,
        "depth_top_ft": depth_top_ft,
        "depth_bottom_ft": depth_bottom_ft,
        "sacks": sacks,
        "cement_class": cement_class,
        "tagged_depth_ft": tagged_depth_ft,
    }


# ===========================================================================
# DWR Parser — Event Detection
# ===========================================================================

class TestDWREventDetection:
    """Test keyword-based event detection from narrative text."""

    def setup_method(self):
        from apps.public_core.services.dwr_parser import DWRParser
        self.parser = DWRParser()

    def test_detect_cement_plug_event(self):
        """'spot cement plug from 7050 to 6950' → set_cement_plug."""
        result = self.parser._classify_event_type(
            "spot cement plug from 7050 to 6950"
        )
        assert result == "set_cement_plug"

    def test_detect_bridge_plug_event(self):
        """'set cibp at 7050' → set_bridge_plug (cibp checked before cement)."""
        result = self.parser._classify_event_type("set cibp at 7050")
        assert result == "set_bridge_plug"

    def test_detect_squeeze_event(self):
        """'squeeze cement at 5000' → squeeze."""
        result = self.parser._classify_event_type("squeeze cement at 5000")
        assert result == "squeeze"

    def test_detect_tag_event(self):
        """'tag cement at 6950' → tag_toc."""
        result = self.parser._classify_event_type("tag cement at 6950")
        assert result == "tag_toc"

    def test_detect_surface_plug(self):
        """'circulate surface plug' → set_surface_plug (surface checked first)."""
        result = self.parser._classify_event_type("circulate surface plug")
        assert result == "set_surface_plug"

    def test_detect_woc(self):
        """'woc 8 hours' → woc."""
        result = self.parser._classify_event_type("woc 8 hours")
        assert result == "woc"

    def test_detect_pressure_test(self):
        """'pressure test casing to 1500 psi' → pressure_test."""
        result = self.parser._classify_event_type("pressure test casing to 1500 psi")
        assert result == "pressure_test"

    def test_no_event_in_generic_text(self):
        """Generic logistics text should not match any event type."""
        result = self.parser._classify_event_type(
            "loaded trucks and drove to yard"
        )
        assert result is None

    def test_short_line_not_classified(self):
        """Lines shorter than 5 chars return None from detect_events."""
        events = self.parser._detect_events_from_text("ok\n")
        assert events == []


# ===========================================================================
# DWR Parser — Extraction Helpers
# ===========================================================================

class TestDWRExtractionHelpers:
    """Test depth, sack, pressure, and cement class extraction from text."""

    def setup_method(self):
        from apps.public_core.services.dwr_parser import DWRParser
        self.parser = DWRParser()

    # Depth -------------------------------------------------------------------

    def test_extract_depth_with_comma(self):
        """'at 7,050 ft' → 7050.0."""
        assert self.parser._extract_depth("spotted cement at 7,050 ft") == 7050.0

    def test_extract_depth_with_ft_suffix(self):
        """'7050 ft' → 7050.0."""
        assert self.parser._extract_depth("set plug at 7050 ft") == 7050.0

    def test_extract_depth_at_symbol(self):
        """'@ 7050' → 7050.0."""
        assert self.parser._extract_depth("tag @ 7050") == 7050.0

    def test_extract_depth_returns_none_on_no_match(self):
        """Text with no depth returns None."""
        assert self.parser._extract_depth("rig up and mobilize equipment") is None

    # Tagged depth ------------------------------------------------------------

    def test_extract_tagged_depth(self):
        """'tagged at 6950 ft' → 6950.0."""
        assert self.parser._extract_tagged_depth("tagged at 6950 ft") == 6950.0

    def test_extract_toc_depth(self):
        """'toc at 6980' → 6980.0."""
        assert self.parser._extract_tagged_depth("toc at 6980") == 6980.0

    # Sacks -------------------------------------------------------------------

    def test_extract_sacks(self):
        """'45 sacks' → 45.0."""
        assert self.parser._extract_sacks("pumped 45 sacks Class H cement") == 45.0

    def test_extract_sacks_sx(self):
        """'45 sx' → 45.0."""
        assert self.parser._extract_sacks("used 45 sx cement") == 45.0

    def test_extract_sacks_sks(self):
        """'100 sks' → 100.0."""
        assert self.parser._extract_sacks("pumped 100 sks") == 100.0

    def test_extract_sacks_with_comma(self):
        """'1,200 sacks' → 1200.0."""
        assert self.parser._extract_sacks("used 1,200 sacks Class G") == 1200.0

    def test_extract_sacks_returns_none(self):
        """Text with no sack count returns None."""
        assert self.parser._extract_sacks("spot cement from 7000 to 6900") is None

    # Pressure ----------------------------------------------------------------

    def test_extract_pressure_psi(self):
        """'1500 psi' → 1500.0."""
        assert self.parser._extract_pressure("tested casing to 1500 psi") == 1500.0

    def test_extract_pressure_test_to_pattern(self):
        """'test to 2000' → 2000.0."""
        assert self.parser._extract_pressure("pressure test to 2000") == 2000.0

    def test_extract_pressure_returns_none(self):
        """No pressure text → None."""
        assert self.parser._extract_pressure("woc 4 hours") is None

    # Cement class ------------------------------------------------------------

    def test_extract_cement_class_h(self):
        """'Class H cement' → 'H'."""
        assert self.parser._extract_cement_class("pumped Class H cement") == "H"

    def test_extract_cement_class_g(self):
        """'class g' → 'G' (uppercase normalised)."""
        assert self.parser._extract_cement_class("use class g blend") == "G"

    def test_extract_cement_class_c(self):
        """'Class C' → 'C'."""
        assert self.parser._extract_cement_class("spot Class C plug") == "C"

    def test_extract_cement_class_returns_none(self):
        """No cement class in text → None."""
        assert self.parser._extract_cement_class("tag at 6950 ft") is None


# ===========================================================================
# DWR Parser — Data Model Tests
# ===========================================================================

class TestDWRParseResult:
    """Test DWRParseResult and DWRDay/DWREvent dataclass field initialisation."""

    def test_parse_result_defaults(self):
        """DWRParseResult fields initialise with correct defaults."""
        from apps.public_core.services.dwr_parser import DWRParseResult

        result = DWRParseResult(api_number="30-025-12345")
        assert result.api_number == "30-025-12345"
        assert result.well_name == ""
        assert result.operator == ""
        assert result.days == []
        assert result.total_days == 0
        assert result.parse_method == ""
        assert result.confidence == 0.0
        assert result.warnings == []

    def test_dwr_event_fields(self):
        """DWREvent stores all operational fields correctly."""
        from apps.public_core.services.dwr_parser import DWREvent

        ev = DWREvent(
            event_type="set_cement_plug",
            description="Spot cement plug 7050 to 6950",
            depth_top_ft=7050.0,
            depth_bottom_ft=6950.0,
            tagged_depth_ft=6960.0,
            cement_class="H",
            sacks=45.0,
            pressure_psi=1500.0,
        )
        assert ev.event_type == "set_cement_plug"
        assert ev.depth_top_ft == 7050.0
        assert ev.depth_bottom_ft == 6950.0
        assert ev.tagged_depth_ft == 6960.0
        assert ev.cement_class == "H"
        assert ev.sacks == 45.0
        assert ev.pressure_psi == 1500.0
        assert ev.start_time is None
        assert ev.end_time is None

    def test_dwr_day_holds_events(self):
        """DWRDay correctly stores a list of DWREvent objects."""
        from apps.public_core.services.dwr_parser import DWRDay, DWREvent

        ev1 = DWREvent(event_type="set_cement_plug", description="Plug 1")
        ev2 = DWREvent(event_type="tag_toc", description="Tag TOC")

        day = DWRDay(work_date=date(2024, 6, 1), day_number=1, events=[ev1, ev2])
        assert len(day.events) == 2
        assert day.events[0].event_type == "set_cement_plug"
        assert day.events[1].event_type == "tag_toc"

    def test_dwr_day_defaults(self):
        """DWRDay optional fields default to None / empty."""
        from apps.public_core.services.dwr_parser import DWRDay

        day = DWRDay(work_date=date(2024, 1, 1), day_number=1)
        assert day.events == []
        assert day.daily_narrative == ""
        assert day.crew_size is None
        assert day.rig_name is None
        assert day.weather is None

    def test_detect_events_from_text_returns_events(self):
        """_detect_events_from_text on multi-keyword text yields DWREvent list."""
        from apps.public_core.services.dwr_parser import DWRParser

        parser = DWRParser()
        text = (
            "Spot cement plug from 7050 to 6950 using 45 sacks Class H.\n"
            "Wait on cement 8 hours."
        )
        events = parser._detect_events_from_text(text)
        types = [ev.event_type for ev in events]
        assert "set_cement_plug" in types
        assert "woc" in types

    def test_build_daily_narrative_empty(self):
        """Empty event list → empty narrative."""
        from apps.public_core.services.dwr_parser import DWRParser

        parser = DWRParser()
        assert parser._build_daily_narrative([]) == ""

    def test_build_daily_narrative_with_events(self):
        """Narrative includes description and depth info."""
        from apps.public_core.services.dwr_parser import DWRParser, DWREvent

        parser = DWRParser()
        ev = DWREvent(
            event_type="set_cement_plug",
            description="Spot cement plug",
            depth_top_ft=7050.0,
            sacks=45.0,
            cement_class="H",
        )
        narrative = parser._build_daily_narrative([ev])
        assert "7,050" in narrative
        assert "45" in narrative
        assert "Class H" in narrative


# ===========================================================================
# DWR Parser — Parser Fixes (sxs abbreviation, parenthesized depths, from/to)
# ===========================================================================

class TestDWRParserFixes:
    """Test fixes for sxs abbreviation, parenthesized depths, and from/to ranges."""

    def setup_method(self):
        from apps.public_core.services.dwr_parser import DWRParser
        self.parser = DWRParser()

    # --- sxs abbreviation ---

    def test_extract_sacks_sxs(self):
        """'20 sxs' → 20.0."""
        assert self.parser._extract_sacks("Spot (20 sxs) class (H) cement") == 20.0

    def test_extract_sacks_sxs_no_space(self):
        """'45sxs' (no space) → 45.0."""
        assert self.parser._extract_sacks("Squeezed (45sxs) class (C) cement") == 45.0

    def test_extract_sacks_sxs_large(self):
        """'230 sxs' → 230.0."""
        assert self.parser._extract_sacks("circulated (230 sxs) class (C) cement") == 230.0

    # --- parenthesized depths ---

    def test_extract_depth_parenthesized(self):
        """'(7020')' → 7020.0."""
        assert self.parser._extract_depth("From (7020') to (6777')") == 7020.0

    def test_extract_depth_parenthesized_no_prime(self):
        """'(6777)' → matched."""
        assert self.parser._extract_depth("depth (6777)") is not None

    # --- from/to depth range ---

    def test_extract_depth_range_basic(self):
        """'From (7020') to (6777')' → top=6777, bottom=7020."""
        top, bottom = self.parser._extract_depth_range("From (7020') to (6777')")
        assert top == 6777.0
        assert bottom == 7020.0

    def test_extract_depth_range_no_parens(self):
        """'from 7020 to 6777' → top=6777, bottom=7020."""
        top, bottom = self.parser._extract_depth_range("from 7020 to 6777")
        assert top == 6777.0
        assert bottom == 7020.0

    def test_extract_depth_range_ascending(self):
        """'From (2500') to (2400')' → top=2400, bottom=2500."""
        top, bottom = self.parser._extract_depth_range("from (2500') to (2400')")
        assert top == 2400.0
        assert bottom == 2500.0

    def test_extract_depth_range_no_match(self):
        """Text without from/to returns (None, None)."""
        top, bottom = self.parser._extract_depth_range("set plug at 7050 ft")
        assert top is None
        assert bottom is None

    # --- full DWR description → all fields populated ---

    def test_detect_events_full_dwr_description(self):
        """Full DWR plug description extracts all structured fields."""
        text = "Plug (# 1) Spot cement plug (20 sxs) class (H) From (7020') to (6777')"
        events = self.parser._detect_events_from_text(text)
        assert len(events) >= 1
        ev = events[0]
        assert ev.sacks == 20.0
        assert ev.cement_class == "H"
        assert ev.depth_top_ft == 6777.0
        assert ev.depth_bottom_ft == 7020.0

    def test_detect_events_squeeze_description(self):
        """Squeeze DWR description extracts all fields."""
        text = "Plug (# 6) Squeezed (45sxs) class (C) cement from (2500') to (2400')"
        events = self.parser._detect_events_from_text(text)
        assert len(events) >= 1
        ev = events[0]
        assert ev.sacks == 45.0
        assert ev.cement_class == "C"
        assert ev.depth_top_ft == 2400.0
        assert ev.depth_bottom_ft == 2500.0

    def test_cement_from_keyword_matches(self):
        """'cement from' keyword detects events missed by 'spot cement'."""
        text = "Plug (# 1) Spot (20 sxs) class (H) cement From (7020') to (6777')"
        events = self.parser._detect_events_from_text(text)
        assert len(events) >= 1
        assert events[0].event_type == "set_cement_plug"

    def test_squeeze_priority_over_cement_from(self):
        """Squeeze keyword takes priority even though 'cement from' also matches."""
        text = "Plug (# 6) Squeezed (45sxs) class (C) cement from (2500') to (2400')"
        events = self.parser._detect_events_from_text(text)
        assert len(events) >= 1
        assert events[0].event_type == "squeeze"

    def test_extract_depth_range_unicode_smart_quotes(self):
        """PDF-extracted text uses U+2019 RIGHT SINGLE QUOTATION MARK for foot marks."""
        # This is the ACTUAL text from PDF extraction — note the \u2019 characters
        text = "Plug (# 1) Spot (20 sxs) class (H) cement From (7020\u2019) to (6777\u2019)"
        top, bottom = self.parser._extract_depth_range(text)
        assert top == 6777.0
        assert bottom == 7020.0

    def test_detect_events_unicode_smart_quotes_full(self):
        """Full event detection works with Unicode smart quotes from PDF extraction."""
        text = "Plug (# 1) Spot (20 sxs) class (H) cement From (7020\u2019) to (6777\u2019)"
        events = self.parser._detect_events_from_text(text)
        assert len(events) >= 1
        ev = events[0]
        assert ev.depth_top_ft == 6777.0
        assert ev.depth_bottom_ft == 7020.0
        assert ev.sacks == 20.0
        assert ev.cement_class == "H"


# ===========================================================================
# W3 Reconciliation Adapter — Enrichment Fixes (to surface, from/to numeric)
# ===========================================================================

class TestAdapterEnrichmentToSurface:
    """Test the 'to surface' pattern in _enrich_event_from_description."""

    def test_to_surface_pattern(self):
        """'from (575') to surface' → top=0, bottom=575."""
        from apps.public_core.services.w3_reconciliation_adapter import _enrich_event_from_description

        event = {
            "description": "Plug (# 8) circulated (230 sxs) class (C) cement from (575') to surface",
            "depth_top_ft": None,
            "depth_bottom_ft": None,
            "sacks": None,
            "cement_class": None,
            "plug_number": None,
            "tagged_depth_ft": None,
        }
        _enrich_event_from_description(event)
        assert event["depth_top_ft"] == 0
        assert event["depth_bottom_ft"] == 575
        assert event["sacks"] == 230
        assert event["cement_class"] == "C"
        assert event["plug_number"] == 8

    def test_numeric_from_to_still_works(self):
        """Normal from/to numeric pattern still works after to-surface addition."""
        from apps.public_core.services.w3_reconciliation_adapter import _enrich_event_from_description

        event = {
            "description": "Plug (# 3) Spot (20 sxs) class (C) cement From (6016') to (5716')",
            "depth_top_ft": None,
            "depth_bottom_ft": None,
            "sacks": None,
            "cement_class": None,
            "plug_number": None,
            "tagged_depth_ft": None,
        }
        _enrich_event_from_description(event)
        assert event["depth_top_ft"] == 5716
        assert event["depth_bottom_ft"] == 6016
        assert event["sacks"] == 20
        assert event["cement_class"] == "C"
        assert event["plug_number"] == 3


# ===========================================================================
# Plug Reconciliation — Matching
# ===========================================================================

class TestPlugMatching:
    """Test planned-to-actual plug matching and deviation levels."""

    def setup_method(self):
        from apps.public_core.services.plug_reconciliation import PlugReconciliationEngine
        self.engine = PlugReconciliationEngine()

    def test_exact_depth_match(self):
        """Planned at 7000-6900, actual at 7000-6900 → MATCH."""
        from apps.public_core.services.plug_reconciliation import DeviationLevel

        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0, sacks_required=50.0)]
        actual = [_make_actual(depth_top_ft=7000.0, depth_bottom_ft=6900.0, sacks=50.0)]
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        assert len(result.comparisons) == 1
        assert result.comparisons[0].deviation_level == DeviationLevel.MATCH

    def test_within_tolerance_match(self):
        """Planned 7000-6900, actual 7010-6910 (10 ft off) → MATCH (tolerance ±20)."""
        from apps.public_core.services.plug_reconciliation import DeviationLevel

        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0)]
        actual = [_make_actual(depth_top_ft=7010.0, depth_bottom_ft=6910.0)]
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        assert result.comparisons[0].deviation_level == DeviationLevel.MATCH

    def test_minor_depth_deviation(self):
        """Planned midpoint 6950, actual midpoint 7010 (60 ft off) → MINOR."""
        from apps.public_core.services.plug_reconciliation import DeviationLevel

        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0)]  # mid=6950
        # actual mid = 7010+6960/2 = 6985 — need 21–100 ft deviation
        # Use one-sided: planned mid=6950, actual mid=7020 → deviation=70 ft → MINOR
        actual = [_make_actual(depth_top_ft=7070.0, depth_bottom_ft=6970.0)]  # mid=7020
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        assert result.comparisons[0].deviation_level == DeviationLevel.MINOR

    def test_major_depth_deviation(self):
        """Planned midpoint 6950, actual midpoint 7200 (250 ft off) → MAJOR."""
        from apps.public_core.services.plug_reconciliation import DeviationLevel

        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0)]  # mid=6950
        actual = [_make_actual(depth_top_ft=7300.0, depth_bottom_ft=7100.0)]  # mid=7200
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        assert result.comparisons[0].deviation_level == DeviationLevel.MAJOR

    def test_cement_class_mismatch_is_major(self):
        """Planned Class C, actual Class H, same depth → MAJOR."""
        from apps.public_core.services.plug_reconciliation import DeviationLevel

        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0, cement_class="C")]
        actual = [_make_actual(depth_top_ft=7000.0, depth_bottom_ft=6900.0, cement_class="H")]
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        assert result.comparisons[0].deviation_level == DeviationLevel.MAJOR

    def test_sack_count_within_tolerance(self):
        """Planned 50, actual 53 (6%) → MATCH (tolerance 10%)."""
        from apps.public_core.services.plug_reconciliation import DeviationLevel

        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0, sacks_required=50.0, cement_class="")]
        actual = [_make_actual(depth_top_ft=7000.0, depth_bottom_ft=6900.0, sacks=53.0, cement_class="")]
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        assert result.comparisons[0].deviation_level == DeviationLevel.MATCH

    def test_sack_count_major_deviation(self):
        """Planned 50, actual 80 (60%) → MAJOR (>3x tolerance)."""
        from apps.public_core.services.plug_reconciliation import DeviationLevel

        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0, sacks_required=50.0, cement_class="")]
        actual = [_make_actual(depth_top_ft=7000.0, depth_bottom_ft=6900.0, sacks=80.0, cement_class="")]
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        assert result.comparisons[0].deviation_level == DeviationLevel.MAJOR

    def test_missing_plug(self):
        """Planned plug not found in actuals → MISSING."""
        from apps.public_core.services.plug_reconciliation import DeviationLevel

        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0)]
        result = self.engine.reconcile(planned, [], api_number="30-025-12345")
        assert result.comparisons[0].deviation_level == DeviationLevel.MISSING

    def test_added_plug(self):
        """Actual plug not in plan → ADDED."""
        from apps.public_core.services.plug_reconciliation import DeviationLevel

        actual = [_make_actual(depth_top_ft=5000.0, depth_bottom_ft=4900.0)]
        result = self.engine.reconcile([], actual, api_number="30-025-12345")
        assert result.comparisons[0].deviation_level == DeviationLevel.ADDED


# ===========================================================================
# Plug Reconciliation — Summary and Narrative
# ===========================================================================

class TestReconciliationResult:
    """Test ReconciliationResult summary counts and narrative."""

    def setup_method(self):
        from apps.public_core.services.plug_reconciliation import PlugReconciliationEngine
        self.engine = PlugReconciliationEngine()

    def test_summary_counts_all_match(self):
        """Two perfect matches → matches=2, all deviations 0."""
        planned = [
            _make_planned(plug_number=1, top_ft=7000.0, bottom_ft=6900.0, cement_class=""),
            _make_planned(plug_number=2, top_ft=5000.0, bottom_ft=4900.0, cement_class=""),
        ]
        actual = [
            _make_actual(depth_top_ft=7000.0, depth_bottom_ft=6900.0, cement_class=""),
            _make_actual(depth_top_ft=5000.0, depth_bottom_ft=4900.0, cement_class=""),
        ]
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        assert result.matches == 2
        assert result.minor_deviations == 0
        assert result.major_deviations == 0
        assert result.added_plugs == 0
        assert result.missing_plugs == 0

    def test_overall_status_compliant(self):
        """All matches → overall_status = 'compliant'."""
        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0, cement_class="")]
        actual = [_make_actual(depth_top_ft=7000.0, depth_bottom_ft=6900.0, cement_class="")]
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        assert result.overall_status == "compliant"

    def test_overall_status_major_deviations(self):
        """Any major deviation → overall_status = 'major_deviations'."""
        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0, cement_class="C")]
        actual = [_make_actual(depth_top_ft=7000.0, depth_bottom_ft=6900.0, cement_class="H")]
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        assert result.overall_status == "major_deviations"

    def test_overall_status_missing_is_major(self):
        """Missing plugs also → overall_status = 'major_deviations'."""
        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0)]
        result = self.engine.reconcile(planned, [], api_number="30-025-12345")
        assert result.overall_status == "major_deviations"

    def test_overall_status_minor_deviations(self):
        """Minor deviation only → overall_status = 'minor_deviations'."""
        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0, sacks_required=50.0, cement_class="")]
        # Sack deviation ~30% → between sack_tolerance and 3x sack_tolerance → MINOR
        actual = [_make_actual(depth_top_ft=7000.0, depth_bottom_ft=6900.0, sacks=65.0, cement_class="")]
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        assert result.overall_status == "minor_deviations"

    def test_summary_narrative_generated(self):
        """summary_narrative is a non-empty string."""
        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0, cement_class="")]
        actual = [_make_actual(depth_top_ft=7000.0, depth_bottom_ft=6900.0, cement_class="")]
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        assert isinstance(result.summary_narrative, str)
        assert len(result.summary_narrative) > 0

    def test_summary_narrative_mentions_api(self):
        """Narrative includes the API number."""
        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0, cement_class="")]
        actual = [_make_actual(depth_top_ft=7000.0, depth_bottom_ft=6900.0, cement_class="")]
        result = self.engine.reconcile(planned, actual, api_number="30-025-99999")
        assert "30-025-99999" in result.summary_narrative

    def test_summary_narrative_action_required_on_major(self):
        """Narrative mentions 'Action required' when there are major deviations."""
        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0, cement_class="C")]
        actual = [_make_actual(depth_top_ft=7000.0, depth_bottom_ft=6900.0, cement_class="H")]
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        assert "Action required" in result.summary_narrative or "major" in result.summary_narrative.lower()


# ===========================================================================
# Plug Reconciliation — Edge Cases
# ===========================================================================

class TestReconciliationEdgeCases:
    """Test edge cases in reconciliation."""

    def setup_method(self):
        from apps.public_core.services.plug_reconciliation import PlugReconciliationEngine
        self.engine = PlugReconciliationEngine()

    def test_empty_planned_all_added(self):
        """No planned plugs — all actuals marked ADDED."""
        from apps.public_core.services.plug_reconciliation import DeviationLevel

        actual = [
            _make_actual(depth_top_ft=7000.0, depth_bottom_ft=6900.0),
            _make_actual(depth_top_ft=5000.0, depth_bottom_ft=4900.0),
        ]
        result = self.engine.reconcile([], actual, api_number="30-025-12345")
        assert result.added_plugs == 2
        assert result.missing_plugs == 0
        assert all(c.deviation_level == DeviationLevel.ADDED for c in result.comparisons)

    def test_empty_actual_all_missing(self):
        """No actual events — all planned marked MISSING."""
        from apps.public_core.services.plug_reconciliation import DeviationLevel

        planned = [
            _make_planned(plug_number=1, top_ft=7000.0, bottom_ft=6900.0),
            _make_planned(plug_number=2, top_ft=5000.0, bottom_ft=4900.0),
        ]
        result = self.engine.reconcile(planned, [], api_number="30-025-12345")
        assert result.missing_plugs == 2
        assert result.added_plugs == 0
        assert all(c.deviation_level == DeviationLevel.MISSING for c in result.comparisons)

    def test_single_plug_perfect_match(self):
        """Single planned, single actual, perfect match → matches=1."""
        from apps.public_core.services.plug_reconciliation import DeviationLevel

        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0, cement_class="")]
        actual = [_make_actual(depth_top_ft=7000.0, depth_bottom_ft=6900.0, cement_class="")]
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        assert result.matches == 1
        assert result.comparisons[0].deviation_level == DeviationLevel.MATCH

    def test_both_empty_no_comparisons(self):
        """No planned, no actual → empty comparisons, compliant status."""
        result = self.engine.reconcile([], [], api_number="30-025-12345")
        assert result.comparisons == []
        assert result.overall_status == "compliant"

    def test_non_plug_actual_events_filtered(self):
        """Actual events that are not plug-placement types are excluded from matching."""
        from apps.public_core.services.plug_reconciliation import DeviationLevel

        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0)]
        actual = [
            # These should be filtered out — not in PLUG_PLACEMENT_EVENT_TYPES
            {"event_type": "woc", "depth_top_ft": 7000.0, "depth_bottom_ft": 6900.0,
             "sacks": None, "cement_class": None, "tagged_depth_ft": None},
            {"event_type": "pressure_test", "depth_top_ft": 7000.0, "depth_bottom_ft": 6900.0,
             "sacks": None, "cement_class": None, "tagged_depth_ft": None},
        ]
        result = self.engine.reconcile(planned, actual, api_number="30-025-12345")
        # Both WOC and pressure_test are filtered, planned plug → MISSING
        assert result.missing_plugs == 1

    def test_custom_depth_tolerance(self):
        """Custom depth_tolerance_ft applied correctly."""
        from apps.public_core.services.plug_reconciliation import (
            PlugReconciliationEngine, DeviationLevel,
        )

        engine = PlugReconciliationEngine(depth_tolerance_ft=5.0)
        planned = [_make_planned(top_ft=7000.0, bottom_ft=6900.0, cement_class="")]
        # Deviation 10 ft — within default (20) but not custom (5)
        actual = [_make_actual(depth_top_ft=7010.0, depth_bottom_ft=6910.0, cement_class="")]
        result = engine.reconcile(planned, actual, api_number="30-025-12345")
        assert result.comparisons[0].deviation_level != DeviationLevel.MATCH

    def test_midpoint_helper_both_present(self):
        """_midpoint returns average when both top and bottom are given."""
        from apps.public_core.services.plug_reconciliation import _midpoint

        assert _midpoint(7000.0, 6900.0) == 6950.0

    def test_midpoint_helper_only_top(self):
        """_midpoint returns top when bottom is None."""
        from apps.public_core.services.plug_reconciliation import _midpoint

        assert _midpoint(7000.0, None) == 7000.0

    def test_midpoint_helper_both_none(self):
        """_midpoint returns None when both are None."""
        from apps.public_core.services.plug_reconciliation import _midpoint

        assert _midpoint(None, None) is None

    def test_escalate_helper(self):
        """_escalate returns the more severe of two deviation levels."""
        from apps.public_core.services.plug_reconciliation import (
            _escalate, DeviationLevel,
        )

        assert _escalate(DeviationLevel.MATCH, DeviationLevel.MINOR) == DeviationLevel.MINOR
        assert _escalate(DeviationLevel.MINOR, DeviationLevel.MAJOR) == DeviationLevel.MAJOR
        assert _escalate(DeviationLevel.MAJOR, DeviationLevel.MATCH) == DeviationLevel.MAJOR


# ===========================================================================
# Subsequent Report Generator — Individual unit tests (no DB)
# ===========================================================================

class TestSubsequentReportGeneratorUnit:
    """Unit tests for SubsequentReportGenerator methods that don't need the DB."""

    def _make_dwr_result(self, num_days=1, plug_events=True):
        """Build a minimal DWRParseResult for testing."""
        from apps.public_core.services.dwr_parser import (
            DWRParseResult, DWRDay, DWREvent,
        )

        days = []
        for i in range(num_days):
            events = []
            if plug_events:
                events.append(DWREvent(
                    event_type="set_cement_plug",
                    description=f"Plug day {i + 1}",
                    depth_top_ft=7000.0 - i * 1000,
                    depth_bottom_ft=6900.0 - i * 1000,
                    sacks=50.0,
                    cement_class="H",
                ))
            day = DWRDay(
                work_date=date(2024, 6, 1 + i),
                day_number=i + 1,
                events=events,
                daily_narrative=f"Day {i + 1} operations complete.",
            )
            days.append(day)

        result = DWRParseResult(
            api_number="30-025-12345",
            well_name="Test Well 1",
            operator="Test Operator",
            days=days,
            total_days=num_days,
            parse_method="jmr_structured",
            confidence=0.95,
        )
        return result

    def test_build_daily_summaries(self):
        """_build_daily_summaries produces one entry per day."""
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        dwr = self._make_dwr_result(num_days=3)
        summaries = gen._build_daily_summaries(dwr)
        assert len(summaries) == 3
        assert summaries[0]["day_number"] == 1
        assert summaries[0]["work_date"] == "2024-06-01"

    def test_build_daily_summaries_includes_events(self):
        """Each summary includes events list with correct structure."""
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        dwr = self._make_dwr_result(num_days=1, plug_events=True)
        summaries = gen._build_daily_summaries(dwr)
        events = summaries[0]["events"]
        assert len(events) == 1
        assert events[0]["event_type"] == "set_cement_plug"
        assert events[0]["depth_top_ft"] == 7000.0

    def test_extract_actual_plugs_filters_non_placement(self):
        """Only plug-placement events appear in actual_plugs."""
        from apps.public_core.services.dwr_parser import DWRParseResult, DWRDay, DWREvent
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        day = DWRDay(
            work_date=date(2024, 6, 1),
            day_number=1,
            events=[
                DWREvent(event_type="set_cement_plug", description="Plug", depth_top_ft=7000.0),
                DWREvent(event_type="woc", description="WOC"),
                DWREvent(event_type="pressure_test", description="Pressure test"),
            ],
        )
        dwr = DWRParseResult(api_number="30-025-12345", days=[day], total_days=1)
        plugs = gen._extract_actual_plugs(dwr)
        assert len(plugs) == 1
        assert plugs[0]["type"] == "set_cement_plug"

    def test_extract_actual_plugs_assigns_plug_numbers(self):
        """Sequential plug numbers assigned when not present on event."""
        from apps.public_core.services.dwr_parser import DWRParseResult, DWRDay, DWREvent
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        day = DWRDay(
            work_date=date(2024, 6, 1),
            day_number=1,
            events=[
                DWREvent(event_type="set_cement_plug", description="P1", depth_top_ft=7000.0),
                DWREvent(event_type="set_bridge_plug", description="P2", depth_top_ft=5000.0),
            ],
        )
        dwr = DWRParseResult(api_number="30-025-12345", days=[day], total_days=1)
        plugs = gen._extract_actual_plugs(dwr)
        assert len(plugs) == 2
        plug_numbers = {p["plug_number"] for p in plugs}
        assert plug_numbers == {1, 2}

    def test_generate_operations_narrative_empty(self):
        """Empty summaries → empty narrative string."""
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        assert gen._generate_operations_narrative([]) == ""

    def test_generate_operations_narrative_with_data(self):
        """Narrative includes day header and narrative text."""
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        summaries = [
            {
                "day_number": 1,
                "work_date": "2024-06-01",
                "narrative": "Spotted plug at 7050 ft using 45 sacks Class H.",
                "events": [],
            }
        ]
        narrative = gen._generate_operations_narrative(summaries)
        assert "Day 1" in narrative
        assert "2024-06-01" in narrative
        assert "Spotted plug" in narrative

    def test_generate_from_dwrs_sets_dates(self):
        """generate_from_dwrs correctly sets start_date and end_date."""
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        dwr = self._make_dwr_result(num_days=3)

        # Mock noi_form
        noi_form = MagicMock()
        noi_form.id = 1
        noi_form.api_number = "30-025-12345"
        noi_form.plugs.all.return_value = []

        report = gen.generate_from_dwrs(noi_form, dwr)
        assert report.start_date == date(2024, 6, 1)
        assert report.end_date == date(2024, 6, 3)
        assert report.total_days == 3

    def test_generate_from_dwrs_operations_narrative_populated(self):
        """generate_from_dwrs populates operations_narrative."""
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        dwr = self._make_dwr_result(num_days=1)

        noi_form = MagicMock()
        noi_form.id = 1
        noi_form.api_number = "30-025-12345"
        noi_form.plugs.all.return_value = []

        report = gen.generate_from_dwrs(noi_form, dwr)
        assert isinstance(report.operations_narrative, str)
        assert len(report.operations_narrative) > 0


# ===========================================================================
# Subsequent Report Generator — ORM (requires DB)
# ===========================================================================

@pytest.mark.django_db
class TestSubsequentReportGeneratorORM:
    """Test ORM persistence via create_subsequent_form."""

    @pytest.fixture
    def nm_well(self, db):
        """WellRegistry with a NM API number."""
        from apps.public_core.models import WellRegistry

        return WellRegistry.objects.create(
            api14="30-025-12345-0000",
            state="NM",
            county="Chaves",
            operator_name="Test Operator NM",
            field_name="Permian Test Field",
        )

    @pytest.fixture
    def noi_form(self, db, nm_well):
        """C103FormORM NOI with one plug."""
        from apps.public_core.models.c103_orm import C103FormORM, C103PlugORM

        form = C103FormORM.objects.create(
            well=nm_well,
            api_number="30-025-12345",
            form_type="noi",
            status="draft",
            region="Southeast",
        )
        C103PlugORM.objects.create(
            c103_form=form,
            plug_number=1,
            step_type="cement_plug",
            operation_type="spot",
            hole_type="cased",
            top_ft=7000.0,
            bottom_ft=6900.0,
            sacks_required=50.0,
            cement_class="H",
        )
        return form

    def _make_report_data(self, noi_form):
        """Build a SubsequentReportData for an existing NOI form."""
        from apps.public_core.services.dwr_parser import (
            DWRParseResult, DWRDay, DWREvent,
        )
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        ev = DWREvent(
            event_type="set_cement_plug",
            description="Spot cement plug 7050-6950",
            depth_top_ft=7050.0,
            depth_bottom_ft=6950.0,
            sacks=48.0,
            cement_class="H",
        )
        day = DWRDay(
            work_date=date(2024, 6, 1),
            day_number=1,
            events=[ev],
            daily_narrative="Spotted cement plug at 7050 ft.",
        )
        dwr = DWRParseResult(
            api_number="30-025-12345",
            days=[day],
            total_days=1,
        )
        gen = SubsequentReportGenerator()
        return gen.generate_from_dwrs(noi_form, dwr)

    def test_create_subsequent_form_type(self, db, noi_form):
        """create_subsequent_form creates a form_type='subsequent' record."""
        from apps.public_core.models.c103_orm import C103FormORM
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        report_data = self._make_report_data(noi_form)
        subsequent = gen.create_subsequent_form(noi_form, report_data)

        assert subsequent.form_type == "subsequent"
        assert subsequent.status == "draft"
        assert C103FormORM.objects.filter(id=subsequent.id, form_type="subsequent").exists()

    def test_create_subsequent_form_api_number(self, db, noi_form):
        """Subsequent form inherits API number from the NOI."""
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        report_data = self._make_report_data(noi_form)
        subsequent = gen.create_subsequent_form(noi_form, report_data)

        assert subsequent.api_number == "30-025-12345"

    def test_daily_work_records_created(self, db, noi_form):
        """DWR days produce DailyWorkRecord ORM instances."""
        from apps.public_core.models.c103_orm import DailyWorkRecord
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        report_data = self._make_report_data(noi_form)
        subsequent = gen.create_subsequent_form(noi_form, report_data)

        records = DailyWorkRecord.objects.filter(c103_form=subsequent)
        assert records.count() == 1
        assert records.first().work_date == date(2024, 6, 1)
        assert records.first().day_number == 1

    def test_events_created(self, db, noi_form):
        """DWR events produce C103EventORM instances linked to the subsequent form."""
        from apps.public_core.models.c103_orm import C103EventORM
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        report_data = self._make_report_data(noi_form)
        subsequent = gen.create_subsequent_form(noi_form, report_data)

        events = C103EventORM.objects.filter(c103_form=subsequent)
        assert events.count() == 1
        ev = events.first()
        assert ev.event_type == "set_cement_plug"
        assert ev.depth_top_ft == 7050.0
        assert ev.cement_class == "H"
        assert ev.sacks == 48.0

    def test_events_linked_to_daily_record(self, db, noi_form):
        """C103EventORM instances are linked to their DailyWorkRecord."""
        from apps.public_core.models.c103_orm import DailyWorkRecord, C103EventORM
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        report_data = self._make_report_data(noi_form)
        subsequent = gen.create_subsequent_form(noi_form, report_data)

        dwr_record = DailyWorkRecord.objects.get(c103_form=subsequent)
        assert dwr_record.events.count() == 1

    def test_reconciliation_stored_in_plan_data(self, db, noi_form):
        """Reconciliation results stored in plan_data JSON field."""
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        report_data = self._make_report_data(noi_form)
        subsequent = gen.create_subsequent_form(noi_form, report_data)

        plan_data = subsequent.plan_data
        assert "reconciliation" in plan_data
        recon = plan_data["reconciliation"]
        assert "overall_status" in recon
        assert "comparisons" in recon
        assert isinstance(recon["comparisons"], list)

    def test_noi_form_id_stored_in_plan_data(self, db, noi_form):
        """noi_form_id is stored in plan_data for traceability."""
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        report_data = self._make_report_data(noi_form)
        subsequent = gen.create_subsequent_form(noi_form, report_data)

        assert subsequent.plan_data["noi_form_id"] == noi_form.id

    def test_operations_narrative_populated(self, db, noi_form):
        """proposed_work_narrative is populated with operations narrative."""
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        report_data = self._make_report_data(noi_form)
        subsequent = gen.create_subsequent_form(noi_form, report_data)

        assert subsequent.proposed_work_narrative
        assert "Day 1" in subsequent.proposed_work_narrative

    def test_actual_plugs_stored_in_plan_data(self, db, noi_form):
        """actual_plugs list stored in plan_data."""
        from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator

        gen = SubsequentReportGenerator()
        report_data = self._make_report_data(noi_form)
        subsequent = gen.create_subsequent_form(noi_form, report_data)

        actual_plugs = subsequent.plan_data.get("actual_plugs", [])
        assert len(actual_plugs) == 1
        assert actual_plugs[0]["type"] == "set_cement_plug"
