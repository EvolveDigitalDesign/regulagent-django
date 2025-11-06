"""
File validation service for tenant-uploaded documents.

Performs security scanning and API number verification before marking
documents as validated.

Phase 1 Implementation:
1. OpenAI security scan for prompt injections and malicious content
2. API number extraction and verification
3. Validation result with errors for rejection tracking
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of file validation."""
    is_valid: bool
    errors: List[str]
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


def _openai_client():  # pragma: no cover
    """Lazy import OpenAI client."""
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured")
    return OpenAI(api_key=api_key)


def normalize_api(api_str: str) -> str:
    """
    Normalize API number to 14-digit format: XXYYYZZZZZCCSS
    Handles various input formats (10-digit, 12-digit, 14-digit, with/without dashes).
    
    Format: XX-YYY-ZZZZZ-CC-SS
    - XX: State (42 for Texas)
    - YYY: County
    - ZZZZZ: Well number
    - CC: Completion number
    - SS: Sidetrack number
    """
    if not api_str:
        return ""
    
    # Remove all non-digits
    digits = ''.join(c for c in str(api_str) if c.isdigit())
    
    # Handle different lengths
    if len(digits) == 10:
        # 10-digit can be:
        # - XXYYYZZZZZ (state + county + well, missing completion/sidetrack)
        # - YYYZZZZZZC (county + well + completion, missing state/sidetrack)
        if digits.startswith('42'):
            # Already has state code (42 for Texas)
            # 4212345678 → 42-123-45678-00-00
            digits = digits + "0000"  # Pad completion and sidetrack
        else:
            # Missing state code, assume Texas
            # 0001234500 → 42-000-12345-00-00
            digits = "42" + digits + "00"
    elif len(digits) == 12:
        # 12-digit: XXYYYZZZZZCC
        # Check if starts with valid state code (42 for Texas)
        if digits.startswith('42'):
            # Has state code, just pad sidetrack
            digits = digits + "00"
        else:
            # Assume first 2 digits are not state, prepend 42
            # But this is ambiguous - for now just pad
            digits = digits + "00"
    elif len(digits) == 14:
        # Already 14 digits
        pass
    else:
        # Return as-is for unexpected formats
        return digits
    
    # Ensure exactly 14 digits
    if len(digits) == 14:
        return digits
    
    return digits


def api_matches(extracted_api: str, expected_api: str, fuzzy: bool = True) -> bool:
    """
    Check if extracted API matches expected API.
    
    Args:
        extracted_api: API from document extraction
        expected_api: API provided by user
        fuzzy: If True, match on last 8 digits (well number + completion)
               If False, require exact 14-digit match
    
    Returns:
        True if APIs match
    """
    norm_extracted = normalize_api(extracted_api)
    norm_expected = normalize_api(expected_api)
    
    if not norm_extracted or not norm_expected:
        return False
    
    if fuzzy:
        # Match on last 8 digits (well number + completion suffix)
        return norm_extracted[-8:] == norm_expected[-8:]
    else:
        # Exact 14-digit match
        return norm_extracted == norm_expected


def openai_security_scan(file_path: Path, document_type: str = "unknown") -> ValidationResult:
    """
    Scan document for security issues using OpenAI moderation.
    
    Checks for:
    - Prompt injection attempts
    - Malicious content
    - Unsafe instructions
    
    Args:
        file_path: Path to PDF file
        document_type: Type of document (w2, gau, etc.)
    
    Returns:
        ValidationResult with is_valid=True if safe
    """
    try:
        # Import extraction utilities
        from apps.public_core.services.openai_extraction import _extract_pdf_text
        
        # Extract text from PDF
        try:
            text = _extract_pdf_text(file_path, max_chars=50000)
        except Exception as e:
            logger.exception("openai_security_scan: failed to extract PDF text")
            return ValidationResult(
                is_valid=False,
                errors=[f"Failed to read PDF: {str(e)}"]
            )
        
        if not text or len(text.strip()) < 50:
            return ValidationResult(
                is_valid=False,
                errors=["PDF appears empty or unreadable"]
            )
        
        # OpenAI Moderation API check
        client = _openai_client()
        
        try:
            moderation_response = client.moderations.create(input=text[:32000])  # API limit
            
            # Check if flagged
            result = moderation_response.results[0]
            if result.flagged:
                # Get flagged categories
                flagged_categories = [
                    cat for cat, flagged in result.categories.__dict__.items()
                    if flagged
                ]
                
                return ValidationResult(
                    is_valid=False,
                    errors=[
                        f"Security scan failed: content flagged for {', '.join(flagged_categories)}"
                    ]
                )
            
        except Exception as e:
            logger.exception("openai_security_scan: moderation API call failed")
            return ValidationResult(
                is_valid=False,
                errors=[f"Security scan failed: {str(e)}"]
            )
        
        # Additional heuristics for prompt injection
        prompt_injection_patterns = [
            "ignore previous instructions",
            "ignore all previous",
            "disregard all previous",
            "new instructions:",
            "system message:",
            "override instructions",
            "jailbreak",
            "act as if you are",
            "pretend you are",
        ]
        
        text_lower = text.lower()
        detected_patterns = [
            pattern for pattern in prompt_injection_patterns
            if pattern in text_lower
        ]
        
        if detected_patterns:
            return ValidationResult(
                is_valid=False,
                errors=[
                    f"Potential prompt injection detected: {', '.join(detected_patterns[:3])}"
                ],
                warnings=["Document contains suspicious instruction-like patterns"]
            )
        
        # Passed all checks
        return ValidationResult(is_valid=True, errors=[])
        
    except Exception as e:
        logger.exception("openai_security_scan: unexpected error")
        return ValidationResult(
            is_valid=False,
            errors=[f"Validation system error: {str(e)}"]
        )


