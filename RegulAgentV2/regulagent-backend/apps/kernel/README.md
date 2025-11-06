# Kernel App - Policy Kernel & Compliance Engine

## Purpose

The **kernel** app is the core business logic engine of RegulAgent. It generates deterministic, regulator-compliant well plugging plans by applying Texas Railroad Commission (RRC) policy rules to well facts. It ensures every plugging operation meets regulatory requirements with full citation traceability.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         KERNEL APP                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  INPUT:  Resolved Facts + Effective Policy                      │
│           ↓                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │  policy_kernel.py                    │                       │
│  │  └─> plan_from_facts()               │  ← Main Entrypoint   │
│  └──────────────────────────────────────┘                       │
│           ↓                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │  w3a_rules.py                        │                       │
│  │  └─> generate_steps()                │  ← W3A Form Logic    │
│  └──────────────────────────────────────┘                       │
│           ↓                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │  Step Enrichment Pipeline            │                       │
│  │   • Apply defaults                   │                       │
│  │   • District overrides               │                       │
│  │   • Mechanical awareness             │                       │
│  │   • CIBP detection                   │                       │
│  │   • Formation plugs                  │                       │
│  │   • Merge adjacent plugs             │                       │
│  │   • Compute materials                │                       │
│  └──────────────────────────────────────┘                       │
│           ↓                                                       │
│  OUTPUT: Compliant Plugging Plan with Steps & Materials         │
│                                                                   │
│  VIEWS:                                                          │
│   • advisory.py     - Sanity checks & advisory findings        │
│   • plan_preview.py - Plan generation for UI preview           │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### Inputs (From)
- **Resolved Facts** (`apps/tenant_overlay/services/facts_resolver.py`)
  - Well identity: API14, state, district, county, lat/lon
  - Well depths: surface shoe, production shoe, UQW base, formation tops
  - Producing interval, casing geometry, existing barriers
  - Mechanical conditions: CIBP, packer, DV tool locations
  
- **Effective Policy** (`apps/policy/services/loader.py`)
  - Base policy requirements from YAML packs
  - District and county overlays
  - Field-specific overrides
  - Cement class, geometry defaults, operational preferences

### Outputs (To)
- **Compliant Plugging Plan** (JSON)
  - Step-by-step plugging operations with depths
  - Material requirements (cement sacks, water, additives)
  - Regulatory citations for each step
  - Violations and constraints
  - Operational instructions

- **Views/API Endpoints**
  - `advisory.py` → `AdvisorySanityCheckView` for UI
  - Plan snapshots stored in `public_core.PlanSnapshot`

---

## Key Services & Methods

### 1. `policy_kernel.py` - Main Kernel Entrypoint

#### **`plan_from_facts(resolved_facts, policy)`**
**Purpose:** Main deterministic kernel entrypoint that generates compliant plugging plans.

**Logic Flow:**
1. **Extract district** from facts (handles both dict and string formats)
2. **Check policy completeness** - if incomplete, return constraints and exit early
3. **Initialize plan structure** with metadata (version, jurisdiction, form, district)
4. **For complete W3A policies:**
   - Call `generate_w3a_steps()` to get base steps
   - Apply **mechanical awareness** filters:
     - Suppress perf/circulate operations when CIBP exists
     - Ensure cap exists above existing CIBP
     - Add isolation plugs around PACKER and DV_TOOL
   - Run **CIBP detector** logic:
     - Check if producing interval is exposed below production shoe
     - If exposed and no existing CIBP, emit bridge plug + cap
     - Calculate cap length from policy knobs
   - **De-duplicate citations** to avoid repeated regulatory references
