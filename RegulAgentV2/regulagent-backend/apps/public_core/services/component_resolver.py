from __future__ import annotations

import logging
import uuid as _uuid
from dataclasses import dataclass, field
from typing import List, Optional

from django.db.models import Q

logger = logging.getLogger(__name__)

# Precedence weights per layer — higher wins
_LAYER_PRECEDENCE = {
    "execution_actual": 4,
    "plan_proposed": 3,
    "tenant": 2,
    "public": 1,
}


@dataclass
class ResolvedComponent:
    component: "WellComponent"   # the winning component
    effective_layer: str          # which layer won precedence
    superseded_by: Optional[_uuid.UUID] = None  # if this was overridden


def resolve_well_components(
    well,
    tenant_id=None,
    plan_snapshot=None,
    wizard_session=None,
    include_proposed: bool = True,
    include_removed: bool = False,
) -> List[ResolvedComponent]:
    """
    Resolve all WellComponent records for a well with layer-based precedence:

        execution_actual (4) > plan_proposed (3) > tenant (2) > public (1)

    Args:
        well: WellRegistry instance or api14 string
        tenant_id: UUID — when provided, tenant and plan_proposed layers are included
        plan_snapshot: PlanSnapshot instance — restricts plan_proposed to this snapshot
        wizard_session: W3WizardSession instance — enables execution_actual layer
        include_proposed: include lifecycle_state in (proposed_addition, proposed_removal)
        include_removed: include lifecycle_state == removed

    Returns:
        List[ResolvedComponent] sorted by sort_order, then top_ft (nulls last)
    """
    from apps.public_core.models import WellComponent, WellRegistry

    # 1. Resolve well to WellRegistry if an api14 string was passed
    if isinstance(well, str):
        well = WellRegistry.objects.get(api14=well)

    # 2. Build the layer / tenant filter
    layer_filter = Q(layer=WellComponent.Layer.PUBLIC)

    if tenant_id is not None:
        tenant_q = Q(tenant_id__isnull=True) | Q(tenant_id=tenant_id)
        layer_filter = Q(layer__in=[WellComponent.Layer.PUBLIC, WellComponent.Layer.TENANT]) & tenant_q

        if plan_snapshot is not None:
            layer_filter |= Q(layer=WellComponent.Layer.PLAN_PROPOSED, plan_snapshot=plan_snapshot) & tenant_q

    # Always include execution_actual if a wizard_session is provided or any exist for this well/tenant
    exec_q = Q(layer=WellComponent.Layer.EXECUTION_ACTUAL, well=well)
    if wizard_session is not None:
        exec_q &= Q(wizard_session=wizard_session)
    if tenant_id is not None:
        exec_q &= Q(tenant_id__isnull=True) | Q(tenant_id=tenant_id)

    if wizard_session is not None or WellComponent.objects.filter(exec_q).exists():
        layer_filter |= exec_q

    # 3. Fetch all non-archived components in one query
    components = list(
        WellComponent.objects.filter(layer_filter, well=well, is_archived=False)
        .select_related()
    )
    logger.debug(
        "resolve_well_components: well=%s fetched %d components",
        well.api14,
        len(components),
    )

    # 4. Build supersession map: IDs that have been superseded by a newer component
    superseded_ids: set[_uuid.UUID] = {
        c.supersedes_id for c in components if c.supersedes_id is not None
    }

    # 5. Apply precedence — for components NOT linked by explicit supersedes chain,
    #    group by (component_type, top_ft, bottom_ft) and let the highest-precedence layer win.
    #    Components in the superseded_ids set are marked as superseded regardless.
    grouped: dict[tuple, ResolvedComponent] = {}

    for component in components:
        if component.id in superseded_ids:
            # Explicitly superseded — skip (handled below when building result)
            continue

        # Include string_type in key for casing/liner to avoid collapsing different strings at same depth
        _props = component.properties or {}
        _string_key = _props.get("string_type") if component.component_type in ("casing", "liner") else None
        key = (
            component.component_type,
            component.top_ft,
            component.bottom_ft,
            _string_key,
        )
        priority = _LAYER_PRECEDENCE.get(component.layer, 0)

        if key not in grouped:
            grouped[key] = ResolvedComponent(
                component=component,
                effective_layer=component.layer,
            )
        else:
            existing_priority = _LAYER_PRECEDENCE.get(grouped[key].effective_layer, 0)
            if priority > existing_priority:
                # New component wins; the old one is implicitly superseded
                grouped[key] = ResolvedComponent(
                    component=component,
                    effective_layer=component.layer,
                )

    # Also include explicitly-superseded components when include_removed is True
    if include_removed:
        for component in components:
            if component.id in superseded_ids:
                grouped[("_superseded_", component.id, None)] = ResolvedComponent(
                    component=component,
                    effective_layer=component.layer,
                    superseded_by=None,  # actual superseding id not tracked here
                )

    # 6. Filter by lifecycle_state
    excluded_states = set()
    if not include_proposed:
        excluded_states.add(WellComponent.LifecycleState.PROPOSED_ADDITION)
        excluded_states.add(WellComponent.LifecycleState.PROPOSED_REMOVAL)
    if not include_removed:
        excluded_states.add(WellComponent.LifecycleState.REMOVED)

    results: List[ResolvedComponent] = [
        rc for rc in grouped.values()
        if rc.component.lifecycle_state not in excluded_states
    ]

    # 7. Sort: sort_order ascending, then top_ft ascending (nulls last)
    results.sort(key=lambda rc: (
        rc.component.sort_order,
        rc.component.top_ft if rc.component.top_ft is not None else float("inf"),
    ))

    logger.info(
        "resolve_well_components: well=%s resolved %d components (tenant=%s)",
        well.api14,
        len(results),
        tenant_id,
    )
    return results


