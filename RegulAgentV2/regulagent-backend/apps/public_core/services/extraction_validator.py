"""
Post-extraction validation for OpenAI Vision extracted data.

Catches hallucinated values, impossible numbers, and column-confusion errors
before they get persisted to ExtractedDocument.json_data.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Known-bad patterns that indicate hallucination
HALLUCINATED_OPERATORS = {
    "abc oil company", "xyz energy", "acme oil", "test operator",
    "sample operator", "example corp", "john doe",
}

HALLUCINATED_API_PATTERNS = [
    r"^42-?123-?45678",  # Classic placeholder
    r"^12-?345-?67890",
    r"^00-?000-?00000",
]


def validate_extracted_data(doc_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and sanitize extracted JSON data.

    Mutates the dict in place and returns it.
    Nulls out values that fail validation rather than rejecting the whole document.
    """
    if not data or not isinstance(data, dict):
        return data

    _validate_well_info(data)
    _validate_operator_info(data)
    _validate_header(data)

    if doc_type in ("w2", "w3", "w3a", "c_101", "c_103", "c_105"):
        _validate_casing_records(data)

    if doc_type in ("w3",):
        _validate_plug_records(data)

    if doc_type in ("w15",):
        _validate_cementing_data(data)

    return data


def _validate_well_info(data: Dict[str, Any]) -> None:
    """Validate well_info section."""
    wi = data.get("well_info")
    if not isinstance(wi, dict):
        return

    # Validate API number
    api = wi.get("api")
    if api and isinstance(api, str):
        # Check for hallucinated API patterns
        clean = re.sub(r"\D+", "", api)
        for pattern in HALLUCINATED_API_PATTERNS:
            if re.match(pattern, api.replace(" ", "")):
                logger.warning(f"[Validator] Nulling hallucinated API: {api}")
                wi["api"] = None
                break

        # API should have at least 5 digits when cleaned
        if wi.get("api") and len(clean) < 5:
            logger.warning(f"[Validator] Nulling too-short API: {api} (cleaned: {clean})")
            wi["api"] = None

    # Validate total_depth_ft
    td = wi.get("total_depth_ft")
    if td is not None:
        try:
            td_num = float(td)
            if td_num < 0 or td_num > 50000:
                logger.warning(f"[Validator] Nulling implausible total_depth_ft: {td}")
                wi["total_depth_ft"] = None
        except (TypeError, ValueError):
            pass

    # Validate coordinates
    loc = wi.get("location")
    if isinstance(loc, dict):
        lat = loc.get("lat")
        lon = loc.get("lon")
        if lat is not None:
            try:
                lat_f = float(lat)
                if lat_f < 25 or lat_f > 37:  # TX latitude range
                    logger.warning(f"[Validator] Nulling out-of-TX-range latitude: {lat}")
                    loc["lat"] = None
            except (TypeError, ValueError):
                loc["lat"] = None
        if lon is not None:
            try:
                lon_f = float(lon)
                # TX longitude: roughly -93 to -107 (negative)
                if lon_f > 0 or lon_f < -110 or lon_f > -90:
                    if not (-110 <= lon_f <= -90):
                        logger.warning(f"[Validator] Nulling out-of-TX-range longitude: {lon}")
                        loc["lon"] = None
            except (TypeError, ValueError):
                loc["lon"] = None


def _validate_operator_info(data: Dict[str, Any]) -> None:
    """Validate operator_info section."""
    oi = data.get("operator_info")
    if not isinstance(oi, dict):
        return

    name = oi.get("name")
    if name and isinstance(name, str):
        if name.lower().strip() in HALLUCINATED_OPERATORS:
            logger.warning(f"[Validator] Nulling hallucinated operator name: {name}")
            oi["name"] = None

        # Operator name should be at least 3 chars and not all digits
        if name and (len(name.strip()) < 3 or name.strip().isdigit()):
            logger.warning(f"[Validator] Nulling suspicious operator name: {name}")
            oi["name"] = None


def _validate_header(data: Dict[str, Any]) -> None:
    """Validate header section."""
    header = data.get("header")
    if not isinstance(header, dict):
        return

    # Validate date_filed
    date = header.get("date_filed")
    if date and isinstance(date, str):
        # Basic date sanity: should match some date-like pattern
        # Allow: YYYY-MM-DD, MM/DD/YYYY, Month DD, YYYY, etc.
        clean_date = re.sub(r"[^0-9a-zA-Z/-]", "", date)
        if len(clean_date) < 4:
            logger.warning(f"[Validator] Nulling suspicious date: {date}")
            header["date_filed"] = None

    # Validate tracking number - check for hallucinated patterns
    tracking = header.get("tracking_number") or header.get("tracking_no")
    if tracking and isinstance(tracking, str):
        if tracking.strip().lower() in ("123456", "000000", "xxxxxx", "n/a"):
            key = "tracking_number" if "tracking_number" in header else "tracking_no"
            logger.warning(f"[Validator] Nulling hallucinated tracking number: {tracking}")
            header[key] = None


