from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework import status

from apps.tenants.services.plan_service import get_tenant_plan, get_effective_features, get_active_user_count


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
                "users_filled": get_active_user_count(user_tenant),
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


class UserProfileView(APIView):
    """
    GET /api/user/profile/

    Returns the authenticated user's profile information including email,
    name, title, phone, organization, and tenant details.
    """
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        user_tenant = user.tenants.first() if user and user.is_authenticated else None

        if not user_tenant:
            return Response(
                {"detail": "No tenant found for user."},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response({
            "id": user.id,
            "email": user.email,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "title": user.title or "",
            "phone": user.phone or "",
            "organization": user.organization or "",
            "tenant": {
                "id": str(user_tenant.id),
                "name": user_tenant.name,
                "slug": user_tenant.slug,
            }
        })

    def put(self, request):
        """Update user profile information"""
        user = request.user
        
        # Update allowed fields
        allowed_fields = ["first_name", "last_name", "title", "phone", "organization"]
        for field in allowed_fields:
            if field in request.data:
                setattr(user, field, request.data[field])
        
        try:
            user.save()
            user_tenant = user.tenants.first() if user and user.is_authenticated else None
            return Response({
                "id": user.id,
                "email": user.email,
                "first_name": user.first_name or "",
                "last_name": user.last_name or "",
                "title": user.title or "",
                "phone": user.phone or "",
                "organization": user.organization or "",
                "tenant": {
                    "id": str(user_tenant.id),
                    "name": user_tenant.name,
                    "slug": user_tenant.slug,
                } if user_tenant else None
            })
        except Exception as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class ChangePasswordView(APIView):
    """
    POST /api/user/change-password/

    Allows the authenticated user to change their password.
    
    Request body:
    {
        "old_password": "current_password",
        "new_password": "new_password"
    }
    """
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        old_password = request.data.get("old_password")
        new_password = request.data.get("new_password")

        if not old_password or not new_password:
            return Response(
                {"detail": "Both old_password and new_password are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if old password is correct
        if not user.check_password(old_password):
            return Response(
                {"detail": "Old password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST
            )        # Set new password
        user.set_password(new_password)
        try:
            user.save()
            return Response({"detail": "Password changed successfully."})
        except Exception as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
