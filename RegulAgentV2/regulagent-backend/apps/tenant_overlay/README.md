# Tenant Overlay App - Tenant-Specific Data & Fact Resolution

## Purpose

The **tenant_overlay** app provides tenant-specific (customer) data overlays that enhance or override public regulator data. It manages engagements (work sessions per well), stores canonical facts (tenant-provided corrections/additions), and resolves the final fact set by merging canonical → public → registry data with proper precedence. This enables RegulAgent to incorporate operator knowledge while maintaining public data as the foundation.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    TENANT_OVERLAY APP                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  PRECEDENCE:  Canonical Facts > Public Facts > Well Registry    │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │  Django Models (PostgreSQL)                                   ││
│  │  ├─> WellEngagement         Tenant work session per well     ││
│  │  └─> CanonicalFacts         Tenant fact overrides            ││
│  └──────────────────────────────────────────────────────────────┘│
│           ↓                                                       │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │  Services                                                      ││
│  │  └─> facts_resolver.py                                        ││
│  │       └─> resolve_engagement_facts()   Merge facts with      ││
│  │                                         precedence rules      ││
│  └──────────────────────────────────────────────────────────────┘│
│           ↓                                                       │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │  API Views                                                     ││
│  │  └─> ResolvedFactsView      GET /api/engagements/{id}/facts/ ││
│  └──────────────────────────────────────────────────────────────┘│
│           ↓                                                       │
│  OUTPUT: Resolved Facts Dictionary → Kernel for Plan Generation  │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### Inputs (From)
- **Tenant User Input** (UI or API)
  - Corrections to public data (e.g., "Surface shoe is actually 1250 ft, not 1200")
  - Additional facts not in public record (e.g., "Packer at 5000 ft")
  - Engagement metadata (mode: plugging/workover, tenant preferences)

- **Public Core App** (`public_core/`)
  - PublicFacts: Regulator data
  - WellRegistry: Well identity

### Processing
1. **Engagement Creation** - Tenant starts work session on a well
2. **Canonical Fact Entry** - Tenant adds/corrects facts
3. **Fact Resolution** - Merge canonical + public + registry with precedence

### Outputs (To)
- **Kernel App** (`kernel/`)
  - Resolved facts dictionary for plan generation
  
- **Frontend UI**
  - Fact provenance display (source_layer: canonical/public/registry)
  - Confidence scores

---

## Key Models

### 1. `WellEngagement` Model

**Purpose:** Represent a tenant work session on a specific well.

**Fields:**
```python
class WellEngagement(models.Model):
    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE, 
                             related_name='engagements')
    
    tenant_id = models.CharField(max_length=64, db_index=True)
        # Customer identifier: "xto", "shell", "conocophillips"
    
    mode = models.CharField(max_length=32, default='plugging')
        # "plugging", "workover", "completion", "inspection"
    
    status = models.CharField(max_length=32, default='active')
        # "active", "submitted", "approved", "archived"
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

**Relationships:**
- `canonical_facts` (reverse FK): Tenant overrides/additions

**Use Cases:**
- Track which wells a tenant is working on
- Isolate canonical facts per engagement (not global per well)
- Support multi-tenant scenarios (different customers, same well)

**Example:**
```python
# XTO working on plugging plan for well 42-000-12345
engagement = WellEngagement.objects.create(
    well=well,
    tenant_id="xto",
    mode="plugging"
)
```

---

### 2. `CanonicalFacts` Model

**Purpose:** Store tenant-specific fact overrides/additions (precedence over public data).

**Fields:**
```python
class CanonicalFacts(models.Model):
    engagement = models.ForeignKey(WellEngagement, on_delete=models.CASCADE,
                                   related_name='canonical_facts')
    
    fact_key = models.CharField(max_length=128)
        # "surface_shoe_ft", "packer_ft", "existing_cibp_ft"
    
    value = models.JSONField()
        # Typed value: 1250 (int), "Spraberry" (str), [7200, 8450] (list)
    
    units = models.CharField(max_length=32, blank=True)
        # "ft", "in", "ppg"
    
    provenance = models.JSONField(default=list)
        # [{"fragment_id": 123, "artifact": "wellbore_schematic_2024.pdf"}]
    
    confidence = models.DecimalField(max_digits=4, decimal_places=2, 
                                     null=True, blank=True)
        # 0.00 - 1.00 (1.00 = certain)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

