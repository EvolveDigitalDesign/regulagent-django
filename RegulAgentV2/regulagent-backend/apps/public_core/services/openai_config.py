"""
Centralized OpenAI configuration for RegulAgent.

Best practices implemented (2025-11-02):
- Structured outputs (strict=True) for reliability
- Prompt caching for cost savings
- Latest models with function calling support
- Consistent temperature settings
- Proper error handling

All OpenAI integrations should import from this module for consistency.
"""

import os
from typing import Optional
from openai import OpenAI

# =============================================================================
# MODEL SELECTION
# =============================================================================

# Chat/Assistant Models (with function calling)
DEFAULT_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o")
DEFAULT_REASONING_MODEL = os.getenv("OPENAI_REASONING_MODEL", "o1")  # For complex compliance

# Document Processing Models
DEFAULT_EXTRACTION_MODEL = os.getenv("OPENAI_EXTRACTION_MODEL", "gpt-4o")
DEFAULT_CLASSIFIER_MODEL = os.getenv("OPENAI_CLASSIFIER_MODEL", "gpt-4o-mini")

# Batch Processing (50% cost savings)
DEFAULT_BATCH_MODEL = os.getenv("OPENAI_BATCH_MODEL", "gpt-4o")

# Embeddings
DEFAULT_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

# =============================================================================
# TEMPERATURE SETTINGS
# =============================================================================

# Low temperature for factual, deterministic responses
TEMPERATURE_FACTUAL = 0.0  # Document extraction, compliance checks
TEMPERATURE_LOW = 0.1  # Chat responses, plan modifications
TEMPERATURE_BALANCED = 0.5  # General conversation
TEMPERATURE_CREATIVE = 0.8  # Suggestions, explanations

# =============================================================================
# API CONFIGURATION
# =============================================================================

def get_openai_client(api_key: Optional[str] = None) -> OpenAI:
    """
    Get OpenAI client instance with proper configuration.
    
    Args:
        api_key: Optional API key override. If None, uses OPENAI_API_KEY env var.
    
    Returns:
        Configured OpenAI client
    
    Raises:
        RuntimeError: If API key not configured
    """
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY not configured. "
            "Set it in .env or pass api_key parameter."
        )
    
    return OpenAI(api_key=key)


# =============================================================================
# STRUCTURED OUTPUTS HELPERS
# =============================================================================

def create_json_schema(
    name: str,
    properties: dict,
    required: list,
    strict: bool = True,
    additional_properties: bool = False
) -> dict:
    """
    Create JSON schema for structured outputs.
    
    Args:
        name: Schema name
        properties: Property definitions
        required: List of required field names
        strict: Use strict mode (100% reliable, recommended)
        additional_properties: Allow fields not in schema
    
    Returns:
        OpenAI-compatible JSON schema
    
    Example:
        >>> schema = create_json_schema(
        ...     name="well_data",
        ...     properties={
        ...         "api": {"type": "string"},
        ...         "depth": {"type": "number"}
        ...     },
        ...     required=["api"]
        ... )
    """
    return {
        "name": name,
        "schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": additional_properties,
        },
        "strict": strict,
    }


# =============================================================================
# PROMPT CACHING PATTERNS
# =============================================================================

def build_cached_messages(
    system_prompt: str,
    context: str,
    user_message: str,
    history: Optional[list] = None
) -> list:
    """
    Build message array optimized for prompt caching.
    
    Caching strategy:
    1. System prompt (cached - reused across requests)
    2. Context (cached - reused when unchanged)
    3. History (varies per conversation)
    4. New user message (always fresh)
    
    Args:
        system_prompt: System instructions (will be cached)
        context: Static context like plan data (will be cached)
        user_message: New user input
        history: Previous messages in conversation
    
    Returns:
        Message list optimized for caching
    
    Savings:
        ~50% cost reduction on cached tokens (system + context)
    """
    messages = [
        {"role": "system", "content": system_prompt},
    ]
    
    if context:
        messages.append({
            "role": "system",
            "content": f"**Context (cached):**\n{context}"
        })
    
    if history:
        messages.extend(history)
    
    messages.append({
        "role": "user",
        "content": user_message
    })
    
    return messages


# =============================================================================
# USAGE TRACKING (Optional)
# =============================================================================

def log_openai_usage(response, operation: str):
    """
    Log OpenAI API usage for cost tracking.
    
    Args:
        response: OpenAI API response
        operation: Operation name (e.g., "document_extraction", "chat")
    
    Example:
        >>> response = client.chat.completions.create(...)
        >>> log_openai_usage(response, "chat_message")
    """
    try:
        usage = getattr(response, 'usage', None)
        if usage:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(
                f"OpenAI Usage [{operation}]: "
                f"prompt={usage.prompt_tokens} "
                f"completion={usage.completion_tokens} "
                f"total={usage.total_tokens}"
            )
    except Exception:
        pass  # Don't fail on logging errors


# =============================================================================
# RECOMMENDED SETTINGS BY USE CASE
# =============================================================================

SETTINGS_BY_USE_CASE = {
    "document_extraction": {
        "model": DEFAULT_EXTRACTION_MODEL,
        "temperature": TEMPERATURE_FACTUAL,
        "response_format": {"type": "json_object"},
        "max_tokens": 4000,
    },
    "chat_assistant": {
        "model": DEFAULT_CHAT_MODEL,
        "temperature": TEMPERATURE_LOW,
        "tools_enabled": True,
    },
    "compliance_check": {
        "model": DEFAULT_REASONING_MODEL,  # Use reasoning for complex logic
        "temperature": TEMPERATURE_FACTUAL,
    },
    "embeddings": {
        "model": DEFAULT_EMBEDDING_MODEL,
        "dimensions": 1536,  # Default for text-embedding-3-small
    },
    "batch_processing": {
        "model": DEFAULT_BATCH_MODEL,
        "temperature": TEMPERATURE_FACTUAL,
        "note": "50% cost savings vs sync"
    }
}


def get_recommended_settings(use_case: str) -> dict:
    """
    Get recommended OpenAI settings for a specific use case.
    
    Args:
        use_case: One of: document_extraction, chat_assistant, 
                  compliance_check, embeddings, batch_processing
    
    Returns:
        Dict of recommended settings
    
    Example:
        >>> settings = get_recommended_settings("chat_assistant")
        >>> response = client.chat.completions.create(**settings, messages=...)
    """
    return SETTINGS_BY_USE_CASE.get(use_case, {
        "model": DEFAULT_CHAT_MODEL,
        "temperature": TEMPERATURE_BALANCED
    })

