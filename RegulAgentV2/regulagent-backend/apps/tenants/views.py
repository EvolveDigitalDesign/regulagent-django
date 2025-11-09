from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.tenants.services.plan_service import get_tenant_plan, get_effective_features


class TenantInfoView(APIView):
    """
    Return the tenant info for the authenticated user, including plan and effective features.
    """
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        user_tenant = user.tenants.first() if user and user.is_authenticated else None

        if not user_tenant:
            return Response({"detail": "No tenant found for user."}, status=404)

        tenant_payload = {
            "id": str(user_tenant.id),
            "name": user_tenant.name,
            "slug": user_tenant.slug,
            "created_on": user_tenant.created_on,
        }

        tenant_plan = get_tenant_plan(user_tenant)
        plan_payload = None
        if tenant_plan:
            plan = tenant_plan.plan
            plan_payload = {
                "id": getattr(plan, "id", None) if plan else None,
                "name": getattr(plan, "name", None) if plan else None,
                "slug": getattr(plan, "slug", None) if plan else None,
                "start_date": tenant_plan.start_date,
                "end_date": tenant_plan.end_date,
                "user_limit": tenant_plan.user_limit,
                "discount": float(tenant_plan.discount) if tenant_plan.discount is not None else None,
                "sales_rep": tenant_plan.sales_rep,
                "notes": tenant_plan.notes,
            }

        features = get_effective_features(user_tenant)

        return Response({
            "tenant": tenant_payload,
            "plan": plan_payload,
            "features": features,
        })


