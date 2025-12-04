# Complete Flow Walkthrough: W-3 From PNA with No Existing API Data

## Scenario
User calls: `POST /api/w3/build-from-pna/` with API `42-501-70575` that has **no prior W-3A data** in RegulAgent.

---

## 📊 Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  1️⃣  PNAEXCHANGE SENDS REQUEST                                             │
│  POST /api/w3/build-from-pna/                                               │
├─────────────────────────────────────────────────────────────────────────────┤
│  {                                                                            │
│    "api_number": "42-501-70575",      ← 10-digit format                     │
│    "subproject_id": 12345,                                                   │
│    "well_name": "Test Well",                                                 │
│    "w3a_reference": {...},                                                   │
│    "pna_events": [                                                           │
│      {"event_id": 4, "input_values": {...}, "date": "2025-01-15"},         │
│      {"event_id": 5, "input_values": {...}, "date": "2025-01-15"},         │
│      ...                                                                     │
│    ]                                                                         │
│  }                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│  2️⃣  REQUEST VALIDATION & PARSING (BuildW3FromPNAView.post)               │
├─────────────────────────────────────────────────────────────────────────────┤
│  • Parse request data                                                        │
│  • Handle wrapped/flat payloads                                             │
│  • Parse JSON strings if needed                                             │
│  • Validate w3a_reference structure                                         │
│  • Validate pna_events list                                                 │
│  ✅ Result: BuildW3FromPNARequestSerializer validates                      │
│     - api_number: "42-501-70575"                                            │
│     - subproject_id: 12345                                                  │
│     - pna_events: [event1, event2, ...]                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│  3️⃣  AUTO-GENERATE W-3A (NON-BLOCKING)                                     │
│  [This is the magic! API has no prior data]                                │
├─────────────────────────────────────────────────────────────────────────────┤
│  a) NORMALIZE API NUMBER                                                    │
│     • Input: "42-501-70575" (10-digit)                                      │
│     • Output: "4250170575" (8-digit)                                        │
│     • Method: normalize_api_number() from w3_utils.py                       │
│                                                                              │
│  b) CHECK FOR EXISTING W-3A DATA                                            │
│     ExtractedDocument.objects.filter(                                       │
│       api_number__contains="50170575",  ← Last 8 digits                     │
│       document_type="w2"                                                    │
│     ).exists()                                                              │
│                                                                              │
│     Result: False ❌ (no W-2 extraction found)                             │
│                                                                              │
│  c) TRIGGER FULL W-3A GENERATION                                            │
│     Call: generate_w3a_for_api(                                             │
│       api_number="4250170575",                                              │
│       plugs_mode="combined",           ← Default best practice              │
│       input_mode="extractions",        ← Use RRC public data only           │
│       merge_threshold_ft=500.0,                                             │
│       confirm_fact_updates=False,      ← Don't modify well registry         │
│       allow_precision_upgrades_only=True,                                   │
│       use_gau_override_if_invalid=False                                     │
│     )                                                                       │
│                                                                              │
│     Result: auto_w3a_result = {...}                                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│  4️⃣  W-3A ORCHESTRATION PROCESS (w3a_orchestrator.py)                      │
│  [Fully asynchronous pipeline]                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  STEP A: Acquire RRC Documents                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ • Query RRC website for: W-2, W-3A, W-15, GAU, Schematic             │  │
│  │ • Save to disk in secure folder                                       │  │
│  │ • Result: 5 files ready for extraction                               │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│           ↓                                                                   │
│  STEP B: Extract JSON from Documents (OpenAI)                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ • Convert PDFs to images                                              │  │
│  │ • Call OpenAI Vision API with regulatory document template           │  │
│  │ • Parse structured JSON for each document                            │  │
│  │ • Store in ExtractedDocument ORM (not yet persisted to plan)        │  │
│  │ • Result: 5 ExtractedDocument records created                       │  │
│  │   - W2 extraction: casing record, perforations, TVD, MD              │  │
│  │   - W3A extraction: existing W-3A form if available                 │  │
│  │   - W15 extraction: historic cement jobs                             │  │
│  │   - GAU extraction: deepest usable water                             │  │
│  │   - Schematic extraction: geometry, formations                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│           ↓                                                                   │
│  STEP C: Enrich WellRegistry (if needed)                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ • Get or create WellRegistry with API 4250170575                     │  │
│  │ • Merge extracted W-2 data (casing, TVD, MD)                         │  │
│  │ • Update well_name, county, operator (if available)                  │  │
│  │ • Result: WellRegistry enriched (conservative mode: only fill blanks)│  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│           ↓                                                                   │
│  STEP D: Build W-3A Plan (Policy Kernel)                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ • Load casing record from W-2                                        │  │
│  │ • Load cement jobs from W-15                                         │  │
│  │ • Load formations from schematic                                     │  │
│  │ • Apply policy kernel rules (regulatory logic)                       │  │
│  │ • Generate full W-3A plan structure                                  │  │
│  │ • Result: plan_data = {...}  (complete plan with all sections)     │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│           ↓                                                                   │
│  STEP E: Create PlanSnapshot (Immutable Record)                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ • Save plan_data to PlanSnapshot ORM                                 │  │
│  │ • Store as immutable JSON blob                                       │  │
│  │ • Store validation results and policy decisions                      │  │
│  │ • Result: snapshot_id = "uuid-4250170575"                            │  │
│  │           This is the versioned W-3A plan                            │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│           ↓                                                                   │
│  STEP F: Extract Well Geometry (For Diagrams)                               │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ From the newly created ExtractedDocuments:                           │  │
│  │                                                                       │  │
│  │ • CASING RECORD:                                                    │  │
│  │   - Surface: 13.375", 0-500ft                                        │  │
│  │   - Intermediate: 9.625", 500-2000ft                                │  │
│  │   - Production: 5.5", 2000-10000ft                                  │  │
│  │                                                                       │  │
│  │ • EXISTING TOOLS:                                                   │  │
│  │   - Existing CIBP at 3000ft                                         │  │
│  │   - Existing Packer at 2500ft                                       │  │
│  │   - DV Tool at 2200ft                                               │  │
│  │                                                                       │  │
│  │ • RETAINER TOOLS (from W-15):                                       │  │
│  │   - Float collar at 1000ft                                          │  │
│  │   - Pup joint at 1050ft                                             │  │
│  │   - Straddle packer at 900ft                                        │  │
│  │                                                                       │  │
│  │ • HISTORIC CEMENT JOBS (from W-15):                                 │  │
│  │   - Surface job: 0-500ft, 150 sacks, 15.8 ppg                       │  │
│  │   - Int'l job: 500-2000ft, 200 sacks, 14.8 ppg                      │  │
│  │                                                                       │  │
│  │ • KOP DATA (from schematic, if horizontal):                         │  │
│  │   - KOP MD: 5000ft                                                   │  │
│  │   - KOP TVD: 4000ft                                                  │  │
│  │                                                                       │  │
│  │ Result: well_geometry = {                                            │  │
│  │   "casing_record": [...],                                            │  │
│  │   "existing_tools": {...},                                           │  │
│  │   "retainer_tools": [...],                                           │  │
│  │   "historic_cement_jobs": [...],                                    │  │
│  │   "kop": {...}                                                       │  │
│  │ }                                                                    │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│           ↓                                                                   │
│  Return: auto_w3a_result = {                                                │
│    "success": True,                                                          │
│    "w3a_data": plan_data,           ← Full plan structure                   │
│    "w3a_well_geometry": well_geometry,  ← For plugged wellbore diagram      │
│    "snapshot_id": "uuid-4250170575",                                        │
│    "auto_generated": True,                                                  │
│    "extraction_count": 5,                                                   │
│    "well_enriched": True,                                                   │
│    "validation": {...}                                                      │
│  }                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│  5️⃣  W-3 FORM GENERATION (NON-BLOCKING)                                    │
│  [Runs in parallel/sequentially after W-3A, doesn't block if W-3A fails]  │
├─────────────────────────────────────────────────────────────────────────────┤
│  Call: build_w3_from_pna_payload(                                           │
│    pna_payload=validated_data,  ← pnaexchange events                       │
│    request=request                                                          │
│  )                                                                          │
│                                                                              │
│  Process:                                                                   │
│  a) Map pnaexchange events to W3Event dataclasses                          │
│     • Normalize event_type to standard enum                                 │
│     • Extract plug_number, depths, cement_class, sacks                      │
│     • Attach api_number to each event                                       │
│     Result: [W3Event(...), W3Event(...), ...]                              │
│                                                                              │
│  b) Apply casing engine logic                                              │
│     • Determine which casing is "active" at each plug depth                │
│     • Handle casing cuts/removals                                          │
│     • Calculate hole size from casing record                               │
│     Result: Each event knows its casing context                            │
│                                                                              │
│  c) Group events into plugs                                                │
│     • Set 1: Perforate + Squeeze + Tag TOC = 1 Plug row                  │
│     • Set 2: Spot cement = 1 Plug row                                      │
│     Result: [W3Plug(...), W3Plug(...), ...]                               │
│                                                                              │
│  d) Format for W-3 submission                                              │
│     • Calculate/validate TOC                                               │
│     • Convert to RRC export format                                         │
│     • Add casing record, perforations, DUQW                               │
│     Result: w3_form = {                                                    │
│       "header": {...},                                                     │
│       "plugs": [...],                                                      │
│       "casing_record": [...],                                              │
│       "perforations": [...],                                               │
│       "duqw": {...},                                                       │
│       "remarks": "Auto-generated from pnaexchange..."                      │
│     }                                                                       │
│                                                                              │
│  Return: result = {                                                         │
│    "success": true,                                                         │
│    "w3_form": w3_form,                                                      │
│    "validation": {...},                                                     │
│    "metadata": {...}                                                        │
│  }                                                                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│  6️⃣  ENRICH RESPONSE WITH WELL GEOMETRY                                    │
│  [Add W-3A data to W-3 response]                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│  if auto_w3a_result and auto_w3a_result["success"]:                        │
│    result["w3a_well_geometry"] = auto_w3a_result["w3a_well_geometry"]     │
│                                                                              │
│  Now result contains BOTH:                                                  │
│  • w3_form: RRC-compliant W-3 form ready to submit                         │
│  • w3a_well_geometry: Historical data for plugged wellbore diagram         │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│  7️⃣  VALIDATE RESPONSE STRUCTURE                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│  response_serializer = BuildW3FromPNAResponseSerializer(data=result)       │
│                                                                              │
│  Validates:                                                                 │
│  • success: boolean ✓                                                       │
│  • w3_form: complete W-3 structure ✓                                        │
│  • w3a_well_geometry: optional, structured if present ✓                    │
│  • validation: warnings and errors ✓                                        │
│  • metadata: api_number, events_processed, plugs_grouped ✓                 │
│                                                                              │
│  Result: ✅ All fields pass DRF validation                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│  8️⃣  RETURN SUCCESS RESPONSE TO PNAEXCHANGE                                │
│  HTTP 200 OK                                                                │
├─────────────────────────────────────────────────────────────────────────────┤
│  {                                                                           │
│    "success": true,                                                         │
│    "w3_form": {                                                             │
│      "header": {                                                            │
│        "api_number": "42-501-70575",                                        │
│        "well_name": "Test Well",                                            │
│        "operator": "...",                                                   │
│        "county": "...",                                                     │
│        ...                                                                  │
│      },                                                                     │
│      "plugs": [                                                             │
│        {                                                                    │
│          "plug_number": 1,                                                  │
│          "depth_top_ft": 200,                                               │
│          "depth_bottom_ft": 500,                                            │
│          "type": "cement_plug",                                             │
│          "cement_class": "H",                                               │
│          "sacks": 50,                                                       │
│          "top_of_plug_ft": 100                                              │
│        },                                                                   │
│        ...                                                                  │
│      ],                                                                     │
│      "casing_record": [                                                     │
│        {"string_type": "surface", "size_in": 13.375, ...},               │
│        {"string_type": "intermediate", "size_in": 9.625, ...},          │
│        {"string_type": "production", "size_in": 5.5, ...}                │
│      ],                                                                     │
│      "perforations": [...],                                                │
│      "duqw": {...},                                                        │
│      "remarks": "..."                                                      │
│    },                                                                       │
│    "w3a_well_geometry": {                                                   │
│      "casing_record": [...],                                               │
│      "existing_tools": {                                                   │
│        "existing_mechanical_barriers": ["CIBP", "PACKER"],               │
│        "existing_cibp_ft": 3000,                                           │
│        "existing_packer_ft": 2500,                                         │
│        "existing_dv_tool_ft": 2200                                         │
│      },                                                                     │
│      "retainer_tools": [                                                   │
│        {"tool_type": "float_collar", "depth_ft": 1000},                   │
│        {"tool_type": "pup_joint", "depth_ft": 1050},                      │
│        {"tool_type": "straddle_packer", "depth_ft": 900}                  │
│      ],                                                                     │
│      "historic_cement_jobs": [                                             │
│        {                                                                    │
│          "job_type": "surface",                                            │
│          "interval_top_ft": 0,                                             │
│          "interval_bottom_ft": 500,                                        │
│          "sacks": 150,                                                     │
│          "slurry_density_ppg": 15.8                                        │
│        },                                                                   │
│        ...                                                                  │
│      ],                                                                     │
│      "kop": {                                                               │
│        "kop_md_ft": 5000,                                                  │
│        "kop_tvd_ft": 4000                                                  │
│      }                                                                      │
│    },                                                                       │
│    "validation": {                                                          │
│      "warnings": ["Optional field X not found"],                           │
│      "errors": []                                                          │
│    },                                                                       │
│    "metadata": {                                                            │
│      "api_number": "42-501-70575",                                         │
│      "subproject_id": 12345,                                               │
│      "events_processed": 15,                                               │
│      "plugs_grouped": 8,                                                   │
│      "generated_at": "2025-01-15T10:30:00Z"                               │
│    }                                                                        │
│  }                                                                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔑 Key Design Decisions

