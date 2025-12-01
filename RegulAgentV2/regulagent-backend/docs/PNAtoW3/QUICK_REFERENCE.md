# W-3 from pnaexchange - Quick Reference

**Status:** ✅ Ready for Phase 1  
**Architecture:** Proper Django separation of concerns  
**Effort:** 13-19 days total

---

## File Structure (RegulAgent)

```
apps/public_core/
├── models/w3_event.py              [NEW] Dataclasses
├── serializers/w3_from_pna.py      [NEW] Request/response validation
├── services/
│   ├── w3_extraction.py            [NEW] PDF extraction
│   ├── w3_mapper.py                [NEW] Event normalization
│   ├── w3_casing_engine.py         [NEW] Casing state
│   ├── w3_formatter.py             [NEW] Plug formatting
│   └── w3_builder.py               [NEW] Orchestrator
└── views/w3_from_pna.py            [NEW] API endpoint
```

---

## Phase Breakdown

| # | File | Days | What |
|---|------|------|------|
| 1 | `models/w3_event.py` | 1 | Dataclasses (W3Event, Plug, CasingStringState, W3Form) |
| 2 | `services/w3_extraction.py` | 1 | extract_w3a_from_pdf() via OpenAI |
| 3 | `services/w3_casing_engine.py` | 1 | apply_cut_casing(), get_active_casing_at_depth() |
| 4 | `services/w3_mapper.py` | 0.5 | normalize_pna_event() - map input_values to fields |
| 5 | `services/w3_formatter.py` | 1.5 | group_events_into_plugs(), build_plug_row() |
| 6 | `services/w3_builder.py` | 1 | W3Builder orchestrator class |
| 7 | `serializers/w3_from_pna.py` | 0.5 | Request/response serializers |
| 8 | `views/w3_from_pna.py` + tests | 2 | API endpoint + integration tests |

**Total: 8-10 days MVP**

---

## Endpoint

```
POST /api/w3/build-from-pna/
Authorization: Bearer {jwt_token}
Content-Type: application/json

{
  "well": {api_number, well_name, operator, well_id},
  "subproject": {id, name},
  "events": [{date, event_type, input_values, ...}],
  "w3a_reference": {type: "regulagent"|"pdf", w3a_id: OR w3a_file:}
}

Response:
{
  "status": "success",
  "w3": {
    "header": {...},
    "plugs": [{plug_no, date, type, from_ft, to_ft, sacks, ...}],
    "casing_record": [...],
    "perforations": [...],
    "duqw": {...},
    "remarks": "..."
  }
}
```

---

## Key Mappings (pnaexchange → W3Event)

From `input_values` dict:
- `"1"` → `plug_number`
- `"3"` → `cement_class` (uppercase)
- `"4"` → `depth_bottom_ft` (float)
- `"5"` → `depth_top_ft` (float)
- `"6"` → `sacks` (float)
- `"7"` → `pressure_psi` (parse "13 psi")

From `transformation_rules`:
- `jump_to_next_casing` → signals casing cut

---

## Reusable Code

- ✅ `_parse_size()` - fractional notation (w3a_from_api.py:764-787)
- ✅ `_build_additional_operations()` - operation formatting (w3a_from_api.py:41-93)
- ✅ `extract_json_from_pdf()` - PDF extraction pattern (openai_extraction.py)
- ✅ JWT infrastructure - already exists both systems

---

## Authentication (Simple)

1. RegulAgent: Create service account `pnaexchange-service@regulagent.com`
2. pnaexchange: Store encrypted credentials in `IntegrationProfile`
3. On request: Get JWT token (cached 55 min) → Include in Bearer header

**No new API key system needed** - just JWT!

---

## Documentation Files

- **IMPLEMENTATION_PLAN.md** - Full code templates for all 8 phases
- **READY_FOR_PHASE_1.md** - Status & next steps
- **W3_PNAEXCHANGE_AUTHENTICATION_INTEGRATION.md** - Auth details
- **QUICK_REFERENCE.md** - This file

---

## Proceed with Phase 1?

Create `apps/public_core/models/w3_event.py` with:
- `CasingStringState` dataclass
- `W3Event` dataclass
- `Plug` dataclass
- `W3Form` dataclass

See IMPLEMENTATION_PLAN.md for exact code.


