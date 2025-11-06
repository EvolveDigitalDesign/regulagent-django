# Policy App - Policy Pack Management

## Purpose

The **policy** app manages regulatory policy packs (YAML files) that define Texas Railroad Commission (RRC) plugging requirements. It loads base policies, applies district/county/field overlays, validates completeness, and delivers executable policy specifications to the kernel. It enables RegulAgent to adapt to hyper-local regulatory variations (e.g., Andrews County enhanced recovery vs Scurry County formation tops) while maintaining a single codebase.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         POLICY APP                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  YAML Policy Packs (apps/policy/packs/tx/)                      │
│    ├─ tx_rrc_w3a_base_policy_pack.yaml    ← Base requirements  │
│    └─ w3a/district_overlays/                                     │
│        ├─ 08a__auto.yml                    ← District 08A      │
│        ├─ 08a__andrews.yml                 ← Andrews County    │
│        ├─ 7c__auto.yml                     ← District 7C       │
│        ├─ 7c__scurry.yml                   ← Scurry County     │
│        └─ texas_county_centroids.json      ← Geospatial lookup │
│                                                                   │
│  ┌──────────────────────────────────────┐                       │
│  │  loader.py                           │                       │
│  │  └─> get_effective_policy()          │  ← Main Entrypoint   │
│  │       • Load base pack               │                       │
│  │       • Apply district overlay       │                       │
│  │       • Apply county overlay         │                       │
│  │       • Apply field overlay (nearest)│                       │
│  │       • Validate completeness        │                       │
│  └──────────────────────────────────────┘                       │
│                                                                   │
│  ┌──────────────────────────────────────┐                       │
│  │  policy_applicator.py                │                       │
│  │  └─> PolicyApplicator.apply()        │  ← Convenience API   │
│  │       • Wrap facts                   │                       │
│  │       • Load policy                  │                       │
│  │       • Call kernel                  │                       │
│  └──────────────────────────────────────┘                       │
│                                                                   │
│  ┌──────────────────────────────────────┐                       │
│  │  district_overlay_builder.py         │                       │
│  │  └─> Extract from plugging books     │  ← Management tool   │
│  └──────────────────────────────────────┘                       │
│                                                                   │
│  OUTPUT: Effective Policy (merged requirements + overlays)      │
│                                                                   │
└─────────────────────────────────────────┘
```

---

## Data Flow

### Inputs (From)
- **YAML Policy Packs** (`apps/policy/packs/tx/`)
  - Base policy: TAC §3.14 requirements (citations, cement class, coverage)
  - District overlays: District-wide requirements (08A, 7C, etc.)
  - County overlays: County-specific rules (Andrews, Scurry, Coke, Reagan)
  - Field overlays: Field-specific formation tops and operational preferences
  
- **Geospatial Data** (`texas_county_centroids.json`)
  - County coordinates for nearest-neighbor field resolution

### Outputs (To)
- **Kernel App** (`apps/kernel/services/policy_kernel.py`)
  - Effective policy dictionary with:
    - `requirements`: Minimum plug lengths, coverage, tag wait hours
    - `preferences`: Geometry defaults, rounding policy, operational instructions
    - `district_overrides`: Formation tops, tagging rules, protect intervals
    - `cement_class`: Shallow vs deep cutoff and classes
    - `citations`: Regulatory references for each knob

- **PolicyApplicator** → Direct plan generation (convenience wrapper)

### Data Sources:
- **YAML files** loaded from filesystem
- **JSON centroids** for geographic calculations
- **Management commands** build overlays from PDF plugging books

---

## Key Services & Methods

### 1. `loader.py` - Policy Loading & Merging Engine

#### **`get_effective_policy(district, county, field, as_of, pack_rel_path)`**
**Purpose:** Load base policy and apply district/county/field overlays in hierarchical order. Return a complete, validated executable policy.

**Parameters:**
- `district`: RRC district code (e.g., "08A", "7C")
- `county`: County name (e.g., "Andrews", "Scurry")
- `field`: Field name (e.g., "Spraberry", "Wolfcamp")
- `as_of`: Effective date (datetime, for future versioning)
- `pack_rel_path`: Base pack filename (default: `tx_rrc_w3a_base_policy_pack.yaml`)

**Logic Flow:**

**Step 1: Load Base Policy**
1. Construct path: `packs/tx_rrc_w3a_base_policy_pack.yaml`
2. Call `_load_yaml()` to parse YAML
3. Extract base requirements (citations, requirements, cement_class)
4. Initialize `merged` dict with base

**Step 2: Apply District Overlay (if district provided)**
1. Check `policy.district_overlays[district]` in base pack
2. If exists, deep merge into `merged` via `_merge()`
3. Look for external district file: `packs/tx/w3a/district_overlays/{district}__auto.yml`
4. If found:
   - Load district-wide requirements, preferences, overrides
   - Deep merge into `merged`
5. Handle zero-padded variants (e.g., "8a" → "08a")

**Step 3: Apply County Overlay (if county provided)**
1. Normalize county name to file-safe: lowercase, spaces → underscores
2. Construct filename: `{district}__{county}.yml`
3. Look in `packs/tx/w3a/district_overlays/`
4. If found:
   - Extract county requirements, overrides, preferences, proposal, fields
   - Deep merge into `merged`
5. **Fallback:** If file not found, look in district `__auto.yml` under counties.{county}
6. Example: `7c__auto.yml` contains `counties.Coke` with formation tops

**Step 4: Apply Field Overlay (if field provided)** - Most Complex!
1. Normalize field name: lowercase, strip parentheticals, normalize dashes
2. **Strategy 1: Exact Field Match in Current County**
   - Look in county overlay under `fields.{field}`
   - Fuzzy match: contains check (bidirectional), skeleton match (letters/digits only)
   - If found → use this field config
   - Set provenance: `method="exact_in_county"`

3. **Strategy 2: Nearest County with Matching Field**
   - Load county centroids from `texas_county_centroids.json`
   - Calculate source county coordinates (lat/lon)
   - For each other county in district:
     - Check if county has matching field key under `fields`
     - Calculate Haversine distance (km)
     - Track nearest match
   - If found → use nearest county's field config
   - Set provenance: `method="nearest_county"`, `nearest_distance_km`

4. **Strategy 3: Nearest County Mentioning Field (anywhere)**
   - If no exact field key match, search for field occurrence:
     - Field key contains requested field name
     - Any formation name mentions field
   - Use `_mentions_field()` to recursively search config
   - Return nearest county where field is mentioned
   - Set provenance: `method="nearest_county_occurrence"`

5. **Merge Selected Field Config:**
   - Extract field requirements, preferences, overrides, proposal, steps_overrides
   - Deep merge into `merged`
   - Hoist field formation_tops into district_overrides (exclusive)

**Step 5: Validate Completeness**
1. Call `_validate_minimal(policy)`
2. Check base scope for required keys: citations, requirements, cement_class
3. Check requirements numerics: casing_shoe_coverage_ft, duqw_coverage_ft, tag_wait_hours
4. Check cement_class: cutoff_ft, shallow_class, deep_class
5. If district provided, validate effective (merged) scope
6. Collect missing keys into `incomplete_reasons`
7. Set `policy.complete = True` if no missing keys

**Step 6: Build Output Dictionary**
```python
{
    'policy_id': 'tx.w3a',
    'policy_version': '2025-Q1',
    'jurisdiction': 'TX',
    'form': 'W-3A',
    'effective_from': '2025-01-01',
    'as_of': '2025-01-15T00:00:00',
    'base': { ... },              # Unmodified base requirements
    'effective': { ... },         # Merged base + overlays
    'district': '08A',
    'county': 'Andrews',
    'field_resolution': {         # Field provenance tracking
        'requested_field': 'Spraberry',
        'matched_field': 'Spraberry',
        'matched_in_county': 'Andrews',
        'method': 'exact_in_county',
        'nearest_distance_km': None
    },
    'complete': True,
    'incomplete_reasons': []
}
```

---

#### **`_load_yaml(path)`**
**Purpose:** Load and parse YAML file safely.

**Logic:**
1. Open file with UTF-8 encoding
2. Call `yaml.safe_load()`
3. Return parsed dictionary (or empty dict if None)

---

#### **`_merge(a, b)`**
**Purpose:** Deep merge two dictionaries recursively (b overrides a).

**Logic:**
1. Start with copy of dict `a`
2. For each key in dict `b`:
   - If both values are dicts → recurse `_merge()`
   - Otherwise → b's value overwrites a's value
3. Return merged dictionary

**Example:**
```python
a = {'requirements': {'min_ft': 50}, 'citations': ['A']}
b = {'requirements': {'min_ft': 100}, 'other': 'value'}
result = _merge(a, b)
# Returns: {
#   'requirements': {'min_ft': 100},
#   'citations': ['A'],
#   'other': 'value'
# }
```

**Merge Priority:**
- Base < District < County < Field
- Last value wins for primitives
- Nested dicts merge recursively

---

#### **`_validate_minimal(policy)`**
**Purpose:** Check if policy has all required knobs for execution.

**Validation Rules:**

**Base Scope:**
- Must have: citations, requirements, cement_class
- requirements must have: casing_shoe_coverage_ft, duqw_coverage_ft, tag_wait_hours
- cement_class must have: cutoff_ft, shallow_class, deep_class

**Effective Scope (if district provided):**
- Same checks on merged effective policy
- Annotate missing keys with `[district:08A]` suffix

**Logic:**
1. Call `_check_scope('base', policy.base)`
2. If district: call `_check_scope('effective', policy.effective, annotate=district)`
3. Collect all missing keys
4. Set `policy.incomplete_reasons = missing`
5. Set `policy.complete = (len(missing) == 0)`

**Example Output:**
```python
{
    'complete': False,
    'incomplete_reasons': [
        'base.requirements.tag_wait_hours',
        'effective.cement_class.cutoff_ft [district:08A]'
    ]
}
```

---

#### **`_haversine_km(lat1, lon1, lat2, lon2)`**
**Purpose:** Calculate great-circle distance between two points on Earth.

**Formula (Haversine):**
```
a = sin²(Δlat/2) + cos(lat1) × cos(lat2) × sin²(Δlon/2)
c = 2 × atan2(√a, √(1-a))
distance = R × c    (R = 6371 km, Earth radius)
```

**Logic:**
1. Convert lat/lon to radians
2. Calculate differences
3. Apply Haversine formula
4. Return distance in kilometers

**Example:**
```python
# Andrews County: 32.3182°N, 102.6399°W
# Martin County: 32.3008°N, 101.9468°W
distance = _haversine_km(32.3182, -102.6399, 32.3008, -101.9468)
# Returns: ~64.5 km
```

**Use Case:**
- Find nearest county with matching field config
- Select "Spraberry" rules from Andrews when requested in Martin

---

#### **`_load_centroids()`**
**Purpose:** Load Texas county geographic centroids from JSON.

**Logic:**
1. Load `packs/tx/w3a/district_overlays/texas_county_centroids.json`
2. Parse JSON array of `[{county, latitude, longitude}, ...]`
3. Normalize county names:
   - Lowercase, collapse whitespace
   - Store both "andrews" and "andrews county"
4. Return dict: `{"andrews": (32.3182, -102.6399), ...}`

**Data Format:**
```json
[
  {"county": "Andrews", "latitude": 32.3182, "longitude": -102.6399},
  {"county": "Scurry", "latitude": 32.7445, "longitude": -101.0577}
]
```

---

#### **`_mentions_field(config, term_norm)`**
**Purpose:** Recursively search if a field/formation name is mentioned anywhere in a county config.

**Logic:**
1. If config is dict:
   - Check each key name for term match (contains either way)
   - If key is "formation", check value for term match
   - Recurse into all values
2. If config is list:
   - Recurse into each item
3. Return True if any match found

**Example:**
```python
config = {
    'fields': {
        'Spraberry Deep': {...}
    },
    'formation_tops': [
        {'formation': 'Spraberry', 'top_ft': 7000}
    ]
}

