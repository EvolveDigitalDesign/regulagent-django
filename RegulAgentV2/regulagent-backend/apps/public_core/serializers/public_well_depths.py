from rest_framework import serializers

from ..models import PublicWellDepths


class PublicWellDepthsSerializer(serializers.ModelSerializer):
    well_api14 = serializers.CharField(source='well.api14', read_only=True)

    class Meta:
        model = PublicWellDepths
        fields = [
            'id', 'well', 'well_api14', 'td_ft', 'kb_elev_ft', 'gl_elev_ft',
            'surf_shoe_ft', 'int_shoe_ft', 'prod_shoe_ft', 'provenance', 'source', 'as_of',
            'created_at', 'updated_at'
        ]
        read_only_fields = fields


