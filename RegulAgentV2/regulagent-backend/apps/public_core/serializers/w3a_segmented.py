"""
Serializers for segmented W3A flow with user verification at each stage.

Flow stages:
1. Initial document sourcing -> combined PDF verification
2. Document confirmation -> extraction
3. Extraction confirmation -> geometry derivation
4. Geometry confirmation -> plan building
5. Apply edits to WellRegistry
"""
from __future__ import annotations

from typing import Any, Dict, List
from rest_framework import serializers


class W3AInitialRequestSerializer(serializers.Serializer):
    """
    Initial request to start W3A flow (document sourcing only).
    """
    api10 = serializers.CharField(help_text="10-digit API number")
    input_mode = serializers.ChoiceField(
        choices=("extractions", "user_files", "hybrid"),
        required=False,
        default="extractions"
    )
    
    # Optional uploaded files
    gau_file = serializers.FileField(required=False, allow_null=True)
    w2_file = serializers.FileField(required=False, allow_null=True)
    w15_file = serializers.FileField(required=False, allow_null=True)
    schematic_file = serializers.FileField(required=False, allow_null=True)
    formation_tops_file = serializers.FileField(required=False, allow_null=True)
    
    def validate_api10(self, value: str) -> str:
        import re
        digits = re.sub(r"\D+", "", str(value or ""))
        if len(digits) != 10:
            raise serializers.ValidationError("api10 must contain exactly 10 digits")
        return digits


class W3AInitialResponseSerializer(serializers.Serializer):
    """
    Response from initial W3A request with combined PDF for verification.
    """
    temp_plan_id = serializers.CharField(help_text="Temporary plan ID for this session")
    combined_pdf_url = serializers.CharField(help_text="URL to download combined PDF")
    combined_pdf_path = serializers.CharField(help_text="Server path to combined PDF (for internal use)")
    source_files = serializers.ListField(
        child=serializers.JSONField(),
        help_text="List of source files included in combined PDF"
    )
    api = serializers.CharField()
    page_count = serializers.IntegerField()
    file_size = serializers.IntegerField()
    ttl_expires_at = serializers.CharField(help_text="ISO timestamp when temp file expires")


class DocumentOverrideSerializer(serializers.Serializer):
    """
    User's decision to accept, reject, or replace a document.
    """
    source_index = serializers.IntegerField(help_text="Index in original source_files array")
    action = serializers.ChoiceField(
        choices=("accept", "reject", "replace"),
        help_text="accept=use as-is, reject=exclude, replace=upload new file"
    )
    replacement_file = serializers.FileField(
        required=False,
        allow_null=True,
        help_text="New file when action=replace"
    )
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Optional reason for rejection/replacement"
    )


class W3AConfirmDocsRequestSerializer(serializers.Serializer):
    """
    User confirms documents after reviewing combined PDF.
    """
    document_overrides = serializers.ListField(
        child=DocumentOverrideSerializer(),
        required=False,
        default=list,
        help_text="List of document accept/reject/replace decisions"
    )
    additional_uploads = serializers.ListField(
        child=serializers.FileField(),
        required=False,
        default=list,
        help_text="Additional files user wants to add"
    )


class ExtractionResultSerializer(serializers.Serializer):
    """
    Extracted JSON from a single document with human-readable summary.
    """
    extracted_document_id = serializers.IntegerField()  # ExtractedDocument uses BigAutoField (int), not UUID
    document_type = serializers.CharField()
    filename = serializers.CharField()
    extraction_status = serializers.CharField()
    errors = serializers.ListField(child=serializers.CharField(), required=False)
    json_data = serializers.JSONField()
    human_readable_summary = serializers.JSONField(
        help_text="Human-friendly representation of key fields for UI display"
    )


class W3AExtractionsResponseSerializer(serializers.Serializer):
    """
    Response with all extracted document JSONs for user review.
    """
    temp_plan_id = serializers.CharField()
    extractions = serializers.ListField(child=ExtractionResultSerializer())
    extraction_count = serializers.IntegerField()


class ExtractionEditSerializer(serializers.Serializer):
    """
    User edit to an extracted field.
    """
    extracted_document_id = serializers.IntegerField()  # ExtractedDocument uses BigAutoField (int), not UUID
    field_path = serializers.CharField(help_text="JSON dotpath (e.g., 'casing_record.0.cement_top_ft')")
    field_label = serializers.CharField(required=False, help_text="Human-readable field name")
    original_value = serializers.JSONField(allow_null=True)
    edited_value = serializers.JSONField()
    reason = serializers.CharField(allow_blank=True, help_text="Why user made this edit")


class W3AConfirmExtractionsRequestSerializer(serializers.Serializer):
    """
    User confirms/edits extractions after reviewing extraction JSONs.
    """
    edits = serializers.ListField(
        child=ExtractionEditSerializer(),
        required=False,
        default=list
    )


class GeometryFieldSerializer(serializers.Serializer):
    """
    Human-readable representation of a derived geometry field.
    """
    field_id = serializers.CharField()
    field_label = serializers.CharField()
    value = serializers.JSONField(allow_null=True)
    unit = serializers.CharField(required=False, allow_blank=True)
    source = serializers.CharField(help_text="Where this value came from (e.g., 'W-2 casing_record')")
    editable = serializers.BooleanField(default=True)


class W3AGeometryResponseSerializer(serializers.Serializer):
    """
    Response with derived well geometry for user review.
    """
    temp_plan_id = serializers.CharField()
    api = serializers.CharField()
    casing_strings = serializers.ListField(child=GeometryFieldSerializer())
    formation_tops = serializers.ListField(child=GeometryFieldSerializer())
    perforations = serializers.ListField(child=GeometryFieldSerializer())
    mechanical_barriers = serializers.ListField(child=GeometryFieldSerializer())
    uqw_data = serializers.JSONField(required=False, allow_null=True)
    kop_data = serializers.JSONField(required=False, allow_null=True)


class GeometryEditSerializer(serializers.Serializer):
    """
    User edit to a derived geometry field.
    """
    field_id = serializers.CharField()
    field_label = serializers.CharField(required=False)
    original_value = serializers.JSONField(allow_null=True)
    edited_value = serializers.JSONField()
    reason = serializers.CharField(allow_blank=True)


class W3AConfirmGeometryRequestSerializer(serializers.Serializer):
    """
    User confirms/edits geometry after reviewing derived data.
    """
    edits = serializers.ListField(
        child=GeometryEditSerializer(),
        required=False,
        default=list
    )
    plugs_mode = serializers.ChoiceField(
        choices=("combined", "isolated", "both"),
        required=False,
        default="combined"
    )
    sack_limit_no_tag = serializers.FloatField(required=False, default=50.0)
    sack_limit_with_tag = serializers.FloatField(required=False, default=150.0)


class ApplyEditsRequestSerializer(serializers.Serializer):
    """
    Apply staged edits to WellRegistry.
    """
    edit_ids = serializers.ListField(
        child=serializers.UUIDField(),
        help_text="List of WellEditAudit IDs to apply"
    )
    apply_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Reason for applying these edits"
    )


class EditAuditSerializer(serializers.Serializer):
    """
    Serializer for WellEditAudit instances (for browsing edits).
    """
    id = serializers.UUIDField()
    field_path = serializers.CharField()
    field_label = serializers.CharField()
    context = serializers.CharField()
    original_value = serializers.JSONField(allow_null=True)
    edited_value = serializers.JSONField()
    editor_display_name = serializers.CharField()
    editor_tenant_id = serializers.UUIDField(allow_null=True)
    edit_reason = serializers.CharField()
    stage = serializers.CharField()
    applied_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


