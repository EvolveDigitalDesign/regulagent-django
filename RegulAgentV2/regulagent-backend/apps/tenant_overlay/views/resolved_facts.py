from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from apps.tenant_overlay.services.facts_resolver import resolve_engagement_facts


class ResolvedFactsView(APIView):
    authentication_classes = []  # wire real auth later
    permission_classes = []

    def get(self, request, engagement_id: int):
        data = resolve_engagement_facts(engagement_id)
        return Response(data, status=status.HTTP_200_OK)


