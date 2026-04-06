"""
W-3 Template Builder — one-time widget stamping for the blank W-3 template.

Stamps named AcroForm widgets onto the flat blank template so that
w3_pdf_generator can fill fields by name instead of by (x, y) coordinate.

All coordinate definitions for the W-3 form live exclusively in this file.
The stamping is intended to run once to produce "W3 Template With Fields.pdf",
which the pdf generator then fills programmatically.

Usage (via Django management command):
    python manage.py build_w3_template [--output PATH] [--verify]

Usage (direct):
    from apps.public_core.services.w3_template_builder import (
        build_annotated_template,
        verify_template,
    )
    output = build_annotated_template()
    info   = verify_template()
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
        "PyMuPDF (fitz) not installed. W-3 template building will be unavailable. "
        "Install with: pip install pymupdf"
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
    / "W3 Blank Template.pdf"
)

_ANNOTATED_TEMPLATE_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "reference_photos"
    / "W3 Template With Fields.pdf"
)

# Public aliases so callers can reference the paths without importing private names.
BLANK_TEMPLATE_PATH: Path = _BLANK_TEMPLATE_PATH
ANNOTATED_TEMPLATE_PATH: Path = _ANNOTATED_TEMPLATE_PATH

# ---------------------------------------------------------------------------
# Page 1 — Header field coordinates
# Each value is (baseline_x, baseline_y).  fontsize=9 for all header fields.
# Rect formula: fitz.Rect(x, y - fontsize, x + width, y + 2)
# ---------------------------------------------------------------------------

_P1_HEADER_FS = 9  # fontsize used by the generator for header text

_P1_HEADER: Dict[str, tuple] = {
    #  field_name:               (x,   y,  width)
    #
    # Cell boundaries (from PDF line extraction):
    #   Left column:   x = 31 – 247   Middle column: x = 247 – 463
    #   Right column:  x = 464 – 576
    #   Row tops: API=81, Lease=107, Field=132, Oper=158, Addr=184,
    #             Loc=209, Sect=237, Type=263, Cond=288, Plug-data=305
    #
    # --- API row  (y = 81 – 107) ---
    "api_number":               (365, 100,  96),   # after pre-printed "42-"
    "rrc_district":             (515, 100,  60),
    # --- RRC Lease row  (y = 107 – 132) ---
    "rrc_lease_id":             (515, 126,  60),
    # --- Field / Lease / Well row  (y = 132 – 158) ---
    "field_name":               ( 33, 151, 212),
    "lease_name":               (249, 151, 212),
    "well_number":              (515, 151,  60),
    # --- Operator / W-1 / County row  (y = 158 – 184) ---
    "operator":                 ( 33, 176, 212),
    "original_w1_operator":     (249, 176, 212),
    "county":                   (515, 176,  60),
    # --- Address / Sub W-1 / Permit Date row  (y = 184 – 209) ---
    "operator_address":         ( 33, 202, 212),
    "subsequent_w1_operator":   (249, 202, 212),
    "drilling_permit_date":     (515, 202,  60),
    # --- Location / Permit # row  (y = 209 – 237) ---
    "feet_from_line1":          (197, 219,  85),
    "feet_from_line2":          (380, 219,  38),
    "permit_number":            (527, 218,  48),
    # --- Section / Direction / Commenced row  (y = 237 – 263) ---
    "section_block_survey":     ( 33, 257, 213),
    "direction_from_town":      (250, 257, 211),
    "drilling_commenced":       (515, 257,  60),
    # --- Type Well / Depth / Completed row  (y = 263 – 288) ---
    "well_type":                ( 33, 283,  73),
    "total_depth":              (110, 283,  41),
    "drilling_completed":       (515, 283,  60),
    # --- Cond. on hand / Plugged row  (y = 288 – 305) ---
    "condensate_on_hand_p1":    (120, 304,  30),
    "date_well_plugged_p1":     (515, 304,  60),
}

# ---------------------------------------------------------------------------
# Page 1 — Plug table coordinates
# 8 columns × 9 rows = 72 widgets
# Column centers (x) and row baselines (y).  fontsize=8.
# Rect formula: fitz.Rect(col_x - 19, row_y - 8, col_x + 19, row_y + 2)
# ---------------------------------------------------------------------------

_P1_PLUG_COL_X: List[int] = [273, 315, 356, 398, 439, 480, 522, 563]

_P1_PLUG_ROW_Y: Dict[str, int] = {
    "cementing_date":          326,
    "hole_size_in":            339,
    "depth_bottom_ft":         352,
    "sacks":                   364,
    "slurry_volume_cf":        377,
    "calculated_top_of_plug":  390,
    "measured_top_of_plug":    403,
    "slurry_weight_ppg":       415,
    "cement_class":            428,
}

# ---------------------------------------------------------------------------
# Page 1 — Casing record coordinates
# 6 rows × 5 columns = 30 widgets.  fontsize=8.
# Rect formula: fitz.Rect(col_x, start_y + (row-1)*12 - 8, col_x + width, start_y + (row-1)*12 + 2)
# ---------------------------------------------------------------------------

_P1_CASING_START_Y: int = 474   # baseline of first data row (cell y=464–477)
_P1_CASING_ROW_H:   int = 13   # actual cell height from PDF line data

_P1_CASING_COL_X: Dict[str, tuple] = {
    #  col_field:    (x,  width)   — cell boundaries from PDF lines
    "od_in":        ( 33,  24),    # cell x = 31 – 59
    "weight_ppf":   ( 61,  41),    # cell x = 59 – 104
    "top_ft":       (106,  68),    # cell x = 104 – 176
    "bottom_ft":    (178,  68),    # cell x = 176 – 248
    "hole_size_in": (250,  54),    # cell x = 248 – 306
}

# ---------------------------------------------------------------------------
# Page 1 — Perforations coordinates
# 5 rows × 4 columns = 20 widgets.  fontsize=8.
# Left  column → perfs 1-5  (from_ft / to_ft)
# Right column → perfs 6-10 (from_ft / to_ft)
# Rect formula: fitz.Rect(x, start_y + (row-1)*12 - 8, x + width, start_y + (row-1)*12 + 2)
# ---------------------------------------------------------------------------

_P1_PERF_START_Y:  int = 540
_P1_PERF_ROW_H:    int = 12
_P1_PERF_WIDTH:    int = 108  # shared width for all four columns

_P1_PERF_COLS: Dict[str, int] = {
    "left_from":  65,   # perfs 1-5  from_ft
    "left_to":   178,   # perfs 1-5  to_ft
    "right_from": 352,  # perfs 6-10 from_ft
    "right_to":   465,  # perfs 6-10 to_ft
}

# ---------------------------------------------------------------------------
# Page 2 — Miscellaneous text and checkbox fields
# Checkboxes are stamped as 12×12 pt squares centred at (x, y).
# Text fields: Rect(x, y - 9, x + width, y + 2)  (fontsize ≈ 9/8)
# ---------------------------------------------------------------------------

_P2_TEXT_FIELDS: Dict[str, tuple] = {
    #  field_name:                 (x,    y,  width)
    #
    # Page 2 cell boundaries (from PDF line extraction):
    #   Row 1 (31/32/33): y=50–77  |  x=31–229 | x=230–468 | x=469–580
    #   Row 2 (34/35):    y=77–95  |  x=31–104 | x=104–225 | x=226–580
    #   Sub-rows (TOP/BOTTOM + If No): y=95–148
    #   Row 3 (37):       y=148–171 |  x=31–441 | x=442–580
    #   Row 4 (38):       y=171–284 |  full width
    #
    # --- Field 32: How was mud applied  (cell x=230–468) ---
    "mud_application_method":   (235,  70, 230),
    # --- Field 33: Mud Weight LBS/GAL  (cell x=469–580) ---
    "mud_weight_ppg":           (475,  68, 103),
    # --- Field 34: Total Depth  (cell x=31–104) ---
    "total_depth_p2":           ( 36,  95,  66),
    # --- Fresh water TOP column  (cell x=104–164, first data row y=95–107) ---
    "fresh_water_top":          (115, 105,  48),
    # --- Fresh water BOTTOM column  (cell x=164–225) ---
    "fresh_water_bottom":       (168, 105,  54),
    # --- Depth of Deepest Fresh Water  (below label, x=31–104) ---
    "deepest_fresh_water":      ( 40, 142,  60),
    # --- Field 36: If No, Explain  (cell x=226–580) ---
    "if_no_explain":            (235, 107, 340),
    # --- Field 37: Cementing company  (cell x=31–441) ---
    "cementing_company":        ( 36, 166, 400),
    # --- Field 37: Date RRC notified  (cell x=442–580) ---
    "date_rrc_notified":        (445, 166, 133),
    # --- Field 38: Surface owners  (full width, y=171–284) ---
    "surface_owners":           ( 42, 188, 528),
}

_P2_CHECKBOXES: Dict[str, tuple] = {
    #  field_name:               (cx,   cy)   — center of 12×12 square
    #
    # Field 31: "Yes" label at x=194 y=50–59,  "No" at x=194 y=61–70
    # Checkbox goes LEFT of label text
    "mud_filled_yes":           (183,  55),
    "mud_filled_no":            (183,  65),
    # Field 35: "Yes" at x=535 y=77–86,  "No" at x=535 y=85–94
    "all_wells_plugged_yes":    (525,  82),
    "all_wells_plugged_no":     (525,  90),
    # Field 39: notice given (below question at y=284–293)
    "notice_given_yes":         ( 42, 298),
    "notice_given_no":          ( 42, 310),
}

# ---------------------------------------------------------------------------
# Page 2 — Remarks multiline text widget
# ---------------------------------------------------------------------------

_P2_REMARKS_X:      int = 42
_P2_REMARKS_Y:      int = 535
_P2_REMARKS_WIDTH:  int = 528
_P2_REMARKS_HEIGHT: int = 225   # 535 → ~760


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

    Identical to :func:`_add_text_widget` except that the ``PDF_FIELD_IS_MULTILINE``
    flag is set, allowing the field to wrap text across multiple lines.

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
# Per-section stamping functions  (Page 1)
# ===========================================================================

def _stamp_page1_header(page: "fitz.Page") -> int:
    """
    Stamp the 23 header text widgets onto *page* (Page 1).

    Converts each ``(x, y, width)`` baseline entry in :data:`_P1_HEADER`
    to a proper :class:`fitz.Rect` using::

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
    logger.debug("_stamp_page1_header: %d widgets", count)
    return count


