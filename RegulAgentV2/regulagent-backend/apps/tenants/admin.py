"""
Django admin configuration for tenant and user management.

Based on TestDriven.io guide:
https://testdriven.io/blog/django-multi-tenant/#django-tenant-users
"""
from django.contrib import admin
from django.core.exceptions import ValidationError

from apps.tenants.models import (
    User,
    Tenant,
    Domain,
    TenantPlan,
    PlanFeature,
    DeletedTenantBackup,
    ClientWorkspace,
    UsageRecord,
)
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


@admin.register(DeletedTenantBackup)
class DeletedTenantBackupAdmin(admin.ModelAdmin):
    """
    Admin interface for viewing deleted tenant backups.

    Provides read-only access to backup records for recovery and auditing.
    """
    list_display = [
        'tenant_slug',
        'tenant_name',
        'soft_deleted_at',
        'scheduled_deletion_at',
        'hard_deleted_at',
        'backup_verified',
        'deleted_by_email',
    ]
    list_filter = [
        'backup_verified',
        'soft_deleted_at',
        'hard_deleted_at',
    ]
    search_fields = [
        'tenant_slug',
        'tenant_name',
        'schema_name',
        'deleted_by_email',
    ]
    readonly_fields = [
        'tenant_id',
        'tenant_slug',
        'tenant_name',
        'schema_name',
        'backup_path',
        'backup_size_bytes',
        'backup_checksum',
        'soft_deleted_at',
        'hard_deleted_at',
        'scheduled_deletion_at',
        'backup_verified',
        'verification_message',
        'deleted_by_email',
        'deletion_reason',
        'metadata',
        'is_hard_deleted_display',
        'is_pending_deletion_display',
    ]

    fieldsets = (
        ('Tenant Information', {
            'fields': (
                'tenant_id',
                'tenant_slug',
                'tenant_name',
                'schema_name',
            )
        }),
        ('Backup Details', {
            'fields': (
                'backup_path',
                'backup_size_bytes',
                'backup_checksum',
                'backup_verified',
                'verification_message',
            )
        }),
        ('Deletion Timeline', {
            'fields': (
                'soft_deleted_at',
                'scheduled_deletion_at',
                'hard_deleted_at',
                'is_hard_deleted_display',
                'is_pending_deletion_display',
            )
        }),
        ('Audit Trail', {
            'fields': (
                'deleted_by_email',
                'deletion_reason',
                'metadata',
            )
        }),
    )

    ordering = ['-soft_deleted_at']

    def has_add_permission(self, request):
        """Prevent manual creation of backup records."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Prevent deletion of backup records (they're for audit trail)."""
        return False

    def is_hard_deleted_display(self, obj):
        """Display whether schema has been permanently deleted."""
        return obj.is_hard_deleted()
    is_hard_deleted_display.short_description = 'Hard Deleted'
    is_hard_deleted_display.boolean = True

    def is_pending_deletion_display(self, obj):
        """Display whether schema is pending deletion."""
        return obj.is_pending_deletion()
    is_pending_deletion_display.short_description = 'Pending Deletion'
    is_pending_deletion_display.boolean = True


@admin.register(ClientWorkspace)
class ClientWorkspaceAdmin(admin.ModelAdmin):
    """
    Admin interface for ClientWorkspace model.
    """
    list_display = ['name', 'tenant', 'operator_number', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'operator_number', 'tenant__name', 'tenant__slug']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        ('Workspace Info', {
            'fields': ('tenant', 'name', 'operator_number', 'description')
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    """
    Admin interface for UsageRecord model (read-only for audit purposes).
    """
    list_display = [
        'created_at',
        'tenant',
        'workspace',
        'event_type',
        'resource_type',
        'resource_id',
        'user_email',
        'tokens_used',
        'processing_time_ms',
    ]
    list_filter = [
        'event_type',
        'resource_type',
        'created_at',
    ]
    search_fields = [
        'tenant__name',
        'tenant__slug',
        'workspace__name',
        'resource_id',
        'user__email',
    ]
    readonly_fields = [
        'tenant',
        'workspace',
        'user',
        'event_type',
        'resource_type',
        'resource_id',
        'tokens_used',
        'processing_time_ms',
        'metadata',
        'created_at',
    ]

    fieldsets = (
        ('Event Details', {
            'fields': ('event_type', 'resource_type', 'resource_id', 'created_at')
        }),
        ('Attribution', {
            'fields': ('tenant', 'workspace', 'user')
        }),
        ('Metrics', {
            'fields': ('tokens_used', 'processing_time_ms')
        }),
        ('Additional Data', {
            'fields': ('metadata',),
            'classes': ('collapse',)
        }),
    )

    ordering = ['-created_at']
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        """Prevent manual creation of usage records."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Prevent deletion of usage records (they're for audit trail)."""
        return False

    def user_email(self, obj):
        """Display user email or 'Anonymous' if no user."""
        return obj.user.email if obj.user else 'Anonymous'
    user_email.short_description = 'User'

