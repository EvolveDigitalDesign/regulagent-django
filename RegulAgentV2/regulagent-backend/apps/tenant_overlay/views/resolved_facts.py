from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from apps.tenant_overlay.services.facts_resolver import resolve_engagement_facts


class ResolvedFactsView(APIView):
    """Returns resolved facts for a well engagement, merging public and tenant data."""

    def get(self, request, engagement_id: int):
        data = resolve_engagement_facts(engagement_id)
        return Response(data, status=status.HTTP_200_OK)


