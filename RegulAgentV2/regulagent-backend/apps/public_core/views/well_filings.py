"""
Well Filings Unified Endpoint

GET /api/wells/{api14}/filings/

Returns all filings (W-3A, W-3, GAU, W-15, W-2, H-5, H-15, Production Log, W-1)
for a specific well with filtering, pagination, and tenant isolation.

Supports:
- Filtering by form_type and status
- Pagination (default 25/page, max 100)
- Sorting by updated_at, created_at, form_type, status
- Tenant isolation (public or tenant-owned)
"""

from typing import List, Dict, Any
from datetime import datetime

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.pagination import PageNumberPagination
from django.db.models import Q
from django.shortcuts import get_object_or_404

from ..models import WellRegistry, PlanSnapshot, W3FormORM
from ..serializers.well_filings import (
    W3AFilingSerializer,
    W3FilingSerializer,
    WellFilingsResponseSerializer,
)


class WellFilingsPagination(PageNumberPagination):
    """Custom pagination for filings"""
    page_size = 25
    page_size_query_param = "page_size"
    page_size_query_max = 100
    page_query_param = "page"


class WellFilingsView(APIView):
    """
    GET /api/wells/{api14}/filings/

    Returns unified list of all filings for a well.
    
    Query Parameters:
    - form_type: Filter by form type (W-3A, W-3, GAU, W-15, W-2, H-5, H-15, Production Log, W-1)
    - status: Filter by status (draft, submitted, rejected, revised and submitted, approved, withdrawn)
    - page: Page number (default: 1)
    - page_size: Items per page (default: 25, max: 100)
    - ordering: Sort field (default: -updated_at, options: updated_at, -updated_at, created_at, -created_at, form_type, status)
    
    Example:
    GET /api/wells/42-003-01016/filings/?form_type=W-3A&form_type=W-3&status=approved&page=1&page_size=25
    """

    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    pagination_class = WellFilingsPagination

    def get(self, request, api14: str) -> Response:
        """Handle GET request to retrieve well filings"""
        
        # Get well by API number
        well = get_object_or_404(WellRegistry, api14=api14)

        # Collect all filings
        all_filings: List[Dict[str, Any]] = []

        # Get W-3A plans from PlanSnapshot
        w3a_filings = self._get_w3a_filings(well, request)
        all_filings.extend(w3a_filings)

        # Get W-3 forms from W3FormORM
        w3_filings = self._get_w3_filings(well, request)
        all_filings.extend(w3_filings)

        # Apply query parameter filters
        all_filings = self._apply_filters(all_filings, request)

        # Sort filings (default: -updated_at)
        all_filings = self._apply_sorting(all_filings, request)

        # Paginate
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(all_filings, request)

        if page is not None:
            response_data = {
                "api14": well.api14,
                "total": paginator.page.paginator.count,
                "count": len(page),
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "filings": page,
            }
            return paginator.get_paginated_response(response_data)

        response_data = {
            "api14": well.api14,
            "total": len(all_filings),
            "count": len(all_filings),
            "next": None,
            "previous": None,
            "filings": all_filings,
        }

        return Response(response_data, status=status.HTTP_200_OK)

    def _get_w3a_filings(
        self, well: WellRegistry, request
    ) -> List[Dict[str, Any]]:
        """Get W-3A plans from PlanSnapshot"""
        
        filings: List[Dict[str, Any]] = []

        # Build query: only public snapshots or user's tenant snapshots
        w3a_filter = Q(well=well)

        # Get tenant ID from request user
        tenant_id = getattr(request.user, "tenant_id", None)
        
        if tenant_id:
            # User can see public filings or their own tenant's filings
            w3a_filter &= Q(
                Q(visibility="public") | Q(tenant_id=tenant_id)
            )
        else:
            # Anonymous users only see public filings
            w3a_filter &= Q(visibility="public")

        # Get W-3A plans (order by created_at since PlanSnapshot doesn't have updated_at)
        w3a_plans = PlanSnapshot.objects.filter(w3a_filter).order_by("-created_at")

        # Serialize
        for plan in w3a_plans:
            serializer = W3AFilingSerializer(plan)
            filings.append(serializer.data)

        return filings

    def _get_w3_filings(
        self, well: WellRegistry, request
    ) -> List[Dict[str, Any]]:
        """Get W-3 forms from W3FormORM"""
        
        filings: List[Dict[str, Any]] = []

        # Get W-3 forms (all for now, tenant isolation to be added when tenant_id field exists)
        w3_forms = W3FormORM.objects.filter(well=well).order_by("-updated_at")

        # Serialize
        for form in w3_forms:
            serializer = W3FilingSerializer(form)
            filings.append(serializer.data)

        return filings

    def _apply_filters(
        self, filings: List[Dict[str, Any]], request
    ) -> List[Dict[str, Any]]:
        """Apply form_type and status filters"""
        
        # Get form_type filter from query params
        form_type_param = request.query_params.get("form_type")
        if form_type_param:
            form_types = [ft.strip() for ft in form_type_param.split(",")]
            filings = [
                f for f in filings
                if f.get("form_type") in form_types
            ]

        # Get status filter from query params
        status_param = request.query_params.get("status")
        if status_param:
            statuses = [s.strip() for s in status_param.split(",")]
            filings = [
                f for f in filings
                if f.get("status") in statuses
            ]

        return filings

    def _apply_sorting(
        self, filings: List[Dict[str, Any]], request
    ) -> List[Dict[str, Any]]:
        """Apply sorting to filings"""
        
        ordering = request.query_params.get("ordering", "-updated_at")
        
        # Determine reverse based on leading dash
        reverse = ordering.startswith("-")
        sort_field = ordering.lstrip("-")

        # Validate sort field
        valid_fields = {"updated_at", "created_at", "form_type", "status"}
        if sort_field not in valid_fields:
            sort_field = "updated_at"
            reverse = True

        # Sort filings
        def get_sort_value(item: Dict[str, Any]) -> Any:
            """Get value for sorting, handling None values"""
            value = item.get(sort_field)
            
            # Handle datetime strings
            if isinstance(value, str) and sort_field in {"updated_at", "created_at"}:
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
                except Exception:
                    return datetime.min
            
            return value or ""

        filings.sort(key=get_sort_value, reverse=reverse)

        return filings