5. **Apply step defaults** from policy preferences (geometry, recipe)
6. **Apply district overrides** (tagging requirements, operational instructions)
7. **Apply explicit step overrides** (cap length, squeeze intervals)
8. **Suppress subsumed plugs** - remove formation/cement plugs fully covered by perf_circulate
9. **Annotate cement class** (shallow vs deep based on policy cutoff depth)
10. **Inject tagging/verification details** (TAG requirements, wait hours)
11. **Generate plan-level notes** (existing conditions, operations summary)
12. **Merge adjacent plugs** (optional, when enabled via long_plug_merge preference)
13. **Compute materials** for all steps using material engine
14. **Apply rounding policy** and safety stock

**Returns:** Complete plan dictionary with steps, materials, citations, violations

---

#### **`_collect_constraints(policy)`**
**Purpose:** Check if policy is complete and collect missing knobs.

**Logic:** 
- If `policy.complete` is False, creates constraint error with list of missing required knobs
- Returns list of constraint dictionaries

---

#### **`_compute_materials_for_steps(steps)`**
**Purpose:** Calculate cement volumes, sacks, water, and additives for each step.

**Logic Flow (for each step type):**

- **balanced_plug:** 
  - Calculate annular capacity between hole/casing and stinger
  - Calculate inside capacity of stinger
  - Add annular excess percentage
  - Compute total barrels, convert to sacks via recipe
  - Calculate displacement fluid volume

- **bridge_plug_cap / cibp_cap:**
  - Calculate annular capacity in casing vs stinger
  - Multiply by cap length and excess
  - Convert to sacks

- **cement_plug:**
  - Supports segmented geometry (piecewise calculations)
  - For open-hole: uses hole diameter vs stinger
  - For cased-hole: uses casing ID vs stinger
  - Applies annular excess based on context
  - Sums volumes across all segments

- **surface_casing_shoe_plug / uqw_isolation_plug / formation_top_plug:**
  - Uses cased-hole calculation (casing ID vs stinger)
  - Default annular excess: 0.4 (40%)
  - **Applies TAC §3.14(d)(11) factor:** +10% per 1000 ft of plug length
  - Example: 2500 ft plug → 3 kft units → 1.3x multiplier

- **squeeze:**
  - Base annular volume multiplied by squeeze_factor (typically 1.5x)
  - Optional spacer/preflush calculation based on contact time and pump rate

**Returns:** Steps array with populated `materials` object containing slurry and fluids

---

#### **`_merge_adjacent_plugs(steps, types, threshold_ft, preserve_tagging)`**
**Purpose:** Combine nearby formation plugs into longer single plugs to reduce wait cycles.

**Logic:**
1. **Partition steps** into mergeable (formation plugs with top/bottom) vs fixed (all others)
2. **Sort mergeable by depth** (deepest first)
3. **Group adjacent plugs** where gap ≤ threshold_ft:
   - Scan ordered list with sliding window
   - Calculate separation between consecutive plugs
   - If separation ≤ threshold, add to current group
   - Otherwise flush group and start new
4. **Merge each group:**
   - New top_ft = max of all tops (shallowest)
   - New bottom_ft = min of all bottoms (deepest)
   - Merge regulatory citations from all members
   - Preserve tag_required if any member needed tagging
   - Record merged sources in details for audit trail
5. **Return combined list** of fixed + merged steps

**Example:** Three 100 ft plugs at 5000, 5150, 5300 ft with threshold=200 → One 400 ft plug from 4900-5300 ft

---

#### **`_apply_step_defaults(steps, preferences)`**
**Purpose:** Attach geometry defaults and recipe to steps that lack them.

**Logic:**
1. **For each step**, lookup geometry defaults by step type
2. **Context-aware filtering:**
   - Open-hole steps: only allow stinger_od_in, stinger_id_in, annular_excess
   - Cased-hole steps: allow casing_id_in, stinger_od_in, annular_excess
3. **Recipe attachment:**
   - Use preferences.default_recipe if available
   - Otherwise use deterministic fallback: Class H, 15.8 ppg, 1.18 ft³/sk
   - Emit MISSING_RECIPE finding when fallback used
4. **Propagate rounding preference** onto step.recipe if missing

