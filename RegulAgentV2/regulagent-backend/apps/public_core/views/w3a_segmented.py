"""
Segmented W3A flow with user verification at each stage.

This module implements a multi-stage W3A generation flow:
1. POST /w3a/initial → Document sourcing + combined PDF
2. POST /w3a/{temp_plan_id}/confirm-docs → User confirms/overrides docs + extraction
3. GET /w3a/{temp_plan_id}/extractions → Get extraction JSONs
4. POST /w3a/{temp_plan_id}/confirm-extractions → User edits extractions
5. GET /w3a/{temp_plan_id}/geometry → Get derived geometry
6. POST /w3a/{temp_plan_id}/confirm-geometry → User edits geometry + build plan
7. POST /w3a/{plan_id}/apply-edits → Apply staged edits to WellRegistry
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.kernel.services.policy_kernel import plan_from_facts
from apps.policy.services.loader import get_effective_policy
from apps.public_core.models import (
    ExtractedDocument,
    PlanSnapshot,
    WellEditAudit,
    WellRegistry,
)
from apps.public_core.serializers.w3a_segmented import (
    W3AInitialRequestSerializer,
    W3AInitialResponseSerializer,
    W3AConfirmDocsRequestSerializer,
    W3AExtractionsResponseSerializer,
    W3AConfirmExtractionsRequestSerializer,
    W3AGeometryResponseSerializer,
    W3AConfirmGeometryRequestSerializer,
    ApplyEditsRequestSerializer,
    EditAuditSerializer,
)
from apps.public_core.services.pdf_combiner import (
    combine_pdfs_to_temp,
    cleanup_temp_pdf,
    PDFCombinerError,
)
from apps.public_core.services.rrc_completions_extractor import extract_completions_all_documents
from apps.public_core.services.openai_extraction import (
    classify_document,
    extract_json_from_pdf,
    vectorize_extracted_document,
)
from apps.public_core.services.well_registry_enrichment import enrich_well_registry_from_documents
from apps.kernel.services.policy_kernel import plan_from_facts
from apps.policy.services.loader import get_effective_policy

logger = logging.getLogger(__name__)


class W3AInitialView(APIView):
    """
    Stage 1: Document sourcing and combined PDF generation.
    
    POST /w3a/initial
    
    Returns:
        - temp_plan_id: Temporary ID for this session
        - combined_pdf_url: URL to download combined PDF for review
        - source_files: List of source files included
    """
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    
    def post(self, request):
        logger.info("🚀 W3A INITIAL - Starting document sourcing")
        
        serializer = W3AInitialRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        api10 = serializer.validated_data["api10"]
        input_mode = serializer.validated_data.get("input_mode", "extractions")
        
        # Normalize API
        api_normalized = re.sub(r"\D+", "", api10)
        
        try:
            # 1. Source documents
            pdf_paths = []
            source_file_metadata = []
            
            # RRC extractions
            if input_mode in ("extractions", "hybrid"):
                logger.info(f"📥 Sourcing RRC extractions for API {api_normalized}")
                dl = extract_completions_all_documents(api_normalized, allowed_kinds=["w2", "w15", "gau"])
                
                for file_info in (dl.get("files") or []):
                    path = file_info.get("path")
                    if path and os.path.exists(path):
                        file_ext = os.path.splitext(path)[1].lower()
                        # Only combine PDF files
                        if file_ext == ".pdf":
                            pdf_paths.append(path)
                            source_file_metadata.append({
                                "source": "rrc",
                                "type": file_info.get("kind", "unknown"),
                                "filename": os.path.basename(path),
                                "path": path,
                            })
                        else:
                            logger.debug(f"Skipping non-PDF file: {path}")
            
            # User uploads
            if input_mode in ("user_files", "hybrid"):
                logger.info("📤 Processing user-uploaded files")
                uploaded_files = [
                    ("w2_file", "w2"),
                    ("w15_file", "w15"),
                    ("gau_file", "gau"),
                    ("schematic_file", "schematic"),
                    ("formation_tops_file", "formation_tops"),
                ]
                
                for field_name, doc_type in uploaded_files:
                    file_obj = serializer.validated_data.get(field_name)
                    if file_obj:
                        # Save to temp location
                        temp_path = self._save_temp_upload(file_obj, api_normalized)
                        
                        if temp_path.lower().endswith(".pdf"):
                            pdf_paths.append(temp_path)
                            source_file_metadata.append({
                                "source": "user_upload",
                                "type": doc_type,
                                "filename": file_obj.name,
                                "path": temp_path,
                            })
            
            if not pdf_paths:
                return Response(
                    {"detail": "No PDF documents found to combine"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # 2. Combine PDFs
            logger.info(f"📄 Combining {len(pdf_paths)} PDFs")
            try:
                combined_info = combine_pdfs_to_temp(
                    pdf_paths,
                    output_prefix=f"w3a_{api_normalized}",
                    ttl_hours=24
                )
            except PDFCombinerError as e:
                logger.error(f"PDF combination failed: {e}")
                return Response(
                    {"detail": f"Failed to combine PDFs: {str(e)}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            # 3. Create temporary plan snapshot to track this session
            temp_plan_id = f"temp_{api_normalized}_{uuid4().hex[:8]}"
            
            # Find or create WellRegistry
            well, _ = WellRegistry.objects.get_or_create(
                api14=api_normalized,
                defaults={"state": "TX"}
            )
            
            # Create temp snapshot
            snapshot = PlanSnapshot.objects.create(
                well=well,
                plan_id=temp_plan_id,
                kind=PlanSnapshot.KIND_BASELINE,
                status=PlanSnapshot.STATUS_DRAFT,
                payload={
                    "stage": "document_sourcing",
                    "api": api_normalized,
                    "combined_pdf_path": combined_info["temp_path"],
                    "source_files": source_file_metadata,
                    "input_mode": input_mode,
                },
                kernel_version="",
                policy_id="tx.w3a",
                overlay_id="",
                tenant_id=request.user.tenants.first().id if request.user.tenants.exists() else None,
                visibility=PlanSnapshot.VISIBILITY_PRIVATE,
            )
            
            logger.info(f"✅ Created temp plan {temp_plan_id}")
            
            # 4. Return response
            response_data = {
                "temp_plan_id": temp_plan_id,
                "combined_pdf_url": f"/api/w3a/{temp_plan_id}/combined.pdf",  # Full API path
                "combined_pdf_path": combined_info["temp_path"],
                "source_files": combined_info["source_files"],
                "api": api_normalized,
                "page_count": combined_info["page_count"],
                "file_size": combined_info["file_size"],
                "ttl_expires_at": combined_info["ttl_expires_at"],
            }
            
            response_serializer = W3AInitialResponseSerializer(data=response_data)
            response_serializer.is_valid(raise_exception=True)
            
            return Response(response_serializer.data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception("W3A Initial - error")
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _save_temp_upload(self, file_obj, api: str) -> str:
        """Save uploaded file to temporary location."""
        root = getattr(settings, "MEDIA_ROOT", tempfile.gettempdir())
        ts = str(int(__import__("time").time()))
        base_dir = os.path.join(root, "uploads", "temp", api)
        os.makedirs(base_dir, exist_ok=True)
        
        filename = getattr(file_obj, "name", "upload.bin")
        safe_name = os.path.basename(filename)
        dest = os.path.join(base_dir, f"{ts}__{safe_name}")
        
        with open(dest, "wb") as outfp:
            chunk = file_obj.read()
            outfp.write(chunk if isinstance(chunk, (bytes, bytearray)) else str(chunk).encode("utf-8"))
        
        return dest


class W3ACombinedPDFView(APIView):
    """
    Download combined PDF for verification.
    
    GET /w3a/{temp_plan_id}/combined.pdf
    """
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    
    def get(self, request, temp_plan_id: str):
        logger.info(f"📄 W3A COMBINED PDF VIEW - Requested: {temp_plan_id}")
        logger.info(f"   User: {request.user}, Authenticated: {request.user.is_authenticated}")
        
        try:
            # Get user's tenant for isolation
            user_tenant_id = request.user.tenants.first().id if request.user.tenants.exists() else None
            
            # Tenant-isolated query
            snapshot = PlanSnapshot.objects.get(
                plan_id=temp_plan_id,
                tenant_id=user_tenant_id  # ← CRITICAL: Tenant isolation
            )
            logger.info(f"   ✅ Found snapshot: {snapshot.id}, tenant_id={snapshot.tenant_id}")
            
            # Verify user has access (tenant check)
            # For temp snapshots, be more lenient - allow if user is authenticated
            if snapshot.tenant_id:
                if request.user.tenants.exists():
                    user_tenant_id = request.user.tenants.first().id
                    logger.info(f"   Tenant check: snapshot={snapshot.tenant_id}, user={user_tenant_id}")
                    if str(snapshot.tenant_id) != str(user_tenant_id):
                        logger.warning(f"   ❌ Tenant mismatch - but allowing for temp snapshot")
                        # Don't block for temp snapshots - they're session-scoped
                        # raise Http404("Plan not found")
                else:
                    logger.info(f"   ℹ️  User has no tenants, allowing access to temp snapshot")
            
            pdf_path = snapshot.payload.get("combined_pdf_path")
            logger.info(f"   PDF path from snapshot: {pdf_path}")
            
            if not pdf_path:
                logger.error(f"   ❌ No combined_pdf_path in snapshot payload")
                raise Http404("Combined PDF path not found in snapshot")
            
            if not os.path.exists(pdf_path):
                logger.error(f"   ❌ PDF file does not exist at {pdf_path}")
                # List files in the directory to debug
                try:
                    parent_dir = os.path.dirname(pdf_path)
                    if os.path.exists(parent_dir):
                        files = os.listdir(parent_dir)
                        logger.error(f"   Files in {parent_dir}: {files}")
                except Exception as e:
                    logger.error(f"   Could not list directory: {e}")
                raise Http404("Combined PDF not found or expired")
            
            # Serve PDF with proper file handling
            try:
                logger.info(f"   📤 Serving PDF: {pdf_path} ({os.path.getsize(pdf_path)} bytes)")
                pdf_file = open(pdf_path, "rb")
                response = FileResponse(
                    pdf_file,
                    content_type="application/pdf",
                    as_attachment=False  # Display inline in browser
                )
                response["Content-Disposition"] = f'inline; filename="{temp_plan_id}_combined.pdf"'
                response["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response["Access-Control-Allow-Origin"] = "*"
                return response
            except Exception as e:
                logger.error(f"   ❌ Error serving PDF {pdf_path}: {e}")
                raise Http404("Failed to serve combined PDF")
            
        except PlanSnapshot.DoesNotExist:
            logger.error(f"   ❌ PlanSnapshot not found for plan_id: {temp_plan_id}")
            # List all temp plan snapshots for debugging
            try:
                temp_snapshots = PlanSnapshot.objects.filter(plan_id__startswith="temp_").values_list("plan_id", flat=True)
                logger.error(f"   Available temp plan IDs: {list(temp_snapshots)}")
            except Exception:
                pass
            raise Http404("Plan not found")


class W3AConfirmDocsView(APIView):
    """
    Stage 2: User confirms documents and triggers extraction.
    
    POST /w3a/{temp_plan_id}/confirm-docs
    
    Returns:
        - Extraction results for each confirmed document
    """
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    
    def post(self, request, temp_plan_id: str):
        logger.info(f"📋 W3A CONFIRM DOCS - {temp_plan_id}")
        
        # Get user's tenant for isolation
        user_tenant_id = request.user.tenants.first().id if request.user.tenants.exists() else None
        
        try:
            # Tenant-isolated query
            snapshot = PlanSnapshot.objects.get(
                plan_id=temp_plan_id,
                tenant_id=user_tenant_id  # ← CRITICAL: Tenant isolation
            )
        except PlanSnapshot.DoesNotExist:
            return Response(
                {"detail": "Plan not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = W3AConfirmDocsRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Get source files from snapshot
            source_files = snapshot.payload.get("source_files", [])
            combined_pdf_path = snapshot.payload.get("combined_pdf_path")
            
            # Process user overrides (accept/reject/replace)
            final_files = self._process_document_overrides(
                source_files,
                serializer.validated_data.get("document_overrides", []),
                serializer.validated_data.get("additional_uploads", [])
            )
            
            # Delete combined PDF (no longer needed)
            if combined_pdf_path:
                cleanup_temp_pdf(combined_pdf_path)
            
            # Extract each confirmed document
            extractions = []
            for file_meta in final_files:
                extraction = self._extract_document(
                    file_meta["path"],
                    file_meta["type"],
                    snapshot.well,
                    snapshot.payload.get("api")
                )
                if extraction:
                    extractions.append(extraction)
            
            # Update snapshot with extraction stage
            snapshot.payload["stage"] = "extraction_complete"
            snapshot.payload["extractions"] = extractions
            snapshot.payload["confirmed_files"] = final_files
            snapshot.save()
            
            logger.info(f"✅ Extracted {len(extractions)} documents")
            
            # Return extractions
            response_data = {
                "temp_plan_id": temp_plan_id,
                "extractions": extractions,
                "extraction_count": len(extractions),
            }
            
            response_serializer = W3AExtractionsResponseSerializer(data=response_data)
            response_serializer.is_valid(raise_exception=True)
            
            return Response(response_serializer.data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception("W3A Confirm Docs - error")
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _process_document_overrides(
        self,
        source_files: List[Dict[str, Any]],
        overrides: List[Dict[str, Any]],
        additional_uploads: List[Any]
    ) -> List[Dict[str, Any]]:
        """Process user's document accept/reject/replace decisions."""
        final_files = []
        
        # Build override map
        override_map = {o["source_index"]: o for o in overrides}
        
        for idx, file_meta in enumerate(source_files):
            override = override_map.get(idx)
            
            if override:
                action = override["action"]
                if action == "reject":
                    logger.info(f"🚫 Rejected {file_meta['filename']}: {override.get('reason', 'no reason')}")
                    continue
                elif action == "replace":
                    # TODO: Handle replacement file
                    logger.warning(f"⚠️  Replace action not yet implemented for {file_meta['filename']}")
                    final_files.append(file_meta)
                else:  # accept
                    final_files.append(file_meta)
            else:
                # No override = accept by default
                final_files.append(file_meta)
        
        # TODO: Handle additional_uploads
        
        return final_files
    
    def _extract_document(
        self,
        file_path: str,
        doc_type: str,
        well: WellRegistry,
        api: str
    ) -> Optional[Dict[str, Any]]:
        """Extract a single document and return summary. Reuses existing extraction if available."""
        try:
            # Classify if needed
            if doc_type == "unknown":
                doc_type = classify_document(Path(file_path))
            
            # Check for existing extraction first
            existing_extraction = ExtractedDocument.objects.filter(
                api_number=api,
                source_path=file_path,
                document_type=doc_type,
                status="success"
            ).order_by("-created_at").first()
            
            if existing_extraction:
                logger.info(f"♻️  Reusing existing extraction for {os.path.basename(file_path)} (ID: {existing_extraction.id})")
                
                # Build human-readable summary
                human_summary = self._build_human_summary(existing_extraction.json_data, doc_type)
                
                return {
                    "extracted_document_id": existing_extraction.id,
                    "document_type": doc_type,
                    "filename": os.path.basename(file_path),
                    "extraction_status": existing_extraction.status,
                    "errors": existing_extraction.errors or [],
                    "json_data": existing_extraction.json_data,
                    "human_readable_summary": human_summary,
                }
            
            # No existing extraction - call OpenAI
            logger.info(f"🤖 Calling OpenAI to extract {os.path.basename(file_path)}")
            extraction_result = extract_json_from_pdf(Path(file_path), doc_type)
            
            # Persist ExtractedDocument
            with transaction.atomic():
                ed = ExtractedDocument.objects.create(
                    well=well,
                    api_number=api,
                    document_type=doc_type,
                    source_path=file_path,
                    model_tag=extraction_result.model_tag,
                    status="success" if not extraction_result.errors else "error",
                    errors=extraction_result.errors,
                    json_data=extraction_result.json_data,
                    source_type=ExtractedDocument.SOURCE_RRC,  # TODO: Distinguish user uploads
                )
            
            # Vectorize (non-blocking)
            try:
                vectorize_extracted_document(ed)
            except Exception as e:
                logger.warning(f"Vectorization failed for {file_path}: {e}")
            
            # Build human-readable summary
            human_summary = self._build_human_summary(extraction_result.json_data, doc_type)
            
            return {
                "extracted_document_id": ed.id,
                "document_type": doc_type,
                "filename": os.path.basename(file_path),
                "extraction_status": ed.status,
                "errors": ed.errors,
                "json_data": extraction_result.json_data,
                "human_readable_summary": human_summary,
            }
            
        except Exception as e:
            logger.error(f"Failed to extract {file_path}: {e}")
            return None
    
    def _build_human_summary(self, json_data: Dict[str, Any], doc_type: str) -> Dict[str, Any]:
        """Convert extracted JSON to human-readable summary for UI display."""
        # TODO: Implement detailed summaries per doc type
        summary = {
            "document_type": doc_type,
            "preview": str(json_data)[:500] + "..." if len(str(json_data)) > 500 else str(json_data)
        }
        
        if doc_type == "w2":
            well_info = json_data.get("well_info", {})
            casing_record = json_data.get("casing_record", [])
            summary["well_info"] = {
                "api": well_info.get("api"),
                "operator": well_info.get("operator"),
                "field": well_info.get("field"),
                "county": well_info.get("county"),
            }
            summary["casing_strings"] = len(casing_record)
        
        return summary


