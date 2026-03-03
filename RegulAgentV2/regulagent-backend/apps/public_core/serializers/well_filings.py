"""
Serializers for unified well filings endpoint.

Handles W-3A plans, W-3 forms, and future form types.
"""

from rest_framework import serializers


class FilingMetadataSerializer(serializers.Serializer):
    """Base metadata for filings"""
    pass


class W3AFilingSerializer(serializers.Serializer):
    """W-3A Plan Snapshot serializer"""
    id = serializers.SerializerMethodField()
    form_type = serializers.SerializerMethodField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.SerializerMethodField()
    metadata = serializers.SerializerMethodField()
    
    def get_id(self, obj):
        return str(obj.id)
    
    def get_form_type(self, obj):
        return "W-3A"
    
    def get_updated_at(self, obj):
        # PlanSnapshot only has created_at, so we use that for updated_at
        return obj.created_at
    
    def get_metadata(self, obj):
        return {
            "plan_id": obj.plan_id,
            "kernel_version": obj.kernel_version,
            "visibility": obj.visibility,
            "kind": obj.kind,
        }


class W3FilingSerializer(serializers.Serializer):
    """W-3 Form ORM serializer"""
    id = serializers.SerializerMethodField()
    form_type = serializers.SerializerMethodField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    metadata = serializers.SerializerMethodField()
    
    def get_id(self, obj):
        return str(obj.id)
    
    def get_form_type(self, obj):
        return "W-3"
    
    def get_metadata(self, obj):
        w3_events_count = 0
        if hasattr(obj, 'w3_events'):
            try:
                w3_events_count = obj.w3_events.count()
            except Exception:
                w3_events_count = 0
        
        return {
            "submitted_by": obj.submitted_by,
            "submitted_at": obj.submitted_at.isoformat() if obj.submitted_at else None,
            "rrc_confirmation_number": obj.rrc_confirmation_number,
            "events_count": w3_events_count,
        }


class WellFilingsResponseSerializer(serializers.Serializer):
    """Unified filings response"""
    api14 = serializers.CharField()
    total = serializers.IntegerField()
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    filings = serializers.ListField(child=serializers.JSONField())

