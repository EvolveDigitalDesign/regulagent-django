# W-3 from pnaexchange - Implementation Summary

**Status:** ✅ Conceptual Plan Complete  
**Updated:** 2025-11-26  
**Effort:** ~10-14 days MVP  
**Approach:** Sequential phases (user preference)

---

## Quick Reference

### What We're Building
A **W-3 Form Generator** that takes:
- **pnaexchange events** (actual field work records)
- **W-3A reference** (either RegulAgent ID or PDF)

And outputs:
- **W-3 Form** (JSON) ready for RRC submission

### Architecture

```
pnaexchange Payload
    ↓
Load W-3A (DB or PDF via OpenAI)
    ↓
Normalize Events → W3Event objects
    ↓
Build Dynamic Casing State (handle cuts)
    ↓
Group Events into Plugs
    ↓
Format W-3 Rows (depths, pipe, cement, materials)
    ↓
Return W3Form (JSON)
```

---

## File Structure

```
apps/public_core/
├── services/rrc/w3/
│   ├── __init__.py
│   ├── models.py              # W3Event, Plug, CasingStringState
│   ├── mapper.py              # normalize_pna_event()
│   ├── extraction.py          # extract_w3a_from_pdf()
│   ├── casing_engine.py       # apply_cut_casing(), get_active_casing_at_depth()
│   ├── formatter.py           # build_plug_row(), group_events_into_plugs()
│   └── builder.py             # W3Builder class (orchestrator)
│
├── views/
│   └── w3_from_pna.py         # W3FromPnaView endpoint
│
└── serializers/
    └── (update for W3FromPnaRequest/Response)
```

---

## Key Implementation Details

### 1. W-3A PDF Extraction
```python
# File: apps/public_core/services/rrc/w3/extraction.py

def extract_w3a_from_pdf(pdf_path: str) -> Dict[str, Any]:
    """
    Extract W-3A from PDF using OpenAI.
    Reuses extract_json_from_pdf(path, doc_type="w3a")
    
    Returns JSON with:
    - header (API, well name, operator, RRC district, county)
    - casing_record (strings, sizes, depths, cement tops, removals)
    - perforations (intervals, formation, status)
    - plugging_proposal (plugs in plan)
    - duqw (groundwater protection depth)
    - remarks
    """
```

**Reuses existing pattern:**
- Same `extract_json_from_pdf()` function from `openai_extraction.py`
- Just add "w3a" as a new doc_type option
- Provide custom extraction prompt for W-3A schema

### 2. Dynamic Casing State Engine
```python
# File: apps/public_core/services/rrc/w3/casing_engine.py

def apply_cut_casing(casing_state, depth_ft):
    """Mark innermost casing as cut at depth_ft"""
    
def get_active_casing_at_depth(casing_state, depth_ft):
    """Get active (innermost) casing string at given depth"""
```

**Logic:**
- Track which casing string is "active" at each depth
- Handle casing cuts (mark as `removed_to_depth`)
- Return the innermost available casing

### 3. Event Normalization
```python
# File: apps/public_core/services/rrc/w3/mapper.py

def normalize_pna_event(event: dict) -> W3Event:
    """
    Convert pnaexchange event → W3Event
    
    Maps input_values dict indices to structured fields:
    - input_values["1"] → plug_number
    - input_values["3"] → cement_class
    - input_values["4"] → depth_bottom_ft
    - input_values["5"] → depth_top_ft
    - input_values["6"] → sacks
    - input_values["7"] → pressure_psi
    """
```

### 4. W3Builder Orchestrator
```python
# File: apps/public_core/services/rrc/w3/builder.py

class W3Builder:
    def __init__(self, w3a_form: dict):
        # Load W-3A structure (casing program, DUQW, etc.)
        
    def build_w3_form(self, raw_events: List[dict]) -> Dict[str, Any]:
        # 1. Normalize events
        # 2. Update casing state (apply cuts)
        # 3. Group plugs
        # 4. Build plug rows
        # 5. Build casing record, perf table
        # 6. Format final W-3
```

### 5. API Endpoint
```python
# File: apps/public_core/views/w3_from_pna.py

class W3FromPnaView(APIView):
    def post(self, request):
        # 1. Validate request
        # 2. Load W-3A (DB or extract from PDF)
        # 3. Create W3Builder
        # 4. Build W-3 form
        # 5. Return JSON response
```

