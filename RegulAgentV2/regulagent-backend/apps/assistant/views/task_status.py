"""
Task status polling endpoint for async chat processing.
"""

import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from celery.result import AsyncResult
from django.shortcuts import get_object_or_404

from apps.assistant.models import ChatThread, ChatMessage
from apps.assistant.serializers import ChatMessageSerializer

logger = logging.getLogger(__name__)


class TaskStatusView(APIView):
    """
    Check status of async task (for polling).
    """
    
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    
    def get(self, request, task_id):
        """
        Get status of an async task.
        
        GET /api/chat/tasks/{task_id}
        
        Response:
        {
          "task_id": "abc-123",
          "status": "PENDING" | "STARTED" | "SUCCESS" | "FAILURE",
          "result": {...},  // if SUCCESS
          "error": "...",   // if FAILURE
          "progress": {...} // if STARTED (optional)
        }
        """
        task_result = AsyncResult(task_id)
        
        response_data = {
            'task_id': task_id,
            'status': task_result.state,
        }
        
        if task_result.state == 'PENDING':
            response_data['message'] = 'Task is queued or processing'
        
        elif task_result.state == 'STARTED':
            response_data['message'] = 'Task is running'
            # Optional: Add progress info if task reports it
            if task_result.info:
                response_data['progress'] = task_result.info
        
        elif task_result.state == 'SUCCESS':
            result = task_result.result
            response_data['message'] = 'Task completed successfully'
            response_data['result'] = result
            
            # Fetch the assistant message if available
            if result and result.get('assistant_message_id'):
                try:
                    assistant_msg = ChatMessage.objects.get(id=result['assistant_message_id'])
                    response_data['assistant_message'] = ChatMessageSerializer(assistant_msg).data
                except ChatMessage.DoesNotExist:
                    pass
        
        elif task_result.state == 'FAILURE':
            response_data['message'] = 'Task failed'
            response_data['error'] = str(task_result.info)
        
        return Response(response_data)


class MessageStatusView(APIView):
    """
    Check status of a specific message (alternative to task-based polling).
    """
    
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    
    def get(self, request, thread_id, message_id):
        """
        Get status of a message and check for response.
        
        GET /api/chat/threads/{thread_id}/messages/{message_id}/status
        
        Response:
        {
          "user_message": {...},
          "assistant_message": {...} | null,
          "status": "processing" | "completed" | "error"
        }
        """
        thread = get_object_or_404(ChatThread, id=thread_id)
        
        # Check view permission
        if not thread.can_view(request.user):
            return Response(
                {"error": "You do not have permission to view this thread"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        user_message = get_object_or_404(ChatMessage, id=message_id, thread=thread)
        
        # Find assistant response (next message after user's)
        assistant_message = ChatMessage.objects.filter(
            thread=thread,
            role=ChatMessage.ROLE_ASSISTANT,
            created_at__gt=user_message.created_at
        ).order_by('created_at').first()
        
        if assistant_message:
            return Response({
                'user_message': ChatMessageSerializer(user_message).data,
                'assistant_message': ChatMessageSerializer(assistant_message).data,
                'status': 'completed'
            })
        else:
            # Still processing
            return Response({
                'user_message': ChatMessageSerializer(user_message).data,
                'assistant_message': None,
                'status': 'processing',
                'message': 'AI is still processing your request'
            }, status=status.HTTP_202_ACCEPTED)

