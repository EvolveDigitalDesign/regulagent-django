"""
API Number Normalization Service

Centralized utilities for normalizing, validating, and retrieving wells by API number.
Handles 8-digit, 10-digit, and 14-digit API formats.

Usage:
    from apps.public_core.services.api_normalization import get_well_by_api

    well = get_well_by_api(api_input)  # Raises Http404 if invalid or not found
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from django.http import Http404
from django.shortcuts import get_object_or_404


def normalize_api_14digit(api_input: str) -> Optional[str]:
    """
    Normalize API number to 14-digit format (standard RRC/EIA format).

    Accepts various input formats:
    - 8-digit: "70575000" -> assumes TX, pads to 14 digits
    - 10-digit with hyphens: "42-501-70575" -> "42501705750000"
    - 14-digit: "42501705750000" -> "42501705750000" (returns as-is)
    - 14-digit with hyphens: "42-501-70575-00" -> "42501705750000"

    Args:
        api_input: API number in any common format

    Returns:
        14-digit API string, or None if invalid

    Examples:
        >>> normalize_api_14digit("42-501-70575")
        "42501705750000"

        >>> normalize_api_14digit("70575000")
        "42705750000000"  # Assumes TX state code 42

        >>> normalize_api_14digit("42501705750000")
        "42501705750000"

        >>> normalize_api_14digit("")
        None
    """
    if not api_input:
        return None

    # Remove all non-digit characters
    digits_only = re.sub(r"\D", "", str(api_input))

    if not digits_only:
        return None

    length = len(digits_only)

    # 14-digit: already complete
    if length == 14:
        return digits_only

    # 10-digit: pad with 0000 at the end (sequence/sidetrack numbers)
    if length == 10:
        return digits_only + "0000"

    # 8-digit: assume Texas (state code 42) and pad
    # Format: 42 + 8 digits + 0000
    if length == 8:
        return "42" + digits_only + "0000"

    # If we have more than 14 digits, take the first 14
    if length > 14:
        return digits_only[:14]

    # Less than 8 digits is too short to be a valid API
    if length < 8:
        return None

    # For lengths 9, 11-13: pad on the right to reach 14 digits
    # This handles edge cases where partial API numbers are provided
    if length < 14:
        return digits_only + "0" * (14 - length)

    return None


def validate_api_format(api_input: str) -> Tuple[bool, str]:
    """
    Validate API number format and return detailed result.

    Args:
        api_input: API number to validate

    Returns:
        Tuple of (is_valid, message) where message explains the validation result

    Examples:
        >>> validate_api_format("42-501-70575")
        (True, "Valid API format")

        >>> validate_api_format("")
        (False, "API number cannot be empty")

        >>> validate_api_format("abc123")
        (False, "API must contain only digits and hyphens")
    """
    if not api_input:
        return False, "API number cannot be empty"

    # Check for invalid characters (allow digits and hyphens only)
    if not re.match(r"^[\d\-]+$", str(api_input)):
        return False, "API must contain only digits and hyphens"

    # Try to normalize
    normalized = normalize_api_14digit(api_input)

    if normalized is None:
        return False, "API number format is invalid or too short"

    # Verify the normalized result is exactly 14 digits
    if len(normalized) != 14:
        return False, f"Normalized API is {len(normalized)} digits, expected 14"

    return True, "Valid API format"


def get_well_by_api(api_input: str):
    """
    Safely retrieve WellRegistry instance by API number.

    Normalizes the input API to 14-digit format and performs exact lookup.
    Raises Http404 with descriptive message if invalid or not found.

    Args:
        api_input: API number in any supported format (8, 10, or 14 digit)

    Returns:
        WellRegistry instance

    Raises:
        Http404: If API format is invalid or well is not found

    Examples:
        >>> well = get_well_by_api("42-501-70575")
        >>> well.api14
        "42501705750000"

        >>> well = get_well_by_api("invalid")
        # Raises Http404
    """
    from apps.public_core.models import WellRegistry

    # Validate format first
    is_valid, message = validate_api_format(api_input)
    if not is_valid:
        raise Http404(f"Invalid API format: {message}")

    # Normalize to 14-digit format
    api_14 = normalize_api_14digit(api_input)
    if not api_14:
        raise Http404(f"Could not normalize API number: {api_input}")

    # Perform exact lookup (efficient, uses index)
    return get_object_or_404(WellRegistry, api14=api_14)


def get_well_by_api_lenient(api_input: str):
    """
    Retrieve WellRegistry with fallback to last-8-digit match.

    This is a legacy compatibility function that first tries exact match,
    then falls back to the inefficient icontains pattern.

    DEPRECATED: Use get_well_by_api() for new code.
    Only use this if you need backward compatibility with old behavior.

    Args:
        api_input: API number in any format

    Returns:
        WellRegistry instance or None if not found
    """
    from apps.public_core.models import WellRegistry

    # Try exact match first
    try:
        return get_well_by_api(api_input)
    except Http404:
        pass

    # Fallback to last-8-digit search (inefficient but compatible)
    digits_only = re.sub(r"\D", "", str(api_input or ""))
    if len(digits_only) >= 8:
        last_8 = digits_only[-8:]
        return WellRegistry.objects.filter(api14__icontains=last_8).first()

    return None
