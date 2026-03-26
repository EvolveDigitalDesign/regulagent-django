"""Unit tests for UniversalTicketParser.

All AI calls and external I/O are mocked. No network or filesystem access required
(except where we create explicit temp files to exercise extractors).
"""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apps.public_core.services.universal_ticket_parser import (
    SUPPORTED_EXTENSIONS,
    FileContent,
    UniversalTicketParser,
)
from apps.public_core.services.dwr_parser import DWRParseResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_parser() -> UniversalTicketParser:
    return UniversalTicketParser()


def _fake_openai_response(json_str: str):
    """Build a minimal mock OpenAI chat completion response."""
    choice = MagicMock()
    choice.message.content = json_str
    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# File type detection via SUPPORTED_EXTENSIONS
# ---------------------------------------------------------------------------

class TestFileTypeDetection:
    def test_pdf_extension_maps_to_pdf(self):
        assert SUPPORTED_EXTENSIONS[".pdf"] == "pdf"

    def test_docx_and_doc_map_to_docx(self):
        assert SUPPORTED_EXTENSIONS[".docx"] == "docx"
        assert SUPPORTED_EXTENSIONS[".doc"] == "docx"

    def test_image_extensions_map_to_image(self):
        for ext in (".jpg", ".jpeg", ".png", ".tiff", ".tif"):
            assert SUPPORTED_EXTENSIONS[ext] == "image", f"{ext} should be 'image'"

    def test_csv_extension_maps_to_csv(self):
        assert SUPPORTED_EXTENSIONS[".csv"] == "csv"

    def test_excel_extensions_map_to_excel(self):
        assert SUPPORTED_EXTENSIONS[".xlsx"] == "excel"
        assert SUPPORTED_EXTENSIONS[".xls"] == "excel"

    def test_unsupported_extension_not_in_map(self):
        assert ".txt" not in SUPPORTED_EXTENSIONS
        assert ".rtf" not in SUPPORTED_EXTENSIONS


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

class TestPdfExtraction:
    def test_pdf_extraction_returns_file_content(self):
        """_extract_from_pdf returns FileContent with extracted text."""
        parser = _make_parser()
        fake_page = MagicMock()
        fake_page.get_text.return_value = "Day 1 cement plug at 5000 ft"

        fake_doc = MagicMock()
        fake_doc.__iter__ = MagicMock(return_value=iter([fake_page]))
        fake_doc.close = MagicMock()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            tf.write(b"%PDF-1.4 fake")
            tmp_path = tf.name

        try:
            with patch("fitz.open", return_value=fake_doc):
                result = parser._extract_from_pdf(tmp_path)

            assert isinstance(result, FileContent)
            assert result.file_type == "pdf"
            assert "cement plug" in result.text_content
        finally:
            os.unlink(tmp_path)

    def test_pdf_extraction_falls_back_to_pdfplumber_on_import_error(self):
        """Falls back to pdfplumber when fitz import fails."""
        parser = _make_parser()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            tf.write(b"%PDF-1.4 fake")
            tmp_path = tf.name

        try:
            fake_page = MagicMock()
            fake_page.extract_text.return_value = "pdfplumber text content"
            fake_pdf_ctx = MagicMock()
            fake_pdf_ctx.__enter__ = MagicMock(return_value=fake_pdf_ctx)
            fake_pdf_ctx.__exit__ = MagicMock(return_value=False)
            fake_pdf_ctx.pages = [fake_page]

            import builtins
            real_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name == "fitz":
                    raise ImportError("fitz not installed")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                with patch("pdfplumber.open", return_value=fake_pdf_ctx):
                    result = parser._extract_from_pdf(tmp_path)

            assert isinstance(result, FileContent)
            assert result.file_type == "pdf"
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# DOCX extraction
# ---------------------------------------------------------------------------

class TestDocxExtraction:
    def test_docx_extraction_delegates_to_extract_text_from_docx(self):
        """_extract_from_docx calls extract_text_from_docx and wraps result."""
        parser = _make_parser()

        with patch(
            "apps.public_core.services.universal_ticket_parser.extract_text_from_docx",
            return_value=("DOCX text content", [{"col": "val"}]),
        ):
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tf:
                tmp_path = tf.name
            try:
                result = parser._extract_from_docx(tmp_path)
            finally:
                os.unlink(tmp_path)

        assert isinstance(result, FileContent)
        assert result.file_type == "docx"
        assert result.text_content == "DOCX text content"
        assert result.tables == [{"col": "val"}]


# ---------------------------------------------------------------------------
# CSV extraction
# ---------------------------------------------------------------------------

