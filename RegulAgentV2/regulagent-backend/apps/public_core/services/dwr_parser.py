"""DWR (Daily Work Record) PDF Parser for C-103 Subsequent Reports.

Parses Daily Work Record PDFs to extract structured operational data
for plug reconciliation and subsequent report generation.

Supports:
1. JMR-format DWRs (structured, table-based) — direct parsing
2. Non-standard DWR formats — AI extraction fallback
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from apps.public_core.services.openai_config import get_openai_client

logger = logging.getLogger(__name__)


# ---- Data Models ----

@dataclass
class DWREvent:
    """Single operational event extracted from a DWR."""
    event_type: str  # matches C103EventORM EVENT_TYPE_CHOICES
    description: str
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    depth_top_ft: Optional[float] = None
    depth_bottom_ft: Optional[float] = None
    tagged_depth_ft: Optional[float] = None
    cement_class: Optional[str] = None
    sacks: Optional[float] = None
    volume_bbl: Optional[float] = None
    pressure_psi: Optional[float] = None
    plug_number: Optional[int] = None
    casing_string: Optional[str] = None
    raw_text: str = ""
    placement_method: Optional[str] = None  # perf_and_squeeze | perf_and_circulate | spot_plug | squeeze
    woc_hours: Optional[float] = None
    woc_tagged: Optional[bool] = None


@dataclass
class DWRDay:
    """One day's operations from a DWR."""
    work_date: date
    day_number: int
    events: List[DWREvent] = field(default_factory=list)
    daily_narrative: str = ""
    crew_size: Optional[int] = None
    rig_name: Optional[str] = None
    weather: Optional[str] = None


@dataclass
class DWRParseResult:
    """Complete parse result from one or more DWR PDFs."""
    api_number: str
    well_name: str = ""
    operator: str = ""
    days: List[DWRDay] = field(default_factory=list)
    total_days: int = 0
    parse_method: str = ""  # "jmr_structured" or "ai_extraction"
    confidence: float = 0.0
    warnings: List[str] = field(default_factory=list)


# ---- Placement Method Inference ----

def infer_placement_methods(days: List["DWRDay"]) -> None:
    """Infer placement_method, woc_hours, and woc_tagged on DWREvents in-place.

    Scans events chronologically across all days to detect sequences such as
    perforate → squeeze (perf_and_squeeze) and perforate → cement (perf_and_circulate),
    then tags WOC events with hours and back-fills woc_tagged on plug events.
    """
    _WOC_HOURS_RE = re.compile(r"WOC\s*(\d+(?:\.\d+)?)\s*h", re.IGNORECASE)

    # Collect all plug placement event types to find "most recent plug"
    _PLUG_TYPES = {"set_cement_plug", "set_surface_plug", "set_bridge_plug",
                   "set_marker", "squeeze", "pump_cement"}

    def _depth_nearby(top1, bot1, top2, bot2, threshold=100.0) -> bool:
        """Return True if two depth intervals overlap or are within threshold ft."""
        mid1 = None
        if top1 is not None and bot1 is not None:
            mid1 = (top1 + bot1) / 2.0
        elif top1 is not None:
            mid1 = top1
        elif bot1 is not None:
            mid1 = bot1

        mid2 = None
        if top2 is not None and bot2 is not None:
            mid2 = (top2 + bot2) / 2.0
        elif top2 is not None:
            mid2 = top2
        elif bot2 is not None:
            mid2 = bot2

        if mid1 is None or mid2 is None:
            return True  # No depth info — assume nearby (don't penalise)
        return abs(mid1 - mid2) <= threshold

    # Build a flat list of (day_index, event) for backward searching
    all_events_flat: List[Tuple[int, "DWREvent"]] = []

    for day_idx, day in enumerate(days):
        # Per-day: track perforate events seen so far (before current event)
        recent_perfs: List["DWREvent"] = []

        for ev in day.events:
            et = ev.event_type

            if et == "perforate":
                recent_perfs.append(ev)

            elif et == "squeeze":
                # Check if a recent perf at nearby depth exists within this day
                matched_perf = next(
                    (p for p in recent_perfs
                     if _depth_nearby(p.depth_top_ft, p.depth_bottom_ft,
                                      ev.depth_top_ft, ev.depth_bottom_ft)),
                    None,
                )
                ev.placement_method = "perf_and_squeeze" if matched_perf else "squeeze"

            elif et in ("circulate", "pump_cement", "set_cement_plug"):
                matched_perf = next(
                    (p for p in recent_perfs
                     if _depth_nearby(p.depth_top_ft, p.depth_bottom_ft,
                                      ev.depth_top_ft, ev.depth_bottom_ft)),
                    None,
                )
                if matched_perf:
                    ev.placement_method = "perf_and_circulate"
                elif et in ("pump_cement", "set_cement_plug"):
                    ev.placement_method = "spot_plug"

            elif et == "woc":
                # Extract WOC hours from description
                m = _WOC_HOURS_RE.search(ev.description or "")
                if m:
                    woc_h = float(m.group(1))
                    # Back-fill woc_hours onto the most recent plug event
                    # Search backwards in current day first, then previous days
                    found = False
                    for prev_ev in reversed(day.events[:day.events.index(ev)]):
                        if prev_ev.event_type in _PLUG_TYPES:
                            prev_ev.woc_hours = woc_h
                            found = True
                            break
                    if not found:
                        for _, prev_ev in reversed(all_events_flat):
                            if prev_ev.event_type in _PLUG_TYPES:
                                prev_ev.woc_hours = woc_h
                                break

            elif et in ("tag_toc", "tag"):
                # Back-fill woc_tagged = True onto most recent plug event
                found = False
                for prev_ev in reversed(day.events[:day.events.index(ev)]):
                    if prev_ev.event_type in _PLUG_TYPES:
                        prev_ev.woc_tagged = True
                        found = True
                        break
                if not found:
                    for _, prev_ev in reversed(all_events_flat):
                        if prev_ev.event_type in _PLUG_TYPES:
                            prev_ev.woc_tagged = True
                            break

            all_events_flat.append((day_idx, ev))


