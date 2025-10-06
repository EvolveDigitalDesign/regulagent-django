from rest_framework import serializers

from ..models import WellRegistry


class WellRegistrySerializer(serializers.ModelSerializer):
    class Meta:
        model = WellRegistry
        fields = [
            'id', 'api14', 'state', 'county', 'lat', 'lon', 'created_at', 'updated_at'
        ]


