# W3 from pnaexchange Implementation Analysis

**Status:** Conceptual Planning
**Date:** 2025-11-26
**Objective:** Leverage existing RegulAgent logic to build W-3 forms from pnaexchange events

---

## Executive Summary

The existing `w3a_from_api.py` codebase creates **W-3A plans** (plugging proposals) by:
1. Extracting W-2, W-15, GAU documents from RRC
2. Parsing them into structured `ExtractedDocument` JSON
3. Assembling "facts" from extracted data
4. Running a **policy kernel** that generates plugging steps
5. Calculating materials via geometry engine

For the **W-3 from pnaexchange** flow, we need to:
1. **Ingest pnaexchange events** (work records, not RRC documents)
2. **Load an existing W-3A** (reference provided in request)
3. **Extract top-level sections** (header, casing record, perforations, DUQW)
4. **Map pnaexchange events → actual work performed** (cut casing, plugs, squeezes)
5. **Build W-3 form rows** from the actual events (not kernel-generated proposals)
6. **Return final W-3** ready for RRC submission

**Key Insight:** We're NOT calling the kernel again. We're using the W-3A structure + actual events to build the **executed** W-3 form.

---

## 1. Current Data Flow in w3a_from_api.py

```
RRC Documents
    ↓
Extract JSON (W-2, W-15, GAU)
    ↓
Create ExtractedDocument records
    ↓
Assemble Facts Dict
    ├─ well geometry (casing program, shoe depths)
    ├─ UQW data (GAU depth, age, protect intervals)
    ├─ formation tops
    ├─ mechanical barriers (existing CIBP, packer, DV tool)
    └─ annular gaps from schematic
    ↓
Load Effective Policy (district/county/field specific rules)
    ↓
Call plan_from_facts(facts, policy) → Policy Kernel
    ├─ Generate regulatory plugging steps
    ├─ Calculate materials (sacks, barrels)
    ├─ Merge adjacent plugs if requested
    └─ Produce "steps" array
    ↓
Build Output Plan
    ├─ Extract header info
    ├─ Format plug rows for RRC export
    ├─ Include materials totals
    └─ Return W3APlanSerializer
```

**Key Functions in Kernel:**
- `plan_from_facts()` - entry point
- `_compute_materials_for_steps()` - calculates cement/volumes
- `_merge_adjacent_plugs()` - merges adjacent formation plugs
- `_build_additional_operations()` - formats squeeze/perforate operations

---

## 2. Proposed Data Flow for W-3 from pnaexchange

```
pnaexchange Payload
    ├─ well {api_number, well_name, operator, well_id}
    ├─ subproject {id, name}
    ├─ events[] {date, event_type, input_values, transformation_rules, ...}
    └─ w3a_reference {type, w3a_id or pdf_url}
    ↓
Load Existing W-3A
    ├─ Query RegulAgent W3AForm DB if type="regulagent"
    ├─ Parse PDF if type="pdf" (future)
    └─ Extract:
    │  ├─ header (API, well name, operator, RRC district, county, etc.)
    │  ├─ casing_program (strings with OD, top_ft, bottom_ft, cement_top)
    │  ├─ duqw (top_ft, bottom_ft)
    │  ├─ planned_perforations (status: open, perforated, plugged)
    │  └─ other top-level fields
    ↓
Normalize pnaexchange Events → W3Event objects
    ├─ Parse event_type (deterministic mapping)
    ├─ Extract depths from input_values
    ├─ Detect cut casing from transformation_rules
    └─ Build W3Event list sorted by date
    ↓
Build Dynamic Casing State
    ├─ Start with casing_program from W-3A
    ├─ Apply cut casing events (removes inner casing to depth)
    ├─ Track active casing string at each event depth
    └─ Produce casing_record with removal depths
    ↓
Group Events into Plugs
    ├─ Cluster by plug_number if provided
    ├─ Or temporal clustering (same day, adjacent depths)
    └─ Produce Plug list
    ↓
Build W-3 Rows
    ├─ For each plug:
    │  ├─ Extract depths (bottom, top/TOC)
    │  ├─ Extract pipe size from casing_string
    │  ├─ Extract cement_class, sacks from events
    │  ├─ Extract wait/tag requirements
    │  └─ Format as RRC W-3 row
    ├─ Copy casing_record from state
    ├─ Copy/update perforation_record
    └─ Include DUQW
    ↓
Build W-3 Form
    ├─ header (from W-3A)
    ├─ plugs (from grouped events)
    ├─ casing_record (from casing state)
    ├─ perforations (from W-3A + event updates)
    ├─ duqw (from W-3A)
    └─ remarks (concatenated event details)
    ↓
Return W3Form (JSON)
```

---

