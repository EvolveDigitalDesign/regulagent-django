from __future__ import annotations

import re
from datetime import datetime, timedelta
import hashlib
from typing import Any, Dict, List, Optional, Tuple


def _parse_dates(text: str) -> List[datetime]:
    dates: List[datetime] = []
    mmddyyyy = re.findall(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", text)
    for m, d, y in mmddyyyy:
        try:
            dates.append(datetime(int(y), int(m), int(d)))
        except Exception:
            pass
    return dates


class Finding:
    def __init__(self, code: str, severity: str, message: str, context: Dict[str, Any]):
        self.code = code
        self.severity = severity
        self.message = message
        self.context = context

    def as_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "message": self.message, "context": self.context}


class Normalizer:
    def normalize(self, filename: str, text: str) -> Tuple[str, Dict[str, Any], List[Finding]]:
        name = filename.lower()
        findings: List[Finding] = []
        doc_type = "unknown"
        record: Dict[str, Any] = {
            "source_file": filename,
            "extracted_at": datetime.utcnow().isoformat(),
            "raw_text": text or "",
            "raw_text_sha256": hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
        }

        if "gau" in name:
            doc_type = "gau_letter"
            record.update(self._normalize_gau(text, findings))
        elif "w-2" in name or " w2" in name or name.endswith("_w2.pdf"):
            doc_type = "w2_completion"
            record.update(self._normalize_w2(text))
        elif "w-15" in name or " w15" in name or name.endswith("_w15.pdf"):
            doc_type = "w15_completion"
            record.update(self._normalize_w15(text))
        elif "schematic" in name:
            doc_type = "well_schematic"
            record.update(self._normalize_schematic(text))
        elif "formation" in name:
            doc_type = "formation_tops"
            record.update(self._normalize_formation_tops(text))
        else:
            record["doc_type_guess"] = "unknown"
            record.update(self._extract_kv_pairs(text))
        return doc_type, record, findings

    def _normalize_gau(self, text: str, findings: List[Finding]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        m_unit = re.search(r"(?:Unit|GAU)\s*Name[:\-]?\s*(.+)", text, flags=re.I)
        if m_unit:
            out["unit_name"] = m_unit.group(1).strip()
        dates = _parse_dates(text)
        if dates:
            filed = min(dates)
            out["filed_at"] = filed.date().isoformat()
            expires = filed + timedelta(days=5 * 365)
            out["expires_at"] = expires.date().isoformat()
            if datetime.utcnow() > expires:
                findings.append(Finding("GAU_EXPIRED", "major", "GAU letter older than 5 years; discarding", {"filed_at": out["filed_at"], "expires_at": out["expires_at"]}))
                out["expired"] = True
        return out

    def _normalize_w2(self, text: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        m_field = re.search(r"Field\s*[:\-]?\s*([^\n]+)", text, flags=re.I)
        if m_field:
            out["field_name"] = m_field.group(1).strip()
        out.update(self._extract_kv_pairs(text))
        return out

    def _normalize_w15(self, text: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        out.update(self._extract_kv_pairs(text))
        return out

    def _normalize_schematic(self, text: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        out.update(self._extract_kv_pairs(text))
        return out

    def _normalize_formation_tops(self, text: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        out.update(self._extract_kv_pairs(text))
        return out

    def _extract_kv_pairs(self, text: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for line in (text or "").splitlines():
            m = re.match(r"\s*([A-Za-z0-9 /_\-\.\(\)]+)\s*[:=]\s*(.+)\s*$", line)
            if not m:
                continue
            key = m.group(1).strip().lower().replace(" ", "_")
            val = m.group(2).strip()
            if key in out:
                if isinstance(out[key], list):
                    out[key].append(val)
                else:
                    out[key] = [out[key], val]
            else:
                out[key] = val
        return out


