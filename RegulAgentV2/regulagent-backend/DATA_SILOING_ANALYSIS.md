# Data Siloing Analysis: Current vs Roadmap Requirements

## 🔍 Analysis Date
November 1, 2025

## 📋 Executive Summary

After reviewing the Consolidated AI Roadmap against the current data siloing setup:

**Status**: ⚠️ **NEEDS MODIFICATIONS**

- ✅ **Good**: `PlanModification` already tenant-isolated
- ✅ **Good**: Core public data (wells, policies) properly shared
- ⚠️ **Issues**: 4 data models need tenant-awareness additions
- 🆕 **Missing**: 6 new tenant-scoped models required for AI features

---

## 🎯 Roadmap Requirements for Tenant Isolation

### Key Statements from Roadmap

**Line 25**: "Tenant boundaries: tenant/org IDs to enforce isolation" (in embeddings)

**Line 40**: "Tenant/privacy: Strict tenant filters; cross-tenant only for public data or de-identified aggregates"

**Line 46**: `DocumentVector.metadata` should include `{ tenant_id, operator, district, county, field, ... }`

**Line 94**: "Tenant scope: all ops and history scoped to tenant; accepted edits mined into optional `TenantOverlayRule` proposals"

**Lines 296-298**: 
- "Use tenant IDs or schemas for private chat histories and plan iterations"
- "Split `PlanSnapshot` into public (initial/final) vs private (tenant-specific edits)"

**Line 305-306**:
- "Utilize tenant-specific accepted modifications for AI enhancement"
- "Ensure anonymized learning contributes to public improvements"

---

## 📊 Current State Assessment

### ✅ CORRECT: Properly Shared Data (Public Schema)

These models are **correctly** in `apps.public_core` (SHARED_APPS only):

| Model | Current Location | Roadmap Requirement | Status |
|-------|-----------------|---------------------|--------|
| `WellRegistry` | public_core (shared) | Shared across tenants | ✅ CORRECT |
| `PolicyRule` | policy_ingest (shared) | Shared regulatory rules | ✅ CORRECT |
| `PolicySection` | policy_ingest (shared) | Shared regulatory rules | ✅ CORRECT |
| `User` | tenants (shared) | Global user accounts | ✅ CORRECT |
| `Tenant` | tenants (shared) | Tenant definitions | ✅ CORRECT |

**Rationale**: Wells, policies, and users are shared resources that all tenants access.

---

### ✅ CORRECT: Properly Isolated Data (Tenant Schemas)

These models are **correctly** in `apps.tenant_overlay` (TENANT_APPS):

| Model | Current Location | Roadmap Requirement | Status |
|-------|-----------------|---------------------|--------|
| `WellEngagement` | tenant_overlay (isolated) | Tenant's private well projects | ✅ CORRECT |
| `CanonicalFacts` | tenant_overlay (isolated) | Tenant's fact overrides | ✅ CORRECT |
| `TenantArtifact` | tenant_overlay (isolated) | Tenant's uploaded files | ✅ CORRECT |
| `PlanModification` | tenant_overlay (isolated) | Tenant's plan edit history | ✅ CORRECT |

**Rationale**: Each tenant's engagements, edits, and files are private.

---

### ⚠️ ISSUE 1: DocumentVector Needs Tenant Awareness

**Current State**:
```python
# apps/public_core/models/document_vector.py
class DocumentVector(models.Model):
    document = ForeignKey(ExtractedDocument)
    embedding = VectorField(dimensions=1536)
    metadata = JSONField()  # ← metadata exists but structure unclear
```

**Location**: `apps.public_core` (SHARED_APPS only) → **PUBLIC SCHEMA**

**Roadmap Requirement** (Line 46):
```
DocumentVector.metadata: { 
    tenant_id,        ← MISSING
    operator, 
    district, 
    county, 
    field, 
    lat, 
    lon, 
    step_types, 
    materials, 
    approval_status, 
    overlay_id, 
    kernel_version 
}
```

**Recommendation**: 
- ✅ Keep in `public_core` (shared schema)
- ⚠️ **ADD** `tenant_id` to `metadata` JSON field
- Add index on `metadata->>'tenant_id'` for fast filtering
- When retrieving similar wells: `WHERE metadata->>'tenant_id' = current_tenant.id`

**Why Not Move to Tenant Schema?**
- Embeddings can be reused across tenants for public/anonymized learning
- Filtering by tenant_id in metadata gives privacy + learning flexibility
- Line 40: "cross-tenant only for public data or de-identified aggregates"

