from typing import Any, Dict, List, Optional


CRITICAL = "critical"
MAJOR = "major"
MINOR = "minor"


class VCodes:
    SURFACE_PLUG_MISSING = "SURFACE_PLUG_MISSING"
    SURFACE_SHOE_DEPTH_UNKNOWN = "SURFACE_SHOE_DEPTH_UNKNOWN"
    CAP_ABOVE_PERF_SHORT = "CAP_ABOVE_PERF_SHORT"
    BELOW_CIBP = "BELOW_CIBP"
    DUQW_ISOLATION_MISSING = "DUQW_ISOLATION_MISSING"
    OH_METHOD_MISMATCH = "OH_METHOD_MISMATCH"
    INSUFFICIENT_SHOE_COVERAGE = "INSUFFICIENT_SHOE_COVERAGE"
    STEP_INTERVAL_GAP = "STEP_INTERVAL_GAP"
    STEP_INTERVAL_OVERLAP = "STEP_INTERVAL_OVERLAP"
    MISSING_CITATION = "MISSING_CITATION"
    EFFECTIVE_DATE_MISMATCH = "EFFECTIVE_DATE_MISMATCH"
    DENSITY_OUT_OF_RANGE = "DENSITY_OUT_OF_RANGE"
    EXCESS_OUT_OF_RANGE = "EXCESS_OUT_OF_RANGE"
    SPACER_INADEQUATE = "SPACER_INADEQUATE"


def make_violation(
    code: str,
    severity: str,
    message: str,
    context: Optional[Dict[str, Any]] = None,
    citations: Optional[List[str]] = None,
    autofix_hint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "context": context or {},
        "citations": citations or [],
        "autofix_hint": autofix_hint or {},
    }


