"""
Usage tracking service for billing and analytics.

Provides centralized function to record usage events across all tenant operations.
"""

import logging
from typing import Optional, Dict, Any
from uuid import UUID

from django.db import transaction
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.tenants.models import Tenant, ClientWorkspace, UsageRecord

logger = logging.getLogger(__name__)

User = get_user_model()


def track_usage(
    tenant: Tenant,
    event_type: str,
    resource_type: str = '',
    resource_id: str = '',
    workspace: Optional[ClientWorkspace] = None,
    user: Optional[User] = None,
    tokens_used: int = 0,
    processing_time_ms: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> UsageRecord:
    """
    Record a usage event for billing and analytics.

    Args:
        tenant: Tenant instance (required)
        event_type: Type of event (use UsageRecord.EVENT_* constants)
        resource_type: Type of resource involved (e.g., 'well', 'plan', 'document')
        resource_id: ID of the resource (e.g., API number, plan UUID)
        workspace: Client workspace for attribution (optional)
        user: User who triggered the event (optional)
        tokens_used: AI tokens consumed (for AI operations, default 0)
        processing_time_ms: Processing time in milliseconds (default 0)
        metadata: Additional event-specific data (optional)

    Returns:
        UsageRecord instance

    Example:
        track_usage(
            tenant=tenant,
            event_type=UsageRecord.EVENT_PLAN_GENERATED,
            resource_type='well',
            resource_id=well.api14,
            workspace=workspace,
            user=request.user,
            processing_time_ms=2500,
            metadata={'plan_type': 'W3A', 'mode': 'hybrid'}
        )

    Raises:
        Exception: If usage tracking fails (logged but does not prevent operation)
    """
    try:
        with transaction.atomic():
            usage_record = UsageRecord.objects.create(
                tenant=tenant,
                workspace=workspace,
                user=user,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                tokens_used=tokens_used,
                processing_time_ms=processing_time_ms,
                metadata=metadata or {},
            )

            logger.info(
                f"Tracked usage: tenant={tenant.slug}, workspace={workspace.name if workspace else 'N/A'}, "
                f"event={event_type}, resource={resource_type}:{resource_id}, tokens={tokens_used}, "
                f"time={processing_time_ms}ms"
            )

            return usage_record

    except Exception as e:
        # Log error but don't fail the operation
        logger.exception(
            f"Failed to track usage for tenant {tenant.slug}, event {event_type}: {e}"
        )
        raise


def get_tenant_usage_summary(
    tenant: Tenant,
    start_date=None,
    end_date=None,
    event_type: Optional[str] = None,
    workspace: Optional[ClientWorkspace] = None,
    group_by: str = 'event_type',
    user=None,
) -> Dict[str, Any]:
    """
    Get usage summary for a tenant within a date range.

    Args:
        tenant: Tenant instance
        start_date: Start of date range (optional, defaults to all time)
        end_date: End of date range (optional, defaults to now)
        event_type: Filter by specific event type (optional)
        workspace: Filter by specific workspace (optional)
        group_by: Field to group by ('event_type', 'resource_type', 'workspace', 'user', 'day')

    Returns:
        Dict with usage statistics:
        {
            'total_events': int,
            'total_tokens': int,
            'total_processing_time_ms': int,
            'breakdown': [
                {'group': str, 'count': int, 'tokens': int, 'time_ms': int},
                ...
            ]
        }
    """
    from django.db.models import Count, Sum
    from django.db.models.functions import TruncDate

    # Build queryset with filters
    queryset = UsageRecord.objects.filter(tenant=tenant)

    if start_date:
        queryset = queryset.filter(created_at__gte=start_date)
    if end_date:
        queryset = queryset.filter(created_at__lte=end_date)
    if event_type:
        queryset = queryset.filter(event_type=event_type)
    if workspace:
        queryset = queryset.filter(workspace=workspace)
    if user:
        queryset = queryset.filter(user=user)

    # Calculate totals
    totals = queryset.aggregate(
        total_events=Count('id'),
        total_tokens=Sum('tokens_used'),
        total_processing_time_ms=Sum('processing_time_ms'),
    )

    # Group by requested field
    if group_by == 'day':
        breakdown = (
            queryset.annotate(day=TruncDate('created_at'))
            .values('day')
            .annotate(
                count=Count('id'),
                tokens=Sum('tokens_used'),
                time_ms=Sum('processing_time_ms'),
            )
            .order_by('-day')
        )
        breakdown = [
            {
                'group': item['day'].isoformat() if item['day'] else None,
                'count': item['count'],
                'tokens': item['tokens'] or 0,
                'time_ms': item['time_ms'] or 0,
            }
            for item in breakdown
        ]
    elif group_by == 'workspace':
        breakdown = (
            queryset.values('workspace__name', 'workspace__id')
            .annotate(
                count=Count('id'),
                tokens=Sum('tokens_used'),
                time_ms=Sum('processing_time_ms'),
            )
            .order_by('-count')
        )
        breakdown = [
            {
                'group': item['workspace__name'] or 'No Workspace',
                'workspace_id': item['workspace__id'],
                'count': item['count'],
                'tokens': item['tokens'] or 0,
                'time_ms': item['time_ms'] or 0,
            }
            for item in breakdown
        ]
    elif group_by == 'user':
        breakdown = (
            queryset.values('user__email', 'user__id')
            .annotate(
                count=Count('id'),
                tokens=Sum('tokens_used'),
                time_ms=Sum('processing_time_ms'),
            )
            .order_by('-count')
        )
        breakdown = [
            {
                'group': item['user__email'] or f"User {item['user__id']}" if item['user__id'] else 'Anonymous',
                'count': item['count'],
                'tokens': item['tokens'] or 0,
                'time_ms': item['time_ms'] or 0,
            }
            for item in breakdown
        ]
    else:
        # Group by event_type or resource_type
        breakdown = (
            queryset.values(group_by)
            .annotate(
                count=Count('id'),
                tokens=Sum('tokens_used'),
                time_ms=Sum('processing_time_ms'),
            )
            .order_by('-count')
        )
        breakdown = [
            {
                'group': item[group_by] or 'Unknown',
                'count': item['count'],
                'tokens': item['tokens'] or 0,
                'time_ms': item['time_ms'] or 0,
            }
            for item in breakdown
        ]

    return {
        'total_events': totals['total_events'] or 0,
        'total_tokens': totals['total_tokens'] or 0,
        'total_processing_time_ms': totals['total_processing_time_ms'] or 0,
        'breakdown': breakdown,
    }


def get_monthly_token_usage(tenant: Tenant, user=None) -> Dict[str, Any]:
    """
    Get token usage and budget info for the current billing month.

    Budget is stored in TenantPlan.feature_overrides['monthly_token_budget'].
    Default budget is 5,000,000 tokens if not configured.

    Returns:
        Dict with tokens_used, monthly_budget, percentage, remaining
    """
    from django.db.models import Sum

    now = timezone.now()
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    qs = UsageRecord.objects.filter(
        tenant=tenant,
        created_at__gte=start_of_month,
        event_type=UsageRecord.EVENT_API_CALL,
    )
    if user:
        qs = qs.filter(user=user)
    total = qs.aggregate(total=Sum('tokens_used'))['total'] or 0

    # Get budget from TenantPlan.feature_overrides
    monthly_budget = 5_000_000  # default
    try:
        tenant_plan = tenant.tenantplan
        monthly_budget = tenant_plan.feature_overrides.get(
            'monthly_token_budget', 5_000_000
        )
    except Exception:
        pass  # No TenantPlan configured, use default

    return {
        'tokens_used': total,
        'monthly_budget': monthly_budget,
        'percentage': round(total / monthly_budget * 100, 1) if monthly_budget else 0,
        'remaining': max(0, monthly_budget - total),
        'billing_period_start': start_of_month.isoformat(),
    }