### 1. **Non-Blocking Auto-Generation**
```python
# If W-3A generation FAILS, W-3 still returns successfully
try:
    auto_w3a_result = generate_w3a_for_api(...)
except Exception as e:
    logger.warning(f"W-3A generation failed: {e}")
    # CONTINUE - don't block W-3 generation
```

**Why?** 
- PNAExchange doesn't need W-3A data to succeed
- Well geometry is "informational" for the diagram
- W-3 form generation only needs pnaexchange events

### 2. **API Normalization**
```python
# Input: "42-501-70575" (10-digit)
# Output: "4250170575" (8-digit)
normalized_api = normalize_api_number(api_number)

# Used to check if data already exists:
ExtractedDocument.objects.filter(
    api_number__contains=normalized_api[-8:]  # Last 8 digits
)
```

**Why?**
- PNA sends 10-digit, RegulAgent stores 8-digit
- Ensures consistent lookups across both systems
- Attached to W3Event for future correlation

### 3. **Lazy W-3A Generation Check**
```python
w2_exists = ExtractedDocument.objects.filter(
    api_number__contains=normalized_api[-8:],
    document_type="w2"
).exists()

if not w2_exists:  # Only trigger if new
    auto_w3a_result = generate_w3a_for_api(...)
```

**Why?**
- Don't re-extract if already done
- W-2 extraction is the "marker" for completeness
- Efficient: one DB query instead of full orchestration