---

### ⚠️ ISSUE 2: ExtractedDocument Needs Tenant Awareness

**Current State**:
```python
# apps/public_core/models/extracted_document.py
class ExtractedDocument(models.Model):
    well = ForeignKey(WellRegistry)
    api_number = CharField()
    document_type = CharField()
    # NO tenant_id field
```

**Location**: `apps.public_core` (SHARED_APPS only) → **PUBLIC SCHEMA**

**Roadmap Implication**:
- Extractions are done by tenants (each tenant uploads/processes docs)
- But wells are shared, so extractions could be shared with attribution

**Recommendation**:
- ✅ Keep in `public_core` (shared schema)
- ⚠️ **ADD** `tenant_id` field (nullable, for attribution)
- ⚠️ **ADD** `is_public` boolean (allow opt-in sharing)
- Filter by tenant when retrieving: show tenant's own + public extractions

**Rationale**:
- Tenant A extracts a well's W-2 → other tenants could benefit
- But tenant can mark as private if proprietary
- Aligns with Line 306: "anonymized learning contributes to public improvements"

---

### ⚠️ ISSUE 3: PlanSnapshot Needs Public/Private Split

**Current State**:
```python
# apps/public_core/models/plan_snapshot.py
class PlanSnapshot(models.Model):
    plan_id = CharField()
    kind = CharField()  # baseline, post_edit, submitted, approved
    # NO tenant_id or public/private distinction
```

**Location**: `apps.public_core` (SHARED_APPS only) → **PUBLIC SCHEMA**

**Roadmap Requirement** (Lines 296-298):
> "Split `PlanSnapshot` into public (initial/final) vs private (tenant-specific edits)"

**Recommendation**:
- ✅ Keep in `public_core` (shared schema for technical reasons)
- ⚠️ **ADD** `tenant_id` field (required)
- ⚠️ **ADD** `visibility` field: `['public', 'private']`
- Public snapshots (kind='baseline', 'approved') → can be shared/learned from
- Private snapshots (kind='post_edit') → tenant-only

**Usage**:
```python
# Baseline plan (public, can feed learning)
PlanSnapshot(kind='baseline', visibility='public', tenant_id=null)

# Tenant's edit iterations (private)
PlanSnapshot(kind='post_edit', visibility='private', tenant_id='demo-uuid')

# Final approved plan (public, can feed precedent search)
PlanSnapshot(kind='approved', visibility='public', tenant_id='demo-uuid')
```

---

### 🆕 MISSING MODELS: New App Required

**Roadmap Lines 108-113, 223-226**: Create new `apps/assistant` app

#### Required Models (ALL Tenant-Scoped):

| Model | Location | Tenant Isolation | Status |
|-------|----------|------------------|--------|
| `ChatThread` | **NEW**: apps.assistant | ✅ Add to TENANT_APPS | 🆕 CREATE |
| `ChatMessage` | **NEW**: apps.assistant | ✅ Add to TENANT_APPS | 🆕 CREATE |
| `ChatVector` | **NEW**: apps.assistant | ✅ Add to TENANT_APPS | 🆕 CREATE |
| `TenantPreference` | **NEW**: apps.assistant | ✅ Add to TENANT_APPS | 🆕 CREATE |
| `TenantOverlayRule` | **NEW**: apps.assistant or tenant_overlay | ✅ Add to TENANT_APPS | 🆕 CREATE |
| `PlanOutcome` | **NEW**: apps.public_core | ⚠️ SHARED_APPS (with tenant_id) | 🆕 CREATE |

---

## 🏗️ Required Changes Summary

### 1. Extend Existing Models (apps.public_core)

