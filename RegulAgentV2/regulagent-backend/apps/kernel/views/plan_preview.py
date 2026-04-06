from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from apps.tenant_overlay.services.facts_resolver import resolve_engagement_facts
from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts


class PlanPreviewView(APIView):
    """Generate a plan preview from resolved facts and policy."""

    def post(self, request):
        engagement_id = request.data.get('engagement_id')
        if engagement_id in (None, ""):
            return Response({"detail": "engagement_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            engagement_int = int(engagement_id)
        except (TypeError, ValueError):
            return Response({"detail": "engagement_id must be an integer"}, status=status.HTTP_400_BAD_REQUEST)

        facts = resolve_engagement_facts(engagement_int)
        district = facts.get('district', {}).get('value') if isinstance(facts.get('district'), dict) else facts.get('district')
        policy = get_effective_policy(district=district)
        plan = plan_from_facts(facts, policy)
        return Response(plan, status=status.HTTP_200_OK)
