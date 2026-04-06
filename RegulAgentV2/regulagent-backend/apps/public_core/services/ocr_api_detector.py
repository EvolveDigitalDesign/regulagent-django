"""
OCR-based API number detection for scanned regulatory documents.

Replaces the broken text-extraction approach (PyMuPDF get_text() returns
empty strings for scanned documents). Uses Tesseract OCR on page header
areas where API numbers typically appear, with a Vision API escalation
for pages where OCR can't find a match.

Usage:
    from apps.public_core.services.ocr_api_detector import detect_api_from_pdf

    result = detect_api_from_pdf(pdf_path, pages=[0, 1])
    # Returns: {"api": "4200335663", "confidence": "high", "method": "ocr_tesseract", "page": 0}
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

# Regex patterns for Texas/NM API numbers
API_PATTERNS = [
    # Standard formats: 42-003-35663, 42 003 35663, 42-003-35663-00-00
    re.compile(r'(?:API\s*(?:#|No\.?|Number)?\s*:?\s*)?([42][20][\s\-\.]*\d{3}[\s\-\.]*\d{5}(?:[\s\-\.]*\d{2}){0,2})', re.IGNORECASE),
    # No separator: 4200335663 or 42003356630000
    re.compile(r'(?:^|\s)([42][20]\d{8,12})(?:\s|$)'),
    # Partial with state prefix: 42-003-35663
    re.compile(r'([42][20]\s*[-.]?\s*\d{3}\s*[-.]?\s*\d{5})'),
]

# Patterns that look like APIs but aren't (permit numbers, tracking numbers, etc.)
FALSE_POSITIVE_PATTERNS = [
    re.compile(r'(?:Permit|Track|Filing|Docket|Case)\s*(?:#|No\.?)?\s*:?\s*\d', re.IGNORECASE),
]


def _clean_api_digits(raw: str) -> str:
    """Strip all non-digit characters from a matched API string."""
    return re.sub(r'\D', '', raw)


def _validate_api(digits: str) -> bool:
    """Check if a string of digits looks like a valid TX or NM API number."""
    if len(digits) < 8:
        return False
    # Must start with 42 (TX) or 30 (NM)
    if not (digits.startswith('42') or digits.startswith('30')):
        return False
    # County code (digits 2-4) should be reasonable
    county = int(digits[2:5])
    if county < 1 or county > 510:
        return False
    return True


def _page_to_pil(pdf_path: Path, page_num: int, dpi: int = 300) -> Image.Image:
    """Render a PDF page to a PIL Image at specified DPI."""
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_num]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return img
    finally:
        doc.close()


def _preprocess_for_ocr(img: Image.Image) -> Image.Image:
    """Apply image preprocessing to improve OCR accuracy on old scanned docs."""
    # Convert to grayscale
    gray = ImageOps.grayscale(img)
    # Increase contrast
    gray = ImageOps.autocontrast(gray, cutoff=2)
    # Slight sharpen to help with blurry scans
    gray = gray.filter(ImageFilter.SHARPEN)
    # Binarize with adaptive threshold effect (simple threshold)
    threshold = 140
    binary = gray.point(lambda p: 255 if p > threshold else 0)
    return binary


def _crop_header(img: Image.Image, fraction: float = 0.25) -> Image.Image:
    """Crop the top portion of a page where API numbers typically appear."""
    w, h = img.size
    return img.crop((0, 0, w, int(h * fraction)))


def _ocr_find_api(img: Image.Image) -> Optional[dict]:
    """
    Run Tesseract OCR on an image and search for API number patterns.

    Returns dict with api, confidence, method or None if not found.
    """
    import pytesseract

    # Run OCR with different PSM modes for robustness
    results = []
    for psm in [6, 4, 3]:  # 6=block, 4=single column, 3=full auto
        try:
            text = pytesseract.image_to_string(
                img,
                config=f'--psm {psm} --oem 3',
            )
            if text.strip():
                results.append(text)
        except Exception as e:
            logger.debug(f"Tesseract PSM {psm} failed: {e}")

    # Search all OCR results for API patterns
    all_text = '\n'.join(results)

    for pattern in API_PATTERNS:
        for match in pattern.finditer(all_text):
            raw = match.group(1)
            digits = _clean_api_digits(raw)
            if _validate_api(digits):
                # Check it's not near a false-positive label
                start = max(0, match.start() - 30)
                context = all_text[start:match.start()]
                is_false_positive = any(
                    fp.search(context) for fp in FALSE_POSITIVE_PATTERNS
                )
                if not is_false_positive:
                    return {
                        "api": digits,
                        "confidence": "high",
                        "method": "ocr_tesseract",
                    }

    return None


def _vision_find_api(img: Image.Image) -> Optional[dict]:
    """
    Send a cropped header image to GPT-4o Vision API to find the API number.
    Used as escalation when Tesseract OCR fails (very old/degraded scans).

    Returns dict with api, confidence, method or None if not found.
    """
    from apps.public_core.services.openai_config import get_openai_client

    # Convert PIL image to base64 PNG
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    b64 = base64.b64encode(buf.getvalue()).decode('utf-8')

    prompt = (
        "Look at this cropped header from an RRC (Railroad Commission of Texas) "
        "regulatory form. Find the API well number.\n\n"
        "The API number is typically in the format: 42-XXX-XXXXX (Texas) or "
        "30-XXX-XXXXX (New Mexico). It may appear with or without dashes.\n\n"
        "CRITICAL: If you cannot clearly read an API number, return null. "
        "Do NOT guess.\n\n"
        "Return JSON only:\n"
        '{"api_number": "4200335663" or null, "confidence": "high"|"medium"|"low"}'
    )

    try:
        client = get_openai_client(operation="ocr_api_detect")
        resp = client.chat.completions.create(
            model="gpt-4o",  # Use full gpt-4o, not mini, for better reading
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64}",
                            "detail": "high",
                        },
                    },
                ],
            }],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )

        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)

        api_raw = data.get("api_number")
        if api_raw:
            digits = _clean_api_digits(str(api_raw))
            if _validate_api(digits):
                conf = data.get("confidence", "medium")
                return {
                    "api": digits,
                    "confidence": conf,
                    "method": "ocr_vision_gpt4o",
                }

        return None

    except Exception as e:
        logger.warning(f"Vision API detection failed: {e}")
        return None


def detect_api_from_page(
    pdf_path: Path,
    page_num: int = 0,
    use_vision_fallback: bool = True,
) -> Optional[dict]:
    """
    Detect API number from a single PDF page using OCR.

    Strategy:
    1. Render page at 300 DPI
    2. Crop top 25% (header area where API appears on RRC forms)
    3. Preprocess for OCR (contrast, sharpen, binarize)
    4. Run Tesseract with multiple PSM modes
    5. If OCR fails and use_vision_fallback=True, send cropped header to GPT-4o

    Returns:
        Dict with keys: api, confidence, method, page
        None if no API found.
    """
    try:
        img = _page_to_pil(pdf_path, page_num, dpi=300)
    except Exception as e:
        logger.warning(f"Failed to render page {page_num} of {pdf_path}: {e}")
        return None

    # Try header crop first (top 25%)
    header = _crop_header(img, fraction=0.25)
    processed = _preprocess_for_ocr(header)

    result = _ocr_find_api(processed)
    if result:
        result["page"] = page_num
        return result

    # Try larger crop (top 40%) — some forms have API lower on the page
    header_large = _crop_header(img, fraction=0.40)
    processed_large = _preprocess_for_ocr(header_large)

    result = _ocr_find_api(processed_large)
    if result:
        result["page"] = page_num
        return result

    # Vision fallback — send the full header to GPT-4o
    if use_vision_fallback:
        result = _vision_find_api(header)
        if result:
            result["page"] = page_num
            return result

    return None


def detect_api_from_pdf(
    pdf_path: Path,
    pages: list[int] | None = None,
    max_pages: int = 3,
    use_vision_fallback: bool = True,
) -> Optional[dict]:
    """
    Detect API number from a PDF, scanning multiple pages.

    Args:
        pdf_path: Path to the PDF file
        pages: Specific pages to scan (0-indexed). If None, scans first max_pages pages.
        max_pages: Maximum pages to scan if pages not specified.
        use_vision_fallback: Whether to use GPT-4o Vision as fallback for each page.

    Returns:
        Dict with keys: api, confidence, method, page
        None if no API found on any scanned page.
    """
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)
    doc.close()

    if pages is None:
        pages = list(range(min(max_pages, total_pages)))

    # Scan pages — return on first high confidence hit
    candidates = []
    for page_num in pages:
        if page_num >= total_pages:
            continue

        result = detect_api_from_page(
            pdf_path, page_num,
            use_vision_fallback=False,  # Try OCR on all pages first
        )

        if result:
            if result["confidence"] == "high":
                logger.info(
                    f"[OCR] Found API {result['api']} on page {page_num} "
                    f"via {result['method']} (high confidence)"
                )
                return result
            candidates.append(result)

    # If OCR found a medium/low candidate, return it
    if candidates:
        best = sorted(candidates, key=lambda c: {"high": 0, "medium": 1, "low": 2}.get(c["confidence"], 3))[0]
        logger.info(
            f"[OCR] Best API candidate {best['api']} from page {best['page']} "
            f"via {best['method']} ({best['confidence']} confidence)"
        )
        return best

    # Vision fallback on first page only (expensive)
    if use_vision_fallback and pages:
        result = detect_api_from_page(
            pdf_path, pages[0],
            use_vision_fallback=True,
        )
        if result:
            logger.info(
                f"[OCR] Vision fallback found API {result['api']} on page {result['page']} "
                f"via {result['method']} ({result['confidence']} confidence)"
            )
            return result

    logger.info(f"[OCR] No API found in {pdf_path.name} after scanning {len(pages)} pages")
    return None