## 3. Reusable Logic from Existing Codebase

### 3.1 Leverage: Facts Assembly Pattern (w3a_from_api.py, lines 915-945)

**What it does:**
- Extracts casing geometry from W-2 `casing_record`
- Normalizes sizes (handles fractional notation)
- Identifies surface/intermediate/production/liner strings
- Tracks shoe depths and material properties

**How to reuse:**
```python
# In new W3 builder:
def parse_w3a_casing_program(w3a_form: dict) -> List[CasingStringState]:
    """
    Extract casing program from W-3A top section.
    Returns list of CasingStringState objects with OD, top, bottom, removed_to_depth.
    """
    casing_program = []
    for row in (w3a_form.get("casing_record") or []):
        cs = CasingStringState(
            name=row.get("string_type") or row.get("string"),
            od_in=_parse_size(row.get("size_in")),
            top_ft=row.get("top_ft") or 0.0,
            bottom_ft=row.get("bottom_ft") or row.get("shoe_depth_ft"),
            removed_to_depth_ft=row.get("removed_to_depth_ft") or None
        )
        casing_program.append(cs)
    return casing_program
```

**Existing code to adapt:**
- Lines 764-787: `_parse_size()` function (handles "5 1/2" and "5.5" formats)
- Lines 738-757: Casing geometry extraction logic

---

### 3.2 Leverage: Dynamic Casing State Engine (mvp_spec, section 5)

**What it does:**
- Tracks which casing is "active" at each depth
- Handles cut casing (marks string as removed_to_depth)
- Determines innermost casing present at a given depth

**How to reuse:**
The specification already defines the algorithm! We just need to implement:
```python
def apply_cut_casing(casing_state: List[CasingStringState], depth_ft: float) -> None:
    """Mark innermost casing as cut at depth_ft."""
    candidates = [
        cs for cs in casing_state 
        if cs.top_ft <= depth_ft <= cs.bottom_ft
        and (not cs.removed_to_depth_ft or depth_ft > cs.removed_to_depth_ft)
    ]
    if candidates:
        innermost = min(candidates, key=lambda cs: cs.od_in)
        innermost.removed_to_depth_ft = depth_ft

def get_active_casing_at_depth(casing_state: List[CasingStringState], depth_ft: float) -> Optional[CasingStringState]:
    """Get the active (innermost) casing string at given depth."""
    present = [
        cs for cs in casing_state 
        if cs.top_ft <= depth_ft <= cs.bottom_ft
        and (not cs.removed_to_depth_ft or depth_ft > cs.removed_to_depth_ft)
    ]
    return min(present, key=lambda cs: cs.od_in) if present else None
```

**No existing code to adapt** - this is new logic, but simple and localized.

---

### 3.3 Leverage: Event Grouping & Plug Row Building

**What it does (kernel):**
- Lines 1221-1356 in `policy_kernel.py`: `_merge_adjacent_plugs()`
- Already implements logic to group nearby plugs by depth threshold
- Handles metadata merging (regulatory basis, tags, merged_steps)

**How to reuse:**
For **pnaexchange W-3**, we don't need the merge logic immediately, but we DO need:
```python
def group_events_into_plugs(events: List[W3Event]) -> List[Plug]:
    """
    Cluster events into logical plug groups.
    - If plug_number provided: use it
    - Else: temporal clustering (same day, adjacent depths)
    """
    if not events:
        return []
    
    # Sort by date, then depth
    sorted_events = sorted(events, key=lambda e: (e.date, e.depth_bottom_ft or 0))
    
    plugs = []
    current_plug = Plug(plug_number=1, events=[])
    
    for event in sorted_events:
        # Simple heuristic: new plug if >48 hrs gap or >500 ft depth gap
        if (not current_plug.events or 
            (event.date - current_plug.events[-1].date).days >= 2 or
            (event.depth_bottom_ft and current_plug.events[-1].depth_bottom_ft and
             abs(event.depth_bottom_ft - current_plug.events[-1].depth_bottom_ft) > 500)):
            if current_plug.events:
                plugs.append(current_plug)
            current_plug = Plug(plug_number=len(plugs)+1, events=[event])
        else:
            current_plug.events.append(event)
    
    if current_plug.events:
        plugs.append(current_plug)
    
    return plugs
```

**Existing code to adapt:**
- `_merge_adjacent_plugs()` merging logic (inverse: split instead)
- Plug numbering and sequencing logic

---

### 3.4 Leverage: Additional Operations Builder (w3a_from_api.py, lines 41-93)

**What it does:**
- Formats compound operations (perforate, squeeze, wait, tag) into human-readable strings
- Used in RRC export for multi-step procedures

