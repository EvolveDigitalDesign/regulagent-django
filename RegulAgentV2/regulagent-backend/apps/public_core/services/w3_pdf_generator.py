"""
W-3 PDF Generator Service

Opens the annotated W-3 PDF template (AcroForm widgets) and fills named
widgets to produce a completed, RRC-compliant W-3 form PDF.

Template: docs/reference_photos/W3 Template With Fields.pdf
  - 2-page Letter-size (612 x 792 pts)
  - 162 named AcroForm widgets covering all header, plug, casing, perf,
    page-2, and remarks fields
  - Built by: python manage.py build_w3_template

Entry point:
    generate_w3_pdf(w3_form_data: dict) -> dict

Dev utility:
    draw_coordinate_grid(output_path=None) -> str
"""

from __future__ import annotations

import datetime
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False
    logger.warning(
        "PyMuPDF (fitz) not installed. W-3 PDF generation will be unavailable. "
        "Install with: pip install pymupdf"
    )

# ---------------------------------------------------------------------------
# Blank template path — used only by draw_coordinate_grid() dev utility
# ---------------------------------------------------------------------------
_BLANK_TEMPLATE_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "reference_photos"
    / "W3 Blank Template.pdf"
)

# ---------------------------------------------------------------------------
# Cement yield table (cf / sack)  — API RP 65
# ---------------------------------------------------------------------------
_CEMENT_YIELD_CF_PER_SACK: Dict[str, float] = {
    "A": 1.15,
    "B": 1.15,
    "C": 1.35,
    "G": 1.15,
    "H": 1.19,
}
_DEFAULT_YIELD_CF_PER_SACK = 1.35  # Class C default


# ===========================================================================
# Custom exception
# ===========================================================================

class W3PDFGeneratorError(Exception):
    """Raised when W-3 PDF generation fails."""
    pass


# ===========================================================================
# Utility helpers
# ===========================================================================

def _safe_str(value: Any, fmt: Optional[str] = None) -> str:
    """
    None-safe value → string formatter.

    - Returns "" for None or empty string.
    - Formats floats: drops trailing ".0" when the value is a whole number.
    - Respects an optional Python format string (e.g. "{:.1f}").
    """
    if value is None or value == "":
        return ""
    if fmt is not None:
        try:
            return fmt.format(value)
        except (ValueError, TypeError):
            return str(value)
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return str(value)
    return str(value)


def _slurry_volume_cf(sacks: Optional[float], cement_class: Optional[str]) -> str:
    """Return slurry volume in cf as a formatted string, or '' if inputs missing."""
    if sacks is None:
        return ""
    yield_cf = _CEMENT_YIELD_CF_PER_SACK.get(
        (cement_class or "").upper(), _DEFAULT_YIELD_CF_PER_SACK
    )
    vol = sacks * yield_cf
    if vol == int(vol):
        return str(int(vol))
    return f"{vol:.1f}"


def _strip_42_prefix(api_number: Optional[str]) -> str:
    """
    The W-3 template pre-prints '42-' for Texas API numbers.
    Strip that prefix so we don't double-print it.
    """
    s = _safe_str(api_number)
    # Handle formats: "42-501-70575", "42501070575", "42-501-70575-00-00"
    if s.startswith("42-"):
        return s[3:]
    if s.startswith("42") and len(s) > 2:
        return s[2:]
    return s


# ===========================================================================
# Widget-based fill functions
# ===========================================================================

