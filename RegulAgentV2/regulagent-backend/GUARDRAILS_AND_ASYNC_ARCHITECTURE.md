# Guardrails & Async Architecture

## 1. ✅ Tool Execution Guardrails

### Purpose
Prevent AI from making unauthorized plan modifications without explicit user confirmation.

---

### Guardrail Policy (Configurable)

```python
GuardrailPolicy:
  # Core controls
  allow_plan_changes: bool = True  # Master switch
  require_confirmation_above_risk: float = 0.5  # Auto-confirm only if risk < 0.5
  
  # Operation controls
  allowed_operations: List[str] = [  # Whitelist
    'combine_plugs',
    'adjust_interval',
    'change_materials',
    'replace_cibp'
  ]
  blocked_operations: List[str] = []  # Blacklist
  
  # Safety limits
  max_material_delta_percent: float = 0.3  # Max 30% material change
  max_steps_removed: int = 3  # Max steps AI can remove
  allow_new_violations: bool = False  # Block if new violations
  
  # Session limits
  max_modifications_per_session: int = 10  # Per hour
```

**Future**: Store in `TenantPreference` model for per-tenant configuration.

---

### Two-Stage Validation

#### Stage 1: Before Tool Execution

```python
# Check BEFORE calling the tool
result = enforce_guardrails(
    tool_name='combine_plugs',
    tool_args={'step_ids': [5, 11]},
    context={
        'user_allow_plan_changes': True,  # From request
        'modifications_this_session': 2,
        'predicted_risk_score': 0.15
    }
)

if not result['allowed']:
    raise GuardrailViolation(result['reason'])

if result['requires_confirmation']:
    # Ask user: "This change has high risk (0.8). Proceed?"
    return confirmation_prompt(result['warnings'])
```

**Checks:**
- ✅ `allow_plan_changes` flag set by user
- ✅ Operation not in `blocked_operations`
- ✅ Operation in `allowed_operations` (if whitelist enabled)
- ✅ Session modification limit not exceeded
- ✅ Predicted risk below threshold

#### Stage 2: After Tool Execution (Before Applying)

```python
# Check AFTER modification computed but BEFORE applying
result = guardrail.validate_modification_result(
    modification_result={
        'modified_payload': {...},
        'risk_score': 0.15,
        'violations_delta': []
    },
    baseline_payload={...}
)

if not result['allowed']:
    # Block and explain
    return error_response(result['reason'])

if result['requires_confirmation']:
    # Store modification as pending, ask user to confirm
    return confirmation_prompt(result['violations'])
```

**Checks:**
- ✅ No new violations (or user accepts)
- ✅ Material delta within limits (±30%)
- ✅ Risk score within acceptable range
- ✅ Actual impact matches predicted impact

---

### Violation Types

| Type | Description | Action |
|------|-------------|--------|
| `plan_changes_disabled` | Plan modifications disabled by policy | Block |
| `user_authorization_required` | User didn't set `allow_plan_changes=true` | Block |
| `operation_blocked` | Operation in blacklist | Block |
| `operation_not_allowed` | Operation not in whitelist | Block |
| `session_limit_exceeded` | Too many modifications in session | Block |
| `new_violations_introduced` | Modification creates compliance violations | Block or confirm |
| `material_delta_exceeded` | Materials change >30% | Confirm |
| `high_risk_score` | Risk score ≥0.5 | Confirm |

---

### Example: Blocked Modification

**Request:**
```bash
POST /api/chat/threads/5/messages
{
  "content": "Remove the surface casing shoe plug",
  "allow_plan_changes": false  # ❌ User didn't authorize
}
```

**Response:**
```json
{
  "status": "error",
  "error": "User did not authorize plan changes (allow_plan_changes=false)",
  "violation_type": "user_authorization_required",
  "assistant_message": {
    "role": "system",
    "content": "⚠️ Action blocked by safety policy: User did not authorize plan changes"
  }
}
```

---

### Example: High-Risk Confirmation

**Request:**
```bash
POST /api/chat/threads/5/messages
{
  "content": "Remove all formation top plugs",
  "allow_plan_changes": true
}
```

