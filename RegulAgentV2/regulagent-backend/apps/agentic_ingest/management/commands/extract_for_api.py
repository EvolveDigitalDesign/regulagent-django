from __future__ import annotations

import asyncio
import json
from django.core.management.base import BaseCommand, CommandParser

from apps.agentic_ingest.services.orchestrator import extract_for_api


class Command(BaseCommand):
    help = "Extract RRC documents for a 10-digit API; OCR; normalize; emit JSON results."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("api10", type=str, help="10-digit API number (e.g., 4212345678)")

    def handle(self, *args, **options):
        api10 = options["api10"]

        async def run() -> None:
            outcome = await extract_for_api(api10)
            self.stdout.write(json.dumps({
                "api10": outcome.api10,
                "state": outcome.state_code,
                "workspace_dir": outcome.workspace_dir,
                "artifacts": [a.__dict__ for a in outcome.artifacts],
                "normalized": outcome.normalized,
                "findings": outcome.findings,
            }, indent=2))

        asyncio.run(run())


