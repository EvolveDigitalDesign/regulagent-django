from django.contrib import admin
from django.utils.html import format_html
from .models import (
    WellRegistry, 
    ExtractedDocument, 
    DocumentVector,
    PlanSnapshot,
    PublicFacts,
    PublicArtifacts,
    PublicCasingString,
    PublicPerforation,
    PublicWellDepths,
)


@admin.register(WellRegistry)
class WellRegistryAdmin(admin.ModelAdmin):
    """Admin interface for well registry data."""
    list_display = ('api14', 'state', 'county', 'field', 'operator_name', 'created_at')
    list_filter = ('state', 'county', 'created_at')
    search_fields = ('api14', 'field', 'operator_name', 'county')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Well Identity', {
            'fields': ('api14', 'state', 'county', 'district', 'field', 'lease')
        }),
        ('Location', {
            'fields': ('lat', 'lon')
        }),
        ('Operator', {
            'fields': ('operator_name',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(ExtractedDocument)
class ExtractedDocumentAdmin(admin.ModelAdmin):
    """Admin interface for extracted regulatory documents."""
    list_display = ('api_number', 'document_type', 'source_type', 'status', 'is_validated', 'created_at')
    list_filter = ('document_type', 'source_type', 'status', 'is_validated', 'created_at')
    search_fields = ('api_number', 'source_path', 'model_tag')
    readonly_fields = ('created_at', 'updated_at', 'json_data_display')
    date_hierarchy = 'created_at'
    
    def json_data_display(self, obj):
        """Display JSON data in a formatted way."""
        import json
        if obj.json_data:
            return format_html(
                '<pre style="overflow-x: auto; max-height: 500px;">{}</pre>',
                json.dumps(obj.json_data, indent=2)
            )
        return 'No data'
    json_data_display.short_description = 'Extracted JSON Data'
    
    fieldsets = (
        ('Document Info', {
            'fields': ('api_number', 'document_type', 'well')
        }),
        ('Extraction Status', {
            'fields': ('status', 'errors', 'model_tag')
        }),
        ('Source', {
            'fields': ('source_path', 'source_type', 'uploaded_by_tenant')
        }),
        ('Validation', {
            'fields': ('is_validated', 'validation_errors'),
            'classes': ('collapse',)
        }),
        ('Data', {
            'fields': ('json_data_display',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(DocumentVector)
class DocumentVectorAdmin(admin.ModelAdmin):
    """Admin interface for semantic vectors from documents."""
    list_display = ('file_name', 'document_type', 'section_name', 'well', 'created_at')
    list_filter = ('document_type', 'created_at')
    search_fields = ('file_name', 'section_name', 'well__api14')
    readonly_fields = ('created_at', 'embedding_display')
    
    def embedding_display(self, obj):
        """Display embedding dimensions."""
        if obj.embedding:
            return f"Vector dimensions: {len(obj.embedding)}"
        return "No embedding"
    embedding_display.short_description = 'Embedding'
    
    fieldsets = (
        ('Document Reference', {
            'fields': ('file_name', 'document_type', 'section_name', 'well')
        }),
        ('Content', {
            'fields': ('section_text',),
            'classes': ('collapse',)
        }),
        ('Vector', {
            'fields': ('embedding_display',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('metadata', 'created_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(PlanSnapshot)
class PlanSnapshotAdmin(admin.ModelAdmin):
    """Admin interface for W3A plan snapshots."""
    list_display = ('plan_id', 'kind', 'status', 'visibility', 'well', 'tenant_display', 'created_at')
    list_filter = ('kind', 'status', 'visibility', 'created_at', 'policy_id')
    search_fields = ('plan_id', 'well__api14', 'policy_id')
    readonly_fields = ('created_at', 'payload_display')
    date_hierarchy = 'created_at'
    
    def tenant_display(self, obj):
        """Display tenant ID with color coding."""
        if obj.tenant_id:
            return format_html(
                '<span style="background-color: #e3f2fd; padding: 3px 8px; border-radius: 3px;">{}</span>',
                str(obj.tenant_id)[:8]
            )
        return "Public"
    tenant_display.short_description = 'Tenant'
    
    def payload_display(self, obj):
        """Display plan payload in formatted way."""
        import json
        if obj.payload:
            return format_html(
                '<pre style="overflow-x: auto; max-height: 600px;">{}</pre>',
                json.dumps(obj.payload, indent=2)[:3000] + '...'
            )
        return 'No payload'
    payload_display.short_description = 'Plan Payload'
    
    fieldsets = (
        ('Plan Identity', {
            'fields': ('plan_id', 'well', 'kind', 'policy_id')
        }),
        ('Status', {
            'fields': ('status', 'visibility')
        }),
        ('Provenance', {
            'fields': ('kernel_version', 'overlay_id', 'extraction_meta'),
            'classes': ('collapse',)
        }),
        ('Tenant', {
            'fields': ('tenant_id',),
            'classes': ('collapse',)
        }),
        ('Plan Data', {
            'fields': ('payload_display',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )


@admin.register(PublicFacts)
class PublicFactsAdmin(admin.ModelAdmin):
    """Admin interface for public facts extracted from regulators."""
    list_display = ('well', 'fact_key', 'value_display', 'source', 'created_at')
    list_filter = ('fact_key', 'created_at')
    search_fields = ('well__api14', 'fact_key', 'source')
    readonly_fields = ('created_at', 'updated_at')
    
    def value_display(self, obj):
        """Display value truncated."""
        import json
        val = obj.value
        if isinstance(val, dict):
            val_str = json.dumps(val)[:50]
        else:
            val_str = str(val)[:50]
        return val_str + ('...' if len(str(val)) > 50 else '')
    value_display.short_description = 'Value'


@admin.register(PublicArtifacts)
class PublicArtifactsAdmin(admin.ModelAdmin):
    """Admin interface for public artifacts (files, reports, etc.)."""
    list_display = ('well', 'kind', 'file_path', 'created_at')
    list_filter = ('kind', 'created_at')
    search_fields = ('well__api14', 'file_path')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(PublicCasingString)
class PublicCasingStringAdmin(admin.ModelAdmin):
    """Admin interface for casing string records."""
    list_display = ('well', 'string_type', 'size_in', 'shoe_depth_ft', 'created_at')
    list_filter = ('string_type', 'created_at')
    search_fields = ('well__api14',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(PublicPerforation)
class PublicPerforationAdmin(admin.ModelAdmin):
    """Admin interface for perforation records."""
    list_display = ('well', 'interval_top_ft', 'interval_bottom_ft', 'formation', 'status', 'created_at')
    list_filter = ('status', 'formation', 'created_at')
    search_fields = ('well__api14', 'formation')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(PublicWellDepths)
class PublicWellDepthsAdmin(admin.ModelAdmin):
    """Admin interface for well depth information."""
    list_display = ('well', 'td_ft', 'kb_ft', 'surface_casing_shoe_ft', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('well__api14',)
    readonly_fields = ('created_at', 'updated_at')

