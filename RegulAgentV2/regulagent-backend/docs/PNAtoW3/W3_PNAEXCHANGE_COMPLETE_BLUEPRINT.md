# W-3 from pnaexchange - Complete Blueprint

**Status:** ✅ READY FOR IMPLEMENTATION  
**Date:** 2025-11-26  
**Total Effort:** ~13-19 days (10-14 MVP + 3-5 auth integration)

---

## Overview

Complete architecture for building W-3 forms from pnaexchange field events using RegulAgent.

```
pnaexchange                      RegulAgent
───────────────                  ─────────────
  Events                         W-3A
    ↓ (w/ JWT)                      ↓
    └─────── POST /api/w3/build-from-pna/ ───────→ W-3 Builder
                                      ↓
                              Dynamic Casing State
                                      ↓
                              Group Events → Plugs
                                      ↓
                              Build W-3 Rows
                                      ↓
                            Return W-3 JSON ←────┘
```

---

## Two Main Documents

### 1. **W3_PNAEXCHANGE_IMPLEMENTATION_ANALYSIS.md** (1042 lines)
   - Architecture deep dive
   - Data flow diagrams
   - Reusable components identification
   - 8-phase implementation roadmap
   - Code templates for each module

### 2. **W3_PNAEXCHANGE_AUTHENTICATION_INTEGRATION.md** (400+ lines)
   - JWT authentication flow
   - Service account setup
   - IntegrationProfile model
   - Credential encryption & token management
   - Celery task for async generation
   - Security best practices

---

## Quick Start (3 Questions)

### Q1: How does pnaexchange connect?
**A:** Stores RegulAgent credentials (email/password) in encrypted `IntegrationProfile`, then:
1. Obtains JWT token via `POST /api/token/`
2. Calls `POST /api/w3/build-from-pna/` with `Authorization: Bearer {token}`
3. Receives W-3 JSON response

### Q2: What needs to be built in RegulAgent?
**A:** New W-3 builder system:
```
apps/public_core/services/rrc/w3/
├── models.py          # W3Event, Plug, CasingStringState
├── mapper.py          # normalize_pna_event()
├── extraction.py      # extract_w3a_from_pdf() via OpenAI
├── casing_engine.py   # apply_cut_casing(), get_active_casing()
├── formatter.py       # build_plug_row(), group_events()
└── builder.py         # W3Builder orchestrator
```
Plus new view: `w3_from_pna.py` at `/api/w3/build-from-pna/`

### Q3: What needs to be built in pnaexchange?
**A:** Integration infrastructure:
```
apps/integrations/regulagent/
├── services.py        # RegulAgentService, RegulAgentConfig
├── models.py          # IntegrationProfile (if new)
└── management/commands/
    └── test_regulagent_connection.py

Plus:
- Settings UI for RegulAgent config
- Celery task: generate_w3_from_events()
- W-3 generation trigger in UI
```

---

## File Structure (RegulAgent)

```
regulagent-backend/
├── apps/
│   ├── public_core/
│   │   ├── services/
│   │   │   └── rrc/
│   │   │       ├── __init__.py
│   │   │       └── w3/
│   │   │           ├── __init__.py
│   │   │           ├── models.py           # 150 lines
│   │   │           ├── mapper.py           # 200 lines
│   │   │           ├── extraction.py       # 250 lines
│   │   │           ├── casing_engine.py    # 150 lines
│   │   │           ├── formatter.py        # 400 lines
│   │   │           └── builder.py          # 250 lines
│   │   └── views/
│   │       └── w3_from_pna.py              # 100 lines (new)
│   └── [existing]
└── [existing]
```

---

## Timeline: 13-19 Days

### RegulAgent MVP (10-14 days)

| Phase | Task | Days | Status |
|-------|------|------|--------|
| 1 | Data models + file structure | 1 | ⏳ Not started |
| 2 | W-3A PDF extraction (OpenAI) | 1 | ⏳ Not started |
| 3 | Casing state engine | 1 | ⏳ Not started |
| 4 | Plug grouping + formatting | 2 | ⏳ Not started |
| 5 | W3Builder orchestrator | 1 | ⏳ Not started |
| 6 | API view + serializers | 1.5 | ⏳ Not started |
| 7 | Integration + error handling | 1 | ⏳ Not started |
| 8 | Testing + refinement | 2 | ⏳ Not started |

### Authentication Integration (3-5 days)

| Phase | Task | Days | System |
|-------|------|------|--------|
| A | IntegrationProfile model + UI | 1.5 | pnaexchange |
| B | RegulAgentService class | 1 | pnaexchange |
| C | Credential encryption setup | 0.5 | pnaexchange |
| D | Celery task + trigger | 1 | pnaexchange |
| E | End-to-end testing | 1 | Both |

---

## Request/Response Example

