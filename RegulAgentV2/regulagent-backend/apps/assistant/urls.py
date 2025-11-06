"""
URL routing for chat and assistant endpoints.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.assistant.views import (
    ChatThreadViewSet,
    ChatMessageView,
    RegulatorOutcomeListView,
    RegulatorOutcomeDetailView,
    mark_outcome_approved,
    mark_outcome_rejected,
    get_outcome_statistics,
)
from apps.assistant.views.plan_versions import (
    get_plan_version_history,
    revert_to_version,
    compare_plan_versions,
)
from apps.assistant.views.task_status import TaskStatusView, MessageStatusView
from apps.assistant.views.thread_debug import debug_thread_permissions

# Router for ChatThread ViewSet
router = DefaultRouter()
router.register(r'threads', ChatThreadViewSet, basename='chat-thread')

urlpatterns = [
    # Thread management (CRUD)
    # GET    /api/chat/threads - List threads
    # POST   /api/chat/threads - Create thread
    # GET    /api/chat/threads/{id} - Get thread
    # PATCH  /api/chat/threads/{id} - Update thread
    # DELETE /api/chat/threads/{id} - Archive thread
    # POST   /api/chat/threads/{id}/share/ - Share thread
    path('', include(router.urls)),
    
    # Messages within a thread
    # GET  /api/chat/threads/{thread_id}/messages - List messages
    # POST /api/chat/threads/{thread_id}/messages - Send message (async by default)
    path('threads/<int:thread_id>/messages/', ChatMessageView.as_view(), name='chat-messages'),
    
    # Message status polling (for async responses)
    # GET /api/chat/threads/{thread_id}/messages/{message_id}/status - Check if response ready
    path('threads/<int:thread_id>/messages/<int:message_id>/status/', MessageStatusView.as_view(), name='message-status'),
    
    # Debug permissions (development only)
    # GET /api/chat/threads/{thread_id}/debug-permissions - Check user permissions
    path('threads/<int:thread_id>/debug-permissions/', debug_thread_permissions, name='debug-thread-permissions'),
    
    # Task status polling (alternative method)
    # GET /api/chat/tasks/{task_id} - Check Celery task status
    path('tasks/<str:task_id>/', TaskStatusView.as_view(), name='task-status'),
    
    # Version history and revert
    # POST /api/chat/threads/{thread_id}/revert - Revert to previous version
    path('threads/<int:thread_id>/revert/', revert_to_version, name='chat-revert'),
    
    # Regulator outcomes
    # GET  /api/chat/outcomes - List outcomes
    # POST /api/chat/outcomes - Create outcome
    path('outcomes/', RegulatorOutcomeListView.as_view(), name='outcome-list'),
    
    # GET /api/chat/outcomes/{id} - Get outcome details
    path('outcomes/<int:outcome_id>/', RegulatorOutcomeDetailView.as_view(), name='outcome-detail'),
    
    # PATCH /api/chat/outcomes/{id}/approve - Mark approved
    path('outcomes/<int:outcome_id>/approve/', mark_outcome_approved, name='outcome-approve'),
    
    # PATCH /api/chat/outcomes/{id}/reject - Mark rejected
    path('outcomes/<int:outcome_id>/reject/', mark_outcome_rejected, name='outcome-reject'),
    
    # GET /api/chat/outcomes/stats - Get statistics
    path('outcomes/stats/', get_outcome_statistics, name='outcome-stats'),
]

# Plan version endpoints (outside of /chat/ namespace)
plan_version_urls = [
    # GET /api/plans/{plan_id}/versions - Get version history
    path('<str:plan_id>/versions/', get_plan_version_history, name='plan-versions'),
    
    # GET /api/plans/compare/{id1}/{id2} - Compare two snapshots
    path('compare/<int:snapshot_id_1>/<int:snapshot_id_2>/', compare_plan_versions, name='plan-compare'),
]

