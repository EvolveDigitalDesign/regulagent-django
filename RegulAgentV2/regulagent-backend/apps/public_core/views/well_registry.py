from rest_framework import viewsets, mixins

from ..models import WellRegistry
from ..serializers.well_registry import WellRegistrySerializer


class WellRegistryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = WellRegistry.objects.all().order_by('id')
    serializer_class = WellRegistrySerializer


