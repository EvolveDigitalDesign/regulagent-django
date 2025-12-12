"""
OpenAI Chat Completions service with:
- Structured outputs (strict=True)
- Prompt caching for system context
- Streaming support
- Function calling with guardrails
"""

import logging
import os
from typing import List, Dict, Any, Generator, Optional
import json

from django.conf import settings

from apps.assistant.models import ChatThread, ChatMessage
from apps.assistant.tools.schemas import TOOL_DEFINITIONS
from apps.assistant.tools import executors
from apps.public_core.services.openai_config import (
    get_openai_client,
    DEFAULT_CHAT_MODEL,
    TEMPERATURE_LOW,
    log_openai_usage,
    check_rate_limit,
)

logger = logging.getLogger(__name__)

# Initialize OpenAI client using central config
client = get_openai_client()


# System prompt (will be cached via prompt caching)
SYSTEM_PROMPT = """You are RegulAgent AI, a specialized assistant for Texas Railroad Commission (RRC) well plugging compliance.

**Your Core Responsibilities:**
1. Help engineers create compliant W-3A plugging plans
2. Answer questions about formations, casing, and regulatory requirements
3. Suggest plan optimizations while maintaining 100% compliance
4. Explain regulatory reasoning clearly

**Critical Rules (NEVER VIOLATE):**
- COMPLIANCE FIRST: All suggestions must meet TX TAC Chapter 3 requirements
- EXPLICIT CONSENT: Never modify plans without user confirmation
- EXPLAINABILITY: Always explain WHY a suggestion is compliant
- RISK TRANSPARENCY: Show risk scores and violation deltas before changes
- PRECEDENT-BASED: Reference similar approved plans when suggesting modifications

**Available Tools:**
1. `get_plan_snapshot` - View current plan details
2. `answer_fact` - Query well/formation data
3. `combine_plugs` - Merge adjacent formation plugs
4. `replace_cibp_with_long_plug` - Replace CIBP with cement plug
5. `recalc_materials_and_export` - Recalculate after modifications

**Interaction Style:**
- Be concise but thorough
- Use technical terminology correctly
- Ask clarifying questions when needed
- Show confidence scores for suggestions
- Highlight compliance implications

**Safety Guardrails:**
- Check violations_delta before suggesting changes
- Require user confirmation for risk_score > 0.5
- Never silently modify plans
- Always preserve audit trail

Remember: You augment engineers' expertise, you don't replace it. When in doubt, ask the user."""


def build_messages_for_openai(
    thread: ChatThread,
    new_user_message: str,
    include_plan_context: bool = True
) -> List[Dict[str, Any]]:
    """
    Build message array for OpenAI API.
    
    Structure for prompt caching:
    1. System prompt (cached)
    2. Plan context (cached if unchanged)
    3. Conversation history
    4. New user message
    """
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]
    
    # Add plan context (will be cached when plan doesn't change)
    if include_plan_context:
        plan = thread.current_plan
        plan_context = f"""
**Current Plan Context:**
- API: {plan.well.api14}
- Operator: {plan.well.operator_name or 'Unknown'}
- Field: {plan.well.field_name or 'Unknown'}
- County: {plan.well.county or 'Unknown'}
- Lease: {plan.well.lease_name or 'Unknown'}
- Well Number: {plan.well.well_number or 'Unknown'}
- Status: {plan.status}
- Steps: {len(plan.payload.get('steps', []))}
- Violations: {len(plan.payload.get('violations', []))}
- Formations: {', '.join(plan.payload.get('formations_targeted', []))}

**Plan ID:** {plan.plan_id}
"""
        messages.append({
            "role": "system",
            "content": plan_context
        })
    
    # Add conversation history (last 10 messages for context window management)
    history = thread.messages.order_by('created_at')[:10]
    for msg in history:
        messages.append({
            "role": msg.role,
            "content": msg.content,
            # Include tool calls if present
            **({"tool_calls": msg.tool_calls} if msg.tool_calls else {}),
            **({"tool_call_id": msg.metadata.get('tool_call_id')} if msg.role == "tool" else {})
        })
    
    # Add new user message
    messages.append({
        "role": "user",
        "content": new_user_message
    })
    
    return messages


