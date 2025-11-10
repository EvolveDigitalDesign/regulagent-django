## Consolidated AI Roadmap: Compliance‑First, Precedent‑Informed Planning

### Purpose and principles
- **Compliance‑first**: Always produce a regulator‑faithful standard plan. AI augments but never silently overrides compliance.
- **Precedent‑informed**: Use historical, nearby, and operator‑similar wells to suggest adjustments with evidence and confidence.
- **Dual data model**: Maintain both structured ORM truth and a semantic vector layer for retrieval and learning.
- **Explainable and safe**: Every AI suggestion includes neighbors, similarity, and regulator outcome context; changes are user‑controlled and auditable.

### Current state (snapshot)
- Extraction → `ExtractedDocument` persisted and embedded into `DocumentVector` (1536‑d; pgvector).
- Planning kernel generates deterministic W‑3A plans with materials; policy overlays applied; strong observability.
- Vector search is stored but not yet exposed via APIs; structured ORM sync of extracted JSON is not fully utilized for retrieval.

### Vector layer strategy (why and how)
- **Why**: PDFs and remarks are heterogeneous; embeddings align similar wells and regulator precedents when structured fields are sparse.
- **What this enables**: “Similar wells” retrieval by operator, field, district, formation, geometry, geospatial radius, and time window to propose steps/materials/tagging already accepted by regulators locally.
- **Learning over time**: As more extractions and outcomes accrue, retrieval quality and recommendations improve without weakening compliance guarantees.

### What to embed (beyond today)
- **Well context**: operator/customer, API14, district/county/field, lat/lon, spud/submit dates, well type.
- **Geometry signature**: casing IDs/ODs, tubing OD, surface/intermediate/production shoes, UQW base.
- **Plan/steps**: step types, intervals, sacks, recipes, tagging, CIBP presence, merge behavior; per‑step summaries as chunks.
- **Regulatory outcomes**: approved/rejected, turnaround time, revision count, reviewer notes summary.
- **Provenance**: source docs, overlay IDs, kernel version, extraction model tag.
- **Tenant boundaries**: tenant/org IDs to enforce isolation.

### Retrieval patterns
- **Similar‑well planning assist**
  - Filters: state → district/county/field → operator/customer → date window (e.g., 12–24 months) → geospatial radius (e.g., ≤0.5 mi).
  - Ranking: weighted blend of vector similarity to a “query well profile,” structured feature match (formation/geometry), recency, and same‑operator boost.
  - Output: top‑k neighbors with step/material deltas and acceptance outcomes.
- **Materials baselining**: For each planned step, retrieve neighbors to suggest recipe/sacks ranges and tag‑wait hours proven locally.
- **Regulatory precedent explorer**: Surface overlays/citations/intervals historically accepted for similar wells; attach confidence and reasons.
- **Anomaly detection**: Flag when planned steps materially diverge from local precedent or neighbor materials.

### Guardrails and modes
- **Compliance floor**: Always emit the standard plan; suggestions are deltas with explicit reasons and toggles.
- **Explainability**: Show neighbors, similarity scores, and outcomes for each suggestion.
- **Risk scoring**: Score by evidence strength (k, diversity, recency, operator match); gate below threshold.
- **Tenant/privacy**: Strict tenant filters; cross‑tenant only for public data or de‑identified aggregates.
- **Fallbacks**: If no strong neighbors, keep the standard plan unchanged.

### Data model extensions
- `WellRegistry`: add `lat`, `lon`, `operator_name`, `spud_date`, `well_type`.
- `PlanOutcome`: (api, submitted_at, approved_at, revisions, status, reviewer_notes_summary_embedding).
- `DocumentVector.metadata`: `{ tenant_id, operator, district, county, field, lat, lon, step_types, materials, approval_status, overlay_id, kernel_version }`.

