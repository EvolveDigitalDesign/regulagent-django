from __future__ import annotations

import re
from typing import Any, Dict, Optional

from django.http import Http404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from apps.public_core.models import PlanSnapshot, WellRegistry
from apps.public_core.services.api_normalization import get_well_by_api, normalize_api_14digit


class FilingExportView(APIView):
    """Export a plan's RRC filing data in JSON or PDF format."""

    def get(self, request, api: str):
        fmt = (request.query_params.get("format") or "json").lower()
        if fmt not in ("json", "pdf"):
            return Response({"detail": "Unsupported format. Use ?format=json|pdf"}, status=status.HTTP_400_BAD_REQUEST)

        # Normalize API for consistent lookup
        api_14 = normalize_api_14digit(api)
        if not api_14:
            return Response({"detail": "Invalid API number format"}, status=status.HTTP_400_BAD_REQUEST)

        well: Optional[WellRegistry] = None
        snapshot: Optional[PlanSnapshot] = None

        # Try to find well by API
        try:
            well = get_well_by_api(api)
            snapshot = (
                PlanSnapshot.objects
                .filter(well=well)
                .order_by('-created_at')
                .first()
            )
        except Http404:
            # Well not found, try fallback
            pass

        if not snapshot:
            # Fallback by plan_id when WellRegistry is missing
            expected_ids = [f"{api_14}:combined", f"{api_14}:isolated", f"{api_14}:both"]
            snapshot = (
                PlanSnapshot.objects
                .filter(plan_id__in=expected_ids)
                .order_by('-created_at')
                .first()
            )
        if not snapshot:
            return Response({"detail": "No plan snapshot found for API"}, status=status.HTTP_404_NOT_FOUND)

        payload: Dict[str, Any] = snapshot.payload or {}
        plan_payload: Optional[Dict[str, Any]] = None
        if isinstance(payload.get("variants"), dict):
            variants = payload["variants"]
            plan_payload = variants.get("combined") or variants.get("isolated") or None
        else:
            plan_payload = payload

        if not isinstance(plan_payload, dict):
            return Response({"detail": "Snapshot payload is malformed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        rrc_export = plan_payload.get("rrc_export") or []
        if fmt == "json":
            out = {
                "api": api_14,
                "plan_id": snapshot.plan_id,
                "filing": rrc_export,
                "kernel_version": plan_payload.get("kernel_version"),
                "jurisdiction": plan_payload.get("jurisdiction"),
                "district": plan_payload.get("district"),
                "county": plan_payload.get("county"),
                "field": plan_payload.get("field"),
            }
            return Response(out, status=status.HTTP_200_OK)

        # PDF format placeholder (MVP defers real PDF rendering)
        return Response({"detail": "PDF export not yet implemented"}, status=status.HTTP_501_NOT_IMPLEMENTED)