def _build_field_values(w3_form_data: dict) -> tuple[Dict[str, str], str]:
    """
    Transform structured W-3 form data into a flat {widget_name: value} dict.

    Returns:
        (field_values, overflow_remarks) — field_values for widget fill,
        overflow_remarks for plugs 9+ that don't fit on the form.
    """
    header = w3_form_data.get("header", {})
    plugs = w3_form_data.get("plugs", [])
    casing_record = w3_form_data.get("casing_record", [])
    perforations = w3_form_data.get("perforations", [])
    duqw = w3_form_data.get("duqw") or {}

    fv: Dict[str, str] = {}

    # --- Header fields ---
    fv["api_number"] = _strip_42_prefix(header.get("api_number"))
    fv["rrc_district"] = _safe_str(header.get("rrc_district"))
    fv["rrc_lease_id"] = _safe_str(header.get("rrc_lease_id"))
    fv["field_name"] = _safe_str(header.get("field_name"))
    fv["lease_name"] = _safe_str(header.get("lease_name"))
    fv["well_number"] = _safe_str(header.get("well_number"))
    fv["operator"] = _safe_str(header.get("operator"))
    fv["original_w1_operator"] = _safe_str(header.get("original_w1_operator"))
    fv["county"] = _safe_str(header.get("county"))
    fv["operator_address"] = _safe_str(header.get("operator_address"))
    fv["subsequent_w1_operator"] = _safe_str(header.get("subsequent_w1_operator"))
    fv["drilling_permit_date"] = _safe_str(header.get("drilling_permit_date"))
    fv["feet_from_line1"] = _safe_str(header.get("feet_from_line1"))
    fv["feet_from_line2"] = _safe_str(header.get("feet_from_line2"))
    fv["permit_number"] = _safe_str(header.get("permit_number"))
    fv["section_block_survey"] = _safe_str(header.get("section_block_survey"))
    fv["direction_from_town"] = _safe_str(header.get("direction_from_town"))
    fv["drilling_commenced"] = _safe_str(header.get("drilling_commenced"))
    fv["well_type"] = _safe_str(header.get("well_type"))
    fv["total_depth"] = _safe_str(header.get("total_depth"))
    fv["drilling_completed"] = _safe_str(header.get("drilling_completed"))
    fv["condensate_on_hand_p1"] = _safe_str(header.get("condensate_on_hand"))
    fv["date_well_plugged_p1"] = _safe_str(header.get("date_well_plugged"))

    # --- Plug table (up to 8 on form) ---
    MAX_PLUGS_ON_FORM = 8
    on_form = plugs[:MAX_PLUGS_ON_FORM]
    overflow = plugs[MAX_PLUGS_ON_FORM:]

    for col_idx, plug in enumerate(on_form, start=1):
        prefix = f"plug_{col_idx}_"
        fv[prefix + "cementing_date"] = _safe_str(plug.get("cementing_date"))
        fv[prefix + "hole_size_in"] = _safe_str(plug.get("hole_size_in"))
        fv[prefix + "depth_bottom_ft"] = _safe_str(plug.get("depth_bottom_ft"))
        fv[prefix + "sacks"] = _safe_str(plug.get("sacks"))
        fv[prefix + "slurry_volume_cf"] = _slurry_volume_cf(plug.get("sacks"), plug.get("cement_class"))
        fv[prefix + "calculated_top_of_plug"] = _safe_str(plug.get("calculated_top_of_plug_ft"))
        fv[prefix + "measured_top_of_plug"] = _safe_str(plug.get("measured_top_of_plug_ft"))
        fv[prefix + "slurry_weight_ppg"] = _safe_str(plug.get("slurry_weight_ppg"))
        fv[prefix + "cement_class"] = _safe_str(plug.get("cement_class"))

    # --- Build overflow remarks for plugs beyond 8 ---
    overflow_lines: List[str] = []
    if overflow:
        overflow_lines.append("ADDITIONAL PLUGS (continuation):")
        for plug in overflow:
            pnum = _safe_str(plug.get("plug_number"))
            bot = _safe_str(plug.get("depth_bottom_ft"))
            calc = _safe_str(plug.get("calculated_top_of_plug_ft"))
            meas = _safe_str(plug.get("measured_top_of_plug_ft"))
            sks = _safe_str(plug.get("sacks"))
            cls_ = _safe_str(plug.get("cement_class"))
            wt = _safe_str(plug.get("slurry_weight_ppg"))
            hs = _safe_str(plug.get("hole_size_in"))
            dt = _safe_str(plug.get("cementing_date"))
            vol = _slurry_volume_cf(plug.get("sacks"), plug.get("cement_class"))
            line = (
                f"Plug #{pnum}: Bot={bot}ft CalcTop={calc}ft MeasTop={meas}ft "
                f"Sks={sks} Class={cls_} Wt={wt}ppg Hole={hs}in Vol={vol}cf Date={dt}"
            )
            overflow_lines.append(line)
    overflow_remarks = "\n".join(overflow_lines)

    # --- Casing record (up to 6) ---
    for row_idx, casing in enumerate(casing_record[:6], start=1):
        prefix = f"casing_{row_idx}_"
        fv[prefix + "od_in"] = _safe_str(casing.get("od_in"))
        fv[prefix + "weight_ppf"] = _safe_str(casing.get("weight_ppf"))
        fv[prefix + "top_ft"] = _safe_str(casing.get("top_ft"))
        fv[prefix + "bottom_ft"] = _safe_str(casing.get("bottom_ft"))
        fv[prefix + "hole_size_in"] = _safe_str(casing.get("hole_size_in"))

    # --- Perforations (up to 10) ---
    for idx, perf in enumerate(perforations[:10], start=1):
        from_val = _safe_str(perf.get("from_ft") or perf.get("interval_top_ft"))
        to_val = _safe_str(perf.get("to_ft") or perf.get("interval_bottom_ft"))
        fv[f"perf_{idx}_from_ft"] = from_val
        fv[f"perf_{idx}_to_ft"] = to_val

    # --- Page 2 checkboxes ---
    # Use "Yes" to indicate checked state; widget fill maps this to field_value = True
    mud_filled = header.get("mud_filled")
    if mud_filled is True:
        fv["mud_filled_yes"] = "Yes"
    elif mud_filled is False:
        fv["mud_filled_no"] = "Yes"

    all_plugged = header.get("all_wells_plugged")
    if all_plugged is True:
        fv["all_wells_plugged_yes"] = "Yes"
    elif all_plugged is False:
        fv["all_wells_plugged_no"] = "Yes"

    notice_given = header.get("notice_given")
    if notice_given is True:
        fv["notice_given_yes"] = "Yes"
    elif notice_given is False:
        fv["notice_given_no"] = "Yes"

    # --- Page 2 text fields ---
    fv["mud_application_method"] = _safe_str(header.get("mud_application_method"))
    fv["mud_weight_ppg"] = _safe_str(header.get("mud_weight_ppg"))
    fv["total_depth_p2"] = _safe_str(duqw.get("depth_ft"))
    fv["fresh_water_top"] = _safe_str(duqw.get("top_ft"))
    fv["fresh_water_bottom"] = _safe_str(duqw.get("bottom_ft"))
    fv["deepest_fresh_water"] = _safe_str(duqw.get("deepest_fresh_water"))
    fv["if_no_explain"] = _safe_str(header.get("if_no_explain"))
    fv["cementing_company"] = _safe_str(header.get("cementing_company"))
    fv["date_rrc_notified"] = _safe_str(header.get("date_rrc_notified"))
    fv["surface_owners"] = _safe_str(header.get("surface_owners"))

    return fv, overflow_remarks


