"""
Chat thread management endpoints.

Endpoints:
- POST /api/chat/threads - Create new thread
- GET /api/chat/threads - List user's threads (owned + shared)
- GET /api/chat/threads/{id} - Get thread details
- PATCH /api/chat/threads/{id} - Update thread (owner only)
- POST /api/chat/threads/{id}/share - Share with users (owner only)
- DELETE /api/chat/threads/{id} - Archive thread (owner only)
"""

import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.shortcuts import get_object_or_404
from django.db.models import Q

from apps.assistant.models import ChatThread
from apps.assistant.serializers import (
    ChatThreadCreateSerializer,
    ChatThreadSerializer,
    ChatThreadShareSerializer,
)
from apps.public_core.models import WellRegistry, PlanSnapshot
from apps.tenants.models import User

logger = logging.getLogger(__name__)


class ChatThreadViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing chat threads.
    
    List: Returns threads user owns or has shared access to
    Create: Create new thread for a well/plan
    Retrieve: Get thread details (if user has access)
    Update: Modify thread metadata (owner only)
    Destroy: Archive thread (owner only)
    """
    
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = ChatThreadSerializer
    
    def get_queryset(self):
        """
        Return threads where user is owner or has shared access,
        scoped to user's tenant.
        """
        user = self.request.user
        user_tenant = user.tenants.first()
        
        if not user_tenant:
            return ChatThread.objects.none()
        
        # Threads user owns OR threads shared with user (within their tenant)
        return ChatThread.objects.filter(
            Q(created_by=user) | Q(shared_with=user),
            tenant_id=user_tenant.id
        ).distinct().select_related(
            'well',
            'baseline_plan',
            'current_plan',
            'created_by'
        ).prefetch_related('shared_with')
    
    def create(self, request):
        """
        Create a new chat thread for a well and plan.
        
        POST /api/chat/threads
        {
          "well_api14": "4200346118",
          "plan_id": "4200346118:combined",
          "title": "Discuss formation plug depths",
          "share_with_user_ids": [2, 3]  // optional
        }
        """
        user_tenant = request.user.tenants.first()
        if not user_tenant:
            return Response(
                {"error": "User not associated with any tenant"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = ChatThreadCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        
        # Get well
        try:
            well = WellRegistry.objects.get(api14=data['well_api14'])
        except WellRegistry.DoesNotExist:
            return Response(
                {"error": f"Well {data['well_api14']} not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get plan (try by plan_id first, then by database ID as fallback)
        plan_identifier = data['plan_id']
        plan = None
        
        # Try as plan_id (string like "4241501493:combined")
        try:
            plan = PlanSnapshot.objects.get(
                plan_id=plan_identifier,
                tenant_id=user_tenant.id
            )
        except PlanSnapshot.DoesNotExist:
            # If it looks like an integer, try as database ID
            if plan_identifier.isdigit():
                try:
                    plan = PlanSnapshot.objects.get(
                        id=int(plan_identifier),
                        tenant_id=user_tenant.id
                    )
                    logger.warning(
                        f"Frontend sent database ID ({plan_identifier}) instead of plan_id. "
                        f"Found plan: {plan.plan_id}"
                    )
                except PlanSnapshot.DoesNotExist:
                    pass
        
        if not plan:
            return Response(
                {
                    "error": f"Plan '{plan_identifier}' not found for your tenant",
                    "hint": "Use 'plan_id' (e.g., '4241501493:combined') not database 'id' (e.g., 3)"
                },
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Create thread
        thread = ChatThread.objects.create(
            tenant_id=user_tenant.id,
            created_by=request.user,
            well=well,
            baseline_plan=plan,
            current_plan=plan,  # Initially same as baseline
            title=data.get('title', ''),
            mode=data.get('mode', 'assistant'),
        )
        
        # Share with users if specified
        share_with_ids = data.get('share_with_user_ids', [])
        if share_with_ids:
            users_to_share = User.objects.filter(
                id__in=share_with_ids,
                tenants__id=user_tenant.id
            )
            thread.shared_with.set(users_to_share)
        
        logger.info(
            f"Created ChatThread {thread.id} for user {request.user.email} "
            f"(well: {well.api14}, plan: {plan.plan_id})"
        )
        
        response_serializer = ChatThreadSerializer(thread, context={'request': request})
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)
    
    def retrieve(self, request, pk=None):
        """
        Get thread details with message summary.
        
        GET /api/chat/threads/{id}
        """
        thread = self.get_object()
        
        # Check access permission
        if not thread.can_view(request.user):
            return Response(
                {"error": "You do not have permission to view this thread"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = self.get_serializer(thread)
        return Response(serializer.data)
    
    def update(self, request, pk=None):
        """
        Update thread metadata (title, is_active).
        Owner only.
        
        PATCH /api/chat/threads/{id}
        {
          "title": "New title",
          "is_active": false
        }
        """
        thread = self.get_object()
        
        # Check edit permission
        if not thread.can_edit(request.user):
            return Response(
                {"error": "Only the thread owner can edit this thread"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Only allow updating certain fields
        allowed_fields = {'title', 'is_active'}
        update_data = {k: v for k, v in request.data.items() if k in allowed_fields}
        
        if not update_data:
            return Response(
                {"error": "No valid fields to update"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        for field, value in update_data.items():
            setattr(thread, field, value)
        
        thread.save()
        
        serializer = self.get_serializer(thread)
        return Response(serializer.data)
    
    def destroy(self, request, pk=None):
        """
        Archive thread (soft delete by setting is_active=False).
        Owner only.
        
        DELETE /api/chat/threads/{id}
        """
        thread = self.get_object()
        
        # Check edit permission
        if not thread.can_edit(request.user):
            return Response(
                {"error": "Only the thread owner can archive this thread"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        thread.is_active = False
        thread.save()
        
        logger.info(f"Archived ChatThread {thread.id} by user {request.user.email}")
        
        return Response(
            {"message": "Thread archived successfully"},
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['post'])
    def share(self, request, pk=None):
        """
        Share or unshare thread with users.
        Owner only.
        
        POST /api/chat/threads/{id}/share/
        {
          "user_ids": [2, 3, 4],
          "action": "add"  // or "remove"
        }
        """
        thread = self.get_object()
        
        # Check edit permission
        if not thread.can_edit(request.user):
            return Response(
                {"error": "Only the thread owner can manage sharing"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = ChatThreadShareSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        user_ids = data['user_ids']
        action_type = data['action']
        
        # Get users in the same tenant
        user_tenant = request.user.tenants.first()
        users = User.objects.filter(
            id__in=user_ids,
            tenants__id=user_tenant.id
        )
        
        if len(users) != len(user_ids):
            found_ids = [u.id for u in users]
            missing_ids = [uid for uid in user_ids if uid not in found_ids]
            return Response(
                {"error": f"Users not found or not in your tenant: {missing_ids}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Apply sharing action
        if action_type == 'add':
            thread.shared_with.add(*users)
            message = f"Shared thread with {len(users)} user(s)"
        else:  # 'remove'
            thread.shared_with.remove(*users)
            message = f"Unshared thread from {len(users)} user(s)"
        
        logger.info(
            f"ChatThread {thread.id} sharing updated by {request.user.email}: "
            f"{action_type} {len(users)} users"
        )
        
        response_serializer = ChatThreadSerializer(thread, context={'request': request})
        return Response({
            "message": message,
            "thread": response_serializer.data
        })