result = _mentions_field(config, 'spraberry')
# Returns: True (found in both field key and formation name)
```

---

#### **`_normalize_field_name(name)`**
**Purpose:** Normalize field names for fuzzy matching.

**Transformations:**
1. Lowercase
2. Remove parentheticals: "(Trend)" → ""
3. Normalize unicode dashes to ASCII hyphen-minus
4. Collapse spaces around hyphens: "Wolfcamp - A" → "wolfcamp-a"
5. Collapse whitespace: "  Spraberry   Deep" → "spraberry deep"

**Example:**
```python
_normalize_field_name("Wolfcamp (Shallow Trend)")
# Returns: "wolfcamp"

_normalize_field_name("Spraberry – Deep")  # em-dash
# Returns: "spraberry-deep"
```

---

#### **`_normalize_county_key(name)`**
**Purpose:** Return both county name variants for lookup.

**Returns:**
```python
("andrews", "andrews county")  # base, with_suffix
```

**Logic:**
1. Lowercase, collapse whitespace
2. Strip trailing " county"
3. Return tuple: (base, with_suffix)

**Use Case:**
- Lookup in centroids dict (which has multiple aliases per county)

---

### 2. `policy_applicator.py` - Convenience Wrapper

#### **`PolicyApplicator` Class**
**Purpose:** High-level API for applying policy to facts and generating plans.

**Methods:**

##### **`__init__(pack_rel_path)`**
Initialize with base pack path (default: tx_rrc_w3a_base_policy_pack.yaml)

---

##### **`load_policy(district, county)`**
**Purpose:** Load effective policy for district/county.

**Logic:**
1. Call `get_effective_policy()`
2. Set default policy_id = "tx.w3a"
3. Return policy dict

---

##### **`apply(facts, district, county)`**
**Purpose:** Apply policy to facts and generate compliant plan.

**Logic:**
1. Load policy via `load_policy()`
2. Mark policy as complete (force step generation)
3. Set default rounding_policy = "nearest"
4. Wrap facts via `_wrap_facts()` (simple values → {"value": x} format)
5. Call `plan_from_facts(wrapped, policy)` from kernel
6. Return plan dictionary

**Example:**
```python
applicator = PolicyApplicator()
facts = {
    'api14': '42000012345678',
    'district': '08A',
    'county': 'Andrews',
    'surface_shoe_ft': 1200,
    'has_uqw': True,
    'uqw_base_ft': 800
}
plan = applicator.apply(facts, district='08A', county='Andrews')
# Returns: Complete plan with steps and materials
```

---

##### **`from_extractions(api)`**
**Purpose:** Build facts from ExtractedDocument records and generate plan.

**Logic:**
1. Query `ExtractedDocument.objects.filter(api_number=api)`
2. Fetch latest W2, GAU, schematic documents
3. Extract facts:
   - **From W2.well_info:** api14, county, field, lease, well_no, district
   - **From GAU/W2.surface_casing_determination:** uqw_base_ft
   - **From W2.casing_record:** surface_shoe_ft, production_shoe_ft, casing IDs
   - **Infer district:** "08A" if district="08" and county="andrews"
4. Build facts dictionary
5. Call `apply(facts, district, county)`
6. Return plan

**Use Case:**
- API endpoint that accepts API14 and returns plan
- Batch processing of extracted documents

---

##### **`_wrap_facts(facts)`**
**Purpose:** Convert simple fact values to kernel-expected format.

**Transformation:**
```python
# Before
{'api14': '42001234567890', 'district': '08A'}