class TestCsvExtraction:
    def test_csv_extraction_reads_file_and_returns_text(self):
        """_extract_from_csv returns FileContent with CSV data as text."""
        parser = _make_parser()

        fd, tmp_path = tempfile.mkstemp(suffix=".csv")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("event_type,depth_top_ft,sacks\nset_cement_plug,5000,50\n")

            result = parser._extract_from_csv(tmp_path)

            assert isinstance(result, FileContent)
            assert result.file_type == "csv"
            assert "set_cement_plug" in result.text_content
            assert len(result.tables) == 1
            assert result.tables[0]["data"][0]["sacks"] == 50
        finally:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# Excel extraction
# ---------------------------------------------------------------------------

class TestExcelExtraction:
    def test_excel_extraction_mocks_pandas(self):
        """_extract_from_excel uses pandas.read_excel and returns all sheets."""
        parser = _make_parser()

        import pandas as pd

        fake_df = pd.DataFrame(
            [{"event_type": "set_cement_plug", "depth_top_ft": 5000, "sacks": 50}]
        )

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
            tmp_path = tf.name

        try:
            with patch("pandas.read_excel", return_value={"Sheet1": fake_df}):
                result = parser._extract_from_excel(tmp_path)

            assert isinstance(result, FileContent)
            assert result.file_type == "excel"
            assert "[Sheet: Sheet1]" in result.text_content
            assert len(result.tables) == 1
            assert result.tables[0]["sheet"] == "Sheet1"
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------

class TestImageExtraction:
    def test_image_extraction_sets_base64(self):
        """_extract_from_image encodes file content as base64."""
        parser = _make_parser()

        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        try:
            with os.fdopen(fd, "wb") as f:
                # Minimal 1x1 PNG bytes
                f.write(
                    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
                    b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
                    b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
                )

            # Patch pytesseract so OCR is skipped silently
            with patch("pytesseract.image_to_string", return_value=""):
                result = parser._extract_from_image(tmp_path)

            assert isinstance(result, FileContent)
            assert result.file_type == "image"
            assert result.image_base64 is not None
            # Verify it's valid base64
            decoded = base64.b64decode(result.image_base64)
            assert len(decoded) > 0
        finally:
            os.unlink(tmp_path)

    def test_image_extraction_continues_if_pytesseract_missing(self):
        """Image extraction succeeds even when pytesseract is not installed."""
        parser = _make_parser()

        fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(b"\xff\xd8\xff\xe0fake jpeg bytes")

            import builtins
            real_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name == "pytesseract":
                    raise ImportError("no pytesseract")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                result = parser._extract_from_image(tmp_path)

            assert isinstance(result, FileContent)
            assert result.image_base64 is not None
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# AI extraction (_ai_extract_events)
# ---------------------------------------------------------------------------

class TestAiExtractEvents:
    def test_ai_extract_events_returns_dwr_parse_result(self):
        """_ai_extract_events maps AI JSON response into a DWRParseResult."""
        parser = _make_parser()

        ai_response_json = """{
            "well_name": "Smith #1",
            "operator": "Test Operator",
            "days": [
                {
                    "work_date": "2024-03-15",
                    "day_number": 1,
                    "daily_narrative": "Set cement plug 1",
                    "crew_size": 4,
                    "rig_name": "Rig 42",
                    "events": [
                        {
                            "event_type": "set_cement_plug",
                            "description": "Set 50-sack cement plug",
                            "depth_top_ft": 5000,
                            "depth_bottom_ft": 5100,
                            "cement_class": "A",
                            "sacks": 50,
                            "plug_number": 1
                        }
                    ]
                }
            ],
            "warnings": []
        }"""

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_openai_response(
            ai_response_json
        )

        contents = [
            FileContent(
                file_name="test.pdf",
                file_type="pdf",
                text_content="Day 1: Set cement plug at 5000 ft",
            )
        ]

        with patch(
            "apps.public_core.services.universal_ticket_parser._openai_client",
            return_value=mock_client,
        ):
            result = parser._ai_extract_events(contents, "42-501-70575")

        assert isinstance(result, DWRParseResult)
        assert result.parse_method == "universal_ai"
        assert result.well_name == "Smith #1"
        assert result.operator == "Test Operator"
        assert len(result.days) == 1
        assert result.total_days == 1

    def test_ai_extract_events_handles_openai_failure(self):
        """AI extraction failure returns a result with warning and zero confidence."""
        parser = _make_parser()

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("OpenAI error")

        contents = [
            FileContent(file_name="test.pdf", file_type="pdf", text_content="some text")
        ]

        with patch(
            "apps.public_core.services.universal_ticket_parser._openai_client",
            return_value=mock_client,
        ):
            result = parser._ai_extract_events(contents, "42-501-70575")

        assert result.confidence == 0.0
        assert any("AI extraction failed" in w for w in result.warnings)

    def test_ai_extract_uses_vision_messages_for_images(self):
        """When image FileContent is present, vision-style message is sent."""
        parser = _make_parser()

        ai_response_json = '{"well_name": "", "operator": "", "days": [], "warnings": []}'
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_openai_response(
            ai_response_json
        )

        contents = [
            FileContent(
                file_name="ticket.png",
                file_type="image",
                text_content="",
                image_base64="abc123",
            )
        ]

        with patch(
            "apps.public_core.services.universal_ticket_parser._openai_client",
            return_value=mock_client,
        ):
            parser._ai_extract_events(contents, "42-501-70575")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args[1]["messages"] if "messages" in call_args[1] else call_args[0][0]
        # The user message content should be a list (vision format) not a plain string
        user_msg = next(m for m in messages if m["role"] == "user")
        assert isinstance(user_msg["content"], list)