### Request (from pnaexchange)
```json
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
      "event_detail": "Plug 1 Squeezed 40 sx class C from 6525 to 6500",
      "start_time": "20:30:00",
      "end_time": "21:30:00",
      "duration_hours": 1.0,
      "work_assignment_id": 175,
      "dwr_id": 167,
      "input_values": {
        "1": "1",
        "3": "c",
        "4": "6525",
        "5": "6500",
        "6": "40",
        "7": "13 psi"
      },
      "transformation_rules": {
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

**Headers:**
```
POST /api/w3/build-from-pna/
Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGc...
Content-Type: application/json
```

### Response (from RegulAgent)
```json
{
  "status": "success",
  "w3": {
    "header": {
      "api_number": "42-501-70575",
      "well_name": "Test Complete Well Flow",
      "operator": "Diamondback E&P LLC",
      "rrc_district": "08A",
      "county": "ANDREWS"
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
        "additional": [
          "Squeeze cement through perforations",
          "Wait 4 hr and tag TOC"
        ]
      }
    ],
    "casing_record": [
      {
        "string": "surface",
        "size_in": 13.375,
        "top_ft": 0,
        "bottom_ft": 2000,
        "shoe_depth_ft": 2000,
        "cement_top_ft": 0
      },
      {
        "string": "production",
        "size_in": 5.5,
        "top_ft": 2000,
        "bottom_ft": 8000,
        "shoe_depth_ft": 8000,
        "cement_top_ft": 5000
      }
    ],
    "perforations": [
      {
        "interval_top_ft": 5000,
        "interval_bottom_ft": 5100,
        "formation": "Spraberry",
        "status": "squeezed"
      }
    ],
    "duqw": {
      "top_ft": 3000,
      "bottom_ft": 3500,
      "formation": "Santa Rosa"
    },
    "remarks": "11/10/25 – Plug 1 Squeezed 40 sx class C from 6525 to 6500'"
  },
  "tenant_id": "uuid-of-pnaexchange-tenant",
  "timestamp": "2025-11-26T14:30:00Z"
}
```

---

## Key Implementation Details

### 1. Event Normalization (RegulAgent)
Maps pnaexchange `input_values` dict indices to W3Event fields:
- `input_values["1"]` → `plug_number`
- `input_values["3"]` → `cement_class` (normalize to uppercase)
- `input_values["4"]` → `depth_bottom_ft` (convert to float)
- `input_values["5"]` → `depth_top_ft`
- `input_values["6"]` → `sacks`
- `input_values["7"]` → `pressure_psi`

### 2. W-3A Loading (RegulAgent)
Handles two reference types:
- **`type: "regulagent"`** → Query DB (TBD which model)
- **`type: "pdf"`** → Extract via OpenAI `extract_json_from_pdf()` with doc_type="w3a"

### 3. Casing State (RegulAgent)
Dynamic tracking of casing cuts:
- Start with W-3A casing program (from header section)
- Apply cuts when events have `jump_to_next_casing=true`
- Track `removed_to_depth` for each string
- Determine active (innermost) casing at each event depth

### 4. Plug Grouping (RegulAgent)
Cluster events into logical plugs:
- By `plug_number` if provided
- Or temporal proximity (same day, adjacent depths)
- Or both

### 5. Authentication (pnaexchange ↔ RegulAgent)
Service account JWT flow:
1. pnaexchange stores encrypted credentials in `IntegrationProfile`
2. On W-3 request, RegulAgentService calls `POST /api/token/`
3. Receives JWT token (55-min cache before expiry)
4. Includes `Authorization: Bearer {token}` header
5. RegulAgent validates via `JWTAuthentication`

---

## Reusable Components

**From existing codebase:**
- ✅ `_parse_size()` (w3a_from_api.py:764-787) - size parsing
- ✅ `_build_additional_operations()` (w3a_from_api.py:41-93) - operation formatting
- ✅ `extract_json_from_pdf()` (openai_extraction.py) - PDF extraction pattern
- ✅ Casing geometry logic patterns (w3a_from_api.py:738-757)
- ✅ JWT infrastructure (both systems already have)

**No changes needed to:**
- ✅ openai_extraction.py - just add "w3a" doc_type
- ✅ w3a_from_api.py - can extract reusable utilities

---

## Deployment Checklist

### RegulAgent
- [ ] Create service account user (pnaexchange-service@regulagent.com)
- [ ] Enable JWT auth on W3FromPnaView
- [ ] Configure CORS for pnaexchange origin
- [ ] Set up logging/audit trail
- [ ] Deploy Phase 1-8 code

### pnaexchange
- [ ] Add IntegrationProfile model + encryption
- [ ] Create RegulAgentService class
- [ ] Build settings UI for RegulAgent config
- [ ] Create Celery task for W-3 generation
- [ ] Add test connection command
- [ ] Deploy auth infrastructure

### Post-Deployment
- [ ] Test service account authentication
- [ ] Run end-to-end test (event → W-3)
- [ ] Verify audit logging
- [ ] Monitor error rates

---

## Next Steps

1. ✅ **Review all 3 documents** (Implementation, Authentication, this Blueprint)
2. 🔍 **Answer 5 clarification questions** (from Implementation Analysis)
3. 👍 **Get approval** for Phase 1 (data models)
4. 🚀 **Begin implementation** sequentially [[memory:7051959]]

---

## Document Reference

| Document | Purpose | Length |
|----------|---------|--------|
| **W3_PNAEXCHANGE_IMPLEMENTATION_ANALYSIS.md** | Architecture + 8-phase roadmap | 1042 lines |
| **W3_PNAEXCHANGE_AUTHENTICATION_INTEGRATION.md** | JWT flow + service setup | 400+ lines |
| **W3_PNAEXCHANGE_SUMMARY.md** | Quick reference | 300 lines |
| **W3_PNAEXCHANGE_COMPLETE_BLUEPRINT.md** | This document | 400 lines |

---

## Questions?

**5 Key Questions Still Pending:**
1. W-3A storage model (where are they persisted?)
2. Valid pnaexchange event_type values (complete list)
3. Plug grouping heuristics (temporal? depth? both?)
4. Perforation status updates (can events change perf status?)
5. Material data fallbacks (cement class, sacks if missing?)

**Once clarified, we proceed with Phase 1 implementation.**

