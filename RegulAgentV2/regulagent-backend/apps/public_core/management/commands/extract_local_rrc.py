from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from django.core.management.base import BaseCommand, CommandParser
from django.core.management import call_command
from django.db import transaction

from apps.public_core.models import ExtractedDocument, WellRegistry
from apps.public_core.services.openai_extraction import classify_document, extract_json_from_pdf


class Command(BaseCommand):
    help = "Extract JSON from local RRC PDFs using OpenAI and persist to ExtractedDocument; then run plan_from_extractions."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dir", dest="directory", default="ra_config/mediafiles/rrc", help="Directory with PDFs")
        parser.add_argument("--api", dest="api", default=None, help="Override API14 to associate with docs")
        parser.add_argument("--dry", dest="dry", action="store_true", help="Dry run - don't persist")

    def handle(self, *args: Any, **options: Any) -> None:
        root = Path(options.get("directory") or "ra_config/mediafiles/rrc")
        override_api = options.get("api")
        dry = bool(options.get("dry"))
        if not root.exists():
            self.stderr.write(f"Directory not found: {root}")
            return

        created: list[Dict[str, Any]] = []

        def _guess_api_from_name(p: Path) -> Optional[str]:
            m = re.search(r"(\d{14}|\d{10}|\d{8})", p.name)
            if not m:
                return None
            val = m.group(1)
            if len(val) == 8:
                return f"42000000{val}"
            if len(val) == 10:
                return f"4200{val}"
            return val

        def _api_from_json(doc_type: str, data: Dict[str, Any]) -> Optional[str]:
            try:
                if doc_type == "w2":
                    wi = data.get("well_info") or {}
                    api = (wi.get("api") or "").replace("-", "").strip()
                    return api or None
                if doc_type == "gau":
                    wi = (data.get("well_info") or {})
                    api = (wi.get("api") or "").replace("-", "").strip()
                    return api or None
                if doc_type == "w15":
                    wi = data.get("well_info") or {}
                    api = (wi.get("api") or "").replace("-", "").strip()
                    return api or None
            except Exception:
                return None
            return None

        pdfs = sorted(root.glob("*.pdf"))
        if not pdfs:
            self.stdout.write(json.dumps({"detail": "no_pdfs", "dir": str(root)}))
            return

        last_api: Optional[str] = None
        for p in pdfs:
            doc_type = classify_document(p)
            if doc_type not in ("gau", "w2", "w15", "schematic", "formation_tops"):
                continue
            ext = extract_json_from_pdf(p, doc_type)
            api14 = override_api or _api_from_json(doc_type, ext.json_data) or _guess_api_from_name(p) or "00000000000000"
            last_api = api14
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
                created.append({"api": api14, "type": doc_type, "path": str(p), "status": payload["status"], "errors": ext.errors})
            else:
                with transaction.atomic():
                    ed = ExtractedDocument.objects.create(**payload)
                    created.append({"id": str(ed.id), "api": api14, "type": doc_type, "path": str(p), "status": payload["status"]})

        self.stdout.write(json.dumps({"created": created}, ensure_ascii=False))

        # Trigger planning with latest API extracted
        if not dry and last_api:
            call_command("plan_from_extractions", api=last_api)

