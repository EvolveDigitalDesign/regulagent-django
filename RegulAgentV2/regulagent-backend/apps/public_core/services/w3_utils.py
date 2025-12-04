"""
W-3 Utility Functions

Helper functions for W-3 form generation, including API number normalization,
validation, and formatting.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


def normalize_api_number(api_input: str) -> Optional[str]:
    """
    Normalize API number to 8-digit format (xxx-xxxxx).
    
    Accepts various formats:
    - 10-digit with hyphens: "42-501-70575" → "70575"  (extracts last 8 digits)
    - 14-digit: "04204470575000" → "70575" (extracts last 8 digits)
    - Already normalized: "70575" → "70575" (returns as-is)
    - With hyphens: "070-575" → "070575" (normalizes to 8 digits without hyphens)
    
    Args:
        api_input: API number in any common format
        
    Returns:
        Normalized 8-digit API (without hyphens), or None if invalid
        
    Examples:
        >>> normalize_api_number("42-501-70575")
        "4250170575"
        
        >>> normalize_api_number("04204470575000")
        "4470575"
        
        >>> normalize_api_number("070575")
        "070575"
    """
    if not api_input:
        return None
    
    # Remove all non-digit characters to get raw numeric string
    digits_only = re.sub(r"\D", "", str(api_input))
    
    if not digits_only:
        return None
    
    # Extract the last 8 digits (the actual API, dropping prefix/state codes)
    # For 10-digit (xx-xxx-xxxxx), this gets xxx-xxxxx
    # For 14-digit (ss-xx-xxx-xxxxx), this gets the xxx-xxxxx part
    if len(digits_only) >= 8:
        api_8digit = digits_only[-8:]
    else:
        # If less than 8 digits, pad with zeros on left (shouldn't happen with valid API)
        api_8digit = digits_only.zfill(8)
    
    return api_8digit


def normalize_api_with_hyphen(api_input: str) -> Optional[str]:
    """
    Normalize API number to formatted 8-digit with hyphen (xxx-xxxxx).
    
    Args:
        api_input: API number in any common format
        
    Returns:
        Formatted API with hyphen (e.g., "070-575"), or None if invalid
        
    Examples:
        >>> normalize_api_with_hyphen("42-501-70575")
        "070-575"
        
        >>> normalize_api_with_hyphen("4250170575")
        "070-575"
    """
    api_8digit = normalize_api_number(api_input)
    if not api_8digit or len(api_8digit) != 8:
        return None
    
    # Format as xxx-xxxxx (3 digits, hyphen, 5 digits)
    return f"{api_8digit[:3]}-{api_8digit[3:]}"


def extract_api_prefix_digits(api_input: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract prefix digits and normalized API from a full API number.
    
    For 10-digit API (xx-xxx-xxxxx), returns (prefix, normalized_api).
    For 14-digit API (ss-xx-xxx-xxxxx), returns (state_code, normalized_api).
    
    Args:
        api_input: Full API number
        
    Returns:
        Tuple of (prefix_digits, normalized_api), or (None, None) if invalid
        
    Examples:
        >>> extract_api_prefix_digits("42-501-70575")
        ("42", "4250170575")
        
        >>> extract_api_prefix_digits("04-42-047-057-5000")
        ("04-42", "4470575")
    """
    if not api_input:
        return None, None
    
    digits_only = re.sub(r"\D", "", str(api_input))
    
    if not digits_only or len(digits_only) < 8:
        return None, None
    
    # Extract last 8 digits as normalized API
    api_8digit = digits_only[-8:]
    
    # Remaining digits are prefix
    prefix = digits_only[:-8] if len(digits_only) > 8 else None
    
    return prefix, api_8digit


def validate_api_number(api_input: str) -> bool:
    """
    Validate that an API number is in a recognized format.
    
    Args:
        api_input: API number to validate
        
    Returns:
        True if valid format, False otherwise
    """
    if not api_input:
        return False
    
    normalized = normalize_api_number(api_input)
    return normalized is not None and len(normalized) == 8


def parse_api_10digit(api_input: str) -> Optional[dict]:
    """
    Parse a 10-digit API number (xx-xxx-xxxxx) into components.
    
    Args:
        api_input: 10-digit API (e.g., "42-501-70575")
        
    Returns:
        Dict with keys: state_code, county_code, lease_code, normalized_api
        or None if invalid format
        
    Examples:
        >>> parse_api_10digit("42-501-70575")
        {
            'state_code': '42',
            'county_code': '501',
            'lease_code': '70575',
            'normalized_api': '4250170575'
        }
    """
    if not api_input:
        return None
    
    # Try to match 10-digit format with hyphens: xx-xxx-xxxxx
    match = re.match(r"^(\d{2})-(\d{3})-(\d{5})$", str(api_input).strip())
    
    if match:
        state_code, county_code, lease_code = match.groups()
        normalized = f"{state_code}{county_code}{lease_code}"
        return {
            'state_code': state_code,
            'county_code': county_code,
            'lease_code': lease_code,
            'normalized_api': normalized,
        }
    
    # Try without hyphens as fallback
    digits_only = re.sub(r"\D", "", str(api_input))
    if len(digits_only) == 10:
        state_code = digits_only[:2]
        county_code = digits_only[2:5]
        lease_code = digits_only[5:10]
        return {
            'state_code': state_code,
            'county_code': county_code,
            'lease_code': lease_code,
            'normalized_api': digits_only,
        }
    
    return None