# After
{'api14': {'value': '42001234567890'}, 'district': {'value': '08A'}}
```

**Only wraps specific keys:** api14, state, district, county, field, lease, well_no, has_uqw, uqw_base_ft, surface_shoe_ft, use_cibp

---

### 3. `district_overlay_builder.py` - Management Tool

#### **Purpose**
Extract requirements from RRC district plugging books (PDFs) and generate YAML overlay files.

#### **Key Functions**

##### **`extract_08a_requirements()`**
**Purpose:** Parse District 08/8A plugging book PDF for Andrews County requirements.

**Extracts:**
- Enhanced recovery zones (Grayburg-San Andres)
- Protect intervals (water-bearing layers)
- Tagging requirements (surface shoe in open hole)
- Formation tops requiring isolation plugs

**Output:** `08a__andrews.yml` with district_overrides

---

##### **`extract_7c_requirements()`**
**Purpose:** Parse District 7C plugging book for formation-specific requirements.

**Extracts:**
- Formation tops per county (Scurry, Coke, Reagan)
- Operational preferences (mud weight, funnel time, notice hours)
- Tagging requirements
- Pump path restrictions (tubing/drillpipe only)

**Output:** `7c__auto.yml` with counties.{name} nested configs

---

### 4. `validate_overlays.py` - Quality Assurance

#### **`validate_all_overlays()`**
**Purpose:** Load all overlay files and check for:
- Valid YAML syntax
- Required keys present
- No orphan references
- Consistent units and data types

**Run via:**
```bash
docker exec regulagent_web python manage.py validate_overlays
```

---

## Management Commands

### `build_district_overlays`
**Purpose:** Run district overlay builder to extract from plugging books.

**Usage:**
```bash
docker exec regulagent_web python manage.py build_district_overlays --district 08A
```

**Output:** YAML files in `packs/tx/w3a/district_overlays/`

---

### `policy_apply`
**Purpose:** Test policy application against sample facts.

**Usage:**
```bash
docker exec regulagent_web python manage.py policy_apply \
  --district 08A \
  --county Andrews \
  --field "Spraberry" \
  --api 42000012345678
