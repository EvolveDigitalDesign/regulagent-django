# Tenant Well Registry Implementation

## Overview

Implemented a tenant-aware well registry system that tracks **all interactions** between tenants and wells, including W3A plan generation, document uploads, plan modifications, and chat threads.

## Architecture

### Data Model: Enhanced `WellEngagement`

The existing `WellEngagement` model was enhanced with interaction tracking fields:

**Location:** `apps/tenant_overlay/models/well_engagement.py`

**New Fields:**
- `last_interaction_type` - Type of most recent interaction (w3a_generated, document_uploaded, plan_modified, chat_created, advisory_requested)
- `interaction_count` - Total number of interactions with this well
- `first_interaction_at` - Timestamp of first interaction (set once, never updated)
- `metadata` - JSONField for flexible interaction summary (plan_ids, document_ids, counts, etc.)

**Existing Fields:**
- `tenant_id` - UUID of the tenant
- `well` - ForeignKey to WellRegistry (public well data)
- `mode` - upload/rrc/hybrid
- `label` - Optional label
- `owner_user` - User who owns this engagement
- `created_at`, `updated_at` - Timestamps

### Service Layer: Engagement Tracker

**Location:** `apps/tenant_overlay/services/engagement_tracker.py`

**Function:** `track_well_interaction(tenant_id, well, interaction_type, user=None, metadata_update=None, mode=None, label=None)`
- Creates or updates WellEngagement records
- Increments interaction_count
- Merges metadata updates
- Handles first_interaction_at (set once)

**Helper Functions:**
- `get_tenant_well_history(tenant_id, well)` - Get engagement for specific tenant-well pair
- `get_tenant_engagement_list(tenant_id)` - Get all wells a tenant has engaged with

### API Endpoints

**Location:** `apps/tenant_overlay/views/tenant_wells.py`

All endpoints require JWT or Session authentication.

#### 1. Get Well by API Number
```
GET /api/tenant/wells/{api14}/
```
Returns well data with the authenticated tenant's interaction history.

**Response:**
```json
{
  "api14": "42123456780000",
  "state": "TX",
  "county": "Andrews",
  "operator_name": "Example Operator",
  "lat": 32.1234,
  "lon": -102.5678,
  "created_at": "2024-01-15T10:00:00Z",
  "tenant_interaction": {
    "has_interacted": true,
    "first_interaction_at": "2024-02-01T14:30:00Z",
    "last_interaction_at": "2024-10-15T09:45:00Z",
    "last_interaction_type": "w3a_generated",
    "interaction_count": 12,
    "mode": "hybrid",
    "label": "Andrews Unit Well #4",
    "owner_user_email": "demo@example.com",
    "metadata": {
      "plan_id": "42123456780000:combined",
      "snapshot_id": "uuid-here",
      "plugs_mode": "combined"
    }
  }
}
```

#### 2. Bulk Query Wells
```
POST /api/tenant/wells/bulk/
```
Query multiple wells by API numbers (max 100).

**Request:**
```json
{
  "api_numbers": ["42123456780000", "42987654320000"]
}
```

**Response:**
```json
{
  "wells": [...],
  "not_found": ["42987654320000"],
  "summary": {
    "requested": 2,
    "found": 1,
    "not_found": 1
  }
}
```

#### 3. Tenant Well History
```
GET /api/tenant/wells/history/?limit=50&offset=0
```
Get all wells the authenticated tenant has interacted with, ordered by most recent interaction.

**Query Parameters:**
- `limit` - Number of wells to return (default: 50, max: 500)
- `offset` - Pagination offset (default: 0)

**Response:**
```json
{
  "wells": [...],
  "pagination": {
    "total": 123,
    "limit": 50,
    "offset": 0,
    "has_more": true
  }
}
```

### Serializers

**Location:** `apps/tenant_overlay/serializers/tenant_wells.py`

- `TenantWellSerializer` - Well data with tenant-specific interaction history
- `TenantInteractionSerializer` - Tenant's interaction history schema
- `BulkWellRequestSerializer` - Validation for bulk requests (max 100 APIs)

### Integration Points

Engagement tracking is automatically triggered in:

#### 1. W3A Plan Generation
**Location:** `apps/public_core/views/w3a_from_api.py`

After PlanSnapshot creation, tracks engagement with:
- `interaction_type`: `W3A_GENERATED`
- `metadata`: plan_id, snapshot_id, plugs_mode

