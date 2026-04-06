"""Universal Ticket Parser for W-3 Daily Ticket Upload & Reconciliation Wizard.

AI-first, format-agnostic parser. Handles any service company's daily ticket format.

Supported file types: PDF, DOCX, images (JPG/PNG/TIFF), CSV, Excel.

Flow:
1. Format-specific text extractors pull raw content from each file type.
2. All content is fed to a single GPT-4o extraction call with a universal DWR prompt.
3. Returns the same DWREvent / DWRDay / DWRParseResult dataclasses from dwr_parser.py.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from apps.public_core.services.dwr_parser import (
    DWRDay,
    DWREvent,
    DWRParser,
    DWRParseResult,
)
from apps.public_core.services.docx_extraction import extract_text_from_docx
from apps.public_core.services.openai_extraction import _openai_client

logger = logging.getLogger(__name__)


SUPPORTED_EXTENSIONS: Dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "docx",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".tiff": "image",
    ".tif": "image",
    ".csv": "csv",
    ".xlsx": "excel",
    ".xls": "excel",
}

def _build_dwr_system_prompt(api_number: str = "", well_context: dict | None = None) -> str:
    """Build a dynamic DWR extraction prompt with optional well context."""

    # Full event type list matching expanded PLUG_EVENT_KEYWORDS
    event_types = (
        "set_cement_plug|set_surface_plug|set_bridge_plug|set_marker|squeeze|"
        "tag_toc|circulate|pump_cement|woc|pressure_test|pull_tubing|cut_casing|"
        "rig_up|rig_down|kill_well|nipple_up|nipple_down|pull_rods|run_tubing|"
        "fish|wireline|pump_acid|pump_fluid|pressure_up|perforate|standby|safety_meeting|other"
    )

    # Well context block
    well_block = ""
    if well_context:
        parts = []
        wh = well_context.get("well_header", {})
        if wh:
            parts.append(f"Well: {wh.get('well_name', 'Unknown')}, API: {api_number}")
            if wh.get("total_depth"):
                parts.append(f"Total Depth: {wh['total_depth']} ft")
        formations = well_context.get("formations", [])
        if formations:
            fmt_list = ", ".join(
                f"{f.get('name', '?')} @ {f.get('depth_top_ft', '?')}-{f.get('depth_bottom_ft', '?')} ft"
                for f in formations[:10]
            )
            parts.append(f"Formations: {fmt_list}")
        casing = well_context.get("casing_record", [])
        if casing:
            csg_list = ", ".join(
                f"{c.get('size', '?')}\" {c.get('type', '?')} to {c.get('depth_ft', '?')} ft"
                for c in casing[:10]
            )
            parts.append(f"Casing Record: {csg_list}")
        if parts:
            well_block = "\n\nWell Context:\n" + "\n".join(f"- {p}" for p in parts)

    return f"""You are an expert at extracting P&A (plug & abandon) operation data from daily field tickets for oil & gas wells.

Extract ALL operations from the provided daily work records — not just plugging events. P&A jobs include prep work (rig up, kill well, pull rods/tubing, fishing), plugging operations (cement plugs, bridge plugs, squeezes), and demobilization (nipple down, rig down).{well_block}

Return a JSON object with this exact structure:
{{
  "well_name": "string or empty",
  "operator": "string or empty",
  "days": [
    {{
      "work_date": "YYYY-MM-DD",
      "day_number": 1,
      "daily_narrative": "summary of day's work",
      "crew_size": null or int,
      "rig_name": "string or empty",
      "events": [
        {{
          "event_type": "{event_types}",
          "description": "what happened",
          "depth_top_ft": null or float,
          "depth_bottom_ft": null or float,
          "tagged_depth_ft": null or float,
          "cement_class": null or "A"|"C"|"H",
          "sacks": null or float,
          "volume_bbl": null or float,
          "pressure_psi": null or float,
          "plug_number": null or int,
          "casing_string": null or "surface"|"intermediate"|"production",
          "start_time": null or "HH:MM",
          "end_time": null or "HH:MM",
          "placement_method": "perf_and_squeeze"|"perf_and_circulate"|"spot_plug"|"squeeze"|null,
          "woc_hours": null or float
        }}
      ]
    }}
  ],
  "warnings": ["any issues or uncertainties"]
}}

Important:
- Order days chronologically
- Number plugs sequentially if not explicitly numbered
- Convert all depths to feet
- Tickets may be JMR Services format (structured tables with From/To times), handwritten field notes, or mixed. Extract times from table columns when available.
- If handwritten or unclear, make best effort and add warnings
- For squeeze operations, look for perforation + cement pump sequences
- Use "other" for operations that don't match any specific event_type"""