```

**Output:** JSON plan printed to stdout

---

### `validate_overlays`
**Purpose:** Validate all YAML policy files for syntax and completeness.

**Usage:**
```bash
docker exec regulagent_web python manage.py validate_overlays
```

---

## YAML Policy Pack Structure

### Base Pack: `tx_rrc_w3a_base_policy_pack.yaml`

```yaml
policy_id: tx.w3a
policy_version: 2025-Q1
jurisdiction: TX
form: W-3A
effective_from: 2025-01-01

base:
  citations:
    - tx.tac.16.3.14
  
  requirements:
    surface_casing_shoe_plug_min_ft:
      value: 100
      citation_keys: [tx.tac.16.3.14(e)(2)]
    
    casing_shoe_coverage_ft:
      value: 100
      citation_keys: [tx.tac.16.3.14(e)(2)]
    
    cement_above_cibp_min_ft:
      value: 20
      citation_keys: [tx.tac.16.3.14(g)(3)]
    
    uqw_isolation_min_len_ft:
      value: 100
      citation_keys: [tx.tac.16.3.14(g)(1)]
    
    duqw_coverage_ft: 200
    tag_wait_hours: 4
    top_plug_length_ft: 10
    casing_cut_below_surface_ft: 3
  
  cement_class:
    cutoff_ft: 4000
    shallow_class: A
    deep_class: H
  
  preferences:
    rounding_policy: nearest
    safety_stock_sacks: 5
    
    geometry_defaults:
      cement_plug:
        casing_id_in: 4.778   # 5-1/2" casing
        stinger_od_in: 2.875  # 2-7/8" tubing
        annular_excess: 0.4
    
    default_recipe:
      id: class_h_neat_15_8
      class: H
      density_ppg: 15.8
      yield_ft3_per_sk: 1.18
      water_gal_per_sk: 5.2
      additives: []

