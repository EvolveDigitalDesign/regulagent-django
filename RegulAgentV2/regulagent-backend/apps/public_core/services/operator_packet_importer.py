"""
Operator Packet Importer

Orchestrates ingestion of an operator-supplied P&A execution packet (.docx).

Flow:
  1. Validate — format check + security scan
  2. Extract — docx_extraction.extract_pa_procedure_from_docx()
  3. Verify API — file_validation.verify_api_number()
  4. Store file — tenant-aware path via default_storage
  5. Create ExtractedDocument — source_type=operator_packet, is_validated=True
  6. WellRegistry — get_or_create, enrich from well_header
  7. Create PlanSnapshot — kind=approved, status=agency_approved, visibility=private
  8. Vectorize — non-fatal
  9. Queue kernel comparison — non-fatal
 10. Track engagement — non-fatal
 11. Return response dict

"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from django.core.files.storage import default_storage
from django.db import transaction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step format normalisation
# ---------------------------------------------------------------------------

_OPERATION_MAP = {
    "CIBP": "cibp",
    "cibp": "cibp",
    "spot_plug": "cement_plug",
    "perf_and_squeeze": "perf_squeeze",
    "casing_pull": "casing_cut",
    "surface_plug": "surface_plug",
    "topoff": "topoff",
}


# Step category classification
_PLUG_TYPES = frozenset({
    "cibp", "cement_plug", "surface_plug", "topoff", "perf_squeeze",
    "squeeze", "plug", "spot_plug", "balanced_plug",
})
_MILESTONE_TYPES = frozenset({
    "miru", "pooh_tubing_rods", "remove_rbp", "cleanout", "run_cbl",
    "pressure_test", "casing_cut", "cut_wellhead", "cbl", "pooh",
    "rig_up", "rig_down", "move_in", "move_out",
})


def _categorize_step(step_type: str) -> str:
    """Classify a step_type as 'plug', 'milestone', or 'unknown'."""
    st = (step_type or "").lower().strip()
    if st in _PLUG_TYPES:
        return "plug"
    if st in _MILESTONE_TYPES:
        return "milestone"
    return "unknown"


def _normalize_pa_steps_to_plan_format(json_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert extracted pa_procedure_steps into the canonical plan payload format.

    Maps operator-packet operation names to kernel step_type values and builds
    a full plan payload dict compatible with PlanSnapshot.payload.

    Args:
        json_data: Extracted JSON from extract_pa_procedure_from_docx()

    Returns:
        Plan payload dict: {"steps": [...], "well_header": {...},
                            "formations": [...], "casing_record": [...]}
    """
    raw_steps = json_data.get("pa_procedure_steps") or []
    steps = []
    for raw in (raw_steps if isinstance(raw_steps, list) else []):
        if not isinstance(raw, dict):
            continue
        operation = raw.get("operation") or ""
        step_type = _OPERATION_MAP.get(operation, operation.lower() or "cement_plug")
        steps.append({
            "step_number": raw.get("step_number"),
            "step_type": step_type,
            "depth_top_ft": raw.get("depth_top_ft"),
            "depth_bottom_ft": raw.get("depth_bottom_ft"),
            "sacks": raw.get("sacks"),
            "cement_class": raw.get("cement_class"),
            "description": raw.get("description"),
            "regulatory_basis": "approved_w3a",
            "contingency_notes": raw.get("contingency_notes"),
            "perf_depth_ft": raw.get("perf_depth_ft"),
            "formations_referenced": raw.get("formations_referenced"),
            "pressure_test_psi": raw.get("pressure_test_psi"),
            "pressure_test_duration_min": raw.get("pressure_test_duration_min"),
            "woc_required": raw.get("woc_required"),
            "category": _categorize_step(step_type),
        })

    return {
        "steps": steps,
        "well_header": json_data.get("well_header") or {},
        "formations": json_data.get("formation_data") or [],
        "casing_record": json_data.get("casing_record") or [],
    }


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def import_operator_packet(
    file_path: str,
    api_number: str,
    request,
    workspace=None,
    skip_security_scan: bool = False,
) -> Dict[str, Any]:
    """
    Ingest an operator P&A packet .docx and persist as an approved PlanSnapshot.

    Args:
        file_path:          Absolute path to the saved .docx file on disk.
        api_number:         Well API number in any format.
        request:            Django HTTP request (provides user + tenant context).
        workspace:          Optional workspace instance to associate the snapshot with.
        skip_security_scan: Bypass security checks (testing only).

    Returns:
        Response dict compatible with a DRF view return.
        On success: {"success": True, "extracted_document_id": ..., ...}
        On failure: {"success": False, "error": ..., "reasons": [...]}
    """
    import re
    import zipfile

    logger.info("=" * 70)
    logger.info("OPERATOR PACKET IMPORTER — api=%s file=%s", api_number, file_path)
    logger.info("=" * 70)

    # ------------------------------------------------------------------ #
    # Resolve tenant context                                               #
    # ------------------------------------------------------------------ #
    tenant_id = None
    if request and hasattr(request, "user") and request.user.is_authenticated:
        user_tenant = request.user.tenants.first()
        tenant_id = user_tenant.id if user_tenant else None
    logger.info("   tenant_id=%s", tenant_id)

    # ------------------------------------------------------------------ #
    # STEP 1: Validate — format + security scan                           #
    # ------------------------------------------------------------------ #
    logger.info("\nSTEP 1: Validation")

    fpath = Path(file_path)
    if fpath.suffix.lower() not in (".docx", ".doc"):
        return {
            "success": False,
            "error": "Invalid file type",
            "reasons": [f"Expected .docx file, got '{fpath.suffix}'"],
        }

    # Confirm it is a valid ZIP (docx is ZIP-based)
    if not zipfile.is_zipfile(str(fpath)):
        return {
            "success": False,
            "error": "Invalid file format",
            "reasons": ["File is not a valid .docx (ZIP structure check failed)"],
        }

    if not skip_security_scan:
        from apps.public_core.services.file_validation import validate_uploaded_file
        # operator packets have no API embedded yet, so skip API verify here
        scan_result = validate_uploaded_file(
            file_path=fpath,
            document_type="pa_procedure",
            expected_api=api_number,
            skip_security_scan=False,
            fuzzy_api_match=True,
            json_data=None,  # security-only pass
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        if not scan_result.is_valid:
            logger.warning("   Security scan FAILED: %s", scan_result.errors)
            return {
                "success": False,
                "error": "Validation failed",
                "reasons": scan_result.errors,
                "warnings": scan_result.warnings,
            }
        logger.info("   Security scan PASSED")

    # ------------------------------------------------------------------ #
    # STEP 2: Extract structured data                                     #
    # ------------------------------------------------------------------ #
    logger.info("\nSTEP 2: Extraction")

    from apps.public_core.services.docx_extraction import extract_pa_procedure_from_docx

    api_digits = re.sub(r"\D+", "", str(api_number or ""))
    extraction = extract_pa_procedure_from_docx(str(fpath), api_digits)
    json_data: Dict[str, Any] = extraction.json_data or {}
    image_analyses = extraction.image_analyses or []
    model_tag = extraction.model_tag or "unknown"

    logger.info(
        "   extraction complete: json_keys=%s images=%d errors=%s",
        list(json_data.keys()),
        len(image_analyses),
        extraction.errors,
    )

    if extraction.errors and not json_data:
        return {
            "success": False,
            "error": "Extraction failed",
            "reasons": extraction.errors,
        }

    # ------------------------------------------------------------------ #
    # STEP 3: Verify API number                                           #
    # ------------------------------------------------------------------ #
    logger.info("\nSTEP 3: API verification")

    from apps.public_core.services.file_validation import verify_api_number

    # pa_procedure stores api in well_header.api_number
    # Build a shim dict that verify_api_number can probe
    api_probe: Dict[str, Any] = dict(json_data)
    well_header = json_data.get("well_header") or {}
    if "well_header" in json_data and not api_probe.get("well_info"):
        api_probe["well_info"] = {
            "api": well_header.get("api_number") or well_header.get("api"),
            "api_number": well_header.get("api_number") or well_header.get("api"),
        }

    api_result = verify_api_number(api_probe, api_number, fuzzy_match=True)
    if not api_result.is_valid:
        logger.warning("   API verification FAILED: %s", api_result.errors)
        return {
            "success": False,
            "error": "API verification failed",
            "reasons": api_result.errors,
        }
    logger.info("   API verification PASSED")

    # ------------------------------------------------------------------ #
    # STEP 4: Store file via default_storage                              #
    # ------------------------------------------------------------------ #
    logger.info("\nSTEP 4: Permanent storage")

    tenant_prefix = str(tenant_id) if tenant_id else "public"
    filename = fpath.name
    storage_key = f"tenant_{tenant_prefix}/operator_packets/{api_number}/{filename}"

    try:
        with open(str(fpath), "rb") as f:
            saved_path = default_storage.save(storage_key, f)
        logger.info("   saved to storage: %s", saved_path)
    except Exception as e:
        logger.exception("   storage save failed")
        return {
            "success": False,
            "error": "File storage failed",
            "reasons": [str(e)],
        }

    # ------------------------------------------------------------------ #
    # STEP 5: Create ExtractedDocument                                    #
    # ------------------------------------------------------------------ #
    logger.info("\nSTEP 5: ExtractedDocument")

    from apps.public_core.models import WellRegistry
    from apps.public_core.models.extracted_document import ExtractedDocument

    ed = None
    try:
        with transaction.atomic():
            ed = ExtractedDocument.objects.create(
                well=None,  # back-filled after WellRegistry in step 6
                api_number=api_digits or api_number,
                document_type="pa_procedure",
                source_path=saved_path,
                model_tag=model_tag,
                status="success" if not extraction.errors else "partial",
                errors=extraction.errors,
                json_data=json_data,
                uploaded_by_tenant=tenant_id,
                source_type=ExtractedDocument.SOURCE_OPERATOR_PACKET,
                is_validated=True,
                validation_errors=[],
            )
        logger.info("   created ExtractedDocument %s", ed.id)
    except Exception as e:
        logger.exception("   ExtractedDocument creation failed")
        return {
            "success": False,
            "error": "Database error creating extraction record",
            "reasons": [str(e)],
        }

    # ------------------------------------------------------------------ #
    # STEP 6: WellRegistry — get_or_create + enrich                      #
    # ------------------------------------------------------------------ #
    logger.info("\nSTEP 6: WellRegistry")

    well = None
    try:
        api14 = api_digits.ljust(14, "0")[:14] if len(api_digits) <= 14 else api_digits[-14:]
        wh = json_data.get("well_header") or {}
        well, created = WellRegistry.objects.get_or_create(
            api14=api14,
            defaults={
                "state": "TX",
                "county": str(wh.get("county") or "")[:64],
                "operator_name": str(wh.get("operator") or "")[:128],
                "field_name": str(wh.get("field") or "")[:128],
            },
        )
        if not created:
            # Enrich blanks from well_header
            changed = False
            for attr, key in [
                ("county", "county"),
                ("operator_name", "operator"),
                ("field_name", "field"),
            ]:
                if not (getattr(well, attr) or "").strip() and wh.get(key):
                    setattr(well, attr, str(wh[key])[:128])
                    changed = True
            # lease_name / well_number — stored in WellRegistry if fields exist
            for attr, key in [
                ("lease_name", "well_name"),
                ("well_number", "api_number"),
            ]:
                if hasattr(well, attr) and not (getattr(well, attr) or "").strip() and wh.get(key):
                    setattr(well, attr, str(wh[key])[:128])
                    changed = True
            if changed:
                well.save()

        # Back-fill well on the ExtractedDocument
        ed.well = well
        ed.save(update_fields=["well"])
        logger.info("   well %s (created=%s)", well.api14, created)
    except Exception as e:
        logger.warning("   WellRegistry step failed (non-fatal): %s", e)

    # ------------------------------------------------------------------ #
    # STEP 7: Create PlanSnapshot                                         #
    # ------------------------------------------------------------------ #
    logger.info("\nSTEP 7: PlanSnapshot")

    from apps.public_core.models import PlanSnapshot

    snapshot = None
    try:
        plan_payload = _normalize_pa_steps_to_plan_format(json_data)
        plan_id = f"{api_number}:approved"

        with transaction.atomic():
            snapshot = PlanSnapshot.objects.create(
                well=well,
                plan_id=plan_id,
                kind=PlanSnapshot.KIND_APPROVED,
                status=PlanSnapshot.STATUS_AGENCY_APPROVED,
                visibility=PlanSnapshot.VISIBILITY_PRIVATE,
                payload=plan_payload,
                kernel_version="",
                policy_id="",
                overlay_id="",
                extraction_meta={
                    "import_source": "operator_packet",
                    "document_id": str(ed.id),
                },
                tenant_id=tenant_id,
                workspace=workspace,
            )
        logger.info("   created PlanSnapshot %s", snapshot.id)
    except Exception as e:
        logger.exception("   PlanSnapshot creation failed")
        return {
            "success": False,
            "error": "Database error creating plan snapshot",
            "reasons": [str(e)],
        }

    # ------------------------------------------------------------------ #
    # STEP 8: Vectorize (non-fatal)                                       #
    # ------------------------------------------------------------------ #
    logger.info("\nSTEP 8: Vectorization")
    try:
        from apps.public_core.services.openai_extraction import vectorize_extracted_document
        vectorize_extracted_document(ed)
        logger.info("   vectorization complete")
    except Exception as e:
        logger.warning("   vectorization failed (non-fatal): %s", e)

    # ------------------------------------------------------------------ #
    # STEP 9: Queue kernel comparison (non-fatal)                         #
    # ------------------------------------------------------------------ #
    logger.info("\nSTEP 9: Queue kernel comparison")
    kernel_comparison_queued = False
    try:
        from apps.public_core.tasks_kernel_comparison import run_kernel_comparison
        run_kernel_comparison.delay(
            api_number,
            str(snapshot.id),
            str(tenant_id) if tenant_id else None,
            str(workspace.id) if workspace else None,
        )
        kernel_comparison_queued = True
        logger.info("   kernel comparison task queued")
    except Exception as e:
        logger.warning("   kernel comparison queueing failed (non-fatal): %s", e)

    # ------------------------------------------------------------------ #
    # STEP 10: Track engagement (non-fatal)                               #
    # ------------------------------------------------------------------ #
    logger.info("\nSTEP 10: Engagement tracking")
    if tenant_id and well:
        try:
            from apps.tenant_overlay.services.engagement_tracker import track_well_interaction
            track_well_interaction(
                tenant_id=tenant_id,
                well=well,
                interaction_type="operator_packet_import",
                user=request.user if (request and hasattr(request, "user")) else None,
                metadata_update={
                    "document_id": str(ed.id),
                    "plan_snapshot_id": str(snapshot.id),
                    "api_number": api_number,
                },
            )
            logger.info("   engagement tracked")
        except Exception as e:
            logger.warning("   engagement tracking failed (non-fatal): %s", e)

    # ------------------------------------------------------------------ #
    # STEP 11: Return response                                            #
    # ------------------------------------------------------------------ #
    logger.info("\nOperator packet import complete.")
    return {
        "success": True,
        "extracted_document_id": str(ed.id),
        "plan_snapshot_id": str(snapshot.id),
        "api_number": api_number,
        "document_type": "pa_procedure",
        "images_analyzed": len(image_analyses),
        "kernel_comparison_queued": kernel_comparison_queued,
        "extracted_data": json_data,
        "message": "Operator P&A packet imported as approved plan.",
    }


def import_packet_headless(
    file_path: str,
    api_number: str,
    tenant_id=None,
    workspace=None,
    user_email: str = "",
) -> Dict[str, Any]:
    """
    Headless wrapper for import_operator_packet — no Django request required.

    Builds minimal context to call the importer from a Celery task
    (e.g., the W-3 Wizard plan import flow).

    Args:
        file_path:   Absolute path to the .docx file.
        api_number:  Well API number.
        tenant_id:   Tenant UUID (optional).
        workspace:   ClientWorkspace instance (optional).
        user_email:  Email of the uploading user (for audit trail).

    Returns:
        Same response dict as import_operator_packet().
    """

    class _HeadlessRequest:
        """Minimal request-like object for the operator packet importer."""
        class _User:
            is_authenticated = True

            def __init__(self, tid, email):
                self._tid = tid
                self.email = email

            @property
            def tenants(self):
                class _TenantQS:
                    def __init__(self, tid):
                        self._tid = tid
                    def first(self):
                        if self._tid is None:
                            return None
                        class _T:
                            pass
                        t = _T()
                        t.id = self._tid
                        return t
                return _TenantQS(self._tid)

        def __init__(self, tid, email):
            self.user = self._User(tid, email)

    request = _HeadlessRequest(tenant_id, user_email)
    return import_operator_packet(
        file_path=file_path,
        api_number=api_number,
        request=request,
        workspace=workspace,
    )