---

#### **`_apply_district_overrides(steps, policy_effective, preferences, district, county)`**
**Purpose:** Apply district-specific requirements (tagging, formation plugs, operational instructions).

**Logic:**

**District 08/08A - Tagging:**
- If `district_overrides.tag.surface_shoe_in_oh = true`, require TAG on surface shoe plug
- If protect_intervals or enhanced_recovery_zone exists, require tagging
- Add regulatory basis citations with district + county identifier

**District 7C - Operational Instructions:**
- Attach pump_through_tubing_or_drillpipe_only requirement
- Add notice_hours_min, mud_min_weight_ppg, funnel_min_s instructions
- Inject as `special_instructions` on relevant steps
- Require TAG on UQW and surface plugs when tagging_required_hint = true

**Formation-Top Plugs:**
- Loop through `district_overrides.formation_tops` array
- For each formation where plug_required = true:
  - Create formation_top_plug step centered at formation top
  - Use symmetric interval: top_ft = center + half, bottom_ft = center - half
  - Attach geometry from preferences.geometry_defaults
  - Require TAG on San Andres and Coleman Junction formations

**Returns:** Enriched steps array

---

#### **`_apply_steps_overrides(steps, policy_effective, preferences)`**
**Purpose:** Allow explicit step parameter overrides from policy (e.g., cap length, squeeze intervals).

**Logic:**

**CIBP Cap Override:**
- If `steps_overrides.cibp_cap.cap_length_ft` provided, update all cibp_cap steps with new length

**Squeeze Via Perf:**
- If `steps_overrides.squeeze_via_perf.interval_ft` = [top, bottom], create new squeeze step
- Use geometry from preferences.geometry_defaults.squeeze
- Apply squeeze_factor (typically 1.5x)
- Honor sacks_override if extraction provided explicit count

**Perf Circulate:**
- Loop `steps_overrides.perf_circulate` array and create perf_circulate steps

**Cement Plugs:**
- Loop `steps_overrides.cement_plugs` array
- Create cement_plug steps with:
  - Respect geometry_context (open_hole vs cased)
  - Attach hole_d_in for open-hole
  - Attach casing_id_in for cased-hole only
  - Support segmented geometry via segments array

---

#### **`_infer_annular_excess(step)`**
**Purpose:** Determine appropriate excess percentage based on geometry and depth.

**Logic:**
- Check step.annular_excess first (explicit wins)
- **Open-hole context:** 1.0 (100% excess)
- **Cased-hole, interval ≥200 ft:** 1.0 (100% excess for long plugs)
- **Cased-hole, interval <200 ft:** 0.5 (50% excess for short plugs)
- **Fallback:** 0.5

**Rationale:** Open-hole has more uncertainty; long plugs need more safety margin

---

### 2. `w3a_rules.py` - W3A Form Step Generation

#### **`generate_steps(facts, policy_effective)`**
**Purpose:** Generate base plugging steps per Texas TAC §3.14 requirements for Form W-3A.

**Logic Flow:**

1. **Surface Casing Shoe Plug** (§3.14(e)(2)):
   - Require `surface_casing_shoe_plug_min_ft` knob from policy
   - If missing → emit violation
   - If surface_shoe_ft fact available:
     - Center plug at shoe depth ± half of min_length
     - Example: shoe @ 1200 ft, min 100 ft → plug 1150-1250 ft
   - If surface_shoe_ft missing → emit SURFACE_SHOE_DEPTH_UNKNOWN violation
   - Check against casing_shoe_coverage_ft if specified
   - Emit INSUFFICIENT_SHOE_COVERAGE violation if below threshold

2. **CIBP Cap** (§3.14(g)(3)):
   - Require cement_above_cibp_min_ft (typically ≥20 ft)
   - Check if CIBP is present or being used (use_cibp or cibp_present facts)
   - If existing cap present and long enough → skip
   - Otherwise calculate remaining footage needed and emit cibp_cap step