def _stamp_page1_plugs(page: "fitz.Page") -> int:
    """
    Stamp the 72 plug-table text widgets (8 columns × 9 rows) onto *page*.

    Widget names follow the pattern ``plug_{col}_{row_field}``
    e.g. ``plug_1_sacks``, ``plug_3_cement_class``.

    Rect formula::

        Rect(col_x - 19, row_y - 8, col_x + 19, row_y + 2)

    Returns the number of widgets added.
    """
    count = 0
    for col_index, col_x in enumerate(_P1_PLUG_COL_X, start=1):
        for row_field, row_y in _P1_PLUG_ROW_Y.items():
            name = f"plug_{col_index}_{row_field}"
            rect = fitz.Rect(col_x - 19, row_y - 8, col_x + 19, row_y + 2)
            _add_text_widget(page, name, rect, fontsize=8)
            count += 1
    logger.debug("_stamp_page1_plugs: %d widgets", count)
    return count


def _stamp_page1_casing(page: "fitz.Page") -> int:
    """
    Stamp the 30 casing-record text widgets (6 rows × 5 columns) onto *page*.

    Widget names follow the pattern ``casing_{row}_{col_field}``
    e.g. ``casing_1_od_in``, ``casing_3_bottom_ft``.

    Rect formula::

        Rect(col_x,
             start_y + (row - 1) * 12 - 8,
             col_x + width,
             start_y + (row - 1) * 12 + 2)

    Returns the number of widgets added.
    """
    count = 0
    for row in range(1, 7):
        row_base = _P1_CASING_START_Y + (row - 1) * _P1_CASING_ROW_H
        for col_field, (col_x, col_width) in _P1_CASING_COL_X.items():
            name = f"casing_{row}_{col_field}"
            rect = fitz.Rect(col_x, row_base - 8, col_x + col_width, row_base + 2)
            _add_text_widget(page, name, rect, fontsize=8)
            count += 1
    logger.debug("_stamp_page1_casing: %d widgets", count)
    return count


