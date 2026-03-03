from django.http import Http404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from apps.public_core.models import PlanSnapshot, WellRegistry
from apps.public_core.services.api_normalization import get_well_by_api, normalize_api_14digit
from apps.tenant_overlay.models import TenantArtifact
import re


class PlanArtifactsView(APIView):
    """List artifacts associated with a plan snapshot."""

    def get(self, request, api: str):
        # Normalize API number for consistent lookup
        api_14 = normalize_api_14digit(api)
        if not api_14:
            return Response({"detail": "Invalid API number format"}, status=status.HTTP_400_BAD_REQUEST)

        # Prefer finding via WellRegistry, but fall back to plan_id and extracted_document
        snapshot = None
        try:
            well = get_well_by_api(api)
            snapshot = PlanSnapshot.objects.filter(well=well).order_by('-created_at').first()
        except Http404:
            # Well not found, try fallback lookups
            pass

        if not snapshot:
            expected_ids = [f"{api_14}:combined", f"{api_14}:isolated", f"{api_14}:both"]
            snapshot = (
                PlanSnapshot.objects
                .filter(plan_id__in=expected_ids)
                .order_by('-created_at')
                .first()
            )

        arts_qs = None
        if snapshot:
            arts_qs = TenantArtifact.objects.filter(plan_snapshot=snapshot).order_by('-created_at')
        else:
            # Last resort: artifacts created from extracted docs for this API even without snapshots/well
            arts_qs = (
                TenantArtifact.objects
                .filter(extracted_document__api_number=api_14)
                .order_by('-created_at')
            )

        arts = arts_qs
        data = [
            {
                "id": str(a.id),
                "artifact_type": a.artifact_type,
                "file_path": a.file_path,
                "content_type": a.content_type,
                "size_bytes": a.size_bytes,
                "sha256": a.sha256,
                "created_at": a.created_at,
            }
            for a in arts
        ]

        return Response({
            "api": api,
            "plan_id": (snapshot.plan_id if snapshot else None),
            "count": len(data),
            "artifacts": data,
        }, status=status.HTTP_200_OK)