### 4. **Response Enrichment**
```python
# Build W-3 form first (always completes)
result = build_w3_from_pna_payload(pna_payload, request)

# Then optionally add well geometry (from W-3A)
if auto_w3a_result and auto_w3a_result["success"]:
    result["w3a_well_geometry"] = auto_w3a_result["w3a_well_geometry"]
```

**Why?**
- Separation of concerns
- W-3 form is independent
- Well geometry is bonus data
- If W-3A generation fails, pnaexchange still gets W-3

---

## 📈 Data Correlation for Multi-Platform

After this flow completes:

| System | Data | Link |
|--------|------|------|
| PNAExchange | W-3 events with api_number: 42-501-70575 | ✅ Stored in W3EventORM |
| RegulAgent | W-3A plan with snapshot_id: "uuid-xyz" | ✅ Stored in PlanSnapshot |
| WellRegistry | Well 4250170575 with enriched data | ✅ Updated from W-2 |
| ExtractedDocument | W-2, W-15, GAU, Schematic, Formations | ✅ All stored |

**Query Future**: "Give me all data for well 42-501-70575"
```python
# Find all W-3 events
W3EventORM.objects.filter(api_number__contains="50170575")

# Find all W-3 forms
W3FormORM.objects.filter(api_number__contains="50170575")

# Find extracted documents
ExtractedDocument.objects.filter(api_number__contains="50170575")

# Find well registry
WellRegistry.objects.get(api_number="4250170575")
```