def _stamp_page1_perforations(page: "fitz.Page") -> int:
    """
    Stamp the 20 perforation text widgets (10 intervals × 2 fields) onto *page*.

    Left column  (rows 1-5):  ``perf_{1-5}_from_ft`` / ``perf_{1-5}_to_ft``
    Right column (rows 6-10): ``perf_{6-10}_from_ft`` / ``perf_{6-10}_to_ft``

    Rect formula::

        Rect(x,
             start_y + (local_row - 1) * 12 - 8,
             x + width,
             start_y + (local_row - 1) * 12 + 2)

    Returns the number of widgets added.
    """
    count = 0
    w = _P1_PERF_WIDTH

    for local_row in range(1, 6):
        row_base = _P1_PERF_START_Y + (local_row - 1) * _P1_PERF_ROW_H

        # Left column: perfs 1-5
        perf_num = local_row
        rect_from = fitz.Rect(
            _P1_PERF_COLS["left_from"],
            row_base - 8,
            _P1_PERF_COLS["left_from"] + w,
            row_base + 2,
        )
        rect_to = fitz.Rect(
            _P1_PERF_COLS["left_to"],
            row_base - 8,
            _P1_PERF_COLS["left_to"] + w,
            row_base + 2,
        )
        _add_text_widget(page, f"perf_{perf_num}_from_ft", rect_from, fontsize=8)
        _add_text_widget(page, f"perf_{perf_num}_to_ft",   rect_to,   fontsize=8)
        count += 2

        # Right column: perfs 6-10
        perf_num_r = local_row + 5
        rect_from_r = fitz.Rect(
            _P1_PERF_COLS["right_from"],
            row_base - 8,
            _P1_PERF_COLS["right_from"] + w,
            row_base + 2,
        )
        rect_to_r = fitz.Rect(
            _P1_PERF_COLS["right_to"],
            row_base - 8,
            _P1_PERF_COLS["right_to"] + w,
            row_base + 2,
        )
        _add_text_widget(page, f"perf_{perf_num_r}_from_ft", rect_from_r, fontsize=8)
        _add_text_widget(page, f"perf_{perf_num_r}_to_ft",   rect_to_r,   fontsize=8)
        count += 2

    logger.debug("_stamp_page1_perforations: %d widgets", count)
    return count


