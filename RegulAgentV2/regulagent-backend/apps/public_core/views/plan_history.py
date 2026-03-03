from __future__ import annotations

from typing import Any, Dict, List

from django.http import Http404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ..models import PlanSnapshot, WellRegistry
from ..services.api_normalization import get_well_by_api


class PlanHistoryView(APIView):
    """Get the history of plan snapshots for a well."""

    def get(self, request, api: str) -> Response:
        try:
            w = get_well_by_api(api)
        except Http404 as e:
            return Response({"detail": str(e)}, status=status.HTTP_404_NOT_FOUND)

        rows = PlanSnapshot.objects.filter(well=w).order_by('created_at')
        out: List[Dict[str, Any]] = []
        for s in rows:
            out.append({
                "created_at": s.created_at.isoformat(),
                "plan_id": s.plan_id,
                "kind": s.kind,
                "has_extraction": bool((s.payload or {}).get("extraction")),
            })
        return Response({"api": w.api14, "count": len(out), "history": out}, status=status.HTTP_200_OK)


