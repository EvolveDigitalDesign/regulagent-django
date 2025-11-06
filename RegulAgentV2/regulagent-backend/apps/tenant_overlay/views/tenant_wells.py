"""
Tenant-aware wells API endpoints.

Provides tenant-isolated well queries and interaction history.
Tenants can ONLY query:
- Specific well(s) by API number
- Their own interaction history

Tenants CANNOT query all wells (no unauthenticated browsing).
"""

import logging
from typing import Optional
from uuid import UUID

from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.public_core.models import WellRegistry
from apps.tenant_overlay.services.engagement_tracker import get_tenant_engagement_list
from apps.tenant_overlay.serializers.tenant_wells import (
    TenantWellSerializer,
    BulkWellRequestSerializer
)

logger = logging.getLogger(__name__)


def get_tenant_id_from_request(request) -> Optional[UUID]:
    """Extract tenant_id from authenticated user."""
    if request.user.is_authenticated:
        user_tenant = request.user.tenants.first()
        return user_tenant.id if user_tenant else None
    return None


@api_view(['GET'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def get_well_by_api(request, api14):
    """
    Get a specific well by API-14 number with tenant's interaction history.
    
    GET /api/tenant/wells/{api14}/
    
    Returns:
        - Well data (public info)
        - Tenant's interaction history with this well (private to tenant)
    """
    tenant_id = get_tenant_id_from_request(request)
    
    if not tenant_id:
        return Response(
            {"error": "No tenant associated with user"},
            status=status.HTTP_403_FORBIDDEN
        )
    
    try:
        well = WellRegistry.objects.get(api14=api14)
    except WellRegistry.DoesNotExist:
        return Response(
            {"error": f"Well with API {api14} not found"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    serializer = TenantWellSerializer(well, context={'request': request})
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['POST'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def bulk_get_wells(request):
    """
    Bulk query wells by list of API numbers with tenant's interaction history.
    
    POST /api/tenant/wells/bulk/
    {
        "api_numbers": ["42123456780000", "42987654320000", ...]
    }
    
    Returns:
        - List of wells found (with tenant interaction history)
        - List of API numbers not found
    
    Limit: 100 wells per request
    """
    tenant_id = get_tenant_id_from_request(request)
    
    if not tenant_id:
        return Response(
            {"error": "No tenant associated with user"},
            status=status.HTTP_403_FORBIDDEN
        )
    
    # Validate request
    request_serializer = BulkWellRequestSerializer(data=request.data)
    if not request_serializer.is_valid():
        return Response(request_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    api_numbers = request_serializer.validated_data['api_numbers']
    
    # Query wells
    wells = WellRegistry.objects.filter(api14__in=api_numbers)
    found_apis = set(well.api14 for well in wells)
    not_found_apis = [api for api in api_numbers if api not in found_apis]
    
    # Serialize wells with tenant interaction history
    wells_serializer = TenantWellSerializer(wells, many=True, context={'request': request})
    
    logger.info(
        f"Bulk well query by tenant {tenant_id}: requested {len(api_numbers)}, "
        f"found {len(found_apis)}, not found {len(not_found_apis)}"
    )
    
    return Response({
        "wells": wells_serializer.data,
        "not_found": not_found_apis,
        "summary": {
            "requested": len(api_numbers),
            "found": len(found_apis),
            "not_found": len(not_found_apis)
        }
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def get_tenant_well_history(request):
    """
    Get all wells the authenticated tenant has interacted with.
    
    GET /api/tenant/wells/history/
    
    Query params:
        - limit: Number of wells to return (default: 50, max: 500)
        - offset: Pagination offset (default: 0)
    
    Returns:
        - List of wells tenant has interacted with
        - Ordered by most recent interaction first
        - Includes full interaction history for each well
    """
    tenant_id = get_tenant_id_from_request(request)
    
    if not tenant_id:
        return Response(
            {"error": "No tenant associated with user"},
            status=status.HTTP_403_FORBIDDEN
        )
    
    # Get pagination params
    try:
        limit = min(int(request.query_params.get('limit', 50)), 500)
        offset = int(request.query_params.get('offset', 0))
    except ValueError:
        return Response(
            {"error": "Invalid limit or offset parameter"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Get all engagements for this tenant
    engagements_qs = get_tenant_engagement_list(tenant_id)
    total_count = engagements_qs.count()
    
    # Apply pagination
    engagements = engagements_qs[offset:offset + limit]
    
    # Extract wells and serialize
    wells = [eng.well for eng in engagements]
    wells_serializer = TenantWellSerializer(wells, many=True, context={'request': request})
    
    logger.info(
        f"History query by tenant {tenant_id}: total {total_count} wells, "
        f"returned {len(wells)} (offset={offset}, limit={limit})"
    )
    
    return Response({
        "wells": wells_serializer.data,
        "pagination": {
            "total": total_count,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total_count
        }
    }, status=status.HTTP_200_OK)