# ---- Plug Event Detection ----

PLUG_EVENT_KEYWORDS: Dict[str, List[str]] = {
    'set_cement_plug': ['spot cement', 'set cement plug', 'cement plug', 'place cement', 'cement from', 'spotted'],
    'set_surface_plug': ['surface plug', 'surface cement'],
    'set_bridge_plug': ['cibp', 'bridge plug', 'cast iron bridge plug', 'set bridge'],
    'set_marker': ['set marker', 'marker plug'],
    'squeeze': ['squeeze', 'squeeze cement', 'annular squeeze'],
    'tag_toc': ['tag cement', 'tag toc', 'tagged at', 'tagged top', 'tag top of cement', 'tagged solid'],
    'circulate': ['circulate', 'circulate cement', 'circulate returns'],
    'pump_cement': ['pump cement', 'pump class', 'pumped cement'],
    'woc': ['woc', 'wait on cement', 'waiting on cement'],
    'pressure_test': ['pressure test', 'pressure tested', 'test to'],
    'pull_tubing': ['pull tubing', 'poh tubing', 'pull out', 'lay down tubing', 'laid down'],
    'cut_casing': ['cut casing', 'cut and pull', 'cut pipe'],
    'rig_up': ['rig up', 'rigged up', 'move in', 'move on location'],
    'rig_down': ['rig down', 'rigged down', 'move off', 'demobilize'],
    'kill_well': ['kill well', 'killed well', 'kill string', 'pump kill fluid', 'bullhead'],
    'nipple_up': ['nipple up', 'nu bop', 'install bop'],
    'nipple_down': ['nipple down', 'nd bop', 'nd wellhead', 'remove bop'],
    'pull_rods': ['pull rods', 'lay down rods', 'laying down rods', 'rod operations', 'poh rods', 'lay down ('],
    'run_tubing': ['run tubing', 'rih tubing', 'pick up tubing', 'ran tubing'],
    'fish': ['fishing', 'fish out', 'fish in', 'overshot', 'jarring', 'grapple', 'string shot'],
    'wireline': ['wireline', 'gauge ring', 'slickline', 'e-line', 'chemical cutter'],
    'pump_acid': ['pump acid', 'pumped acid', 'acidize', 'acid treatment', 'barrels of acid'],
    'pump_fluid': ['pump fluid', 'pump brine', 'pumped brine', 'spot fluid', 'displace', 'circulate brine', 'hot oil', 'hot oiler', 'flush down', 'bbls down', 'barrels down'],
    'pressure_up': ['pressure up', 'test to'],
    'perforate': ['perforate', 'perforated', 'perf run', 'perf shot', 'gun run'],
    'standby': ['standby', 'stand by', 'wait on', 'woo', 'wow', 'weather delay'],
    'safety_meeting': ['safety meeting', 'jsa', 'toolbox talk'],
    'other': [],
}