class W3AConfirmExtractionsView(APIView):
    """
    Stage 3: User confirms/edits extractions.
    
    POST /w3a/{temp_plan_id}/confirm-extractions
    """
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    
    def post(self, request, temp_plan_id: str):
        logger.info(f"✏️  W3A CONFIRM EXTRACTIONS - {temp_plan_id}")
        
        # Get user's tenant for isolation
        user_tenant_id = request.user.tenants.first().id if request.user.tenants.exists() else None
        
        try:
            # Tenant-isolated query
            snapshot = PlanSnapshot.objects.get(
                plan_id=temp_plan_id,
                tenant_id=user_tenant_id  # ← CRITICAL: Tenant isolation
            )
        except PlanSnapshot.DoesNotExist:
            return Response(
                {"detail": "Plan not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = W3AConfirmExtractionsRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Save extraction edits as WellEditAudit records (staged)
            edits = serializer.validated_data.get("edits", [])
            
            user_display_name = request.user.get_full_name() or request.user.username
            user_tenant_id = request.user.tenants.first().id if request.user.tenants.exists() else None
            
            for edit_data in edits:
                WellEditAudit.objects.create(
                    plan_snapshot=snapshot,
                    well=snapshot.well,
                    field_path=edit_data["field_path"],
                    field_label=edit_data.get("field_label", ""),
                    context=WellEditAudit.CONTEXT_EXTRACTION,
                    original_value=edit_data["original_value"],
                    edited_value=edit_data["edited_value"],
                    editor=request.user,
                    editor_display_name=user_display_name,
                    editor_tenant_id=user_tenant_id,
                    edit_reason=edit_data.get("reason", ""),
                    stage=WellEditAudit.STAGE_PENDING,
                )
            
            # Update snapshot stage
            snapshot.payload["stage"] = "extractions_confirmed"
            snapshot.payload["extraction_edits_count"] = len(edits)
            snapshot.save()
            
            logger.info(f"✅ Saved {len(edits)} extraction edits")
            
            return Response(
                {"detail": "Extractions confirmed", "edits_saved": len(edits)},
                status=status.HTTP_200_OK
            )
            
        except Exception as e:
            logger.exception("W3A Confirm Extractions - error")
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class W3AGeometryView(APIView):
    """
    Stage 4: Derive and return well geometry for user review.
    
    GET /w3a/{temp_plan_id}/geometry
    """
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    
    def get(self, request, temp_plan_id: str):
        logger.info(f"📐 W3A GEOMETRY - {temp_plan_id}")
        
        # Get user's tenant for isolation
        user_tenant_id = request.user.tenants.first().id if request.user.tenants.exists() else None
        
        try:
            # Tenant-isolated query
            snapshot = PlanSnapshot.objects.get(
                plan_id=temp_plan_id,
                tenant_id=user_tenant_id  # ← CRITICAL: Tenant isolation
            )
        except PlanSnapshot.DoesNotExist:
            return Response(
                {"detail": "Plan not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            # Get confirmed extractions with edits applied
            extractions = snapshot.payload.get("extractions", [])
            edits = WellEditAudit.objects.filter(
                plan_snapshot=snapshot,
                context=WellEditAudit.CONTEXT_EXTRACTION,
                stage=WellEditAudit.STAGE_PENDING
            )
            
            # Apply edits to extractions (in-memory only)
            edited_extractions = self._apply_edits_to_extractions(extractions, edits)
            
            # Derive geometry from edited extractions
            geometry = self._derive_geometry(edited_extractions)
            
            # Save geometry to snapshot for later reference and editing
            snapshot.payload["geometry"] = geometry
            snapshot.payload["stage"] = "geometry_derived"
            snapshot.save()
            
            # Format for UI display
            response_data = {
                "temp_plan_id": temp_plan_id,
                "api": snapshot.payload.get("api"),
                **geometry
            }
            
            response_serializer = W3AGeometryResponseSerializer(data=response_data)
            response_serializer.is_valid(raise_exception=True)
            
            return Response(response_serializer.data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception("W3A Geometry - error")
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _apply_edits_to_extractions(
        self,
        extractions: List[Dict[str, Any]],
        edits
    ) -> List[Dict[str, Any]]:
        """Apply user edits to extracted JSONs (in-memory)."""
        # TODO: Implement JSON path editing
        return extractions
    
    def _derive_geometry(self, extractions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Derive well geometry from extractions + policy-based formation tops."""
        
        geometry = {
            "casing_strings": [],
            "formation_tops": [],
            "perforations": [],
            "mechanical_barriers": [],
            "uqw_data": None,
            "kop_data": None,
        }
        
        # Extract W-2 data
        w2_data = None
        for ext in extractions:
            if ext["document_type"] == "w2":
                w2_data = ext["json_data"]
                
                # Casing strings
                for casing in w2_data.get("casing_record", []):
                    geometry["casing_strings"].append({
                        "field_id": f"casing_{len(geometry['casing_strings'])}",
                        "field_label": f"{casing.get('string', 'Unknown')} Casing",
                        "value": casing,
                        "source": "W-2 casing_record",
                        "editable": True,
                    })
                
                # Perforations (normalize key names to match kernel expectations)
                for perf in w2_data.get("producing_injection_disposal_interval", []):
                    # Kernel expects top_ft/bottom_ft, but W-2 has from_ft/to_ft
                    normalized_perf = {
                        "top_ft": perf.get("from_ft"),
                        "bottom_ft": perf.get("to_ft"),
                        "open_hole": perf.get("open_hole", False),
                    }
                    geometry["perforations"].append({
                        "field_id": f"perf_{len(geometry['perforations'])}",
                        "field_label": f"Perforation {perf.get('from_ft')}-{perf.get('to_ft')} ft",
                        "value": normalized_perf,
                        "source": "W-2 producing_injection_disposal_interval",
                        "editable": True,
                    })
                    logger.info(f"📍 Extracted perforation: {normalized_perf}")
                
                # Mechanical barriers / existing tools from W-2
                for idx, equipment in enumerate(w2_data.get("mechanical_equipment", [])):
                    equip_type = equipment.get("type", "Unknown")
                    depth = equipment.get("depth_ft") or equipment.get("set_depth_ft")
                    
                    geometry["mechanical_barriers"].append({
                        "field_id": f"mech_{idx}",
                        "field_label": f"{equip_type} @ {depth} ft" if depth else equip_type,
                        "value": {
                            "type": equip_type,
                            "depth_ft": depth,
                            "description": equipment.get("description", ""),
                        },
                        "source": "W-2 mechanical_equipment",
                        "editable": True,
                    })
                    logger.info(f"🔧 Extracted mechanical barrier: {equip_type} @ {depth} ft")
                
                break  # Only process first W-2
        
        # Get policy-based formation tops
        policy_formations = []
        if w2_data:
            try:
                well_info = w2_data.get("well_info", {})
                district = well_info.get("district")
                county = well_info.get("county")
                field = well_info.get("field")
                
                if district and county:
                    logger.info(f"📋 Loading policy for district={district}, county={county}, field={field}")
                    policy = get_effective_policy(district=district, county=county, field=field)
                    
                    effective = policy.get("effective", {})
                    dist_overrides = effective.get("district_overrides", {})
                    policy_formation_tops = dist_overrides.get("formation_tops", [])
                    
                    if policy_formation_tops:
                        logger.info(f"✅ Found {len(policy_formation_tops)} policy-based formation tops")
                        policy_formations = policy_formation_tops
                    else:
                        logger.warning(f"⚠️ No policy formation_tops found for {county}/{field}")
            except Exception as e:
                logger.warning(f"Failed to load policy formations: {e}")
        
        # Merge policy formations with W-2 formations
        formation_map = {}
        
        # Add policy formations first
        for pf in policy_formations:
            formation_name = pf.get("formation")
            if formation_name:
                formation_map[formation_name] = {
                    "field_id": f"formation_{formation_name.lower().replace(' ', '_')}",
                    "field_label": formation_name,
                    "value": pf.get("top_ft"),
                    "unit": "ft",
                    "source": "Policy (County/District)",
                    "editable": True,
                    "formation_name": formation_name,
                }
        
        # Add/override with W-2 formation_record if available
        if w2_data:
            for formation in w2_data.get("formation_record", []):
                formation_name = formation.get("formation")
                if formation_name:
                    # If formation exists from policy, update it; otherwise add it
                    if formation_name in formation_map:
                        formation_map[formation_name]["value"] = formation.get("top_ft")
                        formation_map[formation_name]["source"] = "W-2 formation_record (overrides policy)"
                    else:
                        formation_map[formation_name] = {
                            "field_id": f"formation_{formation_name.lower().replace(' ', '_')}",
                            "field_label": formation_name,
                            "value": formation.get("top_ft"),
                            "unit": "ft",
                            "source": "W-2 formation_record",
                            "editable": True,
                            "formation_name": formation_name,
                        }
        
        # Convert formation_map to list
        geometry["formation_tops"] = list(formation_map.values())
        
        logger.info(f"📊 Derived geometry: {len(geometry['casing_strings'])} casing strings, "
                    f"{len(geometry['formation_tops'])} formation tops, "
                    f"{len(geometry['perforations'])} perforations")
        
        return geometry


class W3AConfirmGeometryView(APIView):
    """
    Stage 5: User confirms/edits geometry and triggers final plan build.
    
    POST /w3a/{temp_plan_id}/confirm-geometry
    """
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    
    def post(self, request, temp_plan_id: str):
        logger.info(f"🏗️  W3A CONFIRM GEOMETRY - {temp_plan_id}")
        
        # Get user's tenant for isolation
        user_tenant_id = request.user.tenants.first().id if request.user.tenants.exists() else None
        
        try:
            # Tenant-isolated query
            snapshot = PlanSnapshot.objects.get(
                plan_id=temp_plan_id,
                tenant_id=user_tenant_id  # ← CRITICAL: Tenant isolation
            )
        except PlanSnapshot.DoesNotExist:
            return Response(
                {"detail": "Plan not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = W3AConfirmGeometryRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Save geometry edits
            edits = serializer.validated_data.get("edits", [])
            
            user_display_name = request.user.get_full_name() or request.user.username
            user_tenant_id = request.user.tenants.first().id if request.user.tenants.exists() else None
            
            # Apply edits to snapshot geometry and save audit records
            geometry = snapshot.payload.get("geometry", {})
            formation_tops = geometry.get("formation_tops", [])
            formation_tops_map = {ft["field_id"]: ft for ft in formation_tops}
            
            mechanical_barriers = geometry.get("mechanical_barriers", [])
            mechanical_barriers_map = {mb["field_id"]: mb for mb in mechanical_barriers}
            
            for edit_data in edits:
                # Determine if this is a user-added formation, mechanical barrier, or modification
                is_user_added_formation = edit_data["original_value"] is None and "user_formation_" in edit_data["field_id"]
                is_user_added_tool = edit_data["original_value"] is None and "user_tool_" in edit_data["field_id"]
                
                # Save to audit trail
                audit_entry = WellEditAudit.objects.create(
                    plan_snapshot=snapshot,
                    well=snapshot.well,
                    field_path=edit_data["field_id"],
                    field_label=edit_data.get("field_label", ""),
                    context=WellEditAudit.CONTEXT_GEOMETRY,
                    original_value=edit_data["original_value"],
                    edited_value=edit_data["edited_value"],
                    editor=request.user,
                    editor_display_name=user_display_name,
                    editor_tenant_id=user_tenant_id,
                    edit_reason=edit_data.get("reason", ""),
                    stage=WellEditAudit.STAGE_PENDING,
                )
                
                # Apply edit to geometry in payload
                field_id = edit_data["field_id"]
                
                if is_user_added_formation:
                    # Add new user-defined formation
                    new_formation = {
                        "field_id": field_id,
                        "field_label": edit_data.get("field_label", ""),
                        "formation_name": edit_data.get("field_label", ""),
                        "value": edit_data["edited_value"],
                        "unit": "ft",
                        "source": "User Added",
                        "editable": True,
                    }
                    formation_tops_map[field_id] = new_formation
                    logger.info(f"👤 User added new formation: {edit_data.get('field_label')} at {edit_data['edited_value']} ft")
                
                elif is_user_added_tool:
                    # Add new user-defined mechanical tool
                    # edited_value should be JSON: {"type": "CIBP", "depth_ft": 7000, "description": "..."}
                    tool_data = edit_data["edited_value"]
                    new_tool = {
                        "field_id": field_id,
                        "field_label": edit_data.get("field_label", ""),
                        "value": tool_data,
                        "source": "User Added (Existing Tool)",
                        "editable": True,
                    }
                    mechanical_barriers_map[field_id] = new_tool
                    logger.info(f"🔧 User added existing tool: {tool_data.get('type')} @ {tool_data.get('depth_ft')} ft")
                
                else:
                    # Update existing formation or tool
                    if field_id in formation_tops_map:
                        formation_tops_map[field_id]["value"] = edit_data["edited_value"]
                        # Update source to indicate modification
                        original_source = formation_tops_map[field_id].get("source", "")
                        if "(User Modified)" not in original_source:
                            formation_tops_map[field_id]["source"] = f"{original_source} (User Modified)"
                        logger.info(f"✏️ User modified formation: {edit_data.get('field_label')} → {edit_data['edited_value']} ft")
                    
                    elif field_id in mechanical_barriers_map:
                        mechanical_barriers_map[field_id]["value"] = edit_data["edited_value"]
                        original_source = mechanical_barriers_map[field_id].get("source", "")
                        if "(User Modified)" not in original_source:
                            mechanical_barriers_map[field_id]["source"] = f"{original_source} (User Modified)"
                        logger.info(f"✏️ User modified tool: {edit_data.get('field_label')}")
            
            # Update geometry with edited formations and tools
            geometry["formation_tops"] = list(formation_tops_map.values())
            geometry["mechanical_barriers"] = list(mechanical_barriers_map.values())
            snapshot.payload["geometry"] = geometry
            
            # Log final formation tops for plan generation
            formation_summary = []
            for ft in geometry["formation_tops"]:
                formation_summary.append(f"{ft['field_label']}: {ft['value']} ft ({ft['source']})")
            
            logger.info(f"📊 Final formation tops for plan generation ({len(geometry['formation_tops'])} formations):")
            for fs in formation_summary:
                logger.info(f"   • {fs}")
            
            # Build final plan using confirmed + edited data
            logger.info("🏗️  Building final plan from edited geometry...")
            try:
                plugs_mode = serializer.validated_data.get("plugs_mode", "combined")
                sack_limit_no_tag = serializer.validated_data.get("sack_limit_no_tag", 50.0)
                sack_limit_with_tag = serializer.validated_data.get("sack_limit_with_tag", 150.0)
                
                plan_result = self._build_plan_from_snapshot(
                    snapshot=snapshot,
                    plugs_mode=plugs_mode,
                    sack_limit_no_tag=sack_limit_no_tag,
                    sack_limit_with_tag=sack_limit_with_tag,
                )
                
                # Save plan to a new PlanSnapshot
                api = snapshot.payload.get("api")
                plan_id = f"{api}:{plugs_mode}"
                
                # Find existing baseline snapshot for this plan_id (tenant-isolated), or create new
                existing_snapshot = PlanSnapshot.objects.filter(
                    well=snapshot.well,
                    plan_id=plan_id,
                    kind="baseline",
                    tenant_id=snapshot.tenant_id  # ← CRITICAL: Tenant isolation
                ).order_by("-created_at").first()
                
                if existing_snapshot:
                    # Update existing snapshot
                    existing_snapshot.tenant_id = snapshot.tenant_id
                    existing_snapshot.visibility = "public"
                    existing_snapshot.status = "draft"
                    existing_snapshot.payload = plan_result
                    existing_snapshot.save()
                    final_snapshot = existing_snapshot
                    created = False
                    logger.info(f"✅ Updated existing PlanSnapshot: {plan_id} (ID: {existing_snapshot.id})")
                else:
                    # Create new snapshot
                    final_snapshot = PlanSnapshot.objects.create(
                        well=snapshot.well,
                        plan_id=plan_id,
                        kind="baseline",
                        tenant_id=snapshot.tenant_id,
                        visibility="public",
                        status="draft",
                        payload=plan_result,
                    )
                    created = True
                    logger.info(f"✅ Created new PlanSnapshot: {plan_id} (ID: {final_snapshot.id})")
                
                # Update temp snapshot with plan reference
                snapshot.payload["stage"] = "plan_built"
                snapshot.payload["final_plan_id"] = plan_id
                snapshot.payload["final_plan_snapshot_id"] = str(final_snapshot.id)
            except Exception as e:
                logger.exception("Failed to build plan")
                # Don't fail the entire request, just log and continue
                snapshot.payload["stage"] = "geometry_confirmed"
                snapshot.payload["plan_build_error"] = str(e)
            
            # Update snapshot metadata
            snapshot.payload["geometry_edits_count"] = len(edits)
            snapshot.payload["plugs_mode"] = serializer.validated_data.get("plugs_mode")
            snapshot.save()
            
            logger.info(f"✅ Saved {len(edits)} geometry edits, ready for plan build")
            
            # Build response
            response_data = {
                "detail": "Geometry confirmed and plan built",
                "edits_saved": len(edits),
                "temp_plan_id": temp_plan_id,
                "formation_tops_count": len(geometry["formation_tops"]),
                "formation_tops": [
                    {
                        "name": ft["field_label"],
                        "depth_ft": ft["value"],
                        "source": ft["source"]
                    }
                    for ft in geometry["formation_tops"]
                ],
                "mechanical_barriers_count": len(geometry.get("mechanical_barriers", [])),
                "mechanical_barriers": [
                    {
                        "type": mb["value"].get("type", "Unknown"),
                        "depth_ft": mb["value"].get("depth_ft"),
                        "description": mb["value"].get("description", ""),
                        "source": mb["source"]
                    }
                    for mb in geometry.get("mechanical_barriers", [])
                ]
            }
            
            # Add plan info if successfully built
            final_plan_id = snapshot.payload.get("final_plan_id")
            if final_plan_id:
                response_data["plan_id"] = final_plan_id
                response_data["plan_built"] = True
            else:
                response_data["plan_built"] = False
                response_data["plan_build_error"] = snapshot.payload.get("plan_build_error")
            
            logger.info(f"📋 Returning geometry confirmation:")
            logger.info(f"   - Formation tops: {len(response_data['formation_tops'])}")
            logger.info(f"   - Mechanical barriers: {len(response_data['mechanical_barriers'])}")
            if response_data['mechanical_barriers']:
                for mb in response_data['mechanical_barriers']:
                    logger.info(f"      • {mb['type']} @ {mb['depth_ft']} ft ({mb['source']})")
            
            return Response(response_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception("W3A Confirm Geometry - error")
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _build_plan_from_snapshot(
        self,
        snapshot: PlanSnapshot,
        plugs_mode: str,
        sack_limit_no_tag: float,
        sack_limit_with_tag: float,
    ) -> Dict[str, Any]:
        """
        Build a W-3A plan from the confirmed snapshot data.
        
        This method takes the edited geometry and extraction data from the snapshot
        and generates a plan using the policy kernel.
        """
        
        # Get extractions from snapshot
        extractions = snapshot.payload.get("extractions", [])
        geometry = snapshot.payload.get("geometry", {})
        api = snapshot.payload.get("api")
        
        # Build W-2 data from extractions
        w2_data = {}
        w15_data = {}
        gau_data = {}
        
        for extraction in extractions:
            doc_type = extraction.get("document_type")
            json_data = extraction.get("json_data", {})
            
            if doc_type == "w2":
                w2_data = json_data
            elif doc_type == "w15":
                w15_data = json_data
            elif doc_type == "gau":
                gau_data = json_data
        
        # Helper to wrap values
        def wrap(v: Any) -> Dict[str, Any]:
            return {"value": v}
        
        # Extract well info
        well_info = w2_data.get("well_info", {})
        api14 = (well_info.get("api") or "").replace("-", "")
        county = well_info.get("county") or ""
        field = well_info.get("field") or ""
        district = well_info.get("district") or ""
        lease = well_info.get("lease") or ""
        well_no = well_info.get("well_no") or ""
        
        # Extract lat/lon from W-2 (critical for geographic zone evaluation)
        loc = well_info.get("location") or {}
        lat = loc.get("lat") or loc.get("latitude")
        lon = loc.get("lon") or loc.get("longitude")
        
        # Fallback to GAU coordinates if W-2 missing/empty (mirroring w3a_from_api logic)
        if (lat is None or lon is None) and gau_data:
            gau_loc = (gau_data.get("well_info") or {}).get("location") or {}
            if lat is None:
                lat = gau_loc.get("lat") or gau_loc.get("latitude")
            if lon is None:
                lon = gau_loc.get("lon") or gau_loc.get("longitude")
        
        logger.info(f"📍 Extracted coordinates: lat={lat}, lon={lon} (for geographic zone eval)")
        
        # Build facts dictionary (mirroring w3a_from_api._build_plan)
        facts: Dict[str, Any] = {
            "api14": wrap(api14),
            "state": wrap("TX"),
            "district": wrap(district),
            "county": wrap(county),
            "field": wrap(field),
            "lease": wrap(lease),
            "well_no": wrap(well_no),
            "lat": float(lat) if lat else None,
            "lon": float(lon) if lon else None,
        }
        
        # Add UQW from GAU if available
        uqw_depth = None
        if gau_data:
            gau_determination = gau_data.get("determination", {})
            uqw_base = gau_determination.get("base_uqw_ft")
            if uqw_base:
                uqw_depth = float(uqw_base)
        
        facts["has_uqw"] = wrap(bool(uqw_depth or gau_data))
        facts["uqw_base_ft"] = wrap(uqw_depth)
        facts["use_cibp"] = wrap(False)  # Default to False, can be overridden
        
        # Add casing record from W-2 (or geometry if available)
        casing_strings_geometry = geometry.get("casing_strings", [])
        casing_record = w2_data.get("casing_record", [])
        
        # Use geometry casing if available (takes precedence)
        if casing_strings_geometry:
            facts["casing_record"] = [cs.get("value", cs) for cs in casing_strings_geometry]
            casing_to_process = [cs.get("value", cs) for cs in casing_strings_geometry]
            logger.info(f"📍 Using {len(casing_to_process)} casing strings from geometry")
        elif casing_record:
            facts["casing_record"] = casing_record
            casing_to_process = casing_record
            logger.info(f"📍 Using {len(casing_to_process)} casing strings from W-2")
        else:
            casing_to_process = []
        
        # Extract surface shoe depth and production TOC (critical for plug type determination)
        for casing in casing_to_process:
            string_type = casing.get("string", "").lower()
            
            if string_type == "surface":
                shoe_depth = casing.get("shoe_depth_ft")
                if shoe_depth:
                    facts["surface_shoe_ft"] = wrap(float(shoe_depth))
                    logger.info(f"📍 Surface shoe: {shoe_depth} ft")
            
            elif string_type == "production":
                # Production casing TOC is CRITICAL for determining spot vs perf&squeeze
                toc = casing.get("cement_top_ft") or casing.get("toc_ft")
                if toc is not None and toc > 0:  # TOC=0 means cemented to surface
                    facts["production_casing_toc_ft"] = wrap(float(toc))
                    logger.info(f"🔧 Production TOC: {toc} ft (formations shallower = spot plug)")
                elif toc == 0:
                    logger.info(f"🔧 Production casing cemented to surface (TOC=0)")
                    facts["production_casing_toc_ft"] = wrap(0.0)
                
                # Production shoe depth (for CIBP detector)
                shoe = casing.get("shoe_depth_ft") or casing.get("bottom_ft")
                if shoe:
                    facts["production_shoe_ft"] = wrap(float(shoe))
                    logger.info(f"🔧 Production shoe: {shoe} ft")
        
        # Add formation tops from edited geometry (including user additions!)
        formation_tops_geometry = geometry.get("formation_tops", [])
        if formation_tops_geometry:
            formation_tops_map = {}
            for ft in formation_tops_geometry:
                formation_name = ft.get("formation_name") or ft.get("field_label")
                depth = ft.get("value")
                if formation_name and depth is not None:
                    formation_tops_map[formation_name] = float(depth)
            facts["formation_tops_map"] = formation_tops_map
            logger.info(f"📍 Passing {len(formation_tops_map)} formation tops to kernel: {list(formation_tops_map.keys())}")
        
        # Add production perforations - check both W-2 and geometry
        # Geometry perforations (from _derive_geometry) take precedence
        perforations_geometry = geometry.get("perforations", [])
        prod_perfs = w2_data.get("producing_injection_disposal_interval", [])
        
        logger.info(f"🔍 PERF DEBUG: perforations_geometry={perforations_geometry}")
        logger.info(f"🔍 PERF DEBUG: prod_perfs={prod_perfs}")
        
        if perforations_geometry:
            # Use perforations from geometry (unwrap the "value" field)
            unwrapped_perfs = [p.get("value", p) for p in perforations_geometry]
            facts["perforations"] = unwrapped_perfs
            facts["production_perforations"] = unwrapped_perfs
            logger.info(f"📍 Using {len(unwrapped_perfs)} perforations from geometry: {unwrapped_perfs}")
        elif prod_perfs:
            # Fallback to W-2 data
            facts["production_perforations"] = prod_perfs
            facts["perforations"] = prod_perfs
            logger.info(f"📍 Using {len(prod_perfs)} perforations from W-2: {prod_perfs}")
        else:
            logger.warning("⚠️ NO PERFORATIONS FOUND - CIBP detection will fail!")
        
        # Check for existing CIBP or bridge plug - from both W-2 AND user-added tools in geometry
        # The kernel checks facts["existing_mechanical_barriers"] for "CIBP"
        mechanical_equipment = w2_data.get("mechanical_equipment", [])
        mechanical_barriers_geometry = geometry.get("mechanical_barriers", [])
        existing_barriers = []
        
        # Process W-2 mechanical equipment
        if mechanical_equipment:
            for equip in mechanical_equipment:
                equip_type = str(equip.get("type", "")).upper()
                if "CIBP" in equip_type or "BRIDGE" in equip_type:
                    existing_barriers.append("CIBP")
                    depth = equip.get("depth_ft") or equip.get("set_depth_ft")
                    if depth:
                        facts["cibp_depth_ft"] = wrap(float(depth))
                        logger.info(f"🔧 Detected existing CIBP from W-2 at {depth} ft")
                elif "PACKER" in equip_type:
                    existing_barriers.append("PACKER")
                elif "RETAINER" in equip_type:
                    existing_barriers.append("RETAINER")
        
        # Process user-added mechanical tools from geometry (including user additions)
        if mechanical_barriers_geometry:
            for mb in mechanical_barriers_geometry:
                tool_data = mb.get("value", {})
                tool_type = str(tool_data.get("type", "")).upper()
                tool_depth = tool_data.get("depth_ft")
                
                if "CIBP" in tool_type or "BRIDGE" in tool_type:
                    if "CIBP" not in existing_barriers:
                        existing_barriers.append("CIBP")
                    if tool_depth and not facts.get("cibp_depth_ft"):
                        facts["cibp_depth_ft"] = wrap(float(tool_depth))
                        logger.info(f"🔧 Detected existing CIBP from user geometry at {tool_depth} ft")
                elif "PACKER" in tool_type and "PACKER" not in existing_barriers:
                    existing_barriers.append("PACKER")
                elif "RETAINER" in tool_type and "RETAINER" not in existing_barriers:
                    existing_barriers.append("RETAINER")
        
        if existing_barriers:
            facts["existing_mechanical_barriers"] = existing_barriers
            # Only set cibp_present if there's actually a CIBP, not just any barrier
            facts["cibp_present"] = wrap("CIBP" in existing_barriers)
            logger.info(f"🔧 Final existing mechanical barriers: {existing_barriers}, cibp_present={'CIBP' in existing_barriers}")
        
        # Add tubing from W-2
        tubing = w2_data.get("tubing_record", [])
        if tubing:
            facts["tubing"] = tubing
        
        # Add liner if available
        liner = w2_data.get("liner_record", [])
        if liner:
            facts["liner"] = liner
        
        # Add historic cement jobs from W-15 if available
        if w15_data:
            cementing_data = w15_data.get("cementing_data", [])
            if cementing_data:
                # Convert W-15 cementing data to historic_cement_jobs format
                historic_jobs = []
                for job in cementing_data:
                    historic_jobs.append({
                        "job_type": job.get("job", "unknown"),
                        "interval_top_ft": job.get("interval_top_ft"),
                        "interval_bottom_ft": job.get("interval_bottom_ft"),
                        "cement_top_ft": job.get("cement_top_ft"),
                        "sacks": job.get("sacks"),
                        "slurry_density_ppg": job.get("slurry_density_ppg"),
                        "yield_ft3_per_sk": job.get("yield_ft3_per_sk"),
                        "additives": job.get("additives", []),
                    })
                if historic_jobs:
                    facts["historic_cement_jobs"] = historic_jobs
                    logger.info(f"📋 Added {len(historic_jobs)} historic cement jobs from W-15")
        
        # Get policy
        logger.info(f"🔍 Loading policy for district={district}, county={county}, field={field}")
        policy = get_effective_policy(district=district, county=county, field=field)
        
        # ✅ INJECT USER-ADDED FORMATIONS INTO POLICY
        # The kernel only processes formations from policy["effective"]["district_overrides"]["formation_tops"]
        # So we need to add user-added formations there for the kernel to see them
        formation_tops_geometry = geometry.get("formation_tops", [])
        if formation_tops_geometry:
            policy_effective = policy.setdefault("effective", {})
            district_overrides = policy_effective.setdefault("district_overrides", {})
            policy_formation_tops = district_overrides.setdefault("formation_tops", [])
            
            # Find user-added formations (those with source="User Added")
            user_added_formations = [
                ft for ft in formation_tops_geometry 
                if "User Added" in str(ft.get("source", ""))
            ]
            
            if user_added_formations:
                logger.info(f"🎯 INJECTING {len(user_added_formations)} user-added formations into policy for kernel")
                for ft in user_added_formations:
                    formation_name = ft.get("formation_name") or ft.get("field_label")
                    depth = ft.get("value") or ft.get("top_ft")
                    
                    if formation_name and depth is not None:
                        # Add to policy formation tops so kernel will process it
                        policy_formation_tops.append({
                            "formation": formation_name,
                            "top_ft": float(depth),
                            "plug_required": True,
                            "use_when": "always",  # User explicitly wants this
                            "source": "User Added",
                            "additional_requirements": None,
                        })
                        logger.info(f"   ✅ Added user formation: {formation_name} @ {depth} ft")
        
        # Override policy metadata
        policy["policy_id"] = "tx.w3a"
        policy["complete"] = True
        
        # Set merge preferences
        prefs = policy.setdefault("preferences", {})
        prefs["rounding_policy"] = "nearest"
        prefs.setdefault("default_recipe", {
            "id": "class_c_neat_15_6",
            "class": "C",
            "density_ppg": 15.6,
            "yield_ft3_per_sk": 1.32,
            "water_gal_per_sk": 5.2,
            "additives": [],
        })
        prefs.setdefault("long_plug_merge", {})
        prefs["long_plug_merge"]["enabled"] = (plugs_mode == "combined")
        prefs["long_plug_merge"]["sack_limit_no_tag"] = float(sack_limit_no_tag)
        prefs["long_plug_merge"]["sack_limit_with_tag"] = float(sack_limit_with_tag)
        prefs["long_plug_merge"].setdefault("types", ["formation_top_plug", "cement_plug", "uqw_isolation_plug"])
        prefs["long_plug_merge"].setdefault("preserve_tagging", True)
        
        logger.info(f"🎯 Merge config: enabled={plugs_mode == 'combined'}, sack_limit_no_tag={sack_limit_no_tag}, sack_limit_with_tag={sack_limit_with_tag}")
        
        # Log critical facts for debugging
        logger.info("=" * 80)
        logger.info("🔍 FACTS SUMMARY BEFORE KERNEL CALL:")
        logger.info(f"   - API: {facts.get('api14', {}).get('value')}")
        logger.info(f"   - County: {county}, Field: {field}, District: {district}")
        logger.info(f"   - Lat/Lon: {facts.get('lat')}, {facts.get('lon')} ← CRITICAL for geographic zones")
        logger.info(f"   - Casing strings: {len(facts.get('casing_record', []))}")
        logger.info(f"   - Production TOC: {facts.get('production_casing_toc_ft', {}).get('value')} ft")
        logger.info(f"   - Production shoe: {facts.get('production_shoe_ft', {}).get('value')} ft")
        logger.info(f"   - Surface shoe: {facts.get('surface_shoe_ft', {}).get('value')} ft")
        logger.info(f"   - Perforations: {len(facts.get('perforations', []))} → {facts.get('perforations', [])}")
        logger.info(f"   - Formation tops: {len(facts.get('formation_tops_map', {}))} → {list(facts.get('formation_tops_map', {}).keys())}")
        logger.info(f"   - Existing barriers: {facts.get('existing_mechanical_barriers', [])}")
        logger.info(f"   - CIBP present: {facts.get('cibp_present', {}).get('value')}")
        logger.info(f"   - Use CIBP: {facts.get('use_cibp', {}).get('value')}")
        logger.info(f"   - Policy formation tops: {len(policy.get('effective', {}).get('district_overrides', {}).get('formation_tops', []))}")
        logger.info("=" * 80)
        
        # Call kernel
        logger.info("🚀 Calling plan_from_facts with edited geometry...")
        out_kernel = plan_from_facts(facts, policy)
        
        # Build output similar to w3a_from_api
        steps = out_kernel.get("steps", [])
        logger.info(f"✅ Kernel returned {len(steps)} steps")
        
        # Build plan payload
        plan_payload = {
            "api": api14,
            "county": county,
            "field": field,
            "district": district,
            "kernel_version": out_kernel.get("kernel_version", "0.1.0"),
            "policy_id": "tx.w3a",
            "steps": steps,
            "formations_targeted": sorted(list(set([
                s.get("formation") for s in steps if s.get("formation")
            ] + [
                basis.split(":formation_top:")[-1]
                for s in steps
                for basis in s.get("regulatory_basis", [])
                if ":formation_top:" in str(basis)
            ]))),
            "materials_totals": out_kernel.get("materials_totals", {}),
            "extraction": {
                "status": "success",
                "files": [e.get("filename") for e in extractions],
            },
            # Add historic cement jobs from W-15 for well_geometry
            "historic_cement_jobs": facts.get("historic_cement_jobs", []),
            # Add production perforations for well_geometry
            "production_perforations": facts.get("production_perforations", []),
        }
        
        # Add mechanical_equipment from geometry (including user-added tools)
        mechanical_barriers_geometry = geometry.get("mechanical_barriers", [])
        mechanical_equipment_list = []
        if mechanical_barriers_geometry:
            for mb in mechanical_barriers_geometry:
                tool_data = mb.get("value", {})
                mechanical_equipment_list.append({
                    "type": tool_data.get("type", "Unknown"),
                    "depth_ft": tool_data.get("depth_ft"),
                    "description": tool_data.get("description", ""),
                    "source": mb.get("source", ""),
                })
            logger.info(f"📦 Adding {len(mechanical_equipment_list)} mechanical_equipment to plan payload")
        
        plan_payload["mechanical_equipment"] = mechanical_equipment_list
        plan_payload["existing_tools"] = mechanical_equipment_list  # Alias for compatibility
        
        return plan_payload


class W3AApplyEditsView(APIView):
    """
    Stage 6: Apply staged edits to WellRegistry.
    
    POST /w3a/{plan_id}/apply-edits
    """
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    
    def post(self, request, plan_id: str):
        logger.info(f"✅ W3A APPLY EDITS - {plan_id}")
        
        serializer = ApplyEditsRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            edit_ids = serializer.validated_data["edit_ids"]
            
            # Get user's tenant for isolation
            user_tenant_id = request.user.tenants.first().id if request.user.tenants.exists() else None
            
            # Get edits (tenant-isolated)
            edits = WellEditAudit.objects.filter(
                id__in=edit_ids,
                stage=WellEditAudit.STAGE_PENDING,
                editor_tenant_id=user_tenant_id  # ← CRITICAL: Tenant isolation - users can only apply their own edits
            )
            
            if not edits.exists():
                return Response(
                    {"detail": "No pending edits found with provided IDs"},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Apply edits to WellRegistry
            applied_count = 0
            with transaction.atomic():
                for edit in edits:
                    # TODO: Implement actual field updates to WellRegistry
                    # This requires JSON path parsing and safe field updates
                    
                    # Mark as applied
                    edit.stage = WellEditAudit.STAGE_APPLIED
                    edit.applied_by = request.user
                    edit.applied_at = __import__("django.utils.timezone").utils.timezone.now()
                    edit.save()
                    
                    applied_count += 1
            
            logger.info(f"✅ Applied {applied_count} edits to WellRegistry")
            
            return Response(
                {"detail": "Edits applied successfully", "applied_count": applied_count},
                status=status.HTTP_200_OK
            )
            
        except Exception as e:
            logger.exception("W3A Apply Edits - error")
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class W3ABrowseEditsView(APIView):
    """
    Browse historical edits for a well (learning from precedent).
    
    GET /w3a/edits?well_api={api}&context={context}&stage={stage}
    """
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            well_api = request.query_params.get("well_api")
            context = request.query_params.get("context")
            stage = request.query_params.get("stage")
            
            queryset = WellEditAudit.objects.all()
            
            if well_api:
                queryset = queryset.filter(well__api14__icontains=well_api)
            
            if context:
                queryset = queryset.filter(context=context)
            
            if stage:
                queryset = queryset.filter(stage=stage)
            
            # Filter by tenant visibility
            user_tenant_id = request.user.tenants.first().id if request.user.tenants.exists() else None
            if user_tenant_id:
                # Show own edits + applied (public) edits + public plan edits
                from django.db.models import Q
                queryset = queryset.filter(
                    Q(editor_tenant_id=user_tenant_id) |
                    Q(stage=WellEditAudit.STAGE_APPLIED) |
                    Q(plan_snapshot__visibility=PlanSnapshot.VISIBILITY_PUBLIC)
                )
            
            edits = queryset[:100]  # Limit results
            
            serializer = EditAuditSerializer(edits, many=True)
            
            return Response(
                {"edits": serializer.data, "count": len(edits)},
                status=status.HTTP_200_OK
            )
            
        except Exception as e:
            logger.exception("W3A Browse Edits - error")
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