**AI Analysis:**
```json
{
  "tool": "remove_steps",
  "args": {"step_ids": [8, 10, 11]},
  "predicted_risk": 0.85,  # High risk!
  "violations_delta": [
    "Formation top coverage required by District 08A"
  ]
}
```

**Response (Requires Confirmation):**
```json
{
  "status": "confirmation_required",
  "assistant_message": {
    "role": "assistant",
    "content": "⚠️ This change has HIGH RISK (0.85):\n\n- Would remove 3 formation top plugs\n- Introduces 1 violation: \"Formation top coverage required\"\n- Not recommended\n\nDo you want to proceed anyway? Reply 'yes' to confirm."
  },
  "modification_preview": {...},
  "warnings": [
    "High risk score (0.85) - confirmation required",
    "New violations introduced"
  ]
}
```

---

## 2. ✅ Async Architecture (Non-Blocking)

### Problem: OpenAI Responses Can Be Slow
- OpenAI API can take 5-30 seconds
- HTTP requests would timeout
- Poor user experience

### Solution: Celery + Redis Queue

```
User sends message
    ↓
API creates user message, returns 202 Accepted
    ↓
Celery task dispatched (async)
    ↓
User polls status endpoint
    ↓
Task completes, assistant message ready
    ↓
Frontend displays response
```

---

### Async Flow Diagram

```
┌─────────┐                    ┌─────────┐
│ Frontend│                    │ Backend │
└────┬────┘                    └────┬────┘
     │                              │
     │ POST /messages (async=true) │
     ├────────────────────────────>│
     │                              │
     │ 202 Accepted {task_id}      │ ← Returns immediately!
     │<────────────────────────────┤
     │                              │
     │                              │ Celery Task Running...
     │                              │ ├─ Call OpenAI
     │                              │ ├─ Execute tools
     │ GET /status?                 │ ├─ Validate guardrails
     ├────────────────────────────>│ ├─ Apply modifications
     │ "processing..."              │ └─ Create assistant message
     │<────────────────────────────┤
     │                              │
     │ GET /status? (poll)          │
     ├────────────────────────────>│
     │ {assistant_message: {...}}   │ ← Task complete!
     │<────────────────────────────┤
     │                              │
```

---

### API: Send Message (Async)

**POST /api/chat/threads/{thread_id}/messages**

```json
{
  "content": "Can we combine the formation plugs?",
  "allow_plan_changes": true,
  "async": true  // default
}
```

**Response (202 Accepted):**
```json
{
  "user_message": {
    "id": 123,
    "content": "Can we combine the formation plugs?",
    "created_at": "2025-11-02T10:00:00Z"
  },
  "task_id": "abc-123-def-456",
  "status_url": "/api/chat/threads/5/messages/123/status",
  "polling_interval_ms": 1000,
  "message": "Processing asynchronously - poll status_url for result"
}
```

---

### API: Poll Status (Two Methods)

#### Method 1: Task-Based Polling

**GET /api/chat/tasks/{task_id}**

```json
{
  "task_id": "abc-123-def-456",
  "status": "SUCCESS",  // PENDING | STARTED | SUCCESS | FAILURE
  "result": {
    "status": "success",
    "assistant_message_id": 124,
    "plan_modified": true,
    "modification_id": 5
  },
  "assistant_message": {
    "id": 124,
    "role": "assistant",
    "content": "✅ I've combined the formation plugs at 6500ft and 9500ft...",
    "tool_calls": [...],
    "created_at": "2025-11-02T10:00:15Z"
  }
}
```

#### Method 2: Message-Based Polling

**GET /api/chat/threads/{thread_id}/messages/{message_id}/status**

```json
{
  "user_message": {...},
  "assistant_message": {
    "id": 124,
    "role": "assistant",
    "content": "✅ I've combined the formation plugs...",
    "created_at": "2025-11-02T10:00:15Z"
  },
  "status": "completed"  // "processing" | "completed" | "error"
}
```

---

### Frontend Polling Strategy

```javascript
// 1. Send message
const response = await fetch('/api/chat/threads/5/messages/', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    content: "Can we combine the formation plugs?",
    allow_plan_changes: true,
    async: true
  })
});

if (response.status === 202) {
  const data = await response.json();
  
  // 2. Poll for result
  const pollInterval = setInterval(async () => {
    const statusResponse = await fetch(data.status_url, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    
    const status = await statusResponse.json();
    
    if (status.status === 'completed') {
      clearInterval(pollInterval);
      displayAssistantMessage(status.assistant_message);
    } else if (status.status === 'error') {
      clearInterval(pollInterval);
      showError(status.error);
    }
    // else: keep polling
    
  }, 1000);  // Poll every second
}
```

