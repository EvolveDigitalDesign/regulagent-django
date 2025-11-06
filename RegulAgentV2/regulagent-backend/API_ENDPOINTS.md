# RegulAgent API Endpoints

Complete API reference for all RegulAgent endpoints. All endpoints require JWT authentication unless noted otherwise.

---

## **Authentication**

### Get JWT Token
```
POST /api/token/
```

**Request:**
```json
{
  "username": "demo@example.com",
  "password": "demo123"
}
```

**Response:**
```json
{
  "access": "eyJ0eXAiOiJKV1QiLCJhbGci...",
  "refresh": "eyJ0eXAiOiJKV1QiLCJhbGci..."
}
```

### Refresh Token
```
POST /api/token/refresh/
```

---

## **1. Tenant Guardrail Policies**

### Get Current Policy
```
GET /api/tenant/settings/guardrails/
Authorization: Bearer {token}
```

**Response:**
```json
{
  "id": 1,
  "tenant_id": "uuid",
  "risk_profile": "conservative",
  "require_confirmation_above_risk": 0.3,
  "max_material_delta_percent": 0.2,
  "max_steps_removed": 2,
  "allow_new_violations": false,
  "max_modifications_per_session": 5,
  "allowed_operations": [],
  "blocked_operations": ["replace_cibp"],
  "district_overrides": {
    "08A": {
      "max_material_delta_percent": 0.15
    }
  },
  "global_baseline": {
    "require_confirmation_above_risk": 0.5,
    "max_material_delta_percent": 0.3
  },
  "is_stricter_than_global": true
}
```

### Update Policy
```
PATCH /api/tenant/settings/guardrails/
Authorization: Bearer {token}
Content-Type: application/json
```

**Request:**
```json
{
  "risk_profile": "conservative",
  "require_confirmation_above_risk": 0.25,
  "blocked_operations": ["replace_cibp"],
  "district_overrides": {
    "08A": {"max_material_delta_percent": 0.15}
  }
}
```

**Response:**
```json
{
  "message": "Guardrail policy updated successfully",
  "policy": {
    "risk_profile": "conservative",
    "require_confirmation_above_risk": 0.25,
    "max_material_delta_percent": 0.2
  },
  "warnings": [
    "Conservative profile may require more manual approvals"
  ]
}
```

### Get Available Risk Profiles
```
GET /api/tenant/settings/guardrails/risk-profiles/
Authorization: Bearer {token}
```

**Response:**
```json
{
  "profiles": [
    {
      "id": "conservative",
      "name": "Conservative",
      "description": "Strict limits, manual review required",
      "icon": "🔒",
      "settings": {
        "require_confirmation_above_risk": 0.3,
        "max_material_delta_percent": 0.2
      },
      "best_for": ["New teams", "High-risk wells"]
    }
  ],
  "global_baseline": {...}
}
```

### Validate Policy Change
```
GET /api/tenant/settings/guardrails/validate/?risk_threshold=0.8&material_delta=0.4
Authorization: Bearer {token}
```

**Response:**
```json
{
  "valid": false,
  "errors": [
    "Risk threshold 0.8 exceeds global baseline 0.5"
  ],
  "warnings": []
}
```

---

## **2. Chat Threads**

### List Threads
```
GET /api/chat/threads/
Authorization: Bearer {token}
```

**Query Params:**
- `well_api` - Filter by well API number
- `is_active` - Filter by active status (true/false)
- `limit` - Results per page (default: 50)
- `offset` - Pagination offset

**Response:**
```json
{
  "threads": [
    {
      "id": 1,
      "title": "Conservative Review: 4200346118",
      "well": {
        "api": "4200346118",
        "operator": "XTO Energy"
      },
      "baseline_plan_id": "4200346118:combined",
      "current_plan_id": "4200346118:combined",
      "message_count": 5,
      "is_active": true,
      "created_by": "demo@example.com",
      "created_at": "2025-11-02T10:00:00Z"
    }
  ]
}
```