**How to reuse:**
```python
# Already defined in w3a_from_api.py!
# Just call _build_additional_operations(step) for each event/plug
additional_ops = _build_additional_operations({
    "type": "perforate_and_squeeze_plug",
    "details": {
        "perforation_interval": {"top_ft": 5000, "bottom_ft": 5100},
        "cement_cap_inside_casing": {"top_ft": 5000, "bottom_ft": 4950, "height_ft": 50},
        "verification": {"required_wait_hr": 4}
    },
    "tag_required": True
})
# Returns: ["Perforate at 5100-5000 ft", "Squeeze cement...", "Wait 4 hr and tag TOC"]
```

**No changes needed** - can be imported directly!

---

### 3.5 Leverage: Materials Calculation (kernel)

**What it does:**
- Lines 730-813 in `policy_kernel.py`: `_compute_materials_for_steps()`
- Calculates cement volumes, sacks, density, additives for each plug type
- Handles geometry contexts (cased, open-hole, annular vs inside)

**How to reuse:**
For **W-3 from pnaexchange**, we MAY NOT need this initially because:
- pnaexchange events should already include sacks, cement class from work records
- But we MIGHT want to calculate volumes/weights from sacks if missing

```python
# Optional enhancement:
from apps.kernel.services.policy_kernel import _compute_materials_for_steps

# If events have sacks, we can optionally compute volumes via existing logic
materials_enhanced = _compute_materials_for_steps([
    {
        "type": "cement_plug",
        "top_ft": 5000,
        "bottom_ft": 5100,
        "sacks": 40,
        "cement_class": "C",
        # geometry context inferred from casing state
    }
])
```

**Use case:** Filling in missing material data from partial pnaexchange records.

---

## 4. Missing/New Logic to Implement

### 4.1 Event Normalization (Mapper)

**Input:** pnaexchange event record
**Output:** W3Event dataclass

```python
@dataclass
class W3Event:
    event_type: str  # "Set Surface Plug", "Squeeze", "Perforate", "Cut Casing", "Tag"
    date: date
    start_time: Optional[time]
    end_time: Optional[time]
    
    # Depths
    depth_top_ft: Optional[float]       # TOC or top of interval
    depth_bottom_ft: Optional[float]    # Bottom of plug/interval
    perf_depth_ft: Optional[float]      # Perforation depth if different
    tagged_depth_ft: Optional[float]    # Tagged depth if measured
    plug_number: Optional[int]          # Plug sequence number
    
    # Materials
    cement_class: Optional[str]         # "C", "G", "H", etc.
    sacks: Optional[float]              # Cement sacks
    volume_bbl: Optional[float]         # Cement volume in barrels
    pressure_psi: Optional[float]       # Squeeze pressure
    
    # Tracking
    raw_event_detail: str
    work_assignment_id: int
    dwr_id: int
    
    # Casing state
    jump_to_next_casing: bool = False
    casing_string: Optional[str] = None  # Will be filled by casing state engine
```

**Mapping logic:**
```python
def normalize_pna_event(event: dict) -> W3Event:
    """Map pnaexchange event → W3Event."""
    tr = event.get("transformation_rules", {})
    iv = event.get("input_values", {})
    
    # Standardize event type (normalize variations)
    event_type_raw = str(event.get("event_type", "")).lower()
    event_type = normalize_event_type(event_type_raw)
    
    return W3Event(
        event_type=event_type,
        date=parse_date(event["date"]),
        start_time=parse_time(event.get("start_time")),
        end_time=parse_time(event.get("end_time")),
        depth_bottom_ft=float(iv.get("4")) if iv.get("4") else None,
        depth_top_ft=float(iv.get("5")) if iv.get("5") else None,
        plug_number=int(iv.get("1")) if iv.get("1") else None,
        cement_class=str(iv.get("3")).upper() if iv.get("3") else None,
        sacks=float(iv.get("6")) if iv.get("6") else None,
        pressure_psi=parse_pressure(iv.get("7")) if iv.get("7") else None,
        raw_event_detail=event.get("event_detail", ""),
        work_assignment_id=event.get("work_assignment_id", 0),
        dwr_id=event.get("dwr_id", 0),
        jump_to_next_casing=tr.get("jump_to_next_casing", False),
    )
```

---

### 4.2 W-3A Loader

**Input:** w3a_reference from pnaexchange request
**Output:** W3AForm dict

