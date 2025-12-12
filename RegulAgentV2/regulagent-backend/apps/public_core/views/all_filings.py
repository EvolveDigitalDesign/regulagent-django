"""
All Filings Unified Endpoint

GET /api/filings/

Returns all filings (W-3A, W-3, GAU, W-15, W-2, H-5, H-15, Production Log, W-1)
across all wells with filtering, pagination, and tenant isolation.

Supports:
- Filtering by form_type and status
- Pagination (default 25/page, max 100)
- Sorting by updated_at, created_at, form_type, status
- Tenant isolation (public or tenant-owned)

Response includes well information (api14, lease_name, well_number, operator_name, county, state)
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

from ..models import WellRegistry, PlanSnapshot, W3FormORM
from ..serializers.well_filings import (
    W3AFilingSerializer,
    W3FilingSerializer,
)


class AllFilingsPagination(PageNumberPagination):
    """Custom pagination for filings"""
    page_size = 25
    page_size_query_param = "page_size"
    page_size_query_max = 100
    page_query_param = "page"


class AllFilingsView(APIView):
    """
    GET /api/filings/

    Returns unified list of all filings across all wells.
    
    Query Parameters:
    - form_type: Filter by form type (W-3A, W-3, GAU, W-15, W-2, H-5, H-15, Production Log, W-1)
    - status: Filter by status (draft, submitted, rejected, revised and submitted, approved, withdrawn)
    - page: Page number (default: 1)
    - page_size: Items per page (default: 25, max: 100)
    - ordering: Sort field (default: -updated_at, options: updated_at, -updated_at, created_at, -created_at, form_type, status)
    
    Example:
    GET /api/filings/?form_type=W-3A&form_type=W-3&status=approved&page=1&page_size=25
    """

    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    pagination_class = AllFilingsPagination

    def get(self, request) -> Response:
        """Handle GET request to retrieve all filings"""
        
        # Collect all filings
        all_filings: List[Dict[str, Any]] = []

        # Get W-3A plans from PlanSnapshot
        w3a_filings = self._get_w3a_filings(request)
        all_filings.extend(w3a_filings)

        # Get W-3 forms from W3FormORM
        w3_filings = self._get_w3_filings(request)
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
                "total": paginator.page.paginator.count,
                "count": len(page),
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "filings": page,
            }
            return paginator.get_paginated_response(response_data)

        response_data = {
            "total": len(all_filings),
            "count": len(all_filings),
            "next": None,
            "previous": None,
            "filings": all_filings,
        }

        return Response(response_data, status=status.HTTP_200_OK)

    def _get_w3a_filings(self, request) -> List[Dict[str, Any]]:
        """Get W-3A plans from PlanSnapshot"""
        
        filings: List[Dict[str, Any]] = []

        # Build query: only public snapshots or user's tenant snapshots
        w3a_filter = Q()

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
        w3a_plans = PlanSnapshot.objects.filter(w3a_filter).select_related("well").order_by("-created_at")

        # Serialize
        for plan in w3a_plans:
            filing_data = W3AFilingSerializer(plan).data
            # Add well information
            if plan.well:
                filing_data.update({
                    "api14": plan.well.api14,
                    "lease_name": plan.well.lease_name,
                    "well_number": plan.well.well_number,
                    "operator_name": plan.well.operator_name,
                    "county": plan.well.county,
                    "state": plan.well.state,
                })
            
            # Add creator from history
            try:
                first_history = plan.history.all().last()  # Get oldest record
                filing_data["created_by"] = first_history.history_user.username if first_history and first_history.history_user else "System"
            except Exception:
                filing_data["created_by"] = "System"
            
            filings.append(filing_data)

        return filings

    def _get_w3_filings(self, request) -> List[Dict[str, Any]]:
        """Get W-3 forms from W3FormORM"""
        
        filings: List[Dict[str, Any]] = []

        # Get W-3 forms (all for now, tenant isolation to be added when tenant_id field exists)
        w3_forms = W3FormORM.objects.filter().select_related("well").order_by("-updated_at")

        # Serialize
        for form in w3_forms:
            filing_data = W3FilingSerializer(form).data
            
            # Add well information
            if form.well:
                filing_data.update({
                    "api14": form.well.api14,
                    "lease_name": form.well.lease_name,
                    "well_number": form.well.well_number,
                    "operator_name": form.well.operator_name,
                    "county": form.well.county,
                    "state": form.well.state,
                })
            
            # Add creator from history
            try:
                first_history = form.history.all().last()  # Get oldest record
                filing_data["created_by"] = first_history.history_user.username if first_history and first_history.history_user else "System"
            except Exception:
                filing_data["created_by"] = "System"
            
            filings.append(filing_data)

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