### Create Thread
```
POST /api/chat/threads/
Authorization: Bearer {token}
```

**Request:**
```json
{
  "well_api": "4200346118",
  "plan_id": "4200346118:combined",
  "title": "Review W3A Plan"
}
```

### Get Thread Details
```
GET /api/chat/threads/{id}/
Authorization: Bearer {token}
```

---

## **3. Chat Messages**

### List Messages
```
GET /api/chat/threads/{thread_id}/messages/
Authorization: Bearer {token}
```

**Query Params:**
- `limit` - Results per page (default: 50)
- `offset` - Pagination offset

**Response:**
```json
{
  "messages": [
    {
      "id": 1,
      "role": "user",
      "content": "Can we combine the formation plugs?",
      "created_at": "2025-11-02T10:00:00Z"
    },
    {
      "id": 2,
      "role": "assistant",
      "content": "Yes, I can combine the plugs at 6500ft and 9500ft...",
      "tool_calls": [...],
      "created_at": "2025-11-02T10:00:15Z"
    }
  ]
}
```

### Send Message (Async)
```
POST /api/chat/threads/{thread_id}/messages/
Authorization: Bearer {token}
```

**Request:**
```json
{
  "content": "Can we combine the formation plugs at 6500ft and 9500ft?",
  "allow_plan_changes": true,
  "async": true
}
```

**Response (202 Accepted):**
```json
{
  "user_message": {...},
  "task_id": "abc-123-def",
  "status_url": "/api/chat/threads/1/messages/1/status",
  "polling_interval_ms": 1000
}
```

### Check Message Status
```
GET /api/chat/threads/{thread_id}/messages/{message_id}/status/
Authorization: Bearer {token}
```

**Response:**
```json
{
  "user_message": {...},
  "assistant_message": {...},
  "status": "completed"
}
```

---

## **4. Regulator Outcomes**

### List Outcomes
```
GET /api/chat/outcomes/
Authorization: Bearer {token}
```

**Query Params:**
- `status` - Filter by status (approved, rejected, pending)
- `filed_after` - Filter by filing date (ISO 8601)
- `limit` - Results per page (default: 50)
- `offset` - Pagination offset

**Response:**
```json
{
  "outcomes": [
    {
      "id": 1,
      "plan_id": "4200346118:combined",
      "api": "4200346118",
      "filing_number": "W3A-2025-001234",
      "status": "approved",
      "agency": "RRC",
      "filed_at": "2025-10-15T10:00:00Z",
      "approved_at": "2025-10-20T14:00:00Z",
      "review_duration_days": 5,
      "confidence_score": 0.8,
      "modifications_count": 2
    }
  ],
  "summary": {
    "total": 10,
    "approved": 8,
    "rejected": 1,
    "pending": 1,
    "approval_rate": 0.8
  }
}
```

### Create Outcome (When Filing)
```
POST /api/chat/outcomes/
Authorization: Bearer {token}
```

**Request:**
```json
{
  "plan_id": "4200346118:combined",
  "filing_number": "W3A-2025-001234",
  "agency": "RRC"
}
```

**Response:**
```json
{
  "id": 1,
  "plan_id": "4200346118:combined",
  "filing_number": "W3A-2025-001234",
  "status": "pending",
  "filed_at": "2025-11-02T10:00:00Z",
  "modifications_linked": 2
}
```

### Get Outcome Details
```
GET /api/chat/outcomes/{id}/
Authorization: Bearer {token}
```

**Response:**
```json
{
  "id": 1,
  "plan": {
    "plan_id": "4200346118:combined",
    "api": "4200346118",
    "operator": "XTO Energy",
    "field": "TXL Spraberry"
  },
  "filing": {
    "filing_number": "W3A-2025-001234",
    "agency": "RRC",
    "filed_at": "2025-10-15T10:00:00Z"
  },
  "status": {
    "current": "approved",
    "reviewed_at": "2025-10-20T14:00:00Z",
    "approved_at": "2025-10-20T14:00:00Z",
    "review_duration_days": 5
  },
  "review": {
    "reviewer_name": "John Smith",
    "reviewer_notes": "Plan meets all requirements. Approved.",
    "revision_count": 0
  },
  "learning": {
    "confidence_score": 0.8,
    "modifications_count": 2,
    "modifications": [...]
  }
}
```

