"""
Celery task for kernel comparison after operator packet import.

When an operator packet is imported and a PlanSnapshot is approved,
this task generates a fresh baseline snapshot using the current kernel
and stores it for side-by-side comparison.
"""
import logging
from typing import Dict, Any

from celery import shared_task
from django.conf import settings

from apps.tenants.context import set_current_tenant
from apps.tenants.models import Tenant

logger = logging.getLogger(__name__)

# Document types that are sufficient to build a comparison plan
_SUFFICIENT_DOC_TYPES = {"w-2", "w2", "gau"}
_USEFUL_DOC_TYPES = {"w-15", "w15", "gau", "w-2", "w2"}


@shared_task(bind=True, max_retries=2)
def run_kernel_comparison(
    self,
    api_number: str,
    approved_snapshot_id: str,
    tenant_id: str,
    workspace_id: str,
) -> Dict[str, Any]:
    """
    Generate a fresh baseline PlanSnapshot for comparison against an approved snapshot.

    Triggered after an operator packet import to record what the current kernel
    would produce for the same well, enabling drift detection over time.

    Args:
        api_number: API-14 well identifier
        approved_snapshot_id: UUID of the approved PlanSnapshot to compare against
        tenant_id: UUID of the tenant who owns the approved snapshot
        workspace_id: UUID of the workspace (may be None/empty for tenant-level tasks)

    Returns:
        {"success": True, "baseline_snapshot_id": str, "comparison_ready": True}
        or
        {"skipped": True, "reason": str}
    """
    from apps.public_core.models import PlanSnapshot, WellRegistry
    from apps.public_core.models.extracted_document import ExtractedDocument
    from apps.public_core.services.w3a_orchestrator import _build_plan_helper

    logger.info(
        f"[KernelComparison] Starting for api={api_number}, "
        f"approved_snapshot={approved_snapshot_id}, tenant={tenant_id}"
    )

    try:
        # ------------------------------------------------------------------
        # 1. Load the approved snapshot (may have been deleted since queuing)
        # ------------------------------------------------------------------
        try:
            approved_snapshot = PlanSnapshot.objects.select_related("well").get(
                id=approved_snapshot_id
            )
        except PlanSnapshot.DoesNotExist:
            logger.warning(
                f"[KernelComparison] Approved snapshot {approved_snapshot_id} no longer exists — skipping"
            )
            return {
                "skipped": True,
                "reason": "approved_snapshot_deleted",
            }

        tenant = Tenant.objects.get(id=tenant_id)
        set_current_tenant(tenant)

        well = approved_snapshot.well

        # ------------------------------------------------------------------
        # 2. Check for sufficient documents (need at least a W-2 or GAU)
        # ------------------------------------------------------------------
        available_docs = ExtractedDocument.objects.filter(
            api_number=api_number,
            document_type__in=_USEFUL_DOC_TYPES,
            status="success",
        ).values_list("document_type", flat=True)

        available_types = {dt.lower() for dt in available_docs}
        has_sufficient = bool(available_types & _SUFFICIENT_DOC_TYPES)

        if not has_sufficient:
            logger.warning(
                f"[KernelComparison] Insufficient documents for api={api_number}. "
                f"Available: {available_types}. Need at least one of: {_SUFFICIENT_DOC_TYPES}"
            )
            return {
                "skipped": True,
                "reason": "insufficient_documents",
            }

        logger.info(
            f"[KernelComparison] Sufficient documents found for api={api_number}: {available_types}"
        )

        # ------------------------------------------------------------------
        # 3. Build the plan using the current kernel
        # ------------------------------------------------------------------
        plan_result = _build_plan_helper(
            api_number,
            merge_enabled=True,
            merge_threshold_ft=10,
        )

        # ------------------------------------------------------------------
        # 4. Persist a new baseline snapshot
        # ------------------------------------------------------------------
        kernel_version = getattr(settings, "KERNEL_VERSION", "v1")

        new_snapshot = PlanSnapshot.objects.create(
            well=well,
            plan_id=f"{api_number}:kernel_comparison",
            kind=PlanSnapshot.KIND_BASELINE,
            status=PlanSnapshot.STATUS_DRAFT,
            visibility=PlanSnapshot.VISIBILITY_PRIVATE,
            kernel_version=kernel_version,
            extraction_meta={
                "comparison_target": approved_snapshot_id,
                "triggered_by": "operator_packet_import",
            },
            tenant_id=tenant_id,
            payload=plan_result,
        )

        logger.info(
            f"[KernelComparison] Baseline snapshot created: {new_snapshot.id} "
            f"for api={api_number}, comparing against {approved_snapshot_id}"
        )

        return {
            "success": True,
            "baseline_snapshot_id": str(new_snapshot.id),
            "comparison_ready": True,
        }

    except Exception as exc:
        logger.exception(
            f"[KernelComparison] Error for api={api_number}, "
            f"approved_snapshot={approved_snapshot_id}: {exc}"
        )

        if self.request.retries < self.max_retries:
            countdown = 60 * (self.request.retries + 1)
            logger.info(
                f"[KernelComparison] Retrying in {countdown}s "
                f"(attempt {self.request.retries + 1}/{self.max_retries})"
            )
            raise self.retry(exc=exc, countdown=countdown)

        return {
            "success": False,
            "error": str(exc),
        }
