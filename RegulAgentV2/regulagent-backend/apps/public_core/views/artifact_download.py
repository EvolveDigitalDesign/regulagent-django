import os

from django.conf import settings
from django.http import FileResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from apps.tenant_overlay.models import TenantArtifact


class ArtifactDownloadView(APIView):
    """Download a tenant artifact file by ID."""

    def get(self, request, artifact_id: str):
        try:
            art = TenantArtifact.objects.filter(id=artifact_id).first()
            if not art:
                return Response({"detail": "Artifact not found"}, status=status.HTTP_404_NOT_FOUND)

            file_path = art.file_path or ""
            if not file_path:
                return Response({"detail": "No file path for artifact"}, status=status.HTTP_400_BAD_REQUEST)

            media_root = os.path.realpath(getattr(settings, "MEDIA_ROOT", "."))
            fp_real = os.path.realpath(file_path)
            # Only allow files served from MEDIA_ROOT
            if not fp_real.startswith(media_root + os.sep):
                return Response({"detail": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

            if not os.path.exists(fp_real):
                return Response({"detail": "File not found on disk"}, status=status.HTTP_404_NOT_FOUND)

            return FileResponse(open(fp_real, "rb"), as_attachment=True, filename=os.path.basename(fp_real))
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