```python
def load_w3a_form(w3a_reference: dict) -> dict:
    """
    Load W-3A form from either RegulAgent DB or uploaded PDF.
    """
    ref_type = w3a_reference.get("type")
    
    if ref_type == "regulagent":
        w3a_id = w3a_reference.get("w3a_id")
        # Query RegulAgent DB (TBD: identify model)
        # For now: assume stored as JSON in ExtractedDocument or PlanSnapshot
        # w3a = W3ARecord.objects.get(id=w3a_id)
        # return w3a.json_data
        pass
    elif ref_type == "pdf":
        pdf_file = w3a_reference.get("pdf_file")  # File object uploaded
        pdf_path = w3a_reference.get("pdf_url")   # Or URL path
        
        # Send PDF to OpenAI with extraction prompt
        from apps.public_core.services.w3_extraction import extract_w3a_from_pdf
        w3a_json = extract_w3a_from_pdf(pdf_path or pdf_file)
        return w3a_json
    else:
        raise ValueError(f"Unknown w3a_reference type: {ref_type}")

    return w3a_form
```

**Implementation Detail:**
The `extract_w3a_from_pdf()` function will:
1. Read the PDF file
2. Build an OpenAI extraction prompt (similar to `classify_document()` and `extract_json_from_pdf()`)
3. Send to OpenAI with instructions to extract:
   - Header section (API, well name, operator, RRC district, county)
   - Casing record (strings, sizes, depths, cement tops)
   - Record of perforated intervals / open hole
   - Plugging proposal (if W-3A contains actual plugging plan)
   - DUQW (depth of usable quality water)
4. Return structured JSON with all required fields

**Reuses existing pattern:**
- Same approach as `extract_json_from_pdf(path, "w2")` in `w3a_from_api.py`
- Leverage `openai_extraction.py` utilities

---

### 4.3 Plug Row Builder

**Input:** Plug object (group of W3Events)
**Output:** RRC-format plug row

```python
def build_plug_row(plug: Plug, casing_state: List[CasingStringState]) -> dict:
    """
    Build a single W-3 plug row from a group of events.
    """
    events = plug.events
    if not events:
        return {}
    
    # Determine plug depth (from deepest event)
    depths = [e.depth_bottom_ft for e in events if e.depth_bottom_ft]
    depths += [e.depth_top_ft for e in events if e.depth_top_ft]
    
    bottom_ft = max(depths) if depths else None
    top_ft = min(depths) if depths else None
    
    # Get active casing and pipe size
    casing = get_active_casing_at_depth(casing_state, bottom_ft) if bottom_ft else None
    pipe_size_in = casing.od_in if casing else None
    
    # Cement and materials from first relevant event
    cement_event = next((e for e in events if e.cement_class), None)
    cement_class = cement_event.cement_class if cement_event else None
    sacks = cement_event.sacks if cement_event else None
    
    # Additional operations
    additional = []
    for event in events:
        if "perforate" in event.event_type.lower():
            additional.append(f"Perforate at {event.perf_depth_ft}' ft")
        if "squeeze" in event.event_type.lower():
            additional.append("Squeeze cement through perforations")
        if "tag" in event.event_type.lower():
            additional.append("Wait and tag TOC")
    
    return {
        "plug_no": plug.plug_number,
        "date": str(min([e.date for e in events])),
        "type": infer_plug_type(events),
        "from_ft": bottom_ft,
        "to_ft": top_ft,
        "pipe_size": f'{pipe_size_in}"' if pipe_size_in else None,
        "toc_calc": top_ft,  # Calculated
        "toc_measured": next((e.tagged_depth_ft for e in events if e.tagged_depth_ft), None),
        "sacks": sacks,
        "cement_class": cement_class,
        "additional": additional if additional else None,
        "remarks": "; ".join([e.raw_event_detail for e in events]),
    }
```

---

## 5. Architecture Decisions

### 5.1 Where to Build the W3 Builder?

**Option A: New View (`W3FromPnaView`)**
- Location: `apps/public_core/views/w3_from_pna.py` (new file)
- Parallel to `W3AFromApiView`
- Pros: Clean separation, testable
- Cons: Code duplication

**Option B: Extend W3AFromApiView**
- Add new method `handle_pna_request()`
- Pros: Reuse auth, error handling
- Cons: Conflates two different flows

**Recommendation:** **Option A** - New view. Keeps concerns separate and allows independent iteration.

---

### 5.2 Where to Build the W3 Form Service?

**New Directory Structure:**
```
apps/public_core/
├── services/
│   ├── rrc/
│   │   ├── w3/
│   │   │   ├── __init__.py
│   │   │   ├── extraction.py           # extract_w3a_from_pdf()
│   │   │   ├── builder.py              # W3Builder class
│   │   │   ├── models.py               # W3Event, Plug, CasingStringState
│   │   │   ├── mapper.py               # normalize_pna_event(), event_type mapping
│   │   │   ├── casing_engine.py        # apply_cut_casing(), get_active_casing_at_depth()
│   │   │   └── formatter.py            # build_plug_row(), build_casing_record(), etc.
│   │   └── __init__.py
│   └── ... (existing services)
├── views/
│   ├── w3_from_pna.py                 # W3FromPnaView (new)
│   └── ... (existing views)
└── ... (existing modules)
```