# ===========================================================================
# Per-section stamping functions  (Page 2)
# ===========================================================================

def _stamp_page2_fields(page: "fitz.Page") -> int:
    """
    Stamp the 10 text widgets and 6 checkbox widgets on Page 2.

    Text widget rect formula::

        Rect(x - 2, y - 9, x + width, y + 2)

    The 2-pt left shift compensates for internal widget text padding.

    Checkbox rect formula (12×12 pt square centred at (cx, cy))::

        Rect(cx - 6, cy - 6, cx + 6, cy + 6)

    Returns the total number of widgets added.
    """
    count = 0

    # --- Text fields ---
    for field_name, (x, y, width) in _P2_TEXT_FIELDS.items():
        rect = fitz.Rect(x - 2, y - 9, x + width, y + 2)
        _add_text_widget(page, field_name, rect, fontsize=9)
        count += 1

    # --- Checkboxes ---
    for field_name, (cx, cy) in _P2_CHECKBOXES.items():
        rect = fitz.Rect(cx - 6, cy - 6, cx + 6, cy + 6)
        _add_checkbox_widget(page, field_name, rect)
        count += 1

    logger.debug("_stamp_page2_fields: %d widgets", count)
    return count


def _stamp_page2_remarks(page: "fitz.Page") -> int:
    """
    Stamp the single multiline ``remarks`` text widget on Page 2.

    The widget spans from y=535 to y≈760 (225 pt tall) and occupies the
    full available width of 528 pt.

    Returns 1 (the number of widgets added).
    """
    rect = fitz.Rect(
        _P2_REMARKS_X,
        _P2_REMARKS_Y,
        _P2_REMARKS_X + _P2_REMARKS_WIDTH,
        _P2_REMARKS_Y + _P2_REMARKS_HEIGHT,
    )
    _add_multiline_text_widget(page, "remarks", rect, fontsize=7)
    logger.debug("_stamp_page2_remarks: 1 widget")
    return 1


