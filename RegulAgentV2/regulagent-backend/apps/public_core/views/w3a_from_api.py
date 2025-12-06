from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import io
import json
import logging
import os
import re
import tempfile
from pathlib import Path

from django.db import transaction
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.public_core.serializers.w3a_plan import (
    W3AFromApiRequestSerializer,
    W3APlanSerializer,
    W3APlanVariantsSerializer,
)
from apps.public_core.services.rrc_completions_extractor import extract_completions_all_documents
from apps.public_core.services.openai_extraction import classify_document, extract_json_from_pdf, vectorize_extracted_document
from apps.public_core.models import ExtractedDocument, WellRegistry, PlanSnapshot
from apps.public_core.services.well_registry_enrichment import enrich_well_registry_from_documents
from apps.tenant_overlay.models import TenantArtifact, WellEngagement
from apps.tenant_overlay.services.engagement_tracker import track_well_interaction
from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts


logger = logging.getLogger(__name__)


def _build_additional_operations(step: Dict[str, Any]) -> List[str]:
    """
    Build list of additional operations for a step (perforate, squeeze, wait, tag).
    
    Returns array of human-readable operation strings for RRC export.
    """
    additional = []
    
    # Check for perforate & squeeze plug (compound operation per §3.14(g)(2))
    if step.get("type") == "perforate_and_squeeze_plug" or step.get("requires_perforation"):
        details = step.get("details") or {}
        perf_details = details.get("perforation_interval", {})
        cap_details = details.get("cement_cap_inside_casing", {})
        
        # Step 1: Perforate
        if perf_details:
            perf_from = perf_details.get("bottom_ft")
            perf_to = perf_details.get("top_ft")
            if perf_from is not None and perf_to is not None:
                additional.append(f"Perforate at {perf_to:.0f}-{perf_from:.0f} ft")
            else:
                additional.append("Perforate")
        else:
            additional.append("Perforate")
        
        # Step 2: Squeeze behind pipe
        additional.append("Squeeze cement through perforations into annulus")
        
        # Step 3: Cement cap inside casing
        if cap_details:
            cap_from = cap_details.get("bottom_ft")
            cap_to = cap_details.get("top_ft")
            cap_height = cap_details.get("height_ft", 50)
            if cap_from is not None and cap_to is not None:
                additional.append(f"Pump {cap_height:.0f} ft cement cap inside casing from {cap_from:.0f}-{cap_to:.0f} ft")
            else:
                additional.append(f"Pump {cap_height:.0f} ft cement cap above perforations")
        else:
            additional.append("Pump 50 ft cement cap above perforations")
    
    # Check for wait and tag requirements
    verification = (step.get("details") or {}).get("verification", {})
    wait_hours = verification.get("required_wait_hr")
    tag_required = step.get("tag_required")
    
    if wait_hours or tag_required:
        wait_text = f"{int(wait_hours)} hr" if wait_hours else "required time"
        if tag_required:
            additional.append(f"Wait {wait_text} and tag TOC")
        else:
            additional.append(f"Wait {wait_text}")
    
    return additional if additional else None


