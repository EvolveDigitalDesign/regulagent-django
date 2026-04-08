"""
Serializer for ManualWBD model.
"""
from rest_framework import serializers

from apps.public_core.models.manual_wbd import ManualWBD
from apps.public_core.models.well_registry import WellRegistry


class ManualWBDSerializer(serializers.ModelSerializer):
    """
    Serializer for ManualWBD creation and representation.

    - Validates required fields: api14, diagram_type, diagram_data
    - Validates diagram_data shape per diagram_type
    - Auto-injects tenant_id and created_by from request context
    - Auto-links well FK if api14 matches a WellRegistry
    """

    class Meta:
        model = ManualWBD
        fields = [
            "id",
            "api14",
            "diagram_type",
            "title",
            "diagram_data",
            "tenant_id",
            "is_archived",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant_id", "is_archived", "created_at", "updated_at"]

    def validate(self, attrs):
        diagram_type = attrs.get("diagram_type")
        diagram_data = attrs.get("diagram_data")

        if diagram_type == ManualWBD.DiagramType.PLANNED:
            payload = diagram_data.get("payload") if diagram_data else None
            if not payload or not isinstance(payload.get("steps"), list) or len(payload["steps"]) == 0:
                raise serializers.ValidationError(
                    {"diagram_data": "Planned diagrams must have a non-empty payload.steps list."}
                )

        elif diagram_type == ManualWBD.DiagramType.AS_PLUGGED:
            plugs = diagram_data.get("plugs") if diagram_data else None
            if not plugs or not isinstance(plugs, list) or len(plugs) == 0:
                raise serializers.ValidationError(
                    {"diagram_data": "As-plugged diagrams must have a non-empty plugs list."}
                )

        return attrs

    def create(self, validated_data):
        request = self.context.get("request")

        # Auto-inject tenant_id
        from apps.tenant_overlay.views.tenant_wells import get_tenant_id_from_request
        tenant_id = get_tenant_id_from_request(request) if request else None
        validated_data["tenant_id"] = tenant_id

        # Auto-inject created_by
        if request and request.user and request.user.is_authenticated:
            validated_data["created_by"] = request.user

        # Auto-link well FK if api14 matches
        api14 = validated_data.get("api14")
        if api14:
            try:
                well = WellRegistry.objects.get(api14=api14)
                validated_data["well"] = well
            except WellRegistry.DoesNotExist:
                pass

        return super().create(validated_data)


class ManualWBDUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for partial updates (PATCH) — only allows title and diagram_data.
    """

    class Meta:
        model = ManualWBD
        fields = ["id", "api14", "diagram_type", "title", "diagram_data", "tenant_id", "is_archived", "created_at", "updated_at"]
        read_only_fields = ["id", "api14", "diagram_type", "tenant_id", "is_archived", "created_at", "updated_at"]