def verify_api_number(
    file_path: Path,
    document_type: str,
    expected_api: str,
    fuzzy_match: bool = True
) -> ValidationResult:
    """
    Extract API number from document and verify it matches expected.
    
    Args:
        file_path: Path to PDF file
        document_type: Type of document (w2, gau, w15, etc.)
        expected_api: API number provided by user
        fuzzy_match: If True, match on last 8 digits
    
    Returns:
        ValidationResult with is_valid=True if API matches
    """
    try:
        # Import extraction utilities
        from apps.public_core.services.openai_extraction import extract_json_from_pdf
        
        # Extract document
        try:
            extraction_result = extract_json_from_pdf(file_path, document_type)
        except Exception as e:
            logger.exception("verify_api_number: extraction failed")
            return ValidationResult(
                is_valid=False,
                errors=[f"Failed to extract document: {str(e)}"]
            )
        
        if extraction_result.errors:
            return ValidationResult(
                is_valid=False,
                errors=[f"Extraction errors: {', '.join(extraction_result.errors)}"]
            )
        
        # Get API from extracted data
        json_data = extraction_result.json_data
        
        # Try common API field locations
        extracted_api = None
        if "well_info" in json_data:
            extracted_api = json_data["well_info"].get("api") or json_data["well_info"].get("api_number")
        
        if not extracted_api and "header" in json_data:
            extracted_api = json_data["header"].get("api") or json_data["header"].get("api_number")
        
        if not extracted_api:
            # Fallback: search all top-level fields
            for key in ["api", "api_number", "api14", "well_api"]:
                if key in json_data:
                    extracted_api = json_data[key]
                    break
        
        if not extracted_api:
            return ValidationResult(
                is_valid=False,
                errors=["Could not extract API number from document"]
            )
        
        # Verify API match
        if api_matches(extracted_api, expected_api, fuzzy=fuzzy_match):
            return ValidationResult(
                is_valid=True,
                errors=[],
                warnings=[
                    f"Matched API: {extracted_api} (expected: {expected_api})"
                ]
            )
        else:
            return ValidationResult(
                is_valid=False,
                errors=[
                    f"API mismatch: document contains '{extracted_api}', expected '{expected_api}'"
                ]
            )
        
    except Exception as e:
        logger.exception("verify_api_number: unexpected error")
        return ValidationResult(
            is_valid=False,
            errors=[f"API verification system error: {str(e)}"]
        )


def validate_uploaded_file(
    file_path: Path,
    document_type: str,
    expected_api: str,
    skip_security_scan: bool = False,
    fuzzy_api_match: bool = True
) -> ValidationResult:
    """
    Complete validation pipeline for tenant-uploaded files.
    
    Validation steps:
    1. Security scan (OpenAI moderation + prompt injection detection)
    2. API number extraction and verification
    
    Args:
        file_path: Path to uploaded PDF
        document_type: Document type (w2, gau, w15, etc.)
        expected_api: API number provided by user
        skip_security_scan: Skip security checks (for testing only)
        fuzzy_api_match: Match on last 8 digits vs exact 14-digit
    
    Returns:
        ValidationResult with is_valid=True if all checks pass
    """
    all_errors = []
    all_warnings = []
    
    # Step 1: Security scan
    if not skip_security_scan:
        logger.info(f"validate_uploaded_file: running security scan for {file_path}")
        security_result = openai_security_scan(file_path, document_type)
        
        if not security_result.is_valid:
            all_errors.extend(security_result.errors)
            return ValidationResult(
                is_valid=False,
                errors=all_errors,
                warnings=all_warnings
            )
        
        all_warnings.extend(security_result.warnings)
    
    # Step 2: API verification
    logger.info(f"validate_uploaded_file: verifying API number for {file_path}")
    api_result = verify_api_number(
        file_path,
        document_type,
        expected_api,
        fuzzy_match=fuzzy_api_match
    )
    
    if not api_result.is_valid:
        all_errors.extend(api_result.errors)
        return ValidationResult(
            is_valid=False,
            errors=all_errors,
            warnings=all_warnings
        )
    
    all_warnings.extend(api_result.warnings)
    
    # All checks passed
    logger.info(f"validate_uploaded_file: validation PASSED for {file_path}")
    return ValidationResult(
        is_valid=True,
        errors=[],
        warnings=all_warnings
    )

