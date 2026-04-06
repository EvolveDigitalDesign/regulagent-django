from django.contrib import admin
from django.utils.html import format_html

from .models import (
    FilingStatusRecord,
    PortalCredential,
    RejectionPattern,
    RejectionRecord,
    Recommendation,
    RecommendationInteraction,
)


@admin.register(FilingStatusRecord)
class FilingStatusRecordAdmin(admin.ModelAdmin):
    list_display = ('filing_id', 'agency', 'form_type', 'status', 'well', 'state', 'status_date', 'created_at')
    list_filter = ('agency', 'form_type', 'status', 'state', 'created_at')
    search_fields = ('filing_id', 'reviewer_name', 'agency_remarks', 'well__api14')
    readonly_fields = ('id', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Identity', {
            'fields': ('id', 'filing_id', 'agency', 'form_type', 'status'),
        }),
        ('Form References', {
            'fields': ('w3_form', 'plan_snapshot', 'c103_form'),
            'classes': ('collapse',),
        }),
        ('Well & Tenant', {
            'fields': ('well', 'tenant_id'),
        }),
        ('Agency Details', {
            'fields': ('agency_remarks', 'reviewer_name', 'status_date', 'portal_url', 'polled_at'),
        }),
        ('Geography', {
            'fields': ('state', 'district', 'county', 'land_type'),
            'classes': ('collapse',),
        }),
        ('Raw Data', {
            'fields': ('raw_portal_data',),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(RejectionRecord)
class RejectionRecordAdmin(admin.ModelAdmin):
    list_display = ('id', 'agency', 'form_type', 'parse_status', 'well', 'state', 'rejection_date', 'created_at')
    list_filter = ('agency', 'form_type', 'parse_status', 'state', 'created_at')
    search_fields = ('well__api14', 'reviewer_name', 'raw_rejection_notes')
    readonly_fields = ('id', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Identity', {
            'fields': ('id', 'filing_status', 'agency', 'form_type'),
        }),
        ('Form References', {
            'fields': ('w3_form', 'plan_snapshot', 'c103_form'),
            'classes': ('collapse',),
        }),
        ('Well & Tenant', {
            'fields': ('well', 'tenant_id'),
        }),
        ('Rejection Details', {
            'fields': ('raw_rejection_notes', 'rejection_date', 'reviewer_name'),
        }),
        ('Parsed Issues', {
            'fields': ('parse_status', 'parsed_issues'),
        }),
        ('Geography', {
            'fields': ('state', 'district', 'county', 'land_type'),
            'classes': ('collapse',),
        }),
        ('Snapshot', {
            'fields': ('submitted_form_snapshot',),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(RejectionPattern)
class RejectionPatternAdmin(admin.ModelAdmin):
    list_display = (
        'form_type', 'field_name', 'issue_category', 'agency', 'state',
        'occurrence_count', 'rejection_rate', 'is_trending', 'confidence', 'last_observed',
    )
    list_filter = ('form_type', 'agency', 'state', 'issue_category', 'is_trending')
    search_fields = ('field_name', 'issue_category', 'issue_subcategory', 'pattern_description')
    readonly_fields = ('id', 'created_at', 'updated_at')

    fieldsets = (
        ('Identity', {
            'fields': ('id', 'form_type', 'field_name', 'issue_category', 'issue_subcategory'),
        }),
        ('Targeting', {
            'fields': ('agency', 'state', 'district'),
        }),
        ('Pattern Details', {
            'fields': ('pattern_description', 'example_bad_value', 'example_good_value'),
        }),
        ('Statistics', {
            'fields': ('occurrence_count', 'tenant_count', 'rejection_rate', 'first_observed', 'last_observed'),
        }),
        ('Trend', {
            'fields': ('is_trending', 'trend_direction', 'confidence'),
        }),
        ('Embedding', {
            'fields': ('embedding_vector',),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(Recommendation)
class RecommendationAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'form_type', 'field_name', 'scope', 'priority',
        'is_active', 'times_shown', 'times_accepted', 'acceptance_rate', 'state',
    )
    list_filter = ('form_type', 'scope', 'priority', 'is_active', 'state')
    search_fields = ('title', 'description', 'field_name', 'suggested_value')
    readonly_fields = ('id', 'created_at', 'updated_at')

    fieldsets = (
        ('Identity', {
            'fields': ('id', 'pattern', 'scope', 'priority', 'is_active'),
        }),
        ('Targeting', {
            'fields': ('form_type', 'field_name', 'state', 'district', 'county', 'land_type'),
        }),
        ('Content', {
            'fields': ('title', 'description', 'suggested_value', 'trigger_condition'),
        }),
        ('Effectiveness', {
            'fields': ('times_shown', 'times_accepted', 'times_dismissed', 'acceptance_rate'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(RecommendationInteraction)
class RecommendationInteractionAdmin(admin.ModelAdmin):
    list_display = ('id', 'recommendation', 'action', 'user', 'tenant_id', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('user__email', 'dismissal_reason', 'field_value_at_time')
    readonly_fields = ('id', 'created_at')

    fieldsets = (
        ('Identity', {
            'fields': ('id', 'recommendation', 'user', 'tenant_id'),
        }),
        ('Action', {
            'fields': ('action', 'field_value_at_time', 'dismissal_reason'),
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',),
        }),
    )


@admin.register(PortalCredential)
class PortalCredentialAdmin(admin.ModelAdmin):
    list_display = ('id', 'agency', 'tenant_id', 'is_active', 'last_successful_login', 'created_at')
    list_filter = ('agency', 'is_active')
    search_fields = ('tenant_id',)
    readonly_fields = ('id', 'created_at', 'updated_at', 'encrypted_username', 'encrypted_password')

    fieldsets = (
        ('Identity', {
            'fields': ('id', 'tenant_id', 'agency', 'is_active'),
        }),
        ('Credentials (encrypted)', {
            'fields': ('encrypted_username', 'encrypted_password'),
            'classes': ('collapse',),
        }),
        ('Status', {
            'fields': ('last_successful_login',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