```python
# apps/public_core/models/document_vector.py
class DocumentVector(models.Model):
    # ... existing fields ...
    
    # ENSURE metadata has this structure:
    metadata = JSONField(default=dict)
    # metadata = {
    #     'tenant_id': 'uuid',          ← ADD
    #     'operator': 'string',
    #     'district': 'string',
    #     'county': 'string',
    #     'field': 'string',
    #     'lat': float,
    #     'lon': float,
    #     'step_types': [],
    #     'materials': {},
    #     'approval_status': 'string',
    #     'overlay_id': 'uuid',
    #     'kernel_version': 'string'
    # }
    
    class Meta:
        indexes = [
            # ADD for fast tenant filtering:
            GinIndex(fields=['metadata']),
            models.Index(
                OpClass(F('metadata__tenant_id'), name='gin_trgm_ops'),
                name='docvector_tenant_idx'
            ),
        ]


# apps/public_core/models/extracted_document.py
class ExtractedDocument(models.Model):
    # ... existing fields ...
    
    # ADD:
    tenant_id = models.UUIDField(null=True, blank=True)  # Attributing tenant
    is_public = models.BooleanField(default=False)       # Can be shared?
    
    class Meta:
        indexes = [
            models.Index(fields=['tenant_id', 'api_number']),
        ]


# apps/public_core/models/plan_snapshot.py
class PlanSnapshot(models.Model):
    # ... existing fields ...
    
    # ADD:
    tenant_id = models.UUIDField(null=True, blank=True)
    visibility = models.CharField(
        max_length=10,
        choices=[('public', 'Public'), ('private', 'Private')],
        default='private'
    )
    
    class Meta:
        indexes = [
            models.Index(fields=['tenant_id', 'plan_id', 'kind']),
            models.Index(fields=['visibility', 'kind']),
        ]


# apps/public_core/models/well_registry.py (line 44 of roadmap)
class WellRegistry(models.Model):
    # ... existing fields ...
    
    # ADD (if not present):
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    operator_name = models.CharField(max_length=255, blank=True)
    spud_date = models.DateField(null=True, blank=True)
    well_type = models.CharField(max_length=50, blank=True)


# apps/public_core/models/plan_outcome.py ← NEW FILE
class PlanOutcome(models.Model):
    """
    Tracks regulatory approval outcomes for plans.
    Stored in PUBLIC schema to enable cross-tenant learning.
    """
    api = models.CharField(max_length=14, db_index=True)
    plan_snapshot = models.ForeignKey(PlanSnapshot, on_delete=models.CASCADE)
    
    # Attribution
    tenant_id = models.UUIDField(db_index=True)
    
    # Outcome
    submitted_at = models.DateTimeField()
    approved_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ('submitted', 'Submitted'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('revision_requested', 'Revision Requested')
        ]
    )
    revisions = models.IntegerField(default=0)
    
    # Regulator feedback
    reviewer_notes = models.TextField(blank=True)
    reviewer_notes_summary_embedding = VectorField(
        dimensions=1536,
        null=True,
        blank=True
    )
    
    class Meta:
        indexes = [
            models.Index(fields=['tenant_id', 'status']),
            models.Index(fields=['api', 'approved_at']),
        ]
```

---

### 2. Create New App: apps/assistant

```bash
# Create the app
python manage.py startapp assistant apps/assistant

# Add to settings.py:
TENANT_APPS = [
    'apps.tenant_overlay',
    'apps.assistant',  # ← ADD (chat is tenant-scoped)
]
```

```python
# apps/assistant/models/chat_thread.py
class ChatThread(models.Model):
    """
    Conversational thread tied to a well engagement.
    TENANT-SCOPED: Each tenant has their own chat history.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    tenant_id = models.UUIDField(db_index=True)  # Redundant but explicit
    
    # Links
    well_id = models.CharField(max_length=14)  # API14
    plan_id = models.UUIDField(null=True, blank=True)
    engagement = models.ForeignKey(
        'tenant_overlay.WellEngagement',
        on_delete=models.CASCADE,
        related_name='chat_threads'
    )
    
    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )
    mode = models.CharField(max_length=20, default='assistant')
    system_purpose = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['tenant_id', 'well_id']),
            models.Index(fields=['engagement', 'created_at']),
        ]


# apps/assistant/models/chat_message.py
class ChatMessage(models.Model):
    """
    Individual message in a chat thread.
    TENANT-SCOPED via thread relationship.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    thread = models.ForeignKey(
        ChatThread,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    
    role = models.CharField(
        max_length=10,
        choices=[('user', 'User'), ('assistant', 'Assistant'), ('system', 'System')]
    )
    content = models.TextField()
    
    # Tool usage
    tool_calls = models.JSONField(default=list, blank=True)
    tool_results = models.JSONField(default=list, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['thread', 'created_at']),
        ]
        ordering = ['created_at']


# apps/assistant/models/chat_vector.py
class ChatVector(models.Model):
    """
    Embeddings of chat messages for RAG over conversation history.
    TENANT-SCOPED via thread relationship.
    """
    thread = models.ForeignKey(ChatThread, on_delete=models.CASCADE)
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE)
    
    embedding = VectorField(dimensions=1536)
    metadata = models.JSONField(default=dict)
    
    class Meta:
        indexes = [
            models.Index(fields=['thread']),
        ]


# apps/assistant/models/tenant_preference.py
class TenantPreference(models.Model):
    """
    Tenant-specific learned preferences and defaults.
    TENANT-SCOPED: Each tenant has their own preferences.
    """
    tenant_id = models.UUIDField(db_index=True)
    
    category = models.CharField(max_length=50)  # e.g., 'plugging_strategy'
    key = models.CharField(max_length=100)      # e.g., 'prefer_long_plugs'
    value = models.JSONField()
    
    # Learning context
    confidence = models.FloatField(default=0.0)
    sample_count = models.IntegerField(default=0)
    last_applied = models.DateTimeField(null=True, blank=True)
    
    enabled = models.BooleanField(default=False)  # User must opt-in
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = [['tenant_id', 'category', 'key']]
        indexes = [
            models.Index(fields=['tenant_id', 'enabled']),
        ]


# apps/assistant/models/tenant_overlay_rule.py
class TenantOverlayRule(models.Model):
    """
    Learned rules from tenant's accepted modifications.
    TENANT-SCOPED: Each tenant's learned behaviors.
    """
    tenant_id = models.UUIDField(db_index=True)
    
    trigger = models.JSONField()  # Conditions when to apply
    adjustment = models.JSONField()  # What to change
    
    # Evidence
    provenance = models.JSONField()  # Which PlanModifications led to this
    confidence = models.FloatField()
    risk_score = models.FloatField()
    
    # Control
    enabled = models.BooleanField(default=False)  # Must be explicitly enabled
    applied_count = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['tenant_id', 'enabled']),
        ]
```

