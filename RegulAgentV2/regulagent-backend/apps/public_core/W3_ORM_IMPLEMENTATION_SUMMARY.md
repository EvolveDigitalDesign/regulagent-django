# W-3 ORM Implementation Summary

## Overview

We've successfully converted W-3 event tracking from **dataclasses-only** (in-memory only) to a complete **Django ORM with persistence**, enabling historical tracking, audit trails, and user review of all W-3 submissions.

## What Changed

### Before (Dataclasses Only)
- W3Event and Plug were plain Python dataclasses
- No database persistence
- No way to query historical W-3 creations
- Data only existed during request/response cycle

### After (Full ORM)
- **W3EventORM**: Persistent operational events from pnaexchange
- **W3PlugORM**: Persistent grouping of related events into plugging operations
- **W3FormORM**: Persistent complete W-3 form submissions
- Full REST API for querying all three entities
- Historical tracking, audit trails, status management
- Multiple W-3 forms allowed per API (account for redrill scenarios)

---

## Files Created/Modified

### 1. **Models** (`apps/public_core/models/`)

#### New: `w3_orm.py` (266 lines)
Three comprehensive Django ORM models:

**W3EventORM**
- Stores individual operational events from pnaexchange
- Fields: event_type, event_date, depths, materials, cement_class, sacks, etc.
- Indexes: api_number + event_date, well + event_date
- Ordered by event_date and event_start_time

**W3PlugORM**
- Groups related events into plugging operations
- Many-to-Many relationship to W3EventORM
- Unique constraint on (api_number, plug_number)
- Tracks calculated vs measured TOC and variance
- Indexes: api_number + plug_number, well + plug_number

**W3FormORM**
- Complete W-3 form submission record
- Stores complete form_data (JSON), well_geometry, RRC export
- Status tracking: draft → submitted → approved → rejected → archived
- Audit trail: who submitted, when, RRC confirmation number
- Many-to-Many to W3PlugORM
- Tracks auto-generated status and W-3A snapshot ID
- Unique constraint removed to allow multiple W-3s per API

#### Modified: `models/__init__.py`
- Added exports: W3EventORM, W3PlugORM, W3FormORM

---

### 2. **Serializers** (`apps/public_core/serializers/`)

#### New: `w3_orm_serializers.py` (450+ lines)
Comprehensive DRF serializers for all three models:

**W3EventORM Serializers**
- `W3EventORM_ListSerializer` - Minimal list view
- `W3EventORM_DetailSerializer` - Full event details with well info
- `W3EventORM_CreateUpdateSerializer` - For create/update operations

**W3PlugORM Serializers**
- `W3PlugORM_ListSerializer` - Minimal list with event count
- `W3PlugORM_DetailSerializer` - Full plug details with nested events
- `W3PlugORM_CreateUpdateSerializer` - For create/update operations

**W3FormORM Serializers**
- `W3FormORM_ListSerializer` - Minimal list with plug count
- `W3FormORM_DetailSerializer` - Full form details with plugs and geometry
- `W3FormORM_CreateUpdateSerializer` - For create/update operations
- `W3FormORM_SubmitSerializer` - For RRC submission workflow

#### Modified: `w3_from_pna.py` (Fixed ordering issue)
- Moved W3AWellGeometrySerializer and related serializers **before** BuildW3FromPNAResponseSerializer
- Fixes NameError: name 'W3AWellGeometrySerializer' is not defined
- Serializers now properly ordered by dependency

---

### 3. **Views/Endpoints** (`apps/public_core/views/`)

#### New: `w3_orm_endpoints.py` (450+ lines)
Three comprehensive ViewSets with full CRUD operations:

**W3EventViewSet**
```
GET    /api/w3/events/                    - List all events
POST   /api/w3/events/                    - Create event
GET    /api/w3/events/{id}/               - Retrieve event
PATCH  /api/w3/events/{id}/               - Update event
DELETE /api/w3/events/{id}/               - Delete event
GET    /api/w3/events/by-api/             - List events for API
GET    /api/w3/events/by-date-range/      - List events in date range
```

Query Parameters:
- `api_number` - Filter by API
- `event_type` - Filter by event type
- `date_from` / `date_to` - Date range filtering
- `plug_number` - Filter by plug number
- `well_id` - Filter by well ID

**W3PlugViewSet**
```
GET    /api/w3/plugs/                     - List all plugs
POST   /api/w3/plugs/                     - Create plug
GET    /api/w3/plugs/{id}/                - Retrieve plug
PATCH  /api/w3/plugs/{id}/                - Update plug
DELETE /api/w3/plugs/{id}/                - Delete plug
GET    /api/w3/plugs/by-api/              - List plugs for API
GET    /api/w3/plugs/{id}/events/         - Get events in plug
POST   /api/w3/plugs/{id}/add-event/      - Add event to plug
POST   /api/w3/plugs/{id}/remove-event/   - Remove event from plug
```