### Mark Outcome Approved
```
PATCH /api/chat/outcomes/{id}/approve/
Authorization: Bearer {token}
```

**Request:**
```json
{
  "reviewer_notes": "Plan meets all requirements. Approved.",
  "reviewer_name": "John Smith",
  "approved_at": "2025-11-02T10:00:00Z"
}
```

**Response:**
```json
{
  "message": "Outcome marked as approved",
  "status": "approved",
  "confidence_score": 0.8,
  "review_duration_days": 5,
  "learning_triggered": true,
  "modifications_updated": 2
}
```

### Mark Outcome Rejected
```
PATCH /api/chat/outcomes/{id}/reject/
Authorization: Bearer {token}
```

**Request:**
```json
{
  "reviewer_notes": "Formation top coverage insufficient.",
  "reviewer_name": "Jane Doe"
}
```

### Get Outcome Statistics
```
GET /api/chat/outcomes/stats/?district=08A
Authorization: Bearer {token}
```

**Response:**
```json
{
  "total_modifications": 100,
  "total_outcomes": 30,
  "approved": 24,
  "rejected": 4,
  "approval_rate": 0.8,
  "avg_confidence": 0.72,
  "by_operation_type": {
    "combine_plugs": {"count": 10, "approval_rate": 0.9}
  },
  "avg_review_duration_days": 5.2
}
```

---

## **5. Plans**

### Get Plan Detail
```
GET /api/plans/{plan_id}/
Authorization: Bearer {token}
```

**Response:**
```json
{
  "id": 1,
  "plan_id": "4200346118:combined",
  "kind": "baseline",
  "status": "draft",
  "well": {
    "api14": "4200346118",
    "operator_name": "XTO Energy",
    "field_name": "TXL Spraberry",
    "lat": 32.242052,
    "lon": -102.282218
  },
  "well_geometry": {
    "casing_strings": [...],
    "formation_tops": [...],
    "perforations": [...]
  },
  "payload": {
    "steps": [...],
    "materials_totals": {...},
    "violations": [...]
  }
}
```

### Get Plan Version History
```
GET /api/plans/{plan_id}/versions/
Authorization: Bearer {token}
```

**Response:**
```json
{
  "baseline_plan_id": "4200346118:combined",
  "current_version": 2,
  "total_versions": 3,
  "versions": [
    {
      "version": 0,
      "snapshot_id": 1,
      "kind": "baseline",
      "status": "draft",
      "created_at": "2025-11-01T10:00:00Z",
      "modification": null
    },
    {
      "version": 1,
      "snapshot_id": 2,
      "kind": "post_edit",
      "status": "internal_review",
      "created_at": "2025-11-02T10:00:00Z",
      "modification": {
        "op_type": "combine_plugs",
        "description": "Combined plugs at 6500ft and 9500ft",
        "risk_score": 0.15
      }
    }
  ]
}
```

### Compare Plan Versions
```
GET /api/plans/compare/{snapshot_id_1}/{snapshot_id_2}/
Authorization: Bearer {token}
```

**Response:**
```json
{
  "snapshot_1": {...},
  "snapshot_2": {...},
  "json_patch": [
    {"op": "remove", "path": "/steps/5"},
    {"op": "replace", "path": "/materials_totals/total_sacks", "value": 250}
  ],
  "steps": [
    {
      "step_id": 5,
      "change_type": "removed",
      "highlight_color": "#ff4444",
      "summary": "Step 5 removed: cement_plug at 6500-6550 ft"
    }
  ],
  "summary": {
    "steps_removed": 2,
    "steps_added": 0,
    "materials_delta": -150,
    "violations_delta": -1,
    "human_readable": "Removed 2 steps: 5, 11. Materials: 150 sacks saved..."
  }
}
```

