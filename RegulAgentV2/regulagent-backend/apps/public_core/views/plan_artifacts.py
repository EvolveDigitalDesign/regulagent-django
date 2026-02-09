from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from apps.public_core.models import PlanSnapshot, WellRegistry
from apps.tenant_overlay.models import TenantArtifact
import re


class PlanArtifactsView(APIView):
    """List artifacts associated with a plan snapshot."""

    def get(self, request, api: str):
        api_normalized = re.sub(r"\D+", "", str(api or ""))
        if not api_normalized:
            return Response({"detail": "API number is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Prefer finding via WellRegistry, but fall back to plan_id and extracted_document
        snapshot = None
        well = WellRegistry.objects.filter(api14__icontains=api_normalized[-8:]).first()
        if well:
            snapshot = PlanSnapshot.objects.filter(well=well).order_by('-created_at').first()
        if not snapshot:
            expected_ids = [f"{api_normalized}:combined", f"{api_normalized}:isolated", f"{api_normalized}:both"]
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
                .filter(extracted_document__api_number__icontains=api_normalized[-8:])
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


