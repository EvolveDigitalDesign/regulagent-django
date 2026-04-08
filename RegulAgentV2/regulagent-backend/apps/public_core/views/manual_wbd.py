"""
Manual WBD API endpoints.

Provides tenant-scoped CRUD access to ManualWBD records.
Tenants can:
  - List their ManualWBDs (GET), filtered by api14 and diagram_type
  - Create a new ManualWBD (POST)
  - Retrieve a single ManualWBD (GET /<uuid>/)
  - Partially update a ManualWBD title/diagram_data (PATCH /<uuid>/)
  - Soft-delete a ManualWBD (DELETE /<uuid>/)
"""
import logging

from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.public_core.models.manual_wbd import ManualWBD
from apps.public_core.serializers.manual_wbd import ManualWBDSerializer, ManualWBDUpdateSerializer
from apps.tenant_overlay.views.tenant_wells import get_tenant_id_from_request

logger = logging.getLogger(__name__)


@api_view(["GET", "POST"])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def manual_wbd_list_create(request):
    """
    GET  /api/tenant/manual-wbd/  — List non-archived ManualWBDs for the tenant.
        Supports ?api14= and ?diagram_type= query params.
    POST /api/tenant/manual-wbd/  — Create a new ManualWBD.
    """
    tenant_id = get_tenant_id_from_request(request)
    if not tenant_id:
        return Response(
            {"error": "No tenant associated with user"},
            status=status.HTTP_403_FORBIDDEN,
        )

    if request.method == "GET":
        queryset = ManualWBD.objects.filter(tenant_id=tenant_id, is_archived=False)

        api14 = request.query_params.get("api14")
        if api14:
            queryset = queryset.filter(api14=api14)

        diagram_type = request.query_params.get("diagram_type")
        if diagram_type:
            queryset = queryset.filter(diagram_type=diagram_type)

        serializer = ManualWBDSerializer(queryset, many=True)
        return Response({"results": serializer.data}, status=status.HTTP_200_OK)

    # POST
    serializer = ManualWBDSerializer(data=request.data, context={"request": request})
    if serializer.is_valid():
        wbd = serializer.save()
        logger.info(
            "Tenant %s created ManualWBD %s (type=%s) for api14=%s",
            tenant_id,
            wbd.id,
            wbd.diagram_type,
            wbd.api14,
        )
        return Response(ManualWBDSerializer(wbd).data, status=status.HTTP_201_CREATED)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET", "PATCH", "DELETE"])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def manual_wbd_detail(request, wbd_id):
    """
    GET    /api/tenant/manual-wbd/<uuid>/  — Retrieve a single non-archived ManualWBD.
    PATCH  /api/tenant/manual-wbd/<uuid>/  — Partially update title/diagram_data.
    DELETE /api/tenant/manual-wbd/<uuid>/  — Soft-delete (sets is_archived=True).
    """
    tenant_id = get_tenant_id_from_request(request)
    if not tenant_id:
        return Response(
            {"error": "No tenant associated with user"},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        wbd = ManualWBD.objects.get(id=wbd_id, tenant_id=tenant_id, is_archived=False)
    except ManualWBD.DoesNotExist:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        serializer = ManualWBDSerializer(wbd)
        return Response(serializer.data, status=status.HTTP_200_OK)

    if request.method == "PATCH":
        serializer = ManualWBDUpdateSerializer(wbd, data=request.data, partial=True)
        if serializer.is_valid():
            wbd = serializer.save()
            logger.info("Tenant %s patched ManualWBD %s", tenant_id, wbd_id)
            return Response(ManualWBDSerializer(wbd).data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    # DELETE — soft delete
    wbd.is_archived = True
    wbd.save(update_fields=["is_archived"])
    logger.info("Tenant %s archived ManualWBD %s", tenant_id, wbd_id)
    return Response({"status": "archived"}, status=status.HTTP_200_OK)