**Location:** `apps/public_core/services/rrc/w3/builder.py` (and supporting modules)

**W3Builder Class:**
```python
# File: apps/public_core/services/rrc/w3/builder.py
from typing import List, Dict, Any
from copy import deepcopy
from .models import W3Event, Plug, CasingStringState, W3Form
from .mapper import normalize_pna_event
from .casing_engine import apply_cut_casing, get_active_casing_at_depth
from .formatter import build_plug_row, build_casing_record, build_perforation_table

class W3Builder:
    """Build W-3 forms from pnaexchange events and W-3A reference."""
    
    def __init__(self, w3a_form: dict):
        self.w3a_form = w3a_form
        self.casing_program = self._parse_w3a_casing_program(w3a_form)
        self.casing_state = deepcopy(self.casing_program)
    
    def _parse_w3a_casing_program(self, w3a_form: dict) -> List[CasingStringState]:
        """Extract casing program from W-3A top section."""
        casing_program = []
        for row in (w3a_form.get("casing_record") or []):
            cs = CasingStringState(
                name=row.get("string_type") or row.get("string"),
                od_in=self._parse_size(row.get("size_in")),
                top_ft=row.get("top_ft") or 0.0,
                bottom_ft=row.get("bottom_ft") or row.get("shoe_depth_ft"),
                removed_to_depth_ft=row.get("removed_to_depth_ft") or None
            )
            casing_program.append(cs)
        return casing_program
    
    @staticmethod
    def _parse_size(txt: Any) -> Optional[float]:
        """Parse casing size (handles '5 1/2', '5.5', etc.)."""
        # Reuse logic from w3a_from_api.py lines 764-787
        if txt is None:
            return None
        if isinstance(txt, (int, float)):
            return float(txt)
        t = str(txt).strip().replace('"', '')
        if ' ' in t:
            parts = t.split()
            try:
                whole = float(parts[0])
            except Exception:
                return None
            frac = 0.0
            if len(parts) > 1 and '/' in parts[1]:
                try:
                    num, den = parts[1].split('/')
                    frac = float(num) / float(den)
                except Exception:
                    frac = 0.0
            return whole + frac
        try:
            return float(t)
        except Exception:
            return None
    
    def normalize_events(self, raw_events: List[dict]) -> List[W3Event]:
        """Convert pnaexchange events → W3Event objects."""
        return [normalize_pna_event(e) for e in raw_events]
    
    def update_casing_state(self, events: List[W3Event]) -> None:
        """Apply cut casing events to casing state."""
        for event in events:
            if event.jump_to_next_casing:
                apply_cut_casing(self.casing_state, event.depth_bottom_ft)
            # Track which casing string each event applies to
            if event.depth_bottom_ft:
                active_casing = get_active_casing_at_depth(self.casing_state, event.depth_bottom_ft)
                if active_casing:
                    event.casing_string = active_casing.name
    
    def group_plugs(self, events: List[W3Event]) -> List[Plug]:
        """Cluster events into logical plugs."""
        from .formatter import group_events_into_plugs
        return group_events_into_plugs(events)
    
    def build_plug_rows(self, plugs: List[Plug]) -> List[dict]:
        """Build RRC W-3 plug rows from plug groups."""
        return [build_plug_row(p, self.casing_state) for p in plugs]
    
    def build_w3_form(self, raw_events: List[dict]) -> Dict[str, Any]:
        """Main entry point: build complete W-3 form."""
        # 1. Normalize
        w3_events = self.normalize_events(raw_events)
        
        # 2. Update casing state
        self.update_casing_state(w3_events)
        
        # 3. Group plugs
        plugs = self.group_plugs(w3_events)
        
        # 4. Build rows
        plug_rows = self.build_plug_rows(plugs)
        
        # 5. Build casing record
        casing_record = build_casing_record(self.casing_state)
        
        # 6. Build perf table
        perfs = build_perforation_table(w3_events, self.w3a_form)
        
        # 7. Build remarks
        remarks = "\n".join([f"{e.date} – {e.raw_event_detail}" for e in w3_events])
        
        return W3Form(
            header=self.w3a_form.get("header", {}),
            plugs=plug_rows,
            casing_record=casing_record,
            perforations=perfs,
            duqw=self.w3a_form.get("duqw", {}),
            remarks=remarks,
        ).to_dict()
```

