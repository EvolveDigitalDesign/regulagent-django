"""
Serializers for ExtractedDocument model.

Used for API responses and validation of extracted regulatory documents.
"""

from rest_framework import serializers
from apps.public_core.models import ExtractedDocument


class ExtractedDocumentListSerializer(serializers.ModelSerializer):
    """List view serializer for ExtractedDocument - minimal fields"""
    
    well_api = serializers.SerializerMethodField()
    
    class Meta:
        model = ExtractedDocument
        fields = [
            'id',
            'api_number',
            'document_type',
            'tracking_no',
            'status',
            'source_type',
            'model_tag',
            'is_validated',
            'well',
            'well_api',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields
    
    def get_well_api(self, obj):
        """Get the well's API number"""
        return obj.well.api14 if obj.well else None


class ExtractedDocumentDetailSerializer(serializers.ModelSerializer):
    """Detail view serializer for ExtractedDocument - includes full JSON data"""
    
    well_api = serializers.SerializerMethodField()
    
    class Meta:
        model = ExtractedDocument
        fields = [
            'id',
            'api_number',
            'document_type',
            'tracking_no',
            'status',
            'errors',
            'source_type',
            'source_path',
            'model_tag',
            'json_data',
            'is_validated',
            'validation_errors',
            'well',
            'well_api',
            'uploaded_by_tenant',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields
    
    def get_well_api(self, obj):
        """Get the well's API number"""
        return obj.well.api14 if obj.well else None


class ExtractedDocumentCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating ExtractedDocument"""
    
    class Meta:
        model = ExtractedDocument
        fields = [
            'api_number',
            'document_type',
            'tracking_no',
            'status',
            'errors',
            'json_data',
            'source_path',
            'model_tag',
            'source_type',
            'is_validated',
            'validation_errors',
            'well',
            'uploaded_by_tenant',
        ]
        read_only_fields = ['created_at', 'updated_at']


class ExtractedDocumentQuerySerializer(serializers.Serializer):
    """Serializer for querying/filtering ExtractedDocuments"""
    
    api_number = serializers.CharField(
        required=False,
        help_text="API number to filter by"
    )
    document_type = serializers.ChoiceField(
        choices=['w2', 'w15', 'gau', 'schematic', 'formation_tops', 'w3', 'w3a'],
        required=False,
        help_text="Document type to filter by"
    )
    tracking_no = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="W-2 tracking number to filter by"
    )
    status = serializers.ChoiceField(
        choices=['success', 'error', 'partial'],
        required=False,
        help_text="Extraction status to filter by"
    )
    source_type = serializers.ChoiceField(
        choices=['rrc', 'tenant_upload'],
        required=False,
        help_text="Source type to filter by"
    )
    is_validated = serializers.BooleanField(
        required=False,
        help_text="Filter by validation status"
    )
    limit = serializers.IntegerField(
        min_value=1,
        max_value=1000,
        required=False,
        default=100,
        help_text="Maximum number of results to return"
    )
    offset = serializers.IntegerField(
        min_value=0,
        required=False,
        default=0,
        help_text="Number of results to skip"
    )


class W2RevisionInfoSerializer(serializers.Serializer):
    """Serializer for W-2 revision information"""
    
    revising_tracking_number = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Tracking number of the W-2 being revised"
    )
    revision_reason = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Description of what was revised"
    )
    other_changes = serializers.BooleanField(
        required=False,
        help_text="Whether there are additional changes beyond the revision"
    )


class ExtractedDocumentWithRevisionsSerializer(serializers.Serializer):
    """Extended serializer for W-2 documents that includes revision information"""
    
    extracted_document = ExtractedDocumentDetailSerializer(
        help_text="The extracted document details"
    )
    revisions = W2RevisionInfoSerializer(
        required=False,
        allow_null=True,
        help_text="Revision information if this W-2 revises another"
    )



