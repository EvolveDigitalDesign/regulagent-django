# Data Siloing: Public vs Tenant-Specific Data

## 🔑 The Key Configuration

In `ra_config/settings/base.py`:

```python
SHARED_APPS = [
    'django_tenants',
    'apps.tenants',
    'django.contrib.admin',
    'django.contrib.auth',
    'rest_framework',
    'apps.public_core',      # ← PUBLIC SCHEMA ONLY
    'apps.tenant_overlay',   # ← IN PUBLIC + EVERY TENANT
    'apps.policy',           # ← PUBLIC SCHEMA ONLY
    'apps.policy_ingest',    # ← PUBLIC SCHEMA ONLY
]

TENANT_APPS = [
    'apps.tenant_overlay',   # ← IN EVERY TENANT SCHEMA
]
```

---

## 🎯 How Django-Tenants Uses This

### Rule 1: SHARED_APPS → Public Schema Only
**Apps in `SHARED_APPS` but NOT in `TENANT_APPS`:**
- Tables created **ONLY** in `public` schema
- Accessible from all tenants (shared data)
- Single source of truth

**Example:**
```python
# apps.public_core → SHARED_APPS only
class WellRegistry(models.Model):
    api14 = models.CharField(max_length=14)
    # ...
```

**Result:**
```
public schema:
  └── public_core_well_registry ✓ (shared across all tenants)

demo schema:
  └── (no well_registry table)

test schema:
  └── (no well_registry table)
```

When ANY tenant queries `WellRegistry`, they all see the SAME data from `public` schema.

---

### Rule 2: TENANT_APPS → Every Tenant Schema
**Apps in `TENANT_APPS`:**
- Tables created in **EVERY** tenant schema (public, demo, test, etc.)
- Each tenant has their own isolated copy
- No cross-tenant visibility

**Example:**
```python
# apps.tenant_overlay → TENANT_APPS
class WellEngagement(models.Model):
    tenant_id = models.UUIDField()
    well = models.ForeignKey(WellRegistry)  # FK to shared data
    # ...
```

**Result:**
```
public schema:
  └── overlay_well_engagement (public's engagements)

demo schema:
  └── overlay_well_engagement (demo's engagements)

test schema:
  └── overlay_well_engagement (test's engagements)
```

Each tenant has their own table with their own data. **Completely isolated.**

---

### Rule 3: Both Lists → Public + Every Tenant
**Apps in BOTH `SHARED_APPS` AND `TENANT_APPS`:**
- Tables created in `public` schema
- ALSO created in every tenant schema
- Each schema has independent data

**Example:**
```python
# apps.tenant_overlay is in BOTH lists
# So ALL its models exist in ALL schemas
```

**Result:**
```
public schema:
  ├── overlay_well_engagement
  ├── overlay_canonical_facts
  ├── tenant_overlay_artifacts
  └── tenant_overlay_plan_modifications

demo schema:
  ├── overlay_well_engagement (demo's data)
  ├── overlay_canonical_facts (demo's data)
  ├── tenant_overlay_artifacts (demo's data)
  └── tenant_overlay_plan_modifications (demo's data)

test schema:
  ├── overlay_well_engagement (test's data)
  ├── overlay_canonical_facts (test's data)
  ├── tenant_overlay_artifacts (test's data)
  └── tenant_overlay_plan_modifications (test's data)
```

---

## 📊 Your Current Data Architecture

### Public Data (Shared Across All Tenants)

**`apps.public_core`** - Shared Master Data
```python
✓ WellRegistry           # All wells visible to all tenants
✓ ExtractedDocument      # All extractions shared
✓ DocumentVector         # All embeddings shared
✓ PlanSnapshot           # All snapshots shared
✓ PublicFacts            # Shared well facts
✓ PublicCasingString     # Shared casing data
✓ PublicPerforation      # Shared perforation data
✓ PublicWellDepths       # Shared depth data
```

**`apps.policy`** - Regulatory Rules (Shared)
```python
✓ All policy files       # 08A/7C plugging books
```

**`apps.policy_ingest`** - Policy Database (Shared)
```python
✓ PolicyRule            # Texas Admin Code rules
✓ PolicySection         # Rule sections (3.14, etc.)
```

**`apps.tenants`** - User & Tenant Management (Shared)
```python
✓ User                  # Global user accounts
✓ Tenant                # Tenant definitions
✓ Domain                # Domain routing
```

**Why Shared?**
- Wells don't belong to one tenant (public registry)
- Policies are the same for everyone (regulations)
- Users can belong to multiple tenants
- Extractions and embeddings can be reused

---

### Tenant-Specific Data (Isolated Per Tenant)

**`apps.tenant_overlay`** - Customer's Private Data
```python
🔒 WellEngagement              # Which wells THIS tenant is working on
🔒 CanonicalFacts              # THIS tenant's fact overrides
🔒 TenantArtifact              # THIS tenant's uploaded files
🔒 PlanModification            # THIS tenant's plan edits/history
🔒 UserTenantPermissions       # User permissions in THIS tenant
```

**Why Isolated?**
- Each customer's projects are private
- Plan modifications are proprietary
- Uploaded files belong to the customer
- Chat histories will be tenant-specific (future)

---

## 🔄 How Queries Work

### Example 1: Querying Shared Data (WellRegistry)

```python
# User in demo tenant queries wells
from apps.public_core.models import WellRegistry

wells = WellRegistry.objects.all()
```

**What Happens:**
1. Connection is in `demo` schema
2. Django tries: `SELECT * FROM overlay_well_registry`
3. Table doesn't exist in demo schema
4. Django-tenants router redirects to `public` schema
5. Query: `SELECT * FROM public.public_core_well_registry`
6. Returns: ALL wells (shared across all tenants)

---

### Example 2: Querying Tenant Data (WellEngagement)

```python
# User in demo tenant queries engagements
from apps.tenant_overlay.models import WellEngagement

engagements = WellEngagement.objects.all()
```

**What Happens:**
1. Connection is in `demo` schema
2. Query: `SELECT * FROM demo.overlay_well_engagement`
3. Returns: ONLY demo tenant's engagements
4. Test tenant's engagements are in `test.overlay_well_engagement` (invisible)

---

### Example 3: Foreign Key Across Boundaries

```python
# Tenant-specific data referencing shared data
class WellEngagement(models.Model):
    tenant_id = models.UUIDField()
    well = models.ForeignKey(
        'public_core.WellRegistry',  # ← Points to PUBLIC schema
        on_delete=models.CASCADE
    )
```

**What Happens:**
```
demo.overlay_well_engagement
├── id: 1
├── tenant_id: demo-uuid
└── well_id: 42 ──┐
                  │
                  └──→ public.public_core_well_registry
                        └── id: 42 (API 42-123-45678)
```

**Result:**
- Demo's engagement references shared well
- Test can have engagement for the SAME well
- But they can't see each other's engagements

---

## 🧪 Verification

Let me verify this RIGHT NOW in your database:

```bash
# Show which apps have tables in which schemas
docker exec regulagent_db psql -U postgres -d regulagent -c "
SELECT 
    schemaname,
    COUNT(*) as table_count,
    string_agg(tablename, ', ' ORDER BY tablename) as tables
FROM pg_tables 
WHERE schemaname IN ('public', 'demo', 'test')
GROUP BY schemaname
ORDER BY schemaname;
"
```

Let me run this:

