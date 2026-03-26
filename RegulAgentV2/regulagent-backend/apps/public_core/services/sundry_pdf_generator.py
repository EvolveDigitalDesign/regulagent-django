"""
BLM Sundry 3160-5 PDF Generator Service

Opens the annotated BLM 3160-5 PDF template (AcroForm widgets) and fills named
widgets to produce a completed Sundry Notice and Report on Wells form.

Template: docs/reference_photos/BLM 3160-5 Template With Fields.pdf
  - 2-page Letter-size (612 x 792 pts)
  - Named AcroForm widgets covering all header, checkbox, remarks, and
    certification fields on page 1
  - Page 2 is static instructions text with no fillable fields
  - Built by: python manage.py build_sundry_template

Entry point:
    generate_sundry_pdf(c103_form_data: dict) -> dict

Dev utility:
    draw_coordinate_grid(output_path=None) -> str
"""

from __future__ import annotations

import datetime
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False
    logger.warning(
        "PyMuPDF (fitz) not installed. Sundry PDF generation will be unavailable. "
        "Install with: pip install pymupdf"
    )

# ---------------------------------------------------------------------------
# Blank template path — used only by draw_coordinate_grid() dev utility
# ---------------------------------------------------------------------------
_BLANK_TEMPLATE_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "reference_photos"
    / "BLM 3160-5 Blank Template.pdf"
)


# ===========================================================================
# Custom exception
# ===========================================================================

class SundryPDFGeneratorError(Exception):
    """Raised when BLM Sundry 3160-5 PDF generation fails."""
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


# ===========================================================================
# Widget-based fill functions
# ===========================================================================

def _build_field_values(c103_form_data: dict) -> Dict[str, str]:
    """
    Transform structured C-103 form data into a flat {widget_name: value} dict
    for BLM 3160-5 AcroForm widgets.

    Args:
        c103_form_data: Dictionary from C103FormORM.plan_data with keys:
            header, submission_type, action_type, remarks, certification,
            and optional indian_tribe, ca_agreement.

    Returns:
        field_values dict mapping sundry widget names to string values.
        Checkbox fields are set to "Yes" when they should be checked.
    """
    header = c103_form_data.get("header", {})
    submission_type = _safe_str(c103_form_data.get("submission_type"))
    action_type = _safe_str(c103_form_data.get("action_type"))
    remarks = _safe_str(c103_form_data.get("remarks"))
    certification = c103_form_data.get("certification", {})

    fv: Dict[str, str] = {}

    # --- Right-block header fields ---
    fv["lease_serial_no"] = _safe_str(header.get("lease_serial"))

    indian_tribe = _safe_str(
        header.get("indian_tribe") or c103_form_data.get("indian_tribe")
    )
    fv["indian_tribe"] = indian_tribe

    ca_agreement = _safe_str(
        header.get("ca_agreement") or c103_form_data.get("ca_agreement")
    )
    fv["ca_agreement"] = ca_agreement

    # Well name: combine well_name and well_number if present
    well_name = _safe_str(header.get("well_name"))
    well_number = _safe_str(header.get("well_number"))
    if well_number:
        fv["well_name"] = f"{well_name} #{well_number}" if well_name else well_number
    else:
        fv["well_name"] = well_name

    fv["api_well_no"] = _safe_str(header.get("api_number"))
    fv["field_pool"] = _safe_str(header.get("field_pool"))

    county = _safe_str(header.get("county"))
    state = _safe_str(header.get("state"))
    if county and state:
        fv["county_state"] = f"{county}, {state}"
    elif county:
        fv["county_state"] = county
    else:
        fv["county_state"] = state

    # --- Left-block header fields ---
    fv["operator_name"] = _safe_str(header.get("operator"))
    fv["address"] = _safe_str(header.get("operator_address"))
    fv["phone"] = _safe_str(header.get("phone"))
    fv["location"] = _safe_str(header.get("location"))

    # --- Well type checkboxes (Field 1) ---
    well_type_raw = (header.get("well_type") or "").lower()
    if "oil" in well_type_raw:
        fv["well_type_oil"] = "Yes"
    elif "gas" in well_type_raw:
        fv["well_type_gas"] = "Yes"
    else:
        fv["well_type_other_cb"] = "Yes"
        fv["well_type_other_text"] = _safe_str(header.get("well_type"))

    # --- Submission type checkboxes (Field 12, left column) ---
    submission_map = {
        "notice_of_intent":    "sub_notice_of_intent",
        "subsequent_report":   "sub_subsequent_report",
        "final_abandonment":   "sub_final_abandonment",
    }
    sub_widget = submission_map.get(submission_type)
    if sub_widget:
        fv[sub_widget] = "Yes"

    # --- Action type checkboxes (Field 12, right columns) ---
    action_map = {
        "acidize":              "act_acidize",
        "alter_casing":         "act_alter_casing",
        "casing_repair":        "act_casing_repair",
        "change_plans":         "act_change_plans",
        "convert_injection":    "act_convert_injection",
        "deepen":               "act_deepen",
        "fracture_treat":       "act_fracture_treat",
        "new_construction":     "act_new_construction",
        "plug_abandon":         "act_plug_abandon",
        "plug_back":            "act_plug_back",
        "production":           "act_production",
        "reclamation":          "act_reclamation",
        "recomplete":           "act_recomplete",
        "temp_abandon":         "act_temp_abandon",
        "water_disposal":       "act_water_disposal",
        "water_shutoff":        "act_water_shutoff",
        "well_integrity":       "act_well_integrity",
        "other":                "act_other",
    }
    act_widget = action_map.get(action_type)
    if act_widget:
        fv[act_widget] = "Yes"
    elif action_type:
        # Unknown action type → check "Other" and fill the text field
        fv["act_other"] = "Yes"
        fv["act_other_text"] = action_type

    # --- Remarks (Field 13) ---
    if remarks:
        fv["remarks"] = remarks

    # --- Certification (Field 14) ---
    fv["cert_name"] = _safe_str(certification.get("name"))
    fv["cert_title"] = _safe_str(certification.get("title"))
    fv["cert_date"] = _safe_str(certification.get("date"))

    return fv


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
# Steps page
# ===========================================================================

