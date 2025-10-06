from rest_framework import viewsets, mixins, filters

from ..models import PublicFacts
from ..serializers.public_facts import PublicFactsSerializer


class PublicFactsViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = PublicFacts.objects.select_related('well').all().order_by('id')
    serializer_class = PublicFactsSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['fact_key', 'well__api14']


