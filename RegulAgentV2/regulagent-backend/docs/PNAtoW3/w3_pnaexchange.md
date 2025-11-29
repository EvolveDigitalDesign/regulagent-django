Below is your complete **`REGULAGENT_W3_MVP_SPEC.md`** — a fully consolidated, implementation-ready guide your engineering team can follow.
You can copy/paste this directly into your repo.

---

# `REGULAGENT_W3_MVP_SPEC.md`

# RegulAgent W-3 MVP Architecture

**Version:** MVP-1.0
**Systems Involved:**

* **pnaexchange** = operational tracking, work descriptions
* **RegulAgent** = regulatory logic, geometry engine, W-3A/W-3 generation

---

# 📌 Overview

This MVP defines how **pnaexchange sends RRC-required plugging events** + **W-3A context** to **RegulAgent**, and how RegulAgent processes that data into a **fully structured W-3 form** (JSON + optional PDF).

This architecture is intentionally lean, reliable, and future-proof:

* MVP = deterministic mapping + geometry engine
* Post-MVP = LLM event extraction for arbitrary platforms

This file defines the **full workflow**, **data models**, **API contracts**, and **builder logic**.

---

# Table of Contents

1. [High-Level Workflow](#high-level-workflow)
2. [API Contract: pnaexchange → RegulAgent](#api-contract)
3. [RegulAgent Data Models](#data-models)
4. [Event Normalization (Mapper)](#mapper)
5. [Dynamic Casing State Engine](#casing-engine)
6. [W-3 Builder Pipeline](#w3builder)
7. [Output Schema: W-3 Form](#w3form)
8. [Future Extensions (LLM, PDF, Multi-Platform)](#future)

---

# 1. High-Level Workflow <a name="high-level-workflow"></a>

### **STEP 1 — User triggers W-3 generation**

User or system calls:

```
GET /core/w3/{subproject_id}/
```

pnaexchange returns **RRC-relevant structured events**, including:

* Work description
* Event type (template-based)
* Input values
* Transformation rules (including cut casing flags)
* Timestamps

### **STEP 2 — pnaexchange POSTs to RegulAgent**

```
POST /api/w3/build-from-pna/
```

Payload contains:

* Well metadata
* Subproject metadata
* Structured event list
* W-3A reference (either existing RegulAgent W-3A or PDF)

### **STEP 3 — RegulAgent builds the W-3**

Steps inside RegulAgent:

1. Normalize events → `W3Event` objects
2. Load structured W-3A (`W3AForm`)
3. Build dynamic casing state (handles cut casing & casing removal)
4. Group events into plugs
5. Build casing table, perf table, DUQW, plug rows, header, remarks
6. Produce final `W3Form`
7. Return JSON (and later: PDF)

### **STEP 4 — pnaexchange receives W-3**

pnaexchange renders or stores the W-3 results.

---

# 2. API Contract: pnaexchange → RegulAgent <a name="api-contract"></a>

## `POST /api/w3/build-from-pna/`

### **Request Body**

```jsonc
{
  "well": {
    "api_number": "42-501-70575",
    "well_name": "Test Complete Well Flow",
    "operator": "Diamondback E&P LLC",
    "well_id": 36
  },
  "subproject": {
    "id": 96,
    "name": "Test Complete Well Flow - Test Type - 09-11-2025"
  },
  "events": [
    {
      "date": "2025-11-10",
      "event_type": "Set Surface Plug",
      "event_detail": "Plug 1 Squeezed 2 class c cement from 6525 to 6500",
      "start_time": "20:30:00",
      "end_time": "21:30:00",
      "duration_hours": 1.0,
      "work_assignment_id": 175,
      "dwr_id": 167,
      "input_values": { "1": "1", "2": "2", "3": "c", "4": "6525", "5": "6500", "6": "26", "7": "13 psi" },
      "transformation_rules": {
        "required_inputs": 7,
        "jump_to_next_casing": false
      }
    }
  ],
  "w3a_reference": {
    "type": "regulagent",
    "w3a_id": 123
  }
}
```

### **Response Body**

```jsonc
{
  "status": "success",
  "w3": {
    "header": {...},
    "plugs": [...],
    "casing_record": [...],
    "perforations": [...],
    "duqw": {...},
    "remarks": "11/4/25 Set CIBP @ 4435’. ...",
    "pdf_url": null  // pdf optional post-MVP
  }
}
```

---

# 3. RegulAgent Data Models <a name="data-models"></a>

## 3.1 `W3Event`

```python
@dataclass
class W3Event:
    event_type: str
    date: date
    start_time: Optional[time]
    end_time: Optional[time]

    depth_top_ft: Optional[float]
    depth_bottom_ft: Optional[float]
    perf_depth_ft: Optional[float]
    tagged_depth_ft: Optional[float]
    plug_number: Optional[int]

    cement_class: Optional[str]
    sacks: Optional[float]
    volume_bbl: Optional[float]
    pressure_psi: Optional[float]

    raw_event_detail: str
    work_assignment_id: int
    dwr_id: int

    jump_to_next_casing: bool = False
    casing_string: Optional[str] = None
```

---

## 3.2 `CasingStringState`

```python
@dataclass
class CasingStringState:
    name: str
    od_in: float
    top_ft: float
    bottom_ft: float
    removed_to_depth_ft: Optional[float] = None
```

---

## 3.3 `W3AForm` (simplified MVP)

```python
@dataclass
class W3AForm:
    casing_program: List[CasingStringState]
    duqw_top_ft: float
    duqw_bottom_ft: float
    header: dict
    planned_perforations: List[dict]
```

---

## 3.4 `W3Form`

```python
@dataclass
class W3Form:
    header: dict
    plugs: List[dict]
    casing_record: List[dict]
    perforations: List[dict]
    duqw: dict
    remarks: str
```

---

# 4. Event Normalization (Mapper) <a name="mapper"></a>

Convert each pnaexchange event → `W3Event`.

Key logic:

* Use `event_type` to classify (deterministic)
* Use `input_values` to extract depths, sacks, volumes
* Use `transformation_rules.jump_to_next_casing` to detect cut casing
* (Fallback) detect “cut casing” in event_detail text

Example:

```python
def map_pna_event(e: dict) -> W3Event:
    tr = e.get("transformation_rules", {})
    iv = e["input_values"]

    evt = W3Event(
        event_type=normalize_event_type(e["event_type"]),
        date=e["date"],
        start_time=e["start_time"],
        end_time=e["end_time"],
        depth_bottom_ft=float(iv.get("4")) if "4" in iv else None,
        depth_top_ft=float(iv.get("5")) if "5" in iv else None,
        raw_event_detail=e["event_detail"],
        work_assignment_id=e["work_assignment_id"],
        dwr_id=e["dwr_id"],
        jump_to_next_casing=tr.get("jump_to_next_casing", False)
    )
    return evt
```

---

# 5. Dynamic Casing State Engine <a name="casing-engine"></a>

Handles:

* **Cut casing**
* **Jump to next casing**
* **Determining active casing string at any depth**

## 5.1 Applying a cut

```python
def apply_cut_casing(state, depth_ft):
    # find innermost casing present at depth
    candidates = [cs for cs in state if cs.top_ft <= depth_ft <= cs.bottom_ft]
    candidates = [cs for cs in candidates
                  if not cs.removed_to_depth_ft or depth_ft > cs.removed_to_depth_ft]

    if not candidates:
        return
    
    inner = min(candidates, key=lambda cs: cs.od_in)
    inner.removed_to_depth_ft = depth_ft
```

## 5.2 Selecting active casing at depth

```python
def get_active_casing_at_depth(state, depth_ft):
    present = [cs for cs in state if cs.top_ft <= depth_ft <= cs.bottom_ft]

    present = [cs for cs in present
               if not cs.removed_to_depth_ft or depth_ft > cs.removed_to_depth_ft]

    return min(present, key=lambda cs: cs.od_in)
```

---

# 6. W-3 Builder Pipeline <a name="w3builder"></a>

## 6.1 Main Function

```python
def build_w3(events, w3a):
    casing_state = deepcopy(w3a.casing_program)
    events = sorted(events, key=lambda e: (e.date, e.start_time))

    # 1. Update casing state
    for ev in events:
        if ev.event_type == "cut_casing" or ev.jump_to_next_casing:
            apply_cut_casing(casing_state, ev.depth_bottom_ft or ev.depth_top_ft)
        else:
            depth = ev.depth_bottom_ft or ev.perf_depth_ft
            ev.casing_string = get_active_casing_at_depth(casing_state, depth).name

    # 2. Group plugs
    plugs = group_events_into_plugs(events)

    # 3. Build plug table
    plug_rows = build_plug_rows(plugs, casing_state)

    # 4. Build casing record
    casing_record = build_casing_record(casing_state)

    # 5. Perf table
    perfs = build_perforation_table(events, w3a)

    # 6. Remarks
    remarks = "\n".join([f"{e.date} – {e.raw_event_detail}" for e in events])

    # 7. return final form
    return W3Form(
        header=w3a.header,
        plugs=plug_rows,
        casing_record=casing_record,
        perforations=perfs,
        duqw={"top": w3a.duqw_top_ft, "bottom": w3a.duqw_bottom_ft},
        remarks=remarks
    )
```

---

# 7. W-3 Form Output (Schema) <a name="w3form"></a>

### Example response:

```jsonc
{
  "status": "success",
  "w3": {
    "header": {...},
    "plugs": [
      {
        "plug_number": 1,
        "date": "2025-11-10",
        "pipe_size": "5.5\"",
        "depth_bottom": 6525,
        "toc_calc": 6500,
        "toc_measured": null,
        "sacks": 40,
        "cement_class": "C"
      }
    ],
    "casing_record": [...],
    "perforations": [...],
    "remarks": "11/10/25 – Plug 1 squeezed 40 sx class C...",
    "duqw": {"top": 3000, "bottom": 3500}
  }
}
```

---

# 8. Future Extensions (Post-MVP) <a name="future"></a>

* **LLM-powered event extraction** (handle arbitrary platforms beyond pnaexchange)
* **W-3A PDF parser** → structured W-3A form
* **Standalone RegulAgent ingestion** (manual text logs)
* **Tenant-specific language models**
* **Direct RRC submission workflow**

---

# 🎉 MVP Summary

**pnaexchange**
✔ Identifies RRC-required events
✔ Provides structured fields
✔ Supplies transformation rules (including cut casing)
✔ Sends payload + W-3A reference to RegulAgent

**RegulAgent**
✔ Normalizes events → W3Event
✔ Processes cut casing
✔ Applies geometry engine
✔ Groups plugs
✔ Builds W-3 rows, casing record, perf list, DUQW, remarks
✔ Returns complete W-3

This architecture is reliable today and extensible tomorrow.

---

If you want a **matching architectural diagram**, **OpenAPI spec**, or **Python code scaffolding**, I can append that as a second file.
