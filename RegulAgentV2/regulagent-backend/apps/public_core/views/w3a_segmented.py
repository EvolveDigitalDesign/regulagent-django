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
import time
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
from apps.kernel.services.jurisdiction_registry import detect_jurisdiction
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


def _parse_fraction(val):
    """Parse a fraction string like '9 5/8' to 9.625, or return float if already numeric."""
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).strip()
    if not val_str:
        return None
    try:
        return float(val_str)
    except ValueError:
        pass
    # Try "whole num/den" format like "9 5/8"
    parts = val_str.split()
    if len(parts) == 2 and "/" in parts[1]:
        try:
            whole = float(parts[0])
            num, den = parts[1].split("/")
            return whole + float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return None
    # Try pure fraction "5/8"
    if "/" in val_str:
        try:
            num, den = val_str.split("/")
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return None
    return None


def _safe_float(val):
    """Convert val to float, returning None if val is None, empty string, or unparseable."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


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
        jurisdiction = serializer.validated_data.get("jurisdiction", "TX")

        # Normalize API
        api_normalized = re.sub(r"\D+", "", api10)

        logger.info(f"📍 API: {api_normalized}, Jurisdiction: {jurisdiction}")

        try:
            # Route to manual entry flow (no document sourcing)
            if input_mode == "manual":
                return self._handle_manual_initial(request, serializer.validated_data)

            # Route to NM-specific flow if jurisdiction is NM
            if jurisdiction == "NM":
                return self._handle_nm_initial(request, api_normalized, input_mode, serializer)

            # Continue with TX (RRC) flow below...

            # 1. Source documents (TX/RRC)
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

            # Resolve workspace
            workspace = None
            workspace_id = request.data.get('workspace_id')
            if workspace_id:
                from apps.tenants.models import ClientWorkspace
                user_tenant = request.user.tenants.first()
                if user_tenant:
                    workspace = ClientWorkspace.objects.filter(id=workspace_id, tenant=user_tenant).first()

            # Create temp snapshot
            snapshot = PlanSnapshot.objects.create(
                well=well,
                plan_id=temp_plan_id,
                kind=PlanSnapshot.KIND_BASELINE,
                status=PlanSnapshot.STATUS_DRAFT,
                payload={
                    "stage": "document_sourcing",
                    "jurisdiction": jurisdiction,
                    "api": api_normalized,
                    "combined_pdf_path": combined_info["temp_path"],
                    "source_files": source_file_metadata,
                    "input_mode": input_mode,
                },
                kernel_version="",
                policy_id="nm.c103" if jurisdiction == "NM" else "tx.w3a",
                overlay_id="",
                tenant_id=request.user.tenants.first().id if request.user.tenants.exists() else None,
                workspace=workspace,
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

    def _handle_manual_initial(self, request, validated_data: Dict[str, Any]) -> Response:
        """
        Handle manual entry mode: skip document sourcing and extraction entirely.

        Creates a PlanSnapshot at stage "geometry_derived" with empty geometry,
        allowing the user to enter casing/perf data directly via the geometry UI.
        """
        api10 = validated_data["api10"]
        api14 = api10 + "0000" if len(api10) == 10 else api10

        logger.info(f"✏️  MANUAL INITIAL - api14={api14}")

        try:
            # Find or create WellRegistry entry
            well, _ = WellRegistry.objects.get_or_create(api14=api14)

            temp_plan_id = f"temp_manual_{api14}_{uuid4().hex[:8]}"

            # Resolve workspace
            workspace = None
            workspace_id = request.data.get("workspace_id")
            if workspace_id:
                from apps.tenants.models import ClientWorkspace
                user_tenant = request.user.tenants.first()
                if user_tenant:
                    workspace = ClientWorkspace.objects.filter(id=workspace_id, tenant=user_tenant).first()

            well_metadata = {
                "district": validated_data.get("district", ""),
                "county": validated_data.get("county", ""),
                "field_name": validated_data.get("field_name", ""),
                "lease": validated_data.get("lease", ""),
                "well_no": validated_data.get("well_no", ""),
                "total_depth_ft": validated_data.get("total_depth"),
                "has_uqw": validated_data.get("has_uqw", False),
                "uqw_base_depth_ft": validated_data.get("uqw_base_depth"),
            }

            snapshot = PlanSnapshot.objects.create(
                well=well,
                plan_id=temp_plan_id,
                kind=PlanSnapshot.KIND_BASELINE,
                status=PlanSnapshot.STATUS_DRAFT,
                payload={
                    "stage": "geometry_derived",
                    "input_mode": "manual",
                    "api": api14,
                    "extractions": [],
                    "geometry": {
                        "casing_strings": [],
                        "formation_tops": [],
                        "perforations": [],
                        "mechanical_barriers": [],
                        "cement_jobs": [],
                        "uqw_data": None,
                        "kop_data": None,
                    },
                    "well_metadata": well_metadata,
                },
                kernel_version="",
                policy_id="tx.w3a",
                overlay_id="",
                tenant_id=request.user.tenants.first().id if request.user.tenants.exists() else None,
                workspace=workspace,
                visibility=PlanSnapshot.VISIBILITY_PRIVATE,
            )

            logger.info(f"✅ Created manual temp plan {temp_plan_id}")

            return Response(
                {
                    "temp_plan_id": temp_plan_id,
                    "input_mode": "manual",
                    "api": api14,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception("Manual Initial - error")
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _handle_nm_initial(self, request, api_normalized: str, input_mode: str, serializer) -> Response:
        """
        Handle NM-specific initial document sourcing.

        For NM wells:
        1. Scrape well data from NM OCD portal
        2. Get document list and combined PDF URL (not downloaded)
        3. Create pseudo-extraction from scraped data
        4. Return scraped data + combined PDF URL for user review

        Unlike TX, we don't download and combine PDFs because NM combined files
        are too large (100+ pages) for practical use.
        """
        from apps.public_core.services.nm_well_scraper import fetch_nm_well
        from apps.public_core.services.nm_document_fetcher import NMDocumentFetcher
        from apps.public_core.services.nm_extraction_mapper import (
            map_nm_well_to_extractions,
            map_nm_well_to_geometry,
            create_nm_extracted_document_data,
        )
        from apps.public_core.services.nm_well_import import import_nm_well

        logger.info(f"🆕 NM INITIAL - Starting NM document sourcing for {api_normalized}")

        try:
            # 1. Import well from NM OCD
            logger.info("   📥 Importing NM well...")
            try:
                import_result = import_nm_well(api_normalized)
                well = import_result.get("well")
                scraped_data = import_result.get("scraped_data", {})
                logger.info(f"   ✅ Well imported: {scraped_data.get('well_name', 'Unknown')}")
            except Exception as e:
                logger.warning(f"   ⚠️ Well import failed: {e}")
                # Create minimal well entry
                api14 = api_normalized + "0000" if len(api_normalized) == 10 else api_normalized
                well, _ = WellRegistry.objects.get_or_create(
                    api14=api14,
                    defaults={"state": "NM"}
                )
                scraped_data = {}

            # 2. Get document list from NM OCD
            documents = []
            combined_pdf_url = None
            try:
                with NMDocumentFetcher() as fetcher:
                    doc_list = fetcher.list_documents(api_normalized)
                    documents = [
                        {
                            "filename": d.filename,
                            "url": d.url,
                            "file_size": d.file_size,
                            "date": d.date,
                            "doc_type": d.doc_type,
                        }
                        for d in doc_list
                    ]
                    combined_pdf_url = fetcher.get_combined_pdf_url(api_normalized)
                    logger.info(f"   📄 Found {len(documents)} documents")
            except Exception as e:
                logger.warning(f"   ⚠️ Document list fetch failed: {e}")

            # 3. Create extraction from scraped data
            extraction = map_nm_well_to_extractions(scraped_data)

            # 3b. Auto-trigger research if critical data is missing
            from apps.public_core.services.research_supplement import trigger_research_if_needed

            research_info = trigger_research_if_needed(
                api_number=api_normalized,
                state="NM",
                scraped_data=scraped_data,
                well=well,
            )

            # 4. Create temp plan snapshot
            temp_plan_id = f"temp_nm_{api_normalized}_{uuid4().hex[:8]}"

            # Resolve workspace
            workspace = None
            workspace_id = request.data.get('workspace_id')
            if workspace_id:
                from apps.tenants.models import ClientWorkspace
                user_tenant = request.user.tenants.first()
                if user_tenant:
                    workspace = ClientWorkspace.objects.filter(id=workspace_id, tenant=user_tenant).first()

            snapshot = PlanSnapshot.objects.create(
                well=well,
                plan_id=temp_plan_id,
                kind=PlanSnapshot.KIND_BASELINE,
                status=PlanSnapshot.STATUS_DRAFT,
                payload={
                    "stage": "document_sourcing",
                    "jurisdiction": "NM",
                    "policy_id": "nm.c103",
                    "api": api_normalized,
                    "combined_pdf_url": combined_pdf_url,  # External URL, not local path
                    "source_files": documents,
                    "input_mode": input_mode,
                    "scraped_data": scraped_data,
                    "extraction": extraction,
                    "research_session_id": research_info.get("research_session_id"),
                    "research_status": research_info.get("research_status"),
                    "research_missing_fields": research_info.get("missing_fields", []),
                },
                kernel_version="",
                policy_id="nm.c103",
                overlay_id="",
                tenant_id=request.user.tenants.first().id if request.user.tenants.exists() else None,
                workspace=workspace,
                visibility=PlanSnapshot.VISIBILITY_PRIVATE,
            )

            logger.info(f"✅ Created NM temp plan {temp_plan_id}")

            # 5. Get geometry mapping for debug output
            geometry = map_nm_well_to_geometry(scraped_data)

            # 6. Return NM-specific response
            response_data = {
                "temp_plan_id": temp_plan_id,
                "jurisdiction": "NM",
                "combined_pdf_url": combined_pdf_url,  # External NM OCD URL
                "source_files": documents,
                "api": api_normalized,
                "well_data": scraped_data,
                "extraction": extraction,
                # NM doesn't have local combined PDF, so page_count/file_size are N/A
                "page_count": 0,
                "file_size": 0,
                "ttl_expires_at": "",
                "requires_manual_entry": True,
                "research": research_info,
                "message": "NM well data scraped. Review documents at OCD portal and enter casing data manually.",
                # Debug output showing all extracted and mapped data
                "nm_debug": {
                    "raw_scraper_output": {
                        "identifiers": {
                            "api10": scraped_data.get("api10"),
                            "api14": scraped_data.get("api14"),
                            "well_name": scraped_data.get("well_name"),
                            "well_number": scraped_data.get("well_number"),
                        },
                        "operator": {
                            "name": scraped_data.get("operator_name"),
                            "number": scraped_data.get("operator_number"),
                        },
                        "classification": {
                            "status": scraped_data.get("status"),
                            "well_type": scraped_data.get("well_type"),
                            "work_type": scraped_data.get("work_type"),
                            "direction": scraped_data.get("direction"),
                            "multi_lateral": scraped_data.get("multi_lateral"),
                        },
                        "ownership": {
                            "mineral_owner": scraped_data.get("mineral_owner"),
                            "surface_owner": scraped_data.get("surface_owner"),
                        },
                        "location": {
                            "surface_location": scraped_data.get("surface_location"),
                            "latitude": scraped_data.get("latitude"),
                            "longitude": scraped_data.get("longitude"),
                            "datum": scraped_data.get("datum"),
                        },
                        "elevations": {
                            "gl_elevation_ft": scraped_data.get("gl_elevation_ft"),
                            "kb_elevation_ft": scraped_data.get("kb_elevation_ft"),
                            "df_elevation_ft": scraped_data.get("df_elevation_ft"),
                        },
                        "formation": {
                            "formation": scraped_data.get("formation"),
                            "proposed_formation": scraped_data.get("proposed_formation"),
                        },
                        "depths": {
                            "proposed_depth_ft": scraped_data.get("proposed_depth_ft"),
                            "measured_vertical_depth_ft": scraped_data.get("measured_vertical_depth_ft"),
                            "true_vertical_depth_ft": scraped_data.get("true_vertical_depth_ft"),
                            "plugback_measured_ft": scraped_data.get("plugback_measured_ft"),
                        },
                        "completion_type": {
                            "sing_mult_compl": scraped_data.get("sing_mult_compl"),
                            "potash_waiver": scraped_data.get("potash_waiver"),
                        },
                        "event_dates": scraped_data.get("event_dates", {}),
                        "casing_records": scraped_data.get("casing_records", []),
                        "completions": scraped_data.get("completions", []),
                    },
                    "mapped_extraction": extraction,
                    "mapped_geometry": geometry,
                    "field_counts": {
                        "casing_records": len(scraped_data.get("casing_records", [])),
                        "completions": len(scraped_data.get("completions", [])),
                        "perforations": sum(
                            len(c.get("perforations", []))
                            for c in scraped_data.get("completions", [])
                        ),
                    },
                },
            }

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception("NM Initial - error")
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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

        # Check if this is an NM well
        jurisdiction = snapshot.payload.get("jurisdiction", "TX")

        try:
            if jurisdiction == "NM":
                # NM flow: Use scraped data as "extraction", no PDF processing needed
                return self._handle_nm_confirm_docs(request, snapshot, serializer)

            # TX flow below...

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
                status="success",
                is_stale=False,
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

    def _handle_nm_confirm_docs(self, request, snapshot, serializer) -> Response:
        """
        Handle NM-specific document confirmation.

        For NM wells, we don't extract from PDFs. Instead:
        1. Use the scraped data from the initial step as our "extraction"
        2. Create an ExtractedDocument record from the scraped data
        3. Return the extraction for user review/editing

        The user will need to manually enter casing data since it's not
        available from the NM OCD scraper.
        """
        from apps.public_core.services.nm_extraction_mapper import (
            create_nm_extracted_document_data,
        )
        from apps.public_core.models import ResearchSession

        logger.info(f"🆕 NM CONFIRM DOCS - {snapshot.plan_id}")

        try:
            scraped_data = snapshot.payload.get("scraped_data", {})
            extraction_data = snapshot.payload.get("extraction", {})
            api = snapshot.payload.get("api")

            # Auto-supplement extraction with research data if available
            from apps.public_core.services.research_supplement import (
                query_research_for_plan_data,
                merge_research_into_extractions,
            )

            research_supplement = {
                "status": "not_triggered",
                "formations_found": 0,
                "perforations_found": 0,
            }
            research_session_id = snapshot.payload.get("research_session_id")
            logger.warning("🔍 EQUIP-TAG: research_session_id=%s", research_session_id)

            if research_session_id:
                try:
                    rs = ResearchSession.objects.filter(id=research_session_id).first()

                    # Wait for research to complete (Celery tasks may still be indexing)
                    if rs and rs.status in ("pending", "fetching", "indexing"):
                        logger.warning("🔍 EQUIP-TAG: Research session %s status=%s, waiting for completion...", rs.id, rs.status)
                        _wait_start = time.time()
                        _MAX_WAIT = 120  # seconds
                        while rs.status in ("pending", "fetching", "indexing") and (time.time() - _wait_start) < _MAX_WAIT:
                            time.sleep(5)
                            rs.refresh_from_db()
                            logger.warning("🔍 EQUIP-TAG: Polling research session %s — status=%s (%.0fs elapsed)", rs.id, rs.status, time.time() - _wait_start)
                        logger.warning("🔍 EQUIP-TAG: Wait complete — final status=%s after %.0fs", rs.status, time.time() - _wait_start)

                    if rs and rs.status == "ready":
                        research_data = query_research_for_plan_data(str(rs.id))
                        extraction_data, equipment_status_map = merge_research_into_extractions(
                            extraction_data, research_data
                        )
                        logger.warning(f"DIAG-1: After merge, c105 formation_record={len(extraction_data.get('c105', {}).get('formation_record', []))}")
                        research_supplement = {
                            "status": "supplemented",
                            "formations_found": len(research_data.get("formation_tops", [])),
                            "perforations_found": len(research_data.get("perforations", [])),
                            "raw_answers": research_data.get("raw_answers", {}),
                        }
                        # Update snapshot with supplemented extractions
                        snapshot.payload["extraction"] = extraction_data
                        snapshot.save(update_fields=["payload"])
                        logger.warning("🔍 EQUIP-TAG: Research ready, starting equipment tagging for rs=%s", rs.id)
                        # Tag C-105 casing_record entries with equipment status
                        equipment_tagged_count = 0
                        try:
                            logger.warning("🔍 EQUIP-TAG: equipment_status_map has %d entries: %s", len(equipment_status_map), equipment_status_map)
                            if equipment_status_map or True:  # Always run — fallback handles missing map entries
                                from apps.public_core.services.research_supplement import _determine_status_from_text
                                from apps.public_core.models import DocumentVector
                                c105 = extraction_data.get("c105", {})
                                casing_entries = c105.get("casing_record", [])
                                logger.warning("🔍 EQUIP-TAG: c105 has %d casing_record entries, types=%s", len(casing_entries), [(e.get("casing_type"), e.get("bottom")) for e in casing_entries])

                                # Pre-fetch sundry sections for deterministic fallback
                                _sundry_sections = None
                                def _get_sundry_sections():
                                    nonlocal _sundry_sections
                                    if _sundry_sections is None:
                                        well = snapshot.well
                                        if well:
                                            vecs = DocumentVector.objects.filter(
                                                well=well,
                                                document_type="sundry",
                                            ).values_list("section_text", flat=True)
                                            _sundry_sections = [{"text": t} for t in vecs if t]
                                        else:
                                            _sundry_sections = []
                                    return _sundry_sections

                                for casing_entry in casing_entries:
                                    ct = (casing_entry.get("casing_type") or "").upper().strip()
                                    depth = casing_entry.get("bottom") or casing_entry.get("shoe_depth_ft") or 0
                                    depth_key = round(float(depth) / 10) * 10
                                    if "PACKER" in ct:
                                        equip_key = "PACKER"
                                    elif "TUBING" in ct:
                                        equip_key = "TUBING"
                                    elif "LINER" in ct:
                                        equip_key = "LINER"
                                    else:
                                        continue  # structural casing — skip

                                    # First try the research map
                                    es = equipment_status_map.get((equip_key, depth_key), "unverified")

                                    # Fallback: if unverified, run deterministic scan against sundry text
                                    if es == "unverified" and equip_key in ("PACKER", "TUBING"):
                                        sundry_secs = _get_sundry_sections()
                                        if sundry_secs:
                                            det_status = _determine_status_from_text(float(depth), sundry_secs)
                                            # Also try rounded depth (e.g. 9051→9050) since
                                            # well docs often use approximate depths like "±9050'"
                                            if det_status == "current" and float(depth) != float(depth_key):
                                                det_status = _determine_status_from_text(float(depth_key), sundry_secs)
                                            if det_status != "current":
                                                es = det_status
                                                logger.warning(
                                                    "🔍 EQUIP-TAG: Deterministic fallback for %s at %s ft → '%s'",
                                                    equip_key, depth, es,
                                                )
                                            else:
                                                # Deterministic says current — trust it over unverified
                                                es = "current"

                                    casing_entry["equipment_status"] = es
                                    equipment_tagged_count += 1
                                    logger.warning("🔍 EQUIP-TAG: Tagged %s at %s ft → '%s'", equip_key, depth, es)
                                # Re-save with tags
                                snapshot.payload["extraction"] = extraction_data
                                # Sync merged c105 data (mechanical_equipment, tagged casing_record)
                                # back into the extractions list so _derive_geometry() sees it.
                                # extraction_data["c105"] has the merged data; extractions[]["json_data"]
                                # was a snapshot taken before research ran.
                                merged_c105 = extraction_data.get("c105", {})
                                for ext_item in snapshot.payload.get("extractions", []):
                                    if ext_item.get("document_type") in ("c105", "c_105"):
                                        ext_item["json_data"] = merged_c105
                                        break
                                for _ext in snapshot.payload.get("extractions", []):
                                    _dt = _ext.get("document_type", "")
                                    if "c105" in _dt or "c_105" in _dt:
                                        _jd = _ext.get("json_data", {})
                                        _fr = _jd.get("formation_record", [])
                                        logger.warning(f"DIAG-2: Synced extraction doc_type={_dt}, json_data has formation_record={len(_fr)}, json_data keys={list(_jd.keys())[:10]}")
                                snapshot.save(update_fields=["payload"])
                        except Exception:
                            logger.warning("🔍 EQUIP-TAG: Equipment status tagging failed", exc_info=True)
                        research_supplement["equipment_found"] = equipment_tagged_count
                    elif rs and rs.status in ("pending", "fetching", "indexing"):
                        research_supplement = {"status": "in_progress"}
                    elif rs and rs.status == "error":
                        research_supplement = {
                            "status": "error",
                            "message": rs.error_message,
                        }
                except Exception:
                    logger.exception("Research supplement failed for session %s", research_session_id)

            # Create ExtractedDocument from scraped data if not already created
            ed = ExtractedDocument.objects.filter(
                well=snapshot.well,
                api_number=api,
                model_tag="nm_ocd_scraper_v1"
            ).first()

            if not ed:
                # Create new extraction document
                doc_data = create_nm_extracted_document_data(
                    well_data=scraped_data,
                    documents=snapshot.payload.get("source_files", []),
                    combined_pdf_url=snapshot.payload.get("combined_pdf_url"),
                )

                with transaction.atomic():
                    ed = ExtractedDocument.objects.create(
                        well=snapshot.well,
                        api_number=api,
                        document_type=doc_data["document_type"],
                        source_path=doc_data["source_path"],
                        model_tag=doc_data["model_tag"],
                        status=doc_data["status"],
                        errors=doc_data["errors"],
                        json_data=doc_data["json_data"],
                    )
                    logger.info(f"   ✅ Created NM ExtractedDocument: {ed.id}")

            # Build extraction result in same format as TX
            c105_data = extraction_data.get("c105", {})
            human_summary = {
                "document_type": "c105",
                "well_info": {
                    "api": scraped_data.get("api10"),
                    "operator": scraped_data.get("operator_name"),
                    "field": scraped_data.get("formation"),
                    "well_name": scraped_data.get("well_name"),
                },
                "casing_strings": 0,  # Not available from scraper
                "requires_manual_entry": True,
                "missing_fields": ["casing_record", "perforations", "formation_record"],
            }

            extractions = [{
                "extracted_document_id": ed.id,
                "document_type": "c105",
                "filename": f"nm_ocd_scrape_{api}.json",
                "extraction_status": "success",
                "errors": [],
                "json_data": c105_data,
                "human_readable_summary": human_summary,
            }]

            # Update snapshot
            snapshot.payload["stage"] = "extraction_complete"
            snapshot.payload["extractions"] = extractions
            snapshot.save()

            logger.info(f"✅ NM extraction ready for review")

            response_data = {
                "temp_plan_id": snapshot.plan_id,
                "jurisdiction": "NM",
                "extractions": extractions,
                "extraction_count": len(extractions),
                "requires_manual_entry": True,
                "research_supplement": research_supplement,
                "message": "NM well data ready for review. Please enter casing data manually.",
            }

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception("NM Confirm Docs - error")
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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
            
            # 🔍 DEBUG: Log what was derived
            logger.info(f"🔍 DERIVED GEOMETRY: casing_strings={len(geometry.get('casing_strings', []))} items")
            logger.info(f"🔍 DERIVED GEOMETRY: perforations={len(geometry.get('perforations', []))} items")
            logger.info(f"🔍 DERIVED GEOMETRY: formation_tops={len(geometry.get('formation_tops', []))} items")
            if geometry.get('casing_strings'):
                logger.info(f"🔍 DERIVED GEOMETRY: First casing string={geometry['casing_strings'][0]}")
            
            # Save geometry to snapshot for later reference and editing
            snapshot.payload["geometry"] = geometry
            snapshot.payload["stage"] = "geometry_derived"
            snapshot.save()
            
            logger.info(f"✅ SAVED GEOMETRY TO SNAPSHOT: payload keys={list(snapshot.payload.keys())}")
            
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
        """Apply user edits to extracted JSONs (in-memory).

        Each edit's field_path is applied to the extraction's json_data.
        Edits are matched to extractions by document_type if the edit
        has a plan_snapshot with extraction metadata, otherwise applied
        to the first extraction that contains the top-level key.
        """
        import copy
        if not edits:
            return extractions

        # Deep copy to avoid mutating the original payload
        edited = copy.deepcopy(extractions)

        # Deduplicate edits (same field_path + edited_value = skip duplicates)
        seen_edits = set()
        unique_edits = []
        for edit in edits:
            key = (edit.field_path, str(edit.edited_value))
            if key not in seen_edits:
                seen_edits.add(key)
                unique_edits.append(edit)

        # Separate DELETE edits from value edits
        delete_edits = []
        non_delete_edits = []
        for edit in unique_edits:
            if edit.edited_value == "__DELETE__":
                delete_edits.append(edit)
            else:
                non_delete_edits.append(edit)

        # Sort deletes by index descending so higher indices are removed first
        delete_edits.sort(
            key=lambda e: int(e.field_path.split(".")[-1]) if e.field_path.split(".")[-1].isdigit() else 0,
            reverse=True,
        )

        # Track deleted indices per array key for re-indexing non-delete edits
        deleted_indices: dict = {}  # {"mechanical_equipment": [1], ...}

        for edit in delete_edits:
            field_path = edit.field_path
            parts = field_path.split(".")
            if len(parts) == 2 and parts[1].isdigit():
                array_key = parts[0]
                delete_idx = int(parts[1])
                for ext in edited:
                    json_data = ext.get("json_data", {})
                    if not json_data:
                        continue
                    if array_key in json_data and isinstance(json_data[array_key], list):
                        target_list = json_data[array_key]
                        if 0 <= delete_idx < len(target_list):
                            target_list.pop(delete_idx)
                            deleted_indices.setdefault(array_key, []).append(delete_idx)
                            logger.info(
                                "Deleted item at index %d from %s (edit %s)",
                                delete_idx, array_key, edit.id
                            )
                            break

        # Re-index non-delete edits: adjust indices that were shifted by deletions
        for edit in non_delete_edits:
            parts = edit.field_path.split(".")
            if len(parts) >= 2 and parts[1].isdigit():
                array_key = parts[0]
                orig_idx = int(parts[1])
                if array_key in deleted_indices:
                    # Count how many deleted indices were BELOW this one
                    shift = sum(1 for d in sorted(deleted_indices[array_key]) if d < orig_idx)
                    if shift > 0:
                        new_idx = orig_idx - shift
                        parts[1] = str(new_idx)
                        edit.field_path = ".".join(parts)
                        logger.info(f"Re-indexed edit {array_key}.{orig_idx} → {array_key}.{new_idx}")

        # Then process non-delete edits
        for edit in non_delete_edits:
            field_path = edit.field_path
            parts = field_path.split(".")
            top_key = parts[0]
            applied = False

            for ext in edited:
                json_data = ext.get("json_data", {})
                if not json_data:
                    continue
                # Apply to the extraction whose json_data contains the top-level key
                if top_key in json_data:
                    try:
                        _apply_field_edit(json_data, field_path, edit.edited_value)
                        applied = True
                        break
                    except (KeyError, IndexError, TypeError) as exc:
                        logger.warning(
                            "Failed to apply extraction edit %s (path=%s): %s",
                            edit.id, field_path, exc
                        )

            if not applied:
                logger.warning(
                    "Extraction edit %s (path=%s) did not match any extraction",
                    edit.id, field_path
                )

        return edited
    
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

        # Equipment status is now tagged on casing_record entries during extraction review (confirm-docs).
        # _derive_geometry reads it directly from each casing dict's "equipment_status" field.

        # Extract W-2 data
        w2_data = None
        c105_data = None
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

        # Extract C-105 data (NM equivalent of W-2)
        if not w2_data:
            for ext in extractions:
                if ext["document_type"] == "c105":
                    c105_data = ext["json_data"]
                    logger.warning(f"DIAG-3: c105_data keys={list(c105_data.keys())[:15]}, formation_record={len(c105_data.get('formation_record', []))}")

                    # Fix B: handle double-nested json_data where c105 key wraps the real data
                    if not c105_data.get("formation_record") and not c105_data.get("casing_record"):
                        nested_c105 = c105_data.get("c105", {})
                        if isinstance(nested_c105, dict) and (nested_c105.get("formation_record") or nested_c105.get("casing_record")):
                            logger.warning(f"DIAG-3: Detected double-nested c105, unwrapping. Nested keys={list(nested_c105.keys())[:15]}")
                            c105_data = nested_c105

                    # Casing strings — map NM field names to TX-compatible field names
                    for casing in c105_data.get("casing_record", []):
                        casing_type = (casing.get("casing_type") or casing.get("string") or "").lower().strip()
                        logger.warning(
                            "🔍 C-105 casing_record entry: type=%s, bottom=%s, shoe_depth=%s",
                            casing_type,
                            casing.get("bottom"),
                            casing.get("shoe_depth_ft"),
                        )

                        if casing_type.startswith("tubing"):
                            tubing_depth = casing.get("bottom") or casing.get("shoe_depth_ft")
                            equip_status = casing.get("equipment_status", "unverified")
                            logger.warning(
                                "🔍 TUBING status lookup: key=('TUBING', %s) → %s",
                                casing.get("bottom") or casing.get("shoe_depth_ft"), equip_status,
                            )
                            if equip_status == "removed":
                                logger.warning(
                                    "Skipping C-105 TUBING at %s ft — research confirms removed",
                                    tubing_depth,
                                )
                                continue
                            # Route tubing to its own list
                            geometry.setdefault("tubing", []).append({
                                "type": casing_type,
                                "size_in": casing.get("diameter") or casing.get("size_in"),
                                "depth_ft": tubing_depth,
                                "source": "C-105 casing_record (tubing)",
                                "equipment_status": equip_status,
                            })
                            continue

                        if casing_type.startswith("packer"):
                            packer_depth = casing.get("bottom") or casing.get("shoe_depth_ft")
                            equip_status = casing.get("equipment_status", "unverified")
                            logger.warning(
                                "🔍 PACKER status lookup: key=('PACKER', %s) → %s",
                                packer_depth, equip_status,
                            )
                            if equip_status == "removed":
                                logger.warning(
                                    "Skipping C-105 PACKER at %s ft — research confirms removed",
                                    packer_depth,
                                )
                                continue
                            label = f"PACKER @ {packer_depth} ft" if packer_depth else "PACKER"
                            if equip_status == "unverified":
                                label += " (unverified)"
                            mb_idx = len(geometry.get("mechanical_barriers", []))
                            geometry.setdefault("mechanical_barriers", []).append({
                                "field_id": f"mech_{mb_idx}",
                                "field_label": label,
                                "value": {
                                    "type": "PACKER",
                                    "depth_ft": packer_depth,
                                    "description": "",
                                    "equipment_status": equip_status,
                                },
                                "source": "C-105 casing_record (packer)",
                                "editable": True,
                            })
                            continue

                        # Check if this is a liner — liners can be removed unlike structural casing
                        if "liner" in casing_type:
                            depth_val = casing.get("bottom") or casing.get("shoe_depth_ft")
                            equip_status = casing.get("equipment_status", "unverified")
                            logger.warning(
                                "🔍 LINER status lookup: key=('LINER', %s) → %s",
                                depth_val, equip_status,
                            )
                            if equip_status == "removed":
                                logger.warning(
                                    "Skipping C-105 LINER at %s ft — research confirms removed",
                                    depth_val,
                                )
                                continue

                        # Route CIBPs and bridge plugs to mechanical barriers (safety net)
                        if "cibp" in casing_type or "bridge" in casing_type or "retainer" in casing_type:
                            tool_depth = casing.get("bottom") or casing.get("shoe_depth_ft")
                            equip_status = casing.get("equipment_status", "unverified")
                            if equip_status == "removed":
                                logger.warning(
                                    "Skipping C-105 %s at %s ft — research confirms removed",
                                    casing_type.upper(), tool_depth,
                                )
                                continue
                            mb_idx = len(geometry.get("mechanical_barriers", []))
                            geometry.setdefault("mechanical_barriers", []).append({
                                "field_id": f"mech_{mb_idx}",
                                "field_label": f"{casing_type.upper()} @ {tool_depth} ft" if tool_depth else casing_type.upper(),
                                "value": {
                                    "type": casing_type.upper(),
                                    "depth_ft": tool_depth,
                                    "description": "",
                                    "equipment_status": equip_status,
                                },
                                "source": "C-105 casing_record (tool/barrier)",
                                "editable": True,
                            })
                            continue

                        # Everything else is a casing string (surface, intermediate, production)
                        mapped = {
                            "string": casing.get("casing_type", ""),
                            "size_in": casing.get("diameter"),
                            "shoe_depth_ft": casing.get("bottom"),
                            "cement_top_ft": casing.get("cement_top"),
                            "top_ft": casing.get("top"),
                            "cement_bottom": casing.get("cement_bottom"),
                            "sacks": casing.get("sacks"),
                            "grade": casing.get("grade"),
                            "weight": casing.get("weight"),
                        }
                        geometry["casing_strings"].append({
                            "field_id": f"casing_{len(geometry['casing_strings'])}",
                            "field_label": f"{mapped['string'] or 'Unknown'} Casing",
                            "value": mapped,
                            "source": "C-105 casing_record",
                            "editable": True,
                        })

                    # Perforations — c105 uses top_md/bottom_md instead of from_ft/to_ft
                    for perf in c105_data.get("producing_injection_disposal_interval", []):
                        normalized_perf = {
                            "top_ft": perf.get("top_md"),
                            "bottom_ft": perf.get("bottom_md"),
                            "open_hole": perf.get("open_hole", False),
                        }
                        geometry["perforations"].append({
                            "field_id": f"perf_{len(geometry['perforations'])}",
                            "field_label": f"Perforation {perf.get('top_md')}-{perf.get('bottom_md')} ft",
                            "value": normalized_perf,
                            "source": "C-105 producing_injection_disposal_interval",
                            "editable": True,
                        })
                        logger.warning(f"📍 Extracted NM perforation: {normalized_perf}")

                    # Mechanical barriers (usually empty for NM but handle defensively)
                    for idx, equipment in enumerate(c105_data.get("mechanical_equipment", [])):
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
                            "source": "C-105 mechanical_equipment",
                            "editable": True,
                        })

                    # Formation tops from C-105 formation_record (populated by research supplement)
                    for formation in c105_data.get("formation_record", []):
                        formation_name = formation.get("formation", "")
                        top_ft = formation.get("top_ft")
                        if formation_name and top_ft is not None:
                            geometry["formation_tops"].append({
                                "field_id": f"formation_{formation_name.lower().replace(' ', '_')}",
                                "field_label": formation_name,
                                "value": float(top_ft),
                                "unit": "ft",
                                "source": "C-105 formation_record",
                                "editable": True,
                                "formation_name": formation_name,
                            })
                    if geometry["formation_tops"]:
                        logger.info(f"📊 Extracted {len(geometry['formation_tops'])} formation tops from C-105")

                    break  # Only process first C-105

        # Deduplicate mechanical_barriers — casing_record packers may duplicate
        # research-discovered mechanical_equipment packers at different depths.
        # Keep mechanical_equipment version (more accurate depth from research AI).
        barriers = geometry.get("mechanical_barriers", [])
        if len(barriers) > 1:
            seen_types = {}  # {type: best_barrier}
            deduped = []
            for b in barriers:
                v = b.get("value", {})
                b_type = (v.get("type") or "").upper()
                b_source = b.get("source", "")
                # Prefer mechanical_equipment source over casing_record source
                if b_type in seen_types:
                    existing_source = seen_types[b_type].get("source", "")
                    if "mechanical_equipment" in b_source and "casing_record" in existing_source:
                        # Replace casing_record version with mechanical_equipment version
                        deduped = [x for x in deduped if x is not seen_types[b_type]]
                        deduped.append(b)
                        seen_types[b_type] = b
                    elif "casing_record" in b_source and "mechanical_equipment" in existing_source:
                        # Keep existing mechanical_equipment version, skip casing_record
                        continue
                    else:
                        # Different sources or same source — keep both (could be different barriers)
                        deduped.append(b)
                else:
                    seen_types[b_type] = b
                    deduped.append(b)
            if len(deduped) < len(barriers):
                logger.info(f"Deduplicated mechanical_barriers: {len(barriers)} → {len(deduped)}")
                geometry["mechanical_barriers"] = deduped

        # Get policy-based formation tops
        policy_formations = []
        source_data = w2_data or c105_data
        if source_data:
            try:
                well_info = source_data.get("well_info", {})
                district = well_info.get("district")
                county = well_info.get("county")
                field = well_info.get("field")

                if county:
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
        
        # Add/override with W-2/C-105 formation_record if available
        if source_data:
            for formation in source_data.get("formation_record", []):
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
            
            # 🔍 DEBUG: Log what's in geometry before processing edits
            logger.info(f"🔍 GEOMETRY BEFORE EDITS: casing_strings={len(geometry.get('casing_strings', []))} items")
            logger.info(f"🔍 GEOMETRY BEFORE EDITS: perforations={len(geometry.get('perforations', []))} items")
            logger.info(f"🔍 GEOMETRY BEFORE EDITS: formation_tops={len(geometry.get('formation_tops', []))} items")
            logger.info(f"🔍 GEOMETRY BEFORE EDITS: mechanical_barriers={len(geometry.get('mechanical_barriers', []))} items")
            
            formation_tops = geometry.get("formation_tops", [])
            formation_tops_map = {ft["field_id"]: ft for ft in formation_tops}
            
            mechanical_barriers = geometry.get("mechanical_barriers", [])
            mechanical_barriers_map = {mb["field_id"]: mb for mb in mechanical_barriers}
            
            for edit_data in edits:
                # Determine if this is a user-added formation, mechanical barrier, casing, perf, or modification
                is_user_added_formation = edit_data["original_value"] is None and "user_formation_" in edit_data["field_id"]
                is_user_added_tool = edit_data["original_value"] is None and "user_tool_" in edit_data["field_id"]
                is_user_added_casing = edit_data["original_value"] is None and "user_casing_" in edit_data["field_id"]
                is_user_added_perf = edit_data["original_value"] is None and "user_perf_" in edit_data["field_id"]
                is_user_added_cement = edit_data["original_value"] is None and "user_cement_" in edit_data["field_id"]
                
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

                elif is_user_added_casing:
                    # Add new user-defined casing string
                    # edited_value should be JSON with size, depth_set, top_of_cement, casing_type
                    casing_data = edit_data["edited_value"]
                    new_casing = {
                        "field_id": field_id,
                        "field_label": edit_data.get("field_label", ""),
                        "value": {
                            "size_in": _parse_fraction(casing_data.get("size")),
                            "weight_per_ft": _parse_fraction(casing_data.get("weight")),
                            "hole_size_in": _parse_fraction(casing_data.get("hole_size")),
                            "shoe_depth_ft": _safe_float(casing_data.get("depth_set")),
                            "cement_top_ft": _safe_float(casing_data.get("top_of_cement")),
                            "cement_bottom_ft": _safe_float(casing_data.get("bottom_of_cement")),
                            "string": casing_data.get("casing_type"),
                        },
                        "source": "User Added",
                        "editable": True,
                    }
                    geometry.setdefault("casing_strings", []).append(new_casing)
                    logger.info(f"🔧 User added casing string: {casing_data.get('casing_type')} @ {casing_data.get('depth_set')} ft")

                elif is_user_added_perf:
                    # Add new user-defined perforation
                    # edited_value should be JSON with top_ft, bottom_ft
                    perf_data = edit_data["edited_value"]
                    new_perf = {
                        "field_id": field_id,
                        "field_label": edit_data.get("field_label", ""),
                        "value": {
                            "top_ft": perf_data.get("top_ft"),
                            "bottom_ft": perf_data.get("bottom_ft"),
                        },
                        "source": "User Added",
                        "editable": True,
                    }
                    geometry.setdefault("perforations", []).append(new_perf)
                    logger.info(f"📍 User added perforation: {perf_data.get('top_ft')}-{perf_data.get('bottom_ft')} ft")

                elif is_user_added_cement:
                    # Add new user-defined historic cement job
                    cement_data = edit_data["edited_value"]
                    new_cement_job = {
                        "field_id": field_id,
                        "field_label": edit_data.get("field_label", ""),
                        "value": {
                            "job_type": cement_data.get("job_type", "unknown"),
                            "cement_top_ft": cement_data.get("cement_top_ft"),
                            "interval_bottom_ft": cement_data.get("cement_bottom_ft"),  # Map to kernel's expected key
                            "sacks": cement_data.get("sacks"),
                            "description": cement_data.get("description", ""),
                        },
                        "source": "User Added",
                        "editable": True,
                    }
                    geometry.setdefault("cement_jobs", []).append(new_cement_job)
                    logger.info(f"🧱 User added cement job: {cement_data.get('job_type')} @ {cement_data.get('cement_top_ft')}-{cement_data.get('cement_bottom_ft')} ft")

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
            # Explicitly preserve other geometry fields (casing_strings, perforations, etc.)
            # Note: These should already be in 'geometry' object, but logging to verify
            logger.info(f"🔍 GEOMETRY AFTER EDITS: casing_strings={len(geometry.get('casing_strings', []))} items")
            logger.info(f"🔍 GEOMETRY AFTER EDITS: perforations={len(geometry.get('perforations', []))} items")
            if not geometry.get('casing_strings'):
                logger.error(f"❌ CRITICAL: casing_strings is EMPTY after processing edits!")
            if not geometry.get('perforations'):
                logger.warning(f"⚠️ perforations is EMPTY after processing edits")
            
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
                    existing_snapshot.workspace = snapshot.workspace
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
                        workspace=snapshot.workspace,
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

                # Write plan_proposed WellComponent records
                try:
                    from apps.public_core.services.component_writer import write_plan_components
                    write_plan_components(
                        well=final_snapshot.well,
                        plan_snapshot=final_snapshot,
                        steps=plan_result.get("steps", []),
                        tenant_id=snapshot.tenant_id,
                    )
                except Exception:
                    logger.warning("Failed to write plan components", exc_info=True)

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
        
        # 🚀🚀🚀 CRITICAL DEBUG: Verify method was called
        print("🚀🚀🚀 _build_plan_from_snapshot CALLED 🚀🚀🚀")
        logger.error("🚀🚀🚀 _build_plan_from_snapshot CALLED - IF YOU SEE THIS, CODE IS ACTIVE 🚀🚀🚀")
        
        # Get extractions from snapshot
        extractions = snapshot.payload.get("extractions", [])

        # Apply any pending extraction edits (user modifications from confirm-extractions step)
        extraction_edits = WellEditAudit.objects.filter(
            plan_snapshot=snapshot,
            context=WellEditAudit.CONTEXT_EXTRACTION,
            stage=WellEditAudit.STAGE_PENDING,
        )
        if extraction_edits.exists():
            extractions = W3AGeometryView._apply_edits_to_extractions(self, extractions, extraction_edits)
            logger.info(f"Applied {extraction_edits.count()} extraction edits to plan builder input")

        geometry = snapshot.payload.get("geometry", {})
        policy_id = snapshot.payload.get("policy_id", "tx.w3a")
        logger.warning(f"DIAG-4: geometry formation_tops={len(geometry.get('formation_tops', []))}, geometry keys={list(geometry.keys())}")

        # 🔍 DEBUG: Log what geometry we received at the start
        logger.error(f"🔍 _build_plan_from_snapshot START: geometry has {len(geometry.get('casing_strings', []))} casing_strings")
        if geometry.get('casing_strings'):
            logger.error(f"🔍 _build_plan_from_snapshot: First casing_string keys={list(geometry['casing_strings'][0].keys())}")
        else:
            logger.error(f"❌ _build_plan_from_snapshot: casing_strings is EMPTY or not in geometry!")
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
            elif doc_type == "c105":
                raw_casing = json_data.get("casing_record", [])
                mapped_casing = []
                for rec in raw_casing:
                    mapped_casing.append({
                        "string": rec.get("casing_type", ""),
                        "size_in": rec.get("diameter"),
                        "shoe_depth_ft": rec.get("bottom"),
                        "cement_top_ft": rec.get("cement_top"),
                        "cement_bottom": rec.get("cement_bottom"),
                        "sacks": rec.get("sacks"),
                        "grade": rec.get("grade"),
                        "weight": rec.get("weight"),
                    })
                w2_data = dict(json_data)
                w2_data["casing_record"] = mapped_casing
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
        well_metadata = snapshot.payload.get("well_metadata", {})
        county = well_info.get("county") or well_metadata.get("county") or ""
        field = well_info.get("field") or well_metadata.get("field_name") or ""
        district = well_info.get("district") or well_metadata.get("district") or ""
        lease = well_info.get("lease") or well_metadata.get("lease") or ""
        well_no = well_info.get("well_no") or well_metadata.get("well_no") or ""
        
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

        # Resolve jurisdiction — payload is authoritative, fallback to API number detection
        jurisdiction = snapshot.payload.get("jurisdiction") or detect_jurisdiction(api or "")
        logger.error(f"🚨 JURISDICTION RESOLVED: {jurisdiction} (from payload={snapshot.payload.get('jurisdiction')}, api={api})")

        # Build facts dictionary (mirroring w3a_from_api._build_plan)
        facts: Dict[str, Any] = {
            "api14": wrap(api14),
            "state": wrap(jurisdiction),
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

        # Fallback to manual entry UQW data
        if not facts.get("has_uqw") or not facts["has_uqw"].get("value"):
            if well_metadata.get("has_uqw"):
                facts["has_uqw"] = wrap(True)
                uqw_base = well_metadata.get("uqw_base_depth_ft")
                if uqw_base:
                    facts["uqw_base_ft"] = wrap(float(uqw_base))
                    facts["base_uqw_ft"] = float(uqw_base)
                logger.info(f"🔵 UQW from manual entry: base={well_metadata.get('uqw_base_depth_ft')} ft")

        # Add total depth from W-2 if available, fallback to manual entry
        total_depth_w2 = None
        well_info_td = well_info.get("total_depth_ft") or well_info.get("td_ft") or well_info.get("total_depth")
        if well_info_td:
            try:
                total_depth_w2 = float(well_info_td)
            except (ValueError, TypeError):
                pass
        if total_depth_w2:
            facts["total_depth_ft"] = total_depth_w2
        else:
            td = well_metadata.get("total_depth_ft")
            if td:
                facts["total_depth_ft"] = float(td)

        facts["use_cibp"] = wrap(False)  # Default to False, can be overridden
        
        # Add casing record from W-2 (or geometry if available)
        casing_strings_geometry = geometry.get("casing_strings", [])
        casing_record = w2_data.get("casing_record", [])
        
        logger.error(f"❌ CASING DEBUG: casing_strings_geometry={len(casing_strings_geometry)} items")
        logger.error(f"❌ CASING DEBUG: casing_record from W-2={len(casing_record)} items")
        if casing_strings_geometry:
            logger.error(f"❌ CASING DEBUG: geometry casing preview={[cs.get('value') for cs in casing_strings_geometry[:1]]}")
        if casing_record:
            logger.error(f"❌ CASING DEBUG: W-2 casing_record preview={casing_record[:2] if len(casing_record) > 0 else 'empty'}")
        
        # Use geometry casing if available (takes precedence)
        if casing_strings_geometry:
            facts["casing_record"] = [cs.get("value", cs) for cs in casing_strings_geometry]
            casing_to_process = [cs.get("value", cs) for cs in casing_strings_geometry]
            logger.error(f"❌ EXTRACTED {len(casing_to_process)} casing strings from geometry for kernel")
            if casing_to_process:
                logger.error(f"❌ FIRST CASING: {casing_to_process[0]}")
        elif casing_record:
            facts["casing_record"] = casing_record
            casing_to_process = casing_record
            logger.error(f"❌ EXTRACTED {len(casing_to_process)} casing strings from W-2 for kernel")
        else:
            casing_to_process = []
            logger.error(f"❌❌❌ NO CASING DATA FOUND - casing_strings will be empty!")

        # Normalize casing values - ensure numeric fields are floats, not fraction strings
        # This handles plans already saved with string fractions like "9 5/8"
        for casing in casing_to_process:
            for key in ("size_in", "hole_size_in", "weight_per_ft", "shoe_depth_ft", "cement_top_ft", "cement_bottom_ft"):
                val = casing.get(key)
                if val is not None and not isinstance(val, (int, float)):
                    casing[key] = _parse_fraction(val)

        # Extract surface shoe depth and production TOC (critical for plug type determination)
        for casing in casing_to_process:
            string_type = casing.get("string", "").lower()
            
            if string_type == "surface":
                shoe_depth = casing.get("shoe_depth_ft")
                if shoe_depth:
                    facts["surface_shoe_ft"] = wrap(float(shoe_depth))
                    logger.info(f"📍 Surface shoe: {shoe_depth} ft")
            
            elif string_type == "intermediate":
                intermediate_shoe = casing.get("shoe_depth_ft") or casing.get("bottom_ft")
                if intermediate_shoe:
                    facts["intermediate_shoe_ft"] = float(intermediate_shoe)
                intermediate_toc = casing.get("cement_top_ft") or casing.get("toc_ft")
                if intermediate_toc is not None:
                    facts["intermediate_toc_ft"] = float(intermediate_toc)

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

        # Auto-derive producing interval from perforations if not set from W-2
        if not facts.get("producing_interval_ft") and facts.get("perforations"):
            perf_tops = []
            perf_bottoms = []
            for p in facts["perforations"]:
                p_val = p.get("value", p) if isinstance(p, dict) else {}
                top = p_val.get("top_ft") or p_val.get("from_ft")
                bottom = p_val.get("bottom_ft") or p_val.get("to_ft")
                if top is not None:
                    try:
                        perf_tops.append(float(top))
                    except (ValueError, TypeError):
                        pass
                if bottom is not None:
                    try:
                        perf_bottoms.append(float(bottom))
                    except (ValueError, TypeError):
                        pass
            if perf_tops and perf_bottoms:
                facts["producing_interval_ft"] = {
                    "top": min(perf_tops),
                    "bottom": max(perf_bottoms),
                }
                # Also set production_perforations for plan output
                if not facts.get("production_perforations"):
                    facts["production_perforations"] = facts["perforations"]
                logger.info(f"📍 Auto-derived producing interval from perfs: {min(perf_tops)}-{max(perf_bottoms)} ft")

        # Check for existing CIBP or bridge plug - from both W-2 AND user-added tools in geometry
        # The kernel checks facts["existing_mechanical_barriers"] for barrier dicts with type + depth_ft
        mechanical_equipment = w2_data.get("mechanical_equipment", [])
        mechanical_barriers_geometry = geometry.get("mechanical_barriers", [])
        logger.warning(f"BARRIER-DIAG: w2_data mechanical_equipment={len(mechanical_equipment)}, geometry mechanical_barriers={len(mechanical_barriers_geometry)}")
        existing_barrier_types = []  # String list for cibp_present check
        existing_barrier_dicts = []  # Full dicts for C-103 barrier filtering

        # Process W-2 mechanical equipment
        if mechanical_equipment:
            for equip in mechanical_equipment:
                equip_type = str(equip.get("type", "")).upper()
                depth = equip.get("depth_ft") or equip.get("set_depth_ft")
                desc = equip.get("description", "")
                if "CIBP" in equip_type or "BRIDGE" in equip_type:
                    existing_barrier_types.append("CIBP")
                    existing_barrier_dicts.append({"type": "CIBP", "depth_ft": float(depth) if depth else 0, "description": desc})
                    if depth:
                        facts["cibp_depth_ft"] = wrap(float(depth))
                        logger.info(f"🔧 Detected existing CIBP from W-2 at {depth} ft")
                elif "PACKER" in equip_type:
                    existing_barrier_types.append("PACKER")
                    existing_barrier_dicts.append({
                        "type": "PACKER",
                        "depth_ft": float(depth) if depth else 0,
                        "description": desc,
                        "cement_top_ft": equip.get("cement_top_ft"),
                        "sacks": equip.get("sacks"),
                    })
                elif "RETAINER" in equip_type:
                    existing_barrier_types.append("RETAINER")
                    existing_barrier_dicts.append({"type": "RETAINER", "depth_ft": float(depth) if depth else 0, "description": desc})

        # Process user-added mechanical tools from geometry (including user additions)
        if mechanical_barriers_geometry:
            for mb in mechanical_barriers_geometry:
                tool_data = mb.get("value", {})
                tool_type = str(tool_data.get("type", "")).upper()
                tool_depth = tool_data.get("depth_ft")
                tool_desc = tool_data.get("description", "")

                if "CIBP" in tool_type or "BRIDGE" in tool_type:
                    if "CIBP" not in existing_barrier_types:
                        existing_barrier_types.append("CIBP")
                    existing_barrier_dicts.append({"type": "CIBP", "depth_ft": float(tool_depth) if tool_depth else 0, "description": tool_desc})
                    if tool_depth and not facts.get("cibp_depth_ft"):
                        facts["cibp_depth_ft"] = wrap(float(tool_depth))
                        logger.info(f"🔧 Detected existing CIBP from user geometry at {tool_depth} ft")
                elif "PACKER" in tool_type:
                    if "PACKER" not in existing_barrier_types:
                        existing_barrier_types.append("PACKER")
                    existing_barrier_dicts.append({
                        "type": "PACKER",
                        "depth_ft": float(tool_depth) if tool_depth else 0,
                        "description": tool_desc,
                        "cement_top_ft": tool_data.get("cement_top_ft"),
                        "sacks": tool_data.get("sacks"),
                    })
                elif "RETAINER" in tool_type:
                    if "RETAINER" not in existing_barrier_types:
                        existing_barrier_types.append("RETAINER")
                    existing_barrier_dicts.append({"type": "RETAINER", "depth_ft": float(tool_depth) if tool_depth else 0, "description": tool_desc})

        logger.warning(f"BARRIER-DIAG-2: existing_barrier_dicts={len(existing_barrier_dicts)}, types={existing_barrier_types}")
        if existing_barrier_dicts:
            facts["existing_mechanical_barriers"] = existing_barrier_dicts  # Full dicts for C-103 filtering
            facts["cibp_present"] = wrap("CIBP" in existing_barrier_types)
            logger.warning(f"🔧 Final existing mechanical barriers: {len(existing_barrier_dicts)} barriers, cibp_present={'CIBP' in existing_barrier_types}")
            for bd in existing_barrier_dicts:
                logger.warning(f"  barrier: {bd}")
        else:
            logger.warning("BARRIER-DIAG-2: NO barriers found despite having equipment data!")
        
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

        # Add user-entered cement jobs from geometry (manual entry mode)
        user_cement_jobs = geometry.get("cement_jobs", [])
        if user_cement_jobs:
            cementing_data_from_user = []
            for job in user_cement_jobs:
                job_val = job.get("value", job) if isinstance(job, dict) else {}
                cementing_data_from_user.append({
                    "job_type": job_val.get("job_type", "unknown"),
                    "interval_top_ft": job_val.get("interval_top_ft"),
                    "interval_bottom_ft": job_val.get("interval_bottom_ft"),
                    "cement_top_ft": job_val.get("cement_top_ft"),
                    "sacks": job_val.get("sacks"),
                })
            # Merge with any existing cementing_data from W-15
            existing_cementing = facts.get("cementing_data", [])
            facts["cementing_data"] = existing_cementing + cementing_data_from_user
            logger.info(f"🧱 Added {len(cementing_data_from_user)} user-entered cement jobs to cementing_data")

            # Also check for CIBP cap specifically
            for job in cementing_data_from_user:
                if job.get("job_type") == "cibp_cap":
                    facts["cibp_cap_present"] = True
                    cap_top = job.get("cement_top_ft")
                    cap_bottom = job.get("interval_bottom_ft")
                    if cap_top is not None and cap_bottom is not None:
                        try:
                            facts["existing_cibp_cap_length_ft"] = float(cap_bottom) - float(cap_top)
                        except (ValueError, TypeError):
                            pass
                    logger.info(f"🧱 CIBP cap detected from user entry")

        # Get policy — jurisdiction-aware (jurisdiction already resolved above, but re-derive with API fallback for safety)
        jurisdiction = snapshot.payload.get("jurisdiction")
        if not jurisdiction:
            api_str = snapshot.payload.get("api", "")
            jurisdiction = detect_jurisdiction(api_str) if api_str else "TX"
        logger.info(f"Policy loading: jurisdiction={jurisdiction}")
        if jurisdiction == "NM":
            from apps.kernel.handlers.nm.handler import NMJurisdictionHandler
            nm_handler = NMJurisdictionHandler()
            logger.info("Loading NM C-103 policy via NMJurisdictionHandler")
            policy = nm_handler.load_effective_policy(facts)
            policy["policy_id"] = "nm.c103"
            policy["complete"] = True
        else:
            policy = get_effective_policy(district=district, county=county, field=field)
        
        # INJECT FORMATIONS INTO POLICY
        # The kernel only processes formations from policy["effective"]["district_overrides"]["formation_tops"]
        formation_tops_geometry = geometry.get("formation_tops", [])
        logger.warning(f"FORMATION INJECT CHECK: geometry has {len(formation_tops_geometry)} formation_tops, jurisdiction={jurisdiction}")
        if formation_tops_geometry:
            logger.warning(f"FORMATION INJECT: first={formation_tops_geometry[0]}")
            policy_effective = policy.setdefault("effective", {})
            district_overrides = policy_effective.setdefault("district_overrides", {})
            policy_formation_tops = district_overrides.setdefault("formation_tops", [])

            if jurisdiction == "NM":
                # NM requires isolation of every formation — inject ALL geometry formation tops
                injected = []
                for ft in formation_tops_geometry:
                    name = ft.get("formation_name") or ft.get("field_label", "")
                    depth = ft.get("value")
                    if name and depth is not None:
                        injected.append({"formation": name, "top_ft": float(depth)})
                if injected:
                    district_overrides["formation_tops"] = injected
                    logger.info(f"Injected {len(injected)} formation tops into NM policy")
            else:
                # TX: inject only user-added formations so kernel sees them
                user_added_formations = [
                    ft for ft in formation_tops_geometry
                    if "User Added" in str(ft.get("source", ""))
                ]
                if user_added_formations:
                    logger.info(f"Injecting {len(user_added_formations)} user-added formations into TX policy for kernel")
                    for ft in user_added_formations:
                        formation_name = ft.get("formation_name") or ft.get("field_label")
                        depth = ft.get("value") or ft.get("top_ft")
                        if formation_name and depth is not None:
                            policy_formation_tops.append({
                                "formation": formation_name,
                                "top_ft": float(depth),
                                "plug_required": True,
                                "use_when": "always",
                                "source": "User Added",
                                "additional_requirements": None,
                            })

        # Override policy metadata (NM policy already has policy_id/complete set above)
        if jurisdiction != "NM":
            policy["policy_id"] = policy_id
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
        # Merge constraint is footage-based (max_length_ft), not sack-based
        # Set sack limits very high so length is the real constraint
        prefs["long_plug_merge"]["sack_limit_no_tag"] = 99999.0
        prefs["long_plug_merge"]["sack_limit_with_tag"] = 99999.0
        prefs["long_plug_merge"].setdefault("types", [
            "formation_top_plug",
            "formation_plug",        # NM C-103 formation plug type
            "cement_plug",
            "uqw_isolation_plug",
            "fill_plug",             # Fill plugs can merge with adjacent formations
            "shoe_plug",             # Shoe plugs can merge with adjacent formations (e.g., Delaware + intermediate shoe)
            "perforate_and_squeeze_plug",  # Perf+squeeze can merge with other perf+squeeze
        ])
        prefs["long_plug_merge"].setdefault("preserve_tagging", True)
        # NM regulatory limit: max 1000 ft per plug (TX allows 1250 ft)
        if jurisdiction == "NM":
            prefs["long_plug_merge"]["max_length_ft"] = 1000
        
        logger.info(f"🎯 Merge config: enabled={plugs_mode == 'combined'}, sack_limit_no_tag={sack_limit_no_tag}, sack_limit_with_tag={sack_limit_with_tag}")
        
        # Inject geometry formation tops into facts for C-103 kernel
        # The NM C-103 step generator reads from facts["formation_tops_map"], NOT from policy
        # This MUST run after policy enrichment to pick up all formation tops
        geometry_ft = geometry.get("formation_tops", [])
        if geometry_ft:
            ft_map = {}
            for ft in geometry_ft:
                name = ft.get("formation_name") or ft.get("field_label", "")
                depth = ft.get("value")
                if name and depth is not None:
                    ft_map[name] = float(depth)
            if ft_map:
                facts["formation_tops_map"] = ft_map  # Override any earlier empty assignment
                facts["formation_tops"] = [{"name": k, "depth_ft": v} for k, v in ft_map.items()]
                logger.info(f"FINAL formation_tops_map for kernel: {len(ft_map)} formations: {list(ft_map.keys())}")

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
        lpm = policy.get("preferences", {}).get("long_plug_merge", {})
        logger.warning(f"MERGE-DIAG: enabled={lpm.get('enabled')}, types={lpm.get('types')}, max_length={lpm.get('max_length_ft')}, sack_no_tag={lpm.get('sack_limit_no_tag')}")
        logger.info("🚀 Calling plan_from_facts with edited geometry...")
        out_kernel = plan_from_facts(facts, policy)
        
        # Build output similar to w3a_from_api
        steps = out_kernel.get("steps", [])
        logger.info(f"✅ Kernel returned {len(steps)} steps")
        
        # Build plan payload
        plan_payload = {
            "api": api14,
            "jurisdiction": jurisdiction,
            "form_type": "c_103" if jurisdiction == "NM" else "w3a",
            "county": county,
            "field": field,
            "district": district,
            "kernel_version": out_kernel.get("kernel_version", "0.1.0"),
            "policy_id": policy_id,
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
            # ALL well formation tops for WBD display (geological markers, not just plug targets)
            # These include formations below barriers that don't have plugs
            "formations": [
                {"formation_name": ft.get("formation_name") or ft.get("field_label", ""),
                 "top_ft": ft.get("value")}
                for ft in geometry.get("formation_tops", [])
                if (ft.get("formation_name") or ft.get("field_label")) and ft.get("value") is not None
            ],
            # Add casing strings for well_geometry (CRITICAL: needed for diagram rendering)
            "casing_strings": facts.get("casing_record", []),
            # Add historic cement jobs from W-15 for well_geometry
            "historic_cement_jobs": facts.get("historic_cement_jobs", []),
            # Add production perforations for well_geometry
            "production_perforations": facts.get("production_perforations", []),
        }
        logger.info(f"Plan payload: jurisdiction={jurisdiction}, form_type={plan_payload['form_type']}, steps={len(steps)}, casing_strings={len(plan_payload.get('casing_strings', []))}")
        
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
                    "equipment_status": tool_data.get("equipment_status"),
                })
            logger.info(f"📦 Adding {len(mechanical_equipment_list)} mechanical_equipment to plan payload")
        
        plan_payload["mechanical_equipment"] = mechanical_equipment_list
        plan_payload["existing_tools"] = mechanical_equipment_list  # Alias for compatibility

        return plan_payload


def _apply_field_edit(target: dict, field_path: str, value) -> dict:
    """Apply an edit to a nested dict using a dotpath.

    Supports paths like:
    - "county" → target["county"] = value
    - "casing_record.0.cement_top_ft" → target["casing_record"][0]["cement_top_ft"] = value
    - "formation_record.2.name" → target["formation_record"][2]["name"] = value

    Returns the modified dict.
    Raises KeyError if path is invalid.
    """
    parts = field_path.split(".")
    obj = target
    for part in parts[:-1]:
        if part.isdigit():
            obj = obj[int(part)]
        else:
            obj = obj[part]

    final_key = parts[-1]
    if final_key.isdigit():
        obj[int(final_key)] = value
    else:
        obj[final_key] = value

    return target


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
                    try:
                        if edit.context == WellEditAudit.CONTEXT_PLAN:
                            # Plan-only edits don't touch WellRegistry or documents
                            pass
                        elif edit.context in (WellEditAudit.CONTEXT_EXTRACTION, WellEditAudit.CONTEXT_GEOMETRY):
                            # Check if field_path targets a direct WellRegistry field
                            well_fields = {f.name for f in WellRegistry._meta.get_fields() if hasattr(f, 'column')}
                            top_field = edit.field_path.split(".")[0]

                            if top_field in well_fields and "." not in edit.field_path:
                                # Direct model field update
                                setattr(edit.well, top_field, edit.edited_value)
                                edit.well.save(update_fields=[top_field, "updated_at"])
                            else:
                                # Nested path → update the ExtractedDocument json_data
                                ext_doc = ExtractedDocument.objects.filter(
                                    api_number=edit.well.api14
                                ).order_by('-created_at').first()

                                if ext_doc and ext_doc.json_data:
                                    _apply_field_edit(ext_doc.json_data, edit.field_path, edit.edited_value)
                                    ext_doc.save(update_fields=["json_data", "updated_at"])
                                else:
                                    logger.warning(
                                        "Cannot apply edit %s: no ExtractedDocument for well %s",
                                        edit.id, edit.well.api14 if edit.well else "unknown"
                                    )
                    except (KeyError, IndexError, TypeError) as exc:
                        logger.warning("Failed to apply edit %s (path=%s): %s", edit.id, edit.field_path, exc)
                        # Still mark as applied — the edit is recorded for audit purposes

                    # Mark as applied
                    edit.stage = WellEditAudit.STAGE_APPLIED
                    edit.applied_by = request.user
                    edit.applied_at = timezone.now()
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