def _fill_widgets(doc: "fitz.Document", field_values: Dict[str, str]) -> None:
    """
    Fill all AcroForm widgets in doc by matching field names to field_values.

    Each widget whose field_name appears in *field_values* gets its value set
    and ``widget.update()`` called to generate the appearance stream.
    """
    for page in doc:
        for widget in page.widgets():
            name = widget.field_name
            if name in field_values:
                value = field_values[name]
                if not value:
                    continue
                if widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                    widget.field_value = True
                else:
                    widget.field_value = value
                widget.update()


# ===========================================================================
# Main entry point
# ===========================================================================

def generate_w3_pdf(w3_form_data: dict, wbd_image_path: str = "") -> Dict[str, Any]:
    """
    Generate a completed W-3 PDF from structured form data.

    Opens the annotated W-3 template (AcroForm widgets), fills each named
    widget, saves the output to MEDIA_ROOT/temp_pdfs/, and returns metadata
    about the file.

    Args:
        w3_form_data: Dictionary produced by w3_builder.py with keys:
            header, plugs, casing_record, perforations, duqw, remarks

    Returns:
        {
            "temp_path":      str  — absolute path to the generated PDF,
            "file_size":      int  — file size in bytes,
            "page_count":     int  — number of pages (always 2 for W-3),
            "api_number":     str  — API number from header,
            "ttl_expires_at": str  — ISO-8601 expiry timestamp (UTC + 24h),
        }

    Raises:
        W3PDFGeneratorError: If PyMuPDF is not available or generation fails.
    """
    if not HAS_FITZ:
        raise W3PDFGeneratorError(
            "PyMuPDF (fitz) is not installed. "
            "Install with: pip install pymupdf"
        )

    from apps.public_core.services.w3_template_builder import ANNOTATED_TEMPLATE_PATH

    if not ANNOTATED_TEMPLATE_PATH.exists():
        raise W3PDFGeneratorError(
            f"Annotated W-3 template not found at: {ANNOTATED_TEMPLATE_PATH}. "
            f"Run: python manage.py build_w3_template"
        )

    header = w3_form_data.get("header", {})
    plugs = w3_form_data.get("plugs", [])
    casing_record = w3_form_data.get("casing_record", [])
    perforations = w3_form_data.get("perforations", [])
    remarks_base = w3_form_data.get("remarks", "") or ""

    api_number = _safe_str(header.get("api_number"))

    logger.info(
        "Generating W-3 PDF for API %s (%d plugs, %d casing strings, %d perforations)",
        api_number, len(plugs), len(casing_record), len(perforations),
    )

    try:
        doc = fitz.open(str(ANNOTATED_TEMPLATE_PATH))

        if doc.page_count < 2:
            raise W3PDFGeneratorError(
                f"Template has {doc.page_count} page(s); expected 2."
            )

        field_values, overflow_remarks = _build_field_values(w3_form_data)

        # Merge base remarks with any plug-overflow text
        full_remarks = remarks_base
        if overflow_remarks:
            full_remarks = (full_remarks + "\n" + overflow_remarks).strip() if full_remarks else overflow_remarks
        if full_remarks:
            field_values["remarks"] = full_remarks

        _fill_widgets(doc, field_values)

        # --- Persist to MEDIA_ROOT/temp_pdfs/ ---
        try:
            import django.conf
            media_root = getattr(django.conf.settings, "MEDIA_ROOT", None) or ""
        except Exception:
            media_root = ""

        if not media_root:
            import tempfile
            media_root = tempfile.gettempdir()

        temp_dir = os.path.join(media_root, "temp_pdfs")
        os.makedirs(temp_dir, exist_ok=True)

        ts = str(int(time.time()))
        safe_api = api_number.replace("/", "-").replace(" ", "_") if api_number else "unknown"
        filename = f"w3_{safe_api}_{ts}.pdf"
        temp_path = os.path.join(temp_dir, filename)

        # --- Append wellbore diagram page if image exists ---
        if wbd_image_path and os.path.isfile(wbd_image_path):
            try:
                wbd_page = doc.new_page(width=612, height=792)  # Letter size
                # Center image with margins
                margin = 36  # 0.5 inch
                img_rect = fitz.Rect(margin, margin, 612 - margin, 792 - margin)
                wbd_page.insert_image(img_rect, filename=wbd_image_path)
                logger.info("W-3 PDF: appended WBD diagram page from %s", wbd_image_path)
            except Exception as wbd_err:
                logger.warning("W-3 PDF: failed to append WBD page (non-fatal): %s", wbd_err)

        doc.save(temp_path, garbage=4, deflate=True)
        doc.close()

        file_size = os.path.getsize(temp_path)
        page_count = 2
        expires_at = (
            datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        ).isoformat() + "Z"

        logger.info(
            "W-3 PDF saved: %s (%.1f KB)",
            temp_path, file_size / 1024,
        )

        return {
            "temp_path":      temp_path,
            "file_size":      file_size,
            "page_count":     page_count,
            "api_number":     api_number,
            "ttl_expires_at": expires_at,
        }

    except W3PDFGeneratorError:
        raise
    except Exception as exc:
        logger.error("W-3 PDF generation failed: %s", exc, exc_info=True)
        raise W3PDFGeneratorError(f"Failed to generate W-3 PDF: {exc}") from exc


