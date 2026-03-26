"""
Shared text processing utilities for the public_core services layer.

Provides helpers for converting structured JSON sections to readable prose
and for splitting long text into overlapping chunks for downstream processing.
"""
import json as _json


def json_to_prose(section_name: str, json_text: str) -> str:
    """Convert JSON section text to human-readable prose for better LLM comprehension."""
    try:
        data = _json.loads(json_text)
    except (ValueError, TypeError):
        return json_text

    section_lower = section_name.lower()

    # casing_record: array of casing entries
    if "casing" in section_lower and isinstance(data, list):
        lines = []
        for entry in data:
            ct = entry.get("casing_type", "Unknown")
            diam = entry.get("diameter", "?")
            bottom = entry.get("bottom", entry.get("shoe_depth_ft", "?"))
            top = entry.get("top", 0)
            sacks = entry.get("sacks", "?")
            weight = entry.get("weight", "?")
            lines.append(
                f"{ct} casing: {diam}\" diameter, {weight} lb/ft, "
                f"set {top}-{bottom} ft, {sacks} sacks cement"
            )
        return "\n".join(lines) if lines else json_text

    # description dict: extract work_description
    if "description" in section_lower and isinstance(data, dict):
        work_desc = data.get("work_description") or data.get("description") or ""
        purpose = data.get("purpose", "")
        if work_desc:
            result = work_desc
            if purpose:
                result += f" (Purpose: {purpose})"
            return result
        return json_text

    # well_info dict
    if "well_info" in section_lower and isinstance(data, dict):
        parts = []
        if data.get("well_no") or data.get("well_name"):
            parts.append(f"Well {data.get('well_no') or data.get('well_name', '?')}")
        if data.get("operator") or data.get("operator_name"):
            parts.append(f"Operator: {data.get('operator') or data.get('operator_name')}")
        if data.get("field") or data.get("field_name"):
            parts.append(f"Field: {data.get('field') or data.get('field_name')}")
        if data.get("county"):
            parts.append(f"County: {data.get('county')}")
        if data.get("api") or data.get("api_number"):
            parts.append(f"API: {data.get('api') or data.get('api_number')}")
        return ", ".join(parts) if parts else json_text

    # notice_type dict
    if "notice" in section_lower and isinstance(data, dict):
        nt = data.get("type", "")
        desc = data.get("description", "")
        return f"Notice type: {nt}. {desc}".strip() if nt else json_text

    return json_text


def chunk_text(text: str, max_chars: int = 500, overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks, breaking at sentence boundaries.

    If the text is shorter than max_chars, returns [text] unchanged.
    Chunks overlap by `overlap` characters to preserve context across boundaries.
    """
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        if end >= len(text):
            chunks.append(text[start:])
            break

        # Try to break at a sentence boundary (. ! ?)
        # Look backwards from `end` for sentence-ending punctuation
        best_break = -1
        for i in range(end, max(start + max_chars // 2, start), -1):
            if text[i - 1] in '.!?\n' and (i >= len(text) or text[i] in ' \n\t'):
                best_break = i
                break

        if best_break > start:
            chunks.append(text[start:best_break].strip())
            start = best_break - overlap
        else:
            # No sentence boundary found — break at a space
            space_pos = text.rfind(' ', start + max_chars // 2, end)
            if space_pos > start:
                chunks.append(text[start:space_pos].strip())
                start = space_pos - overlap + 1
            else:
                # Hard break
                chunks.append(text[start:end].strip())
                start = end - overlap

        if start < 0:
            start = 0

    return [c for c in chunks if c]
