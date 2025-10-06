from rest_framework import serializers

from ..models import PublicFacts


class PublicFactsSerializer(serializers.ModelSerializer):
    well_api14 = serializers.CharField(source='well.api14', read_only=True)

    class Meta:
        model = PublicFacts
        fields = [
            'id', 'well', 'well_api14', 'fact_key', 'value', 'units', 'provenance', 'source', 'as_of', 'created_at', 'updated_at'
        ]
        read_only_fields = fields