**Unique Together:** `(engagement, fact_key)`
- One canonical value per fact per engagement

**Example Records:**
```
engagement_id: 1, fact_key: surface_shoe_ft, value: 1250, confidence: 1.00
engagement_id: 1, fact_key: packer_ft, value: 5000, confidence: 0.95
engagement_id: 1, fact_key: existing_cibp_ft, value: 8500, confidence: 1.00
```

**Rationale for "Canonical":**
The term "canonical" signifies **authoritative, tenant-validated data** that takes precedence over public (potentially outdated or incomplete) regulator data. Operators have firsthand knowledge of their wells.

---

## Key Services

### 1. `facts_resolver.py` - Fact Resolution Engine

#### **`resolve_engagement_facts(engagement_id)`**
**Purpose:** Merge facts from three sources with precedence: Canonical > Public > Registry.

**Parameters:**
- `engagement_id`: WellEngagement primary key

**Returns:**
Dictionary of facts keyed by `fact_key`, each with:
```python
{
    "value": <any>,           # The actual value
    "units": str,             # Units (ft, in, ppg)
    "source_layer": str,      # "canonical", "public", "registry"
    "provenance": list,       # Audit trail
    "confidence": float       # 0.0-1.0 or None
}
```

**Logic Flow:**

**Step 1: Load Engagement & Well**
```python
engagement = WellEngagement.objects.select_related("well").get(id=engagement_id)
well = engagement.well
```

**Step 2: Initialize Resolved Dictionary**
```python
resolved = {}
```

**Step 3: Layer 1 - CanonicalFacts (Highest Priority)**
```python
for cf in CanonicalFacts.objects.filter(engagement=engagement):
    resolved[cf.fact_key] = _pack(
        value=cf.value,
        units=cf.units,
        source_layer="canonical",
        provenance=cf.provenance or [],
        confidence=cf.confidence
    )
```

**Example:**
```python
resolved["surface_shoe_ft"] = {
    "value": 1250,
    "units": "ft",
    "source_layer": "canonical",
    "provenance": [{"source": "tenant_upload"}],
    "confidence": 1.00
}
```

**Step 4: Layer 2 - PublicFacts (Only if not already set)**
```python
for pf in PublicFacts.objects.filter(well=well):
    if pf.fact_key not in resolved:
        resolved[pf.fact_key] = _pack(
            value=pf.value,
            units=pf.units,
            source_layer="public",
            provenance=pf.provenance,
            confidence=None
        )
```

**Example:**
```python
# If surface_shoe_ft already set by canonical, skip
# If production_shoe_ft not in canonical:
resolved["production_shoe_ft"] = {
    "value": 9850,
    "units": "ft",
    "source_layer": "public",
    "provenance": [{"source": "rrc.w2.casing_record"}],
    "confidence": None
}
```

**Step 5: Layer 3 - WellRegistry (Only if not already set)**
```python
registry_fallbacks = {
    "api14": well.api14,
    "state": well.state,
    "county": well.county,
    "lat": float(well.lat) if well.lat else None,
    "lon": float(well.lon) if well.lon else None,
}

for key, val in registry_fallbacks.items():
    if val is None:
        continue
    if key not in resolved:
        resolved[key] = _pack(
            value=val,
            units="",
            source_layer="registry",
            provenance=[],
            confidence=None
        )
```

**Example:**
```python
resolved["api14"] = {
    "value": "42-000-12345-00-00",
    "units": "",
    "source_layer": "registry",
    "provenance": [],
    "confidence": None
}
```

**Step 6: Return Resolved Facts**
```python
return resolved
```

**Full Example Output:**
```python
{
    "api14": {
        "value": "42-000-12345-00-00",
        "units": "",
        "source_layer": "registry",
        "provenance": [],
        "confidence": None
    },
    "surface_shoe_ft": {
        "value": 1250,              # Canonical override (was 1200 in public)
        "units": "ft",
        "source_layer": "canonical",
        "provenance": [{"tenant_upload": "schematic_2024.pdf"}],
        "confidence": 1.00
    },
    "production_shoe_ft": {
        "value": 9850,              # From PublicFacts (no canonical override)
        "units": "ft",
        "source_layer": "public",
        "provenance": [{"source": "rrc.w2"}],
        "confidence": None
    },
    "packer_ft": {
        "value": 5000,              # Canonical addition (not in public data)
        "units": "ft",
        "source_layer": "canonical",
        "provenance": [{"fragment_id": 42}],
        "confidence": 0.95
    },
    "county": {
        "value": "Andrews",
        "units": "",
        "source_layer": "registry",
        "provenance": [],
        "confidence": None
    }
}
```