def _render_steps_page(doc: "fitz.Document", steps_content: str, header: dict) -> None:
    """
    Append one or more Letter-size pages with a header block and the plug procedure steps.

    Uses line-by-line rendering to handle multi-page content correctly,
    since PyMuPDF's insert_textbox renders nothing when text overflows.

    Args:
        doc: Open PyMuPDF document to append to.
        steps_content: Multi-line text with plug summary and remarks.
        header: Dict with keys api_number, well_name, well_number, operator.
    """
    margin = 54  # 0.75 inch
    page_width = 612
    page_height = 792
    max_y = page_height - margin
    body_fontname = "cour"
    body_fontsize = 9
    line_height = body_fontsize + 3  # 12pt line spacing for 9pt font
    center_x = page_width / 2

    # --- Build header info ---
    title = "P&A NOI"
    operator = _safe_str(header.get("operator"))
    well_name = _safe_str(header.get("well_name"))
    well_number = _safe_str(header.get("well_number"))
    api_number = _safe_str(header.get("api_number"))

    well_display = well_name
    if well_number:
        well_display = f"{well_name} #{well_number}" if well_name else well_number

    header_lines = [title]
    if operator:
        header_lines.append(operator)
    if well_display:
        header_lines.append(well_display)
    if api_number:
        header_lines.append(f"API: {api_number}")

    # --- Word-wrap body text to fit page width ---
    body_width = page_width - 2 * margin
    char_width = fitz.get_text_length("M", fontname=body_fontname, fontsize=body_fontsize)
    max_chars = int(body_width / char_width) if char_width > 0 else 80

    wrapped_lines = []
    for raw_line in steps_content.split("\n"):
        if len(raw_line) <= max_chars:
            wrapped_lines.append(raw_line)
        else:
            # Word-wrap long lines
            while len(raw_line) > max_chars:
                # Find last space before max_chars
                break_at = raw_line.rfind(" ", 0, max_chars)
                if break_at <= 0:
                    break_at = max_chars  # Force break if no space
                wrapped_lines.append(raw_line[:break_at])
                raw_line = raw_line[break_at:].lstrip()
            if raw_line:
                wrapped_lines.append(raw_line)

    # --- Render pages ---
    page = None
    y = max_y  # Force new page on first iteration
    is_first_page = True

    for line in wrapped_lines:
        # Check if we need a new page
        if y + line_height > max_y or page is None:
            page = doc.new_page(width=page_width, height=page_height)
            y = margin

            if is_first_page:
                # Draw header lines centered on first page only
                for i, hl in enumerate(header_lines):
                    fontsize = 14 if i == 0 else 11
                    fontname = "helv"
                    tw = fitz.get_text_length(hl, fontname=fontname, fontsize=fontsize)
                    x = center_x - tw / 2
                    page.insert_text(fitz.Point(x, y), hl, fontname=fontname, fontsize=fontsize)
                    y += fontsize + 6
                y += 12  # gap between header and body
                is_first_page = False

        # Render this line
        page.insert_text(
            fitz.Point(margin, y),
            line,
            fontname=body_fontname,
            fontsize=body_fontsize,
        )
        y += line_height


# ===========================================================================
# Main entry point
# ===========================================================================