3. **UQW Isolation Plug** (§3.14(g)(1)):
   - Check has_uqw fact
   - Require uqw_isolation_min_len_ft, uqw_below_base_ft, uqw_above_base_ft
   - Fallback defaults: 100 ft total, 50 ft below, 50 ft above
   - If uqw_base_ft fact available:
     - top_ft = base + above_ft
     - bottom_ft = base - below_ft
   - Emit uqw_isolation_plug step with citations

4. **DUQW Check:**
   - If duqw_isolation_required and has_duqw but no UQW step planned
   - Emit DUQW_ISOLATION_MISSING violation

5. **GAU Protect Intervals:**
   - Loop gau_protect_intervals fact array
   - For each interval: emit cement_plug with cased_production context
   - Citation: tx.gau.protect_interval

6. **Proposal Generation** (overlay-driven):
   - Requires producing_interval_ft fact + proposal knobs
   - proposal.plug_count: number of plugs to space
   - proposal.segment_length_ft: length of each plug
   - proposal.spacing_ft: gap between plugs
   - **Algorithm:**
     - Start from top of producing interval
     - Place plug #1: top = current_top, bottom = top - segment_length
     - Advance: current_top -= spacing
     - Repeat until plug_count reached or hit bottom

7. **Top Plug & Casing Cut** (§3.14(d)(8)):
   - Require top_plug_length_ft (typically 10 ft)
   - Emit top_plug step from 0-10 ft
   - Require casing_cut_below_surface_ft (typically 3 ft)
   - Emit cut_casing_below_surface step

8. **Intermediate Casing Shoe Plug** (§3.14(f)(1)):
   - If intermediate_shoe_ft fact provided
   - Emit intermediate_casing_shoe_plug centered at shoe ±50 ft

9. **Productive Horizon Isolation** (§3.14(k)):
   - If producing_interval_ft = [from, to]
   - Find deepest depth = max(from, to)
   - Emit productive_horizon_isolation_plug centered ±50 ft

10. **Operational Instructions:**
    - Inject preferences.operational.mud_min_weight_ppg
    - Inject preferences.operational.funnel_min_s
    - Attach to relevant steps as special_instructions

11. **Formation Plug Deduplication:**
    - Scan all formation_top_plug steps
    - Prefer county-specific regulatory basis over district-wide
    - Remove duplicates by formation name

**Returns:** Dictionary with `steps` array and `violations` array

---

### 3. `violations.py` - Violation Codes & Builder

#### **`make_violation(code, severity, message, context, citations, autofix_hint)`**
**Purpose:** Create structured violation dictionary for reporting.

**Fields:**
- `code`: String constant from VCodes class
- `severity`: "critical" | "major" | "minor"
- `message`: Human-readable description
- `context`: Additional data for debugging
- `citations`: Regulatory references
- `autofix_hint`: Suggested resolution (future use)

**Example:**
```python
make_violation(
    VCodes.INSUFFICIENT_SHOE_COVERAGE,
    MAJOR,
    "Surface shoe plug 50ft is below required coverage 100ft",
    citations=["tx.tac.16.3.14(e)(2)"],
    context={"min_length_ft": 50, "required_ft": 100}
)
```

---

### 4. `policy_registry.py` - Policy Handler Registry

#### **`get_policy_handler(policy_id)`**
**Purpose:** Return the appropriate planning function for a policy ID.

**Logic:**
- Lookup policy_id in _REGISTRY dictionary
- Currently maps "tx.w3a" → `plan_from_facts`
- Falls back to `plan_from_facts` for unknown policies

#### **`register_policy_handler(policy_id, handler)`**
**Purpose:** Allow dynamic registration of new policy handlers.

**Use Case:** Future support for other forms (W-2, W-15, other states)

---

## Views (API Endpoints)

### 1. `advisory.py`

#### **`AdvisorySanityCheckView`**
**Endpoint:** `POST /api/kernel/advisory`