---

## ⏱️ Timing & Performance

| Step | Time | Blocking? |
|------|------|-----------|
| Request parsing | ~50ms | ✅ Yes |
| W-3A check | ~10ms | ✅ Yes |
| **W-3A generation** | **~30-60s** | ❌ No (try/catch) |
| W-3 form generation | ~500ms | ✅ Yes |
| Response building | ~50ms | ✅ Yes |
| **Total if W-3A needed** | **~30-61s** | ⚠️ Slow but non-blocking |
| **Total if W-3A skipped** | **~0.6s** | ✅ Fast |

---

## 🚨 Error Scenarios

### Scenario A: W-3A Generation Fails (RRC site down)
```
✅ W-3 form still returns successfully
✅ PNA gets W-3 form data
⚠️ w3a_well_geometry is NULL/empty
📝 Log: "W-3A generation failed (non-fatal): timeout"
```

### Scenario B: W-3 Generation Fails (bad events)
```
❌ Returns 400 Bad Request
📝 Validation errors included
✅ Auto-W-3A generation attempted anyway (will succeed if data exists)
```

### Scenario C: W-3A Already Exists (redrill scenario)
```
⏭️ Skips W-3A generation (W-2 exists)
✅ W-3 form returns successfully
✅ w3a_well_geometry has existing data
⚡ Fast path: ~0.6s total
```

