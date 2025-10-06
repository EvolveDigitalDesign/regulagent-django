from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .fetchers import RRCFetcher
from .ocr import OCROrchestrator
from .normalizer import Normalizer, Finding


def _is_api10(value: str) -> bool:
    digits = "".join(filter(str.isdigit, value or ""))
    return len(digits) == 10


def _state_prefix(api10: str) -> str:
    return ("".join(filter(str.isdigit, api10)))[:2]


@dataclass
class Artifact:
    file_path: str
    doc_type: str
    ocr_backend: str
    text_score: float


@dataclass
class ExtractionOutcome:
    api10: str
    state_code: str
    workspace_dir: str
    artifacts: List[Artifact]
    normalized: Dict[str, Any]
    findings: List[Dict[str, Any]]


async def extract_for_api(api10: str) -> ExtractionOutcome:
    if not _is_api10(api10):
        raise ValueError("API must be 10 digits")
    state = _state_prefix(api10)
    temp_root = Path(tempfile.gettempdir()) / f"agentic_ingest_{api10}_{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"
    temp_root.mkdir(parents=True, exist_ok=True)

    artifacts: List[Artifact] = []
    findings: List[Finding] = []
    normalized: Dict[str, Any] = {}

    files: List[Path] = []
    if state == "42":
        files, fetch_findings = await RRCFetcher(api10, temp_root).fetch()
        for f in fetch_findings:
            findings.append(Finding(code=f.get("code",""), severity=f.get("severity","minor"), message=f.get("message",""), context=f.get("context") or {}))
    else:
        findings.append(Finding(code="STATE_NOT_SUPPORTED", severity="minor", message="Only Texas (42) supported in MVP", context={"api10": api10, "state": state}))

    ocr = OCROrchestrator()
    normalizer = Normalizer()

    for pdf in files:
        backend, text, score = ocr.run_all(pdf)
        doc_type, record, doc_findings = normalizer.normalize(pdf.name, text)
        # GAU expiry handling: drop expired GAU records but retain finding
        if doc_type == "gau_letter" and record.get("expired"):
            findings.extend(doc_findings)
            artifacts.append(Artifact(str(pdf), doc_type, backend, score))
            continue
        normalized.setdefault(doc_type, [])
        normalized[doc_type].append(record)
        findings.extend(doc_findings)
        artifacts.append(Artifact(str(pdf), doc_type, backend, score))

    return ExtractionOutcome(
        api10="".join(filter(str.isdigit, api10)),
        state_code=state,
        workspace_dir=str(temp_root),
        artifacts=artifacts,
        normalized=normalized,
        findings=[f.as_dict() for f in findings],
    )


