"""
Plan status transition endpoints.

User-facing actions for workflow state changes:
- Modify Plan (draft -> internal_review)
- Approve Plan (internal_review -> engineer_approved)
- File Plan (engineer_approved -> filed)
"""

import logging
from typing import Optional

from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.public_core.models import PlanSnapshot

logger = logging.getLogger(__name__)


def _get_plan_snapshot(plan_id: str) -> Optional[PlanSnapshot]:
    """Get PlanSnapshot by plan_id."""
    try:
        return PlanSnapshot.objects.get(plan_id=plan_id)
    except PlanSnapshot.DoesNotExist:
        return None


def _validate_status_transition(snapshot: PlanSnapshot, new_status: str) -> tuple[bool, Optional[str]]:
    """
    Validate if status transition is allowed.
    
    Returns: (is_valid, error_message)
    """
    current = snapshot.status
    
    # Define valid transitions
    valid_transitions = {
        PlanSnapshot.STATUS_DRAFT: [
            PlanSnapshot.STATUS_INTERNAL_REVIEW,
        ],
        PlanSnapshot.STATUS_INTERNAL_REVIEW: [
            PlanSnapshot.STATUS_ENGINEER_APPROVED,
            PlanSnapshot.STATUS_DRAFT,  # can go back
        ],
        PlanSnapshot.STATUS_ENGINEER_APPROVED: [
            PlanSnapshot.STATUS_FILED,
            PlanSnapshot.STATUS_INTERNAL_REVIEW,  # can go back for revisions
        ],
        PlanSnapshot.STATUS_FILED: [
            PlanSnapshot.STATUS_UNDER_AGENCY_REVIEW,  # set by cron
            PlanSnapshot.STATUS_WITHDRAWN,  # user can withdraw
        ],
        PlanSnapshot.STATUS_UNDER_AGENCY_REVIEW: [
            PlanSnapshot.STATUS_AGENCY_APPROVED,  # set by cron
            PlanSnapshot.STATUS_AGENCY_REJECTED,  # set by cron
            PlanSnapshot.STATUS_REVISION_REQUESTED,  # set by cron
        ],
        PlanSnapshot.STATUS_AGENCY_REJECTED: [
            PlanSnapshot.STATUS_INTERNAL_REVIEW,  # can revise and resubmit
        ],
        PlanSnapshot.STATUS_REVISION_REQUESTED: [
            PlanSnapshot.STATUS_INTERNAL_REVIEW,  # can revise
        ],
    }
    
    allowed = valid_transitions.get(current, [])
    
    if new_status not in allowed:
        return False, f"Cannot transition from '{current}' to '{new_status}'. Allowed: {', '.join(allowed)}"
    
    return True, None


@api_view(['POST'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def modify_plan(request, plan_id):
    """
    Transition plan status from draft -> internal_review.
    
    POST /api/plans/{plan_id}/modify/
    
    User clicked "Modify Plan" button to start editing.
    """
    snapshot = _get_plan_snapshot(plan_id)
    if not snapshot:
        return Response(
            {"error": f"Plan {plan_id} not found"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Validate transition
    is_valid, error = _validate_status_transition(snapshot, PlanSnapshot.STATUS_INTERNAL_REVIEW)
    if not is_valid:
        return Response(
            {"error": error, "current_status": snapshot.status},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Update status
    snapshot.status = PlanSnapshot.STATUS_INTERNAL_REVIEW
    snapshot.save()  # simple-history will track user/timestamp
    
    logger.info(f"Plan {plan_id} status changed to internal_review by user {request.user.email}")
    
    return Response({
        "success": True,
        "plan_id": plan_id,
        "status": snapshot.status,
        "message": "Plan moved to internal review"
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def approve_plan(request, plan_id):
    """
    Transition plan status from internal_review -> engineer_approved.
    
    POST /api/plans/{plan_id}/approve/
    
    User clicked "Approve" button after reviewing modifications.
    """
    snapshot = _get_plan_snapshot(plan_id)
    if not snapshot:
        return Response(
            {"error": f"Plan {plan_id} not found"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Validate transition
    is_valid, error = _validate_status_transition(snapshot, PlanSnapshot.STATUS_ENGINEER_APPROVED)
    if not is_valid:
        return Response(
            {"error": error, "current_status": snapshot.status},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Update status
    snapshot.status = PlanSnapshot.STATUS_ENGINEER_APPROVED
    snapshot.save()  # simple-history will track user/timestamp
    
    logger.info(f"Plan {plan_id} approved by engineer {request.user.email}")
    
    return Response({
        "success": True,
        "plan_id": plan_id,
        "status": snapshot.status,
        "message": "Plan approved, ready to file"
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def file_plan(request, plan_id):
    """
    Transition plan status from engineer_approved -> filed.
    
    POST /api/plans/{plan_id}/file/
    
    User clicked "File" button to submit to RRC (future: actual filing logic).
    """
    snapshot = _get_plan_snapshot(plan_id)
    if not snapshot:
        return Response(
            {"error": f"Plan {plan_id} not found"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Validate transition
    is_valid, error = _validate_status_transition(snapshot, PlanSnapshot.STATUS_FILED)
    if not is_valid:
        return Response(
            {"error": error, "current_status": snapshot.status},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Update status
    snapshot.status = PlanSnapshot.STATUS_FILED
    snapshot.save()  # simple-history will track user/timestamp
    
    logger.info(f"Plan {plan_id} filed with RRC by user {request.user.email}")
    
    # TODO: Future - trigger actual RRC filing workflow here
    
    return Response({
        "success": True,
        "plan_id": plan_id,
        "status": snapshot.status,
        "message": "Plan filed with RRC (filing workflow to be implemented)"
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def get_plan_status(request, plan_id):
    """
    Get current status of a plan with history.
    
    GET /api/plans/{plan_id}/status/
    
    Returns current status and recent status changes.
    """
    snapshot = _get_plan_snapshot(plan_id)
    if not snapshot:
        return Response(
            {"error": f"Plan {plan_id} not found"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Get status history (last 10 changes)
    history_records = snapshot.history.all().order_by('-history_date')[:10]
    
    history = []
    for record in history_records:
        history.append({
            "status": record.status,
            "changed_at": record.history_date,
            "changed_by": record.history_user.email if record.history_user else None,
            "change_type": record.history_type,  # + for create, ~ for update, - for delete
        })
    
    return Response({
        "plan_id": plan_id,
        "current_status": snapshot.status,
        "kind": snapshot.kind,
        "visibility": snapshot.visibility,
        "tenant_id": str(snapshot.tenant_id) if snapshot.tenant_id else None,
        "created_at": snapshot.created_at,
        "history": history
    }, status=status.HTTP_200_OK)