# Ordered by specificity — more specific patterns checked first
_EVENT_TYPE_PRIORITY = [
    # Plugging operations (most specific first)
    'set_surface_plug',
    'set_bridge_plug',
    'set_marker',
    'squeeze',
    'set_cement_plug',
    'tag_toc',
    'woc',
    'pressure_test',
    'pressure_up',
    'cut_casing',
    # Tubing/rod operations
    'pull_tubing',
    'pull_rods',
    'run_tubing',
    'circulate',
    # Pump operations: specific types BEFORE generic cement
    'kill_well',
    'pump_acid',
    'pump_fluid',
    'pump_cement',
    # Wellhead / BOP
    'nipple_up',
    'nipple_down',
    # Rig operations
    'rig_up',
    'rig_down',
    # Downhole operations
    'fish',
    'wireline',
    'perforate',
    # Non-operational
    'safety_meeting',
    'standby',
    'other',
]


# ---- DWR AI Extraction Prompt ----

_DWR_EXTRACTION_PROMPT = """Extract all daily work record (DWR) data from this oil & gas plugging report.

Return a JSON object with this structure:
{
  "api_number": "string or null",
  "well_name": "string or null",
  "operator": "string or null",
  "days": [
    {
      "work_date": "YYYY-MM-DD",
      "day_number": 1,
      "rig_name": "string or null",
      "crew_size": null or integer,
      "weather": "string or null",
      "daily_narrative": "plain text summary of all operations that day",
      "events": [
        {
          "event_type": "one of: set_cement_plug|set_surface_plug|set_bridge_plug|set_marker|squeeze|tag_toc|circulate|pump_cement|woc|pressure_test|pull_tubing|cut_casing|rig_up|rig_down|kill_well|nipple_up|nipple_down|pull_rods|run_tubing|fish|wireline|pump_acid|pump_fluid|pressure_up|perforate|standby|safety_meeting|other",
          "description": "brief description of the operation",
          "start_time": "HH:MM or null",
          "end_time": "HH:MM or null",
          "depth_top_ft": "shallower depth in feet (smaller number) or null",
          "depth_bottom_ft": "deeper depth in feet (larger number) or null",
          "tagged_depth_ft": "measured/tagged depth of cement top or null",
          "cement_class": "A|B|C|G|H or null",
          "sacks": "number of cement sacks or null",
          "volume_bbl": "volume in barrels or null",
          "pressure_psi": "pressure in PSI or null",
          "plug_number": "plug sequence number or null",
          "casing_string": "casing description or null",
          "placement_method": "perf_and_squeeze|perf_and_circulate|spot_plug|squeeze|null",
          "woc_hours": "float|null — hours of WOC if this is a WOC event"
        }
      ]
    }
  ]
}

Rules:
- Extract ALL days present in the document, in chronological order
- Classify each field operation using the event_type values listed above
- For cement plug operations: ALWAYS extract plug_number, depth_top_ft, depth_bottom_ft, sacks, and cement_class when present in the description
- "Spot", "Spotted", "Squeezed", "Pumped", "Circulated" followed by cement details are all cement plug operations (set_cement_plug or squeeze)
- Depth values: convert to plain numbers in feet (no units). Handle commas (7,020), prime notation (7020'), smart quotes, and parenthesized values like (7020')
- "From X to Y" depth ranges: depth_top_ft = smaller number, depth_bottom_ft = larger number
- "to surface" means depth_top_ft = 0
- Sack counts: handle abbreviations sacks, sx, sxs, sks — always return as a number
- If a field is not mentioned, set it to null
- Return only valid JSON, no commentary
"""


