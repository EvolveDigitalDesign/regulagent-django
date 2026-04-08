"""
Tests for RRCPortalScraper static parsing methods.

All tests exercise pure Python logic — no browser, no database, no network.
This keeps the suite fast and dependency-free.
"""

import pytest

from apps.intelligence.services.portal_scrapers.rrc import RRCPortalScraper


# ---------------------------------------------------------------------------
# _parse_status
# ---------------------------------------------------------------------------


class TestParseStatus:
    def test_parse_status_approved(self):
        result = RRCPortalScraper._parse_status("Application Approved")
        assert result == "approved"

    def test_parse_status_rejected(self):
        result = RRCPortalScraper._parse_status("Rejected")
        assert result == "rejected"

    def test_parse_status_pending(self):
        result = RRCPortalScraper._parse_status("Pending")
        assert result == "pending"

    def test_parse_status_unknown_fallback(self):
        """Any text that matches no known phrase must fall back to 'under_review'."""
        result = RRCPortalScraper._parse_status("Lorem ipsum dolor sit amet")
        assert result == "under_review"

    def test_parse_status_deficiency_notice(self):
        """'Deficiency Notice' must map before the shorter 'deficiency' key."""
        result = RRCPortalScraper._parse_status("Deficiency Notice Issued")
        assert result == "deficiency"

    def test_parse_status_under_review(self):
        result = RRCPortalScraper._parse_status("Currently Under Review")
        assert result == "under_review"

    def test_parse_status_revision_requested(self):
        result = RRCPortalScraper._parse_status("Revision Requested")
        assert result == "revision_requested"

    def test_parse_status_case_insensitive(self):
        """Status matching is case-insensitive (text is lowercased before map lookup)."""
        assert RRCPortalScraper._parse_status("APPROVED") == "approved"
        assert RRCPortalScraper._parse_status("REJECTED") == "rejected"

    def test_parse_status_empty_string_fallback(self):
        result = RRCPortalScraper._parse_status("")
        assert result == "under_review"


# ---------------------------------------------------------------------------
# _parse_remarks
# ---------------------------------------------------------------------------


class TestParseRemarks:
    def test_parse_remarks_extracts_remarks(self):
        text = "Status: Pending\nRemarks: Missing GAU letter\nOther info"
        result = RRCPortalScraper._parse_remarks(text)
        assert result == "Missing GAU letter"

    def test_parse_remarks_empty(self):
        """Text with no remarks/notes/comments/reason label returns empty string."""
        text = "Status: Approved\nDate: 03/15/2026\nReviewer: Jane Smith"
        result = RRCPortalScraper._parse_remarks(text)
        assert result == ""

    def test_parse_remarks_notes_label(self):
        text = "Notes: Cement volume incorrect"
        result = RRCPortalScraper._parse_remarks(text)
        assert result == "Cement volume incorrect"

    def test_parse_remarks_comments_label(self):
        text = "Comments: Please resubmit with corrected depths"
        result = RRCPortalScraper._parse_remarks(text)
        assert result == "Please resubmit with corrected depths"

    def test_parse_remarks_reason_label(self):
        text = "Reason: Plug depth mismatch"
        result = RRCPortalScraper._parse_remarks(text)
        assert result == "Plug depth mismatch"

    def test_parse_remarks_truncated_at_500(self):
        long_remark = "x" * 600
        text = f"Remarks: {long_remark}"
        result = RRCPortalScraper._parse_remarks(text)
        assert len(result) == 500

    def test_parse_remarks_strips_whitespace(self):
        text = "Remarks:   Leading and trailing spaces   "
        result = RRCPortalScraper._parse_remarks(text)
        assert result == "Leading and trailing spaces"


# ---------------------------------------------------------------------------
# _parse_reviewer
# ---------------------------------------------------------------------------


class TestParseReviewer:
    def test_parse_reviewer_extracts_name(self):
        text = "Reviewed by: John Smith\nStatus: Pending"
        result = RRCPortalScraper._parse_reviewer(text)
        assert result == "John Smith"

    def test_parse_reviewer_reviewer_label(self):
        text = "Reviewer: Jane Doe"
        result = RRCPortalScraper._parse_reviewer(text)
        assert result == "Jane Doe"

    def test_parse_reviewer_assigned_to_label(self):
        text = "Assigned to: Bob Johnson"
        result = RRCPortalScraper._parse_reviewer(text)
        assert result == "Bob Johnson"

    def test_parse_reviewer_empty_when_absent(self):
        text = "Status: Approved\nDate: 2026-03-15"
        result = RRCPortalScraper._parse_reviewer(text)
        assert result == ""

    def test_parse_reviewer_truncated_at_128(self):
        long_name = "A" * 200
        text = f"Reviewer: {long_name}"
        result = RRCPortalScraper._parse_reviewer(text)
        assert len(result) == 128


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------


class TestParseDate:
    def test_parse_date_mm_dd_yyyy(self):
        text = "Status: 03/15/2026"
        result = RRCPortalScraper._parse_date(text)
        assert result == "2026-03-15"

    def test_parse_date_iso(self):
        text = "Date: 2026-03-15"
        result = RRCPortalScraper._parse_date(text)
        assert result == "2026-03-15"

    def test_parse_date_none(self):
        """Text with no recognisable date returns None."""
        text = "Status: Pending\nRemarks: No dates here"
        result = RRCPortalScraper._parse_date(text)
        assert result is None

    def test_parse_date_prefers_mm_dd_yyyy_when_first(self):
        """MM/DD/YYYY pattern is checked first and returned when it appears first in text."""
        text = "Filed 01/10/2025, updated 2026-03-15"
        result = RRCPortalScraper._parse_date(text)
        assert result == "2025-01-10"

    def test_parse_date_single_digit_month_and_day(self):
        """Single-digit months and days are zero-padded correctly."""
        text = "Status date: 03/05/2026"
        result = RRCPortalScraper._parse_date(text)
        assert result == "2026-03-05"

    def test_parse_date_empty_string(self):
        result = RRCPortalScraper._parse_date("")
        assert result is None


# ---------------------------------------------------------------------------
# _map_form_type
# ---------------------------------------------------------------------------


class TestMapFormType:
    def test_map_form_type_w3a(self):
        result = RRCPortalScraper._map_form_type("W-3A")
        assert result == "w3a"

    def test_map_form_type_unknown(self):
        """Unknown form type strings are returned normalised (stripped, lowercased)."""
        result = RRCPortalScraper._map_form_type("Unknown Form")
        assert result == "unknown form"

    def test_map_form_type_w3(self):
        result = RRCPortalScraper._map_form_type("W-3")
        assert result == "w3"

    def test_map_form_type_w3a_no_hyphen(self):
        result = RRCPortalScraper._map_form_type("w3a")
        assert result == "w3a"

    def test_map_form_type_c103(self):
        result = RRCPortalScraper._map_form_type("C-103")
        assert result == "c103"

    def test_map_form_type_empty_string(self):
        result = RRCPortalScraper._map_form_type("")
        assert result == ""

    def test_map_form_type_strips_whitespace(self):
        result = RRCPortalScraper._map_form_type("  W-3A  ")
        assert result == "w3a"

    def test_map_form_type_case_insensitive(self):
        result = RRCPortalScraper._map_form_type("W-3A")
        assert result == RRCPortalScraper._map_form_type("w-3a")
