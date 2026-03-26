"""
BLM 3160-5 (Sundry Notices and Reports on Wells) Template Builder.

Stamps named AcroForm widgets onto the flat blank BLM 3160-5 template so that
sundry_pdf_generator can fill fields by name instead of by (x, y) coordinate.

All coordinate definitions for the BLM 3160-5 form live exclusively in this file.
The stamping is intended to run once to produce
"BLM 3160-5 Template With Fields.pdf", which the PDF generator then fills
programmatically.

Usage (via Django management command):
    python manage.py build_sundry_template [--output PATH] [--verify]

Usage (direct):
    from apps.public_core.services.sundry_template_builder import (
        build_annotated_template,
        verify_template,
    )
    output = build_annotated_template()
    info   = verify_template()

Form structure (Letter-size 612 × 792 pts), Page 1 only — page 2 is static
instructions text with no fillable fields.

Grid boundaries extracted from PDF structural analysis:
  Left block:    x = 25 – 390
  Right block:   x = 390 – 590
  Vertical dividers: x=153 (submission/action split), x=245 (addr/phone),
                     x=296 (cert name/title), x=316 (approval columns),
                     x=460 (approval date/office)
  Horizontal row tops (left block):
    Header rows: 63, 84, 118/121, 138, 148, 167, 186, 212, 239
  Field 12 (checkboxes): y=259–343
  Field 13 (remarks): y=343–582
  Field 14 (cert): y=582–639
  Approval: y=643–717
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False
    logger.warning(
        "PyMuPDF (fitz) not installed. BLM 3160-5 template building will be "
        "unavailable. Install with: pip install pymupdf"
    )

__all__ = [
    "build_annotated_template",
    "verify_template",
    "BLANK_TEMPLATE_PATH",
    "ANNOTATED_TEMPLATE_PATH",
]

# ---------------------------------------------------------------------------
# Template paths — relative to this file, works in Docker
# ---------------------------------------------------------------------------

_BLANK_TEMPLATE_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "reference_photos"
    / "BLM 3160-5 Blank Template.pdf"
)

_ANNOTATED_TEMPLATE_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "reference_photos"
    / "BLM 3160-5 Template With Fields.pdf"
)

# Public aliases so callers can reference the paths without importing private names.
BLANK_TEMPLATE_PATH: Path = _BLANK_TEMPLATE_PATH
ANNOTATED_TEMPLATE_PATH: Path = _ANNOTATED_TEMPLATE_PATH

# ---------------------------------------------------------------------------
# Page 1 — Header field coordinates
#
# Form grid (from PDF structural analysis):
#   Left block x=25–390, Right block x=390–590
#   Row boundaries (horizontal lines):
#     y=63  → Field 5 (Lease Serial No.) top boundary (right block)
#     y=84  → Field 6 (Indian Tribe) top boundary (right block)
#     y=118 → bottom of header block / top of sub-header text
#     y=138 → Field 1 (Type of Well) label row top
#     y=148 → Field 7 (CA/Agreement) top boundary (right block)
#     y=167 → Row: Field 2 Operator / Field 8 Well Name
#     y=186 → Row: Field 3a Address / Field 9 API Well No.
#     y=212 → Row: Field 4 Location / Field 10 Field and Pool
#     y=239 → Row: Field 11 County or Parish (bottom of header section)
#
# Each entry: (x, y, width)
#   x = left edge of fillable area (after label text)
#   y = baseline for text (bottom of text line)
#   width = available width in points
#
# Rect formula: fitz.Rect(x - 2, y - fontsize, x + width, y + 2)
# ---------------------------------------------------------------------------

_P1_HEADER_FS = 9  # fontsize for header text fields

_P1_HEADER: Dict[str, tuple] = {
    #  field_name:               (x,   y,  width)
    #
    # Right block top rows (x=390–590, label starts at ~392)
    # Field 5: "5. Lease Serial No." — right block, row y=36–63
    "lease_serial_no":          (453,  58, 135),   # after "5. Lease Serial No." label
    # Field 6: "6. If Indian..." — right block, row y=63–84
    "indian_tribe":             (453,  79, 135),   # after label (label ends ~508 but narrow)
    # Field 7: "7. If Unit..." — right block, row y=121–148
    "ca_agreement":             (453, 143, 135),   # right block row y=121–148
    # Field 8: "8. Well Name and No." — right block, row y=148–167
    "well_name":                (453, 162, 135),   # right block row y=148–167
    # Field 9: "9. API Well No." — right block, row y=167–186
    "api_well_no":              (453, 181, 135),   # right block row y=167–186
    # Field 10: "10. Field and Pool..." — right block, row y=186–212
    "field_pool":               (453, 207, 135),   # right block row y=186–212
    # Field 11: "11. County or Parish..." — right block, row y=212–239
    "county_state":             (453, 234, 135),   # right block row y=212–239
    #
    # Left block rows (x=25–390)
    # Field 1 "Other" text  (row y=138–167, checkboxes at y=157)
    "well_type_other_text":     (235, 162,  80),   # text field after "Other" checkbox/label
    # Field 2: "2. Name of Operator" — left block, row y=167–186  (y=138–167 is type-of-well)
    "operator_name":            ( 27, 181, 215),   # left block row y=167–186 (label ends ~94)
    # Field 3a: "3a. Address" — left block, row y=186–212, left sub-column x=25–245
    "address":                  ( 27, 207, 214),   # left sub-column, row y=186–212
    # Field 3b: "Phone No." — left block, row y=186–212, right sub-column x=245–390
    "phone":                    (250, 207, 135),   # right sub-column (label ends ~361)
    # Field 4: "4. Location of Well..." — left block, row y=212–239
    "location":                 ( 27, 234, 360),   # left block row y=212–239
}

# ---------------------------------------------------------------------------
# Page 1 — Well type checkboxes (Field 1)
# These are at row y=138–167, label row starts at y=138
# Checkbox centers from PDF structural analysis:
#   Oil Well:  center=(66, 157)
#   Gas Well:  center=(124, 157)
#   Other:     center=(201, 157)
# Checkbox rect formula: fitz.Rect(cx - 6, cy - 6, cx + 6, cy + 6)
# ---------------------------------------------------------------------------

_P1_WELL_TYPE_CHECKBOXES: Dict[str, tuple] = {
    #  field_name:     (cx,   cy)   — center of 12×12 square
    "well_type_oil":  ( 66, 157),
    "well_type_gas":  (124, 157),
    "well_type_other_cb": (201, 157),
}

# ---------------------------------------------------------------------------
# Page 1 — Submission type checkboxes (Field 12, left column)
# From PDF structural analysis:
#   "TYPE OF SUBMISSION" label at y=262–272
#   Notice of Intent: center=(40, 286)
#   Subsequent Report: center=(40, 313)
#   Final Abandonment Notice: center=(40, 334)
# ---------------------------------------------------------------------------

_P1_SUBMISSION_CHECKBOXES: Dict[str, tuple] = {
    #  field_name:                  (cx,  cy)
    "sub_notice_of_intent":         ( 40, 286),
    "sub_subsequent_report":        ( 40, 313),
    "sub_final_abandonment":        ( 40, 334),
}

# ---------------------------------------------------------------------------
# Page 1 — Action type checkboxes (Field 12, right columns)
# 5 rows × 4 columns of checkboxes
# Column checkbox centers from PDF structural analysis:
#   Col A (x≈171): Acidize, Alter Casing, Casing Repair, Change Plans, Convert to Injection
#   Col B (x≈270): Deepen, Fracture Treat, New Construction, Plug and Abandon, Plug Back
#   Col C (x≈364): Production, Reclamation, Recomplete, Temporarily Abandon, Water Disposal
#   Col D (x≈479): Water Shut-Off, Well Integrity, Other (+ text), [no row 4/5 checkbox]
#
# Row centers:
#   Row 1: cy≈283
#   Row 2: cy≈295
#   Row 3: cy≈308
#   Row 4: cy≈321
#   Row 5: cy≈334
# ---------------------------------------------------------------------------

_P1_ACTION_CHECKBOXES: Dict[str, tuple] = {
    #  field_name:                (cx,   cy)
    # Column A
    "act_acidize":                (171, 283),
    "act_alter_casing":           (171, 295),
    "act_casing_repair":          (171, 308),
    "act_change_plans":           (171, 321),
    "act_convert_injection":      (171, 334),
    # Column B
    "act_deepen":                 (270, 283),
    "act_fracture_treat":         (270, 295),
    "act_new_construction":       (270, 308),
    "act_plug_abandon":           (270, 321),
    "act_plug_back":              (270, 334),
    # Column C
    "act_production":             (364, 283),
    "act_reclamation":            (364, 295),
    "act_recomplete":             (364, 308),
    "act_temp_abandon":           (364, 321),
    "act_water_disposal":         (364, 334),
    # Column D (rows 1–2 only have checkboxes; rows 3 has "Other" checkbox)
    "act_water_shutoff":          (479, 283),
    "act_well_integrity":         (479, 295),
    "act_other":                  (479, 308),
}

# ---------------------------------------------------------------------------
# Page 1 — "Other" action text field (next to act_other checkbox)
# The "Other ____" line is at row 3 of col D, text after checkbox at x≈489
# Row y=300–318 approx; baseline y≈308
# ---------------------------------------------------------------------------

_P1_ACT_OTHER_TEXT: tuple = (490, 308, 98)  # (x, y, width) — fills after "Other" label area

# ---------------------------------------------------------------------------
# Page 1 — Remarks (Field 13)
# Large multiline area from y=343 to y=582, full width x=25–590
# The label text occupies y=345–401, so actual input area starts lower.
# We'll use y=401 as the top of the input region to stay below label text.
# ---------------------------------------------------------------------------

_P1_REMARKS_X:      int = 27
_P1_REMARKS_Y:      int = 402    # below the instructional label text
_P1_REMARKS_WIDTH:  int = 560
_P1_REMARKS_HEIGHT: int = 175    # 402 → 577, leaving margin before y=582 line

# ---------------------------------------------------------------------------
# Page 1 — Certification (Field 14)
# Row y=582–612: Name and Title (divided at x=296)
# Row y=612–639: Signature (skip) and Date (right column x=296–590)
#
# Text at y=582–591: "14. I hereby certify..." label then "(Printed/Typed)"
# Label ends around x=271, so name field starts at x=27
# "Title" label at x=298–316, so title field starts at x=318
# "Date" at row y=612–639 at x=298–316; date field after that
# ---------------------------------------------------------------------------

_P1_CERT_FIELDS: Dict[str, tuple] = {
    #  field_name:    (x,   y,  width)
    "cert_name":      ( 27, 607, 265),   # left of divider x=296, below label row
    "cert_title":     (318, 607, 269),   # right of divider x=296
    "cert_date":      (318, 634, 269),   # date row right column
}

# ---------------------------------------------------------------------------
# Page 1 — Approval section (bottom of page)
# Row y=643–660: double line header
# Row y=660–664: "THIS SPACE FOR FEDERAL OR STATE OFFICE USE" text
# Row y=664–690: Approved by (x=25–316) | Title (x=316–460) | Date (x=460–590)
# Row y=690–717: [continuation] | Title (x=316–460) | Date (x=460–590) | Office (x=316–)
# "Approved by" label at y=664–674, x=26–68
# "Title" at y=681–691 x=316–333
# "Date" at y=681–691 x=461–477
# "Office" at y=699–709 x=318–339
# ---------------------------------------------------------------------------

_P1_APPROVAL_FIELDS: Dict[str, tuple] = {
    #  field_name:       (x,   y,  width)
    "approval_by":       ( 27, 685, 285),   # x=25–316, below "Approved by" label
    "approval_title":    (318, 685, 139),   # x=316–460, "Title" column
    "approval_date":     (462, 685, 125),   # x=460–590, "Date" column
    "approval_office":   (342, 712, 245),   # x=316–590, "Office" row (y=690–717)
}


# ===========================================================================
# Low-level widget helpers
# ===========================================================================

def _add_text_widget(
    page: "fitz.Page",
    name: str,
    rect: "fitz.Rect",
    fontsize: float = 9,
    fontname: str = "Cour",
) -> None:
    """
    Stamp a single-line AcroForm text widget onto *page*.

    Parameters
    ----------
    page:
        The PyMuPDF page object to receive the widget.
    name:
        The AcroForm field name (must be unique within the document).
    rect:
        The bounding rectangle for the widget in PDF user-space points.
    fontsize:
        Point size for the embedded font; defaults to 9.
    fontname:
        PDF base-font name; "Cour" (Courier) matches the generator's font.
    """
    widget = fitz.Widget()
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.field_name = name
    widget.rect = rect
    widget.text_fontsize = fontsize
    widget.text_font = fontname
    widget.text_color = (0, 0, 0)
    widget.field_flags = 0
    page.add_widget(widget)


def _add_checkbox_widget(
    page: "fitz.Page",
    name: str,
    rect: "fitz.Rect",
) -> None:
    """
    Stamp a checkbox AcroForm widget onto *page*.

    The caller is responsible for sizing the rect as a 12×12 pt square
    centred at the original coordinate supplied in the coordinate tables.

    Parameters
    ----------
    page:
        The PyMuPDF page object to receive the widget.
    name:
        The AcroForm field name.
    rect:
        A 12×12 pt bounding rectangle.
    """
    widget = fitz.Widget()
    widget.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
    widget.field_name = name
    widget.rect = rect
    widget.field_value = False
    page.add_widget(widget)


def _add_multiline_text_widget(
    page: "fitz.Page",
    name: str,
    rect: "fitz.Rect",
    fontsize: float = 7,
    fontname: str = "Cour",
) -> None:
    """
    Stamp a multiline AcroForm text widget onto *page*.

    Identical to :func:`_add_text_widget` except that the
    ``PDF_FIELD_IS_MULTILINE`` flag is set, allowing the field to wrap text
    across multiple lines.

    Parameters
    ----------
    page:
        The PyMuPDF page object to receive the widget.
    name:
        The AcroForm field name.
    rect:
        The bounding rectangle for the widget.
    fontsize:
        Point size; defaults to 7 for the remarks area.
    fontname:
        PDF base-font name; defaults to "Cour".
    """
    widget = fitz.Widget()
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.field_name = name
    widget.rect = rect
    widget.text_fontsize = fontsize
    widget.text_font = fontname
    widget.text_color = (0, 0, 0)
    widget.field_flags = fitz.PDF_TX_FIELD_IS_MULTILINE
    page.add_widget(widget)


# ===========================================================================
# Per-section stamping functions
# ===========================================================================

def _stamp_header_fields(page: "fitz.Page") -> int:
    """
    Stamp the header text widgets onto *page* (Fields 2–11).

    Converts each ``(x, y, width)`` entry in :data:`_P1_HEADER` to a proper
    :class:`fitz.Rect` using::

        Rect(x - 2, y - fontsize, x + width, y + 2)

    The 2-pt left shift compensates for the internal text padding that
    PyMuPDF adds inside widget rects.

    Returns the number of widgets added.
    """
    fs = _P1_HEADER_FS
    count = 0
    for field_name, (x, y, width) in _P1_HEADER.items():
        rect = fitz.Rect(x - 2, y - fs, x + width, y + 2)
        _add_text_widget(page, field_name, rect, fontsize=fs)
        count += 1
    logger.debug("_stamp_header_fields: %d widgets", count)
    return count


def _stamp_well_type_checkboxes(page: "fitz.Page") -> int:
    """
    Stamp the 3 well-type checkbox widgets (Field 1) onto *page*.

    Checkbox rect formula (12×12 pt square centred at (cx, cy))::

        Rect(cx - 6, cy - 6, cx + 6, cy + 6)

    Returns the number of widgets added.
    """
    count = 0
    for field_name, (cx, cy) in _P1_WELL_TYPE_CHECKBOXES.items():
        rect = fitz.Rect(cx - 6, cy - 6, cx + 6, cy + 6)
        _add_checkbox_widget(page, field_name, rect)
        count += 1
    logger.debug("_stamp_well_type_checkboxes: %d widgets", count)
    return count


def _stamp_submission_checkboxes(page: "fitz.Page") -> int:
    """
    Stamp the 3 submission-type checkbox widgets (Field 12, left column).

    Returns the number of widgets added.
    """
    count = 0
    for field_name, (cx, cy) in _P1_SUBMISSION_CHECKBOXES.items():
        rect = fitz.Rect(cx - 6, cy - 6, cx + 6, cy + 6)
        _add_checkbox_widget(page, field_name, rect)
        count += 1
    logger.debug("_stamp_submission_checkboxes: %d widgets", count)
    return count


def _stamp_action_checkboxes(page: "fitz.Page") -> int:
    """
    Stamp the 18 action-type checkbox widgets (Field 12, right columns).

    Returns the number of widgets added.
    """
    count = 0
    for field_name, (cx, cy) in _P1_ACTION_CHECKBOXES.items():
        rect = fitz.Rect(cx - 6, cy - 6, cx + 6, cy + 6)
        _add_checkbox_widget(page, field_name, rect)
        count += 1
    logger.debug("_stamp_action_checkboxes: %d widgets", count)
    return count


def _stamp_act_other_text(page: "fitz.Page") -> int:
    """
    Stamp the text field for the "Other" action type (Field 12, col D, row 3).

    Returns 1 (the number of widgets added).
    """
    x, y, width = _P1_ACT_OTHER_TEXT
    fs = 8
    rect = fitz.Rect(x - 2, y - fs, x + width, y + 2)
    _add_text_widget(page, "act_other_text", rect, fontsize=fs)
    logger.debug("_stamp_act_other_text: 1 widget")
    return 1


def _stamp_remarks(page: "fitz.Page") -> int:
    """
    Stamp the multiline remarks widget (Field 13) onto *page*.

    The widget spans from y≈402 to y≈577, below the field 13 instruction
    label text, at full available width.

    Returns 1 (the number of widgets added).
    """
    rect = fitz.Rect(
        _P1_REMARKS_X,
        _P1_REMARKS_Y,
        _P1_REMARKS_X + _P1_REMARKS_WIDTH,
        _P1_REMARKS_Y + _P1_REMARKS_HEIGHT,
    )
    _add_multiline_text_widget(page, "remarks", rect, fontsize=7)
    logger.debug("_stamp_remarks: 1 widget")
    return 1


def _stamp_certification_fields(page: "fitz.Page") -> int:
    """
    Stamp the 3 certification text widgets (Field 14) onto *page*.

    Returns the number of widgets added.
    """
    fs = _P1_HEADER_FS
    count = 0
    for field_name, (x, y, width) in _P1_CERT_FIELDS.items():
        rect = fitz.Rect(x - 2, y - fs, x + width, y + 2)
        _add_text_widget(page, field_name, rect, fontsize=fs)
        count += 1
    logger.debug("_stamp_certification_fields: %d widgets", count)
    return count


def _stamp_approval_fields(page: "fitz.Page") -> int:
    """
    Stamp the 4 approval-section text widgets onto *page*.

    Returns the number of widgets added.
    """
    fs = _P1_HEADER_FS
    count = 0
    for field_name, (x, y, width) in _P1_APPROVAL_FIELDS.items():
        rect = fitz.Rect(x - 2, y - fs, x + width, y + 2)
        _add_text_widget(page, field_name, rect, fontsize=fs)
        count += 1
    logger.debug("_stamp_approval_fields: %d widgets", count)
    return count


# ===========================================================================
# Public API
# ===========================================================================

def build_annotated_template(
    input_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> Path:
    """
    Open the blank BLM 3160-5 template and stamp all AcroForm widgets.

    Reads the flat (no-widget) blank template, adds named AcroForm text and
    checkbox widgets at the correct positions for every fillable field on
    page 1, then saves the result as the annotated template.  Page 2 is
    static instructions text and receives no widgets.

    Parameters
    ----------
    input_path:
        Path to the blank BLM 3160-5 PDF template.  Defaults to
        ``docs/reference_photos/BLM 3160-5 Blank Template.pdf`` relative to
        the project root.
    output_path:
        Destination path for the annotated template.  Defaults to
        ``docs/reference_photos/BLM 3160-5 Template With Fields.pdf``.

    Returns
    -------
    Path
        The resolved path of the written annotated template.

    Raises
    ------
    RuntimeError
        If PyMuPDF (fitz) is not installed.
    FileNotFoundError
        If *input_path* does not exist.
    """
    if not HAS_FITZ:
        raise RuntimeError(
            "PyMuPDF (fitz) is required for build_annotated_template(). "
            "Install with: pip install pymupdf"
        )

    src = Path(input_path) if input_path is not None else _BLANK_TEMPLATE_PATH
    dst = Path(output_path) if output_path is not None else _ANNOTATED_TEMPLATE_PATH

    if not src.exists():
        raise FileNotFoundError(f"Blank BLM 3160-5 template not found: {src}")

    dst.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Opening blank BLM 3160-5 template: %s", src)
    doc = fitz.open(str(src))

    if doc.page_count < 1:
        raise ValueError(
            f"Expected at least 1 page in the BLM 3160-5 template, got {doc.page_count}."
        )

    page1 = doc[0]

    # --- Page 1: all fillable sections ---
    p1_count = 0
    p1_count += _stamp_header_fields(page1)
    p1_count += _stamp_well_type_checkboxes(page1)
    p1_count += _stamp_submission_checkboxes(page1)
    p1_count += _stamp_action_checkboxes(page1)
    p1_count += _stamp_act_other_text(page1)
    p1_count += _stamp_remarks(page1)
    p1_count += _stamp_certification_fields(page1)
    p1_count += _stamp_approval_fields(page1)

    logger.info("Page 1: %d widgets stamped", p1_count)
    logger.info("Total:  %d widgets stamped", p1_count)

    doc.save(str(dst))
    doc.close()

    logger.info("Annotated BLM 3160-5 template saved to: %s", dst)
    return dst


def verify_template(path: Optional[Path] = None) -> Dict[str, object]:
    """
    Open the annotated BLM 3160-5 template and report the AcroForm inventory.

    Parameters
    ----------
    path:
        Path to the annotated template.  Defaults to
        ``docs/reference_photos/BLM 3160-5 Template With Fields.pdf``.

    Returns
    -------
    dict
        A dictionary with the following keys:

        ``total_widgets`` : int
            Total number of AcroForm widgets across all pages.
        ``page_1_widgets`` : int
            Widget count on Page 1.
        ``widget_names`` : list[str]
            Sorted list of all widget field names.

    Raises
    ------
    RuntimeError
        If PyMuPDF (fitz) is not installed.
    FileNotFoundError
        If the annotated template does not exist at *path*.
    """
    if not HAS_FITZ:
        raise RuntimeError(
            "PyMuPDF (fitz) is required for verify_template(). "
            "Install with: pip install pymupdf"
        )

    src = Path(path) if path is not None else _ANNOTATED_TEMPLATE_PATH

    if not src.exists():
        raise FileNotFoundError(
            f"Annotated BLM 3160-5 template not found: {src}. "
            "Run build_annotated_template() first."
        )

    doc = fitz.open(str(src))

    p1_names: List[str] = []

    if doc.page_count >= 1:
        for widget in doc[0].widgets():
            p1_names.append(widget.field_name)

    doc.close()

    all_names = sorted(p1_names)
    result = {
        "total_widgets":  len(all_names),
        "page_1_widgets": len(p1_names),
        "widget_names":   all_names,
    }

    logger.info(
        "verify_template: total=%d  page1=%d",
        result["total_widgets"],
        result["page_1_widgets"],
    )
    return result


def draw_coordinate_grid(
    input_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
    step: int = 50,
) -> Path:
    """
    Development utility: overlay a coordinate grid on page 1 of the blank template.

    Draws horizontal and vertical lines every *step* points and labels each
    intersection so coordinates can be read directly from the output PDF.

    Parameters
    ----------
    input_path:
        Source blank template.  Defaults to ``_BLANK_TEMPLATE_PATH``.
    output_path:
        Where to save the annotated grid PDF.  Defaults to the same directory
        as the blank template with ``_grid`` appended to the stem.
    step:
        Grid line spacing in points.  Defaults to 50.

    Returns
    -------
    Path
        The path of the saved grid PDF.
    """
    if not HAS_FITZ:
        raise RuntimeError("PyMuPDF (fitz) is required for draw_coordinate_grid().")

    src = Path(input_path) if input_path is not None else _BLANK_TEMPLATE_PATH
    if output_path is None:
        dst = src.with_name(src.stem + "_grid.pdf")
    else:
        dst = Path(output_path)

    doc = fitz.open(str(src))
    page = doc[0]
    w, h = page.rect.width, page.rect.height

    blue = (0, 0, 1)
    fs = 6

    for x in range(0, int(w) + 1, step):
        page.draw_line(fitz.Point(x, 0), fitz.Point(x, h), color=blue, width=0.3)
        page.insert_text(fitz.Point(x + 1, 10), str(x), fontsize=fs, color=blue)

    for y in range(0, int(h) + 1, step):
        page.draw_line(fitz.Point(0, y), fitz.Point(w, y), color=blue, width=0.3)
        page.insert_text(fitz.Point(2, y - 1), str(y), fontsize=fs, color=blue)

    doc.save(str(dst))
    doc.close()
    return dst