---

### 3. Update Settings Configuration

```python
# ra_config/settings/base.py

SHARED_APPS = [
    'django_tenants',
    'apps.tenants',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'django_filters',
    'corsheaders',
    'tenant_users.permissions',
    'tenant_users.tenants',
    'apps.public_core',      # ← Shared data (wells, policies, extractions)
    'apps.tenant_overlay',   # ← Also in TENANT_APPS (both schemas)
    'apps.assistant',        # ← ADD: Also in TENANT_APPS (both schemas)
    'apps.policy',
    'apps.policy_ingest',
]

TENANT_APPS = [
    'apps.tenant_overlay',   # ← Tenant-specific engagements/facts
    'apps.assistant',        # ← ADD: Tenant-specific chat/preferences
]
```

---

## 🎯 Migration Strategy

### Phase 1: Extend Existing Models (Non-Breaking)
1. Add `tenant_id` to `ExtractedDocument` (nullable)
2. Add `tenant_id`, `visibility` to `PlanSnapshot` (nullable/default)
3. Extend `DocumentVector.metadata` structure (JSON, non-breaking)
4. Add geospatial fields to `WellRegistry` (nullable)
5. Create `PlanOutcome` model

### Phase 2: Create Assistant App (New Feature)
1. Create `apps/assistant` app
2. Add to `TENANT_APPS`
3. Create all chat/preference models
4. Run `makemigrations` and `migrate_schemas`

### Phase 3: Backfill Tenant IDs (Data Migration)
1. Assign `tenant_id` to existing extractions/snapshots
2. Populate `DocumentVector.metadata` with tenant_ids
3. Set default visibility for existing snapshots

---

## ✅ Validation Checklist

After implementing changes, verify:

- [ ] `DocumentVector` queries filter by `metadata->>'tenant_id'`
- [ ] `ExtractedDocument` shows tenant's own + public docs only
- [ ] `PlanSnapshot` with visibility='private' not visible to other tenants
- [ ] `ChatThread` exists only in tenant schemas (demo, test, etc.)
- [ ] `ChatMessage` cannot be queried across tenant boundaries
- [ ] `TenantPreference` isolated per tenant
- [ ] `PlanOutcome` tracks outcomes but respects tenant attribution

---

## 🎉 Summary

**Required Changes**: 4 model extensions + 1 new app (6 new models)

**Tenant Isolation Strategy**:
- **Physical isolation** (separate schemas): Chat, Preferences, Rules
- **Logical isolation** (tenant_id + filters): Vectors, Extractions, Snapshots, Outcomes
- **Shared data** (no isolation): Wells, Policies, Users

**Rationale**:
- Chat/preferences are purely private → full schema isolation
- Vectors/extractions enable learning → logical isolation with opt-in sharing
- Wells/policies are regulatory/public → fully shared

**This aligns with Roadmap Line 40**: 
> "Strict tenant filters; cross-tenant only for public data or de-identified aggregates"

---

**Ready to implement?** Let me know if you want me to start creating these migrations and models!