**File Organization Rationale:**
- `extraction.py`: W-3A PDF extraction (parallel to `openai_extraction.py`)
- `builder.py`: Main orchestrator class
- `models.py`: Data classes (W3Event, Plug, CasingStringState, W3Form)
- `mapper.py`: Event normalization (pnaexchange → W3Event)
- `casing_engine.py`: Casing state logic (apply_cut, get_active)
- `formatter.py`: Output formatting (plug rows, casing record, perfs)

---

## 6. Implementation Roadmap

### Phase 1: File Structure & Data Models (1 day)
**Location:** `apps/public_core/services/rrc/w3/`

- [ ] Create directory structure:
  - `apps/public_core/services/rrc/w3/__init__.py`
  - `apps/public_core/services/rrc/w3/models.py`
  - `apps/public_core/services/rrc/w3/mapper.py`
  - `apps/public_core/services/rrc/w3/casing_engine.py`
  - `apps/public_core/services/rrc/w3/formatter.py`
  - `apps/public_core/services/rrc/w3/builder.py`
  - `apps/public_core/services/rrc/w3/extraction.py`
  - `apps/public_core/services/rrc/__init__.py` (if not exists)

- [ ] `models.py`: Define dataclasses
  - W3Event
  - Plug
  - CasingStringState
  - W3Form

- [ ] `mapper.py`: Event type normalization
  - `normalize_event_type()`
  - `normalize_pna_event()`

### Phase 2: W-3A Extraction (1 day)
**Location:** `apps/public_core/services/rrc/w3/extraction.py`

- [ ] Implement `extract_w3a_from_pdf(pdf_path: str) -> dict`
  - Build OpenAI extraction prompt
  - Extract header, casing, perforations, DUQW
  - Validate JSON response
  - Return structured W-3A dict

- [ ] Reuse patterns from `openai_extraction.py`:
  - `_load_prompt()` for extraction instructions
  - `classify_document()`
  - `extract_json_from_pdf()`

### Phase 3: Casing State Engine (1 day)
**Location:** `apps/public_core/services/rrc/w3/casing_engine.py`

- [ ] Implement `apply_cut_casing()`
- [ ] Implement `get_active_casing_at_depth()`
- [ ] Unit tests for edge cases:
  - Multiple overlapping cuts
  - No active casing (error handling)
  - Cuts at boundaries

### Phase 4: Plug Grouping & Row Building (2 days)
**Location:** `apps/public_core/services/rrc/w3/formatter.py`

- [ ] Implement `group_events_into_plugs()`
  - Temporal clustering logic
  - Depth-based clustering
  - Plug numbering

- [ ] Implement `build_plug_row()`
  - Extract depths, pipe size, cement, materials
  - Infer plug type
  - Format for RRC export

- [ ] Implement helper functions:
  - `infer_plug_type()`
  - `build_casing_record()`
  - `build_perforation_table()`
  - `parse_date()`, `parse_time()`, `parse_pressure()`

- [ ] Reuse `_build_additional_operations()` from `w3a_from_api.py`

### Phase 5: W3 Builder Service (1 day)
**Location:** `apps/public_core/services/rrc/w3/builder.py`

- [ ] Create `W3Builder` class
- [ ] Implement `__init__()`, `normalize_events()`, `update_casing_state()`
- [ ] Implement `group_plugs()`, `build_plug_rows()`
- [ ] Implement `build_w3_form()` pipeline
- [ ] Handle error cases (missing fields, invalid states)

### Phase 6: API View & Serializers (1.5 days)
**Location:** `apps/public_core/views/w3_from_pna.py` + serializers

- [ ] Create `W3FromPnaView` (new file)
  - Parallel to `W3AFromApiView`
  - POST endpoint `/api/w3/build-from-pna/`
  - Request validation, response formatting

- [ ] Create serializers:
  - `W3FromPnaRequestSerializer`
  - `W3FormResponseSerializer`

- [ ] Wire URL in `apps/public_core/urls.py`

### Phase 7: Integration & Error Handling (1 day)
- [ ] Handle W-3A reference loading (DB vs PDF)
- [ ] Error responses for invalid events
- [ ] Logging and debug output
- [ ] Request/response validation

### Phase 8: Testing & Refinement (2 days)
- [ ] Unit tests for each module
- [ ] Integration test with sample pnaexchange payload
- [ ] Edge case handling (missing fields, cuts beyond casing, etc.)
- [ ] Documentation

---

## 7. W-3A PDF Extraction Approach

### Overview
When pnaexchange provides a W-3A as a PDF (vs. a RegulAgent ID reference), we extract structured JSON using OpenAI, **exactly like we do for W-2, W-15, and GAU documents**.

### Implementation: `extract_w3a_from_pdf()`

**Location:** `apps/public_core/services/rrc/w3/extraction.py`