### Indexing and infra
- **ANN**: pgvector HNSW for embeddings; maintain cosine distance index.
- **Filters**: btree/GiST on tenant, operator, district/county/field, dates.
- **Geospatial**: PostGIS for radius filters (or Haversine prefilter → ANN rerank if PostGIS unavailable).

### Online planning workflow (RAG for plans)
1) Build a “query profile” text from identity, geometry, intervals, formations; embed.
2) Retrieve k similar wells with structured filters and ANN.
3) Aggregate neighbor features by step type to propose materials/interval/tagging adjustments with confidence.
4) Compare against the standard plan; produce “AI suggestions” with reasons and a risk score.
5) If accepted by user, annotate plan with “precedent‑supported adjustment” and log feedback.

### Learning loop and feedback
- Capture user actions (accept/reject suggestions, manual edits, resubmittals).
- Ingest regulator outcomes and reviewer notes; embed summaries.
- Compute uplift metrics (approval rate, time‑to‑approval, sacks variance) vs baseline.
- Periodically adjust retrieval/ranking weights; keep kernel deterministic and policy‑driven.

### Metrics / KPIs
- Approval rate and time‑to‑approval vs baseline by district/field/operator.
- Share of plans with ≥N strong neighbors and of steps using precedent‑supported adjustments.
- Sacks/volume error against field usage where available.
- Retrieval quality: neighbor acceptance rate and diversity.

### Roadmap (sequential)
- **Phase 1: Read APIs**
  - “Find similar wells” endpoint with filters + ANN; return neighbors and outcome stats.
  - “Precedent suggestions” endpoint returning per‑step deltas with confidence and explain.
- **Phase 2: Planner integration**
  - Show AI suggestions alongside the standard plan; apply behind a toggle with audit trail.
- **Phase 3: Outcomes capture**
  - Ingest approval outcomes and reviewer notes; embed; start KPI dashboards.

### Operating posture
- Default to strict compliance output; allow AI‑assisted adjustments only with clear evidence and explicit user consent.
- Keep provenance and auditability first‑class: sources, overlays, kernel version, and neighbor identities.


### Conversational planning (chat‑first)
#### Plan modification engine (generic, selector‑based)
- **Selectors**: target steps by `step_id`, `type`, formation name, depth ranges, or JSONPath‑like predicates.
- **Composable ops**: `set`, `inc`/`dec`, `add`, `remove`, `move`, `merge`, `replace`, `tag`.
- **Domain shortcuts**: high‑level intents (e.g., combine plugs, replace CIBP with long plug) compile into generic ops.
- **Flow**: NL request → compiler → ops → apply → re‑run validator/kernel → diff + risk.
- **Schema**: persist request (NL + ops), compiled ops, diff, and provenance in `PlanModification`; store `PlanSnapshot(kind='post_edit')`.
- **Guardrails**: compliance floor, `violations_delta`, `risk_score`, and explicit user consent for application.
- **Tenant scope**: all ops and history scoped to tenant; accepted edits mined into optional `TenantOverlayRule` proposals.
- **Conversational layer (stateful)**
  - Use OpenAI Responses/Assistants with tool calls; persist a `thread_id` per well/engagement to maintain chat history.
  - System prompt enforces compliance‑first; tools perform precise plan edits rather than free‑form changes.

- **Core tools (post‑plan operations)**
  - `get_plan_snapshot(plan_id)`: return latest plan JSON + provenance.
  - `answer_fact(question)`: resolve from structured facts + `DocumentVector` (e.g., “open hole behind production casing?”).
  - `combine_plugs(plug_ids | interval, threshold_ft?)`: merge adjacent/selected plugs; recompute materials/export.
  - `replace_cibp_with_long_plug(interval='producing')`: remove CIBP+cap; add long plug across producing interval; recompute.
  - `toggle_merge(enabled, threshold_ft)`: gate long‑plug merge behavior for the plan.
  - `recalc_materials_and_export()`: recompute sacks/totals/violations after any edit.
  - Tools report `violations_delta` and `risk_score`; block or warn per tenant policy.

