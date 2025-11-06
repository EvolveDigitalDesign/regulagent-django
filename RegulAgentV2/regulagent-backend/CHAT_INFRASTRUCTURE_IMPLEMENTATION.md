# Chat Infrastructure Implementation Summary

## ✅ What's Been Implemented

### 1. **Data Models** (apps/assistant/models.py)

#### ChatThread
- **Ownership Model**: 
  - `created_by`: Owner with full edit rights
  - `shared_with`: Many-to-many for read-only access
  - All users scoped to same tenant
  
- **Context Tracking**:
  - Links to `WellRegistry` (what well is being discussed)
  - Links to `baseline_plan` (original plan)
  - Links to `current_plan` (latest modified version)
  
- **OpenAI Integration Ready**:
  - `openai_thread_id`: For OpenAI Assistants API continuity
  
- **Helper Methods**:
  - `can_edit(user)`: Check if user is owner
  - `can_view(user)`: Check if user is owner or has shared access
  - `share_with_user(user)`: Grant read-only access
  - `unshare_with_user(user)`: Revoke shared access

#### ChatMessage
- **Message Types**: `user`, `assistant`, `system`
- **Tool Tracking**: Stores `tool_calls` and `tool_results` as JSON
- **OpenAI Integration**: `openai_message_id`, `openai_run_id` for API correlation
- **Metadata**: Flexible JSON for model info, tokens, latency, etc.

#### PlanModification
- **Operation Types**: `combine_plugs`, `replace_cibp`, `adjust_interval`, `change_materials`, `add_step`, `remove_step`, `reorder_steps`, `custom`
- **Snapshot Linking**: 
  - `source_snapshot`: Plan before modification
  - `result_snapshot`: Plan after modification (creates new `PlanSnapshot` with `kind='post_edit'`)
- **Risk Assessment**:
  - `risk_score`: 0.0-1.0 divergence from baseline
  - `violations_delta`: New or resolved violations
- **Audit Trail**: Links to `chat_thread`, `chat_message`, `applied_by`
- **Diff Storage**: Full JSON diff for review/revert

---

### 2. **API Endpoints** (apps/assistant/views/)

#### Thread Management

**POST /api/chat/threads** - Create Thread
```json
{
  "well_api14": "4200346118",
  "plan_id": "4200346118:combined",
  "title": "Discuss formation plug depths",
  "share_with_user_ids": [2, 3]
}
```
Response: Full thread details with permissions

**GET /api/chat/threads** - List Threads
- Returns threads user owns OR has shared access to
- Scoped to user's tenant
- Includes message count, modification count, permission flags

**GET /api/chat/threads/{id}** - Get Thread Details
- Full thread metadata
- Permission check (can_view)
- Message summary

**PATCH /api/chat/threads/{id}** - Update Thread
- Owner only
- Can update: `title`, `is_active`
```json
{
  "title": "New title",
  "is_active": false
}
```

**DELETE /api/chat/threads/{id}** - Archive Thread
- Owner only
- Soft delete (sets `is_active=False`)

