# OpenAI Integration - Implementation Summary

## ✅ What's Been Implemented

### 1. **Chat Completions API** (Not Assistants API)
**Why this choice:**
- ✅ More control over conversation flow
- ✅ Lower latency (no polling)
- ✅ Cost-effective (pay per token)
- ✅ Stateless (better for multi-tenancy)
- ✅ Built-in parallel tool calls

### 2. **Structured Outputs** (`strict: true`)
All tool definitions use JSON Schema with strict mode:
- 100% reliable function calling
- No hallucinated parameters
- Type-safe with Pydantic validation

### 3. **Prompt Caching**
System prompt and plan context are structured for caching:
```
[System Prompt - CACHED]
[Plan Context - CACHED when unchanged]
[Conversation History]
[New User Message]
```

**Expected Savings:** ~35-50% cost reduction on repeated requests

### 4. **Function Calling Tools**

#### MVP Tools (Implemented)
1. **`get_plan_snapshot`** - Retrieve full plan JSON
2. **`answer_fact`** - Query well/formation data
3. **`combine_plugs`** ⭐ - Merge formation plugs (WORKING)
4. **`replace_cibp_with_long_plug`** - CIBP replacement (stub)
5. **`recalc_materials_and_export`** - Recalculate (stub)

#### Tool Execution Flow
```
User: "Can we combine the Yates and San Andres plugs?"
  ↓
AI decides to call: combine_plugs(step_ids=[3, 4], reason="...")
  ↓
Guardrails check: allow_plan_changes=true?
  ↓
Execute: Merge steps, recalc materials, create new PlanSnapshot
  ↓
Return: success + risk_score + violations_delta
  ↓
AI explains result to user
```

### 5. **Guardrails Integration**

**Three-Tier Enforcement:**
1. **Global Baseline** - Non-negotiable platform rules
2. **Tenant Policy** - Org-specific risk tolerance
3. **Session Auth** - User-level `allow_plan_changes` flag

**Enforced at:**
- API request level (deny if flag=false)
- Tool execution level (raise GuardrailViolation)
- Response level (show risk_score before applying)

### 6. **Async Processing with Celery**

**Flow:**
```
Frontend → POST /api/chat/threads/20/messages/
  ↓ (202 Accepted)
Redis Queue ← Task queued
  ↓
Celery Worker picks up task
  ↓
OpenAI API call → Tool execution → Response
  ↓
ChatMessage created with assistant response
  ↓
Frontend polls → GET /api/chat/threads/20/messages/
```

**Benefits:**
- Non-blocking API responses
- Long-running OpenAI calls don't timeout
- Retry logic built-in (3 retries)

---

## 📁 File Structure

```
apps/assistant/
├── tools/
│   ├── __init__.py
│   ├── schemas.py          # Pydantic tool definitions
│   └── executors.py        # Tool implementation logic
├── services/
│   ├── openai_service.py   # Main OpenAI integration
│   ├── guardrails.py       # Enforcement logic (existing)
│   └── ...
├── tasks.py                # Celery async task
├── models.py               # ChatThread, ChatMessage, PlanModification
└── views/
    └── chat_messages.py    # API endpoints
```

---

## 🔧 Configuration

### Environment Variables

Add to `.env`:
```bash
# OpenAI API Key (required)
OPENAI_API_KEY=sk-proj-...

# Optional: Override default model
OPENAI_MODEL=gpt-4o  # Default

# Optional: Adjust temperature
OPENAI_TEMPERATURE=0.1  # Low for factual responses
```

### Django Settings

Already configured in `ra_config/settings/base.py`:
- Celery broker/backend
- Redis connection
- Task routing

---

## 🧪 Testing

### 1. **Test Basic Chat**

```bash
# Get JWT token
TOKEN=$(curl -X POST http://127.0.0.1:8001/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@example.com","password":"demo123"}' \
  | jq -r '.access')

# Create thread
THREAD_ID=$(curl -X POST http://127.0.0.1:8001/api/chat/threads/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "well_api14": "4241501493",
    "plan_id": "4241501493:combined",
    "title": "Test Thread"
  }' | jq -r '.id')

# Send message
curl -X POST http://127.0.0.1:8001/api/chat/threads/$THREAD_ID/messages/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "What formations are in this plan?",
    "allow_plan_changes": false
  }'

# Check messages (after ~2 seconds)
curl http://127.0.0.1:8001/api/chat/threads/$THREAD_ID/messages/ \
  -H "Authorization: Bearer $TOKEN"
```

