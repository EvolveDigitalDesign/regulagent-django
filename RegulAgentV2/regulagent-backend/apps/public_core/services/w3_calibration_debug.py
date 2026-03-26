#!/usr/bin/env python3
"""
W-3 Calibration Debug Overlay — standalone script (no Django required).

Generates a debug PDF that overlays labeled, color-coded rectangles at every
current widget position onto the blank W-3 template. This lets you see exactly
where each widget sits relative to the actual form fields.

Usage:
    python apps/public_core/services/w3_calibration_debug.py

Output:
    w3_debug_overlay.pdf  (in current working directory)
"""

from pathlib import Path
import fitz  # PyMuPDF

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent.parent
_BLANK_TEMPLATE = _PROJECT_ROOT / "docs" / "reference_photos" / "W3 Blank Template.pdf"

# ---------------------------------------------------------------------------
# Current coordinates — copied from w3_template_builder.py
# ---------------------------------------------------------------------------

_P1_HEADER_FS = 9

_P1_HEADER = {
    "api_number":               (365, 100,  96),
    "rrc_district":             (515, 100,  60),
    "rrc_lease_id":             (515, 126,  60),
    "field_name":               ( 33, 151, 212),
    "lease_name":               (249, 151, 212),
    "well_number":              (515, 151,  60),
    "operator":                 ( 33, 176, 212),
    "original_w1_operator":     (249, 176, 212),
    "county":                   (515, 176,  60),
    "operator_address":         ( 33, 202, 212),
    "subsequent_w1_operator":   (249, 202, 212),
    "drilling_permit_date":     (515, 202,  60),
    "feet_from_line1":          (197, 219,  85),
    "feet_from_line2":          (380, 219,  38),
    "permit_number":            (527, 218,  48),
    "section_block_survey":     ( 33, 257, 213),
    "direction_from_town":      (250, 257, 211),
    "drilling_commenced":       (515, 257,  60),
    "well_type":                ( 33, 283,  73),
    "total_depth":              (110, 283,  41),
    "drilling_completed":       (515, 283,  60),
    "condensate_on_hand_p1":    (120, 304,  30),
    "date_well_plugged_p1":     (515, 304,  60),
}

_P1_PLUG_COL_X = [273, 315, 356, 398, 439, 480, 522, 563]

_P1_PLUG_ROW_Y = {
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

_P1_CASING_START_Y = 474
_P1_CASING_ROW_H = 13

_P1_CASING_COL_X = {
    "od_in":        ( 33,  24),
    "weight_ppf":   ( 61,  41),
    "top_ft":       (106,  68),
    "bottom_ft":    (178,  68),
    "hole_size_in": (250,  54),
}

_P1_PERF_START_Y = 540
_P1_PERF_ROW_H = 12

_P1_PERF_COLS = {
    "left_from":  65,
    "left_to":   178,
    "right_from": 352,
    "right_to":   465,
}
_P1_PERF_WIDTH = 108


# ---------------------------------------------------------------------------
# Color palette for sections
# ---------------------------------------------------------------------------
COLORS = {
    "header":  (1.0, 0.2, 0.2, 0.25),   # red semi-transparent
    "plug":    (0.2, 0.6, 1.0, 0.25),   # blue semi-transparent
    "casing":  (0.2, 0.8, 0.2, 0.25),   # green semi-transparent
    "perf":    (0.8, 0.6, 0.0, 0.25),   # orange semi-transparent
}

LABEL_COLORS = {
    "header":  (0.8, 0.0, 0.0),
    "plug":    (0.0, 0.3, 0.8),
    "casing":  (0.0, 0.6, 0.0),
    "perf":    (0.6, 0.4, 0.0),
}

BORDER_COLORS = {
    "header":  (1.0, 0.0, 0.0),
    "plug":    (0.0, 0.4, 1.0),
    "casing":  (0.0, 0.7, 0.0),
    "perf":    (0.8, 0.5, 0.0),
}


def draw_grid(page):
    """Draw coordinate grid on the page."""
    w, h = page.rect.width, page.rect.height
    BLUE = (0.0, 0.4, 0.8)
    GREY = (0.8, 0.8, 0.8)
    LABEL_FS = 5

    # Fine grid every 10pt
    for x in range(0, int(w) + 1, 10):
        page.draw_line(fitz.Point(x, 0), fitz.Point(x, h), color=GREY, width=0.2)
    for y in range(0, int(h) + 1, 10):
        page.draw_line(fitz.Point(0, y), fitz.Point(w, y), color=GREY, width=0.2)

    # Major grid every 50pt with labels
    for x in range(0, int(w) + 1, 50):
        page.draw_line(fitz.Point(x, 0), fitz.Point(x, h), color=BLUE, width=0.5)
        page.insert_text(fitz.Point(x + 1, 8), str(x), fontsize=LABEL_FS, color=BLUE)
    for y in range(0, int(h) + 1, 50):
        page.draw_line(fitz.Point(0, y), fitz.Point(w, y), color=BLUE, width=0.5)
        page.insert_text(fitz.Point(2, y - 1), str(y), fontsize=LABEL_FS, color=BLUE)


def draw_labeled_rect(page, rect, label, section="header"):
    """Draw a semi-transparent colored rect with a label."""
    fill = COLORS[section][:3]
    border = BORDER_COLORS[section]
    label_color = LABEL_COLORS[section]

    # Fill rect
    page.draw_rect(rect, color=border, fill=fill, width=0.8, fill_opacity=0.25)

    # Label — tiny text at top-left of rect
    label_pt = fitz.Point(rect.x0 + 1, rect.y0 + 5)
    page.insert_text(label_pt, label, fontsize=4, color=label_color)


def main():
    if not _BLANK_TEMPLATE.exists():
        print(f"ERROR: Blank template not found: {_BLANK_TEMPLATE}")
        return

    doc = fitz.open(str(_BLANK_TEMPLATE))
    page = doc[0]

    # Draw grid first (behind everything)
    draw_grid(page)

    # --- Header fields ---
    fs = _P1_HEADER_FS
    for field_name, (x, y, width) in _P1_HEADER.items():
        rect = fitz.Rect(x - 2, y - fs, x + width, y + 2)
        draw_labeled_rect(page, rect, field_name, "header")

    # --- Plug table ---
    for col_index, col_x in enumerate(_P1_PLUG_COL_X, start=1):
        for row_field, row_y in _P1_PLUG_ROW_Y.items():
            name = f"p{col_index}_{row_field[:6]}"
            rect = fitz.Rect(col_x - 19, row_y - 8, col_x + 19, row_y + 2)
            draw_labeled_rect(page, rect, name, "plug")

    # --- Casing record ---
    for row in range(1, 7):
        row_base = _P1_CASING_START_Y + (row - 1) * _P1_CASING_ROW_H
        for col_field, (col_x, col_width) in _P1_CASING_COL_X.items():
            name = f"c{row}_{col_field[:5]}"
            rect = fitz.Rect(col_x, row_base - 8, col_x + col_width, row_base + 2)
            draw_labeled_rect(page, rect, name, "casing")

    # --- Perforations ---
    w = _P1_PERF_WIDTH
    for local_row in range(1, 6):
        row_base = _P1_PERF_START_Y + (local_row - 1) * _P1_PERF_ROW_H
        for col_key, col_x in _P1_PERF_COLS.items():
            name = f"pf{local_row}_{col_key[:4]}"
            rect = fitz.Rect(col_x, row_base - 8, col_x + w, row_base + 2)
            draw_labeled_rect(page, rect, name, "perf")

    # Save
    output = Path.cwd() / "w3_debug_overlay.pdf"
    doc.save(str(output))
    doc.close()
    print(f"Debug overlay saved to: {output}")
    print(f"  RED    = header fields (sections 1-18)")
    print(f"  BLUE   = plug table")
    print(f"  GREEN  = casing record")
    print(f"  ORANGE = perforations")


if __name__ == "__main__":
    main()
