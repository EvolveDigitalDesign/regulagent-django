"""
DRF views for the intelligence app.

All views require authentication and filter data by the authenticated user's tenant_id.
Cross-tenant RejectionPattern data is served with privacy guards (tenant_count >= 3).
"""

import logging

from django.db.models import Count, F, FloatField, Q, Value
from django.db.models.functions import Cast
from rest_framework import generics, status, views
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import (
    FilingStatusRecord,
    Recommendation,
    RecommendationInteraction,
    RejectionPattern,
    RejectionRecord,
)
from .serializers import (
    DashboardSerializer,
    FieldCheckSerializer,
    FilingStatusCreateSerializer,
    FilingStatusRecordSerializer,
    InteractionSerializer,
    RecommendationSerializer,
    RejectionRecordSerializer,
    RejectionVerifySerializer,
    TrendSerializer,
)
from .services.recommendation_engine import RecommendationEngine

logger = logging.getLogger(__name__)


def _get_tenant_id(request):
    """Extract tenant_id from the authenticated user."""
    return getattr(request.user, "tenant_id", None)


class StandardResultsPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


# =============================================================================
# Recommendations
# =============================================================================


class RecommendationListView(generics.ListAPIView):
    """
    GET /api/intelligence/recommendations/
    Query params: form_type, state, district, field_values (JSON)
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = RecommendationSerializer
    pagination_class = StandardResultsPagination

    def get_queryset(self):
        # Base queryset — engine handles scoring; return all active for filtering
        return Recommendation.objects.filter(is_active=True).select_related("pattern")

    def list(self, request, *args, **kwargs):
        form_type = request.query_params.get("form_type", "")
        state = request.query_params.get("state", "")
        district = request.query_params.get("district", "")

        # Optional JSON field_values for trigger scoring
        import json
        field_values_raw = request.query_params.get("field_values", "{}")
        try:
            field_values = json.loads(field_values_raw)
        except (ValueError, TypeError):
            field_values = {}

        engine = RecommendationEngine()
        recs = engine.get_recommendations_for_context(
            form_type=form_type,
            state=state,
            district=district,
            field_values=field_values,
        )

        page = self.paginate_queryset(recs)
        if page is not None:
            return self.get_paginated_response(page)
        return Response(recs)


class FieldCheckView(views.APIView):
    """
    POST /api/intelligence/recommendations/check-field/
    Body: {form_type, field_name, value, state, district}
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = FieldCheckSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        engine = RecommendationEngine()
        results = engine.check_field_value(
            form_type=data["form_type"],
            field_name=data["field_name"],
            value=data["value"],
            state=data.get("state", ""),
            district=data.get("district", ""),
        )
        return Response({"recommendations": results})


class RecommendationInteractView(views.APIView):
    """
    POST /api/intelligence/recommendations/{pk}/interact/
    Body: {action, field_value_at_time, dismissal_reason}
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            rec = Recommendation.objects.get(pk=pk, is_active=True)
        except Recommendation.DoesNotExist:
            return Response(
                {"detail": "Recommendation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = InteractionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        tenant_id = _get_tenant_id(request)

        RecommendationInteraction.objects.create(
            recommendation=rec,
            tenant_id=tenant_id,
            user=request.user,
            action=data["action"],
            field_value_at_time=data.get("field_value_at_time", ""),
            dismissal_reason=data.get("dismissal_reason", ""),
        )

        # Update recommendation counters
        action = data["action"]
        if action == "shown":
            rec.times_shown = F("times_shown") + 1
        elif action == "accepted":
            rec.times_accepted = F("times_accepted") + 1
        elif action == "dismissed":
            rec.times_dismissed = F("times_dismissed") + 1
        rec.save(update_fields=["times_shown", "times_accepted", "times_dismissed", "updated_at"])

        # Recalculate acceptance_rate
        rec.refresh_from_db()
        if rec.times_shown > 0:
            rec.acceptance_rate = rec.times_accepted / rec.times_shown
            rec.save(update_fields=["acceptance_rate"])

        return Response({"status": "recorded"}, status=status.HTTP_201_CREATED)


# =============================================================================
# Rejections
# =============================================================================


class RejectionListView(generics.ListAPIView):
    """GET /api/intelligence/rejections/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = RejectionRecordSerializer
    pagination_class = StandardResultsPagination

    def get_queryset(self):
        tenant_id = _get_tenant_id(self.request)
        qs = RejectionRecord.objects.select_related("filing_status", "well")
        if tenant_id:
            qs = qs.filter(tenant_id=tenant_id)
        return qs.order_by("-rejection_date", "-created_at")