def _validate_casing_records(data: Dict[str, Any]) -> None:
    """Validate casing_record array — catch column confusion."""
    records = data.get("casing_record")
    if not isinstance(records, list):
        return

    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue

        # Weight per foot: typically 1-100 lb/ft for oilfield casing
        weight = rec.get("weight_ppf") or rec.get("weight_per_ft")
        weight_key = "weight_ppf" if "weight_ppf" in rec else "weight_per_ft"
        if weight is not None:
            try:
                w = float(weight)
                if w > 150 or w < 0.5:
                    logger.warning(
                        f"[Validator] Casing record {i}: implausible weight {w} ppf — "
                        f"likely column confusion. Nulling."
                    )
                    rec[weight_key] = None
            except (TypeError, ValueError):
                pass

        # Size in inches: typically 2.375 to 20 inches
        size = rec.get("size_in")
        if size is not None:
            try:
                s = float(size)
                if s > 30 or s < 1:
                    logger.warning(
                        f"[Validator] Casing record {i}: implausible size {s} in — nulling."
                    )
                    rec["size_in"] = None
            except (TypeError, ValueError):
                pass

        # Hole size: typically 3.75 to 26 inches
        hole = rec.get("hole_size_in")
        if hole is not None:
            try:
                h = float(hole)
                if h > 36 or h < 2:
                    logger.warning(
                        f"[Validator] Casing record {i}: implausible hole_size {h} in — nulling."
                    )
                    rec["hole_size_in"] = None
            except (TypeError, ValueError):
                pass

        # Bottom/shoe depth: should be positive, < 50000
        for depth_key in ("bottom_ft", "shoe_depth_ft", "top_ft"):
            depth = rec.get(depth_key)
            if depth is not None:
                try:
                    d = float(depth)
                    if d < 0 or d > 50000:
                        logger.warning(
                            f"[Validator] Casing record {i}: implausible {depth_key}={d} — nulling."
                        )
                        rec[depth_key] = None
                except (TypeError, ValueError):
                    pass


def _validate_plug_records(data: Dict[str, Any]) -> None:
    """Validate plug_record array."""
    records = data.get("plug_record")
    if not isinstance(records, list):
        return

    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue

        # Sacks: typically 1-500 for a single plug, rarely up to 1000
        sacks = rec.get("sacks")
        if sacks is not None:
            try:
                s = float(sacks)
                if s > 2000 or s < 0:
                    logger.warning(
                        f"[Validator] Plug record {i}: implausible sacks={s} — nulling."
                    )
                    rec["sacks"] = None
                elif s > 500:
                    logger.info(
                        f"[Validator] Plug record {i}: high sack count={s} — flagging but keeping."
                    )
            except (TypeError, ValueError):
                pass

        # Slurry weight: typically 11-18 ppg
        slurry = rec.get("slurry_weight_ppg")
        if slurry is not None:
            try:
                sw = float(slurry)
                if sw < 8 or sw > 25:
                    logger.warning(
                        f"[Validator] Plug record {i}: implausible slurry_weight={sw} ppg — nulling."
                    )
                    rec["slurry_weight_ppg"] = None
            except (TypeError, ValueError):
                pass

        # Depths: top should be <= bottom (unless it's a surface plug)
        top = rec.get("depth_top_ft")
        bottom = rec.get("depth_bottom_ft")
        if top is not None and bottom is not None:
            try:
                t, b = float(top), float(bottom)
                if t > 50000 or b > 50000 or t < 0 or b < 0:
                    logger.warning(
                        f"[Validator] Plug record {i}: depth out of range top={t} bottom={b} — nulling."
                    )
                    if t > 50000 or t < 0:
                        rec["depth_top_ft"] = None
                    if b > 50000 or b < 0:
                        rec["depth_bottom_ft"] = None
            except (TypeError, ValueError):
                pass

        # Cement class: should be a single letter (A, C, G, H) or short string
        cc = rec.get("cement_class")
        if cc and isinstance(cc, str) and len(cc) > 5:
            logger.warning(
                f"[Validator] Plug record {i}: suspicious cement_class='{cc}' — truncating to first word."
            )
            rec["cement_class"] = cc.split()[0][:2] if cc.split() else None


def _validate_cementing_data(data: Dict[str, Any]) -> None:
    """Validate W-15 cementing_data array."""
    records = data.get("cementing_data")
    if not isinstance(records, list):
        return

    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue

        # Slurry density: typically 11-18 ppg
        density = rec.get("slurry_density_ppg")
        if density is not None:
            try:
                d = float(density)
                if d < 8 or d > 25:
                    logger.warning(
                        f"[Validator] Cementing record {i}: implausible density={d} ppg — nulling."
                    )
                    rec["slurry_density_ppg"] = None
            except (TypeError, ValueError):
                pass

        # Sacks
        sacks = rec.get("sacks")
        if sacks is not None:
            try:
                s = float(sacks)
                if s > 5000 or s < 0:
                    logger.warning(
                        f"[Validator] Cementing record {i}: implausible sacks={s} — nulling."
                    )
                    rec["sacks"] = None
            except (TypeError, ValueError):
                pass
