from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

try:
    import pdfplumber  # type: ignore
except Exception:
    pdfplumber = None  # type: ignore

try:
    import boto3  # type: ignore
except Exception:
    boto3 = None  # type: ignore

try:
    from google.cloud import vision  # type: ignore
except Exception:
    vision = None  # type: ignore


def _score_text_quality(text: str) -> float:
    if not text:
        return 0.0
    length_score = min(len(text) / 2000.0, 1.0)
    digits = sum(c.isdigit() for c in text)
    digit_ratio = digits / max(len(text), 1)
    token_hits = sum(1 for t in ("API", "W-2", "W-15", "GAU", "Operator", "District") if t.lower() in text.lower())
    return 0.5 * length_score + 0.3 * digit_ratio + 0.2 * (min(token_hits, 5) / 5.0)


class OCROrchestrator:
    def __init__(self) -> None:
        self.has_textract = boto3 is not None and (os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_PROFILE"))
        self.has_vision = vision is not None and os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    def run_all(self, pdf_path: Path) -> Tuple[str, str, float]:
        texts: list[tuple[str, str]] = []
        if self.has_textract:
            try:
                texts.append(("textract", self._run_textract(pdf_path)))
            except Exception:
                pass
        if self.has_vision:
            try:
                texts.append(("vision", self._run_vision(pdf_path)))
            except Exception:
                pass
        if pdfplumber is not None:
            try:
                texts.append(("pdfplumber", self._run_pdfplumber(pdf_path)))
            except Exception:
                pass
        if not texts:
            return ("none", "", 0.0)
        scored = [(backend, text, _score_text_quality(text)) for backend, text in texts]
        backend, text, score = max(scored, key=lambda t: t[2])
        return (backend, text, score)

    def _run_textract(self, pdf_path: Path) -> str:
        client = boto3.client("textract")  # type: ignore
        with open(pdf_path, "rb") as f:
            data = f.read()
        resp = client.analyze_document(Document={"Bytes": data}, FeatureTypes=["TABLES", "FORMS"])  # type: ignore
        blocks = resp.get("Blocks", [])
        lines = [b.get("Text", "") for b in blocks if b.get("BlockType") in ("LINE", "WORD") and b.get("Text")]
        return "\n".join(lines)

    def _run_vision(self, pdf_path: Path) -> str:
        client = vision.ImageAnnotatorClient()  # type: ignore
        with open(pdf_path, "rb") as f:
            content = f.read()
        image = vision.Image(content=content)  # type: ignore
        response = client.document_text_detection(image=image)
        if response.error.message:
            raise RuntimeError(response.error.message)
        return response.full_text_annotation.text or ""

    def _run_pdfplumber(self, pdf_path: Path) -> str:
        text = ""
        with pdfplumber.open(pdf_path) as pdf:  # type: ignore
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
        return text


