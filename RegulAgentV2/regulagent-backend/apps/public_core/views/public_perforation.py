from rest_framework import viewsets, mixins

from ..models import PublicPerforation
from ..serializers.public_perforation import PublicPerforationSerializer


class PublicPerforationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = PublicPerforation.objects.select_related('well').all().order_by('well_id', 'top_ft')
    serializer_class = PublicPerforationSerializer