class DWRParser:
    """Parse Daily Work Record PDFs into structured data."""

    def __init__(self):
        self._fitz = None  # Lazy import PyMuPDF

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, pdf_path: str | Path, api_number: str = "") -> DWRParseResult:
        """Parse a DWR PDF file.

        Attempts JMR structured parsing first, falls back to AI extraction.

        Args:
            pdf_path: Path to PDF file
            api_number: Well API number (optional, extracted if not provided)

        Returns:
            DWRParseResult with extracted daily operations
        """
        pdf_path = Path(pdf_path)
        logger.info("DWRParser.parse: start path=%s api=%s", pdf_path, api_number)

        text = self._extract_text_from_pdf(pdf_path)
        if not text.strip():
            result = DWRParseResult(api_number=api_number, parse_method="failed", confidence=0.0)
            result.warnings.append(f"No text extracted from {pdf_path.name}")
            return result

        # Always use AI extraction for reliability — regex parsing was too
        # fragile with encoding variations (Unicode smart quotes, varying
        # abbreviations, non-standard depth notations from PDF extraction).
        result = self._ai_extraction_parse(text, api_number)
        return result

    def parse_multiple(self, pdf_paths: List[str | Path], api_number: str = "") -> DWRParseResult:
        """Parse multiple DWR PDFs and merge into single result.

        Sorts days chronologically and deduplicates same-date entries.
        """
        if not pdf_paths:
            return DWRParseResult(api_number=api_number, parse_method="no_input", confidence=0.0)

        results = []
        for path in pdf_paths:
            try:
                r = self.parse(path, api_number)
                results.append(r)
            except Exception as exc:
                logger.warning("DWRParser.parse_multiple: failed on %s: %s", path, exc)

        if not results:
            merged = DWRParseResult(api_number=api_number, parse_method="failed", confidence=0.0)
            merged.warnings.append("All PDF parse attempts failed")
            return merged

        # Merge: use first result as base, accumulate days
        merged = DWRParseResult(
            api_number=results[0].api_number or api_number,
            well_name=next((r.well_name for r in results if r.well_name), ""),
            operator=next((r.operator for r in results if r.operator), ""),
            parse_method=results[0].parse_method,
            confidence=min(r.confidence for r in results),
        )

        seen_dates: Dict[date, DWRDay] = {}
        for result in results:
            merged.warnings.extend(result.warnings)
            for day in result.days:
                if day.work_date not in seen_dates:
                    seen_dates[day.work_date] = day
                else:
                    # Merge events from duplicate date — prefer whichever has more events
                    existing = seen_dates[day.work_date]
                    if len(day.events) > len(existing.events):
                        seen_dates[day.work_date] = day
                    merged.warnings.append(
                        f"Duplicate date {day.work_date} across PDFs — kept entry with more events"
                    )

        merged.days = sorted(seen_dates.values(), key=lambda d: d.work_date)
        # Reassign sequential day numbers
        for idx, day in enumerate(merged.days, start=1):
            day.day_number = idx
        merged.total_days = len(merged.days)

        return merged

    # ------------------------------------------------------------------
    # JMR Structured Parsing
    # ------------------------------------------------------------------

    def _try_jmr_parse(self, text: str, api_number: str) -> Optional[DWRParseResult]:
        """Attempt to parse as JMR-format structured DWR.

        JMR format has:
        - Header with API#, Well Name, Operator
        - Date columns
        - Tabular operation descriptions
        - Depth entries
        - Materials used

        Returns None if text does not match JMR format heuristics.
        """
        # JMR heuristic: look for "DAILY WORK RECORD" or "JMR" in header
        upper = text[:2000].upper()
        is_jmr = (
            "DAILY WORK RECORD" in upper
            or "JMR" in upper
            or ("DAY NO" in upper and "DEPTH" in upper)
            or ("DAILY REPORT" in upper and "API" in upper)
        )
        if not is_jmr:
            return None

        warnings: List[str] = []

        # Extract header fields
        extracted_api = api_number or self._extract_api_from_header(text)
        well_name = self._extract_header_field(text, r'(?:well\s*name|well)[:\s]+([^\n\r]+)', 'well_name')
        operator = self._extract_header_field(text, r'(?:operator|company)[:\s]+([^\n\r]+)', 'operator')

        result = DWRParseResult(
            api_number=extracted_api,
            well_name=well_name,
            operator=operator,
            parse_method="jmr_structured",
            confidence=0.95,
        )

        # Split into day blocks and parse each
        day_blocks = self._split_into_day_blocks(text)
        if not day_blocks:
            # Treat entire text as one day block
            day_blocks = [(text, None, 1)]

        for block_text, block_date, day_num in day_blocks:
            if block_date is None:
                block_date = self._extract_first_date(block_text)
            if block_date is None:
                warnings.append(f"Could not determine date for day block #{day_num}")
                continue

            events = self._detect_events_from_text(block_text)
            narrative = self._build_daily_narrative(events)
            rig = self._extract_header_field(block_text, r'rig[:\s]+([^\n\r]+)', 'rig')
            weather = self._extract_header_field(block_text, r'weather[:\s]+([^\n\r]+)', 'weather')
            crew_match = re.search(r'crew[:\s]+(\d+)', block_text, re.IGNORECASE)
            crew_size = int(crew_match.group(1)) if crew_match else None

            day = DWRDay(
                work_date=block_date,
                day_number=day_num,
                events=events,
                daily_narrative=narrative,
                rig_name=rig,
                weather=weather,
                crew_size=crew_size,
            )
            result.days.append(day)

        result.days.sort(key=lambda d: d.work_date)
        for idx, day in enumerate(result.days, start=1):
            day.day_number = idx
        result.total_days = len(result.days)
        result.warnings = warnings

        if result.total_days == 0:
            # JMR detection matched but no days extracted — fall through to AI
            return None

        infer_placement_methods(result.days)

        return result

    def _split_into_day_blocks(self, text: str) -> List[Tuple[str, Optional[date], int]]:
        """Split text into per-day blocks.

        Returns list of (block_text, date_or_None, day_number).
        Looks for day-separator patterns common in JMR format.
        """
        # Pattern: "DAY 1", "Day No. 1", "DATE: MM/DD/YYYY" section headers
        day_header_pattern = re.compile(
            r'(?:^|\n)\s*(?:day\s*(?:no\.?\s*)?(\d+)|date[:\s]+([\d/\-]+))',
            re.IGNORECASE,
        )
        matches = list(day_header_pattern.finditer(text))
        if len(matches) < 2:
            return []

        blocks: List[Tuple[str, Optional[date], int]] = []
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block_text = text[start:end]

            day_num_str = match.group(1)
            date_str = match.group(2)
            day_num = int(day_num_str) if day_num_str else (i + 1)
            parsed_date = self._parse_date(date_str) if date_str else None

            blocks.append((block_text, parsed_date, day_num))

        return blocks

    # ------------------------------------------------------------------
    # AI Extraction Fallback
    # ------------------------------------------------------------------

    def _ai_extraction_parse(self, text: str, api_number: str) -> DWRParseResult:
        """Fallback: Use AI (OpenAI) to extract structured data from freeform DWR text."""
        result = DWRParseResult(
            api_number=api_number,
            parse_method="ai_extraction",
            confidence=0.90,
        )

        # Truncate text to avoid exceeding model context
        max_chars = 24000
        if len(text) > max_chars:
            text = text[:max_chars]
            result.warnings.append(f"DWR text truncated to {max_chars} chars for AI extraction")

        try:
            client = get_openai_client(operation="dwr_parse")
            model = os.getenv("OPENAI_EXTRACTION_MODEL", "gpt-4o")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are an oil & gas document extraction specialist. Return only valid JSON."},
                    {"role": "user", "content": f"{_DWR_EXTRACTION_PROMPT}\n\n---DWR TEXT---\n{text}"},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            raw_json = response.choices[0].message.content or "{}"
            data = json.loads(raw_json)
        except Exception as exc:
            logger.exception("DWRParser._ai_extraction_parse: OpenAI call failed: %s", exc)
            result.warnings.append(f"AI extraction failed: {exc}")
            result.confidence = 0.0
            return result

        result.api_number = data.get("api_number") or api_number
        result.well_name = data.get("well_name") or ""
        result.operator = data.get("operator") or ""

        for day_data in data.get("days", []):
            day = self._build_dwr_day_from_ai(day_data)
            if day is not None:
                result.days.append(day)

        result.days.sort(key=lambda d: d.work_date)
        for idx, day in enumerate(result.days, start=1):
            day.day_number = idx
        result.total_days = len(result.days)

        infer_placement_methods(result.days)

        return result

    def _build_dwr_day_from_ai(self, day_data: Dict[str, Any]) -> Optional[DWRDay]:
        """Construct a DWRDay from AI-extracted dict."""
        raw_date = day_data.get("work_date")
        work_date = self._parse_date(str(raw_date)) if raw_date else None
        if work_date is None:
            logger.warning("DWRParser: AI day missing work_date: %s", day_data)
            return None

        events: List[DWREvent] = []
        for ev_data in day_data.get("events", []):
            event = self._build_dwr_event_from_ai(ev_data)
            if event:
                events.append(event)

        return DWRDay(
            work_date=work_date,
            day_number=day_data.get("day_number", 1),
            events=events,
            daily_narrative=day_data.get("daily_narrative", ""),
            rig_name=day_data.get("rig_name"),
            weather=day_data.get("weather"),
            crew_size=day_data.get("crew_size"),
        )

    def _build_dwr_event_from_ai(self, ev_data: Dict[str, Any]) -> Optional[DWREvent]:
        """Construct a DWREvent from AI-extracted dict."""
        event_type = ev_data.get("event_type", "")
        valid_types = {k for k in PLUG_EVENT_KEYWORDS}
        if event_type not in valid_types:
            event_type = "other"  # catch-all for unrecognized types

        start_t = self._parse_time(str(ev_data["start_time"])) if ev_data.get("start_time") else None
        end_t = self._parse_time(str(ev_data["end_time"])) if ev_data.get("end_time") else None

        cement_class_raw = ev_data.get("cement_class")
        cement_class = cement_class_raw.upper() if cement_class_raw else None
        if cement_class not in ("A", "B", "C", "G", "H"):
            cement_class = None

        return DWREvent(
            event_type=event_type,
            description=ev_data.get("description", ""),
            start_time=start_t,
            end_time=end_t,
            depth_top_ft=_safe_float(ev_data.get("depth_top_ft")),
            depth_bottom_ft=_safe_float(ev_data.get("depth_bottom_ft")),
            tagged_depth_ft=_safe_float(ev_data.get("tagged_depth_ft")),
            cement_class=cement_class,
            sacks=_safe_float(ev_data.get("sacks")),
            volume_bbl=_safe_float(ev_data.get("volume_bbl")),
            pressure_psi=_safe_float(ev_data.get("pressure_psi")),
            plug_number=ev_data.get("plug_number"),
            casing_string=ev_data.get("casing_string"),
            placement_method=ev_data.get("placement_method"),
            woc_hours=_safe_float(ev_data.get("woc_hours")),
        )

    # ------------------------------------------------------------------
    # Text Extraction
    # ------------------------------------------------------------------

    def _extract_text_from_pdf(self, pdf_path: str | Path) -> str:
        """Extract text from PDF using PyMuPDF with pdfplumber fallback."""
        pdf_path = Path(pdf_path)
        text_parts: List[str] = []

        # Try PyMuPDF (fitz) first
        try:
            if self._fitz is None:
                import fitz as _fitz
                self._fitz = _fitz
            doc = self._fitz.open(str(pdf_path))
            for page in doc:
                t = page.get_text() or ""
                if t.strip():
                    text_parts.append(t)
            doc.close()
        except ImportError:
            logger.warning("PyMuPDF (fitz) not installed; trying pdfplumber")
        except Exception as exc:
            logger.warning("DWRParser._extract_text_from_pdf: fitz failed: %s", exc)

        # Fall back to pdfplumber if fitz yielded nothing
        if not text_parts:
            try:
                import pdfplumber
                with pdfplumber.open(str(pdf_path)) as pdf:
                    for page in pdf.pages:
                        t = page.extract_text() or ""
                        if t.strip():
                            text_parts.append(t)
            except Exception as exc:
                logger.warning("DWRParser._extract_text_from_pdf: pdfplumber failed: %s", exc)

        return "\n\n".join(text_parts).strip()

    # ------------------------------------------------------------------
    # Event Detection
    # ------------------------------------------------------------------

    # Boilerplate phrases from JMR ticket disclaimers/footers — never operational
    _BOILERPLATE_PHRASES = [
        'services have been provided subject to',
        'customer agrees to pay all invoices',
        'shall not be liable for damages',
        'disputed specifying reason therefor',
        'expenses of collection including court costs',
        'payable in midland',
        'receipt of which is hereby acknowledged',
        'customer representative',
        'jmr representative',
    ]

    def _detect_events_from_text(self, text: str) -> List[DWREvent]:
        """Detect plug-related events from narrative text using keyword matching."""
        events: List[DWREvent] = []
        lower = text.lower()

        # Split into sentence-like chunks for line-level matching
        lines = re.split(r'[\n\r]+|[.;]', text)
        for line in lines:
            stripped = line.strip()
            if len(stripped) < 5:
                continue
            lower_stripped = stripped.lower()
            # Skip boilerplate/disclaimer text
            if any(bp in lower_stripped for bp in self._BOILERPLATE_PHRASES):
                continue
            event_type = self._classify_event_type(lower_stripped)
            if event_type is None:
                continue

            # Try range extraction first (from X to Y)
            range_top, range_bottom = self._extract_depth_range(stripped)

            event = DWREvent(
                event_type=event_type,
                description=stripped[:200],
                raw_text=stripped,
                depth_top_ft=range_top if range_top is not None else self._extract_depth(stripped),
                depth_bottom_ft=range_bottom,
                tagged_depth_ft=self._extract_tagged_depth(stripped),
                cement_class=self._extract_cement_class(stripped),
                sacks=self._extract_sacks(stripped),
                pressure_psi=self._extract_pressure(stripped),
            )

            # Attempt to parse time ranges like "08:00 - 10:00" or "0800-1000"
            event.start_time, event.end_time = self._extract_time_range(stripped)

            events.append(event)

        return events

    def _classify_event_type(self, lower_line: str) -> Optional[str]:
        """Return the best-matching event type for a line of text."""
        for event_type in _EVENT_TYPE_PRIORITY:
            keywords = PLUG_EVENT_KEYWORDS[event_type]
            for kw in keywords:
                if kw in lower_line:
                    return event_type
        return None

    # ------------------------------------------------------------------
    # Extraction Helpers
    # ------------------------------------------------------------------

    def _extract_depth(self, text: str) -> Optional[float]:
        """Extract primary depth value from text.

        Handles: '7,050 ft', "7050'", '7050 feet', '@ 7050'
        """
        # Prefer depth with context: "at X ft", "to X ft", "@ X"
        patterns = [
            r"(?:at|to|@|depth[:\s]+)\s*([\d,]+)\s*(?:ft|feet|['\u2018\u2019′])?",
            r"([\d,]+)\s*(?:ft|feet|['\u2018\u2019′])\b",
            r"\((\d[\d,]*)['′\u2018\u2019]?\)",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return _parse_depth_str(m.group(1))
        return None

    def _extract_depth_range(self, text: str):
        """Extract from/to depth range like 'From (7020') to (6777')'.

        Returns (top_ft, bottom_ft) tuple or (None, None) if no match.
        The shallower (smaller) value becomes top_ft, deeper becomes bottom_ft.
        """
        m = re.search(
            r"[Ff]rom\s*\(?(\d[\d,]*)['′\u2018\u2019]?\)?\s*[Tt]o\s*\(?(\d[\d,]*)['′\u2018\u2019]?\)?",
            text, re.IGNORECASE
        )
        if m:
            val1 = _parse_depth_str(m.group(1))
            val2 = _parse_depth_str(m.group(2))
            if val1 is not None and val2 is not None:
                return (min(val1, val2), max(val1, val2))
        return (None, None)

    def _extract_tagged_depth(self, text: str) -> Optional[float]:
        """Extract tagged/measured depth for TOC operations."""
        patterns = [
            r"tag(?:ged)?\s+(?:at|to|@)?\s*([\d,]+)\s*(?:ft|feet|['\u2018\u2019′])?",
            r"measured\s+(?:top|toc)\s+(?:at|@)?\s*([\d,]+)\s*(?:ft|feet|['\u2018\u2019′])?",
            r"toc\s+(?:at|@)?\s*([\d,]+)\s*(?:ft|feet|['\u2018\u2019′])?",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return _parse_depth_str(m.group(1))
        return None

    def _extract_sacks(self, text: str) -> Optional[float]:
        """Extract sack count from text like '45 sacks', '45 sx', '45 sks'."""
        m = re.search(
            r'([\d,]+(?:\.\d+)?)\s*(?:sacks?|sxs|sx|sks)\b',
            text, re.IGNORECASE
        )
        if m:
            return _safe_float(m.group(1).replace(",", ""))
        return None

    def _extract_pressure(self, text: str) -> Optional[float]:
        """Extract pressure from text like '1500 psi', 'test to 1500'."""
        m = re.search(
            r'([\d,]+(?:\.\d+)?)\s*psi\b',
            text, re.IGNORECASE
        )
        if m:
            return _safe_float(m.group(1).replace(",", ""))
        # "test to XXXX" pattern (no unit)
        m = re.search(r'test\s+to\s+([\d,]+)', text, re.IGNORECASE)
        if m:
            return _safe_float(m.group(1).replace(",", ""))
        return None

    def _extract_cement_class(self, text: str) -> Optional[str]:
        """Extract cement class from text like 'Class H', 'Class C', 'class g'."""
        m = re.search(r'\bclass\s+\(?([ABCGH])\)?', text, re.IGNORECASE)
        if m:
            return m.group(1).upper()
        return None

    def _extract_api_from_header(self, text: str) -> str:
        """Extract API number from document header."""
        # Match XX-XXX-XXXXX or XXXXXXXXXX formats
        m = re.search(r'\b(\d{2}-\d{3}-\d{5})\b', text[:3000])
        if m:
            return m.group(1)
        m = re.search(r'\bapi[#:\s]+(\d[\d\-]+)', text[:3000], re.IGNORECASE)
        if m:
            return m.group(1)
        return ""

    def _extract_header_field(self, text: str, pattern: str, field_name: str) -> str:
        """Extract a labelled header field using regex."""
        m = re.search(pattern, text[:3000], re.IGNORECASE)
        if m:
            return m.group(1).strip()[:100]
        return ""

    def _extract_first_date(self, text: str) -> Optional[date]:
        """Find the first date-like string in a block of text."""
        date_patterns = [
            r'\b(\d{1,2}/\d{1,2}/\d{2,4})\b',
            r'\b(\d{4}-\d{2}-\d{2})\b',
            r'\b(\w+ \d{1,2},?\s+\d{4})\b',
        ]
        for pat in date_patterns:
            m = re.search(pat, text)
            if m:
                parsed = self._parse_date(m.group(1))
                if parsed:
                    return parsed
        return None

    def _extract_time_range(self, text: str) -> Tuple[Optional[time], Optional[time]]:
        """Extract start/end time from text like '0800-1000' or '08:00 to 10:00'."""
        m = re.search(
            r'(\d{1,2}[:h]?\d{2})\s*(?:-|to)\s*(\d{1,2}[:h]?\d{2})',
            text, re.IGNORECASE
        )
        if m:
            return self._parse_time(m.group(1)), self._parse_time(m.group(2))
        m = re.search(r'\b(\d{1,2}[:h]\d{2})\b', text, re.IGNORECASE)
        if m:
            return self._parse_time(m.group(1)), None
        return None, None

    # ------------------------------------------------------------------
    # Date / Time Parsers
    # ------------------------------------------------------------------

    def _parse_date(self, text: str) -> Optional[date]:
        """Parse date from various formats."""
        if not text:
            return None
        text = text.strip()
        formats = [
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%m/%d/%y",
            "%d/%m/%Y",
            "%B %d, %Y",
            "%B %d %Y",
            "%b %d, %Y",
            "%b %d %Y",
        ]
        for fmt in formats:
            try:
                from datetime import datetime as _dt
                return _dt.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    def _parse_time(self, text: str) -> Optional[time]:
        """Parse time from various formats (HH:MM, HHMM, HHhMM)."""
        if not text:
            return None
        text = text.strip()
        # Normalize separators
        normalized = re.sub(r'[h:]', ':', text)
        # 4-digit no separator: 0800 → 08:00
        if re.match(r'^\d{4}$', normalized):
            normalized = f"{normalized[:2]}:{normalized[2:]}"
        try:
            from datetime import datetime as _dt
            return _dt.strptime(normalized, "%H:%M").time()
        except ValueError:
            pass
        return None

    # ------------------------------------------------------------------
    # Narrative Builder
    # ------------------------------------------------------------------

    def _build_daily_narrative(self, events: List[DWREvent]) -> str:
        """Build readable daily narrative from events list."""
        if not events:
            return ""
        lines: List[str] = []
        for ev in events:
            parts = [ev.description or ev.event_type.replace("_", " ").title()]
            if ev.depth_top_ft is not None:
                parts.append(f"@ {ev.depth_top_ft:,.0f} ft")
            if ev.tagged_depth_ft is not None:
                parts.append(f"(tagged {ev.tagged_depth_ft:,.0f} ft)")
            if ev.sacks is not None:
                parts.append(f"{ev.sacks:g} sks")
            if ev.cement_class:
                parts.append(f"Class {ev.cement_class}")
            if ev.pressure_psi is not None:
                parts.append(f"{ev.pressure_psi:,.0f} psi")
            lines.append(" ".join(parts))
        return ". ".join(lines)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _safe_float(value: Any) -> Optional[float]:
    """Convert value to float, return None on failure."""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip().rstrip("'")
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_depth_str(raw: str) -> Optional[float]:
    """Parse a depth string that may include commas and prime notation."""
    cleaned = raw.replace(",", "").strip().rstrip("'")
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None
