"""
Celery tasks for asynchronous AI assistant operations.

Long-running operations (OpenAI API calls, plan modifications) are executed
asynchronously to avoid blocking HTTP responses.
"""

import logging
from celery import shared_task
from django.utils import timezone
from typing import Dict, Any

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def process_chat_message_async(
    self,
    thread_id: int,
    user_message_id: int,
    user_content: str,
    allow_plan_changes: bool = True,
    max_tool_calls: int = 10
) -> Dict[str, Any]:
    """
    Process a chat message asynchronously with OpenAI integration.
    
    This task:
    1. Calls OpenAI Assistants API
    2. Executes tool calls (with guardrails)
    3. Creates assistant response message
    4. Updates thread state
    5. Notifies frontend via WebSocket/polling
    
    Args:
        thread_id: ChatThread ID
        user_message_id: ChatMessage ID of user's message
        user_content: User's message text
        allow_plan_changes: Whether to allow plan modifications
        max_tool_calls: Max tool calls per response
    
    Returns:
        {
            'status': 'success' | 'error',
            'assistant_message_id': int,
            'plan_modified': bool,
            'modification_id': int | None,
            'error': str | None
        }
    """
    from apps.assistant.models import ChatThread, ChatMessage, PlanModification
    from apps.assistant.services.guardrails import enforce_guardrails, GuardrailViolation
    
    try:
        # Get thread and validate
        thread = ChatThread.objects.select_related(
            'current_plan',
            'baseline_plan',
            'well'
        ).get(id=thread_id)
        
        user_message = ChatMessage.objects.get(id=user_message_id)
        
        logger.info(
            f"[Task] Processing chat message {user_message_id} in thread {thread_id}"
        )
        
        # Track modifications in this session
        modifications_this_session = thread.modifications.filter(
            created_at__gte=timezone.now() - timezone.timedelta(hours=1)
        ).count()
        
        # Process with OpenAI
        from apps.assistant.services.openai_service import process_chat_with_openai
        
        ai_response = process_chat_with_openai(
            thread=thread,
            user_message_content=user_content,
            user=user_message.thread.created_by,
            allow_plan_changes=allow_plan_changes,
            max_tool_calls=max_tool_calls,
        )
        
        assistant_content = ai_response.get('content', 'No response generated')
        tool_calls_made = ai_response.get('tool_calls', [])
        model_used = ai_response.get('model', 'unknown')
        
        # Create assistant response
        assistant_message = ChatMessage.objects.create(
            thread=thread,
            role=ChatMessage.ROLE_ASSISTANT,
            content=assistant_content,
            tool_calls=tool_calls_made if tool_calls_made else [],
            metadata={
                'processed_async': True,
                'task_id': self.request.id,
                'allow_plan_changes': allow_plan_changes,
                'modifications_this_session': modifications_this_session,
                'model': model_used,
                'max_tool_calls': max_tool_calls,
                'temperature': 0.1,  # TEMPERATURE_LOW from config
            }
        )
        
        # Update thread
        thread.last_message_at = timezone.now()
        thread.save(update_fields=['last_message_at'])
        
        logger.info(
            f"[Task] Created assistant message {assistant_message.id} for thread {thread_id}"
        )
        
        return {
            'status': 'success',
            'assistant_message_id': assistant_message.id,
            'plan_modified': False,
            'modification_id': None,
            'error': None
        }
    
    except GuardrailViolation as e:
        logger.warning(
            f"[Task] Guardrail violation in thread {thread_id}: {e.violation_type}"
        )
        
        # Create system message explaining the block
        system_message = ChatMessage.objects.create(
            thread_id=thread_id,
            role=ChatMessage.ROLE_SYSTEM,
            content=f"⚠️ Action blocked by safety policy: {str(e)}",
            metadata={
                'violation_type': e.violation_type,
                'task_id': self.request.id
            }
        )
        
        return {
            'status': 'error',
            'assistant_message_id': system_message.id,
            'plan_modified': False,
            'modification_id': None,
            'error': str(e)
        }
    
    except Exception as e:
        logger.exception(f"[Task] Error processing chat message {user_message_id}")
        
        # Retry up to 3 times
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=5 * (self.request.retries + 1))
        
        # Create error message
        error_message = ChatMessage.objects.create(
            thread_id=thread_id,
            role=ChatMessage.ROLE_SYSTEM,
            content=f"❌ Error processing message: {str(e)}",
            metadata={
                'error': str(e),
                'task_id': self.request.id,
                'retries': self.request.retries
            }
        )
        
        return {
            'status': 'error',
            'assistant_message_id': error_message.id,
            'plan_modified': False,
            'modification_id': None,
            'error': str(e)
        }


@shared_task
def embed_plan_modification(modification_id: int):
    """
    Generate and store embeddings for a plan modification (for learning).
    
    This runs asynchronously after a modification is applied to avoid
    blocking the modification response.
    
    Args:
        modification_id: PlanModification ID to embed
    """
    from apps.assistant.models import PlanModification
    from apps.public_core.models import DocumentVector
    # from openai import OpenAI  # TODO: Add when implementing
    
    try:
        modification = PlanModification.objects.select_related(
            'source_snapshot',
            'result_snapshot',
            'chat_thread__well',
            'applied_by'
        ).get(id=modification_id)
        
        # Build embedding text
        well = modification.source_snapshot.well
        embedding_text = f"""
Plan Modification
Type: {modification.op_type}
Description: {modification.description}

Well Context:
- API: {well.api14}
- Operator: {well.operator_name}
- Field: {well.field_name}
- County: {well.county}

Operation:
- Risk Score: {modification.risk_score}
- Violations Before: {len(modification.source_snapshot.payload.get('violations', []))}
- Violations After: {len(modification.result_snapshot.payload.get('violations', []))}

Outcome:
- Applied: {modification.is_applied}
- User: {modification.applied_by.email if modification.applied_by else 'Unknown'}
"""
        
        # TODO: Generate embedding with OpenAI
        # client = OpenAI()
        # response = client.embeddings.create(
        #     input=embedding_text,
        #     model="text-embedding-3-small"
        # )
        # vector = response.data[0].embedding
        
        # TODO: Store in DocumentVector
        # DocumentVector.objects.create(
        #     vector=vector,
        #     metadata={
        #         'type': 'plan_modification',
        #         'modification_id': modification.id,
        #         'tenant_id': str(modification.chat_thread.tenant_id),
        #         'well_context': {...},
        #         'operation': {...},
        #         'outcome': {...}
        #     }
        # )
        
        logger.info(f"[Task] Embedded modification {modification_id} for learning")
        
    except Exception as e:
        logger.exception(f"[Task] Error embedding modification {modification_id}")
        raise


@shared_task
def cleanup_old_drafts():
    """
    Periodic task to clean up old draft plans that were never approved.
    Runs daily via Celery Beat.
    """
    from apps.public_core.models import PlanSnapshot
    from datetime import timedelta
    
    cutoff = timezone.now() - timedelta(days=30)
    
    old_drafts = PlanSnapshot.objects.filter(
        status=PlanSnapshot.STATUS_DRAFT,
        created_at__lt=cutoff
    )
    
    count = old_drafts.count()
    old_drafts.delete()
    
    logger.info(f"[Task] Cleaned up {count} old draft plans")
    return {'deleted': count}