---

## Reusable Components

✅ **From `w3a_from_api.py`:**
- `_parse_size()` (lines 764-787) - size parsing
- `_build_additional_operations()` (lines 41-93) - format operations

✅ **From `openai_extraction.py`:**
- `extract_json_from_pdf(path, doc_type)` - PDF extraction pattern
- Prompt building utilities

✅ **From `policy_kernel.py`:**
- Materials calculation (optional enhancement)
- Merge adjacent plugs logic (optional)

---

## Implementation Phases

| Phase | Task | Days | Files |
|-------|------|------|-------|
| 1 | File structure + data models | 1 | models.py |
| 2 | W-3A extraction via OpenAI | 1 | extraction.py |
| 3 | Casing state engine | 1 | casing_engine.py |
| 4 | Plug grouping + formatting | 2 | formatter.py |
| 5 | W3Builder orchestrator | 1 | builder.py |
| 6 | API view + serializers | 1.5 | w3_from_pna.py |
| 7 | Integration + error handling | 1 | views/, services/ |
| 8 | Testing + refinement | 2 | tests/ |
| **Total** | | **~10-14 days** | |

---

## Request/Response Example

### Request
```json
{
  "well": {
    "api_number": "42-501-70575",
    "well_name": "Test Well",
    "operator": "Diamondback E&P LLC",
    "well_id": 36
  },
  "subproject": {
    "id": 96,
    "name": "Well Plug - 09-11-2025"
  },
  "events": [
    {
      "date": "2025-11-10",
      "event_type": "Set Surface Plug",
      "event_detail": "Plug 1 Squeezed 40 sx class C from 6525 to 6500",
      "input_values": {"1": "1", "3": "c", "4": "6525", "5": "6500", "6": "40", "7": "13 psi"},
      "transformation_rules": {"jump_to_next_casing": false},
      "work_assignment_id": 175,
      "dwr_id": 167
    }
  ],
  "w3a_reference": {
    "type": "regulagent",
    "w3a_id": 123
  }
}
```

### Response
```json
{
  "status": "success",
  "w3": {
    "header": {
      "api_number": "42-501-70575",
      "well_name": "Test Well",
      "operator": "Diamondback E&P LLC"
    },
    "plugs": [
      {
        "plug_no": 1,
        "date": "2025-11-10",
        "type": "cement_plug",
        "from_ft": 6525,
        "to_ft": 6500,
        "pipe_size": "5.5\"",
        "toc_calc": 6500,
        "toc_measured": null,
        "sacks": 40,
        "cement_class": "C",
        "additional": ["Squeeze cement through perforations"]
      }
    ],
    "casing_record": [...],
    "perforations": [...],
    "duqw": {"top": 3000, "bottom": 3500},
    "remarks": "11/10/25 – Plug 1 Squeezed 40 sx class C..."
  }
}
```

---

## Dependencies & Integration Points

### New Dependencies
- None (uses existing OpenAI integration)

### Existing Services Used
- `openai_extraction.extract_json_from_pdf()`
- `w3a_from_api._parse_size()`
- `w3a_from_api._build_additional_operations()`

### Database Queries
- Load W-3A by ID from (TBD: identify model)
- Create W3Event records (optional)

---

## Open Questions

1. **W-3A Storage Model:** Where/how are W-3A forms stored?
   - Separate W3AForm model?
   - ExtractedDocument records?
   - PlanSnapshot payloads?

2. **Event Type Values:** What are valid pnaexchange event_type strings?
   - Complete list needed for mapper

3. **Plug Grouping Heuristics:** How should events be clustered?
   - By plug_number if provided?
   - By temporal proximity (same day)?
   - By depth proximity (< 500 ft)?

4. **Perforation Updates:** Can events change perforation status?
   - E.g., "Squeezed perf" changes from "open" to "squeezed"?

5. **Material Fallbacks:** If pnaexchange missing data:
   - Cement class, sacks, pressure?
   - Use defaults or error?

---

## Next Steps

1. ✅ **Review this document** - approve architecture
2. ⏳ **Answer the 5 questions** - clarify requirements
3. 🚀 **Approve Phase 1** - start data models
4. 📋 **Proceed sequentially** per phase plan

See full analysis: `W3_PNAEXCHANGE_IMPLEMENTATION_ANALYSIS.md`