def build_well_geometry_from_components(
    well,
    tenant_id=None,
    plan_snapshot=None,
    wizard_session=None,
) -> dict:
    """
    Resolve WellComponent records and map them to the well_geometry dict shape
    expected by the frontend.

    Returns the same geometry dict structure as well_geometry_builder.build_well_geometry():
    {
        "casing_strings": [...],
        "formation_tops": [...],
        "perforations": [...],
        "production_perforations": [...],
        "tubing": [...],
        "liner": [...],
        "historic_cement_jobs": [...],
        "mechanical_equipment": [...],
        "existing_tools": [...],
    }
    """
    geometry = {
        "casing_strings": [],
        "formation_tops": [],
        "perforations": [],
        "production_perforations": [],
        "tubing": [],
        "liner": [],
        "historic_cement_jobs": [],
        "mechanical_equipment": [],
        "existing_tools": [],
    }

    resolved = resolve_well_components(
        well=well,
        tenant_id=tenant_id,
        plan_snapshot=plan_snapshot,
        wizard_session=wizard_session,
        include_proposed=True,
        include_removed=False,
    )

    def _f(val):
        """Convert Decimal to float for JSON serialisation; pass None through."""
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return val

    for rc in resolved:
        c = rc.component
        cid = str(c.id)
        layer = rc.effective_layer
        ctype = c.component_type
        props = c.properties or {}

        if ctype == "casing":
            geometry["casing_strings"].append({
                "string_type": props.get("string_type", ""),
                "outside_dia_in": _f(c.outside_dia_in),
                "weight_ppf": _f(c.weight_ppf),
                "grade": c.grade or "",
                "top_ft": _f(c.top_ft),
                "bottom_ft": _f(c.bottom_ft),
                "cement_top_ft": _f(c.cement_top_ft),
                "hole_size_in": _f(c.hole_size_in),
                "_component_id": cid,
                "_layer": layer,
            })

        elif ctype == "tubing":
            geometry["tubing"].append({
                "size_in": _f(c.outside_dia_in),
                "top_ft": _f(c.top_ft),
                "bottom_ft": _f(c.bottom_ft),
                "weight_ppf": _f(c.weight_ppf),
                "_component_id": cid,
                "_layer": layer,
            })

        elif ctype == "liner":
            geometry["liner"].append({
                "size_in": _f(c.outside_dia_in),
                "top_ft": _f(c.top_ft),
                "bottom_ft": _f(c.bottom_ft),
                "cement_top_ft": _f(c.cement_top_ft),
                "weight_ppf": _f(c.weight_ppf),
                "grade": c.grade or "",
                "_component_id": cid,
                "_layer": layer,
            })

        elif ctype == "perforation":
            geometry["perforations"].append({
                "top_ft": _f(c.top_ft),
                "bottom_ft": _f(c.bottom_ft),
                "formation": props.get("formation"),
                "shot_density_spf": props.get("shot_density_spf"),
                "_component_id": cid,
                "_layer": layer,
            })

        elif ctype == "formation_top":
            geometry["formation_tops"].append({
                "formation": props.get("formation"),
                "top_ft": _f(c.top_ft),
                "_component_id": cid,
                "_layer": layer,
            })

        elif ctype == "cement_plug":
            geometry["mechanical_equipment"].append({
                "equipment_type": "cement_plug",
                "depth_ft": _f(c.top_ft),
                "top_ft": _f(c.top_ft),
                "bottom_ft": _f(c.bottom_ft),
                "sacks": _f(c.sacks),
                "cement_class": c.cement_class or "",
                "_component_id": cid,
                "_layer": layer,
            })

        elif ctype == "bridge_plug":
            geometry["existing_tools"].append({
                "tool_type": "CIBP",
                "depth_ft": _f(c.depth_ft if c.depth_ft is not None else c.top_ft),
                "source": "component",
                "_component_id": cid,
                "_layer": layer,
            })

        elif ctype == "packer":
            geometry["existing_tools"].append({
                "tool_type": "Packer",
                "depth_ft": _f(c.depth_ft if c.depth_ft is not None else c.top_ft),
                "source": "component",
                "_component_id": cid,
                "_layer": layer,
            })

        elif ctype == "retainer":
            geometry["existing_tools"].append({
                "tool_type": "Retainer",
                "depth_ft": _f(c.depth_ft if c.depth_ft is not None else c.top_ft),
                "source": "component",
                "_component_id": cid,
                "_layer": layer,
            })

        elif ctype == "dv_tool":
            geometry["existing_tools"].append({
                "tool_type": "DV Tool",
                "depth_ft": _f(c.depth_ft if c.depth_ft is not None else c.top_ft),
                "source": "component",
                "_component_id": cid,
                "_layer": layer,
            })

        elif ctype == "straddle_packer":
            geometry["existing_tools"].append({
                "tool_type": "Straddle Packer",
                "depth_ft": _f(c.depth_ft if c.depth_ft is not None else c.top_ft),
                "source": "component",
                "_component_id": cid,
                "_layer": layer,
            })

        elif ctype == "cement_job":
            geometry["historic_cement_jobs"].append({
                "job_type": props.get("job_type"),
                "interval_top_ft": _f(c.top_ft),
                "interval_bottom_ft": _f(c.bottom_ft),
                "cement_top_ft": _f(c.cement_top_ft),
                "sacks": _f(c.sacks),
                "cement_class": c.cement_class or "",
                "_component_id": cid,
                "_layer": layer,
            })

        else:
            logger.debug(
                "build_well_geometry_from_components: unhandled component_type=%s id=%s",
                ctype,
                cid,
            )

    # Populate production_perforations from perforations for frontend consistency
    geometry["production_perforations"] = list(geometry["perforations"])

    logger.info(
        "build_well_geometry_from_components: built geometry for well=%s — "
        "casing=%d tubing=%d liner=%d perfs=%d formation_tops=%d "
        "cement_plugs=%d existing_tools=%d cement_jobs=%d",
        getattr(well, "api14", well),
        len(geometry["casing_strings"]),
        len(geometry["tubing"]),
        len(geometry["liner"]),
        len(geometry["perforations"]),
        len(geometry["formation_tops"]),
        len(geometry["mechanical_equipment"]),
        len(geometry["existing_tools"]),
        len(geometry["historic_cement_jobs"]),
    )
    return geometry