---

## 🎯 What PNAExchange Gets Back

**At minimum (always)**:
- ✅ w3_form: Complete W-3 ready for submission
- ✅ validation: Any warnings/errors
- ✅ metadata: What was processed

**Optionally (if W-3A generation succeeds)**:
- ✅ w3a_well_geometry: Casing, tools, cement, KOP for diagram
- ✅ snapshot_id: Reference to the W-3A plan created

**Never blocked by**:
- ❌ W-3A extraction failures
- ❌ RRC site unavailability
- ❌ OpenAI API errors (logged, continued)

---

## 🔮 Future ORM Integration

Once we wire up the ORM persistence (next todo):

```python
# Create ORM records while generating W-3
for event in pna_events:
    W3EventORM.objects.create(
        api_number=normalized_api,
        event_type=event.event_type,
        event_date=event.date,
        depths_top_ft=event.depth_top,
        cement_class=event.cement_class,
        sacks=event.sacks
    )

# Create plugs
for plug in plugs:
    W3PlugORM.objects.create(
        api_number=normalized_api,
        plug_number=plug.plug_number,
        depth_top_ft=plug.depth_top,
        ...
    )

# Create final form
W3FormORM.objects.create(
    api_number=normalized_api,
    status='draft',
    form_data=w3_form,
    well_geometry=w3a_well_geometry,
    auto_generated=bool(auto_w3a_result),
    generated_from_w3a_snapshot=auto_w3a_result.get('snapshot_id')
)
```

Then users can query historical W-3s:
```
GET /api/w3/forms/?api_number=4250170575
→ Returns all W-3 forms ever created for this well
→ Each with status, submission info, validation history
```