@dataclass
class FileContent:
    """Intermediate container for content extracted from a single file."""

    file_name: str
    file_type: str  # "pdf", "docx", "image", "csv", "excel"
    text_content: str
    tables: List[dict] = field(default_factory=list)
    image_base64: Optional[str] = None  # For images sent to Vision API


class UniversalTicketParser:
    """Parse any service company's daily ticket format into DWRParseResult.

    Uses format-specific text extractors followed by a single GPT-4o call
    to produce structured DWR data regardless of input format.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_files(self, file_paths: List[str], api_number: str, well_context: dict | None = None) -> DWRParseResult:
        """Parse one or more ticket files into a single merged DWRParseResult.

        Args:
            file_paths: Absolute paths to daily ticket files.
            api_number: Well API number used for result attribution.
            well_context: Optional well context dict with well_header, formations, casing_record.

        Returns:
            DWRParseResult with all days sorted chronologically.
        """
        if not file_paths:
            result = DWRParseResult(
                api_number=api_number,
                parse_method="no_input",
                confidence=0.0,
            )
            result.warnings.append("No files provided to UniversalTicketParser")
            return result

        contents: List[FileContent] = []
        jmr_results: List[DWRParseResult] = []

        for path in file_paths:
            file_content = self._extract_file(path)
            if file_content is None:
                continue

            # Check PDF content for JMR format and use structured parser when detected
            if file_content.file_type == "pdf" and self._is_jmr_content(
                file_content.text_content
            ):
                logger.info(
                    "UniversalTicketParser: JMR format detected in %s, using DWRParser",
                    file_content.file_name,
                )
                try:
                    dwr_parser = DWRParser()
                    jmr_result = dwr_parser._try_jmr_parse(
                        file_content.text_content, api_number
                    )
                    if jmr_result is not None:
                        jmr_results.append(jmr_result)
                        continue  # Skip AI extraction for this file
                except Exception as exc:
                    logger.warning(
                        "UniversalTicketParser: JMR parse failed for %s: %s — falling back to AI",
                        file_content.file_name,
                        exc,
                    )

            contents.append(file_content)

        # Run AI extraction on all non-JMR content
        ai_result: Optional[DWRParseResult] = None
        if contents:
            ai_result = self._ai_extract_events(contents, api_number, well_context)

        # Merge JMR results and AI result
        return self._merge_results(
            jmr_results=jmr_results,
            ai_result=ai_result,
            api_number=api_number,
        )

    # ------------------------------------------------------------------
    # Format Extractors (private)
    # ------------------------------------------------------------------

    def _extract_file(self, file_path: str) -> Optional[FileContent]:
        """Detect file type and dispatch to the appropriate extractor.

        Returns None if the file type is unsupported or extraction fails.
        """
        path = Path(file_path)
        ext = path.suffix.lower()
        file_type = SUPPORTED_EXTENSIONS.get(ext)

        if file_type is None:
            logger.warning(
                "UniversalTicketParser: unsupported extension %s for %s — skipping",
                ext,
                path.name,
            )
            return None

        extractors = {
            "pdf": self._extract_from_pdf,
            "docx": self._extract_from_docx,
            "image": self._extract_from_image,
            "csv": self._extract_from_csv,
            "excel": self._extract_from_excel,
        }

        extractor = extractors[file_type]
        try:
            return extractor(file_path)
        except Exception as exc:
            logger.warning(
                "UniversalTicketParser: extraction failed for %s (%s): %s",
                path.name,
                file_type,
                exc,
            )
            return None

    def _extract_from_pdf(self, file_path: str) -> FileContent:
        """Extract text from a PDF using PyMuPDF, with pdfplumber fallback."""
        path = Path(file_path)
        text_parts: List[str] = []

        # Try PyMuPDF (fitz) first
        fitz_ok = False
        try:
            import fitz  # type: ignore

            doc = fitz.open(str(path))
            for page in doc:
                t = page.get_text() or ""
                if t.strip():
                    text_parts.append(t)
            doc.close()
            fitz_ok = True
        except ImportError:
            logger.warning(
                "UniversalTicketParser._extract_from_pdf: PyMuPDF not installed, trying pdfplumber"
            )
        except Exception as exc:
            logger.warning(
                "UniversalTicketParser._extract_from_pdf: fitz failed for %s: %s",
                path.name,
                exc,
            )

        combined = "\n\n".join(text_parts).strip()

        # Fall back to pdfplumber if fitz yielded minimal text
        if not fitz_ok or len(combined) < 100:
            try:
                import pdfplumber  # type: ignore

                fallback_parts: List[str] = []
                with pdfplumber.open(str(path)) as pdf:
                    for page in pdf.pages:
                        t = page.extract_text() or ""
                        if t.strip():
                            fallback_parts.append(t)
                fallback_text = "\n\n".join(fallback_parts).strip()
                if len(fallback_text) > len(combined):
                    combined = fallback_text
            except ImportError:
                logger.warning(
                    "UniversalTicketParser._extract_from_pdf: pdfplumber not installed"
                )
            except Exception as exc:
                logger.warning(
                    "UniversalTicketParser._extract_from_pdf: pdfplumber failed for %s: %s",
                    path.name,
                    exc,
                )

        return FileContent(
            file_name=path.name,
            file_type="pdf",
            text_content=combined,
        )

    def _extract_from_docx(self, file_path: str) -> FileContent:
        """Extract text and tables from a DOCX file."""
        path = Path(file_path)
        text, tables = extract_text_from_docx(file_path)
        return FileContent(
            file_name=path.name,
            file_type="docx",
            text_content=text,
            tables=tables,
        )

    def _extract_from_image(self, file_path: str) -> FileContent:
        """Read an image file as base64 for Vision API; attempt OCR as text fallback."""
        path = Path(file_path)

        with open(file_path, "rb") as f:
            image_bytes = f.read()
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        # Attempt OCR fallback (non-fatal if tesseract not installed)
        ocr_text = ""
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore
            import io

            pil_image = Image.open(io.BytesIO(image_bytes))
            ocr_text = pytesseract.image_to_string(pil_image) or ""
        except ImportError:
            logger.warning(
                "UniversalTicketParser._extract_from_image: pytesseract/Pillow not installed; Vision API only"
            )
        except Exception as exc:
            logger.warning(
                "UniversalTicketParser._extract_from_image: OCR failed for %s: %s",
                path.name,
                exc,
            )

        return FileContent(
            file_name=path.name,
            file_type="image",
            text_content=ocr_text,
            image_base64=image_base64,
        )

    def _extract_from_csv(self, file_path: str) -> FileContent:
        """Extract CSV content as text and table dict."""
        path = Path(file_path)
        import pandas as pd  # type: ignore

        df = pd.read_csv(file_path)
        text = df.to_string(index=False)
        table = {"file": path.name, "data": df.to_dict(orient="records")}

        return FileContent(
            file_name=path.name,
            file_type="csv",
            text_content=text,
            tables=[table],
        )

    def _extract_from_excel(self, file_path: str) -> FileContent:
        """Extract all sheets from an Excel file as concatenated text and table dicts."""
        path = Path(file_path)
        import pandas as pd  # type: ignore

        sheets: Dict[str, Any] = pd.read_excel(file_path, sheet_name=None)

        text_parts: List[str] = []
        tables: List[dict] = []

        for sheet_name, df in sheets.items():
            sheet_text = f"[Sheet: {sheet_name}]\n{df.to_string(index=False)}"
            text_parts.append(sheet_text)
            tables.append(
                {
                    "sheet": sheet_name,
                    "data": df.to_dict(orient="records"),
                }
            )

        return FileContent(
            file_name=path.name,
            file_type="excel",
            text_content="\n\n".join(text_parts),
            tables=tables,
        )

    # ------------------------------------------------------------------
    # AI Extraction
    # ------------------------------------------------------------------

    def _ai_extract_events(
        self, contents: List[FileContent], api_number: str, well_context: dict | None = None
    ) -> DWRParseResult:
        """Send extracted content to GPT-4o and parse the structured response.

        Uses Vision API messages when any FileContent contains image_base64.
        """
        result = DWRParseResult(
            api_number=api_number,
            parse_method="universal_ai",
            confidence=0.0,
        )

        # Concatenate text from all non-image (or OCR-supplemented) files
        text_parts: List[str] = []
        image_contents: List[FileContent] = []

        for fc in contents:
            if fc.image_base64 is not None:
                image_contents.append(fc)
            if fc.text_content.strip():
                text_parts.append(f"[File: {fc.file_name}]\n{fc.text_content}")

        combined_text = "\n\n---\n\n".join(text_parts)

        # Truncate to avoid exceeding model context
        original_len = len(combined_text)
        max_chars = 100_000
        if original_len > max_chars:
            combined_text = combined_text[:max_chars]
            result.warnings.append(
                f"Combined ticket text truncated from {original_len} to {max_chars} chars for AI extraction"
            )

        try:
            client = _openai_client()
            model = os.getenv("OPENAI_EXTRACTION_MODEL", "gpt-4o")

            if image_contents:
                # Build vision messages with image_url content blocks
                user_content: List[Dict[str, Any]] = [
                    {
                        "type": "text",
                        "text": (
                            f"Extract all P&A operation data from these daily ticket files.\n\n"
                            f"TEXT CONTENT:\n{combined_text}"
                            if combined_text
                            else "Extract all P&A operation data from the provided images."
                        ),
                    }
                ]
                for fc in image_contents:
                    ext = Path(fc.file_name).suffix.lower().lstrip(".")
                    mime = {
                        "jpg": "image/jpeg",
                        "jpeg": "image/jpeg",
                        "png": "image/png",
                        "tiff": "image/tiff",
                        "tif": "image/tiff",
                    }.get(ext, "image/jpeg")
                    user_content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{fc.image_base64}"
                            },
                        }
                    )

                messages = [
                    {
                        "role": "system",
                        "content": _build_dwr_system_prompt(api_number, well_context),
                    },
                    {
                        "role": "user",
                        "content": user_content,
                    },
                ]
            else:
                messages = [
                    {
                        "role": "system",
                        "content": _build_dwr_system_prompt(api_number, well_context),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Extract all P&A operations from these daily ticket files:\n\n"
                            f"{combined_text}"
                        ),
                    },
                ]

            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
            )
            raw_json = response.choices[0].message.content or "{}"
            data = json.loads(raw_json)

            # Dynamic confidence scoring
            confidence = 0.85  # base for text-only extraction
            if image_contents:
                confidence -= 0.20  # OCR uncertainty
            if original_len > max_chars:
                confidence -= 0.10  # truncation penalty
            confidence = max(confidence, 0.10)
            result.confidence = confidence

        except Exception as exc:
            logger.exception(
                "UniversalTicketParser._ai_extract_events: OpenAI call failed: %s", exc
            )
            result.warnings.append(f"AI extraction failed: {exc}")
            result.confidence = 0.0
            return result

        # Map AI response into DWRParseResult
        result.well_name = data.get("well_name") or ""
        result.operator = data.get("operator") or ""

        dwr_parser = DWRParser()
        for day_data in data.get("days", []):
            day = dwr_parser._build_dwr_day_from_ai(day_data)
            if day is not None:
                result.days.append(day)

        result.days.sort(key=lambda d: d.work_date)
        for idx, day in enumerate(result.days, start=1):
            day.day_number = idx
        result.total_days = len(result.days)

        for warning in data.get("warnings", []):
            if warning:
                result.warnings.append(str(warning))

        return result

    # ------------------------------------------------------------------
    # Merge & Utilities
    # ------------------------------------------------------------------

    def _merge_results(
        self,
        jmr_results: List[DWRParseResult],
        ai_result: Optional[DWRParseResult],
        api_number: str,
    ) -> DWRParseResult:
        """Merge JMR structured results and AI result into a single DWRParseResult.

        JMR results take priority for overlapping dates (higher confidence).
        """
        all_results = list(jmr_results)
        if ai_result is not None:
            all_results.append(ai_result)

        if not all_results:
            empty = DWRParseResult(
                api_number=api_number,
                parse_method="failed",
                confidence=0.0,
            )
            empty.warnings.append("No content could be extracted from provided files")
            return empty

        if len(all_results) == 1:
            return all_results[0]

        # Determine parse_method label
        has_jmr = bool(jmr_results)
        has_ai = ai_result is not None
        if has_jmr and has_ai:
            parse_method = "universal_mixed"
        elif has_jmr:
            parse_method = "jmr_structured"
        else:
            parse_method = "universal_ai"

        merged = DWRParseResult(
            api_number=next(
                (r.api_number for r in all_results if r.api_number), api_number
            ),
            well_name=next((r.well_name for r in all_results if r.well_name), ""),
            operator=next((r.operator for r in all_results if r.operator), ""),
            parse_method=parse_method,
            confidence=min(r.confidence for r in all_results),
        )

        # Merge days — JMR entries take priority on date conflicts
        seen_dates: Dict[Any, DWRDay] = {}

        # Add JMR days first (higher confidence)
        for r in jmr_results:
            merged.warnings.extend(r.warnings)
            for day in r.days:
                seen_dates[day.work_date] = day

        # Add AI days, skip dates already covered by JMR
        if ai_result is not None:
            merged.warnings.extend(ai_result.warnings)
            for day in ai_result.days:
                if day.work_date not in seen_dates:
                    seen_dates[day.work_date] = day
                else:
                    merged.warnings.append(
                        f"Duplicate date {day.work_date} in AI and JMR results — kept JMR entry"
                    )

        merged.days = sorted(seen_dates.values(), key=lambda d: d.work_date)
        for idx, day in enumerate(merged.days, start=1):
            day.day_number = idx
        merged.total_days = len(merged.days)

        return merged

    def _is_jmr_content(self, text: str) -> bool:
        """Heuristic check: does extracted PDF text look like JMR format?"""
        upper = text[:2000].upper()
        return (
            "DAILY WORK RECORD" in upper
            or "JMR" in upper
            or ("DAY NO" in upper and "DEPTH" in upper)
            or ("DAILY REPORT" in upper and "API" in upper)
        )