```python
from pathlib import Path
from typing import Dict, Any
from apps.public_core.services.openai_extraction import extract_json_from_pdf

def extract_w3a_from_pdf(pdf_path: str) -> Dict[str, Any]:
    """
    Extract W-3A form data from PDF using OpenAI.
    
    Sends PDF to OpenAI with structured extraction prompt.
    Returns JSON with: header, casing_record, perforations, duqw, remarks, etc.
    """
    prompt = _build_w3a_extraction_prompt()
    result = extract_json_from_pdf(Path(pdf_path), doc_type="w3a", custom_prompt=prompt)
    return result.json_data

def _build_w3a_extraction_prompt() -> str:
    """
    Build extraction prompt for W-3A PDF forms.
    
    Instructs OpenAI to extract:
    1. Header section (API, well name, operator, RRC district, county, field)
    2. Casing record (strings with sizes, depths, cement tops, removed depths)
    3. Record of perforated intervals / open hole
    4. Plugging proposal (actual plugs planned/executed)
    5. DUQW (depth of usable quality water)
    6. Any remarks or notes
    
    Returns normalized JSON following W-3A schema.
    """
    return """Extract W-3A (Well Plugging and Abandonment) form data.
    
Return a JSON object with the following structure:

{
  "header": {
    "api_number": "string (14-digit, e.g., 42-501-70575)",
    "well_name": "string",
    "operator": "string",
    "operator_number": "string",
    "rrc_district": "string (e.g., '08A')",
    "county": "string",
    "field": "string",
    "lease": "string",
    "well_no": "string",
    "latitude": float or null,
    "longitude": float or null
  },
  
  "casing_record": [
    {
      "string_type": "surface|intermediate|production|liner",
      "size_in": float,
      "weight_ppf": float or null,
      "grade": "string or null",
      "top_ft": float,
      "bottom_ft": float,
      "shoe_depth_ft": float or null,
      "cement_top_ft": float or null,
      "removed_to_depth_ft": float or null  // If cut or pulled
    }
  ],
  
  "perforations": [
    {
      "interval_top_ft": float,
      "interval_bottom_ft": float,
      "formation": "string or null",
      "status": "open|perforated|squeezed|plugged",
      "perforation_date": "string or null"
    }
  ],
  
  "plugging_proposal": [
    {
      "plug_number": int,
      "depth_top_ft": float,
      "depth_bottom_ft": float,
      "type": "cement_plug|bridge_plug|squeeze|other|string",
      "cement_class": "string or null",
      "sacks": int or null,
      "remarks": "string or null"
    }
  ],
  
  "duqw": {
    "depth_ft": float,
    "formation": "string or null",
    "determination_method": "string or null"
  },
  
  "remarks": "string or null"
}

Key extraction rules:
- Casing record: Extract all strings (surface, intermediate, production, liner)
- Cement tops: Look for 'TOC', 'cement top', 'cemented to' annotations
- Removed/cut casing: Note any strings that were cut or pulled
- Perforations: Extract all perforation intervals with status (open, squeezed, plugged)
- Plugging proposal: Extract proposed plug sequence with depths, types, materials
- DUQW: Extract groundwater protection determination depth
- Dates: Extract well completion date, plugging dates if available
- Handle both TVD (true vertical depth) and MD (measured depth) - report TVD

Return ONLY valid JSON. If a field cannot be found, use null. Do not include
explanatory text, only the JSON structure."""
```

### Reuse Pattern from Existing Code

**Existing function signature:**
```python
# From apps/public_core/services/openai_extraction.py
def extract_json_from_pdf(path: Path, doc_type: str) -> ExtractedData:
    """
    path: Path to PDF file
    doc_type: One of 'w2', 'w15', 'gau', 'schematic', 'formation_tops', or NEW: 'w3a'
    
    Returns ExtractedData with:
        - json_data: Dict with extracted fields
        - model_tag: str identifier of model used
        - errors: List of validation errors if any
    """
```

**Our new `extract_w3a_from_pdf()` function:**
- Calls `extract_json_from_pdf(pdf_path, "w3a")`
- Returns `result.json_data` (the structured dict)
- Errors handled by caller

**No changes needed to `openai_extraction.py`** - we just add `w3a` as a new doc_type option in the existing prompt system.

### Integration with W3FromPnaView