- **Data model additions**
  - `ChatThread(id, tenant_id, well_id, plan_id, created_by, mode)`
  - `ChatMessage(thread_id, role, content, tool_calls, tool_results, created_at)`
  - `PlanModification(id, plan_id, thread_id, op_type, payload, diff, applied_by, applied_at, risk_score, violations_delta)`
  - `TenantPreference/TenantOverlayRule(tenant_id, trigger, adjustment, enabled, provenance)` for learned, opt‑in defaults.
  - Optional `ChatVector(thread_id, message_id, embedding, metadata)` for retrieving prior rationales.

- **Tenant learning (behavior carryover)**
  - Log accepted modifications → mine frequent, safe deltas → propose as `TenantOverlayRule` (e.g., prefer long plug instead of CIBP under constraints).
  - Apply only with explicit tenant opt‑in; show explain, neighbors, and risk score.

- **APIs**
  - `POST /api/chat/threads`: create thread for (tenant, well, plan_id).
  - `POST /api/chat/threads/{id}/messages`: user message → OpenAI + tools → assistant reply + updated plan snapshot/diff.
  - `GET /api/plans/{id}/history`: list `PlanModification`s with diffs and outcomes.

- **Guardrails**
  - Compliance floor: standard plan remains baseline; deltas explicit and reversible.
  - Each tool returns `violations_delta` and `risk_score`; block high‑risk changes by tenant policy.
  - Full audit: messages, tool calls, diffs, and rationale persisted.

- **Rollout (sequential)**
  1) Persist `ChatThread`/`ChatMessage`; implement `get_plan_snapshot` and `answer_fact`.
  2) Add `combine_plugs`, `replace_cibp_with_long_plug`, and `recalc_materials_and_export` tools.
  3) Store `PlanModification` with diffs; UI to apply/revert.
  4) Mine accepted mods → propose `TenantOverlayRule` suggestions.

### Chat APIs (spec)
- `POST /api/chat/threads`
  - Purpose: create a chat thread tied to a well and plan; returns `thread_id` to maintain history.
  - Request JSON:
    - `tenant_id` (string, required)
    - `well_id` (string, required)
    - `plan_id` (string, required)
    - `mode` (string, optional; e.g., "assistant")
    - `system_purpose` (string, optional)
  - Response JSON:
    - `thread_id` (string)
    - `created_at` (ISO timestamp)

- `POST /api/chat/threads/{thread_id}/messages`
  - Purpose: append a user message; assistant executes tools and replies; may modify plan.
  - Request JSON:
    - `role` ("user")
    - `content` (string)
    - `options` (object, optional): `{ allow_plan_changes: boolean, max_neighbors: number }`
  - Response JSON:
    - `message`:
      - `role` ("assistant")
      - `content` (string)
      - `tool_calls` (array) and `tool_results` (array)
    - `plan_update` (object, optional):
      - `plan_id` (string)
      - `diff` (JSON Patch or step‑level delta)
      - `violations_delta` (array)
      - `risk_score` (number 0..1)
    - `modification_id` (string, when plan changed)
    - `created_at` (ISO timestamp)

- `GET /api/chat/threads/{thread_id}`
  - Purpose: fetch thread metadata and recent messages.
  - Response JSON includes `{ thread, messages[] }` (paged).

- `GET /api/plans/{plan_id}/history`
  - Purpose: list `PlanModification` records with diffs and outcomes for audit.

- Security & tenancy
  - All endpoints require auth; all queries scoped to `tenant_id`.
  - Rate limiting per tenant and per thread.

### MVP (ship first)
- **Backend**
  - Solidify standard W‑3A plan generation from RRC docs with deterministic outputs.
  - Ensure all cement‑bearing steps compute sacks (finish formation plug sacks + totals).
  - Expose a simple read‑only "Similar wells" endpoint (structured filters only).
  - Persist plan artifacts; add SHA‑256 hashing and secure download.
  - Add `PlanModification` model and basic diff persistence; full generic engine ships post‑MVP.