**Purpose:** Compare generated plan against policy plugging charts for sanity checks.

**Logic:**
1. Extract facts and district/county from request payload
2. Load effective policy via `get_effective_policy()`
3. Generate plan via `plan_from_facts()`
4. **Advisory Check:**
   - If surface_casing_shoe_plug step exists:
     - Lookup plugging_chart.casing_open_hole.data
     - Find "Surface" row
     - Compare step sacks vs chart recommendation
     - If delta >10 sacks → emit advisory finding
5. Return plan + findings array

**Response:**
```json
{
  "plan": { ... },
  "findings": [
    {
      "code": "advisory.sacks_vs_chart_delta",
      "severity": "minor",
      "message": "sacks 45 differ from chart 30 (surface) by >10",
      "context": {
        "chart_rec_sacks": 30,
        "computed_sacks": 45
      }
    }
  ]
}
```

---

### 2. `plan_preview.py`

#### **`PlanPreviewView` (assumed, not shown in snippet)**
**Purpose:** Generate preview plan for UI without persisting.

**Typical Flow:**
1. Accept well facts + preferences from frontend
2. Resolve facts via `facts_resolver.resolve_engagement_facts()`
3. Load effective policy
4. Generate plan
5. Return JSON to UI for review before submission

---

## Testing

The kernel has **16 comprehensive test files** covering:

- **District-specific tests:**
  - `test_district_08a_enhanced_recovery.py` - Andrews County enhanced recovery zones
  - `test_district_08a_tagging.py` - Open-hole shoe tagging requirements
  - `test_district_08a_wbl_protect.py` - Water-bearing layer protection
  - `test_district_7c_formation_tops.py` - Coke County formation plugs
  - `test_district_7c_ops_and_tag.py` - Operational instructions + tagging

- **Golden test cases:**
  - `test_golden_08a_andrews_approved.py` - Real approved W-3A from Andrews
  - `test_golden_08a_andrews_w2_case.py` - W-2 derived geometry tests
  - `test_golden_7c_coke.py` - Coke County approved plan
  - `test_golden_7c_reagan_sherrod_unit.py` - Reagan County field tests

- **Feature tests:**
  - `test_w3a_cibp_cap.py` - CIBP cap logic
  - `test_w3a_surface_shoe.py` - Surface shoe placement
  - `test_w3a_uqw.py` - UQW isolation
  - `test_w3a_golden_simple.py` - Minimal viable plan
  - `test_w3a_golden_with_materials.py` - Full materials computation
  - `test_piecewise_segments.py` - Segmented geometry calculations

- **Integration tests:**
  - `test_extracted_data_golden.py` - End-to-end extraction → plan
  - `test_kernel_golden_min.py` - Minimal kernel smoke test

**Run tests:**
```bash
docker exec regulagent_web python manage.py test apps.kernel.tests
```

---

## Integration Points

### Consumes From:
- **`apps/policy/services/loader.py`** → `get_effective_policy()` for policy loading
- **`apps/tenant_overlay/services/facts_resolver.py`** → `resolve_engagement_facts()` for merged facts
- **`apps/materials/services/material_engine.py`** → All capacity/sacks/volume calculations

### Provides To:
- **`apps/public_core/views/w3a_from_api.py`** → Uses kernel to generate plans from RRC API data
- **Frontend UI** → Advisory checks and plan previews
- **`apps/public_core/models/plan_snapshot.py`** → Plans stored for audit trail

---

## Key Concepts

### 1. **Deterministic Planning**
- Same facts + same policy → same plan every time
- No heuristics or AI guessing
- Full citation traceability for every step

### 2. **Policy Completeness**
- Kernel checks if all required knobs are present
- If incomplete → return constraints, no steps
- Ensures plans are never based on assumptions