# ===========================================================================
# Dev utility: coordinate grid overlay
# ===========================================================================

def draw_coordinate_grid(output_path: Optional[str] = None) -> str:
    """
    Render a coordinate grid overlay on top of the blank W-3 template for
    visual calibration purposes.

    Grid lines:
        - Every 50 pts — blue, labeled with coordinate value
        - Every 10 pts — light grey (no label)

    Args:
        output_path: Where to write the annotated PDF.
                     Defaults to MEDIA_ROOT/temp_pdfs/w3_grid_<ts>.pdf.

    Returns:
        Absolute path to the generated grid PDF.

    Raises:
        W3PDFGeneratorError: If PyMuPDF is unavailable or template missing.
    """
    if not HAS_FITZ:
        raise W3PDFGeneratorError("PyMuPDF (fitz) is not installed.")

    if not _BLANK_TEMPLATE_PATH.exists():
        raise W3PDFGeneratorError(f"W-3 blank template not found: {_BLANK_TEMPLATE_PATH}")

    doc = fitz.open(str(_BLANK_TEMPLATE_PATH))

    BLUE = (0.0, 0.4, 0.8)
    GREY = (0.7, 0.7, 0.7)
    LABEL_FS = 5

    for page in doc:
        w = page.rect.width   # 612
        h = page.rect.height  # 792

        # Fine grid — every 10 pts
        for x in range(0, int(w) + 1, 10):
            page.draw_line(fitz.Point(x, 0), fitz.Point(x, h), color=GREY, width=0.3)
        for y in range(0, int(h) + 1, 10):
            page.draw_line(fitz.Point(0, y), fitz.Point(w, y), color=GREY, width=0.3)

        # Major grid — every 50 pts with labels
        for x in range(0, int(w) + 1, 50):
            page.draw_line(fitz.Point(x, 0), fitz.Point(x, h), color=BLUE, width=0.7)
            page.insert_text(fitz.Point(x + 1, 10), str(x), fontsize=LABEL_FS, color=BLUE)
        for y in range(0, int(h) + 1, 50):
            page.draw_line(fitz.Point(0, y), fitz.Point(w, y), color=BLUE, width=0.7)
            page.insert_text(fitz.Point(2, y - 1), str(y), fontsize=LABEL_FS, color=BLUE)

    # Determine output path
    if output_path is None:
        try:
            import django.conf
            media_root = getattr(django.conf.settings, "MEDIA_ROOT", None) or ""
        except Exception:
            media_root = ""
        if not media_root:
            import tempfile
            media_root = tempfile.gettempdir()
        temp_dir = os.path.join(media_root, "temp_pdfs")
        os.makedirs(temp_dir, exist_ok=True)
        output_path = os.path.join(temp_dir, f"w3_grid_{int(time.time())}.pdf")

    doc.save(output_path)
    doc.close()

    logger.info("Coordinate grid overlay saved: %s", output_path)
    return output_path
