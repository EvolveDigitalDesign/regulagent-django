from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory

from apps.public_core.models import WellRegistry, PlanSnapshot
from apps.public_core.views.w3a_from_api import W3AFromApiView


class Command(BaseCommand):
    help = "Smoke-validate the W3A planning endpoint and snapshot persistence"

    def add_arguments(self, parser) -> None:  # type: ignore[override]
        parser.add_argument("--api10", required=True, help="10-digit API (digits or formatted)")
        parser.add_argument("--plugs-mode", default="combined", choices=["combined", "isolated", "both"], help="Plugs mode")
        parser.add_argument("--merge-threshold-ft", default=500, type=float, help="Merge threshold in feet (for combined mode)")

    def handle(self, *args: Any, **options: Any) -> None:  # type: ignore[override]
        api10: str = options["api10"]
        plugs_mode: str = options["plugs_mode"]
        merge_threshold_ft: float = float(options["merge_threshold_ft"])

        # Count snapshots before run for delta check
        api_digits = "".join(ch for ch in str(api10) if ch.isdigit())
        well: Optional[WellRegistry] = WellRegistry.objects.filter(api14__icontains=api_digits[-8:]).first()
        before_count = PlanSnapshot.objects.filter(well=well).count() if well else 0

        # Invoke the view via RequestFactory to avoid external clients
        rf = RequestFactory()
        body = {"api10": api10, "plugs_mode": plugs_mode, "merge_threshold_ft": merge_threshold_ft}
        req = rf.post("/api/plans/w3a/from-api", data=json.dumps(body), content_type="application/json")
        resp = W3AFromApiView.as_view()(req)
        status_code = getattr(resp, "status_code", 500)
        if status_code != 200:
            raise CommandError(f"Endpoint returned status {status_code}")

        data: Dict[str, Any] = getattr(resp, "data", {}) or {}
        # Select plan variant for checks
        if plugs_mode == "both":
            plan = (data.get("variants") or {}).get("combined") or (data.get("variants") or {}).get("isolated") or {}
        else:
            plan = data

        # Checks
        failures: List[str] = []
        steps: List[Dict[str, Any]] = plan.get("steps") or []
        if not isinstance(steps, list) or len(steps) == 0:
            failures.append("no_steps")

        mt = plan.get("materials_totals") or {}
        if not isinstance(mt.get("total_sacks"), (int, float)):
            failures.append("materials_totals.total_sacks_missing")
        if not isinstance(mt.get("total_bbl"), (int, float)):
            failures.append("materials_totals.total_bbl_missing")

        export = plan.get("rrc_export") or []
        if not isinstance(export, list) or len(export) == 0:
            failures.append("rrc_export_missing")

        # If a bridge_plug exists, ensure a cap exists as well
        types = [s.get("type") for s in steps]
        if "bridge_plug" in types and not any(t in ("bridge_plug_cap", "cibp_cap") for t in types):
            failures.append("bridge_plug_without_cap")

        # Snapshot delta
        well_after: Optional[WellRegistry] = WellRegistry.objects.filter(api14__icontains=api_digits[-8:]).first()
        after_count = PlanSnapshot.objects.filter(well=well_after).count() if well_after else 0
        if after_count <= before_count:
            failures.append("baseline_snapshot_not_persisted")

        if failures:
            self.stdout.write(self.style.ERROR(f"FAIL: {', '.join(failures)}"))
            raise CommandError("smoke_validate failed")

        self.stdout.write(self.style.SUCCESS("PASS: w3a-from-api + snapshot persistence"))