### 3. **Mechanical Awareness**
- Kernel inspects existing_mechanical_barriers fact
- Suppresses operations blocked by existing equipment (CIBP blocks perf/circulate)
- Adds isolation plugs around PACKER and DV_TOOL
- Ensures cap exists above existing CIBP

### 4. **CIBP Detector**
- Checks if producing interval exposed below production shoe
- **Exposure = producing zone below shoe without CIBP**
- Emits bridge_plug + cement cap to isolate
- Uses policy knob for cap length (default 100 ft)

### 5. **Piecewise Geometry**
- Supports changing hole sizes across depth intervals
- Each segment has own outer/inner diameters
- Materials engine sums volumes across all segments
- Critical for intermediate casing transitions

### 6. **TAC §3.14(d)(11) Excess**
- "+10% per 1000 ft of plug length"
- Applied to surface shoe, UQW, formation plugs
- Example: 2500 ft plug → 2.5 kft → +25% multiplier
- Accounts for wellbore irregularities on long intervals

### 7. **Long Plug Merge**
- Optional optimization to reduce rig time
- Combines adjacent formation plugs into single operation
- Preserves tagging requirements
- Threshold-based (e.g., merge if gap ≤200 ft)

---

## File Structure

```
apps/kernel/
├── services/
│   ├── policy_kernel.py      # Main entrypoint, step enrichment pipeline
│   ├── w3a_rules.py           # W3A form step generation (TAC §3.14 logic)
│   ├── policy_registry.py    # Policy handler lookup registry
│   └── violations.py          # Violation codes and builder
├── views/
│   ├── advisory.py            # Advisory sanity check endpoint
│   └── plan_preview.py        # Plan preview for UI (assumed)
└── tests/
    ├── test_district_08a_*.py
    ├── test_district_7c_*.py
    ├── test_golden_*.py
    ├── test_w3a_*.py
    └── test_piecewise_segments.py
```

---

## Example Usage

### Generate a Compliant Plan

```python
from apps.kernel.services.policy_kernel import plan_from_facts
from apps.policy.services.loader import get_effective_policy
from apps.tenant_overlay.services.facts_resolver import resolve_engagement_facts

# 1. Resolve facts from engagement (merges tenant overlay → public → registry)
engagement_id = 123
facts = resolve_engagement_facts(engagement_id)

# 2. Load effective policy (base + district + county + field overlays)
district = facts.get("district", {}).get("value")
county = facts.get("county", {}).get("value")
policy = get_effective_policy(district=district, county=county)

# 3. Generate compliant plan
plan = plan_from_facts(facts, policy)

# 4. Access results
print(f"Generated {len(plan['steps'])} steps")
print(f"Violations: {len(plan['violations'])}")
for step in plan['steps']:
    print(f"{step['type']}: {step.get('top_ft')}-{step.get('bottom_ft')} ft")
    print(f"  Sacks: {step.get('materials', {}).get('slurry', {}).get('sacks')}")
    print(f"  Citations: {step.get('regulatory_basis')}")
```

---

## Future Enhancements

1. **Multi-Form Support** - W-2, W-15, other states
2. **Real-Time Violation Detection** - As user edits facts in UI
3. **AutoFix Hints** - Suggest corrections for violations
4. **Cost Estimation** - Materials pricing integration
5. **Rig Time Optimization** - Minimize wait cycles between operations
6. **PDF Report Generation** - Printable W-3A with all citations

---

## Maintenance Notes

- **Add new step types:** Update `_compute_materials_for_steps()` with material calculation logic
- **Add new violations:** Define in `violations.py` VCodes class
- **Add district overrides:** Extend `_apply_district_overrides()` with new district-specific rules
- **Update TAC rules:** Modify `w3a_rules.py` and update test fixtures

---

## Questions / Support

For questions about kernel logic or to report bugs:
1. Check test files for expected behavior examples
2. Review YAML policy packs in `apps/policy/packs/tx/`
3. Consult Texas TAC Title 16 Chapter 3 §3.14 for regulatory source

