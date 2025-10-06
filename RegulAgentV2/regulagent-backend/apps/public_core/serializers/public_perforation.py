from rest_framework import serializers

from ..models import PublicPerforation


class PublicPerforationSerializer(serializers.ModelSerializer):
    well_api14 = serializers.CharField(source='well.api14', read_only=True)

    class Meta:
        model = PublicPerforation
        fields = [
            'id', 'well', 'well_api14', 'top_ft', 'bottom_ft', 'formation',
            'shot_density_spf', 'phase_deg', 'provenance', 'source', 'as_of',
            'created_at', 'updated_at'
        ]
        read_only_fields = fields


