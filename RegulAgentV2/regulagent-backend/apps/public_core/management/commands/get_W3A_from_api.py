from __future__ import annotations

import json
import re
from pathlib import Path
import io
from typing import Any, Dict, Optional

from django.core.management.base import BaseCommand, CommandParser
from django.core.management import call_command
from django.db import transaction
from django.conf import settings

from apps.public_core.models import ExtractedDocument, WellRegistry
from apps.public_core.services.rrc_completions_extractor import extract_completions_all_documents
from apps.public_core.services.openai_extraction import classify_document, extract_json_from_pdf


class Command(BaseCommand):
    help = (
        "End-to-end: download RRC PDFs for an API, extract JSON to DB, then run plan_from_extractions."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--api", dest="api", required=True, help="API number (8/10/14-digit)")
        parser.add_argument("--dry", dest="dry", action="store_true", help="Dry run - don't persist or plan")
        parser.add_argument("--gau_file", dest="gau_file", default=None, help="Path to a user-provided GAU PDF to use if no valid GAU is found")

    def handle(self, *args: Any, **options: Any) -> None:
        api_in = options.get("api") or ""
        dry = bool(options.get("dry"))

        def _normalize_api(val: str) -> str:
            s = re.sub(r"\D+", "", str(val or ""))
            if len(s) in (14, 10, 8):
                return s
            # best effort: if last 8 digits provided, prepend Texas state prefix
            if len(s) > 8:
                return s[-14:] if len(s) >= 14 else s[-10:] if len(s) >= 10 else s[-8:]
            return s

        api = _normalize_api(api_in)
        if len(api) not in (8, 10, 14):
            self.stderr.write(json.dumps({"error": "invalid_api", "api": api_in}, ensure_ascii=False))
            return

        # 1) Download most recent set of RRC PDFs for this API (cached by extractor)
        # Only pull W-2, W-15, and GAU-family docs for now (keep extractor flexible)
        dl = extract_completions_all_documents(api, allowed_kinds=["w2", "w15", "gau"])
        status = dl.get("status")
        files = dl.get("files") or []
        out_dir = dl.get("output_dir")
        if not files:
            self.stdout.write(json.dumps({"detail": "no_documents", "status": status, "api": api, "dir": out_dir}, ensure_ascii=False))
            return

        created: list[Dict[str, Any]] = []

        def _api_from_json(doc_type: str, data: Dict[str, Any]) -> Optional[str]:
            try:
                wi = data.get("well_info") or {}
                val = (wi.get("api") or "").replace("-", "").strip()
                return val or None
            except Exception:
                return None

        # 2) Classify and extract JSON for each PDF; persist to ExtractedDocument
        for rec in files:
            try:
                p = Path(rec.get("path") or "")
                if not p.exists():
                    continue
                doc_type = classify_document(p)
                if doc_type not in ("gau", "w2", "w15", "schematic", "formation_tops"):
                    continue
                ext = extract_json_from_pdf(p, doc_type)
                api14 = _api_from_json(doc_type, ext.json_data) or api
                # Normalize/expand 8 or 10-digit to 14 when possible (TX prefix 42 + county)
                # Here we keep as-is; downstream uses last 8 for matching
                well = WellRegistry.objects.filter(api14__icontains=api14[-8:]).first()
                payload = {
                    "well": well,
                    "api_number": api14,
                    "document_type": doc_type,
                    "source_path": str(p),
                    "model_tag": ext.model_tag,
                    "status": "success" if not ext.errors else "error",
                    "errors": ext.errors,
                    "json_data": ext.json_data,
                }
                if dry:
                    created.append({
                        "api": api14,
                        "type": doc_type,
                        "path": str(p),
                        "status": payload["status"],
                        "errors": ext.errors,
                    })
                else:
                    with transaction.atomic():
                        ed = ExtractedDocument.objects.create(**payload)
                        created.append({
                            "id": str(ed.id),
                            "api": api14,
                            "type": doc_type,
                            "path": str(p),
                            "status": payload["status"],
                        })
            except Exception as e:
                created.append({"path": rec.get("path"), "status": "error", "error": str(e)})

        # 2b) Optional: user-provided GAU override file (used when GAU is missing/invalid)
        gau_override_path = options.get("gau_file")
        if gau_override_path and Path(gau_override_path).exists():
            try:
                p = Path(gau_override_path)
                # Force doc_type gau for user-provided file
                ext = extract_json_from_pdf(p, "gau")
                # Associate with target API regardless of embedded API in the letter
                payload = {
                    "well": WellRegistry.objects.filter(api14__icontains=api[-8:]).first(),
                    "api_number": api,
                    "document_type": "gau",
                    "source_path": str(p),
                    "model_tag": ext.model_tag,
                    "status": "success" if not ext.errors else "error",
                    "errors": ext.errors,
                    "json_data": ext.json_data,
                }
                if dry:
                    created.append({"api": api, "type": "gau(user)", "path": str(p), "status": payload["status"], "errors": ext.errors})
                else:
                    with transaction.atomic():
                        ed = ExtractedDocument.objects.create(**payload)
                        created.append({"id": str(ed.id), "api": api, "type": "gau(user)", "path": str(p), "status": payload["status"]})
            except Exception as e:
                created.append({"path": gau_override_path, "status": "error", "error": str(e)})

        self.stdout.write(json.dumps({
            "api": api,
            "output_dir": out_dir,
            "created": created,
            "source": dl.get("source"),
        }, ensure_ascii=False))

        # 3) Trigger planning with provided API; capture JSON and echo to stdout; also save to tmp
        if not dry:
            buf = io.StringIO()
            call_command("plan_from_extractions", api=api, stdout=buf)
            plan_json = buf.getvalue() or "{}"
            # Print plan JSON for callers that expect stdout summary
            self.stdout.write(plan_json)
            # Persist under tmp/extractions for inspection
            try:
                tmp_dir = Path(settings.BASE_DIR) / 'tmp' / 'extractions'
                tmp_dir.mkdir(parents=True, exist_ok=True)
                out_path = tmp_dir / f"W3A_{api}_plan.json"
                with open(out_path, 'w', encoding='utf-8') as f_out:
                    f_out.write(plan_json)
            except Exception:
                # non-fatal if tmp save fails
                pass