### 2. **Test Tool Calling (Combine Plugs)**

```bash
curl -X POST http://127.0.0.1:8001/api/chat/threads/$THREAD_ID/messages/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Can we combine the formation plugs at step IDs 3 and 4?",
    "allow_plan_changes": true,
    "max_tool_calls": 10
  }'
```

**Expected:**
- AI analyzes plan
- Calls `get_plan_snapshot` to inspect steps
- Calls `combine_plugs(step_ids=[3,4], reason="...")`
- Returns explanation with risk_score

### 3. **Monitor with Flower**

```bash
# Open Flower UI
open http://localhost:5555

# Check:
- Active workers
- Task execution times
- Success/failure rates
```

### 4. **Check Celery Logs**

```bash
docker logs -f regulagent_celery
```

Look for:
```
[Task] Processing chat message X in thread Y
Executing tool: get_plan_snapshot with args: {...}
Executing tool: combine_plugs with args: {...}
[Task] Created assistant message Z for thread Y
```

---

## 🎯 Next Steps

### Phase 2: Complete Tool Implementation
- [ ] **Replace CIBP tool** - Full implementation
- [ ] **Recalc materials tool** - Integrate with material_engine
- [ ] **Vector search** - For answer_fact tool (similar wells)

### Phase 3: Advanced Features
- [ ] **Streaming responses** - SSE/WebSocket for real-time chat
- [ ] **Reasoning model** - Use `o1` for compliance validation
- [ ] **Vision API** - Read wellbore schematics
- [ ] **Batch embeddings** - Process historical plans

### Phase 4: Learning Loop
- [ ] **Precedent retrieval** - Show similar approved plans
- [ ] **Risk scoring** - ML-based risk assessment
- [ ] **Tenant learning** - Mine accepted modifications
- [ ] **Fine-tuning** - Custom model on TX RRC data

---

## 💡 Key Design Decisions

### Why Chat Completions over Assistants API?
- **Control**: We manage conversation state (better multi-tenancy)
- **Cost**: No storage fees, pay per token only
- **Latency**: No polling, immediate responses
- **Flexibility**: Easy to customize prompts per tenant

### Why Structured Outputs?
- **Reliability**: 100% valid tool calls
- **Type Safety**: Pydantic validation catches errors
- **No Retries**: Eliminates failed parse → retry loop

### Why Celery for OpenAI Calls?
- **Non-blocking**: API returns 202 immediately
- **Resilience**: Automatic retries on failure
- **Scale**: Handle multiple concurrent users
- **Monitoring**: Flower UI for observability

---

## 📊 Expected Performance

| Metric | Target | Notes |
|--------|--------|-------|
| **API Response** | <200ms | 202 Accepted (task queued) |
| **OpenAI Latency** | 2-5s | Depends on tool calls |
| **Tool Execution** | <1s | Per tool (combine_plugs) |
| **Total Time** | 3-8s | User sees response via polling |
| **Cost per Request** | $0.01-0.05 | With prompt caching |

---

## 🚨 Troubleshooting

### Issue: "Connection refused" to Redis
**Fix:** Ensure Redis container is running
```bash
docker compose -f docker/compose.dev.yml ps
# Should show regulagent_redis as healthy
```

### Issue: "No OpenAI API key"
**Fix:** Add to `.env`
```bash
echo "OPENAI_API_KEY=sk-proj-..." >> .env
docker compose -f docker/compose.dev.yml restart web celery
```

### Issue: Task not processing
**Fix:** Check Celery worker logs
```bash
docker logs regulagent_celery --tail 50
# Look for "celery@... ready"
```

### Issue: Tool execution fails
**Fix:** Check guardrails
```bash
# Ensure allow_plan_changes=true in request
{
  "content": "...",
  "allow_plan_changes": true  # ← Required for modifications
}
```

---

**Status**: ✅ OpenAI integration MVP complete  
**Date**: 2025-11-02  
**Version**: 1.0

