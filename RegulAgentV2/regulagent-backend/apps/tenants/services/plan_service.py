from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from django.db import transaction
from django.utils import timezone
from django.db.utils import ProgrammingError

from apps.tenants.models import Tenant, TenantPlan, PlanFeature, User


def get_tenant_plan(tenant: Tenant) -> Optional[TenantPlan]:
    """
    Return the TenantPlan for a tenant, if present.
    """
    try:
        # Defer 'feature_overrides' so SELECT does not include the column
        # in environments where the migration hasn't been applied yet.
        return TenantPlan.objects.select_related("plan").defer("feature_overrides").get(tenant=tenant)
    except TenantPlan.DoesNotExist:
        return None


def get_plan_features_for_plan_id(plan_id: int) -> Dict[str, Any]:
    """
    Load plan-level feature defaults (JSON) for a given plan id.
    Returns {} when no PlanFeature exists yet.
    """
    try:
        pf = PlanFeature.objects.only("features").get(plan_id=plan_id)
        return pf.features or {}
    except PlanFeature.DoesNotExist:
        return {}


def get_effective_features(tenant: Tenant) -> Dict[str, Any]:
    """
    Merge plan defaults with optional per-tenant overrides.
    Tenant overrides win on conflicts.
    """
    tenant_plan = get_tenant_plan(tenant)
    if not tenant_plan or not tenant_plan.plan_id:
        return {}

    plan_defaults = get_plan_features_for_plan_id(tenant_plan.plan_id)
    # Accessing a deferred/missing DB column can raise ProgrammingError
    try:
        overrides = tenant_plan.feature_overrides or {}
    except ProgrammingError:
        overrides = {}
    return {**plan_defaults, **overrides}


def tenant_has_feature(tenant: Tenant, feature_key: str) -> bool:
    """
    True if the tenant's effective feature set contains a truthy flag for feature_key.
    """
    features = get_effective_features(tenant)
    value = features.get(feature_key)
    if isinstance(value, bool):
        return value
    return bool(value)


def can_add_user(tenant: Tenant) -> bool:
    """
    True if the tenant can add another user (based on TenantPlan.user_limit).
    If no TenantPlan/user_limit is configured, return True.
    """
    tenant_plan = get_tenant_plan(tenant)
    if not tenant_plan or tenant_plan.user_limit is None:
        return True
    return tenant.user_set.count() < tenant_plan.user_limit


@transaction.atomic
def set_tenant_plan(
    *,
    tenant: Tenant,
    plan,
    user_limit: Optional[int] = None,
    discount: Optional[float] = None,
    sales_rep: Optional[str] = None,
    notes: Optional[str] = None,
    feature_overrides: Optional[Dict[str, Any]] = None,
    start_date=None,
    end_date=None,
) -> Tuple[TenantPlan, bool]:
    """
    Create or update the TenantPlan with commercial terms and optional overrides.
    Returns (tenant_plan, created).
    """
    if start_date is None:
        start_date = timezone.now()

    tenant_plan, created = TenantPlan.objects.select_for_update().get_or_create(
        tenant=tenant,
        defaults={
            "plan": plan,
            "start_date": start_date,
            "end_date": end_date,
            "user_limit": user_limit if user_limit is not None else 0,
            "discount": discount if discount is not None else 0.0,
            "sales_rep": sales_rep or "",
            "notes": notes,
            "feature_overrides": feature_overrides or {},
        },
    )

    if not created:
        tenant_plan.plan = plan
        if start_date is not None:
            tenant_plan.start_date = start_date
        tenant_plan.end_date = end_date
        if user_limit is not None:
            tenant_plan.user_limit = user_limit
        if discount is not None:
            tenant_plan.discount = discount
        if sales_rep is not None:
            tenant_plan.sales_rep = sales_rep
        if notes is not None:
            tenant_plan.notes = notes
        if feature_overrides is not None:
            tenant_plan.feature_overrides = feature_overrides
        tenant_plan.save()

    return tenant_plan, created