---

#### **`_pack(value, units, source_layer, provenance, confidence)`**
**Purpose:** Helper to create standardized fact payload.

**Returns:**
```python
{
    "value": value,
    "units": units or "",
    "source_layer": source_layer,
    "provenance": provenance or [],
    "confidence": confidence
}
```

---

## API Views

### 1. `ResolvedFactsView` - Get Resolved Facts

**Endpoint:** `GET /api/engagements/{engagement_id}/facts/`

**Purpose:** Return merged facts for an engagement (used by kernel and UI).

**Logic:**
```python
class ResolvedFactsView(APIView):
    def get(self, request, engagement_id):
        facts = resolve_engagement_facts(engagement_id)
        return Response(facts, status=200)
```

**Response:**
```json
{
  "api14": {
    "value": "42-000-12345-00-00",
    "units": "",
    "source_layer": "registry",
    "provenance": [],
    "confidence": null
  },
  "surface_shoe_ft": {
    "value": 1250,
    "units": "ft",
    "source_layer": "canonical",
    "provenance": [{"tenant_upload": "schematic.pdf"}],
    "confidence": 1.0
  }
}
```

**Use Cases:**
- Frontend displays facts with color-coding by source_layer
- Kernel receives resolved facts for plan generation
- Audit trail shows which facts were overridden

---

### 2. `CanonicalFactsViewSet` - CRUD for Canonical Facts

**Endpoints:**
- `GET /api/engagements/{engagement_id}/canonical-facts/` - List all
- `POST /api/engagements/{engagement_id}/canonical-facts/` - Create fact
- `PUT /api/engagements/{engagement_id}/canonical-facts/{fact_key}/` - Update
- `DELETE /api/engagements/{engagement_id}/canonical-facts/{fact_key}/` - Delete

**Example POST:**
```json
{
  "fact_key": "packer_ft",
  "value": 5000,
  "units": "ft",
  "confidence": 0.95,
  "provenance": [{"source": "wellbore_schematic_2024.pdf"}]
}
```

---

## Integration Points

### Provides To:
- **Kernel App** (`kernel/`) → Resolved facts for plan generation
- **Frontend UI** → Fact display with provenance

### Consumes From:
- **Public Core** (`public_core/`) → PublicFacts, WellRegistry
- **User Input** → Tenant corrections/additions

---

## Testing

**Run tests:**
```bash
docker exec regulagent_web python manage.py test apps.tenant_overlay.tests
```

**Coverage:**
- Fact resolution precedence (canonical > public > registry)
- Engagement isolation (tenant A can't see tenant B's canonical facts)
- Confidence scoring

---

## Key Concepts

### 1. **Three-Layer Fact Resolution**

**Priority Order:**
1. **Canonical** (tenant override) - Highest precedence
2. **Public** (regulator data) - Middle precedence
3. **Registry** (well identity) - Lowest precedence

**Rationale:**
- Operators know their wells better than public records
- Public data may be outdated (e.g., W-2 from 5 years ago)
- Registry provides baseline identity when nothing else available

---

### 2. **Engagement Isolation**

**Multi-Tenancy:**
- Each tenant gets their own WellEngagement
- Canonical facts are scoped to engagement, not global
- Tenant A's overrides don't affect Tenant B's view

**Example:**
- XTO engagement 1: surface_shoe_ft = 1250 (canonical)
- Shell engagement 2: surface_shoe_ft = 1200 (public, no override)
- Both work on same well, see different values

---

### 3. **Provenance Tracking**

**Audit Trail:**
Every fact includes provenance showing:
- Where it came from (source document, fragment ID)
- When it was created
- Who created it (tenant_id via engagement)

**Compliance:**
- Regulatory defense: "This value came from operator-provided schematic"
- Change tracking: "Surface shoe was updated from 1200 to 1250 on 2025-01-15"

---

### 4. **Confidence Scoring**

**Scale:** 0.00 (uncertain) to 1.00 (certain)

