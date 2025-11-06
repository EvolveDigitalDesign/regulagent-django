from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.policy.services.policy_applicator import PolicyApplicator


class Command(BaseCommand):
    help = "Apply W-3A policy to extracted data and print plan JSON. Usage: manage.py policy_apply --api 4200..."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--api", dest="api", required=True, help="API number (8/10/14-digit)")

    def handle(self, *args: Any, **options: Any) -> None:
        api: str = str(options.get("api") or "").strip()
        if not api:
            self.stdout.write("{}")
            return
        applicator = PolicyApplicator()
        plan = applicator.from_extractions(api)
        self.stdout.write(json.dumps(plan))