class RejectionDetailView(generics.RetrieveAPIView):
    """GET /api/intelligence/rejections/{pk}/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = RejectionRecordSerializer

    def get_queryset(self):
        tenant_id = _get_tenant_id(self.request)
        qs = RejectionRecord.objects.select_related("filing_status", "well")
        if tenant_id:
            qs = qs.filter(tenant_id=tenant_id)
        return qs


class RejectionVerifyView(views.APIView):
    """
    PATCH /api/intelligence/rejections/{pk}/verify/
    Body: {parsed_issues: [...]}
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        tenant_id = _get_tenant_id(request)
        try:
            qs = RejectionRecord.objects.all()
            if tenant_id:
                qs = qs.filter(tenant_id=tenant_id)
            rejection = qs.get(pk=pk)
        except RejectionRecord.DoesNotExist:
            return Response(
                {"detail": "Rejection record not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = RejectionVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        rejection.parsed_issues = serializer.validated_data["parsed_issues"]
        rejection.parse_status = "verified"
        rejection.save(update_fields=["parsed_issues", "parse_status", "updated_at"])

        return Response(RejectionRecordSerializer(rejection).data)


# =============================================================================
# Filing Status
# =============================================================================


class FilingStatusListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/intelligence/filing-status/  — list (filtered by tenant)
    POST /api/intelligence/filing-status/  — create (automation callback)
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsPagination

    def get_serializer_class(self):
        if self.request.method == "POST":
            return FilingStatusCreateSerializer
        return FilingStatusRecordSerializer

    def get_queryset(self):
        tenant_id = _get_tenant_id(self.request)
        qs = FilingStatusRecord.objects.select_related("well")
        if tenant_id:
            qs = qs.filter(tenant_id=tenant_id)
        return qs.order_by("-status_date", "-created_at")

    def create(self, request, *args, **kwargs):
        serializer = FilingStatusCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        from apps.public_core.models import WellRegistry

        try:
            well = WellRegistry.objects.get(pk=data["well_id"])
        except WellRegistry.DoesNotExist:
            return Response(
                {"detail": "Well not found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        filing_status = FilingStatusRecord.objects.create(
            filing_id=data["filing_id"],
            form_type=data["form_type"],
            agency=data["agency"],
            tenant_id=data["tenant_id"],
            well=well,
            w3_form_id=data.get("w3_form_id"),
            plan_snapshot_id=data.get("plan_snapshot_id"),
            c103_form_id=data.get("c103_form_id"),
            state=data.get("state", ""),
            district=data.get("district", ""),
            county=data.get("county", ""),
        )

        return Response(
            FilingStatusRecordSerializer(filing_status).data,
            status=status.HTTP_201_CREATED,
        )


class FilingStatusDetailView(generics.RetrieveAPIView):
    """GET /api/intelligence/filing-status/{pk}/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = FilingStatusRecordSerializer

    def get_queryset(self):
        tenant_id = _get_tenant_id(self.request)
        qs = FilingStatusRecord.objects.select_related("well")
        if tenant_id:
            qs = qs.filter(tenant_id=tenant_id)
        return qs


# =============================================================================
# Trends & Analytics
# =============================================================================


class TrendsView(generics.ListAPIView):
    """GET /api/intelligence/trends/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = TrendSerializer
    pagination_class = StandardResultsPagination

    def get_queryset(self):
        qs = RejectionPattern.objects.filter(
            tenant_count__gte=3,  # privacy guard
        ).order_by("-is_trending", "-occurrence_count")

        form_type = self.request.query_params.get("form_type")
        state = self.request.query_params.get("state")
        if form_type:
            qs = qs.filter(form_type=form_type)
        if state:
            qs = qs.filter(state=state)

        return qs


class TrendsHeatmapView(views.APIView):
    """
    GET /api/intelligence/trends/heatmap/?form_type=w3a&state=TX
    Returns aggregate rejection rates by district/county.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        form_type = request.query_params.get("form_type", "")
        state = request.query_params.get("state", "")

        qs = RejectionPattern.objects.filter(tenant_count__gte=3)
        if form_type:
            qs = qs.filter(form_type=form_type)
        if state:
            qs = qs.filter(state=state)

        # Aggregate by district + county
        heatmap_data = (
            qs.values("district", "county", "state")
            .annotate(
                rejection_count=Count("id"),
                total_occurrences=Count("occurrence_count"),
            )
            .order_by("-rejection_count")
        )

        results = [
            {
                "state": row["state"],
                "district": row["district"],
                "county": row["county"],
                "rejection_count": row["rejection_count"],
                "total_occurrences": row["total_occurrences"],
            }
            for row in heatmap_data
        ]

        return Response({"heatmap": results})


class DashboardView(views.APIView):
    """GET /api/intelligence/dashboard/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant_id = _get_tenant_id(request)

        filing_qs = FilingStatusRecord.objects.all()
        rejection_qs = RejectionRecord.objects.all()
        if tenant_id:
            filing_qs = filing_qs.filter(tenant_id=tenant_id)
            rejection_qs = rejection_qs.filter(tenant_id=tenant_id)

        total_filings = filing_qs.count()
        total_rejections = rejection_qs.count()

        approved = filing_qs.filter(status="approved").count()
        approval_rate = (approved / total_filings * 100) if total_filings > 0 else 0.0

        # Top rejection reasons from parsed issues (aggregate by issue_category)
        from django.db.models import JSONField
        top_reasons_raw = (
            rejection_qs.filter(parse_status__in=["parsed", "verified"])
            .values("form_type", "agency")
            .annotate(count=Count("id"))
            .order_by("-count")[:5]
        )
        top_rejection_reasons = list(top_reasons_raw)

        # Trending patterns (cross-tenant, privacy-safe)
        trending_patterns = RejectionPattern.objects.filter(
            is_trending=True,
            tenant_count__gte=3,
        ).order_by("-occurrence_count")[:5]

        # Recent rejections for this tenant
        recent_rejections = rejection_qs.order_by("-created_at")[:10]

        data = {
            "total_filings": total_filings,
            "total_rejections": total_rejections,
            "approval_rate": round(approval_rate, 2),
            "top_rejection_reasons": top_rejection_reasons,
            "trending_patterns": trending_patterns,
            "recent_rejections": recent_rejections,
        }

        serializer = DashboardSerializer(data)
        return Response(serializer.data)