- **Frontend**
  - Plan preview with violations/materials/export, and basic edit forms: combine selected plugs, replace CIBP with long plug, adjust intervals.
  - Minimal chat pane that issues the same edit ops and shows returned diffs.
- **Data**
  - Keep embeddings for document sections (existing) and extend `DocumentVector.metadata` with tenant/operator/field/dates for filtering.

#### Definition of done
- Initial draft generated, 2–3 common edits applied, materials/export recalculated, and artifact downloadable.
- Formation plug sacks included in `materials_totals`; missing sacks only when geometry is provably absent (with warning).
- All edits captured as `PlanModification` and are reversible.

### 4‑week delivery plan
- **Week 1: Plan integrity and materials**
  - Finish formation plug sacks; ensure `materials_totals` includes them.
  - Add `PlanModification` model + diffing and revert.
  - Frontend: plan preview polish; edit forms for combine plugs and replace CIBP with long plug.
  - Deliverable: end‑to‑end edits with recalculated materials and export.
- **Week 2: Conversational scaffolding**
  - Models/endpoints: `ChatThread`, `ChatMessage`; create/append APIs.
  - Assistant calls existing edit endpoints; audit trail persisted.
  - Frontend: chat pane in plan view; show diffs.
  - Deliverable: chat‑driven edits that mirror form‑based edits.
- **Week 3: Similar wells (read‑only) and facts Q&A**
  - Endpoint: structured‑filter neighbors; summarize precedents.
  - `answer_fact(question)` MVP over structured facts (e.g., open hole behind casing).
  - Frontend: neighbors panel; fact Q&A via chat.
  - Deliverable: precedents visible; targeted fact answers.
- **Week 4: ANN retrieval alpha + guardrails**
  - Enable ANN index on `DocumentVector`; add geospatial prefilter.
  - Assistant tool returns neighbor evidence with confidence; apply edits only on explicit user request.
  - Add risk scoring stub (k, recency, operator match) to suggestions.
  - Deliverable: suggestions with precedent evidence and risk shown.

#### Optional stretch
- Add `PlanOutcome` ingestion if approval data is available.
- Export a filing‑ready packet.

### Implementation in codebase (integration plan)
- **New app**: `apps/assistant/` (chat + plan edits + precedent suggestions)
  - `models/`: `ChatThread`, `ChatMessage`, `PlanModification` (optional `PlanOutcome`).
  - `services/`: `openai_assistant.py`, `plan_editor.py`, `precedent_retrieval.py`, `facts_qa.py`.
  - `views/`: `chat.py`, `plan_history.py`, `similar_wells.py`; `urls.py` included from `ra_config/urls.py`.
- **Kernel‑safe edits**
  - Keep `kernel.services.policy_kernel` authoritative. Drive changes via `policy.loader` overrides (`steps_overrides`), then re‑run kernel.
  - Use `materials.material_engine` as‑is; no forked math.
- **Reuse/extend**
  - Extend `apps/public_core/models/document_vector.py` metadata with tenant/operator/field/dates.
  - Consider `apps/tenant_overlay` for opt‑in `TenantPreference`/learned defaults.
- **Routing & settings**
  - Add `/api/chat/*` and `/api/plans/*` endpoints; DRF throttles per tenant/thread; auth required.
- **Migrations**
  - New models in `apps/assistant` with isolated migrations; extend `DocumentVector.metadata` via migration.
- **Guardrails**
  - Always re‑run kernel with overrides; compute `violations_delta`; block or warn per tenant policy; persist provenance.

### Baseline plan storage and comparison
- **Why**: Preserve a policy‑faithful baseline to compare user edits and measure divergence, outcomes, and uplift.
- **What to store**
  - `PlanSnapshot(id, plan_id, kind, created_at, kernel_version, overlay_id, policy_id, extraction_meta)`:
    - `kind`: `baseline` (initial standard plan from `/api/plans/w3a/from-api`) | `post_edit` | `submitted` | `approved`.
  - Persist baseline immediately after the initial API response returns.
