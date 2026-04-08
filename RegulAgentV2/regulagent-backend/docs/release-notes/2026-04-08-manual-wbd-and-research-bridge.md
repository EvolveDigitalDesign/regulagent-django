# Manual Wellbore Diagrams + Research-to-Well Bridge — April 8, 2026

## Overview

This release adds the ability to **manually create wellbore diagrams** and fixes a critical disconnect where research session data was not flowing into well components. Users can now research a well, have that data automatically populate well components, and then create or customize wellbore diagrams — all from the well detail page.

---

## New Feature: Manual Wellbore Diagrams

### What It Does

You can now build wellbore diagrams by hand for any well, without needing a plan or W3 wizard session. Three diagram types are available:

- **Current** — shows the well's current state (casing, formations, perforations, tools — no plugs)
- **Planned** — shows the well geometry plus a proposed plugging program
- **As-Plugged** — shows the well geometry plus actual plugs that were placed

### How to Use It

1. Navigate to any well detail page and click the **Diagrams** tab
2. Click **Create New Diagram**
3. The editor opens with a **split-panel layout** — form on the left, live SVG preview on the right

**If the well has been researched**, all extracted data (casings, formations, perforations, tubing, tools) will **auto-populate** from the regulatory documents. You can then modify anything.

**If you want to start from scratch**, enter an API14 and click **Load from Well** to pull in whatever data exists, or manually add rows.

### What You Can Add

- **Casing strings** — string type, OD, top/bottom depth, hole size, cement top
- **Formation tops** — formation name, top depth, base depth
- **Production perforations** — top/bottom depth
- **Tubing** — size, top/bottom depth
- **Existing tools** — CIBP, packer, retainer, DV tool, straddle packer
- **Historic cement jobs** — perf & squeeze, perf & circulate, spot & dump bail
  - Measured top, measured bottom, tagged top of cement, sacks
  - **Through which casing** — tells the diagram where to draw the cement in the annulus
  - **New/Existing toggle** — "Existing" cement renders as gray (current state), "New" cement renders as green (planned work)
- **Plug steps** (planned type) — cement plug, bridge plug, perf & squeeze, perf & circulate, balanced plug
  - Plug type, formation, through-casing, sacks, cement class, purpose
- **As-plugged plugs** — plug number, type, tagged depth, placement method, WOC hours, formation

### Live Preview

The diagram updates in real-time as you fill in data. The same SVG renderers used for plan-generated and reconciliation diagrams are used here — what you see is what you get.

### Save and Export

- Diagrams are saved to the server and appear in the **Diagrams** tab on the well detail page
- **PDF export** is available from the editor (same format as plan WBDs)
- Saved diagrams can be edited at any time from `/manual-wbd` or the well detail Diagrams tab

### Visibility Toggles

Same controls as plan diagrams — you can toggle on/off: casing, tubing, perforations, formation tops, new cement plugs, existing cement, bridge plugs, and existing tools.

---

## Fix: Research Data Now Flows Into Well Components

### What Was Broken

When you ran a research session for a well and it indexed 32 documents, that data was trapped in the research/RAG pipeline. The well's **components endpoint** (which feeds the wellbore diagram) returned empty because:

1. **API number format mismatch** — Research stores documents with dashed format (e.g., `30-025-37069`) but the component extractor searched for normalized format (`30015286920000`). They never matched.
2. **RRC-only extraction** — The component extractor re-scraped the RRC website, which doesn't work for NM wells. It never used the documents already extracted by research.
3. **No NM document type handling** — C-105 (completion report), C-103 (plugging notice), and C-101 (permit) were not mapped to well components.

### What We Fixed

- **Suffix matching** — If an exact API match fails, the system now matches on the last 8 digits (same logic used by the research session cache). Documents created by research are found regardless of format.
- **NM document types** — Added handlers for:
  - **C-105** → casings, liners, formation tops, perforations (from `perforation_record` with `interval_top_ft`/`interval_bottom_ft`)
  - **C-103** → casings (from `casing_program`), plug records, perforations
  - **C-101** → casings (from `casing_record`, using `setting_depth_ft` for shoe depth)
  - **W-3 / W-3A** → plug records
- **Deduplication** — When multiple documents describe the same casing string, only the most authoritative source is kept: C-105 (completion report) wins over C-103, which wins over C-101. This prevents the "7 casings when there should be 3" problem.

### Verification

For API 30-025-37069 (NM well with a research session):
- **Before:** 0 components, empty wellbore diagram
- **After:** 6 components — 3 casings (surface 13.375" to 729', intermediate 9.625" to 5235', production 7" to 12460'), 1 liner (4.5" at 12170-14400'), 2 perforations (8642-8691' and 13837-14035')

---

## Fix: Research Sessions No Longer Disappear

### What Was Broken

Research sessions vanished when you navigated away from the Research page because:

1. **No list endpoint** — `GET /api/research/sessions/` returned 405 (Method Not Allowed). The backend only had a POST handler. Past sessions couldn't be listed.
2. **No URL persistence** — The session ID lived only in React component state. When you navigated away and came back, the component re-mounted with empty state.
3. **No bridge to wells** — There was no way to see research sessions from the well detail page or start research for a specific well.

### What We Fixed

- **GET endpoint** — `GET /api/research/sessions/` now returns your tenant's sessions. Supports `?well_api14=`, `?api_number=`, and `?status=` filters.
- **URL deep-linking** — Research sessions now live in the URL: `/research/{sessionId}`. Navigating directly to that URL loads the session. Bookmarkable. Shareable.
- **Auto-resume** — When you create or resume a session, the URL updates. Coming back to `/research` shows the past sessions list. Clicking any session opens it.
- **Research tab on Well Detail** — Every well now has a **Research** tab showing past research sessions for that well (status, document count, date). Click **Start Research** to launch a new session with the well's API pre-filled. Click **Open** on an existing session to jump straight to the chat.

---

## New Navigation

- **Manual WBD** sidebar item → `/manual-wbd` (list of all saved diagrams)
- **Diagrams tab** on well detail pages → list of manual WBDs for that well + create new
- **Research tab** on well detail pages → list of research sessions for that well + start new

---

## API Changes

### New Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tenant/manual-wbd/` | List manual WBDs (filter: `?api14=`, `?diagram_type=`) |
| POST | `/api/tenant/manual-wbd/` | Create manual WBD |
| GET | `/api/tenant/manual-wbd/{id}/` | Retrieve single WBD |
| PATCH | `/api/tenant/manual-wbd/{id}/` | Update title/diagram_data |
| DELETE | `/api/tenant/manual-wbd/{id}/` | Soft-delete |

### Modified Endpoints

| Method | Path | Change |
|--------|------|--------|
| GET | `/api/research/sessions/` | **New** — was 405, now returns session list with filters |
| GET | `/api/tenant/wells/{api14}/components/` | Now returns data for NM wells (C-105/C-103/C-101 sources) |

---

## Questions?

If you run a research session and the well's components endpoint still returns empty, try navigating to the well detail page and clicking **Load from Well** in the manual WBD editor — this triggers the component extraction. If you see duplicate casings or missing data, please report the API number and what you're seeing vs. what you expect.