### Update Plan Status
```
PATCH /api/plans/{plan_id}/status/modify/
Authorization: Bearer {token}
```

**Response:**
```json
{
  "message": "Plan status updated to internal_review",
  "new_status": "internal_review"
}
```

---

## **6. Tenant Wells**

### Get Well History
```
GET /api/tenant/wells/history/
Authorization: Bearer {token}
```

**Response:**
```json
{
  "wells": [
    {
      "id": 1,
      "api14": "4200346118",
      "operator_name": "XTO Energy",
      "tenant_interaction": {
        "has_interacted": true,
        "first_interaction_at": "2025-11-01T10:00:00Z",
        "last_interaction_type": "w3a_generated",
        "interaction_count": 5,
        "metadata": {...}
      }
    }
  ]
}
```

### Get Well by API
```
GET /api/tenant/wells/{api14}/
Authorization: Bearer {token}
```

### Bulk Get Wells
```
POST /api/tenant/wells/bulk/
Authorization: Bearer {token}
```

**Request:**
```json
{
  "api_numbers": ["4200346118", "4241501493"]
}
```

---

## **Authentication Headers**

All endpoints except `/api/token/` require:
```
Authorization: Bearer {access_token}
```

---

## **Error Responses**

### 400 Bad Request
```json
{
  "error": "plan_id and filing_number are required"
}
```

### 401 Unauthorized
```json
{
  "detail": "Authentication credentials were not provided."
}
```

### 403 Forbidden
```json
{
  "error": "User not associated with any tenant"
}
```

### 404 Not Found
```json
{
  "error": "Plan 4200346118:combined not found"
}
```

---

## **Rate Limits**

- Chat messages: 60 per minute
- Plan modifications: 10 per hour (enforced by guardrails)
- All other endpoints: 100 per minute

---

## **WebSocket Support (Future)**

Real-time updates for:
- Chat messages
- Plan modifications
- Outcome status changes

---

## **Pagination**

Most list endpoints support:
- `limit` - Results per page (default: 50, max: 100)
- `offset` - Number of results to skip

**Response includes:**
```json
{
  "pagination": {
    "total": 100,
    "limit": 50,
    "offset": 0,
    "has_more": true
  }
}
```

---

## **Filtering**

Common filters across endpoints:
- `status` - Filter by status
- `created_after` / `created_before` - Date range
- `district` - Filter by RRC district
- `well_api` - Filter by well API number

---

## **Quick Start Examples**

### 1. Configure Tenant Guardrails
```bash
# Get current policy
curl -X GET http://localhost:8001/api/tenant/settings/guardrails/ \
  -H "Authorization: Bearer $TOKEN"

# Update to conservative
curl -X PATCH http://localhost:8001/api/tenant/settings/guardrails/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"risk_profile": "conservative"}'
```

### 2. Create Chat Thread
```bash
curl -X POST http://localhost:8001/api/chat/threads/ \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "well_api": "4200346118",
    "plan_id": "4200346118:combined",
    "title": "Review W3A Plan"
  }'
```

### 3. Send Chat Message
```bash
curl -X POST http://localhost:8001/api/chat/threads/1/messages/ \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "content": "Can we combine the formation plugs?",
    "allow_plan_changes": true
  }'
```

### 4. File Plan with RRC
```bash
curl -X POST http://localhost:8001/api/chat/outcomes/ \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "plan_id": "4200346118:combined",
    "filing_number": "W3A-2025-001234"
  }'
```

### 5. Mark Plan Approved
```bash
curl -X PATCH http://localhost:8001/api/chat/outcomes/1/approve/ \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "reviewer_notes": "Plan approved",
    "reviewer_name": "John Smith"
  }'
```

---

**Last Updated**: 2025-11-02  
**API Version**: 1.0