def generate_sundry_pdf(c103_form_data: dict, wbd_image_path: str = "") -> Dict[str, Any]:
    """
    Generate a completed BLM 3160-5 Sundry PDF from C-103 form data.

    Opens the annotated sundry template (AcroForm widgets), fills each named
    widget, saves the output to MEDIA_ROOT/temp_pdfs/, and returns metadata
    about the file.

    Args:
        c103_form_data: Dictionary from C103FormORM.plan_data with keys:
            header, submission_type, action_type, remarks, certification

    Returns:
        {
            "temp_path":      str  — absolute path to the generated PDF,
            "file_size":      int  — file size in bytes,
            "page_count":     int  — number of pages (always 2 for BLM 3160-5),
            "api_number":     str  — API number from header,
            "ttl_expires_at": str  — ISO-8601 expiry timestamp (UTC + 24h),
        }

    Raises:
        SundryPDFGeneratorError: If PyMuPDF is not available or generation fails.
    """
    if not HAS_FITZ:
        raise SundryPDFGeneratorError(
            "PyMuPDF (fitz) is not installed. "
            "Install with: pip install pymupdf"
        )

    from apps.public_core.services.sundry_template_builder import ANNOTATED_TEMPLATE_PATH

    if not ANNOTATED_TEMPLATE_PATH.exists():
        raise SundryPDFGeneratorError(
            f"Annotated Sundry template not found at: {ANNOTATED_TEMPLATE_PATH}. "
            f"Run: python manage.py build_sundry_template"
        )

    header = c103_form_data.get("header", {})
    api_number = _safe_str(header.get("api_number"))

    logger.info(
        "Generating Sundry 3160-5 PDF for API %s (submission_type=%s, action_type=%s)",
        api_number,
        c103_form_data.get("submission_type", ""),
        c103_form_data.get("action_type", ""),
    )

    try:
        doc = fitz.open(str(ANNOTATED_TEMPLATE_PATH))

        if doc.page_count < 2:
            raise SundryPDFGeneratorError(
                f"Template has {doc.page_count} page(s); expected 2."
            )

        field_values = _build_field_values(c103_form_data)

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
        filename = f"sundry_{safe_api}_{ts}.pdf"
        temp_path = os.path.join(temp_dir, filename)

        # --- Append steps page if content exists ---
        steps_content = c103_form_data.get("steps_content", "")
        if steps_content:
            _render_steps_page(doc, steps_content, header)

        # --- Append wellbore diagram page if image exists ---
        if wbd_image_path and os.path.isfile(wbd_image_path):
            try:
                wbd_page = doc.new_page(width=612, height=792)  # Letter size
                margin = 36  # 0.5 inch
                img_rect = fitz.Rect(margin, margin, 612 - margin, 792 - margin)
                wbd_page.insert_image(img_rect, filename=wbd_image_path)
                logger.info("Sundry PDF: appended WBD diagram page from %s", wbd_image_path)
            except Exception as wbd_err:
                logger.warning("Sundry PDF: failed to append WBD page (non-fatal): %s", wbd_err)

        final_page_count = doc.page_count
        doc.save(temp_path, garbage=4, deflate=True)
        doc.close()

        file_size = os.path.getsize(temp_path)
        page_count = final_page_count
        expires_at = (
            datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        ).isoformat() + "Z"

        logger.info(
            "Sundry 3160-5 PDF saved: %s (%.1f KB)",
            temp_path, file_size / 1024,
        )

        return {
            "temp_path":      temp_path,
            "file_size":      file_size,
            "page_count":     page_count,
            "api_number":     api_number,
            "ttl_expires_at": expires_at,
        }

    except SundryPDFGeneratorError:
        raise
    except Exception as exc:
        logger.error("Sundry 3160-5 PDF generation failed: %s", exc, exc_info=True)
        raise SundryPDFGeneratorError(f"Failed to generate Sundry 3160-5 PDF: {exc}") from exc


# ===========================================================================
# Dev utility: coordinate grid overlay
# ===========================================================================

def draw_coordinate_grid(output_path: Optional[str] = None) -> str:
    """
    Render a coordinate grid overlay on top of the blank BLM 3160-5 template
    for visual calibration purposes.

    Grid lines:
        - Every 50 pts — blue, labeled with coordinate value
        - Every 10 pts — light grey (no label)

    Args:
        output_path: Where to write the annotated PDF.
                     Defaults to MEDIA_ROOT/temp_pdfs/sundry_grid_<ts>.pdf.

    Returns:
        Absolute path to the generated grid PDF.

    Raises:
        SundryPDFGeneratorError: If PyMuPDF is unavailable or template missing.
    """
    if not HAS_FITZ:
        raise SundryPDFGeneratorError("PyMuPDF (fitz) is not installed.")

    if not _BLANK_TEMPLATE_PATH.exists():
        raise SundryPDFGeneratorError(
            f"BLM 3160-5 blank template not found: {_BLANK_TEMPLATE_PATH}"
        )

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
        output_path = os.path.join(temp_dir, f"sundry_grid_{int(time.time())}.pdf")

    doc.save(output_path)
    doc.close()

    logger.info("Coordinate grid overlay saved: %s", output_path)
    return output_path
