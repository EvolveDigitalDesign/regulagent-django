"""
Operator Packet Upload endpoint.

Accepts a .docx P&A execution packet from a tenant, runs the import pipeline
(security scan → extraction → persistence), then queues the kernel comparison
Celery task.

POST /api/documents/operator-packet/
    - file:               .docx file (required)
    - api_number:         Well API number (required)
    - plan_snapshot_id:   UUID of existing PlanSnapshot to compare against (optional)
    - skip_security_scan: Skip prompt-injection check (DEBUG mode only)
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from django.conf import settings
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.public_core.services.operator_packet_importer import import_operator_packet

logger = logging.getLogger(__name__)


class OperatorPacketUploadView(APIView):
    """
    Upload a tenant-supplied P&A execution packet (.docx).

    Request:
        - file:               .docx file
        - api_number:         Well API number
        - plan_snapshot_id:   (optional) PlanSnapshot UUID to compare against
        - skip_security_scan: (optional, DEBUG only) "true" to bypass prompt-injection check

    Response (success, 201):
        {
            "success": true,
            "extracted_document_id": "uuid",
            "plan_snapshot_id": null,
            "api_number": "42-329-34838",
            "document_type": "pa_procedure",
            "images_analyzed": 9,
            "kernel_comparison_queued": true,
            "extracted_data": {},
            "message": "Operator P&A packet imported as approved plan."
        }

    Response (validation / extraction failure, 400/500):
        {
            "error": "...",
            "reasons": [...],
            "warnings": [...]
        }
    """

    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        # -------- Extract parameters --------
        uploaded_file = request.FILES.get("file")
        api_number = request.data.get("api_number", "").strip()
        plan_snapshot_id = request.data.get("plan_snapshot_id", "").strip() or None
        # skip_security_scan only honoured in DEBUG mode
        skip_security_scan = (
            getattr(settings, "DEBUG", False)
            and request.data.get("skip_security_scan", "false").lower() == "true"
        )

        # -------- Resolve tenant + workspace --------
        tenant_id = None
        workspace = None
        if request.user.is_authenticated:
            user_tenant = request.user.tenants.first()
            tenant_id = user_tenant.id if user_tenant else None
            logger.info(
                "operator_packet_upload: user=%s tenant_id=%s",
                request.user.email,
                tenant_id,
            )
            # Resolve workspace from request data (optional)
            workspace_id = request.data.get("workspace_id", "").strip() or None
            if workspace_id and user_tenant:
                try:
                    workspace = user_tenant.workspaces.filter(id=workspace_id).first()
                except Exception:
                    pass

        # -------- Input validation --------
        if not uploaded_file:
            return Response(
                {"error": "No file provided", "detail": "Request must include a 'file' parameter"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not api_number:
            return Response(
                {"error": "api_number is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        fname_lower = uploaded_file.name.lower()
        if not fname_lower.endswith(".docx"):
            return Response(
                {
                    "error": "Invalid file type",
                    "detail": "Only .docx files are supported for operator packets",
                    "received": uploaded_file.name,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # -------- Save upload to temp file (importer expects a file path) --------
        tmp_path = None
        try:
            suffix = Path(uploaded_file.name).suffix or ".docx"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                for chunk in uploaded_file.chunks():
                    tmp.write(chunk)
                tmp_path = tmp.name
            logger.info(
                "operator_packet_upload: saved temp file %s for api=%s",
                tmp_path,
                api_number,
            )
        except Exception as e:
            logger.exception("operator_packet_upload: failed to save temp file")
            return Response(
                {"error": "Failed to save uploaded file", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # -------- Run import pipeline --------
        logger.info(
            "operator_packet_upload: starting import for api=%s file=%s",
            api_number,
            uploaded_file.name,
        )
        try:
            result = import_operator_packet(
                file_path=tmp_path,
                api_number=api_number,
                request=request,
                workspace=workspace,
                skip_security_scan=skip_security_scan,
            )
        except Exception as e:
            logger.exception("operator_packet_upload: import pipeline error")
            return Response(
                {"error": "Import pipeline error", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        finally:
            # Clean up temp file
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

        if not result.get("success"):
            logger.warning(
                "operator_packet_upload: import failed for api=%s errors=%s",
                api_number,
                result.get("errors"),
            )
            return Response(
                {
                    "error": result.get("error", "Import failed"),
                    "reasons": result.get("reasons") or result.get("errors") or [],
                    "warnings": result.get("warnings", []),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success": True,
                "extracted_document_id": result.get("extracted_document_id"),
                "plan_snapshot_id": result.get("plan_snapshot_id"),
                "api_number": result.get("api_number", api_number),
                "document_type": result.get("document_type", "pa_procedure"),
                "images_analyzed": result.get("images_analyzed", 0),
                "kernel_comparison_queued": result.get("kernel_comparison_queued", False),
                "extracted_data": result.get("extracted_data"),
                "warnings": result.get("warnings", []),
                "message": result.get("message", "Operator P&A packet imported as approved plan."),
            },
            status=status.HTTP_201_CREATED,
        )
