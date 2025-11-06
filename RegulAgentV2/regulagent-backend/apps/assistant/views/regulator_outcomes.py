"""
Regulator outcome tracking endpoints.

Tracks RRC approval/rejection status for filed plans to enable
the learning feedback loop.
"""

import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.shortcuts import get_object_or_404
from django.utils import timezone

from django.db import models as django_models

from apps.assistant.models import RegulatorOutcome, PlanModification
from apps.public_core.models import PlanSnapshot
from apps.assistant.services.learning_feedback import calculate_confidence_statistics

logger = logging.getLogger(__name__)


class RegulatorOutcomeListView(APIView):
    """
    List regulator outcomes for tenant's plans.
    
    GET /api/outcomes/
    POST /api/outcomes/
    """
    
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """
        List all regulator outcomes for tenant's plans.
        
        GET /api/outcomes/?status=approved&filed_after=2025-01-01
        
        Response:
        {
          "outcomes": [
            {
              "id": 1,
              "plan_id": "4200346118:combined",
              "api": "4200346118",
              "filing_number": "W3A-2025-001234",
              "status": "approved",
              "agency": "RRC",
              "filed_at": "...",
              "approved_at": "...",
              "review_duration_days": 5,
              "confidence_score": 0.8,
              "modifications_count": 2
            }
          ],
          "summary": {
            "total": 10,
            "approved": 8,
            "rejected": 1,
            "pending": 1,
            "approval_rate": 0.8
          }
        }
        """
        user_tenant = request.user.tenants.first()
        if not user_tenant:
            return Response(
                {"error": "User not associated with any tenant"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get outcomes for tenant's plans
        outcomes = RegulatorOutcome.objects.filter(
            plan_snapshot__tenant_id=user_tenant.id
        ).select_related('plan_snapshot__well').order_by('-filed_at')
        
        # Apply filters
        status_filter = request.query_params.get('status')
        if status_filter:
            outcomes = outcomes.filter(status=status_filter)
        
        filed_after = request.query_params.get('filed_after')
        if filed_after:
            outcomes = outcomes.filter(filed_at__gte=filed_after)
        
        # Pagination
        limit = int(request.query_params.get('limit', 50))
        offset = int(request.query_params.get('offset', 0))
        
        total_count = outcomes.count()
        outcomes = outcomes[offset:offset + limit]
        
        # Serialize
        outcomes_data = []
        for outcome in outcomes:
            outcomes_data.append({
                "id": outcome.id,
                "plan_id": outcome.plan_snapshot.plan_id,
                "api": outcome.plan_snapshot.well.api14,
                "filing_number": outcome.filing_number,
                "status": outcome.status,
                "agency": outcome.agency,
                "filed_at": outcome.filed_at.isoformat() if outcome.filed_at else None,
                "reviewed_at": outcome.reviewed_at.isoformat() if outcome.reviewed_at else None,
                "approved_at": outcome.approved_at.isoformat() if outcome.approved_at else None,
                "review_duration_days": outcome.review_duration_days,
                "confidence_score": outcome.confidence_score,
                "modifications_count": outcome.influenced_by_modifications.count(),
                "revision_count": outcome.revision_count,
            })
        
        # Calculate summary stats
        all_outcomes = RegulatorOutcome.objects.filter(plan_snapshot__tenant_id=user_tenant.id)
        approved_count = all_outcomes.filter(status=RegulatorOutcome.STATUS_APPROVED).count()
        rejected_count = all_outcomes.filter(status=RegulatorOutcome.STATUS_REJECTED).count()
        pending_count = all_outcomes.filter(status__in=[
            RegulatorOutcome.STATUS_PENDING,
            RegulatorOutcome.STATUS_UNDER_REVIEW
        ]).count()
        
        total = all_outcomes.count()
        approval_rate = approved_count / total if total > 0 else 0.0
        
        return Response({
            "outcomes": outcomes_data,
            "pagination": {
                "total": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total_count
            },
            "summary": {
                "total": total,
                "approved": approved_count,
                "rejected": rejected_count,
                "pending": pending_count,
                "approval_rate": round(approval_rate, 2)
            }
        })
    
    def post(self, request):
        """
        Create a new regulator outcome (when filing plan).
        
        POST /api/outcomes/
        {
          "plan_id": "4200346118:combined",
          "filing_number": "W3A-2025-001234",
          "agency": "RRC"
        }
        """
        user_tenant = request.user.tenants.first()
        if not user_tenant:
            return Response(
                {"error": "User not associated with any tenant"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        plan_id = request.data.get('plan_id')
        filing_number = request.data.get('filing_number')
        agency = request.data.get('agency', 'RRC')
        
        if not plan_id or not filing_number:
            return Response(
                {"error": "plan_id and filing_number are required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get plan snapshot
        try:
            plan_snapshot = PlanSnapshot.objects.get(
                plan_id=plan_id,
                tenant_id=user_tenant.id
            )
        except PlanSnapshot.DoesNotExist:
            return Response(
                {"error": f"Plan {plan_id} not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Create outcome
        outcome = RegulatorOutcome.objects.create(
            plan_snapshot=plan_snapshot,
            filing_number=filing_number,
            agency=agency,
            status=RegulatorOutcome.STATUS_PENDING,
            filed_at=timezone.now()
        )
        
        # Link modifications that influenced this plan
        modifications = PlanModification.get_modification_chain(plan_snapshot)
        if modifications:
            outcome.influenced_by_modifications.set(modifications)
        
        logger.info(
            f"Created regulator outcome {outcome.id} for plan {plan_id} "
            f"(filing: {filing_number}) by user {request.user.email}"
        )
        
        return Response({
            "id": outcome.id,
            "plan_id": plan_snapshot.plan_id,
            "filing_number": outcome.filing_number,
            "status": outcome.status,
            "filed_at": outcome.filed_at.isoformat(),
            "modifications_linked": len(modifications)
        }, status=status.HTTP_201_CREATED)


class RegulatorOutcomeDetailView(APIView):
    """
    Get, update, or delete a specific regulator outcome.
    
    GET /api/outcomes/{id}/
    """
    
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    
    def get(self, request, outcome_id):
        """
        Get detailed information about a regulator outcome.
        
        Response includes modification history and timeline.
        """
        user_tenant = request.user.tenants.first()
        if not user_tenant:
            return Response(
                {"error": "User not associated with any tenant"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        outcome = get_object_or_404(
            RegulatorOutcome.objects.select_related('plan_snapshot__well'),
            id=outcome_id,
            plan_snapshot__tenant_id=user_tenant.id
        )
        
        # Get linked modifications
        modifications = []
        for mod in outcome.influenced_by_modifications.all():
            modifications.append({
                "id": mod.id,
                "op_type": mod.op_type,
                "description": mod.description,
                "risk_score": mod.risk_score,
                "is_applied": mod.is_applied,
                "created_at": mod.created_at.isoformat()
            })
        
        return Response({
            "id": outcome.id,
            "plan": {
                "plan_id": outcome.plan_snapshot.plan_id,
                "api": outcome.plan_snapshot.well.api14,
                "operator": outcome.plan_snapshot.well.operator_name,
                "field": outcome.plan_snapshot.well.field_name,
            },
            "filing": {
                "filing_number": outcome.filing_number,
                "agency": outcome.agency,
                "filed_at": outcome.filed_at.isoformat() if outcome.filed_at else None,
            },
            "status": {
                "current": outcome.status,
                "reviewed_at": outcome.reviewed_at.isoformat() if outcome.reviewed_at else None,
                "approved_at": outcome.approved_at.isoformat() if outcome.approved_at else None,
                "review_duration_days": outcome.review_duration_days,
            },
            "review": {
                "reviewer_name": outcome.reviewer_name,
                "reviewer_notes": outcome.reviewer_notes,
                "revision_count": outcome.revision_count,
                "revision_notes": outcome.revision_notes,
            },
            "learning": {
                "confidence_score": outcome.confidence_score,
                "modifications_count": len(modifications),
                "modifications": modifications,
            },
            "timestamps": {
                "created_at": outcome.created_at.isoformat(),
                "updated_at": outcome.updated_at.isoformat(),
            }
        })


@api_view(['PATCH'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def mark_outcome_approved(request, outcome_id):
    """
    Mark an outcome as approved by RRC.
    
    PATCH /api/outcomes/{id}/approve/
    {
      "reviewer_notes": "Plan meets all requirements. Approved.",
      "reviewer_name": "John Smith",
      "approved_at": "2025-11-02T10:00:00Z"  // optional
    }
    
    This triggers the learning feedback loop.
    """
    user_tenant = request.user.tenants.first()
    if not user_tenant:
        return Response(
            {"error": "User not associated with any tenant"},
            status=status.HTTP_403_FORBIDDEN
        )
    
    outcome = get_object_or_404(
        RegulatorOutcome,
        id=outcome_id,
        plan_snapshot__tenant_id=user_tenant.id
    )
    
    reviewer_notes = request.data.get('reviewer_notes', '')
    reviewer_name = request.data.get('reviewer_name', '')
    approved_at_str = request.data.get('approved_at')
    
    approved_at = None
    if approved_at_str:
        from django.utils.dateparse import parse_datetime
        approved_at = parse_datetime(approved_at_str)
    
    # Mark as approved (triggers learning loop)
    outcome.mark_approved(
        approved_at=approved_at,
        reviewer_notes=reviewer_notes
    )
    
    if reviewer_name:
        outcome.reviewer_name = reviewer_name
        outcome.save(update_fields=['reviewer_name'])
    
    logger.info(
        f"User {request.user.email} marked outcome {outcome_id} as approved "
        f"(filing: {outcome.filing_number})"
    )
    
    return Response({
        "message": "Outcome marked as approved",
        "status": outcome.status,
        "confidence_score": outcome.confidence_score,
        "review_duration_days": outcome.review_duration_days,
        "learning_triggered": True,
        "modifications_updated": outcome.influenced_by_modifications.count()
    })


@api_view(['PATCH'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def mark_outcome_rejected(request, outcome_id):
    """
    Mark an outcome as rejected by RRC.
    
    PATCH /api/outcomes/{id}/reject/
    {
      "reviewer_notes": "Formation top coverage insufficient.",
      "reviewer_name": "Jane Doe",
      "reviewed_at": "2025-11-02T10:00:00Z"  // optional
    }
    
    This triggers the learning feedback loop (negative).
    """
    user_tenant = request.user.tenants.first()
    if not user_tenant:
        return Response(
            {"error": "User not associated with any tenant"},
            status=status.HTTP_403_FORBIDDEN
        )
    
    outcome = get_object_or_404(
        RegulatorOutcome,
        id=outcome_id,
        plan_snapshot__tenant_id=user_tenant.id
    )
    
    reviewer_notes = request.data.get('reviewer_notes', '')
    reviewer_name = request.data.get('reviewer_name', '')
    reviewed_at_str = request.data.get('reviewed_at')
    
    reviewed_at = None
    if reviewed_at_str:
        from django.utils.dateparse import parse_datetime
        reviewed_at = parse_datetime(reviewed_at_str)
    
    # Mark as rejected (triggers learning loop)
    outcome.mark_rejected(
        reviewed_at=reviewed_at,
        reviewer_notes=reviewer_notes
    )
    
    if reviewer_name:
        outcome.reviewer_name = reviewer_name
        outcome.save(update_fields=['reviewer_name'])
    
    logger.info(
        f"User {request.user.email} marked outcome {outcome_id} as rejected "
        f"(filing: {outcome.filing_number})"
    )
    
    return Response({
        "message": "Outcome marked as rejected",
        "status": outcome.status,
        "confidence_score": outcome.confidence_score,
        "review_duration_days": outcome.review_duration_days,
        "learning_triggered": True,
        "modifications_updated": outcome.influenced_by_modifications.count()
    })


@api_view(['GET'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def get_outcome_statistics(request):
    """
    Get aggregate statistics about regulator outcomes.
    
    GET /api/outcomes/stats/?district=08A
    
    Response:
    {
      "total_modifications": 100,
      "total_outcomes": 30,
      "approved": 24,
      "rejected": 4,
      "approval_rate": 0.8,
      "avg_confidence": 0.72,
      "by_operation_type": {
        "combine_plugs": {"count": 10, "approval_rate": 0.9},
        ...
      },
      "avg_review_duration_days": 5.2
    }
    """
    user_tenant = request.user.tenants.first()
    if not user_tenant:
        return Response(
            {"error": "User not associated with any tenant"},
            status=status.HTTP_403_FORBIDDEN
        )
    
    district = request.query_params.get('district')
    
    # Get statistics (uses service from learning_feedback.py)
    stats = calculate_confidence_statistics(district=district)
    
    # Add tenant-specific stats
    tenant_outcomes = RegulatorOutcome.objects.filter(
        plan_snapshot__tenant_id=user_tenant.id
    )
    
    if district:
        tenant_outcomes = tenant_outcomes.filter(
            plan_snapshot__payload__district=district
        )
    
    # Calculate average review duration
    completed = tenant_outcomes.filter(
        review_duration_days__isnull=False
    )
    avg_duration = completed.aggregate(
        avg=django_models.Avg('review_duration_days')
    )['avg'] or 0.0
    
    stats['avg_review_duration_days'] = round(avg_duration, 1)
    stats['tenant_total_outcomes'] = tenant_outcomes.count()
    
    return Response(stats)

