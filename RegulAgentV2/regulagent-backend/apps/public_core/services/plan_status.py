"""Plan status workflow service with state machine validation."""

from __future__ import annotations
import logging
from typing import Optional, Tuple
from django.db import transaction

from apps.public_core.models import PlanSnapshot
from apps.public_core.models.plan_approval import PlanApproval

logger = logging.getLogger(__name__)

# Valid state transitions
VALID_TRANSITIONS = {
    'draft': ['internal_review'],
    'internal_review': ['engineer_approved', 'draft'],
    'engineer_approved': ['filed', 'internal_review'],
    'filed': ['under_agency_review', 'withdrawn'],
    'under_agency_review': ['agency_approved', 'agency_rejected'],
    'agency_approved': [],
    'agency_rejected': ['internal_review'],
}


def is_valid_transition(from_status: str, to_status: str) -> bool:
    """Check if transition is allowed."""
    return to_status in VALID_TRANSITIONS.get(from_status, [])


def get_allowed_transitions(current_status: str) -> list:
    """Get list of allowed next statuses from current status."""
    return VALID_TRANSITIONS.get(current_status, [])


@transaction.atomic
def transition_plan_status(
    plan_snapshot,
    new_status: str,
    user=None,
    comments: str = ""
) -> Tuple[bool, Optional[str], Optional[PlanApproval]]:
    """
    Transition plan to new status with validation and audit trail.

    Args:
        plan_snapshot: PlanSnapshot to transition
        new_status: Target status
        user: User performing the transition (None for automated)
        comments: Optional comments

    Returns: (success, error_message, approval_record)
    """
    current = plan_snapshot.status

    if current == new_status:
        return False, f"Plan is already in status '{new_status}'", None

    if not is_valid_transition(current, new_status):
        allowed = VALID_TRANSITIONS.get(current, [])
        if allowed:
            return False, f"Cannot transition from {current} to {new_status}. Allowed: {allowed}", None
        return False, f"Status '{current}' is terminal. No transitions allowed.", None

    # Create approval record
    approval = PlanApproval.objects.create(
        plan_snapshot=plan_snapshot,
        from_status=current,
        to_status=new_status,
        approved_by=user,
        comments=comments,
        is_automated=(user is None)
    )

    # Update plan status
    plan_snapshot.status = new_status
    plan_snapshot.save()

    logger.info(f"Plan {plan_snapshot.plan_id}: {current} -> {new_status} by {user}")

    return True, None, approval


def get_pending_approvals(user, status: Optional[str] = None):
    """
    Get plans waiting for approval.

    Args:
        user: User to get pending approvals for
        status: Optional filter by specific status

    Returns:
        QuerySet of PlanSnapshot objects
    """
    # Get statuses that can transition
    approvable = [s for s, t in VALID_TRANSITIONS.items() if t]
    queryset = PlanSnapshot.objects.filter(status__in=approvable)

    if status:
        queryset = queryset.filter(status=status)

    # Filter by tenant if user has tenant_id
    if hasattr(user, 'tenant_id') and user.tenant_id:
        queryset = queryset.filter(tenant_id=user.tenant_id)

    return queryset.order_by('created_at')


def get_approval_history(plan_snapshot, limit: int = 10) -> list:
    """Get approval history for a plan."""
    approvals = plan_snapshot.approvals.all()[:limit]
    return [
        {
            'id': a.id,
            'from_status': a.from_status,
            'to_status': a.to_status,
            'approved_by': a.approved_by.email if a.approved_by else 'System',
            'approved_at': a.approved_at,
            'comments': a.comments,
            'is_automated': a.is_automated,
        }
        for a in approvals
    ]


def can_user_approve(user, plan_snapshot) -> bool:
    """
    Check if user has permission to approve this plan.

    Args:
        user: User to check
        plan_snapshot: Plan to approve

    Returns:
        True if user can approve
    """
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    # Check tenant isolation
    if hasattr(user, 'tenant_id') and hasattr(plan_snapshot, 'tenant_id'):
        if plan_snapshot.tenant_id and str(user.tenant_id) != str(plan_snapshot.tenant_id):
            return False

    # Check if plan has valid transitions
    if not get_allowed_transitions(plan_snapshot.status):
        return False

    return user.is_staff
