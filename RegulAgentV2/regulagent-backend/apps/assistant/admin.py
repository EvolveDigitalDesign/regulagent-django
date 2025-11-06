from django.contrib import admin
from apps.assistant.models import ChatThread, ChatMessage, PlanModification


@admin.register(ChatThread)
class ChatThreadAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant_id', 'well', 'baseline_plan', 'created_by', 'shared_count', 'is_active', 'created_at', 'last_message_at')
    list_filter = ('is_active', 'mode', 'created_at')
    search_fields = ('title', 'tenant_id', 'well__api14', 'openai_thread_id', 'created_by__email')
    readonly_fields = ('created_at', 'updated_at', 'openai_thread_id')
    filter_horizontal = ('shared_with',)  # Nice UI for many-to-many
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Ownership & Sharing', {
            'fields': ('tenant_id', 'created_by', 'shared_with', 'title', 'mode')
        }),
        ('Context', {
            'fields': ('well', 'baseline_plan', 'current_plan')
        }),
        ('OpenAI Integration', {
            'fields': ('openai_thread_id',)
        }),
        ('Status', {
            'fields': ('is_active', 'last_message_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )
    
    def shared_count(self, obj):
        return obj.shared_with.count()
    shared_count.short_description = 'Shared With'


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'thread', 'role', 'content_preview', 'created_at', 'has_tool_calls')
    list_filter = ('role', 'created_at')
    search_fields = ('content', 'thread__title', 'openai_message_id')
    readonly_fields = ('created_at', 'openai_message_id', 'openai_run_id', 'tool_calls', 'tool_results', 'metadata')
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Message', {
            'fields': ('thread', 'role', 'content')
        }),
        ('OpenAI Integration', {
            'fields': ('openai_message_id', 'openai_run_id')
        }),
        ('Tool Usage', {
            'fields': ('tool_calls', 'tool_results')
        }),
        ('Metadata', {
            'fields': ('metadata', 'created_at')
        }),
    )
    
    def content_preview(self, obj):
        return obj.content[:100] + '...' if len(obj.content) > 100 else obj.content
    content_preview.short_description = 'Content'
    
    def has_tool_calls(self, obj):
        return bool(obj.tool_calls)
    has_tool_calls.boolean = True
    has_tool_calls.short_description = 'Tools'


@admin.register(PlanModification)
class PlanModificationAdmin(admin.ModelAdmin):
    list_display = ('id', 'op_type', 'description_preview', 'source_snapshot', 'result_snapshot', 'risk_score', 'is_applied', 'is_reverted', 'applied_by', 'created_at')
    list_filter = ('op_type', 'is_applied', 'is_reverted', 'created_at')
    search_fields = ('description', 'source_snapshot__plan_id', 'result_snapshot__plan_id')
    readonly_fields = ('created_at', 'applied_at', 'reverted_at', 'diff', 'violations_delta', 'operation_payload')
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Operation', {
            'fields': ('op_type', 'description', 'operation_payload')
        }),
        ('Snapshots', {
            'fields': ('source_snapshot', 'result_snapshot')
        }),
        ('Risk Assessment', {
            'fields': ('risk_score', 'violations_delta', 'diff')
        }),
        ('Audit Trail', {
            'fields': ('chat_thread', 'chat_message', 'applied_by')
        }),
        ('Status', {
            'fields': ('is_applied', 'is_reverted', 'applied_at', 'reverted_at')
        }),
        ('Timestamps', {
            'fields': ('created_at',)
        }),
    )
    
    def description_preview(self, obj):
        return obj.description[:80] + '...' if len(obj.description) > 80 else obj.description
    description_preview.short_description = 'Description'

