from rest_framework import viewsets, mixins

from ..models import PublicCasingString
from ..serializers.public_casing_string import PublicCasingStringSerializer


class PublicCasingStringViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = PublicCasingString.objects.select_related('well').all().order_by('well_id', 'string_no')
    serializer_class = PublicCasingStringSerializer


