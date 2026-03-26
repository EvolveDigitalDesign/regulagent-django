"""
Well component API endpoints.

Provides tenant-scoped read and write access to WellComponent records.
Tenants can:
  - List resolved components for a well (GET)
  - Add a tenant-layer component (POST)
  - Soft-delete a tenant-layer component (DELETE)
"""

import logging

from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.public_core.models import WellRegistry, WellComponent
from apps.public_core.services.component_resolver import (
    resolve_well_components,
    build_well_geometry_from_components,
)
from apps.tenant_overlay.services.engagement_tracker import track_well_interaction
from apps.tenant_overlay.views.tenant_wells import get_tenant_id_from_request

logger = logging.getLogger(__name__)


def _serialize_component(c, effective_layer=None) -> dict:
    """Serialize a WellComponent to the response shape."""
    return {
        "id": str(c.id),
        "component_type": c.component_type,
        "layer": effective_layer or c.layer,
        "lifecycle_state": c.lifecycle_state,
        "top_ft": float(c.top_ft) if c.top_ft is not None else None,
        "bottom_ft": float(c.bottom_ft) if c.bottom_ft is not None else None,
        "outside_dia_in": float(c.outside_dia_in) if c.outside_dia_in is not None else None,
        "weight_ppf": float(c.weight_ppf) if c.weight_ppf is not None else None,
        "grade": c.grade or "",
        "cement_top_ft": float(c.cement_top_ft) if c.cement_top_ft is not None else None,
        "hole_size_in": float(c.hole_size_in) if c.hole_size_in is not None else None,
        "cement_class": c.cement_class or "",
        "sacks": float(c.sacks) if c.sacks is not None else None,
        "properties": c.properties or {},
        "source_document_type": c.source_document_type or "",
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@api_view(["GET", "POST"])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def well_components_view(request, api14):
    """
    GET  /api/tenant/wells/<api14>/components/ — List resolved components for a well.
    POST /api/tenant/wells/<api14>/components/ — Add a tenant-layer component.
    """
    if request.method == "GET":
        return _list_well_components(request, api14)
    return _add_well_component(request, api14)


def _list_well_components(request, api14):
    tenant_id = get_tenant_id_from_request(request)
    if not tenant_id:
        return Response(
            {"error": "No tenant associated with user"},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        well = WellRegistry.objects.get(api14=api14)
    except WellRegistry.DoesNotExist:
        return Response(
            {"error": f"Well with API {api14} not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    resolved = resolve_well_components(well, tenant_id=tenant_id)
    geometry = build_well_geometry_from_components(well, tenant_id=tenant_id)

    components = [_serialize_component(rc.component, rc.effective_layer) for rc in resolved]

    return Response(
        {
            "api14": api14,
            "total_components": len(components),
            "components": components,
            "geometry": geometry,
        },
        status=status.HTTP_200_OK,
    )


def _add_well_component(request, api14):
    tenant_id = get_tenant_id_from_request(request)
    if not tenant_id:
        return Response(
            {"error": "No tenant associated with user"},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        well = WellRegistry.objects.get(api14=api14)
    except WellRegistry.DoesNotExist:
        return Response(
            {"error": f"Well with API {api14} not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    data = request.data
    component_type = data.get("component_type")
    valid_types = [ct.value for ct in WellComponent.ComponentType]
    if not component_type or component_type not in valid_types:
        return Response(
            {"error": f"Invalid or missing component_type. Must be one of: {valid_types}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    component = WellComponent.objects.create(
        well=well,
        component_type=component_type,
        layer=WellComponent.Layer.TENANT,
        tenant_id=tenant_id,
        lifecycle_state=WellComponent.LifecycleState.INSTALLED,
        top_ft=data.get("top_ft"),
        bottom_ft=data.get("bottom_ft"),
        outside_dia_in=data.get("outside_dia_in"),
        weight_ppf=data.get("weight_ppf"),
        grade=data.get("grade", ""),
        cement_top_ft=data.get("cement_top_ft"),
        hole_size_in=data.get("hole_size_in"),
        cement_class=data.get("cement_class", ""),
        sacks=data.get("sacks"),
        properties=data.get("properties", {}),
    )

    track_well_interaction(
        tenant_id=tenant_id,
        well=well,
        interaction_type="component_added",
        user=request.user,
        metadata_update={"component_id": str(component.id), "component_type": component_type},
    )

    logger.info(
        "Tenant %s added component %s (%s) to well %s",
        tenant_id,
        component.id,
        component_type,
        api14,
    )

    return Response(_serialize_component(component), status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def delete_well_component_view(request, api14, component_id):
    """
    DELETE /api/tenant/wells/<api14>/components/<uuid:component_id>/

    Soft-delete a tenant-layer component (sets is_archived=True).
    Only the owning tenant may delete their own components.
    """
    tenant_id = get_tenant_id_from_request(request)
    if not tenant_id:
        return Response(
            {"error": "No tenant associated with user"},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        component = WellComponent.objects.get(
            id=component_id,
            well__api14=api14,
        )
    except WellComponent.DoesNotExist:
        return Response(
            {"error": "Component not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if component.layer != WellComponent.Layer.TENANT:
        return Response(
            {"error": "Only tenant-layer components can be deleted"},
            status=status.HTTP_403_FORBIDDEN,
        )

    if component.tenant_id != tenant_id:
        return Response(
            {"error": "Component does not belong to your tenant"},
            status=status.HTTP_403_FORBIDDEN,
        )

    component.is_archived = True
    component.save(update_fields=["is_archived", "updated_at"])

    logger.info(
        "Tenant %s archived component %s (%s) on well %s",
        tenant_id,
        component_id,
        component.component_type,
        api14,
    )

    return Response(status=status.HTTP_204_NO_CONTENT)
