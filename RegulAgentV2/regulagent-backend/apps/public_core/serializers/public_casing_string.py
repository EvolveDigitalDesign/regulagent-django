from rest_framework import serializers

from ..models import PublicCasingString


class PublicCasingStringSerializer(serializers.ModelSerializer):
    well_api14 = serializers.CharField(source='well.api14', read_only=True)

    class Meta:
        model = PublicCasingString
        fields = [
            'id', 'well', 'well_api14', 'string_no', 'outside_dia_in', 'weight_ppf',
            'grade', 'thread_type', 'top_ft', 'shoe_ft', 'cement_to_ft',
            'provenance', 'source', 'as_of', 'created_at', 'updated_at'
        ]
        read_only_fields = fields


