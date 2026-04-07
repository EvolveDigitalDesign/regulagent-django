from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Step type → WellComponent.ComponentType mapping
_STEP_TYPE_MAP = {
    "cement_plug": "cement_plug",
    "bridge_plug": "bridge_plug",
    "bridge_plug_cap": "cement_plug",  # cement cap on CIBP
}


def write_plan_components(well, plan_snapshot, steps: List[Dict[str, Any]], tenant_id=None):
    """
    Write WellComponent(layer='plan_proposed') records from kernel steps.
    Idempotent — skips if components already exist for this snapshot.
    """
    from apps.public_core.models import WellComponent, WellComponentSnapshot
    from apps.public_core.services.component_resolver import resolve_well_components

    # Idempotency check
    if WellComponent.objects.filter(plan_snapshot=plan_snapshot).exists():
        logger.info("write_plan_components: components already exist for snapshot %s, skipping", plan_snapshot.id)
        return

    components = []
    for i, step in enumerate(steps):
        step_type = step.get("type", "")
        component_type = _STEP_TYPE_MAP.get(step_type)
        if not component_type:
            logger.debug("write_plan_components: skipping unknown step type '%s'", step_type)
            continue

        components.append(WellComponent(
            well=well,
            component_type=component_type,
            layer=WellComponent.Layer.PLAN_PROPOSED,
            lifecycle_state=WellComponent.LifecycleState.PROPOSED_ADDITION,
            tenant_id=tenant_id,
            plan_snapshot=plan_snapshot,
            top_ft=step.get("top_ft"),
            bottom_ft=step.get("bottom_ft"),
            depth_ft=step.get("depth_ft"),
            sacks=step.get("sacks"),
            cement_class=step.get("cement_class", ""),
            sort_order=i,
            source_document_type="plan_snapshot",
            provenance={"plan_snapshot_id": str(plan_snapshot.id)},
            properties={
                k: step.get(k) for k in ("regulatory_basis", "placement_basis", "geometry_context", "cap_length_ft", "annular_excess")
                if step.get(k) is not None
            },
        ))

    if components:
        WellComponent.objects.bulk_create(components)
        logger.info("write_plan_components: created %d plan_proposed components for snapshot %s", len(components), plan_snapshot.id)

        # Create pre-plugging snapshot
        try:
            resolved = resolve_well_components(well, tenant_id=tenant_id, plan_snapshot=plan_snapshot)
            WellComponentSnapshot.objects.create(
                well=well,
                tenant_id=tenant_id,
                context=WellComponentSnapshot.SnapshotContext.PRE_PLUGGING,
                plan_snapshot=plan_snapshot,
                snapshot_data=[{
                    "component_id": str(rc.component.id),
                    "component_type": rc.component.component_type,
                    "layer": rc.effective_layer,
                    "top_ft": float(rc.component.top_ft) if rc.component.top_ft else None,
                    "bottom_ft": float(rc.component.bottom_ft) if rc.component.bottom_ft else None,
                } for rc in resolved],
                component_count=len(resolved),
            )
        except Exception:
            logger.warning("write_plan_components: snapshot creation failed", exc_info=True)


def write_execution_components(session, form_dict: Dict[str, Any]):
    """
    Write WellComponent(layer='execution_actual') from W3 wizard completion.
    Idempotent — skips if components already exist for this session.
    """
    from apps.public_core.models import WellComponent, WellComponentSnapshot
    from apps.public_core.services.component_resolver import resolve_well_components

    # Idempotency
    if WellComponent.objects.filter(wizard_session=session).exists():
        logger.info("write_execution_components: already exist for session %s", session.id)
        return

    # Resolve well — session.well may be null for NM/sundry flows
    well = session.well
    if well is None:
        from apps.public_core.models import WellRegistry
        well = WellRegistry.objects.filter(api14=session.api_number).first()
        if well is None:
            logger.warning(
                "write_execution_components: no WellRegistry found for api_number=%s, skipping",
                session.api_number,
            )
            return

    components = []
    plugs = form_dict.get("plugs", [])

    for i, plug in enumerate(plugs):
        # Determine component type from plug data
        # Most W-3 plugs are cement plugs; bridge plugs would have a specific indicator
        component_type = "cement_plug"
        # Check if this is a bridge plug (CIBP) based on plug properties
        plug_type = plug.get("plug_type", plug.get("type", ""))
        if "bridge" in str(plug_type).lower() or "cibp" in str(plug_type).lower():
            component_type = "bridge_plug"

        components.append(WellComponent(
            well=well,
            component_type=component_type,
            layer=WellComponent.Layer.EXECUTION_ACTUAL,
            lifecycle_state=WellComponent.LifecycleState.INSTALLED,
            tenant_id=session.tenant_id,
            wizard_session=session,
            top_ft=plug.get("depth_top_ft") or plug.get("top_ft"),
            bottom_ft=plug.get("depth_bottom_ft") or plug.get("bottom_ft"),
            sacks=plug.get("sacks"),
            cement_class=plug.get("cement_class", ""),
            sort_order=i,
            source_document_type="w3_wizard",
            provenance={"wizard_session_id": str(session.id), "plug_number": plug.get("plug_number")},
            properties={
                k: plug.get(k) for k in ("plug_number", "calculated_top_of_plug_ft", "slurry_weight", "method")
                if plug.get(k) is not None
            },
        ))

    if components:
        WellComponent.objects.bulk_create(components)
        logger.info(
            "write_execution_components: created %d components for session %s",
            len(components),
            session.id,
        )

        # Create post-plugging snapshot
        try:
            resolved = resolve_well_components(
                well=session.well, tenant_id=session.tenant_id, wizard_session=session
            )
            WellComponentSnapshot.objects.create(
                well=session.well,
                tenant_id=session.tenant_id,
                context=WellComponentSnapshot.SnapshotContext.POST_PLUGGING,
                wizard_session=session,
                snapshot_data=[{
                    "component_id": str(rc.component.id),
                    "component_type": rc.component.component_type,
                    "layer": rc.effective_layer,
                    "top_ft": float(rc.component.top_ft) if rc.component.top_ft else None,
                    "bottom_ft": float(rc.component.bottom_ft) if rc.component.bottom_ft else None,
                } for rc in resolved],
                component_count=len(resolved),
            )
        except Exception:
            logger.warning("write_execution_components: snapshot failed", exc_info=True)