**Use Cases:**
- 1.00: Tenant measured value (e.g., tagged depth)
- 0.95: High confidence inference (e.g., calculated from known geometry)
- 0.70: Medium confidence (e.g., visual estimate from diagram)
- 0.50: Low confidence (e.g., interpolated from nearby wells)

**Future:**
- Kernel can warn when low-confidence facts affect plan
- UI shows visual indicator for confidence level

---

## File Structure

```
apps/tenant_overlay/
├── models/
│   ├── __init__.py
│   ├── well_engagement.py
│   └── canonical_facts.py
├── services/
│   └── facts_resolver.py       # Main resolution logic
├── views/
│   └── resolved_facts.py        # API endpoints
├── migrations/
│   ├── __init__.py
│   └── 0001_initial.py
└── __init__.py
```

---

## Example Usage

### Create Engagement & Add Canonical Facts

```python
from apps.public_core.models import WellRegistry
from apps.tenant_overlay.models import WellEngagement, CanonicalFacts

# 1. Get well
well = WellRegistry.objects.get(api14="42000012345678")

# 2. Create engagement
engagement = WellEngagement.objects.create(
    well=well,
    tenant_id="xto",
    mode="plugging"
)

# 3. Add canonical facts (overrides)
CanonicalFacts.objects.create(
    engagement=engagement,
    fact_key="surface_shoe_ft",
    value=1250,           # Override public value of 1200
    units="ft",
    confidence=1.00,
    provenance=[{"source": "operator_as_built_2024.pdf"}]
)

CanonicalFacts.objects.create(
    engagement=engagement,
    fact_key="packer_ft",
    value=5000,           # New fact not in public data
    units="ft",
    confidence=0.95,
    provenance=[{"source": "workover_report_2023.pdf"}]
)
```

---

### Resolve Facts & Generate Plan

```python
from apps.tenant_overlay.services.facts_resolver import resolve_engagement_facts
from apps.kernel.services.policy_kernel import plan_from_facts
from apps.policy.services.loader import get_effective_policy

# 1. Resolve facts (canonical + public + registry)
facts = resolve_engagement_facts(engagement.id)

# 2. Load policy
district = facts["district"]["value"]
county = facts["county"]["value"]
policy = get_effective_policy(district=district, county=county)

# 3. Generate plan
plan = plan_from_facts(facts, policy)

# 4. Check which facts were used
for step in plan["steps"]:
    if step.get("top_ft"):
        # Check if depth came from canonical or public
        shoe_fact = facts.get("surface_shoe_ft", {})
        print(f"Surface shoe: {shoe_fact['value']} ft (from {shoe_fact['source_layer']})")
```

---

### Query Facts by Source Layer

```python
# Show which facts are canonical overrides
canonical_keys = [k for k, v in facts.items() if v["source_layer"] == "canonical"]
print(f"Tenant overrides: {canonical_keys}")
# Output: ['surface_shoe_ft', 'packer_ft']

# Show public facts
public_keys = [k for k, v in facts.items() if v["source_layer"] == "public"]
print(f"Public regulator data: {public_keys}")
# Output: ['production_shoe_ft', 'uqw_base_ft', 'producing_formation']

# Show low-confidence facts
uncertain = {k: v for k, v in facts.items() 
             if v.get("confidence") and v["confidence"] < 0.80}
print(f"Uncertain facts: {uncertain.keys()}")
```

---

## Future Enhancements

1. **Fact History** - Track changes over time (CanonicalFacts versioning)
2. **Bulk Import** - Upload Excel/CSV with canonical facts
3. **Conflict Resolution UI** - Show canonical vs public side-by-side
4. **Approval Workflow** - Require manager approval for canonical overrides
5. **Auto-Confidence** - ML model to assign confidence scores
6. **Fact Propagation** - Copy canonical facts from engagement A to B

---

## Maintenance Notes

- **Add new fact types** by creating new fact_key conventions
- **Update provenance schema** when adding new source types
- **Tune confidence thresholds** based on field feedback
- **Archive old engagements** when status = "approved" and plan submitted

---

## Questions / Support

For questions about tenant_overlay:
1. Review fact resolution logic in facts_resolver.py
2. Check CanonicalFacts records in Django admin
3. Validate provenance tracking for audit compliance
4. Test multi-tenant isolation (ensure tenants can't see each other's data)

