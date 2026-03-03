from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework import status, viewsets
from rest_framework.decorators import action
from django_tenants.utils import get_tenant_model
from django.db import connection

from apps.tenants.services.plan_service import get_tenant_plan, get_effective_features, get_active_user_count
from apps.tenants.services.usage_tracker import get_tenant_usage_summary
from .models import ClientWorkspace, UsageRecord
from .serializers import ClientWorkspaceSerializer, ClientWorkspaceCreateSerializer, UsageRecordSerializer


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


class ClientWorkspaceViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing client workspaces within a tenant.
    Automatically filters to current tenant's workspaces.

    Endpoints:
    - GET /api/tenant/workspaces/ - List all workspaces for current tenant
    - POST /api/tenant/workspaces/ - Create new workspace
    - GET /api/tenant/workspaces/{id}/ - Retrieve workspace details
    - PUT /api/tenant/workspaces/{id}/ - Update workspace
    - PATCH /api/tenant/workspaces/{id}/ - Partial update workspace
    - DELETE /api/tenant/workspaces/{id}/ - Delete workspace
    - POST /api/tenant/workspaces/{id}/archive/ - Archive workspace
    - POST /api/tenant/workspaces/{id}/restore/ - Restore archived workspace
    """
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter to current tenant based on schema."""
        Tenant = get_tenant_model()
        tenant = Tenant.objects.get(schema_name=connection.schema_name)

        queryset = ClientWorkspace.objects.filter(tenant=tenant)

        # Optional filter by is_active
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')

        return queryset.select_related('tenant').prefetch_related('wells')

    def get_serializer_class(self):
        """Use create serializer for write operations."""
        if self.action in ['create', 'update', 'partial_update']:
            return ClientWorkspaceCreateSerializer
        return ClientWorkspaceSerializer

    def get_serializer_context(self):
        """Add tenant to serializer context for validation."""
        context = super().get_serializer_context()
        if hasattr(self, 'request') and hasattr(self.request, 'user'):
            user = self.request.user
            if user.is_authenticated:
                tenant = user.tenants.first()
                if tenant:
                    context['tenant'] = tenant
        return context

    def perform_create(self, serializer):
        """Automatically set tenant to current tenant."""
        user = self.request.user
        tenant = user.tenants.first()
        if not tenant:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("No tenant found for user")
        serializer.save(tenant=tenant)

    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        """Archive a workspace (set is_active=False)."""
        workspace = self.get_object()
        workspace.is_active = False
        workspace.save()
        serializer = self.get_serializer(workspace)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        """Restore an archived workspace (set is_active=True)."""
        workspace = self.get_object()
        workspace.is_active = True
        workspace.save()
        serializer = self.get_serializer(workspace)
        return Response(serializer.data)


class UsageSummaryView(APIView):
    """
    GET /api/tenant/usage/summary/

    Returns usage statistics for the current tenant with optional filtering.

    Query parameters:
    - start_date: ISO date string (e.g., "2024-01-01")
    - end_date: ISO date string
    - event_type: Filter by event type
    - workspace_id: Filter by workspace ID
    - group_by: Group results by 'event_type', 'workspace', 'user', 'day', or 'resource_type' (default: event_type)
    """
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from datetime import datetime
        from django_tenants.utils import get_tenant_model

        user = request.user
        tenant = user.tenants.first() if user and user.is_authenticated else None

        if not tenant:
            return Response(
                {"detail": "No tenant found for user."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Parse query parameters
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')
        event_type = request.query_params.get('event_type')
        workspace_id = request.query_params.get('workspace_id')
        group_by = request.query_params.get('group_by', 'event_type')

        # Parse dates
        start_date = None
        end_date = None
        if start_date_str:
            try:
                start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
            except ValueError:
                return Response(
                    {"detail": "Invalid start_date format. Use ISO format (YYYY-MM-DD)."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        if end_date_str:
            try:
                end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            except ValueError:
                return Response(
                    {"detail": "Invalid end_date format. Use ISO format (YYYY-MM-DD)."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Get workspace if specified
        workspace = None
        if workspace_id:
            try:
                workspace = ClientWorkspace.objects.get(id=workspace_id, tenant=tenant)
            except ClientWorkspace.DoesNotExist:
                return Response(
                    {"detail": f"Workspace {workspace_id} not found for this tenant."},
                    status=status.HTTP_404_NOT_FOUND
                )

        # Validate group_by parameter
        valid_group_by = ['event_type', 'resource_type', 'workspace', 'user', 'day']
        if group_by not in valid_group_by:
            return Response(
                {"detail": f"Invalid group_by parameter. Must be one of: {', '.join(valid_group_by)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get usage summary
        summary = get_tenant_usage_summary(
            tenant=tenant,
            start_date=start_date,
            end_date=end_date,
            event_type=event_type,
            workspace=workspace,
            group_by=group_by,
        )

        return Response({
            'tenant': {
                'id': str(tenant.id),
                'slug': tenant.slug,
                'name': tenant.name,
            },
            'filters': {
                'start_date': start_date.isoformat() if start_date else None,
                'end_date': end_date.isoformat() if end_date else None,
                'event_type': event_type,
                'workspace_id': workspace_id,
                'group_by': group_by,
            },
            'summary': summary,
        })


class UsageRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API endpoint for viewing usage records.
    Automatically filters to current tenant's usage records.

    Endpoints:
    - GET /api/tenant/usage/records/ - List all usage records for current tenant
    - GET /api/tenant/usage/records/{id}/ - Retrieve usage record details

    Query parameters:
    - event_type: Filter by event type
    - workspace_id: Filter by workspace ID
    - user_id: Filter by user ID
    - start_date: ISO date string
    - end_date: ISO date string
    """
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = UsageRecordSerializer

    def get_queryset(self):
        """Filter to current tenant based on user."""
        from datetime import datetime

        user = self.request.user
        tenant = user.tenants.first() if user and user.is_authenticated else None

        if not tenant:
            return UsageRecord.objects.none()

        queryset = UsageRecord.objects.filter(tenant=tenant).select_related(
            'tenant', 'workspace', 'user'
        )

        # Apply filters from query parameters
        event_type = self.request.query_params.get('event_type')
        if event_type:
            queryset = queryset.filter(event_type=event_type)

        workspace_id = self.request.query_params.get('workspace_id')
        if workspace_id:
            queryset = queryset.filter(workspace_id=workspace_id)

        user_id = self.request.query_params.get('user_id')
        if user_id:
            queryset = queryset.filter(user_id=user_id)

        start_date_str = self.request.query_params.get('start_date')
        if start_date_str:
            try:
                start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                queryset = queryset.filter(created_at__gte=start_date)
            except ValueError:
                pass  # Ignore invalid date format

        end_date_str = self.request.query_params.get('end_date')
        if end_date_str:
            try:
                end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                queryset = queryset.filter(created_at__lte=end_date)
            except ValueError:
                pass  # Ignore invalid date format

        return queryset
