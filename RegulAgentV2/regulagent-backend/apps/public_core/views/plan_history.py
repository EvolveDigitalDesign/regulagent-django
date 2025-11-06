from __future__ import annotations

from typing import Any, Dict, List

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ..models import PlanSnapshot, WellRegistry


class PlanHistoryView(APIView):
    authentication_classes = []  # TODO: wire auth
    permission_classes = []

    def get(self, request, api: str) -> Response:
        api_digits = ''.join(ch for ch in str(api) if ch.isdigit())
        if not api_digits:
            return Response({"detail": "invalid api"}, status=status.HTTP_400_BAD_REQUEST)
        w = WellRegistry.objects.filter(api14__icontains=api_digits[-8:]).first()
        if not w:
            return Response({"detail": "well not found"}, status=status.HTTP_404_NOT_FOUND)
        rows = PlanSnapshot.objects.filter(well=w).order_by('created_at')
        out: List[Dict[str, Any]] = []
        for s in rows:
            out.append({
                "created_at": s.created_at.isoformat(),
                "plan_id": s.plan_id,
                "kind": s.kind,
                "has_extraction": bool((s.payload or {}).get("extraction")),
            })
        return Response({"api": api_digits, "count": len(out), "history": out}, status=status.HTTP_200_OK)