def execute_tool_call(
    tool_name: str,
    tool_args: Dict[str, Any],
    thread: ChatThread,
    user,
    allow_plan_changes: bool = False
) -> Dict[str, Any]:
    """
    Execute a tool call and return results.
    
    Routes to appropriate executor based on tool_name.
    """
    logger.info(f"Executing tool: {tool_name} with args: {tool_args}")
    
    try:
        if tool_name == "get_plan_snapshot":
            return executors.execute_get_plan_snapshot(
                plan_id=tool_args.get('plan_id'),
                thread=thread
            )
        
        elif tool_name == "answer_fact":
            return executors.execute_answer_fact(
                question=tool_args.get('question'),
                search_scope=tool_args.get('search_scope', 'all'),
                thread=thread
            )
        
        elif tool_name == "combine_plugs":
            return executors.execute_combine_plugs(
                step_ids=tool_args.get('step_ids', []),
                reason=tool_args.get('reason', ''),
                thread=thread,
                user=user,
                allow_plan_changes=allow_plan_changes
            )
        
        elif tool_name == "replace_cibp_with_long_plug":
            return executors.execute_replace_cibp(
                interval=tool_args.get('interval'),
                custom_top_depth=tool_args.get('custom_top_depth'),
                custom_base_depth=tool_args.get('custom_base_depth'),
                reason=tool_args.get('reason', ''),
                thread=thread,
                user=user,
                allow_plan_changes=allow_plan_changes
            )
        
        elif tool_name == "recalc_materials_and_export":
            return executors.execute_recalc_materials(
                revalidate_compliance=tool_args.get('revalidate_compliance', True),
                thread=thread
            )
        
        elif tool_name == "change_plug_type":
            return executors.execute_change_plug_type(
                new_type=tool_args.get('new_type'),
                apply_to_all=tool_args.get('apply_to_all', False),
                step_ids=tool_args.get('step_ids'),
                formations=tool_args.get('formations'),
                reason=tool_args.get('reason', ''),
                thread=thread,
                user=user,
                allow_plan_changes=allow_plan_changes
            )
        
        elif tool_name == "remove_steps":
            return executors.execute_remove_steps(
                step_ids=tool_args.get('step_ids', []),
                reason=tool_args.get('reason', ''),
                thread=thread,
                user=user,
                allow_plan_changes=allow_plan_changes
            )
        
        elif tool_name == "add_plug":
            return executors.execute_add_plug(
                type=tool_args.get('type'),
                top_ft=tool_args.get('top_ft'),
                bottom_ft=tool_args.get('bottom_ft'),
                custom_sacks=tool_args.get('custom_sacks'),
                cement_class=tool_args.get('cement_class'),
                placement_reason=tool_args.get('placement_reason', ''),
                thread=thread,
                user=user,
                allow_plan_changes=allow_plan_changes
            )
        
        elif tool_name == "add_formation_plugs":
            return executors.execute_add_formation_plugs(
                formations=tool_args.get('formations', []),
                placement_reason=tool_args.get('placement_reason', ''),
                thread=thread,
                user=user,
                allow_plan_changes=allow_plan_changes
            )
        
        elif tool_name == "override_step_materials":
            return executors.execute_override_materials(
                step_id=tool_args.get('step_id'),
                sacks=tool_args.get('sacks'),
                reason=tool_args.get('reason', ''),
                thread=thread,
                user=user,
                allow_plan_changes=allow_plan_changes
            )
        
        else:
            return {
                "success": False,
                "message": f"Unknown tool: {tool_name}"
            }
    
    except Exception as e:
        logger.exception(f"Error executing tool {tool_name}")
        return {
            "success": False,
            "message": f"Tool execution error: {str(e)}"
        }


def process_chat_with_openai(
    thread: ChatThread,
    user_message_content: str,
    user,
    allow_plan_changes: bool = True,
    max_tool_calls: int = 10,
) -> Dict[str, Any]:
    """
    Process chat message with OpenAI and execute tool calls.
    
    Args:
        thread: ChatThread instance
        user_message_content: User's message
        user: User instance
        allow_plan_changes: Whether to allow plan modifications
        max_tool_calls: Maximum number of tool iterations
    
    Returns:
        Dict with 'content', 'tool_calls', and 'model' keys
    
    Note: This is non-streaming. Streaming will be implemented as a separate function.
    """
    messages = build_messages_for_openai(thread, user_message_content)
    
    tool_iterations = 0
    final_response = None
    
    while tool_iterations < max_tool_calls:
        try:
            # Check rate limit before making request (prevents 429 errors)
            check_rate_limit(estimated_tokens=15000)
            
            # Call OpenAI with optimized settings
            response = client.chat.completions.create(
                model=DEFAULT_CHAT_MODEL,  # Configurable via env (default: gpt-4o)
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",  # Let model decide when to use tools
                stream=False,  # Non-streaming for now
                temperature=TEMPERATURE_LOW,  # Low temperature for consistent, factual responses
            )
            
            message = response.choices[0].message
            
            # Log usage for cost tracking and rate limit updates
            log_openai_usage(response, f"chat_thread_{thread.id}")
            
            # If no tool calls, we're done
            if not message.tool_calls:
                final_response = {
                    "content": message.content,
                    "tool_calls": [],
                    "model": DEFAULT_CHAT_MODEL,
                }
                break
            
            # Process tool calls
            messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    } for tc in message.tool_calls
                ]
            })
            
            # Execute each tool call
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)
                
                # Execute tool
                tool_result = execute_tool_call(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    thread=thread,
                    user=user,
                    allow_plan_changes=allow_plan_changes
                )
                
                # Add tool result to conversation
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(tool_result)
                })
            
            tool_iterations += 1
        
        except Exception as e:
            logger.exception("Error in OpenAI API call")
            return {
                "error": str(e),
                "content": f"Sorry, I encountered an error: {str(e)}",
                "tool_calls": []
            }
    
    if tool_iterations >= max_tool_calls:
        logger.warning(f"Hit max tool iterations ({max_tool_calls}) for thread {thread.id}")
        return {
            "content": "I've reached the maximum number of operations for this request. Please try breaking it into smaller steps.",
            "tool_calls": [],
            "warning": "max_tool_calls_reached"
        }
    
    return final_response


def get_available_models() -> List[str]:
    """
    Get list of available OpenAI models.
    
    Useful for admin/debugging.
    """
    try:
        models = client.models.list()
        return [m.id for m in models.data if 'gpt' in m.id]
    except Exception as e:
        logger.exception("Error fetching OpenAI models")
        return []