# ===========================================================================
# Public API
# ===========================================================================

def build_annotated_template(
    input_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> Path:
    """
    Open the blank W-3 template and stamp all AcroForm widgets onto it.

    Reads the flat (no-widget) blank template, adds named AcroForm text and
    checkbox widgets at the correct positions for every field on both pages,
    then saves the result as the annotated template.

    Parameters
    ----------
    input_path:
        Path to the blank W-3 PDF template.  Defaults to
        ``docs/reference_photos/W3 Blank Template.pdf`` relative to the
        project root.
    output_path:
        Destination path for the annotated template.  Defaults to
        ``docs/reference_photos/W3 Template With Fields.pdf``.

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
        raise FileNotFoundError(f"Blank W-3 template not found: {src}")

    dst.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Opening blank W-3 template: %s", src)
    doc = fitz.open(str(src))

    if doc.page_count < 2:
        raise ValueError(
            f"Expected at least 2 pages in the W-3 template, got {doc.page_count}."
        )

    page1 = doc[0]
    page2 = doc[1]

    # --- Page 1 ---
    p1_count = 0
    p1_count += _stamp_page1_header(page1)
    p1_count += _stamp_page1_plugs(page1)
    p1_count += _stamp_page1_casing(page1)
    p1_count += _stamp_page1_perforations(page1)

    logger.info("Page 1: %d widgets stamped", p1_count)

    # --- Page 2 ---
    p2_count = 0
    p2_count += _stamp_page2_fields(page2)
    p2_count += _stamp_page2_remarks(page2)

    logger.info("Page 2: %d widgets stamped", p2_count)
    logger.info("Total:  %d widgets stamped", p1_count + p2_count)

    doc.save(str(dst))
    doc.close()

    logger.info("Annotated W-3 template saved to: %s", dst)
    return dst


def verify_template(path: Optional[Path] = None) -> Dict[str, object]:
    """
    Open the annotated W-3 template and report the AcroForm widget inventory.

    Parameters
    ----------
    path:
        Path to the annotated template.  Defaults to
        ``docs/reference_photos/W3 Template With Fields.pdf``.

    Returns
    -------
    dict
        A dictionary with the following keys:

        ``total_widgets`` : int
            Total number of AcroForm widgets across both pages.
        ``page_1_widgets`` : int
            Widget count on Page 1.
        ``page_2_widgets`` : int
            Widget count on Page 2.
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
            f"Annotated W-3 template not found: {src}. "
            "Run build_annotated_template() first."
        )

    doc = fitz.open(str(src))

    p1_names: List[str] = []
    p2_names: List[str] = []

    if doc.page_count >= 1:
        for widget in doc[0].widgets():
            p1_names.append(widget.field_name)

    if doc.page_count >= 2:
        for widget in doc[1].widgets():
            p2_names.append(widget.field_name)

    doc.close()

    all_names = sorted(p1_names + p2_names)
    result = {
        "total_widgets":  len(all_names),
        "page_1_widgets": len(p1_names),
        "page_2_widgets": len(p2_names),
        "widget_names":   all_names,
    }

    logger.info(
        "verify_template: total=%d  page1=%d  page2=%d",
        result["total_widgets"],
        result["page_1_widgets"],
        result["page_2_widgets"],
    )
    return result