**POST /api/chat/threads/{id}/share/** - Manage Sharing
- Owner only
```json
{
  "user_ids": [2, 3, 4],
  "action": "add"  // or "remove"
}
```

#### Message Management

**GET /api/chat/threads/{thread_id}/messages** - List Messages
- View permission required (owner or shared)
- Paginated (limit/offset)
- Ordered by creation time

**POST /api/chat/threads/{thread_id}/messages** - Send Message
- Owner only (edit permission)
- Creates user message
- Triggers AI response (OpenAI integration pending)
```json
{
  "content": "Can we combine the formation plugs at 6500 ft and 9500 ft?",
  "allow_plan_changes": true,
  "max_tool_calls": 10
}
```

---

### 3. **Serializers** (apps/assistant/serializers.py)

- **ChatThreadCreateSerializer**: For thread creation
- **ChatThreadSerializer**: Full thread details with permissions
- **ChatMessageSerializer**: Message with tool calls
- **ChatMessageCreateSerializer**: User input validation
- **PlanModificationSerializer**: Modification details with diff
- **ChatThreadShareSerializer**: Sharing management

---

### 4. **Admin Interface** (apps/assistant/admin.py)

All models registered with Django Admin:
- **ChatThread**: Shows owner, shared count, well, plan
  - Filter horizontal for `shared_with` (nice UX)
- **ChatMessage**: Shows role, preview, tool call indicator
- **PlanModification**: Shows operation, risk score, status

---

## 🎯 Key Features

### Tenant & User Isolation
```python
# User must be in same tenant to access thread
thread.tenant_id == user.tenants.first().id

# Permission checks
thread.can_edit(user)  # Owner only
thread.can_view(user)  # Owner or shared users
```

### Sharing Model
```
Owner (created_by)      →  Full edit rights (send messages, modify plan)
Shared Users (M2M)      →  Read-only (view thread, view messages)
All in Same Tenant      →  Enforced at DB and API level
```

### Plan Modification Architecture
```
Baseline Plan (PlanSnapshot.payload)
    ↓
User asks: "Combine formation plugs?"
    ↓
AI suggests modification
    ↓
PlanModification created (op_type, payload, diff, risk_score)
    ↓
New PlanSnapshot(kind='post_edit') with modified payload
    ↓
ChatThread.current_plan updated to new snapshot
```

---

## 🚧 What's Next (Pending TODOs)

### chat_5: Plan Modification Service
Create service to apply modifications to `PlanSnapshot.payload`:
```python
# apps/assistant/services/plan_editor.py
def combine_plugs(payload, step_ids):
    # Merge steps in payload JSON
    # Recompute materials
    # Return modified payload + diff

def replace_cibp(payload, interval):
    # Remove CIBP steps
    # Add long plug
    # Recompute
```

### chat_6: OpenAI Integration
Integrate OpenAI Assistants API in `ChatMessageView.post()`:
```python
# 1. Get or create OpenAI thread
# 2. Send user message to OpenAI
# 3. Execute tool calls (get_plan_snapshot, combine_plugs, etc.)
# 4. Store tool results
# 5. Return assistant response
```

---

## 📊 Database Schema

### New Tables
- `assistant_chat_threads`
- `assistant_chat_threads_shared_with` (M2M junction)
- `assistant_chat_messages`
- `assistant_plan_modifications`

### Relationships
```
ChatThread
  ├─ created_by (FK → User)
  ├─ shared_with (M2M → User)
  ├─ well (FK → WellRegistry)
  ├─ baseline_plan (FK → PlanSnapshot)
  ├─ current_plan (FK → PlanSnapshot)
  └─ messages (1:N → ChatMessage)
      └─ modifications (1:N → PlanModification)

PlanModification
  ├─ source_snapshot (FK → PlanSnapshot)
  ├─ result_snapshot (FK → PlanSnapshot)
  ├─ chat_thread (FK → ChatThread)
  └─ chat_message (FK → ChatMessage)
```

---

## 🧪 Testing

### Test Credentials
- Email: `demo@example.com`
- Password: `demo123`

### Example Flow

1. **Generate a plan**:
```bash
curl -X POST http://127.0.0.1:8001/api/plans/w3a/from-api \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"api10": "4200346118"}'
```

2. **Create chat thread**:
```bash
curl -X POST http://127.0.0.1:8001/api/chat/threads/ \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "well_api14": "4200346118",
    "plan_id": "4200346118:combined",
    "title": "Discuss formation plugs"
  }'
```

3. **Send message**:
```bash
curl -X POST http://127.0.0.1:8001/api/chat/threads/1/messages/ \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "content": "What formation plugs are in this plan?"
  }'
```

4. **Share with colleague**:
```bash
curl -X POST http://127.0.0.1:8001/api/chat/threads/1/share/ \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "user_ids": [2],
    "action": "add"
  }'
```

---

## 📝 Implementation Notes

### Why `PlanSnapshot.payload` for Modifications?

The full W3A plan is stored in `PlanSnapshot.payload` as JSON:
```json
{
  "api": "4200346118",
  "steps": [ /* 12 plugging steps */ ],
  "violations": [],
  "materials_totals": {...},
  "formations_targeted": [...],
  ...
}
```

**Benefits**:
- Immutable history: Each modification creates a new snapshot
- Easy diff: Compare `source.payload` vs `result.payload`
- Flexible: Can modify any part of the plan JSON
- Auditable: Full provenance chain

### Sharing Security

- ✅ All users must be in same tenant
- ✅ Owner validation before sharing
- ✅ Permission checks on every endpoint
- ✅ Read-only enforcement (only owner can send messages)
- ✅ Tenant isolation at DB query level

---

## 🎓 Next Steps for Frontend

1. **Thread List UI**: Show owned + shared threads
2. **Thread Detail**: Messages, plan preview, sharing controls
3. **Message Input**: Send messages, show AI responses
4. **Permission Indicators**: Show "Owner" vs "Read-only" badges
5. **Sharing Modal**: Invite team members to thread

---

## 📚 Related Documentation

- [W3A From API Endpoint Spec](./W3A_FROM_API_ENDPOINT_SPEC.md)
- [Consolidated AI Roadmap](../Consolidated-AI-Roadmap.md)
- [Plan Status Workflow](./apps/public_core/views/plan_status.py)

---

**Status**: ✅ Chat infrastructure complete, ready for OpenAI integration
**Date**: 2025-11-02
**Version**: 1.0

