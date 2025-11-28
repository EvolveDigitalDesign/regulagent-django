# ✅ W-3 from pnaexchange - READY FOR PHASE 1

**Status:** Implementation plan complete with CORRECT Django architecture  
**Date:** 2025-11-26  
**Total Effort:** ~13-19 days (10-14 MVP + 3-5 auth)

---

## Architecture: Proper Django Separation

```
apps/public_core/
├── models/
│   ├── w3_event.py          [NEW] Dataclasses: W3Event, Plug, CasingStringState
│   └── w3_form.py           [NEW] Optional ORM for persistence
│
├── serializers/
│   └── w3_from_pna.py       [NEW] Request/response validation
│
├── services/
│   ├── w3_extraction.py     [NEW] extract_w3a_from_pdf()
│   ├── w3_mapper.py         [NEW] normalize_pna_event()
│   ├── w3_casing_engine.py  [NEW] apply_cut_casing(), get_active_casing()
│   ├── w3_formatter.py      [NEW] build_plug_row(), group_events()
│   └── w3_builder.py        [NEW] W3Builder orchestrator
│
└── views/
    └── w3_from_pna.py       [NEW] POST /api/w3/build-from-pna/
```

**Clean separation:** Models (data) → Services (logic) → Serializers (validation) → Views (API)

---

## 8-Phase Implementation Roadmap

| Phase | File(s) | Days | Task |
|-------|---------|------|------|
| 1 | `models/w3_event.py` | 1 | Data model classes (dataclasses) |
| 2 | `services/w3_extraction.py` | 1 | W-3A PDF → JSON via OpenAI |
| 3 | `services/w3_casing_engine.py` | 1 | Casing state logic (cuts, active casing) |
| 4 | `services/w3_mapper.py` | 0.5 | Event normalization (input_values → fields) |
| 5 | `services/w3_formatter.py` | 1.5 | Plug grouping, row building |
| 6 | `services/w3_builder.py` | 1 | Orchestrator (main build flow) |
| 7 | `serializers/w3_from_pna.py` | 0.5 | Request/response validation |
| 8 | `views/w3_from_pna.py` + tests | 2 | API endpoint + testing |

**Total MVP:** 8-10 days

---

## Phase 1: Data Models (TODAY)

### New File: `apps/public_core/models/w3_event.py`

Create dataclasses for:
- `CasingStringState` - (name, od_in, top_ft, bottom_ft, removed_to_depth_ft)
- `W3Event` - (event_type, date, depths, materials, tracking info)
- `Plug` - (plug_number, list of W3Events)
- `W3Form` - (header, plugs, casing_record, perforations, duqw, remarks)

Full code in `IMPLEMENTATION_PLAN.md`.

### Optional File: `apps/public_core/models/w3_form.py`

Django ORM model for persisting W-3 results (like PlanSnapshot):
- `W3FormRecord` - stores generated W-3 JSON + metadata

Not required for MVP (can just return JSON from API).

---

## Request/Response Example

### From pnaexchange
```json
POST /api/w3/build-from-pna/
Authorization: Bearer {jwt_token}

{
  "well": {
    "api_number": "42-501-70575",
    "well_name": "Test Well",
    "operator": "Diamondback E&P LLC",
    "well_id": 36
  },
  "subproject": {"id": 96, "name": "Well Plug - 09-11-2025"},
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
  "w3a_reference": {"type": "regulagent", "w3a_id": 123}
}
```

### From RegulAgent
```json
{
  "status": "success",
  "w3": {
    "header": {
      "api_number": "42-501-70575",
      "well_name": "Test Well",
      "rrc_district": "08A"
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
        "sacks": 40,
        "cement_class": "C"
      }
    ],
    "casing_record": [...],
    "perforations": [...],
    "duqw": {...},
    "remarks": "11/10/25 – Plug 1 Squeezed 40 sx class C from 6525 to 6500'"
  },
  "timestamp": "2025-11-26T14:30:00Z"
}
```

---

## Authentication (pnaexchange ↔ RegulAgent)

**No new API key system needed** - both use JWT:

1. **RegulAgent:** Create service account `pnaexchange-service@regulagent.com`
2. **pnaexchange:** Store encrypted credentials in `IntegrationProfile`
3. **On W-3 request:**
   - RegulAgentService obtains JWT via `POST /api/token/`
   - Caches token (55 min, auto-refresh on 60-min expiry)
   - POSTs to `/api/w3/build-from-pna/` with `Authorization: Bearer {token}`

Full details in `W3_PNAEXCHANGE_AUTHENTICATION_INTEGRATION.md` (in docs/PNAtoW3/).

---

## Key Features

✅ **Reuses existing code:**
- `_parse_size()` from w3a_from_api.py
- `_build_additional_operations()` from w3a_from_api.py
- `extract_json_from_pdf()` pattern from openai_extraction.py
- JWT infrastructure (both systems have)

✅ **Proper Django patterns:**
- Models in `models/`
- Serializers in `serializers/`
- Business logic in `services/`
- API endpoints in `views/`

✅ **Secure authentication:**
- Encrypted credentials (not hardcoded)
- Token caching with expiry checks
- Audit logging
- Per-tenant rate limiting (optional)

---

## Documentation

| File | Purpose |
|------|---------|
| `IMPLEMENTATION_PLAN.md` | Full architecture + all code templates |
| `READY_FOR_PHASE_1.md` | This file - status & next steps |
| `W3_PNAEXCHANGE_AUTHENTICATION_INTEGRATION.md` | JWT setup + pnaexchange integration |

---

## Next Steps

### Today: Phase 1 (1 day)
- [ ] Create `models/w3_event.py` with 4 dataclasses
- [ ] Run migrations (if using ORM model)
- [ ] Commit & test imports

### Tomorrow: Phase 2 (1 day)
- [ ] Create `services/w3_extraction.py`
- [ ] Test W-3A PDF extraction

### Then: Phases 3-8 (7-9 days)
- Sequential development
- Check-ins after each phase
- Integration testing with sample pnaexchange data

---

## 5 Open Questions (Clarify Before Phase 2)

1. **W-3A Storage:** Which model stores W-3A forms in RegulAgent?
2. **Event Types:** Complete list of valid pnaexchange event_type values?
3. **Plug Grouping:** Cluster by plug_number? Date? Depth? Combination?
4. **Perforation Updates:** Can events change perf status (open→squeezed)?
5. **Material Fallbacks:** If cement class/sacks missing, use defaults or error?

---

## Success Criteria

✅ W-3 builder endpoint at `POST /api/w3/build-from-pna/`  
✅ JWT authentication required (bearer token)  
✅ W-3 JSON output with all required sections  
✅ Dynamic casing state handling cuts correctly  
✅ No hardcoded credentials or API keys  
✅ Audit logging for all requests  
✅ End-to-end tested with real pnaexchange data  

---

**Ready to begin Phase 1?**

See `IMPLEMENTATION_PLAN.md` for complete code templates.