```python
# In views/w3_from_pna.py
from apps.public_core.services.rrc.w3.extraction import extract_w3a_from_pdf

def post(self, request):
    # ... validation ...
    
    w3a_reference = request_data.get("w3a_reference", {})
    ref_type = w3a_reference.get("type")  # "regulagent" or "pdf"
    
    if ref_type == "regulagent":
        w3a_id = w3a_reference.get("w3a_id")
        w3a_form = self._load_w3a_from_db(w3a_id)
    elif ref_type == "pdf":
        w3a_file = request.FILES.get("w3a_file")
        w3a_path = save_upload(w3a_file)
        try:
            w3a_form = extract_w3a_from_pdf(w3a_path)
        finally:
            cleanup_upload(w3a_path)
    else:
        return error_response("Invalid w3a_reference type")
    
    # Build W-3 using extracted W-3A
    builder = W3Builder(w3a_form)
    w3_result = builder.build_w3_form(events)
    return success_response(w3_result)
```

---

## 8. Key Questions to Clarify

1. **W-3A Storage:** Where/how are W-3A forms stored in RegulAgent? Are they:
   - ExtractedDocument records?
   - Separate W3AForm model?
   - Embedded in PlanSnapshot payloads?

2. **Event Type Mapping:** What are the valid pnaexchange event_type values?
   - "Set Surface Plug"?
   - "Squeeze"?
   - "Perforate"?
   - "Tag"?
   - List needed for normalization logic.

3. **Plug Grouping Strategy:** How should we cluster events?
   - By plug_number if provided?
   - By temporal proximity (same day)?
   - By depth proximity (< 500 ft)?
   - By event type (all plug-setting events form one plug)?

4. **Perforation Updates:** Can pnaexchange events UPDATE perforation status?
   - E.g., "Squeezed perf at 5100'" changes perf from "open" to "squeezed"?
   - Should W-3 reflect historical vs current perf status?

5. **Materials from Events:** Does pnaexchange always provide:
   - Cement class?
   - Sacks?
   - Pressure?
   - Or do we need fallback logic?

---

## 9. Conclusion

### Reusable Existing Components

From `w3a_from_api.py` and existing services:
- ✅ `_parse_size()` - size parsing (lines 764-787)
- ✅ `_build_additional_operations()` - operation formatting (lines 41-93)
- ✅ `extract_json_from_pdf()` - PDF extraction pattern (from `openai_extraction.py`)
- ✅ Casing geometry extraction patterns (lines 738-757)
- ✅ Materials calculation (optional enhancement, lines 730-813)
- ✅ RRC export formatting

### New Components to Build

Organized in `apps/public_core/services/rrc/w3/`:
- ❌ `models.py` - W3Event, Plug, CasingStringState, W3Form dataclasses
- ❌ `mapper.py` - Event normalization (pnaexchange → W3Event)
- ❌ `extraction.py` - W-3A PDF extraction via OpenAI
- ❌ `casing_engine.py` - Dynamic casing state (apply_cut_casing, get_active_casing)
- ❌ `formatter.py` - Plug grouping, row building, formatting
- ❌ `builder.py` - W3Builder orchestrator class
- ❌ `w3_from_pna.py` - W3FromPnaView API endpoint (in `views/`)

### File Structure

```
apps/public_core/
├── services/
│   ├── rrc/
│   │   ├── __init__.py
│   │   └── w3/
│   │       ├── __init__.py
│   │       ├── models.py              # Data classes
│   │       ├── mapper.py              # Event mapping
│   │       ├── extraction.py          # W-3A PDF extraction
│   │       ├── casing_engine.py       # Casing state logic
│   │       ├── formatter.py           # Output formatting
│   │       └── builder.py             # W3Builder class
├── views/
│   └── w3_from_pna.py                # W3FromPnaView (new)
└── ...
```

### Effort Estimate

**~10-14 days** for full MVP including tests and integration:
- Phase 1: 1 day (file structure + models)
- Phase 2: 1 day (W-3A extraction via OpenAI)
- Phase 3: 1 day (casing state engine)
- Phase 4: 2 days (plug grouping + formatting)
- Phase 5: 1 day (W3Builder service)
- Phase 6: 1.5 days (API view + serializers)
- Phase 7: 1 day (integration + error handling)
- Phase 8: 2 days (testing + refinement)

### Key Clarifications Made

✅ **PDF Extraction:** W-3A PDFs extracted via OpenAI (same pattern as W-2/W-15/GAU), returning structured JSON with header, casing, perforations, DUQW sections.

✅ **File Organization:** All new logic in `apps/public_core/services/rrc/w3/` directory with clear separation of concerns (models, mapper, extraction, engine, formatter, builder).

✅ **Reuse:** Leverages existing patterns from `w3a_from_api.py` and `openai_extraction.py` without modification.

### Next Steps

1. ✅ **Review this analysis** - confirm architecture and file structure
2. ⏳ **Clarify remaining questions** (event types, W-3A storage, plug grouping heuristics)
3. 🚀 **Approve Phase 1-2** - begin implementation of data models and extraction
4. 📋 **Proceed sequentially** through phases as per your preference [[memory:7051959]]


