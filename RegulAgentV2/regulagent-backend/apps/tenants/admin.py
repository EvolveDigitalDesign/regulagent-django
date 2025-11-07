"""
Django admin configuration for tenant and user management.

Based on TestDriven.io guide:
https://testdriven.io/blog/django-multi-tenant/#django-tenant-users
"""
from django.contrib import admin
from django.core.exceptions import ValidationError

from apps.tenants.models import User, Tenant, Domain, TenantPlan, PlanFeature
from apps.tenants.forms import UserAdminForm


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """
    Admin interface for User model with password hashing and validation.
    """
    form = UserAdminForm
    
    list_display = ['email', 'first_name', 'last_name', 'title', 'is_active', 'is_verified']
    list_filter = ['is_active', 'is_verified']
    search_fields = ['email', 'first_name', 'last_name', 'phone', 'organization']
    readonly_fields = ['last_login']
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('email', 'password')
        }),
        ('Personal Info', {
            'fields': ('first_name', 'last_name', 'title', 'phone', 'organization')
        }),
        ('Status', {
            'fields': ('is_active', 'is_verified')
        }),
        ('Tenants', {
            'fields': ('tenants',)
        }),
        ('Important Dates', {
            'fields': ('last_login',),
            'classes': ('collapse',)
        }),
    )
    
    filter_horizontal = ['tenants']
    
    def delete_model(self, request, obj):
        """
        Override delete to prevent deletion of tenant owners and users still in tenants.
        """
        # Check if the user owns any tenant
        if obj.id in Tenant.objects.values_list('owner_id', flat=True):
            raise ValidationError(
                "Cannot delete a user that is a tenant owner. "
                "Transfer ownership or delete the tenant first."
            )
        
        # Check if the user still belongs to any tenant
        if obj.tenants.count() > 0:
            raise ValidationError(
                "Cannot delete a user that belongs to tenants. "
                "Remove the user from all tenants first."
            )
        
        # Otherwise, delete the user
        obj.delete(force_drop=True)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    """
    Admin interface for Tenant model with schema management.
    """
    list_display = ['schema_name', 'name', 'slug', 'owner', 'created_on']
    search_fields = ['schema_name', 'name', 'slug']
    readonly_fields = ['schema_name', 'created_on']
    
    fieldsets = (
        ('Tenant Info', {
            'fields': ('schema_name', 'name', 'slug', 'owner')
        }),
        ('Settings', {
            'fields': ('created_on',)
        }),
    )
    
    def delete_model(self, request, obj):
        """
        Force delete the tenant and drop its schema.
        """
        obj.delete(force_drop=True)


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    """
    Admin interface for Domain model.
    """
    list_display = ['domain', 'tenant', 'is_primary']
    list_filter = ['is_primary']
    search_fields = ['domain', 'tenant__name']


@admin.register(TenantPlan)
class TenantPlanAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'plan', 'start_date', 'end_date', 'user_limit', 'discount', 'sales_rep']
    search_fields = ['tenant__name', 'plan__name', 'sales_rep']
    list_filter = ['plan', 'start_date', 'end_date', 'sales_rep']
    ordering = ['tenant']
    fieldsets = (
        ('Plan Info', {
            'fields': ('tenant', 'plan', 'start_date', 'end_date')
        }),
        ('Details', {
            'fields': ('user_limit', 'discount', 'sales_rep', 'notes')
        }),
    )


@admin.register(PlanFeature)
class PlanFeatureAdmin(admin.ModelAdmin):
    list_display = ['plan', 'plan_name', 'feature_count']
    search_fields = ['plan__name']
    fieldsets = (
        ('Plan', {
            'fields': ('plan',)
        }),
        ('Features (JSON)', {
            'fields': ('features',)
        }),
    )

    def plan_name(self, obj):
        return getattr(obj.plan, 'name', '-')

    def feature_count(self, obj):
        try:
            return len(obj.features or {})
        except Exception:
            return 0