district_overlays:
  "08A": {}  # Stub, actual overlay in external file
  "7C": {}
```

---

### District Overlay: `08a__auto.yml`

```yaml
requirements:
  casing_shoe_coverage_ft:
    value: 150           # District 08A requires more coverage
    citation_keys: [rrc.district.08a:shoe_coverage]

preferences:
  operational:
    notice_hours_min: 12
    mud_min_weight_ppg: 9.0

overrides:
  tag:
    surface_shoe_in_oh: true    # Require tagging in Andrews
```

---

### County Overlay: `08a__andrews.yml`

```yaml
requirements:
  surface_casing_shoe_plug_min_ft:
    value: 200           # Andrews requires 200 ft, not 100
    citation_keys: [rrc.district.08a.andrews:shoe_plug]

overrides:
  enhanced_recovery_zone:
    formation: Grayburg-San Andres
    depth_range_ft: [3000, 4500]
    requires_tagging: true
  
  protect_intervals:
    - name: Santa Rosa
      top_ft: 800
      bottom_ft: 900
      requires_plug: true
  
  tag:
    surface_shoe_in_oh: true

fields:
  Spraberry:
    formation_tops:
      - formation: Dean
        top_ft: 8450
        plug_required: true
      
      - formation: Spraberry
        top_ft: 7200
        plug_required: true
        tag_required: true
```

---

### Combined District: `7c__auto.yml`

```yaml
requirements:
  tag_wait_hours: 8      # District 7C requires longer wait
  pump_through_tubing_or_drillpipe_only:
    value: true
    citation_keys: [rrc.district.7c:pump_path]

preferences:
  operational:
    notice_hours_min: 24
    mud_min_weight_ppg: 10.0
    funnel_min_s: 35

counties:
  Scurry:
    fields:
      Canyon:
        formation_tops:
          - formation: San Andres
            top_ft: 3200
            plug_required: true
            tag_required: true
          
          - formation: Coleman Junction
            top_ft: 6100
            plug_required: true
            tag_required: true
  
  Coke:
    formation_tops:
      - formation: Odom
        top_ft: 3250
        plug_required: true
