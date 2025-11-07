from __future__ import annotations

from django.db import models
from django.utils import timezone
from django_tenants.models import TenantMixin, DomainMixin
from tenant_users.tenants.models import TenantBase
from tenant_users.tenants.models import UserProfile as TenantUser


class User(TenantUser):
    """
    Custom user model that extends TenantUser from django-tenant-users.
    This model supports multi-tenancy with tenant-scoped permissions.
    """
    # Add any custom fields here if needed
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    title = models.CharField(max_length=150, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    organization = models.CharField(max_length=255, blank=True, null=True)
    
    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self) -> str:
        return self.email if self.email else self.username


class Tenant(TenantBase):
    """
    Tenant model with multi-tenant support.
    Each tenant has its own PostgreSQL schema and isolated data.
    """
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=64, unique=True)
    created_on = models.DateTimeField(auto_now_add=True)

    # Required by TenantMixin
    auto_create_schema = True
    auto_drop_schema = True  # Allows schema deletion when tenant is deleted

    class Meta:
        verbose_name = 'Tenant'
        verbose_name_plural = 'Tenants'

    def __str__(self) -> str:
        return f"Tenant<{self.slug}>"


class Domain(DomainMixin):
    """
    Domain model for routing requests to the correct tenant.
    """
    pass


class TenantPlan(models.Model):
    tenant = models.OneToOneField('Tenant', on_delete=models.CASCADE)
    plan = models.ForeignKey('plans.Plan', on_delete=models.SET_NULL, null=True)
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField(null=True, blank=True)
    user_limit = models.PositiveIntegerField()
    discount = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    sales_rep = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True, null=True)
    # Optional per-tenant overrides layered over plan defaults
    feature_overrides = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.tenant} - {self.plan} Plan"

    def has_space_for_user(self):
        current_users = self.tenant.user_set.count()
        return current_users < self.user_limit



class PlanFeature(models.Model):
    """
    Plan-level feature set stored as JSON for flexibility and easy evolution.
    Example payload keys:
      {
        "single_state": true,
        "multi_state": false,
        "auto_extraction": true,
        "actual_wellbore_diagrams": true,
        "as_plugged_diagrams": false,
        "ai_plan_mods": true,
        "regulatory_filing": true,
        "regulatory_tracking": true,
        "tenant_policies": true,
        "single_filings": true,
        "multi_filings": true,
        "estimator": true,
        "erp_integration": false
      }
    """
    plan = models.OneToOneField('plans.Plan', on_delete=models.CASCADE, related_name='features')
    features = models.JSONField(default=dict)

    class Meta:
        verbose_name = 'Plan Feature'
        verbose_name_plural = 'Plan Features'

    def __str__(self) -> str:
        return f"PlanFeature<{getattr(self.plan, 'name', str(self.plan_id))}>"