#### 2. Document Upload
**Location:** `apps/public_core/views/document_upload.py`

After ExtractedDocument creation, tracks engagement with:
- `interaction_type`: `DOCUMENT_UPLOADED`
- `metadata`: document_id, document_type, source_path, is_public

#### 3. Future Integration Points (Ready)
- Plan modification (chat-driven or form-based)
- Chat thread creation
- Advisory requests

All tracking is wrapped in try/except blocks to ensure failures don't break the main workflow.

## Privacy & Tenant Isolation

- Tenants can **ONLY** query:
  - Specific wells by API number
  - Bulk query by list of API numbers
  - Their own interaction history
- Tenants **CANNOT**:
  - Query all wells (no unauthenticated browsing)
  - See other tenants' interaction histories
- Each query returns:
  - Public well data (from WellRegistry, shared)
  - Only the authenticated tenant's interaction history (private)

## Alignment with AI Roadmap

From `Consolidated-AI-Roadmap.md`:
- **Lines 25-26**: "Tenant boundaries: tenant/org IDs to enforce isolation" ✅
- **Lines 289-294**: "WellRegistry and ExtractedDocument Checks" - engagement tracking provides this historical context ✅
- **Lines 296-298**: "Tenant and Privacy Considerations" - private histories isolated per tenant ✅
- **Lines 108-113**: Chat data model shows `ChatThread(tenant_id, well_id, ...)` - ready for future chat integration ✅

## Database Migrations

**Migration:** `apps/tenant_overlay/migrations/0002_wellengagement_first_interaction_at_and_more.py`

Applied successfully across all tenant schemas (public, demo, test).

## Testing

### Authentication Test
```bash
curl -X POST http://127.0.0.1:8001/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@example.com","password":"demo123"}'
```

### History Endpoint Test
```bash
curl -X GET http://127.0.0.1:8001/api/tenant/wells/history/ \
  -H "Authorization: Bearer $TOKEN"
```

### Single Well Query Test
```bash
curl -X GET http://127.0.0.1:8001/api/tenant/wells/42001234560000/ \
  -H "Authorization: Bearer $TOKEN"
```

### Bulk Query Test
```bash
curl -X POST http://127.0.0.1:8001/api/tenant/wells/bulk/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"api_numbers": ["42001234560000", "42123456780000"]}'
```

## Next Steps

1. **Frontend Integration:**
   - Create well search/browse UI
   - Show interaction history for each well
   - Display "last worked on" timestamps

2. **Chat Integration:**
   - Add `track_well_interaction` calls when chat threads are created
   - Include chat metadata (thread_id, message_count)

3. **Plan Modification Tracking:**
   - Track when tenants modify plans
   - Include modification type and diff summary in metadata

4. **Analytics Dashboard:**
   - Show most-worked-on wells per tenant
   - Display interaction trends over time
   - Surface "stale" wells (no interaction in X days)

5. **Retroactive Population (Optional):**
   - Scan existing PlanSnapshot records with tenant_id
   - Scan existing ExtractedDocument records with uploaded_by_tenant
   - Create historical WellEngagement records

## Files Created/Modified

### Created:
- `apps/tenant_overlay/services/engagement_tracker.py`
- `apps/tenant_overlay/serializers/tenant_wells.py`
- `apps/tenant_overlay/views/tenant_wells.py`
- `TENANT_WELL_REGISTRY_IMPLEMENTATION.md`

### Modified:
- `apps/tenant_overlay/models/well_engagement.py` - Added interaction tracking fields
- `apps/public_core/views/w3a_from_api.py` - Added engagement tracking after plan generation
- `apps/public_core/views/document_upload.py` - Added engagement tracking after document upload
- `ra_config/urls.py` - Added tenant wells API routes

## API Summary

| Endpoint | Method | Purpose | Auth Required |
|----------|--------|---------|---------------|
| `/api/tenant/wells/history/` | GET | Get all wells tenant has interacted with | ✅ JWT/Session |
| `/api/tenant/wells/bulk/` | POST | Bulk query wells by API numbers (max 100) | ✅ JWT/Session |
| `/api/tenant/wells/{api14}/` | GET | Get specific well with interaction history | ✅ JWT/Session |

All endpoints return tenant-isolated data with privacy guarantees.

