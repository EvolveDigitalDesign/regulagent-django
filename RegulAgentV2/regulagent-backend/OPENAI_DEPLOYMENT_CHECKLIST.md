# OpenAI Integration - Deployment Checklist

## ✅ Pre-Deployment

### 1. Environment Configuration
- [ ] Add `OPENAI_API_KEY` to `.env`
- [ ] Verify Redis is running (`docker compose ps`)
- [ ] Verify Celery worker is running (`docker logs regulagent_celery`)
- [ ] Verify Celery beat is running (for scheduled tasks)

### 2. Database Ready
- [ ] Migrations applied (`python manage.py migrate`)
- [ ] `ChatThread`, `ChatMessage`, `PlanModification` tables exist
- [ ] `TenantGuardrailPolicy` tables exist

### 3. Test Data
- [ ] At least one `PlanSnapshot` exists (e.g., `4241501493:combined`)
- [ ] Demo user has access token
- [ ] Tenant is properly configured

---

## 🧪 Testing Sequence

### Test 1: Basic Chat (No Tools)
```bash
# Goal: Verify OpenAI API connection and basic response

curl -X POST http://127.0.0.1:8001/api/chat/threads/20/messages/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "What is this plan about?",
    "allow_plan_changes": false
  }'

# Expected: 202 Accepted
# Wait 2-3s, then check messages
# Should see AI response describing the plan
```

**Success Criteria:**
- ✅ 202 status code
- ✅ Celery logs show task picked up
- ✅ Assistant message created
- ✅ Response is relevant to plan

**Failure Debug:**
- Check Celery logs: `docker logs regulagent_celery --tail 50`
- Check web logs: `docker logs regulagent_web --tail 50`
- Verify API key: `docker exec regulagent_web env | grep OPENAI`

---

### Test 2: Simple Tool Call (get_plan_snapshot)
```bash
# Goal: Verify function calling works

curl -X POST http://127.0.0.1:8001/api/chat/threads/20/messages/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Show me the current plan details",
    "allow_plan_changes": false
  }'

# Expected: AI calls get_plan_snapshot tool
```

**Success Criteria:**
- ✅ Celery logs show: `Executing tool: get_plan_snapshot`
- ✅ Response includes plan details (steps, formations, etc.)
- ✅ Response is structured and specific

**Failure Debug:**
- Check if AI decided not to use tools (response might be general)
- Verify tool definitions are loaded: Check startup logs
- Try more explicit prompt: "Use get_plan_snapshot to show the plan"

---

### Test 3: Complex Tool Call (combine_plugs)
```bash
# Goal: Verify plan modifications work

curl -X POST http://127.0.0.1:8001/api/chat/threads/20/messages/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Can we combine step IDs 3 and 4 into one plug?",
    "allow_plan_changes": true,
    "max_tool_calls": 10
  }'

# Expected: AI analyzes, calls combine_plugs, creates new PlanSnapshot
```

**Success Criteria:**
- ✅ Celery logs show: `Executing tool: combine_plugs`
- ✅ New `PlanSnapshot` created with `kind='post_edit'`
- ✅ `PlanModification` record created
- ✅ Response includes risk_score and confirmation
- ✅ Thread's `current_plan` updated

**Failure Debug:**
- Check guardrails: Is `allow_plan_changes=true`?
- Check step IDs exist in plan
- Check if steps are formation_plugs
- Verify adjacency (within 500 ft)

---

### Test 4: Guardrail Blocking
```bash
# Goal: Verify safety guardrails work

curl -X POST http://127.0.0.1:8001/api/chat/threads/20/messages/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Combine step IDs 3 and 4",
    "allow_plan_changes": false  ← FALSE
  }'

# Expected: AI should NOT be able to modify plan
```

**Success Criteria:**
- ✅ Tool execution blocked
- ✅ Error message about guardrails
- ✅ No new PlanSnapshot created
- ✅ Response explains why blocked

---

### Test 5: Token Usage Logging
```bash
# Goal: Verify cost tracking works

# Send a few messages, then check logs:
docker logs regulagent_celery | grep "OpenAI Usage"

# Expected output:
# OpenAI Usage [chat_thread_20]: prompt=1234 completion=567 total=1801
```

**Success Criteria:**
- ✅ Usage logs appear
- ✅ Token counts seem reasonable
- ✅ Prompt tokens > completion tokens (due to caching)

---

## 📊 Monitoring

### Celery Worker Health
```bash
# Check worker status
docker logs regulagent_celery --tail 20

# Should see:
# celery@... ready.
# Connected to redis://redis:6379/0
```

### Flower Dashboard
```bash
open http://localhost:5555

# Check:
- Workers: 1 active
- Tasks: process_chat_message_async
- Success rate: >90%
- Average time: 3-5 seconds
```

### Database Verification
```bash
docker exec regulagent_web python manage.py shell

# In shell:
from apps.assistant.models import ChatMessage, PlanModification
ChatMessage.objects.count()  # Should increase after each message
PlanModification.objects.count()  # Should increase after tool calls
```

---

## 🚨 Common Issues

### Issue 1: "Connection refused to Redis"
**Symptom:** Celery task never processes  
**Fix:**
```bash
docker compose -f docker/compose.dev.yml restart redis celery
```

### Issue 2: "OPENAI_API_KEY not configured"
**Symptom:** RuntimeError in logs  
**Fix:**
```bash
echo "OPENAI_API_KEY=sk-proj-..." >> .env
docker compose -f docker/compose.dev.yml restart web celery
```

### Issue 3: "Plan X not found"
**Symptom:** 404 error when creating thread  
**Fix:**
```bash
# Verify plan exists:
curl http://127.0.0.1:8001/api/plans/4241501493:combined/ \
  -H "Authorization: Bearer $TOKEN"

# If not, generate it first via /api/plans/w3a/from-api
```

### Issue 4: AI not calling tools
**Symptom:** General response instead of tool use  
**Possible Causes:**
- Prompt not clear enough (try: "Use get_plan_snapshot to show...")
- Tool definitions not loaded (check startup logs)
- Model chose not to use tools (check response reasoning)

**Fix:** Be more explicit in prompts, or check tool definitions

---

## 📈 Success Metrics

After 24 hours of usage, verify:

| Metric | Target | How to Check |
|--------|--------|--------------|
| **API Success Rate** | >95% | Flower dashboard |
| **Avg Response Time** | <5s | Flower task metrics |
| **Tool Call Success** | >90% | Check PlanModification count |
| **Guardrail Blocks** | >0 | Logs show blocked attempts |
| **Cost per Request** | <$0.05 | Sum token usage logs |
| **Cache Hit Rate** | >80% | Compare prompt vs total tokens |

---

## 🎉 Deployment Complete Checklist

- [ ] All 5 tests passed
- [ ] Flower dashboard shows healthy worker
- [ ] Token usage logs appearing
- [ ] No errors in Celery/web logs
- [ ] At least one successful plan modification
- [ ] Guardrails successfully blocked at least one attempt
- [ ] Team notified and trained on how to use
- [ ] Documentation shared (OPENAI_INTEGRATION_SUMMARY.md)
- [ ] Cost tracking dashboard set up (Flower)
- [ ] Backup plan for API key rotation

---

**Status**: Ready for production deployment  
**Contact**: DevOps team if any issues  
**Emergency Rollback**: Set `OPENAI_API_KEY=""` to disable AI (falls back to error messages)

🚀 **Let's ship it!** 🚀