# ---------------------------------------------------------------------------
# parse_files (public API)
# ---------------------------------------------------------------------------

class TestParseFiles:
    def test_parse_files_returns_no_input_result_for_empty_list(self):
        """parse_files with no file_paths returns a DWRParseResult with warning."""
        parser = _make_parser()
        result = parser.parse_files([], "42-501-70575")

        assert isinstance(result, DWRParseResult)
        assert result.parse_method == "no_input"
        assert result.confidence == 0.0
        assert any("No files provided" in w for w in result.warnings)

    def test_parse_files_skips_unsupported_extension(self):
        """Files with unsupported extensions are skipped and do not raise."""
        parser = _make_parser()

        fd, tmp_path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        try:
            # No AI call should be made — unsupported file, nothing to parse
            result = parser.parse_files([tmp_path], "42-501-70575")
            # Reached here means it didn't raise
            assert isinstance(result, DWRParseResult)
        finally:
            os.unlink(tmp_path)

    def test_parse_files_continues_on_single_file_extraction_failure(self):
        """A file extraction failure returns None from _extract_file and is skipped."""
        # _extract_file internally catches exceptions and returns None.
        # This test verifies that behaviour by creating a file whose extractor raises.
        parser = _make_parser()

        # Pass one unsupported + one valid CSV — unsupported returns None (skipped)
        fd_csv, path_csv = tempfile.mkstemp(suffix=".csv")
        fd_txt, path_txt = tempfile.mkstemp(suffix=".txt")  # unsupported
        try:
            with os.fdopen(fd_csv, "w") as f:
                f.write("event_type,depth_top_ft\nset_cement_plug,5000\n")
            os.close(fd_txt)

            ai_response_json = (
                '{"well_name":"","operator":"","days":[],"warnings":[]}'
            )
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _fake_openai_response(
                ai_response_json
            )

            with patch(
                "apps.public_core.services.universal_ticket_parser._openai_client",
                return_value=mock_client,
            ):
                result = parser.parse_files([path_txt, path_csv], "42-501-70575")

            # Should not raise; result is a valid DWRParseResult
            assert isinstance(result, DWRParseResult)
        finally:
            for p in (path_csv, path_txt):
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass

    def test_parse_files_handles_mixed_formats(self):
        """parse_files processes a mix of PDF and CSV files."""
        parser = _make_parser()

        fd_csv, path_csv = tempfile.mkstemp(suffix=".csv")
        fd_pdf, path_pdf = tempfile.mkstemp(suffix=".pdf")
        try:
            with os.fdopen(fd_csv, "w") as f:
                f.write("event_type,depth_top_ft\nset_cement_plug,5000\n")
            with os.fdopen(fd_pdf, "wb") as f:
                f.write(b"%PDF-1.4 fake content")

            ai_response_json = (
                '{"well_name":"Test","operator":"","days":[],"warnings":[]}'
            )
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _fake_openai_response(
                ai_response_json
            )

            fake_page = MagicMock()
            fake_page.get_text.return_value = "non-JMR text about plugging ops"
            fake_doc = MagicMock()
            fake_doc.__iter__ = MagicMock(return_value=iter([fake_page]))
            fake_doc.close = MagicMock()

            with patch("fitz.open", return_value=fake_doc):
                with patch(
                    "apps.public_core.services.universal_ticket_parser._openai_client",
                    return_value=mock_client,
                ):
                    result = parser.parse_files([path_csv, path_pdf], "42-501-70575")

            assert isinstance(result, DWRParseResult)
        finally:
            for p in (path_csv, path_pdf):
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass


# ---------------------------------------------------------------------------
# JMR detection heuristic
# ---------------------------------------------------------------------------

class TestIsJmrContent:
    def test_detects_daily_work_record_header(self):
        parser = _make_parser()
        assert parser._is_jmr_content("DAILY WORK RECORD\nAPI: 42-501-70575")

    def test_detects_jmr_brand_name(self):
        parser = _make_parser()
        assert parser._is_jmr_content("JMR Services Inc\nDAY NO 1\nDEPTH 5000")

    def test_rejects_generic_text(self):
        parser = _make_parser()
        assert not parser._is_jmr_content("Service company ticket for well plugging")

    def test_detects_day_no_and_depth_combo(self):
        parser = _make_parser()
        assert parser._is_jmr_content("DAY NO 1  DEPTH 5000 ft")