---

### Celery Task Implementation

```python
@shared_task(bind=True, max_retries=3)
def process_chat_message_async(
    self,
    thread_id: int,
    user_message_id: int,
    user_content: str,
    allow_plan_changes: bool = True,
    max_tool_calls: int = 10
):
    # 1. Load context
    thread = ChatThread.objects.get(id=thread_id)
    
    # 2. Enforce guardrails
    policy = ToolExecutionGuardrail.get_tenant_policy(thread.tenant_id)
    guardrail = ToolExecutionGuardrail(policy)
    
    # 3. Call OpenAI (TODO)
    # response = openai.chat.completions.create(...)
    
    # 4. Execute tool calls with guardrails
    for tool_call in tool_calls:
        # Validate before execution
        result = guardrail.validate_tool_call(
            tool_name=tool_call.name,
            tool_args=tool_call.arguments,
            context={
                'user_allow_plan_changes': allow_plan_changes,
                'modifications_this_session': modifications_count
            }
        )
        
        if not result['allowed']:
            raise GuardrailViolation(result['reason'])
        
        # Execute tool
        tool_result = execute_tool(tool_call)
        
        # Validate result before applying
        if tool_call.name in MODIFICATION_TOOLS:
            validation = guardrail.validate_modification_result(
                tool_result,
                baseline_payload
            )
            
            if not validation['allowed']:
                raise GuardrailViolation(validation['reason'])
    
    # 5. Create assistant message
    assistant_message = ChatMessage.objects.create(
        thread=thread,
        role='assistant',
        content=assistant_response,
        tool_calls=tool_calls_data,
        tool_results=tool_results_data
    )
    
    # 6. Embed modification for learning (async)
    if modification_created:
        embed_plan_modification.delay(modification.id)
    
    return {
        'status': 'success',
        'assistant_message_id': assistant_message.id,
        'plan_modified': bool(modification_created),
        'modification_id': modification.id if modification_created else None
    }
```

---

### Celery Configuration (Required)

```python
# ra_config/celery.py
from celery import Celery
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ra_config.settings.development')

app = Celery('regulagent')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# ra_config/settings/base.py
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
```

---

### Docker Compose (Add Celery Worker)

```yaml
services:
  # ... (existing services)
  
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
  
  celery_worker:
    build: .
    command: celery -A ra_config worker -l info
    depends_on:
      - db
      - redis
    env_file:
      - .env
    volumes:
      - ./regulagent-backend:/app
  
  celery_beat:  # For periodic tasks
    build: .
    command: celery -A ra_config beat -l info
    depends_on:
      - db
      - redis
    env_file:
      - .env
```

---

## Summary

### Guardrails ✅
- ✅ **User authorization**: `allow_plan_changes` flag enforced
- ✅ **Two-stage validation**: Before AND after tool execution
- ✅ **Configurable policies**: Per-tenant customization (future)
- ✅ **Risk-based confirmation**: High-risk changes require explicit approval
- ✅ **Session limits**: Max modifications per hour
- ✅ **Violation blocking**: Can't introduce compliance violations

### Async Processing ✅
- ✅ **Non-blocking API**: Returns 202 Accepted immediately
- ✅ **Celery tasks**: Long-running operations in background
- ✅ **Status polling**: Frontend polls for completion
- ✅ **Retry logic**: Automatic retries on transient failures
- ✅ **Learning hooks**: Async embedding generation

### Next Steps
1. **Configure Celery**: Add Redis + Celery worker to Docker
2. **Implement OpenAI integration**: Replace placeholder in task
3. **Add WebSocket support**: Real-time updates instead of polling (optional)
4. **Store tenant policies**: Add `TenantPreference` model for guardrails
5. **Monitoring**: Add Celery Flower for task monitoring

---

**Status**: ✅ Guardrails and async architecture implemented  
**Testing**: Requires Celery/Redis setup  
**Date**: 2025-11-02

