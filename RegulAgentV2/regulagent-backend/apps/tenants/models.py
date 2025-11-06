from __future__ import annotations

from django.db import models
from django_tenants.models import TenantMixin, DomainMixin
from tenant_users.tenants.models import TenantBase
from tenant_users.tenants.models import UserProfile as TenantUser


class User(TenantUser):
    """
    Custom user model that extends TenantUser from django-tenant-users.
    This model supports multi-tenancy with tenant-scoped permissions.
    """
    # Add any custom fields here if needed
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