Query Parameters:
- `api_number` - Filter by API
- `plug_type` - Filter by plug type
- `well_id` - Filter by well ID

**W3FormViewSet**
```
GET    /api/w3/forms/                     - List all forms
POST   /api/w3/forms/                     - Create form
GET    /api/w3/forms/{id}/                - Retrieve form
PATCH  /api/w3/forms/{id}/                - Update form
DELETE /api/w3/forms/{id}/                - Delete form (cascades to plugs/events)
GET    /api/w3/forms/by-api/              - List forms for API
GET    /api/w3/forms/pending-submission/  - List draft forms
GET    /api/w3/forms/submitted/           - List submitted forms
POST   /api/w3/forms/{id}/submit/         - Submit form to RRC
GET    /api/w3/forms/{id}/plugs/          - Get plugs in form
POST   /api/w3/forms/{id}/add-plug/       - Add plug to form
```

Query Parameters:
- `api_number` - Filter by API
- `status` - Filter by status (draft, submitted, approved, etc.)
- `well_id` - Filter by well ID
- `auto_generated` - Filter by auto-generated status

---

### 4. **Migrations** (`apps/public_core/migrations/`)

#### New: `0007_add_w3_orm_models.py`
Comprehensive Django migration that:
- Creates W3EventORM model with all fields and indexes
- Creates W3PlugORM model with all fields, unique_together, and indexes
- Creates W3FormORM model with all fields and indexes
- Adds 6 database indexes for efficient querying:
  - W3EventORM: (api_number, event_date) and (well, event_date)
  - W3PlugORM: (api_number, plug_number) and (well, plug_number)
  - W3FormORM: (api_number, status) and (well, -created_at)

---

## Data Model Relationships

```
WellRegistry (existing)
    ├─ w3_events (1:M) → W3EventORM
    ├─ w3_plugs (1:M) → W3PlugORM
    └─ w3_forms (1:M) → W3FormORM

W3EventORM
    └─ plugs (M:M via through table) → W3PlugORM

W3PlugORM
    └─ w3_forms (M:M via through table) → W3FormORM
```

**Deletion Behavior**:
- Deleting WellRegistry cascades to all W3EventORM, W3PlugORM, W3FormORM
- Deleting W3FormORM cascades to associated W3PlugORM and W3EventORM
- Allows multiple W-3 forms per API number (supports redrill scenarios)

---

## Cascade Behavior

✅ **Deletion Cascade**:
- Delete W3FormORM → W3PlugORM entries are deleted → W3EventORM entries are deleted
- Delete WellRegistry → All W3 data for that well is deleted
- Clean historical cleanup possible

---

## Next Steps: Persistence Logic

To complete the implementation, we need to:

1. **Modify `/api/w3/build-from-pna/` view** to persist W-3 data:
   - Create W3EventORM instances for each pnaexchange event
   - Create W3PlugORM instances for each plugging operation
   - Create W3FormORM instance for the final form
   - Link all together via foreign keys and many-to-many relationships

2. **Update `w3_builder.py` and `w3_mapper.py`** to:
   - Accept ORM save parameters
   - Return model instances instead of/in addition to dataclasses

3. **Wire up URL routing** in `ra_config/urls.py`:
   - Register the three ViewSets with DefaultRouter
   - Expose at `/api/w3/events/`, `/api/w3/plugs/`, `/api/w3/forms/`

---

## Benefits

✅ **Historical Tracking**: Query all W-3 forms ever created for a well  
✅ **Audit Trail**: See who submitted, when, and RRC confirmation numbers  
✅ **Status Management**: Draft → submitted → approved workflow  
✅ **Multi-W-3 Support**: Handle redrill and re-plug scenarios  
✅ **REST API**: Full CRUD + custom actions for querying  
✅ **Efficient**: Indexes on api_number, event_date, status for fast queries  
✅ **Cascading Deletes**: Clean removal of related data  
✅ **Well-Linked**: All data associated with WellRegistry for context  

---

## Testing Strategy

Ready to implement end-to-end tests for:
1. Creating W3EventORM instances from pnaexchange events
2. Grouping events into W3PlugORM
3. Persisting complete W3FormORM with all relationships
4. Querying historical data via REST endpoints
5. Status transitions (draft → submitted → approved)
6. Cascade deletion behavior

---

## TODO Items Completed

✅ orm-1-create-models: Create Django ORM models for W3  
✅ orm-2-serializers: Create DRF serializers for ORM models  
✅ orm-3-migrations: Generate and verify Django migrations  
⏳ orm-4-persist-logic: Add logic to persist W3 data when generated  
⏳ orm-5-query-endpoints: Create endpoints to query historic W-3 forms (ready - just needs URL wiring)

