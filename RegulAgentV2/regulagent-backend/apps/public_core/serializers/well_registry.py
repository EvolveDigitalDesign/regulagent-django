from rest_framework import serializers

from ..models import WellRegistry


class WellRegistrySerializer(serializers.ModelSerializer):
    workspace_name = serializers.CharField(source='workspace.name', read_only=True, allow_null=True)

    class Meta:
        model = WellRegistry
        fields = [
            'id', 'api14', 'state', 'county', 'district', 'lat', 'lon',
            'operator_name', 'field_name', 'lease_name', 'well_number',
            'workspace', 'workspace_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']