```

---

## Integration Points

### Provides To:
- **`apps/kernel/services/policy_kernel.py`** → Effective policy for plan generation
- **`apps/kernel/views/advisory.py`** → Policy for sanity checks
- **PolicyApplicator** → Direct kernel invocation

### Consumes From:
- **YAML files** on filesystem (read-only)
- **JSON centroids** for geospatial calculations
- **Management commands** for overlay building

---

## Testing

### Test Scenarios:
- Base policy loading
- District overlay merging (08A, 7C)
- County overlay merging (Andrews, Scurry, Coke)
- Field resolution (exact, nearest county, fuzzy match)
- Policy validation (complete vs incomplete)
- Haversine distance calculations

**Run tests:**
```bash
docker exec regulagent_web python manage.py test apps.policy.tests
```

---

## Key Concepts

### 1. **Hierarchical Merging**
Base → District → County → Field
- Later values override earlier
- Nested dicts merge recursively
- Lists replace (no append)

### 2. **Field Resolution Strategies**
1. Exact match in current county
2. Nearest county with matching field key
3. Nearest county mentioning field anywhere
4. Uses Haversine distance for "nearest"

### 3. **Policy Completeness**
- Kernel refuses to execute incomplete policies
- Returns constraints listing missing knobs
- Ensures no assumptions/heuristics

### 4. **Provenance Tracking**
- field_resolution object records how field was matched
- Audit trail for regulatory compliance
- Transparency for operators

---

## File Structure

```
apps/policy/
├── services/
│   ├── __init__.py
│   ├── loader.py                      # Main loading + merging logic
│   ├── policy_applicator.py           # Convenience wrapper
│   ├── district_overlay_builder.py    # Extract from PDFs
│   └── validate_overlays.py           # QA checks
├── packs/
│   ├── schema.json                    # YAML schema definition
│   ├── tx_rrc_w3a_base_policy_pack.yaml
│   └── tx/
│       └── w3a/
│           └── district_overlays/
│               ├── 08a__auto.yml
│               ├── 08a__andrews.yml
│               ├── 7c__auto.yml
│               ├── 7c__scurry.yml
│               └── texas_county_centroids.json
└── management/
    └── commands/
        ├── build_district_overlays.py
        ├── policy_apply.py
        └── validate_overlays.py
```

---

## Example Usage

### Load Effective Policy

```python
from apps.policy.services.loader import get_effective_policy

# Load policy for Andrews County, Spraberry field
policy = get_effective_policy(
    district='08A',
    county='Andrews',
    field='Spraberry'
)

# Check completeness
if not policy['complete']:
    print("Missing knobs:", policy['incomplete_reasons'])
else:
    # Access merged requirements
    shoe_min = policy['effective']['requirements']['surface_casing_shoe_plug_min_ft']['value']
    # 200 ft (Andrews override, not 100 ft base)
    
    # Access field-specific formation tops
    formations = policy['effective']['district_overrides']['formation_tops']
    # [{'formation': 'Dean', 'top_ft': 8450}, ...]
    
    # Check field provenance
    print(policy['field_resolution'])
    # {'requested_field': 'Spraberry',
    #  'matched_field': 'Spraberry',
    #  'matched_in_county': 'Andrews',
    #  'method': 'exact_in_county'}
```

---

### Generate Plan via Applicator

```python
from apps.policy.services.policy_applicator import PolicyApplicator

applicator = PolicyApplicator()

facts = {
    'api14': '42000012345678',
    'state': 'TX',
    'district': '08A',
    'county': 'Andrews',
    'field': 'Spraberry',
    'surface_shoe_ft': 1200,
    'has_uqw': True,
    'uqw_base_ft': 800,
    'use_cibp': True
}

plan = applicator.apply(facts, district='08A', county='Andrews')

print(f"Steps: {len(plan['steps'])}")
for step in plan['steps']:
    print(f"{step['type']}: {step.get('regulatory_basis')}")
```

---

## Future Enhancements

1. **Versioned Policies** - Track effective dates, allow historical plan generation
2. **Policy Diff Tool** - Compare two versions to see what changed
3. **Visual Policy Editor** - UI for editing YAML overlays
4. **Multi-State Support** - New Mexico, Oklahoma regulatory packs
5. **Auto-Update** - Scrape RRC website for plugging book updates
6. **Field Library** - Pre-built configs for top 100 TX fields

---

## Maintenance Notes

- **Add new district:** Create `{district}__auto.yml` with district-wide requirements
- **Add new county:** Create `{district}__{county}.yml` with county-specific overrides
- **Add new field:** Add under `fields.{field_name}` in county overlay
- **Update TAC rules:** Modify base pack requirements and citation keys
- **Add centroids:** Update `texas_county_centroids.json` with new counties

---

## Questions / Support

For questions about policy management:
1. Review YAML examples in `packs/tx/w3a/district_overlays/`
2. Check loader.py for merge precedence rules
3. Validate overlays with management command
4. Consult RRC plugging books for district-specific requirements

