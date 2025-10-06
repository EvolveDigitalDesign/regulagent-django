from rest_framework import viewsets, mixins

from ..models import PublicWellDepths
from ..serializers.public_well_depths import PublicWellDepthsSerializer


class PublicWellDepthsViewSet(mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = PublicWellDepths.objects.select_related('well').all().order_by('well_id')
    serializer_class = PublicWellDepthsSerializer


