from rest_framework import generics
from rest_framework.filters import OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend

from .models import PolicySection, PolicyRule
from .serializers import PolicySectionSerializer, PolicyRuleSerializer


class PolicySectionsListView(generics.ListAPIView):
    queryset = PolicySection.objects.select_related('rule').all()
    serializer_class = PolicySectionSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['rule__rule_id', 'version_tag', 'path']
    ordering_fields = ['order_idx']
    ordering = ['order_idx']


class PolicyRulesListView(generics.ListAPIView):
    queryset = PolicyRule.objects.all()
    serializer_class = PolicyRuleSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['rule_id', 'version_tag']