class W3AFromApiView(APIView):
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request):
        logger.critical("🔥🔥🔥 W3A FROM API - POST METHOD CALLED 🔥🔥🔥")
        req = W3AFromApiRequestSerializer(data=request.data)
        if not req.is_valid():
            return Response(req.errors, status=status.HTTP_400_BAD_REQUEST)

        api10: str = req.validated_data["api10"]
        plugs_mode: str = req.validated_data.get("plugs_mode", "combined")
        input_mode: str = req.validated_data.get("input_mode", "extractions")
        merge_threshold_ft: float = float(req.validated_data.get("merge_threshold_ft", 500.0) or 500.0)
        
        # NEW: Sack-based merge limits (for combine mode)
        # sack_limit_no_tag: max sacks to combine WITHOUT tag requirement (default 50)
        # sack_limit_with_tag: max sacks to combine WITH tag requirement (default 150)
        sack_limit_no_tag: float = float(req.validated_data.get("sack_limit_no_tag", 50.0) or 50.0)
        sack_limit_with_tag: float = float(req.validated_data.get("sack_limit_with_tag", 150.0) or 150.0)
        
        confirm_fact_updates: bool = bool(req.validated_data.get("confirm_fact_updates", False))
        allow_precision_upgrades_only: bool = bool(req.validated_data.get("allow_precision_upgrades_only", True))
        use_gau_override_if_invalid: bool = bool(req.validated_data.get("use_gau_override_if_invalid", False))
        gau_file = req.validated_data.get("gau_file")
        w2_file = req.validated_data.get("w2_file")
        w15_file = req.validated_data.get("w15_file")
        schematic_file = req.validated_data.get("schematic_file")
        formation_tops_file = req.validated_data.get("formation_tops_file")

        # Normalize 10-digit API into a flexible key; downstream extractor/DB matching uses last 8 digits
        def _normalize_api(val: str) -> str:
            s = re.sub(r"\D+", "", str(val or ""))
            if len(s) in (14, 10, 8):
                return s
            if len(s) > 8:
                return s[-14:] if len(s) >= 14 else s[-10:] if len(s) >= 10 else s[-8:]
            return s

        api_in = _normalize_api(api10)

        try:
            # 1) Acquire documents: RRC extractions, user uploads, or hybrid
            created: List[Dict[str, Any]] = []
            uploaded_refs: List[Dict[str, Any]] = []

            def _ensure_dir(p: str) -> None:
                os.makedirs(p, exist_ok=True)

            def _sha256_bytes(bts: bytes) -> str:
                import hashlib
                h = hashlib.sha256()
                h.update(bts)
                return h.hexdigest()

            def _sha256_file(path: str) -> str:
                import hashlib
                h = hashlib.sha256()
                with open(path, 'rb') as fp:
                    for chunk in iter(lambda: fp.read(8192), b''):
                        h.update(chunk)
                return h.hexdigest()

            def _save_upload(fobj, api_digits: str) -> str:
                root = getattr(settings, "MEDIA_ROOT", ".")
                ts = str(int(__import__("time").time()))
                base_dir = os.path.join(root, "uploads", api_digits)
                _ensure_dir(base_dir)
                fname = getattr(fobj, "name", "upload.bin")
                safe_name = os.path.basename(fname)
                dest = os.path.join(base_dir, f"{ts}__{safe_name}")
                with open(dest, "wb") as outfp:
                    chunk = fobj.read()
                    outfp.write(chunk if isinstance(chunk, (bytes, bytearray)) else str(chunk).encode("utf-8"))
                return dest

            def _detect_doc_type_from_json(obj: Dict[str, Any]) -> Optional[str]:
                try:
                    if isinstance(obj, dict):
                        if isinstance(obj.get("surface_casing_determination"), dict):
                            return "gau"
                        if "casing_record" in obj or "well_info" in obj:
                            return "w2"
                        if "cementing_report" in obj or "squeeze_operations" in obj:
                            return "w15"
                        if "schematic" in obj or "strings" in obj:
                            return "schematic"
                        if "formation_record" in obj:
                            return "formation_tops"
                except Exception:
                    return None
                return None

            # Default to extractions
            dl: Dict[str, Any] = {}
            files: List[str] = []
            api = api_in
            if input_mode in ("extractions", "hybrid"):
                dl = extract_completions_all_documents(api_in, allowed_kinds=["w2", "w15", "gau"])
                files = dl.get("files") or []
                api = dl.get("api") or api_in
            well = WellRegistry.objects.filter(api14__icontains=str(api)[-8:]).first()
            if input_mode in ("extractions", "hybrid"):
                for f in files:
                    path = f.get("path")
                    if not path:
                        continue
                    doc_type = classify_document(Path(path))
                    if doc_type not in ("gau", "w2", "w15", "schematic", "formation_tops"):
                        continue
                    ext = extract_json_from_pdf(Path(path), doc_type)
                    
                    # Extract tracking_no for W-2 documents (for revision tracking)
                    tracking_no = None
                    if doc_type == "w2" and ext.json_data:
                        try:
                            header = ext.json_data.get("header", {})
                            tracking_no = header.get("tracking_no")
                            if tracking_no:
                                logger.debug(f"📝 W-2 extracted with tracking_no: {tracking_no}")
                        except Exception as e:
                            logger.warning(f"⚠️  Failed to extract tracking_no from W-2: {e}")
                    
                    with transaction.atomic():
                        ed = ExtractedDocument.objects.create(
                            well=well,
                            api_number=api,
                            document_type=doc_type,
                            tracking_no=tracking_no,  # Store tracking_no for W-2s
                            source_path=str(path),
                            model_tag=ext.model_tag,
                            status="success" if not ext.errors else "error",
                            errors=ext.errors,
                            json_data=ext.json_data,
                        )
                        try:
                            vectorize_extracted_document(ed)
                        except Exception:
                            logger.exception("vectorize: failed for RRC doc")
                    created.append({"document_type": doc_type, "extracted_document_id": str(ed.id), "tracking_no": tracking_no})

            # Ingest user files for user_files or hybrid modes
            if input_mode in ("user_files", "hybrid"):
                uploads = [("w2", w2_file), ("w15", w15_file), ("gau", gau_file), ("schematic", schematic_file), ("formation_tops", formation_tops_file)]
                for label, fobj in uploads:
                    if not fobj:
                        continue
                    content_type = getattr(fobj, "content_type", "") or ""
                    filename = getattr(fobj, "name", "") or ""
                    is_json = ("json" in content_type.lower()) or filename.lower().endswith(".json")
                    if is_json:
                        try:
                            raw = fobj.read()
                            data = json.loads(raw.decode("utf-8")) if isinstance(raw, (bytes, bytearray)) else json.loads(str(raw))
                            doc_type = _detect_doc_type_from_json(data) or label
                            
                            # Extract tracking_no for W-2 documents
                            tracking_no = None
                            if doc_type == "w2" and isinstance(data, dict):
                                try:
                                    header = data.get("header", {})
                                    tracking_no = header.get("tracking_no")
                                except Exception:
                                    pass
                            
                            with transaction.atomic():
                                ed = ExtractedDocument.objects.create(
                                    well=well,
                                    api_number=api,
                                    document_type=doc_type,
                                    tracking_no=tracking_no,
                                    source_path=f"upload:{filename or 'user.json'}",
                                    model_tag="user_uploaded_json",
                                    status="success",
                                    errors=[],
                                    json_data=data,
                                )
                            try:
                                vectorize_extracted_document(ed)
                            except Exception:
                                logger.exception("vectorize: failed for user JSON")
                                try:
                                    digest = _sha256_bytes(raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode("utf-8"))
                                    TenantArtifact.objects.create(
                                        artifact_type=doc_type,
                                        file_path=f"upload:{filename or 'user.json'}",
                                        content_type=content_type or "application/json",
                                        size_bytes=len(raw) if isinstance(raw, (bytes, bytearray)) else len(str(raw).encode("utf-8")),
                                        sha256=digest,
                                        extracted_document=ed,
                                        plan_snapshot=None,
                                        metadata={"source": "user_upload", "label": label},
                                    )
                                except Exception:
                                    logger.exception("Failed to persist TenantArtifact for JSON upload")
                            created.append({"document_type": doc_type, "extracted_document_id": str(ed.id)})
                            uploaded_refs.append({"type": doc_type, "filename": filename or "user.json", "kind": "json"})
                        except Exception as e:
                            raise ValueError(f"Invalid {label} JSON upload: {e}")
                    else:
                        # Save to uploads dir, then classify + extract
                        saved_path = _save_upload(fobj, str(api))
                        doc_type = classify_document(Path(saved_path))
                        if doc_type not in ("gau", "w2", "w15", "schematic", "formation_tops"):
                            continue
                        ext = extract_json_from_pdf(Path(saved_path), doc_type)
                        
                        # Extract tracking_no for W-2 documents
                        tracking_no = None
                        if doc_type == "w2" and ext.json_data:
                            try:
                                header = ext.json_data.get("header", {})
                                tracking_no = header.get("tracking_no")
                            except Exception:
                                pass
                        
                        with transaction.atomic():
                            ed = ExtractedDocument.objects.create(
                                well=well,
                                api_number=api,
                                document_type=doc_type,
                                tracking_no=tracking_no,
                                source_path=saved_path,
                                model_tag="user_uploaded_pdf",
                                status="success" if not ext.errors else "error",
                                errors=ext.errors,
                                json_data=ext.json_data,
                            )
                        try:
                            vectorize_extracted_document(ed)
                        except Exception:
                            logger.exception("vectorize: failed for user PDF")
                            try:
                                size_bytes = None
                                try:
                                    size_bytes = os.path.getsize(saved_path)
                                except Exception:
                                    size_bytes = None
                                digest = _sha256_file(saved_path)
                                TenantArtifact.objects.create(
                                    artifact_type=doc_type,
                                    file_path=saved_path,
                                    content_type=content_type or "application/pdf",
                                    size_bytes=size_bytes,
                                    sha256=digest,
                                    extracted_document=ed,
                                    plan_snapshot=None,
                                    metadata={"source": "user_upload", "label": label},
                                )
                            except Exception:
                                logger.exception("Failed to persist TenantArtifact for PDF upload")
                        created.append({"document_type": doc_type, "extracted_document_id": str(ed.id)})
                        uploaded_refs.append({"type": doc_type, "filename": os.path.basename(saved_path), "kind": "pdf"})

            # Ensure WellRegistry exists (create/update) before building plan and snapshots
            try:
                api_digits = re.sub(r"\D+", "", str(api or ""))
                api_candidate = api_digits or api_in
                # Prefer API from latest W-2 if available for canonicalization and county
                w2_latest = (
                    ExtractedDocument.objects
                    .filter(api_number=api, document_type="w2")
                    .order_by("-created_at")
                    .first()
                )
                # Also grab latest GAU for potential lat/lon fallback
                gau_latest = (
                    ExtractedDocument.objects
                    .filter(api_number=api, document_type="gau")
                    .order_by("-created_at")
                    .first()
                )
                api14_cand = None
                county_cand = None
                district_cand = None
                lat_cand = None
                lon_cand = None
                if w2_latest and isinstance(w2_latest.json_data, dict):
                    wi = (w2_latest.json_data.get("well_info") or {})
                    api14_cand = re.sub(r"\D+", "", str(wi.get("api") or "")) or None
                    county_cand = wi.get("county") or None
                    district_cand = wi.get("district") or None  # Extract RRC district (e.g., '8A', '7C')
                    # Common W-2 formats for location lat/lon
                    loc = wi.get("location") or {}
                    lat_cand = loc.get("lat") or loc.get("latitude") or None
                    lon_cand = loc.get("lon") or loc.get("longitude") or None
                    operator_cand = wi.get("operator") or wi.get("operator_name") or None
                    field_cand = wi.get("field") or wi.get("field_name") or None
                # Fallback to GAU coordinates if W-2 missing/empty
                if (lat_cand is None or lon_cand is None) and gau_latest and isinstance(gau_latest.json_data, dict):
                    wi_g = (gau_latest.json_data.get("well_info") or {})
                    loc_g = wi_g.get("location") or {}
                    lat_cand = lat_cand or loc_g.get("lat") or loc_g.get("latitude") or wi_g.get("latitude")
                    lon_cand = lon_cand or loc_g.get("lon") or loc_g.get("longitude") or wi_g.get("longitude")
                api14_final = api14_cand or re.sub(r"\D+", "", str(api_candidate or ""))
                proposed_changes: Dict[str, Any] = {}
                applied_changes: Dict[str, Any] = {}
                if api14_final:
                    well, _created = WellRegistry.objects.get_or_create(
                        api14=api14_final,
                        defaults={"state": "TX", "county": (county_cand or "")},
                    )
                    # Backfill county/district/operator/field if learned now
                    try:
                        changed = False
                        if county_cand and not (well.county or "").strip():
                            proposed_changes["county"] = {"before": well.county, "after": str(county_cand), "source": "w2"}
                            changed = True
                        if district_cand and not (well.district or "").strip():
                            proposed_changes["district"] = {"before": well.district, "after": str(district_cand), "source": "w2"}
                            changed = True
                        if operator_cand and not (well.operator_name or "").strip():
                            proposed_changes["operator_name"] = {"before": well.operator_name, "after": str(operator_cand)[:128], "source": "w2"}
                            changed = True
                        if field_cand and not (well.field_name or "").strip():
                            proposed_changes["field_name"] = {"before": well.field_name, "after": str(field_cand)[:128], "source": "w2"}
                            changed = True
                    except Exception:
                        pass
                    # Backfill lat/lon if available (always stage; apply per approval policy)
                    try:
                        def _to_dec(v):
                            if v is None:
                                return None
                            try:
                                return float(v)
                            except Exception:
                                return None
                        lat_val = _to_dec(lat_cand)
                        lon_val = _to_dec(lon_cand)
                        if (lat_val is not None and lon_val is not None):
                            # Stage change always and compute materiality (simple degree delta check)
                            before_lat = float(well.lat) if well.lat is not None else None
                            before_lon = float(well.lon) if well.lon is not None else None
                            proposed_changes["lat"] = {"before": before_lat, "after": float(lat_val), "source": ("w2" if w2_latest else "gau")}
                            proposed_changes["lon"] = {"before": before_lon, "after": float(lon_val), "source": ("w2" if w2_latest else "gau")}
                    except Exception:
                        pass
                    # Apply proposed changes if confirmed
                    try:
                        if proposed_changes and confirm_fact_updates:
                            # Decide apply policy
                            def _small_delta(old_val, new_val):
                                try:
                                    if old_val is None:
                                        return True
                                    return abs(float(old_val) - float(new_val)) < 0.001  # ~0.001 deg
                                except Exception:
                                    return False
                            # county/district/operator/field: fill only when empty unless explicit non-precision approval
                            if "county" in proposed_changes and (not (well.county or "").strip() or not allow_precision_upgrades_only):
                                well.county = proposed_changes["county"]["after"]
                                applied_changes["county"] = proposed_changes["county"]
                            if "district" in proposed_changes and (not (well.district or "").strip() or not allow_precision_upgrades_only):
                                well.district = proposed_changes["district"]["after"]
                                applied_changes["district"] = proposed_changes["district"]
                            if "operator_name" in proposed_changes and (not (well.operator_name or "").strip() or not allow_precision_upgrades_only):
                                well.operator_name = proposed_changes["operator_name"]["after"]
                                applied_changes["operator_name"] = proposed_changes["operator_name"]
                            if "field_name" in proposed_changes and (not (well.field_name or "").strip() or not allow_precision_upgrades_only):
                                well.field_name = proposed_changes["field_name"]["after"]
                                applied_changes["field_name"] = proposed_changes["field_name"]
                            if "lat" in proposed_changes and "lon" in proposed_changes:
                                new_lat = proposed_changes["lat"]["after"]
                                new_lon = proposed_changes["lon"]["after"]
                                if not allow_precision_upgrades_only:
                                    well.lat = new_lat; well.lon = new_lon
                                    applied_changes["lat"] = proposed_changes["lat"]
                                    applied_changes["lon"] = proposed_changes["lon"]
                                else:
                                    if (well.lat is None or well.lon is None) or (_small_delta(well.lat, new_lat) and _small_delta(well.lon, new_lon)):
                                        well.lat = new_lat; well.lon = new_lon
                                        applied_changes["lat"] = proposed_changes["lat"]
                                        applied_changes["lon"] = proposed_changes["lon"]
                            if applied_changes:
                                well.save()
                    except Exception:
                        logger.exception("Failed to persist confirmed fact updates")

                    # Attach any created ExtractedDocuments in this request to this well
                    try:
                        ed_ids = [c.get("extracted_document_id") for c in created if c.get("extracted_document_id")]
                        if ed_ids:
                            ExtractedDocument.objects.filter(id__in=ed_ids).update(well=well)
                    except Exception:
                        logger.exception("Failed to backfill well on ExtractedDocuments")
            except Exception:
                logger.exception("Failed to ensure WellRegistry before plan build")

            # 2) Determine GAU validity; if invalid and user provided override, ingest it (only in extractions mode)
            gau_valid = self._has_valid_gau(api)
            if input_mode == "extractions" and (not gau_valid) and use_gau_override_if_invalid and gau_file is not None:
                # Accept either PDF (preferred) or JSON payload for GAU override
                content_type = getattr(gau_file, "content_type", None) or ""
                filename = getattr(gau_file, "name", "") or ""
                is_json = ("json" in content_type.lower()) or filename.lower().endswith(".json")
                if is_json:
                    try:
                        raw = gau_file.read()
                        data = json.loads(raw.decode("utf-8")) if isinstance(raw, (bytes, bytearray)) else json.loads(str(raw))
                        with transaction.atomic():
                            ExtractedDocument.objects.create(
                                well=well,
                                api_number=api,
                                document_type="gau",
                                source_path=filename or "gau(user).json",
                                model_tag="user_uploaded_json",
                                status="success",
                                errors=[],
                                json_data=data,
                            )
                    except Exception as e:
                        logger.exception("GAU JSON override ingest failed")
                        raise ValueError(f"Invalid GAU JSON override: {e}")
                else:
                    tmp_path = self._persist_upload_to_tmp_pdf(gau_file)
                    try:
                        ext = extract_json_from_pdf(Path(tmp_path), "gau")
                        with transaction.atomic():
                            ExtractedDocument.objects.create(
                                well=well,
                                api_number=api,
                                document_type="gau",
                                source_path=tmp_path,
                                model_tag=ext.model_tag,
                                status="success" if not ext.errors else "error",
                                errors=ext.errors,
                                json_data=ext.json_data,
                            )
                    finally:
                        try:
                            os.unlink(tmp_path)
                        except Exception:
                            pass

            # 2.5) Enrich WellRegistry from extracted documents (operator, field, lease, lat/lon)
            if well:
                try:
                    extracted_docs = ExtractedDocument.objects.filter(well=well, api_number__contains=str(api)[-8:])
                    if extracted_docs.exists():
                        enrich_well_registry_from_documents(well, list(extracted_docs))
                        logger.info(f"Enriched WellRegistry for API {api} from {extracted_docs.count()} documents")
                except Exception:
                    logger.exception(f"Failed to enrich WellRegistry for API {api} (non-fatal)")
            
            # 3) Build plans according to plugs_mode
            extraction_info = {
                "status": dl.get("status"),
                "source": dl.get("source"),
                "output_dir": dl.get("output_dir"),
                "files": [os.fspath((f or {}).get("path")) for f in (dl.get("files") or [])],
            }
            if uploaded_refs:
                extraction_info["user_files"] = uploaded_refs
            if plugs_mode == "both":
                combined = self._build_plan(api, merge_enabled=True, merge_threshold_ft=merge_threshold_ft, sack_limit_no_tag=sack_limit_no_tag, sack_limit_with_tag=sack_limit_with_tag)
                isolated = self._build_plan(api, merge_enabled=False, merge_threshold_ft=merge_threshold_ft, sack_limit_no_tag=sack_limit_no_tag, sack_limit_with_tag=sack_limit_with_tag)
                out = {"variants": {"combined": combined, "isolated": isolated}, "extraction": extraction_info}
                # Persist baseline snapshot (variants payload) if well available
                try:
                    well_for_snapshot = well or WellRegistry.objects.filter(api14__icontains=str(api)[-8:]).first()
                    if well_for_snapshot is not None:
                        snapshot = PlanSnapshot.objects.create(
                            well=well_for_snapshot,
                            plan_id=f"{api}:both",
                            kind=PlanSnapshot.KIND_BASELINE,
                            payload=out,
                            kernel_version=str((combined or {}).get("kernel_version") or ""),
                            policy_id="tx.w3a",
                            overlay_id="",
                            extraction_meta=extraction_info,
                            # Baseline plans are public (shareable for learning)
                            visibility=PlanSnapshot.VISIBILITY_PUBLIC,
                            tenant_id=request.user.tenants.first().id if (request.user.is_authenticated and request.user.tenants.exists()) else None,
                            status=PlanSnapshot.STATUS_DRAFT,  # Initial plan starts as draft
                        )
                        try:
                            # Link any artifacts created during this request to the snapshot
                            ed_ids = [c.get("extracted_document_id") for c in created if c.get("extracted_document_id")]
                            if ed_ids:
                                TenantArtifact.objects.filter(extracted_document__id__in=ed_ids).update(plan_snapshot=snapshot)
                        except Exception:
                            logger.exception("Failed to link TenantArtifacts to baseline snapshot (both)")
                        
                        # Track tenant-well engagement if user is authenticated
                        try:
                            if request.user.is_authenticated:
                                user_tenant = request.user.tenants.first()
                                if user_tenant:
                                    track_well_interaction(
                                        tenant_id=user_tenant.id,
                                        well=well_for_snapshot,
                                        interaction_type=WellEngagement.InteractionType.W3A_GENERATED,
                                        user=request.user,
                                        metadata_update={
                                            'plan_id': snapshot.plan_id,
                                            'snapshot_id': str(snapshot.id),
                                            'plugs_mode': 'both'
                                        }
                                    )
                        except Exception:
                            logger.exception("Failed to track well engagement (both)")
                except Exception:
                    logger.exception("W3AFromApiView: failed to persist baseline snapshot (both)")
                ser = W3APlanVariantsSerializer(data=out["variants"])  # validate shape
                ser.is_valid(raise_exception=False)
                if proposed_changes and not confirm_fact_updates:
                    out["facts_update_preview"] = proposed_changes
                return Response(out, status=status.HTTP_200_OK)
            else:
                merge_enabled = (plugs_mode == "combined")
                plan = self._build_plan(api, merge_enabled=merge_enabled, merge_threshold_ft=merge_threshold_ft, sack_limit_no_tag=sack_limit_no_tag, sack_limit_with_tag=sack_limit_with_tag)
                ser = W3APlanSerializer(data=plan)
                ser.is_valid(raise_exception=False)
                response_payload = {**plan, "extraction": extraction_info}
                if proposed_changes and not confirm_fact_updates:
                    response_payload["facts_update_preview"] = proposed_changes
                # Persist baseline snapshot (single variant) if well available
                try:
                    well_for_snapshot = well or WellRegistry.objects.filter(api14__icontains=str(api)[-8:]).first()
                    if well_for_snapshot is not None:
                        variant_label = "combined" if merge_enabled else "isolated"
                        snapshot = PlanSnapshot.objects.create(
                            well=well_for_snapshot,
                            plan_id=f"{api}:{variant_label}",
                            kind=PlanSnapshot.KIND_BASELINE,
                            payload=response_payload,
                            kernel_version=str((plan or {}).get("kernel_version") or ""),
                            policy_id="tx.w3a",
                            overlay_id="",
                            extraction_meta=extraction_info,
                            # Baseline plans are public (shareable for learning)
                            visibility=PlanSnapshot.VISIBILITY_PUBLIC,
                            tenant_id=request.user.tenants.first().id if (request.user.is_authenticated and request.user.tenants.exists()) else None,
                            status=PlanSnapshot.STATUS_DRAFT,  # Initial plan starts as draft
                        )
                        try:
                            # Link any artifacts created during this request to the snapshot
                            ed_ids = [c.get("extracted_document_id") for c in created if c.get("extracted_document_id")]
                            if ed_ids:
                                TenantArtifact.objects.filter(extracted_document__id__in=ed_ids).update(plan_snapshot=snapshot)
                        except Exception:
                            logger.exception("Failed to link TenantArtifacts to baseline snapshot (single)")
                        
                        # Track tenant-well engagement if user is authenticated
                        try:
                            if request.user.is_authenticated:
                                user_tenant = request.user.tenants.first()
                                if user_tenant:
                                    track_well_interaction(
                                        tenant_id=user_tenant.id,
                                        well=well_for_snapshot,
                                        interaction_type=WellEngagement.InteractionType.W3A_GENERATED,
                                        user=request.user,
                                        metadata_update={
                                            'plan_id': snapshot.plan_id,
                                            'snapshot_id': str(snapshot.id),
                                            'plugs_mode': plugs_mode
                                        }
                                    )
                        except Exception:
                            logger.exception("Failed to track well engagement (single)")
                except Exception:
                    logger.exception("W3AFromApiView: failed to persist baseline snapshot (single)")
                return Response(response_payload, status=status.HTTP_200_OK)

        except ValueError as ve:
            return Response({"detail": str(ve)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("W3AFromApiView: error")
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # --- Helpers ---
    def _persist_upload_to_tmp_pdf(self, fobj) -> str:
        # Persist uploaded GAU PDF to a temporary file for extraction service
        suffix = ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            # fobj can be InMemoryUploadedFile or TemporaryUploadedFile
            chunk = fobj.read()
            if isinstance(chunk, bytes):
                tmp.write(chunk)
            else:
                tmp.write(chunk.encode("utf-8"))
            return tmp.name

    def _has_valid_gau(self, api: str) -> bool:
        # GAU considered valid when <= 5 years old and has determination depth
        try:
            gau_doc = (
                ExtractedDocument.objects
                .filter(api_number=api, document_type="gau")
                .order_by("-created_at")
                .first()
            )
            gau = (gau_doc and gau_doc.json_data) or {}
            import datetime as _dt
            gau_date_txt = ((gau.get("header") or {}).get("date") if gau else None) or None
            gau_depth = (gau.get("surface_casing_determination") or {}).get("gau_groundwater_protection_determination_depth") if gau else None
            if gau_depth is None or not gau_date_txt:
                return False
            gau_dt = None
            for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d", "%d %B %Y", "%B %d, %Y", "%d %b %Y", "%b %d, %Y"):
                try:
                    gau_dt = _dt.datetime.strptime(str(gau_date_txt), fmt)
                    break
                except Exception:
                    gau_dt = None
            if not gau_dt:
                return False
            age_days = (_dt.datetime.utcnow() - gau_dt).days
            return bool(age_days <= (5 * 365))
        except Exception:
            return False

    def _build_plan(
        self,
        api: str,
        *,
        merge_enabled: bool,
        merge_threshold_ft: float,
        sack_limit_no_tag: float = 50.0,
        sack_limit_with_tag: float = 150.0,
    ) -> Dict[str, Any]:
        # Mirror management command: assemble facts from latest W-2/W-15/GAU extractions, then run kernel
        def latest(doc_type: str) -> Optional[ExtractedDocument]:
            return (
                ExtractedDocument.objects
                .filter(api_number=api, document_type=doc_type)
                .order_by("-created_at")
                .first()
            )
        
        def get_consolidated_w2() -> Dict[str, Any]:
            """
            Retrieve and consolidate all W-2 extractions, applying revisions.
            
            Returns the consolidated W-2 JSON with revisions applied.
            """
            logger.info("\n🔀 CONSOLIDATING W-2 EXTRACTIONS")
            try:
                from apps.public_core.services.w2_revision_consolidator import consolidate_w2_extractions
                
                # Get all W-2 extractions for this API (not just latest)
                w2_docs = ExtractedDocument.objects.filter(
                    api_number=api,
                    document_type="w2"
                ).order_by("created_at")
                
                if not w2_docs.exists():
                    logger.info("   ℹ️  No W-2 documents found")
                    return {}
                
                logger.info(f"   Found {w2_docs.count()} W-2 extraction(s)")
                
                # Build input for consolidator
                w2_extractions = []
                for w2_doc in w2_docs:
                    w2_extractions.append({
                        "json_data": w2_doc.json_data,
                        "revisions": w2_doc.json_data.get("revisions")
                    })
                
                # Run consolidation
                consolidation_result = consolidate_w2_extractions(w2_extractions)
                
                # Extract the final consolidated W-2 (use the last one after all revisions applied)
                consolidated_list = consolidation_result.get("consolidated_w2s", [])
                if consolidated_list:
                    # Use the last consolidated W-2 (most recent chronologically)
                    final_w2 = consolidated_list[-1]["json_data"]
                    logger.info(f"   ✅ Using consolidated W-2 (tracking_no: {consolidated_list[-1].get('tracking_no')})")
                    return final_w2
                else:
                    logger.warning(f"   ⚠️  Consolidation returned empty list")
                    return (w2_docs.last().json_data) if w2_docs.exists() else {}
                
            except Exception as e:
                logger.warning(f"   ⚠️  Consolidation failed (non-fatal), falling back to latest W-2: {e}")
                # Fallback to latest W-2
                w2_doc = latest("w2")
                return (w2_doc and w2_doc.json_data) or {}

        w2 = get_consolidated_w2()
        gau_doc = latest("gau"); w15_doc = latest("w15"); schematic_doc = latest("schematic")
        gau = (gau_doc and gau_doc.json_data) or {}
        w15 = (w15_doc and w15_doc.json_data) or {}
        schematic = (schematic_doc and schematic_doc.json_data) or {}

        wi = w2.get("well_info") or {}
        api14 = (wi.get("api") or "").replace("-", "")
        county = wi.get("county") or ""
        field = wi.get("field") or ""
        lease = wi.get("lease") or ""
        well_no = wi.get("well_no") or ""
        rrc = (wi.get("district") or wi.get("rrc_district") or "").strip()
        district = "08A" if (rrc in ("08", "8") and ("andrews" in str(county).lower())) else (rrc or "08A")

        # GAU base depth if not older than 5 years
        uqw_depth = None
        uqw_source = None
        uqw_age_days: Optional[int] = None
        try:
            import datetime as _dt
            gau_date_txt = ((gau.get("header") or {}).get("date") if gau else None) or None
            gau_depth = (gau.get("surface_casing_determination") or {}).get("gau_groundwater_protection_determination_depth") if gau else None
            if gau_depth is not None and gau_date_txt:
                for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d", "%d %B %Y", "%B %d, %Y", "%d %b %Y", "%b %d, %Y"):
                    try:
                        gau_dt = _dt.datetime.strptime(str(gau_date_txt), fmt)
                        break
                    except Exception:
                        gau_dt = None
                if gau_dt:
                    age_days = (_dt.datetime.utcnow() - gau_dt).days
                    uqw_age_days = int(age_days)
                    if age_days <= (5 * 365):
                        uqw_depth = gau_depth
                        uqw_source = "gau"
        except Exception:
            uqw_depth = uqw_depth

        # Parse GAU protect intervals from recommendation text
        gau_protect_intervals: List[Dict[str, Any]] = []
        try:
            rec = str((gau.get("recommendation") or "") if gau else "")
            m_surf = re.search(r"surface\s+to\s+a\s+depth\s+of\s+(\d{2,5})\s*feet", rec, flags=re.IGNORECASE)
            if m_surf:
                top = float(m_surf.group(1)); gau_protect_intervals.append({"top_ft": top, "bottom_ft": 0.0, "source": "gau"})
            for m in re.finditer(r"from\s+a\s+depth\s+of\s+(\d{2,5})\s*feet\s+to\s+(\d{2,5})\s*feet", rec, flags=re.IGNORECASE):
                a = float(m.group(1)); b = float(m.group(2)); lo, hi = min(a, b), max(a, b)
                gau_protect_intervals.append({"top_ft": hi, "bottom_ft": lo, "source": "gau"})
        except Exception:
            gau_protect_intervals = gau_protect_intervals

        # Casing geometry from W-2
        surface_shoe_ft = None
        surface_size_in = None
        intermediate_size_in = None
        prod_size_in = None
        production_shoe_ft = None
        intermediate_shoe_ft = None
        deepest_shoe_any_ft = None
        for row in (w2.get("casing_record") or []):
            kind = (row.get("string") or row.get("type_of_casing") or "").lower()
            if kind.startswith("surface"):
                surface_shoe_ft = row.get("shoe_depth_ft") or row.get("setting_depth_ft") or row.get("bottom_ft")
                surface_size_in = row.get("size_in") or row.get("casing_size_in")
            if kind.startswith("production") and production_shoe_ft is None:
                production_shoe_ft = row.get("shoe_depth_ft") or row.get("setting_depth_ft") or row.get("bottom_ft")
            if kind.startswith("intermediate") and intermediate_shoe_ft is None:
                intermediate_shoe_ft = row.get("setting_depth_ft") or row.get("bottom_ft") or None
                intermediate_size_in = row.get("size_in") or row.get("casing_size_in")
            # Track deepest shoe/setting/bottom across all strings as fallback for production shoe
            try:
                cand = row.get("shoe_depth_ft") or row.get("setting_depth_ft") or row.get("bottom_ft")
                if cand is not None:
                    cval = float(cand)
                    if (deepest_shoe_any_ft is None) or (cval > float(deepest_shoe_any_ft)):
                        deepest_shoe_any_ft = cval
            except Exception:
                pass
        
        # Extract TOC (Top of Cement) from casing record for plug type determination
        production_casing_toc_ft = None
        intermediate_casing_toc_ft = None
        surface_casing_toc_ft = None
        
        for row in (w2.get("casing_record") or []):
            kind = (row.get("string") or row.get("type_of_casing") or "").lower()
            cement_top = row.get("cement_top_ft")
            
            if cement_top is not None:
                try:
                    cement_top_val = float(cement_top)
                    
                    if kind.startswith("production") and production_casing_toc_ft is None:
                        production_casing_toc_ft = cement_top_val
                        logger.info(f"📍 Production casing TOC extracted: {production_casing_toc_ft} ft")
                    
                    elif kind.startswith("intermediate") and intermediate_casing_toc_ft is None:
                        intermediate_casing_toc_ft = cement_top_val
                        logger.info(f"📍 Intermediate casing TOC extracted: {intermediate_casing_toc_ft} ft")
                    
                    elif kind.startswith("surface") and surface_casing_toc_ft is None:
                        surface_casing_toc_ft = cement_top_val
                        logger.info(f"📍 Surface casing TOC extracted: {surface_casing_toc_ft} ft")
                except (ValueError, TypeError):
                    pass
        
        sizes = []
        for row in (w2.get("casing_record") or []):
            s = row.get("size_in") or row.get("casing_size_in")
            if s:
                sizes.append(s)

        def _parse_size(txt: Any) -> Optional[float]:
            if txt is None:
                return None
            if isinstance(txt, (int, float)):
                return float(txt)
            t = str(txt).strip().replace('"', '')
            if ' ' in t:
                parts = t.split()
                try:
                    whole = float(parts[0])
                except Exception:
                    return None
                frac = 0.0
                if len(parts) > 1 and '/' in parts[1]:
                    try:
                        num, den = parts[1].split('/')
                        frac = float(num) / float(den)
                    except Exception:
                        frac = 0.0
                return whole + frac
            try:
                return float(t)
            except Exception:
                return None

        parsed_sizes = [ps for ps in (_parse_size(s) for s in sizes) if ps]
        if parsed_sizes:
            prod_size_in = min(parsed_sizes)

        # Producing interval from W-2 if present
        prod_iv = None
        perforations_from_w2: List[Dict[str, Any]] = []
        try:
            piv = w2.get("producing_injection_disposal_interval") or {}
            
            # Check if it's a single interval (dict) or multiple intervals (list)
            if isinstance(piv, dict):
                # Single interval
                if piv.get("from_ft") and piv.get("to_ft"):
                    f = float(piv["from_ft"]) if piv["from_ft"] is not None else None
                    t = float(piv["to_ft"]) if piv["to_ft"] is not None else None
                    if f is not None and t is not None:
                        prod_iv = [f, t]
                        # Add as perforation interval
                        perf_interval = {
                            "interval_top_ft": min(f, t),
                            "interval_bottom_ft": max(f, t),
                            "status": "perforated" if piv.get("open_hole") != "Yes" else "open_hole"
                        }
                        perforations_from_w2.append(perf_interval)
                        logger.info(f"📍 Extracted W-2 producing interval: {perf_interval}")
            
            elif isinstance(piv, list):
                # Multiple intervals (as shown in the user's image - Ro 1, 2, 3, etc.)
                logger.info(f"📍 Found {len(piv)} producing/injection/disposal intervals in W-2")
                for idx, interval in enumerate(piv):
                    if isinstance(interval, dict):
                        f = interval.get("from_ft") or interval.get("From (ft.)")
                        t = interval.get("to_ft") or interval.get("To (ft.)")
                        open_hole = interval.get("open_hole") or interval.get("Open hole?")
                        
                        if f is not None and t is not None:
                            try:
                                f_val = float(f)
                                t_val = float(t)
                                
                                # Use the first interval as overall producing_iv
                                if prod_iv is None:
                                    prod_iv = [min(f_val, t_val), max(f_val, t_val)]
                                
                                # Add all intervals as perforations
                                perf_interval = {
                                    "interval_top_ft": min(f_val, t_val),
                                    "interval_bottom_ft": max(f_val, t_val),
                                    "status": "open_hole" if (open_hole and str(open_hole).upper() == "YES") else "perforated"
                                }
                                perforations_from_w2.append(perf_interval)
                                logger.info(f"   [{idx+1}] Interval {min(f_val, t_val)}-{max(f_val, t_val)} ft ({perf_interval['status']})")
                            except (ValueError, TypeError):
                                pass
        
        except Exception as e:
            logger.warning(f"⚠️  Failed to extract producing intervals from W-2: {e}")
            prod_iv = None

        # Extract historic cement jobs from W-15 cementing data
        historic_cement_jobs: List[Dict[str, Any]] = []
        try:
            cementing_data = w15.get("cementing_data") or []
            if isinstance(cementing_data, list):
                for cement_job in cementing_data:
                    if isinstance(cement_job, dict):
                        try:
                            job_entry: Dict[str, Any] = {
                                "job_type": cement_job.get("job"),  # surface|intermediate|production|plug|squeeze
                                "interval_top_ft": cement_job.get("interval_top_ft"),
                                "interval_bottom_ft": cement_job.get("interval_bottom_ft"),
                                "cement_top_ft": cement_job.get("cement_top_ft"),
                                "sacks": cement_job.get("sacks"),
                                "slurry_density_ppg": cement_job.get("slurry_density_ppg"),
                            }
                            # Only add if we have meaningful data
                            if job_entry.get("job_type") or job_entry.get("sacks"):
                                # Filter out None values to keep output clean
                                job_entry = {k: v for k, v in job_entry.items() if v is not None}
                                historic_cement_jobs.append(job_entry)
                        except Exception:
                            pass
            logger.info(f"Extracted {len(historic_cement_jobs)} historic cement jobs from W-15")
        except Exception:
            logger.exception("Failed to extract historic cement jobs from W-15")
            historic_cement_jobs = []

        # Formation tops map
        formation_tops_map: Dict[str, float] = {}
        try:
            for rec in (w2.get("formation_record") or []):
                name = str(rec.get("formation") or "").strip().lower()
                top = rec.get("top_ft")
                if name and top is not None:
                    formation_tops_map[name] = float(top)
        except Exception:
            formation_tops_map = {}

        # Extract existing mechanical barriers (CIBP, packer, DV tool) from W-2 remarks
        # This is critical for distinguishing historical tools from new steps to add
        existing_mech_barriers: List[str] = []
        existing_cibp_ft = None
        existing_packer_ft = None
        existing_dv_tool_ft = None
        
        try:
            # Combine remarks and RRC remarks for searching
            remarks_txt = str(w2.get("remarks") or "")
            rrc_remarks_obj = w2.get("rrc_remarks") or {}
            rrc_remarks_txt = ""
            if isinstance(rrc_remarks_obj, dict):
                # Flatten all RRC remarks fields
                for key, val in rrc_remarks_obj.items():
                    if val:
                        rrc_remarks_txt += f" {val}"
            elif isinstance(rrc_remarks_obj, str):
                rrc_remarks_txt = rrc_remarks_obj
            
            combined_remarks = f"{remarks_txt} {rrc_remarks_txt}"
            
            # Search for CIBP (Cast Iron Bridge Plug) depth
            for pattern in [
                r"CIBP\s*(?:at|@)?\s*(\d{3,5})",
                r"cast\s*iron\s*bridge\s*plug\s*(?:at|@)?\s*(\d{3,5})",
                r"\bBP\b\s*(?:at|@)?\s*(\d{3,5})"
            ]:
                match = re.search(pattern, combined_remarks, flags=re.IGNORECASE)
                if match:
                    try:
                        existing_cibp_ft = float(match.group(1))
                        if "CIBP" not in existing_mech_barriers:
                            existing_mech_barriers.append("CIBP")
                        break
                    except Exception:
                        pass
            
            # Search for Packer depth
            packer_match = re.search(r"packer\s*(?:at|set\s*at|@)?\s*(\d{3,5})", combined_remarks, flags=re.IGNORECASE)
            if packer_match:
                try:
                    existing_packer_ft = float(packer_match.group(1))
                    if "PACKER" not in existing_mech_barriers:
                        existing_mech_barriers.append("PACKER")
                except Exception:
                    pass
            
            # Search for DV tool depth
            for pattern in [
                r"DV[- ]?(?:stage)?\s*tool\s*(?:at|@)?\s*(\d{3,5})",
                r"DV[- ]?tool\s*(\d{3,5})"
            ]:
                dv_match = re.search(pattern, combined_remarks, flags=re.IGNORECASE)
                if dv_match:
                    try:
                        existing_dv_tool_ft = float(dv_match.group(1))
                        if "DV_TOOL" not in existing_mech_barriers:
                            existing_mech_barriers.append("DV_TOOL")
                        break
                    except Exception:
                        pass
            
            # Also check acid_fracture_operations for squeeze operations (often indicate CIBPs)
            acid_ops = w2.get("acid_fracture_operations", {})
            if isinstance(acid_ops, dict):
                operations = acid_ops.get("operations", [])
                for op in operations:
                    if isinstance(op, dict):
                        op_type = str(op.get("type_of_operation") or "").lower()
                        # Squeezes often done through or above CIBPs
                        if "squeeze" in op_type or "bridge" in op_type or "cibp" in op_type:
                            # Note: We found a squeeze but can't determine exact CIBP depth from this alone
                            # The remarks parsing above is more reliable for depth
                            pass
        
        except Exception:
            # Fail silently - existing tools extraction is enhancement, not critical
            pass

        # Extract retainer tools from W-2 remarks
        retainer_tools: List[Dict[str, Any]] = []
        try:
            # Combine remarks and RRC remarks for searching
            remarks_txt = str(w2.get("remarks") or "")
            rrc_remarks_obj = w2.get("rrc_remarks") or {}
            rrc_remarks_txt = ""
            if isinstance(rrc_remarks_obj, dict):
                for key, val in rrc_remarks_obj.items():
                    if val:
                        rrc_remarks_txt += f" {val}"
            elif isinstance(rrc_remarks_obj, str):
                rrc_remarks_txt = rrc_remarks_obj
            
            combined_remarks = f"{remarks_txt} {rrc_remarks_txt}"
            
            # Search for Retainer depth
            for pattern in [
                r"retainer\s*(?:at|@)?\s*(\d{3,5})",
                r"retainer\s+(?:packer\s+)?(?:at|@)?\s*(\d{3,5})"
            ]:
                retainer_matches = re.finditer(pattern, combined_remarks, flags=re.IGNORECASE)
                for match in retainer_matches:
                    try:
                        depth = float(match.group(1))
                        retainer_tools.append({"tool_type": "retainer", "depth_ft": depth})
                    except Exception:
                        pass
            
            # Search for Straddle Packer depth
            for pattern in [
                r"straddle\s*(?:packer\s+)?(?:at|@)?\s*(\d{3,5})",
                r"straddle\s*(?:at|@)?\s*(\d{3,5})"
            ]:
                straddle_matches = re.finditer(pattern, combined_remarks, flags=re.IGNORECASE)
                for match in straddle_matches:
                    try:
                        depth = float(match.group(1))
                        if not any(t.get("tool_type") == "straddle_packer" and t.get("depth_ft") == depth for t in retainer_tools):
                            retainer_tools.append({"tool_type": "straddle_packer", "depth_ft": depth})
                    except Exception:
                        pass
            
            # Search for Float Collar depth
            for pattern in [
                r"float\s*(?:collar\s+)?(?:at|@)?\s*(\d{3,5})",
                r"float\s*(?:at|@)?\s*(\d{3,5})"
            ]:
                float_matches = re.finditer(pattern, combined_remarks, flags=re.IGNORECASE)
                for match in float_matches:
                    try:
                        depth = float(match.group(1))
                        if not any(t.get("tool_type") == "float_collar" and t.get("depth_ft") == depth for t in retainer_tools):
                            retainer_tools.append({"tool_type": "float_collar", "depth_ft": depth})
                    except Exception:
                        pass
            
            # Search for Pup Joint depth
            for pattern in [
                r"pup\s*(?:joint\s+)?(?:at|@)?\s*(\d{3,5})",
                r"pup\s*(?:at|@)?\s*(\d{3,5})"
            ]:
                pup_matches = re.finditer(pattern, combined_remarks, flags=re.IGNORECASE)
                for match in pup_matches:
                    try:
                        depth = float(match.group(1))
                        if not any(t.get("tool_type") == "pup_joint" and t.get("depth_ft") == depth for t in retainer_tools):
                            retainer_tools.append({"tool_type": "pup_joint", "depth_ft": depth})
                    except Exception:
                        pass
        
        except Exception:
            # Fail silently - retainer tools extraction is enhancement, not critical
            logger.exception("Failed to extract retainer tools from remarks")
            pass

        # Extract KOP (Kick-Off Point) from W-2 for horizontal well CIBP placement
        kop_md_ft = None
        kop_tvd_ft = None
        try:
            kop_data = w2.get("kop") or {}
            if isinstance(kop_data, dict):
                kop_md_ft = kop_data.get("kop_md_ft")
                kop_tvd_ft = kop_data.get("kop_tvd_ft")
                if kop_md_ft is not None:
                    kop_md_ft = float(kop_md_ft)
                    logger.info(f"📍 KOP extracted: MD={kop_md_ft} ft, TVD={kop_tvd_ft} ft")
        except Exception as e:
            logger.warning(f"Failed to extract KOP data: {e}")
            kop_md_ft = None
            kop_tvd_ft = None

        def wrap(v: Any) -> Dict[str, Any]:
            return {"value": v}

        facts: Dict[str, Any] = {
            "api14": wrap(api14),
            "state": wrap("TX"),
            "district": wrap(district),
            "county": wrap(county),
            "field": wrap(field),
            "lease": wrap(lease),
            "well_no": wrap(well_no),
            "has_uqw": wrap(bool(gau or uqw_depth)),
            "uqw_base_ft": wrap(uqw_depth),
            "use_cibp": wrap(False),
            "surface_shoe_ft": wrap(surface_shoe_ft),
        }
        
        # Add existing mechanical barriers if found
        if existing_mech_barriers:
            facts["existing_mechanical_barriers"] = existing_mech_barriers
        if existing_cibp_ft is not None:
            facts["existing_cibp_ft"] = wrap(existing_cibp_ft)
            facts["cibp_present"] = wrap(True)
        if existing_packer_ft is not None:
            facts["packer_ft"] = wrap(existing_packer_ft)
        if existing_dv_tool_ft is not None:
            facts["dv_tool_ft"] = wrap(existing_dv_tool_ft)
        
        # Add retainer tools if found
        if retainer_tools:
            facts["retainer_tools"] = retainer_tools
            logger.info(f"🔧 Added {len(retainer_tools)} retainer tools to facts: {retainer_tools}")
        
        # Add historic cement jobs from W-15 if found
        if historic_cement_jobs:
            facts["historic_cement_jobs"] = historic_cement_jobs
            logger.info(f"🧱 Added {len(historic_cement_jobs)} historic cement jobs to facts")
        
        # Add KOP data for horizontal well CIBP placement
        if kop_md_ft is not None or kop_tvd_ft is not None:
            facts["kop"] = {
                "kop_md_ft": kop_md_ft,
                "kop_tvd_ft": kop_tvd_ft
            }
        
        # Add schematic annular gap data for perforate & squeeze detection
        # This is the PRIMARY source of truth for annular isolation requirements
        logger.critical(f"🔥 w3a: REACHED SCHEMATIC CHECKING CODE - schematic={bool(schematic)}, has_gaps={bool(schematic and schematic.get('annular_gaps'))}")
        logger.critical(f"🔥 w3a: About to check IF condition")
        if schematic and schematic.get('annular_gaps'):
            logger.critical(f"🔥 w3a: INSIDE IF BLOCK - schematic has annular_gaps")
            annular_gaps = schematic.get('annular_gaps', [])
            logger.critical(f"🔥 w3a: Found {len(annular_gaps)} total annular gaps in schematic")
            # Filter for gaps that require isolation
            gaps_requiring_isolation = [
                gap for gap in annular_gaps 
                if gap.get('requires_isolation') and not gap.get('cement_present')
            ]
            logger.critical(f"🔥 w3a: {len(gaps_requiring_isolation)} gaps require isolation after filtering")
            if gaps_requiring_isolation:
                facts["annular_gaps"] = gaps_requiring_isolation
                logger.critical(
                    f"🔥 w3a: SUCCESSFULLY ADDED {len(gaps_requiring_isolation)} annular gaps to facts for kernel! ✅"
                )
            else:
                logger.critical(f"🔥 w3a: No gaps requiring isolation after filter")
        else:
            logger.critical(f"🔥 w3a: FAILED IF CHECK - schematic data: {list(schematic.keys()) if schematic else 'None'}")
        
        if gau_protect_intervals:
            facts["gau_protect_intervals"] = gau_protect_intervals
        if intermediate_shoe_ft is not None:
            try:
                if float(intermediate_shoe_ft) >= 1500.0:
                    facts["intermediate_shoe_ft"] = wrap(float(intermediate_shoe_ft))
            except Exception:
                pass
        if prod_iv is not None:
            facts["producing_interval_ft"] = wrap(prod_iv)
        
        # Add perforations from W-2 producing intervals to facts for CIBP placement logic
        if perforations_from_w2:
            facts["perforations"] = perforations_from_w2
            logger.info(f"🔫 Added {len(perforations_from_w2)} perforation intervals to facts for kernel CIBP placement")
            
            # Calculate shallowest perforation for CIBP placement (50 ft shallower than shallowest perf)
            shallowest_perf_top = min(p.get("interval_top_ft", float('inf')) for p in perforations_from_w2 if p.get("interval_top_ft"))
            if shallowest_perf_top != float('inf'):
                logger.info(f"   Shallowest perforation top: {shallowest_perf_top} ft")
                logger.info(f"   CIBP should be placed 50 ft shallower: {shallowest_perf_top - 50.0} ft")
        
        if formation_tops_map:
            facts["formation_tops_map"] = formation_tops_map
        if production_shoe_ft is None and deepest_shoe_any_ft is not None:
            production_shoe_ft = deepest_shoe_any_ft
        if production_shoe_ft is not None:
            try:
                facts["production_shoe_ft"] = wrap(float(production_shoe_ft))
            except Exception:
                pass
        
        # Add TOC (Top of Cement) for all strings for plug type determination
        if production_casing_toc_ft is not None:
            facts["production_casing_toc_ft"] = wrap(float(production_casing_toc_ft))
            logger.info(f"🎯 Added production_casing_toc_ft to facts: {production_casing_toc_ft} ft")
        
        if intermediate_casing_toc_ft is not None:
            facts["intermediate_casing_toc_ft"] = wrap(float(intermediate_casing_toc_ft))
            logger.info(f"🎯 Added intermediate_casing_toc_ft to facts: {intermediate_casing_toc_ft} ft")
        
        if surface_casing_toc_ft is not None:
            facts["surface_casing_toc_ft"] = wrap(float(surface_casing_toc_ft))
            logger.info(f"🎯 Added surface_casing_toc_ft to facts: {surface_casing_toc_ft} ft")

        field_name = facts["field"]["value"] or None
        district_val = facts["district"]["value"]
        county_val = facts["county"]["value"] or None
        
        print(f"🔍 GETTING POLICY: district={district_val}, county={county_val}, field={field_name}", flush=True)
        logger.critical(f"🔍 GETTING POLICY: district={district_val}, county={county_val}, field={field_name}")
        
        # get_effective_policy returns the effective policy directly (not wrapped)
        try:
            effective_policy_result = get_effective_policy(district=district_val, county=county_val, field=field_name)
            logger.info(f"✅ get_effective_policy returned successfully")
        except Exception as e:
            logger.exception(f"Failed to load policy: {e}")
            # Return empty policy with error
            effective_policy_result = {}
        
        # DEBUG: Verify formation_tops are loaded from the raw result
        dist_overrides = effective_policy_result.get("district_overrides") or {}
        formation_tops = dist_overrides.get("formation_tops") or []
        
        if formation_tops:
            logger.info(f"✅ POLICY: Found {len(formation_tops)} formation tops: {[ft.get('formation') for ft in formation_tops]}")
        else:
            logger.warning(f"⚠️ POLICY: No formation_tops found for {county_val} / {field_name}")
        
        # CRITICAL: Wrap effective_policy result in the structure expected by plan_from_facts
        # The kernel expects policy["effective"]["district_overrides"], not policy["district_overrides"]
        policy = {
            "policy_id": "tx.w3a",
            "complete": True,
            "effective": effective_policy_result,  # <-- Nested under "effective"
            "preferences": effective_policy_result.get("preferences", {}),  # <-- Also at top level for easy access
        }
        
        # Override/augment preferences
        prefs = policy["preferences"]
        prefs["rounding_policy"] = "nearest"
        prefs.setdefault("default_recipe", {
            "id": "class_h_neat_15_8",
            "class": "H",
            "density_ppg": 15.8,
            "yield_ft3_per_sk": 1.18,
            "water_gal_per_sk": 5.2,
            "additives": [],
        })
        # Long-plug merge preference based on request
        # NEW: Sack-based merging with tag-aware limits
        prefs.setdefault("long_plug_merge", {})
        prefs["long_plug_merge"]["enabled"] = bool(merge_enabled)
        prefs["long_plug_merge"]["threshold_ft"] = float(merge_threshold_ft)  # Deprecated, kept for compat
        prefs["long_plug_merge"]["sack_limit_no_tag"] = float(sack_limit_no_tag)  # Max sacks WITHOUT tag (default 50)
        prefs["long_plug_merge"]["sack_limit_with_tag"] = float(sack_limit_with_tag)  # Max sacks WITH tag (default 150)
        prefs["long_plug_merge"].setdefault("types", ["formation_top_plug", "cement_plug", "uqw_isolation_plug"])
        prefs["long_plug_merge"].setdefault("preserve_tagging", True)
        
        logger.info(
            f"🎯 Merge config: enabled={merge_enabled}, "
            f"sack_limit_no_tag={sack_limit_no_tag}, sack_limit_with_tag={sack_limit_with_tag}"
        )

        # Map OD to nominal ID for common casing sizes (inches)
        NOMINAL_ID = {
            13.375: 12.515,  # 13 3/8" intermediate
            11.75: 10.965,   # 11 3/4" intermediate  
            10.625: 10.2,    # Not commonly used
            9.625: 8.681,    # 9 5/8" intermediate (47 lb/ft)
            8.625: 7.921,    # 8 5/8" production
            7.0: 6.094,      # 7" production
            5.5: 4.778       # 5 1/2" production
        }

        def _nominal_id(size_txt: Any) -> Optional[float]:
            val = _parse_size(size_txt)
            if not val:
                return None
            if val in NOMINAL_ID:
                return NOMINAL_ID[val]
            for k in NOMINAL_ID.keys():
                if abs(val - k) < 0.02:
                    return NOMINAL_ID[k]
            return None

        def _parse_size(txt: Any) -> Optional[float]:
            if txt is None:
                return None
            if isinstance(txt, (int, float)):
                return float(txt)
            t = str(txt).strip().replace('"', '')
            if ' ' in t:
                parts = t.split()
                try:
                    whole = float(parts[0])
                except Exception:
                    return None
                frac = 0.0
                if len(parts) > 1 and '/' in parts[1]:
                    try:
                        num, den = parts[1].split('/')
                        frac = float(num) / float(den)
                    except Exception:
                        frac = 0.0
                return whole + frac
            try:
                return float(t)
            except Exception:
                return None

        surface_id = _nominal_id(surface_size_in)
        intermediate_id = _nominal_id(intermediate_size_in)
        prod_id = _nominal_id(prod_size_in)
        # Tubing/stinger from W-2 tubing_record
        stinger_od_in = None
        tr = (w2.get("tubing_record") or [])
        if tr:
            stinger_od_in = _parse_size(tr[0].get("size_in"))

        gdefs = policy.setdefault("preferences", {}).setdefault("geometry_defaults", {})
        if surface_id and stinger_od_in:
            gdefs.setdefault("surface_casing_shoe_plug", {}).update({
                "casing_id_in": surface_id,
                "stinger_od_in": stinger_od_in,
                "annular_excess": 0.4,
            })
            gdefs.setdefault("cased_surface", {}).update({
                "casing_id_in": surface_id,
                "stinger_od_in": stinger_od_in,
                "annular_excess": 0.4,
            })
            gdefs.setdefault("uqw_isolation_plug", {}).update({
                "casing_id_in": prod_id,  # UQW plugs run through production casing, not surface
                "stinger_od_in": stinger_od_in,
                "annular_excess": 0.4,  # Standard cased excess, matching other plug types
            })
        # Intermediate casing shoe plug geometry
        if intermediate_id and stinger_od_in:
            gdefs.setdefault("intermediate_casing_shoe_plug", {}).update({
                "casing_id_in": intermediate_id,
                "stinger_od_in": stinger_od_in,
                "annular_excess": 0.4,
            })
        
        # Perf and circulate to surface (annulus fill: intermediate OD inside surface ID)
        if surface_id and intermediate_size_in:
            gdefs.setdefault("perf_and_circulate_to_surface", {}).update({
                "outer_casing_id_in": surface_id,  # 13⅜" ID ~12.515"
                "inner_casing_od_in": float(intermediate_size_in),  # 9⅝" OD 9.625"
                "operational_topoff": 1.05,  # 5% for circulation returns
            })
        cap_id = prod_id or surface_id
        if cap_id and stinger_od_in:
            gdefs.setdefault("cibp_cap", {}).update({
                "casing_id_in": cap_id,
                "stinger_od_in": stinger_od_in,
                "annular_excess": 0.4,
            })
        if prod_id and stinger_od_in:
            gdefs.setdefault("squeeze", {}).update({
                "casing_id_in": prod_id,
                "stinger_od_in": stinger_od_in,
                "interval_ft": 100.0,
                "squeeze_factor": 0.4,
                "annular_excess": 0.4,
            })
            gdefs.setdefault("cement_plug", {}).update({
                "casing_id_in": prod_id,
                "stinger_od_in": stinger_od_in,
                "annular_excess": 0.4,
            })
            gdefs.setdefault("formation_top_plug", {}).update({
                "casing_id_in": prod_id,
                "stinger_od_in": stinger_od_in,
                "annular_excess": 0.4,
            })

        logger.critical(f"🔥🔥🔥 ABOUT TO CALL KERNEL - facts has annular_gaps: {bool(facts.get('annular_gaps'))}, count: {len(facts.get('annular_gaps', []))}")
        out_kernel = plan_from_facts(facts, policy)

        # Summarize output similar to management command
        def _step_summary(s: Dict[str, Any]) -> Dict[str, Any]:
            # Normalize depth field names for consistency
            top = s.get("top_ft") or s.get("top") or s.get("depth_ft")
            bottom = s.get("bottom_ft") or s.get("base_ft") or s.get("base") or s.get("depth_ft")
            
            # For merged plugs, extract min/max from merged_steps if top-level depths are missing
            details = s.get("details") or {}
            if details.get("merged") and (top is None or bottom is None):
                merged_steps = details.get("merged_steps", [])
                if merged_steps:
                    all_tops = []
                    all_bottoms = []
                    for ms in merged_steps:
                        if ms.get("top_ft") is not None:
                            all_tops.append(float(ms["top_ft"]))
                        if ms.get("bottom_ft") is not None:
                            all_bottoms.append(float(ms["bottom_ft"]))
                    
                    if all_tops and top is None:
                        top = max(all_tops)  # Shallowest top
                    if all_bottoms and bottom is None:
                        bottom = min(all_bottoms)  # Deepest bottom
            
            out_s = {
                "type": s.get("type"),
                "plug_type": s.get("plug_type"),  # Mechanical type (spot, perf & squeeze, etc.)
                "plug_purpose": s.get("plug_purpose"),  # NEW: Original purpose (formation_top_plug, bridge_plug, etc.)
                "top": top,  # Consistent field name for AI tools
                "base": bottom,  # Consistent field name for AI tools
                "top_ft": top,  # Keep legacy field for backward compat
                "bottom_ft": bottom,  # Keep legacy field for backward compat
                "sacks": ((s.get("materials") or {}).get("slurry") or {}).get("sacks") or s.get("sacks"),
                "tag_required": s.get("tag_required"),  # Whether TAG (WOC) is required
                "regulatory_basis": s.get("regulatory_basis"),
                "special_instructions": s.get("special_instructions"),
                "details": details,
            }
            
            # Build display_name with TAG suffix if required
            step_type = s.get("type")
            formation = s.get("formation", "")
            purpose_name = step_type
            
            # Map step type to display name
            if step_type == "formation_top_plug":
                purpose_name = f"Formation isolation ({formation})" if formation else "Formation top isolation"
            elif step_type == "uqw_isolation_plug":
                purpose_name = "UQW isolation"
            elif step_type == "intermediate_casing_shoe_plug":
                purpose_name = "Intermediate shoe isolation"
            elif step_type == "surface_casing_shoe_plug":
                purpose_name = "Surface shoe isolation"
            elif step_type == "perf_and_circulate_to_surface":
                purpose_name = "Annulus circulation to surface"
            elif step_type == "productive_horizon_isolation_plug":
                purpose_name = "Productive horizon isolation"
            elif step_type == "cibp_cap":
                purpose_name = "CIBP cap"
            elif step_type == "top_plug":
                purpose_name = "Surface safety plug"
            
            # Get plug type for display
            plug_type = s.get("plug_type")
            plug_type_display = ""
            if plug_type == "spot_plug":
                plug_type_display = "Spot plug"
            elif plug_type == "perf_and_squeeze_plug":
                plug_type_display = "Perf & squeeze"
            elif plug_type == "perf_and_circulate_plug":
                plug_type_display = "Perf & circulate"
            elif plug_type == "dumbell_plug":
                plug_type_display = "Dumbell"
            
            # Build display name: "PlugType - Purpose" with TAG suffix if required
            display_name = f"{plug_type_display} - {purpose_name}" if plug_type_display else purpose_name
            
            if s.get("tag_required") is True:
                # Add WOC (Wait On Cement) and Tag requirement
                woc_hours = ((s.get("details") or {}).get("verification") or {}).get("required_wait_hr", 4)
                display_name += f" - WOC {int(woc_hours)} Hours and Tag"
            
            out_s["display_name"] = display_name
            
            if s.get("type") == "cement_plug":
                out_s["cement_class"] = s.get("cement_class")
                out_s["depth_mid_ft"] = s.get("depth_mid_ft")
            try:
                m = ((s.get("materials") or {}).get("slurry") or {})
                if isinstance(m.get("explain"), dict):
                    out_s.setdefault("details", {})["materials_explain"] = m.get("explain")
            except Exception:
                pass
            try:
                if s.get("type") == "squeeze" and isinstance(out_s.get("details", {}).get("materials_explain"), dict):
                    out_s["details"]["sacks_override_from"] = "W-15 cementing report"
            except Exception:
                pass
            return out_s

        county_val = facts.get("county", {}).get("value") if isinstance(facts.get("county"), dict) else facts.get("county")
        field_val = facts.get("field", {}).get("value") if isinstance(facts.get("field"), dict) else facts.get("field")
        tops_map = facts.get("formation_tops_map") or {}
        detected_formations = sorted(list(tops_map.keys())) if isinstance(tops_map, dict) else []
        targeted_formations: List[str] = []
        try:
            for s in out_kernel.get("steps", []) or []:
                fm = s.get("formation")
                if isinstance(fm, str):
                    targeted_formations.append(fm)
                bases = s.get("regulatory_basis") or []
                if isinstance(bases, list):
                    for b in bases:
                        if isinstance(b, str) and ":formation_top:" in b:
                            targeted_formations.append(b.split(":formation_top:", 1)[1])
                        if isinstance(b, str) and ":mid." in b:
                            targeted_formations.append(b.split(":mid.", 1)[1])
        except Exception:
            pass
        targeted_formations = sorted(list({str(x) for x in targeted_formations if x}))

        plan_notes = []
        try:
            if facts.get("existing_cibp_ft", {}).get("value"):
                plan_notes.append(f"Existing CIBP at {int(float(facts['existing_cibp_ft']['value']))} ft – tag and cap only; do not drill out.")
            if facts.get("dv_tool_ft", {}).get("value"):
                plan_notes.append(f"DV tool isolation considered at {int(float(facts['dv_tool_ft']['value']))} ft.")
            sqz_ov = (policy.get("effective") or {}).get("steps_overrides", {}).get("squeeze_via_perf") or {}
            if isinstance(sqz_ov.get("interval_ft"), list) and len(sqz_ov["interval_ft"]) == 2:
                t_s, b_s = sqz_ov["interval_ft"][0], sqz_ov["interval_ft"][1]
                sxs = sqz_ov.get("sacks_override")
                if sxs:
                    plan_notes.append(f"Squeeze interval {int(t_s)}–{int(b_s)} ft per W-15, {int(sxs)} sks applied.")
        except Exception:
            pass

        # plan-level totals for materials
        total_sacks = 0
        total_bbl = 0.0
        try:
            for s in out_kernel.get("steps", []):
                sl = ((s.get("materials") or {}).get("slurry") or {})
                if isinstance(sl.get("sacks"), (int, float)):
                    total_sacks += int(sl.get("sacks"))
                if isinstance(sl.get("total_bbl"), (int, float)):
                    total_bbl += float(sl.get("total_bbl"))
        except Exception:
            pass

        # Sort steps by depth (deepest to shallowest) and assign sequential step_ids
        # This reflects the actual plugging procedure order (work from bottom to top)
        raw_steps = out_kernel.get("steps", [])
        sorted_steps = sorted(
            raw_steps,
            key=lambda s: s.get("bottom_ft") or s.get("base_ft") or s.get("depth_ft") or 0,
            reverse=True  # Deepest first
        )
        
        # Assign step_id 1, 2, 3... in procedural order (deepest → shallowest)
        steps_with_ids = []
        for idx, step in enumerate(sorted_steps, start=1):
            summary = _step_summary(step)
            summary["step_id"] = idx
            steps_with_ids.append(summary)
        
        result: Dict[str, Any] = {
            "api": api,
            "jurisdiction": out_kernel.get("jurisdiction"),
            "district": out_kernel.get("district"),
            "county": county_val,
            "field": field_val,
            "field_resolution": policy.get("field_resolution"),
            "formation_tops_detected": detected_formations,
            "formations_targeted": targeted_formations,
            "rounding": (out_kernel.get("materials_policy") or {}).get("rounding"),
            "steps": steps_with_ids,
            "plan_notes": plan_notes or None,
            "notes": out_kernel.get("notes", {}),  # Include kernel warnings and operational notes
            "materials_totals": {
                "total_sacks": total_sacks if total_sacks > 0 else None,
                "total_bbl": round(total_bbl, 2) if total_bbl > 0 else None,
            },
            "debug_overrides": {
                "squeeze_via_perf": ((policy.get("effective") or {}).get("steps_overrides") or {}).get("squeeze_via_perf")
            },
            "rrc_export": [
                (
                    lambda s: {
                        "plug_no": idx + 1,
                        "step_id": idx + 1,  # Match the step_id from steps array
                        "type": (
                            "CIBP" if (s.get("type") == "bridge_plug") else (
                                "Dumbell (CIBP cap)" if (s.get("plug_type") == "dumbell_plug") else (
                                    "Spot plug" if (s.get("plug_type") == "spot_plug") else (
                                        "Perf & squeeze" if (s.get("plug_type") == "perf_and_squeeze_plug") else (
                                            "Perf & circulate" if (s.get("plug_type") == "perf_and_circulate_plug") else s.get("type")
                                        )
                                    )
                                )
                            )
                        ),
                        "mechanical_type": s.get("plug_type"),  # Include mechanical type for reference
                        "regulatory_purpose": s.get("type"),  # Original purpose type for reference
                        "from_ft": (s.get("bottom_ft") if s.get("bottom_ft") is not None else s.get("depth_ft")),
                        "to_ft": (s.get("top_ft") if s.get("top_ft") is not None else s.get("depth_ft")),
                        "sacks": ((s.get("materials") or {}).get("slurry") or {}).get("sacks"),
                        "cement_class": (s.get("details") or {}).get("cement_class"),
                        "wait_hours": (s.get("details") or {}).get("verification", {}).get("required_wait_hr"),
                        "tag_required": s.get("tag_required"),
                        "toc_ft": s.get("top_ft") if s.get("top_ft") is not None else s.get("depth_ft"),
                        "additional": _build_additional_operations(s),
                        "remarks": ", ".join(filter(None, [
                            ("; ".join(s.get("regulatory_basis") or []) if isinstance(s.get("regulatory_basis"), list) else None),
                            (s.get("placement_basis") or (s.get("details") or {}).get("placement_basis")),
                        ])) or None,
                    }
                )(s)
                for idx, s in enumerate(
                    sorted(
                        out_kernel.get("steps", []),
                        key=lambda x: (
                            float(
                                (x or {}).get("bottom_ft")
                                if (x or {}).get("bottom_ft") is not None
                                else (x or {}).get("depth_ft")
                                or 0.0
                            )
                        ),
                        reverse=True,
                    )
                )
            ],
            "violations": out_kernel.get("violations", []),
        }
        try:
            kv = out_kernel.get("kernel_version")
            if kv:
                result["kernel_version"] = kv
        except Exception:
            pass
        if gau_protect_intervals:
            result["gau_protect_intervals"] = gau_protect_intervals
        try:
            if uqw_source or uqw_age_days is not None or uqw_depth is not None:
                for s in result["steps"]:
                    if s.get("type") == "uqw_isolation_plug":
                        d = s.setdefault("details", {})
                        d["uqw_base_source"] = uqw_source or "none"
                        if uqw_age_days is not None:
                            d["uqw_base_age_days"] = int(uqw_age_days)
                        if uqw_depth is not None:
                            d["uqw_base_ft"] = float(uqw_depth)
                        break
        except Exception:
            pass
        
        # Add existing mechanical barriers if found
        if existing_mech_barriers:
            result["existing_mechanical_barriers"] = existing_mech_barriers
            logger.info(f"📊 Added existing mechanical barriers to response: {existing_mech_barriers}")
        
        # Add retainer tools if found
        if retainer_tools:
            result["retainer_tools"] = retainer_tools
            logger.info(f"🔧 Added {len(retainer_tools)} retainer tools to response")
        
        # Add historic cement jobs from W-15 if found
        if historic_cement_jobs:
            result["historic_cement_jobs"] = historic_cement_jobs
            logger.info(f"🧱 Added {len(historic_cement_jobs)} historic cement jobs to response")
        
        # Add KOP (Kick-off Point) data if present
        if kop_md_ft is not None or kop_tvd_ft is not None:
            result["kop"] = {
                "kop_md_ft": kop_md_ft,
                "kop_tvd_ft": kop_tvd_ft
            }
            logger.info(f"📍 Added KOP data to response: MD={kop_md_ft} ft, TVD={kop_tvd_ft} ft")
        
        return result


