"""
Serve source PDF files for ExtractedDocuments.
"""
import logging
from pathlib import Path

from django.http import FileResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.public_core.models import ExtractedDocument

logger = logging.getLogger(__name__)


class DocumentPDFView(APIView):
    """
    GET /api/documents/<id>/pdf/

    Serve the source PDF file for an ExtractedDocument.
    For Neubus documents: looks up NeubusDocument.local_path via neubus_filename.
    For NM OCD documents: returns redirect to OCD URL if source_path is a URL.
    For uploads: serves from source_path directly.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, doc_id):
        try:
            ed = ExtractedDocument.objects.get(id=doc_id)
        except ExtractedDocument.DoesNotExist:
            return Response({"detail": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

        pdf_path = None

        # Strategy 1: Neubus document — look up via neubus_filename
        if ed.neubus_filename:
            from apps.public_core.models.neubus_lease import NeubusDocument
            neubus_doc = NeubusDocument.objects.filter(
                neubus_filename=ed.neubus_filename
            ).first()
            if neubus_doc and neubus_doc.local_path:
                pdf_path = Path(neubus_doc.local_path)

        # Strategy 2: source_path is a URL (NM OCD docs)
        if not pdf_path and ed.source_path:
            if ed.source_path.startswith("http://") or ed.source_path.startswith("https://"):
                from django.http import HttpResponseRedirect
                return HttpResponseRedirect(ed.source_path)
            # source_path is a local path
            pdf_path = Path(ed.source_path)

        if not pdf_path or not pdf_path.exists():
            logger.warning(
                f"[DocumentPDF] PDF not found for ED {doc_id}: "
                f"neubus_filename={ed.neubus_filename}, source_path={ed.source_path}, "
                f"resolved_path={pdf_path}"
            )
            return Response(
                {"detail": "Source PDF file not found on server"},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(f"[DocumentPDF] Serving {pdf_path} for ED {doc_id}")

        # Determine filename for Content-Disposition
        filename = pdf_path.name
        if ed.neubus_filename:
            filename = ed.neubus_filename

        response = FileResponse(
            open(pdf_path, "rb"),
            content_type="application/pdf",
            filename=filename,
        )
        # Allow inline display (not forced download)
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response