- **How it’s used**
  - Chat threads and `PlanModification`s reference the baseline snapshot.
  - Diffs computed against baseline for audit and accuracy/efficiency analysis.
  - When approval outcomes arrive, attach to the nearest snapshot; compute metrics vs baseline.
- **API impact**
  - `/api/plans/{plan_id}/history` returns baseline + modifications + outcomes for comparison.
  - Assistant replies include whether a suggestion deviates from baseline and by how much (interval/materials/violations).

### Formation knowledge layer and completeness guarantees
- **Motivation**: W‑2 extraction may omit formation tops; 08/08A and 7C plugging books provide authoritative fallbacks by county.
- **Precedence (authoritative order)**
  1) W‑2 extracted formation tops (document truth)
  2) Field overlay formations (if field match found)
  3) County formation pack (plugging‑book derived)
  4) District‑level fallback or none (emit violation)
- **Provenance**: Each top carries `{ source: w2 | overlay_field | overlay_county | plugging_book, confidence }` and is surfaced in step `placement_basis` and `plan.notes`.
- **Loader integration**: `policy.services.loader.get_effective_policy` merges an `effective.formations` section using the precedence above; kernel consumes it deterministically and avoids duplicates.
- **Completeness pass**: Before planning, fill missing facts from `effective.formations`; when still missing, skip dependent rules and emit `FORMATION_TOPS_INCOMPLETE`.

### Automatic county formation packs (on‑demand build + pre‑warm)
- **Directory layout**: `apps/policy/packs/tx/formations/{district}/{county}.yml`
  - Schema example per county file:
    - `county: "Andrews"`
    - `district: "08A"`
    - `formations:` list of items `{ name: "San Andres", top_ft: 2750, aliases: ["SanAndres", "San-Andres"], notes: "from plugging book" }`
- **On‑demand creation**
  - When a well in a district/county is processed and `.../formations/{district}/{county}.yml` is absent:
    - Parse the district’s full plugging‑book YAML once, extract the county block, write the county file, and cache in memory.
    - Subsequent requests read the county file directly (no full‑book scan).
- **Pre‑warm command**
  - Management command: `policy.management.commands.build_formations_county_packs` with flags `--district 08A` or `--all` to generate all counties ahead of time.
  - Validation command: `policy.management.commands.validate_formations_packs` to ensure schema, aliases normalization, and coverage.
- **Loader lookup order**
  - Check county file first → field overlay → fallback to full plugging‑book (last resort) → none.
- **Name normalization**
  - Maintain an ontology of formation aliases; resolver function `get_top_ft(formation_name)` normalizes input and resolves to canonical entries.
- **Tests**
  - Golden tests for Andrews (08A) and Reagan/Sherrod (7C):
    - When W‑2 lacks tops, kernel uses county pack tops with provenance.
    - When both W‑2 and county pack present, W‑2 wins and duplicates are not emitted.


### User and API Interaction
- Ensure all files match the provided API number during `/api/plans/w3a/from-api` requests.
- Use both filename checks and content-based verification for API consistency.

### WellRegistry and ExtractedDocument Checks
- Check `updated_at` for WellRegistry and ExtractedDocuments.
- Skip processing if updates are within the last 5 business days, unless manually overridden.

### Tenant and Privacy Considerations
- Use tenant IDs or schemas for private chat histories and plan iterations.
- Split `PlanSnapshot` into public (initial/final) vs private (tenant-specific edits).

### Data Extraction
- Proceed with RRC extraction for stale or missing data.
- Ensure thorough validation and secure storage of plans and documents.

### AI Feedback and Learning
- Utilize tenant-specific accepted modifications for AI enhancement.
- Ensure anonymized learning contributes to public improvements.

