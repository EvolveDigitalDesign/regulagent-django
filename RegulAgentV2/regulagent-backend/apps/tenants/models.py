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

    # Vault passphrase hash (Argon2/PBKDF2 via Django's make_password) — authorization gate
    # for credential management. The actual encryption uses a per-tenant key derived from
    # tenant.id + ENCRYPTION_PEPPER, so background sync works without user intervention.
    vault_passphrase_hash = models.CharField(
        max_length=255,
        blank=True,
        help_text="Hashed vault passphrase for credential management authorization",
    )

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


class ClientWorkspace(models.Model):
    """
    A workspace within a tenant for a specific client/operator.
    Regulatory firms have multiple clients, each with their own wells.
    This allows isolation of wells and plans by client within a single tenant.
    """
    tenant = models.ForeignKey('Tenant', on_delete=models.CASCADE, related_name='workspaces')
    name = models.CharField(max_length=255, help_text="Client/operator name (e.g., 'Acme Oil Co')")
    operator_number = models.CharField(max_length=50, blank=True, help_text="RRC operator number")
    description = models.TextField(blank=True, help_text="Additional notes about this client")
    is_active = models.BooleanField(default=True, help_text="Inactive workspaces are archived")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [['tenant', 'name']]
        ordering = ['name']
        verbose_name = 'Client Workspace'
        verbose_name_plural = 'Client Workspaces'
        indexes = [
            models.Index(fields=['tenant', 'is_active']),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.tenant.slug})"


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


class DeletedTenantBackup(models.Model):
    """
    Track backups of deleted tenants for recovery and compliance.

    This model stores metadata about tenant backups created before deletion,
    including the pg_dump backup file path and verification status.
    """
    # Original tenant information
    tenant_id = models.UUIDField(db_index=True, help_text="Original tenant UUID")
    tenant_slug = models.CharField(max_length=64, db_index=True)
    tenant_name = models.CharField(max_length=255)
    schema_name = models.CharField(max_length=63, help_text="PostgreSQL schema name")

    # Backup metadata
    backup_path = models.CharField(
        max_length=512,
        help_text="Full path to pg_dump backup file"
    )
    backup_size_bytes = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Size of backup file in bytes"
    )
    backup_checksum = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA256 checksum of backup file"
    )

    # Deletion workflow
    soft_deleted_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When tenant was marked for deletion (soft delete)"
    )
    hard_deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When schema was actually dropped (hard delete)"
    )
    scheduled_deletion_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When schema is scheduled to be dropped"
    )

    # Backup verification
    backup_verified = models.BooleanField(
        default=False,
        help_text="Whether backup integrity was verified"
    )
    verification_message = models.TextField(
        blank=True,
        help_text="Details from backup verification"
    )

    # Additional context
    deleted_by_email = models.EmailField(
        blank=True,
        help_text="Email of user who initiated deletion"
    )
    deletion_reason = models.TextField(
        blank=True,
        help_text="Reason for deletion (audit trail)"
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional metadata (record counts, etc.)"
    )

    class Meta:
        verbose_name = 'Deleted Tenant Backup'
        verbose_name_plural = 'Deleted Tenant Backups'
        ordering = ['-soft_deleted_at']
        indexes = [
            models.Index(fields=['-soft_deleted_at']),
            models.Index(fields=['scheduled_deletion_at']),
        ]

    def __str__(self) -> str:
        return f"DeletedTenantBackup<{self.tenant_slug} @ {self.soft_deleted_at}>"

    def is_hard_deleted(self) -> bool:
        """Check if schema has been permanently dropped."""
        return self.hard_deleted_at is not None

    def is_pending_deletion(self) -> bool:
        """Check if schema is still pending hard deletion."""
        return self.hard_deleted_at is None and self.scheduled_deletion_at is not None


class UsageRecord(models.Model):
    """
    Track usage events for billing and analytics.

    Records all billable events per tenant including:
    - Plan generation
    - Document extraction
    - AI chat interactions
    - API calls

    Used for usage reporting, billing, and analytics dashboards.
    """

    # Event type choices
    EVENT_PLAN_GENERATED = 'plan_generated'
    EVENT_EXTRACTION_COMPLETED = 'extraction_completed'
    EVENT_AI_CHAT_MESSAGE = 'ai_chat_message'
    EVENT_API_CALL = 'api_call'
    EVENT_DOCUMENT_UPLOADED = 'document_uploaded'
    EVENT_PLAN_MODIFIED = 'plan_modified'

    EVENT_TYPE_CHOICES = [
        (EVENT_PLAN_GENERATED, 'Plan Generated'),
        (EVENT_EXTRACTION_COMPLETED, 'Extraction Completed'),
        (EVENT_AI_CHAT_MESSAGE, 'AI Chat Message'),
        (EVENT_API_CALL, 'API Call'),
        (EVENT_DOCUMENT_UPLOADED, 'Document Uploaded'),
        (EVENT_PLAN_MODIFIED, 'Plan Modified'),
    ]

    # Core relationships
    tenant = models.ForeignKey(
        'Tenant',
        on_delete=models.CASCADE,
        related_name='usage_records',
        help_text="Tenant this usage is attributed to"
    )
    workspace = models.ForeignKey(
        'ClientWorkspace',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='usage_records',
        help_text="Client workspace this usage is attributed to (if applicable)"
    )
    user = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='usage_records',
        help_text="User who triggered this event (if applicable)"
    )

    # Event details
    event_type = models.CharField(
        max_length=50,
        choices=EVENT_TYPE_CHOICES,
        db_index=True,
        help_text="Type of usage event"
    )
    resource_type = models.CharField(
        max_length=50,
        blank=True,
        help_text="Resource type (e.g., 'well', 'plan', 'document')"
    )
    resource_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="ID of the resource (e.g., API number, plan ID, document ID)"
    )

    # Usage metrics
    tokens_used = models.IntegerField(
        default=0,
        help_text="AI tokens consumed (for AI operations)"
    )
    processing_time_ms = models.IntegerField(
        default=0,
        help_text="Processing time in milliseconds"
    )

    # Additional metadata
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional event-specific data: model used, endpoint, parameters, etc."
    )

    # Timestamps
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="When this usage event occurred"
    )

    class Meta:
        db_table = 'tenants_usage_records'
        verbose_name = 'Usage Record'
        verbose_name_plural = 'Usage Records'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', '-created_at']),
            models.Index(fields=['tenant', 'event_type', '-created_at']),
            models.Index(fields=['workspace', '-created_at']),
            models.Index(fields=['event_type', '-created_at']),
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self) -> str:
        return f"UsageRecord<{self.tenant.slug}:{self.event_type} @ {self.created_at}>"

