"""
Chat message endpoints.

Endpoints:
- POST /api/chat/threads/{thread_id}/messages - Send message and get AI response
- GET /api/chat/threads/{thread_id}/messages - List thread messages
"""

import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.assistant.models import ChatThread, ChatMessage
from apps.assistant.serializers import (
    ChatMessageSerializer,
    ChatMessageCreateSerializer,
)
from celery.result import AsyncResult

logger = logging.getLogger(__name__)


class ChatMessageView(APIView):
    """
    Handle chat messages within a thread.
    """
    
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    
    def get(self, request, thread_id):
        """
        List messages in a thread.
        
        GET /api/chat/threads/{thread_id}/messages
        """
        thread = get_object_or_404(ChatThread, id=thread_id)
        
        # Check view permission
        if not thread.can_view(request.user):
            return Response(
                {"error": "You do not have permission to view this thread"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        messages = thread.messages.all().order_by('created_at')
        
        # Pagination
        limit = int(request.query_params.get('limit', 50))
        offset = int(request.query_params.get('offset', 0))
        
        paginated_messages = messages[offset:offset + limit]
        
        serializer = ChatMessageSerializer(paginated_messages, many=True)
        
        return Response({
            'thread_id': thread.id,
            'messages': serializer.data,
            'pagination': {
                'total': messages.count(),
                'limit': limit,
                'offset': offset,
                'has_more': messages.count() > (offset + limit)
            }
        })
    
    def post(self, request, thread_id):
        """
        Send a message and get AI assistant response (ASYNC).
        
        POST /api/chat/threads/{thread_id}/messages
        {
          "content": "Can we combine the formation plugs at 6500 ft and 9500 ft?",
          "allow_plan_changes": true,
          "async": true  // default: true
        }
        
        Async Response (202 Accepted):
        {
          "user_message": {...},
          "task_id": "abc-123-def",
          "status_url": "/api/chat/threads/{thread_id}/messages/{message_id}/status",
          "polling_interval_ms": 1000
        }
        
        Sync Response (201 Created) - if async=false:
        {
          "user_message": {...},
          "assistant_message": {...},
          "plan_modification": {...}
        }
        """
        from apps.assistant.tasks import process_chat_message_async
        from apps.assistant.services.guardrails import GuardrailPolicy, ToolExecutionGuardrail
        
        thread = get_object_or_404(ChatThread, id=thread_id)
        
        # Check edit permission (only owner can send messages)
        if not thread.can_edit(request.user):
            return Response(
                {"error": "Only the thread owner can send messages"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = ChatMessageCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        user_content = data['content']
        allow_plan_changes = data.get('allow_plan_changes', True)
        max_tool_calls = data.get('max_tool_calls', 10)
        use_async = request.data.get('async', True)  # Default to async
        
        # Create user message
        user_message = ChatMessage.objects.create(
            thread=thread,
            role=ChatMessage.ROLE_USER,
            content=user_content
        )
        
        # Update thread's last_message_at
        thread.last_message_at = timezone.now()
        thread.save(update_fields=['last_message_at'])
        
        logger.info(
            f"User {request.user.email} sent message in ChatThread {thread.id} "
            f"(allow_plan_changes={allow_plan_changes}, async={use_async})"
        )
        
        if use_async:
            # Dispatch Celery task for async processing
            task = process_chat_message_async.delay(
                thread_id=thread.id,
                user_message_id=user_message.id,
                user_content=user_content,
                allow_plan_changes=allow_plan_changes,
                max_tool_calls=max_tool_calls
            )
            
            return Response({
                'user_message': ChatMessageSerializer(user_message).data,
                'task_id': task.id,
                'status_url': f'/api/chat/threads/{thread_id}/messages/{user_message.id}/status',
                'polling_interval_ms': 1000,
                'message': 'Processing asynchronously - poll status_url for result'
            }, status=status.HTTP_202_ACCEPTED)
        
        else:
            # Synchronous processing (blocks until complete)
            # NOT RECOMMENDED for production - can timeout on slow OpenAI responses
            
            # Placeholder response
            assistant_content = (
                "🚧 Synchronous AI processing\n\n"
                f"You asked: \"{user_content}\"\n\n"
                "Guardrails:\n"
                f"- Plan changes: {'✅ Allowed' if allow_plan_changes else '❌ Blocked'}\n\n"
                "OpenAI integration pending..."
            )
            
            assistant_message = ChatMessage.objects.create(
                thread=thread,
                role=ChatMessage.ROLE_ASSISTANT,
                content=assistant_content,
                metadata={
                    'processed_sync': True,
                    'allow_plan_changes': allow_plan_changes
                }
            )
            
            return Response({
                'user_message': ChatMessageSerializer(user_message).data,
                'assistant_message': ChatMessageSerializer(assistant_message).data,
                'plan_modification': None,
                'note': 'OpenAI integration pending'
            }, status=status.HTTP_201_CREATED)

