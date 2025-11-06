## Existing Wellbore Awareness: Implementation Roadmap (W-3A)

### Purpose
Establish a complete, regulator-faithful W-3A plan generator that is aware of existing downhole hardware and prior cementing, integrates RRC §3.14 rules, and produces transparent, material-complete steps with traceable regulatory bases and notes.

### Outcomes
- Mechanical awareness (CIBP, packer, DV tool) prevents impossible ops and shifts plan to verification/caps where appropriate.
- Steps include placement basis, citations, verification/tagging, and computed sacks with clear geometry assumptions.
- Historical work (W-15/W-2) is reflected exactly where it should be (existing squeeze, caps), while new plugs follow policy logic.

---

## 1. Current Flow (as-is)

- Extraction → Planning Entry:
  - `apps/public_core/management/commands/plan_from_extractions.py`
    - Gathers latest W-2/GAU/W-15 payloads
    - Normalizes facts (api, district, county, shoes, UQW, sizes, producing interval, formation tops map)
    - Loads effective policy via loader and calls kernel

- Policy Loader / Overlay Merge:
  - `apps/policy/services/loader.py`
    - Merges base pack with district/county/field overlays
    - Combined overlay fallback from `tx/w3a/district_overlays/08a__auto.yml`

- Kernel + Rules + Materials:
  - `apps/kernel/services/policy_kernel.py` (orchestrates)
  - `apps/kernel/services/w3a_rules.py` (generates steps)
  - `apps/materials/services/material_engine.py` (volume → sacks)

- Overlays & Packs (references):
  - `apps/policy/packs/tx_rrc_w3a_base_policy_pack.yaml`
  - `apps/policy/packs/tx/w3a/district_overlays/08a__auto.yml`

- Extraction Services:
  - `apps/public_core/services/openai_extraction.py` (how JSON sections are structured)
  - Model: `apps/public_core/models/extracted_document.py`

---

## 2. Gaps to Close

- Missing explicit facts for existing mechanical barriers and prior cementing (CIBP, packer, DV tool; W-15 intervals/sacks) — DONE
- Tagging and verification behavior not represented (TOC tag results, required wait hours) — DONE
- Cement class selection shallow vs deep (Class C/H) not surfaced at step-level — DONE
- Placement basis / notes missing (formation transitions, shoe, perf tops) — DONE
- Squeeze step geometry and sacks not derived from W-15 when available — PARTIAL (interval/sacks to be preferred from W-15)
- CIBP cap placement lacks explicit interval when prior CIBP is present — DONE

---

## 3. Target Facts and Sources

Add or strengthen these facts in planning input (sourced in order of authority):

- Well identity/region:
  - `api14`, `district`, `county`, `field` (W-2 → `well_info`)

- Casing/tubing and shoes:
  - `surface_shoe_ft`, `intermediate_shoe_ft` (W-2 `casing_record`), `production_shoe_ft` if present
  - `prod_id_in`, `surface_id_in` (map OD → nominal ID), `stinger_od_in` (W-2 `tubing_record`)

- UQW and DUQW:
  - `uqw_base_ft` (GAU letter preferred; fallback W-2 `surface_casing_determination`)

- Producing/perf intervals:
  - `producing_interval_ft` (W-2)
  - `perfs` (extract as list of {top_ft,bottom_ft} if available)

- Formation tops:
  - `formation_tops_map` (W-2 `formation_record`; normalize names)

- Existing mechanical barriers: new fact keys
  - `existing_mechanical_barriers`: e.g., `["CIBP", "PACKER", "DV_TOOL"]`
  - `existing_cibp_ft`, `packer_ft`, `dv_tool_ft` when derivable (from W-2 remarks, schematic if present)
  - Source order: schematic > W-2 remarks/log fields

- Historical cementing: new override payloads
  - From W-15 `cementing_to_squeeze` → span(s) for squeeze
  - From W-2 remarks/rrc_remarks → detect patterns like `830 SXS` → `sacks_override`

References used: W-2 (structure + remarks), W-15 (cementing spans), GAU Letter (UQW), Plugging Book (overlay YAML), Base Policy Pack (requirements/preferences).

---

## 4. Module-by-Module Implementation Plan

### 4.1 `apps/public_core/management/commands/plan_from_extractions.py`

- Facts enrichment (new or improved):
  - Parse mechanical barriers from remarks/schematic when present:
    - `existing_mechanical_barriers`, `existing_cibp_ft`, `packer_ft`, `dv_tool_ft`
  - Ensure `perfs` list if discernible from producing interval or remarks
  - Prefer W-15 `cementing_to_squeeze` spans for existing squeeze; fallback to W-2 ops; include `sacks_override` from remarks (e.g., `830 SXS`)
  - Populate `preferences.geometry_defaults` for squeeze, cement_plug, and caps using derived IDs/ODs and stinger sizes

- Historical-only step overrides:
  - `effective.steps_overrides.squeeze_via_perf`:
    - `interval_ft: [top,bottom]` from W-15 or W-2
    - `sacks_override` from remarks when found
    - add citation: `W-15: cementing_report` when sourced from W-15
  - For existing CIBP: provide `cibp_cap` geometry (cap length default 20 ft) and, when known, top/bottom anchored at `existing_cibp_ft`

- Output summary enrichment:
  - Include `regulatory_basis`, `special_instructions`, `details` with `tag_required`, `materials_explain`, `placement_basis` where available

Why: Planning entry is the integration point that has access to all extracted data and can shape facts/overrides before deterministic kernel generation.

### 4.2 `apps/policy/services/loader.py`

- DONE: include `overrides.fields` when merging combined/per-county overlays
- DONE: nearest-county field resolution via centroids with normalized names; annotate `field_resolution`
- TODO: surface `preferences.operational.tag_wait_hours` from overlay/base pack when present
- Ensure cement class cutoff keys from base pack are exposed via `effective`

Why: Loader produces the authoritative effective policy; downstream logic relies on these preferences/requirements.

### 4.3 `apps/kernel/services/policy_kernel.py`

- Mechanical awareness gate:
  - If `existing_mechanical_barriers` contains `CIBP` near perf interval, suppress perf/circulate suggestions and favor `cibp_cap` only
  - If `PACKER`/`DV_TOOL` present, consider 100 ft cement plug centered on tool depth when overlay indicates

- Tagging and verification:
  - On steps where `tag_required` is true (e.g., UQW isolation, surface shoe in OH, formation-top when overlay demands), attach `details.verification = { action: "TAG", required_wait_hr: preferences.operational.tag_wait_hours || 4 }`

- Cement class selection:
  - For each cementing step, choose shallow/deep class based on `effective.base.cement_class.cutoff_ft` and step depth midpoint; annotate in `recipe` or `details`

- Existing squeeze/cap handling:
  - When `squeeze_via_perf` overrides exist:
    - Use provided interval (t/b) and geometry defaults for materials
    - If `sacks_override` present, set `materials.slurry.sacks` directly and mark `explain.sacks_override_from_extraction`
  - When `existing_cibp_ft` present, emit `cibp_cap` with top/bottom spanning cap length above plug

Status: DONE for mechanical gates, tagging/verification, cement class annotation, plan-level notes. TODO: ensure all cement-bearing steps have geometry/length for sacks.

Why: Kernel owns deterministic step creation, conflict checks, and materials computation.

### 4.4 `apps/kernel/services/w3a_rules.py`

- Formation-driven plugs:
  - Use `formation_tops_map` to place 100 ft plugs (±50 ft) at transitions per overlay (e.g., Grayburg, San Andres), tag where required
  - Add `placement_basis` (e.g., `Formation transition: San Andres top`) and include district/county basis key in `regulatory_basis`

- Yates/Red Bed fallback in 08A:
  - If missing tops, use conservative anchors (e.g., Yates ~1200 ft) until W-2 tops available

- Top plug and surface cut:
  - Emit with §3.14(d)(8) basis; include explicit `top_ft:10, bottom_ft:0` and `cut_depth_ft:3`

Status: DONE (top plug geometry, mid-depth plugs, provenance annotations). TODO: guarantee `details.length_ft` and geometry defaults on every cement step so materials can compute sacks.

Why: Step-level rule generation lives here; adding placement context and fallbacks yields readability and parity with field practice.

### 4.5 `apps/materials/services/material_engine.py`

- Keep guards (no negative/zero inputs); do not guess volumes
- Optionally add support API for computing sacks given explicit `sacks_override` passthrough (no change to core math)

Why: Materials logic should remain strict; overrides for historical operations should be explicit on the step.

### 4.6 Packs & Overlays

- `apps/policy/packs/tx_rrc_w3a_base_policy_pack.yaml`:
  - Confirm `cement_class.cutoff_ft`, `shallow_class`, `deep_class`
  - `preferences.operational`: include `tag_wait_hours: 4`, `mud_min_weight_ppg: 9.5`, `funnel_min_s: 40`

- `apps/policy/packs/tx/w3a/district_overlays/08a__auto.yml`:
  - Ensure county `overrides` include `tag.surface_shoe_in_oh`, protect intervals, and formation plug rules

Why: Policy knobs must reflect cited RRC expectations and district nuances.

---

## 5. Development Tasks (sequenced)

1) Planning facts and overrides
- Update `plan_from_extractions.py` to emit:
  - mechanical facts and existing cement overrides (W-15), `sacks_override` when detected
  - geometry defaults for squeeze, caps, plugs using derived IDs/ODs/stinger
  - enriched summary output with basis/instructions/details

Status: PARTIAL DONE
- Implemented mechanical barrier parsing from W-2 remarks (`existing_mechanical_barriers`, `existing_cibp_ft`, `packer_ft`, `dv_tool_ft`).
- Implemented W-15/W-2-driven squeeze interval selection and `sacks_override` detection; added citations where sourced from W-15.
- Added geometry defaults for squeeze and default recipe attachment.

2) Kernel awareness and tagging
- In `policy_kernel.py`:
  - DONE: Block perf/circulate when `CIBP` exists; ensure `cibp_cap` if CIBP present.
  - DONE: Tagging/verification enrichment with `required_wait_hr` (default 4 or overlay value).
  - DONE: Mechanical isolation plugs around detected DV tool/packer (±50 ft) with placement_basis and basis key.

3) Rule refinements
- In `w3a_rules.py`:
  - DONE: Added placement_basis for shoe, UQW, formation-top plugs; added 08A formation fallbacks and mid-depth plugs (Clearfork/Grayburg) when available.

4) Cement class selection
- Base pack updated: cutoff_ft=6500, shallow=C, deep=H.
- DONE: Annotate cement_class and depth_mid_ft per step in kernel.

5) Plan-level notes / metadata
- DONE: `plan.notes.existing_conditions` (CIBP/packer/DV) and `plan.notes.operations` synthesized from steps.

6) Field resolution and provenance
- DONE: nearest-county match with `field_resolution` annotation and normalized names.

2) Kernel awareness and tagging
- In `policy_kernel.py`:
  - block conflicting ops in presence of `CIBP`
  - attach verification on tagged steps (using `tag_wait_hours`)
  - cement class selection via base pack cutoff

3) Rule refinements
- In `w3a_rules.py`:
  - formation plugs with placement_basis and fallback anchors
  - explicit top plug geometry; incorporate intermediate shoe plug as applicable

4) Loader finalization
- In `loader.py`: surface `operational.tag_wait_hours`, ensure field overrides merge order is correct

5) Packs/config
- In base pack YAML: confirm cement class cutoffs and add tag wait hours; verify rounding policy

6) Materials integration
- No new math; ensure squeeze uses geometry defaults; honor `sacks_override` when present. Ensure all cement steps have `length_ft` and geometry to enable sacks computation.

7) Tests
- Add kernel tests for:
  - CIBP present → no perf/circ, only cap
  - W-15 squeeze interval honored; `sacks_override` applied
  - Formation plug placement from W-2 tops; 08A fallbacks
  - Tagging/verification presence on UQW and surface shoe steps

---

## 6. RRC §3.14 Mapping (keys → behaviors)

- §3.14(e)(2): Surface shoe plug ≥100 ft (±50 ft) → `surface_casing_shoe_plug` with min_length_ft 100; tag if overlay requires
- §3.14(g)(3): CIBP cap ≥20 ft → `cibp_cap` above existing/new CIBP; no circulate through plugs
- §3.14(g)(1): UQW isolation plug 50 ft above/below base → `uqw_isolation_plug`, tag required
- §3.14(d)(8): Top plug 10 ft and cut casing 3 ft below surface → `top_plug`, `cut_casing_below_surface`
- §3.14(d)(11): Plug volumes carry +10% per 1000 ft depth (in pack); use yields

Each step attaches `regulatory_basis` (TAC citations + overlay keys) and `placement_basis` for human traceability.

---

## 7. Acceptance Criteria

- Plans reflect existing barriers and prior cementing where present; no impossible operations are suggested
- Every cement-bearing step has top/bottom and computed sacks (or explicit `sacks_override` with justification)
- Steps show `regulatory_basis`, `placement_basis`, and `details.verification` when `tag_required`
- District/field overlays (08A/Andrews) are merged and visible in behavior (WBL, formation plugs, tagging)

---

## 8. Risks & Mitigations

- Sparse/ambiguous source data → use precedence: W-15 > schematic > W-2 remarks; log when assumptions are applied
- Naming variance in formation tops → normalized map and contains-matching; unit tests cover common aliases
- Overlay evolution → keep loader merging tolerant (`overrides.fields`) and guarded

---

## 9. File Touchpoints (summary)

- Planning entry: `apps/public_core/management/commands/plan_from_extractions.py`
- Loader: `apps/policy/services/loader.py`
- Kernel: `apps/kernel/services/policy_kernel.py`
- Rules: `apps/kernel/services/w3a_rules.py`
- Materials: `apps/materials/services/material_engine.py`
- Packs/Overlays: `apps/policy/packs/tx_rrc_w3a_base_policy_pack.yaml`, `apps/policy/packs/tx/w3a/district_overlays/08a__auto.yml`
- Extraction shape (optional expansions): `apps/public_core/services/openai_extraction.py`

This roadmap is designed to keep implementation focused and auditable against RRC §3.14 and district overlays, with explicit file-level guidance and data-source precedence to avoid drift.

---

## 10. Current Fix Targets and Recent Updates

### What we are fixing now (in progress)
- Formation plug sacks not surfaced per step:
  - Ensure `formation_top_plug` steps compute and expose per-plug sacks using already-available W‑2 geometry (production casing ID vs stinger OD).
  - Action: attach `casing_id_in`, `stinger_od_in`, and `annular_excess` onto each `formation_top_plug` when assembling steps in `policy_kernel.py`, then compute and surface `step.sacks`.
  - Additional step: inject `details.geometry_used` and `explain` on each `formation_top_plug` prior to materials compute to document capacity path, interval length, excess, rounding, and TAC excess scaling.
- Materials totals undercount when formation sacks are null:
  - Action: once per-formation sacks compute, `materials_totals.total_sacks/total_bbl` will include those plugs.
- Optional polish (backlog):
  - Merge adjacent formation plugs (< 500–2000 ft) to emulate field long-plug strategy where appropriate.
  - Normalize `rrc_export` intervals to descending depth for readability.

### Remaining Work (clarified)
- Formation Plug Volumes:
  - Inject `details.geometry_used` and `explain` at creation of `formation_top_plug` steps in `policy_kernel.py` so volumes are transparent and sacks compute reliably using W‑2 production casing ID vs stinger OD and step `annular_excess`.
- Materials Totals:
  - After sacks compute on formation plugs, existing aggregation in `plan_from_extractions.py` reconciles totals automatically (`materials_totals.total_sacks/total_bbl`).
- Optional Heuristic:
  - Merge adjacent formation plugs within 500–2000 ft to approximate field long-plug behavior; keep dedupe rules intact.

### Recently completed updates
- Field overlay resolution and formation tops
  - Loader (`apps/policy/services/loader.py`):
    - Robust field matching with normalization and “skeleton” fuzzy match; handles parentheticals/dash variants.
    - Zero-padded overlay filename fallback (e.g., `08a__auto.yml`).
    - Merge field-level `formation_tops` and `tag` into `effective.district_overrides` and prefer them exclusively when a field match exists.
    - `field_resolution` populated with `requested_field`, `matched_in_county`, `method`, and `nearest_distance_km`.
- Rules and duplication control
  - `apps/kernel/services/w3a_rules.py`:
    - Removed district-level default WBL generation.
    - Stopped generating formation-top plugs in rules to avoid duplicates; kernel is now the single source for these steps.
- Kernel step construction and materials
  - `apps/kernel/services/policy_kernel.py`:
    - Added symmetric formation intervals (±50 ft) with `placement_basis` and `details.center_ft`.
    - Computation path updated to surface `step.sacks` for cased-hole steps and to annotate `details.geometry_used` and `explain` (path, capacity, interval, excess).
    - Implemented TAC §3.14(d)(11) excess scaling (+10% per 1000 ft) in cased-annulus volume calc.
    - Continued mechanical awareness: suppress perf/circ when `CIBP` exists; add `cibp_cap`; handle DV tool isolation plug when indicated.
- Planning entry/output
  - `apps/public_core/management/commands/plan_from_extractions.py`:
    - Prefer W‑15 `cementing_to_squeeze` intervals and `sacks_override`; added debug payload; guard W‑2 fallback.
    - Enriched output with `materials_explain`, `rrc_export`, and plan-level notes.
    - Surfaces computed sacks to `step.sacks` and adds `materials_totals` (total sacks and bbl).

- End-to-end orchestration
  - `apps/public_core/management/commands/get_W3A_from_api.py`:
    - Fetch latest RRC Completions PDFs by API (allowlist: W‑2, W‑15, GAU), extract JSON, persist `ExtractedDocument`s, then run planner.
    - Prints plan JSON to stdout and saves to `tmp/extractions/W3A_<api>_plan.json` for auditability.

### Validation status (selected APIs)
- 4200346118 (Mabee 140A – Andrews, Spraberry):
  - Parity: ~95% structural, ~90% operational. W‑15 squeeze honored (2450–3630 ft, 830 sks override). Formation plugs present; per-plug sacks pending final geometry wiring on those steps.
- 4241501493 (Lion Diamond – Scurry, Diamond‑M‑):
  - Field matched in-county; formation plugs present with ±50 ft; per-plug sacks pending compute at formation plugs; totals currently reflect only computed steps (CIBP/squeeze/etc.).

### Notes
- We do not use district-level defaults when a field-level overlay exists; formation tops are exclusively sourced from the matched field overlay plus W‑2 facts.
- All cement-bearing steps will ultimately include top/bottom, computed sacks (or explicit `sacks_override`), geometry annotations, and materials rationale for auditability.

---

## 11. Variant Plans: Standard vs Minimum Wait-Time (Long-Plug Merge)

### Motivation
- Keep the regulator-faithful “standard” plan unchanged.
- Also provide an optional “minimum wait-time” variant that merges adjacent formation plugs into longer, field-style plugs to reduce operational wait/tag cycles while maintaining compliance.

### Behavior Overview
- Standard regulatory compliance (current output):
  - Discrete `formation_top_plug` steps centered on formation tops (±50 ft), shoe/UQW plugs per policy, no merging.
- Minimum wait-time compliance (new optional variant):
  - Merge adjacent `formation_top_plug` steps when the gap between them is ≤ threshold (default 500 ft), recompute materials as a single long interval, preserve tagging and citations.

### Configuration (Core preference; tenant overrides threshold)
- `preferences.long_plug_merge` (core policy preference):
  - `enabled`: false (default)
  - `threshold_ft`: 500 (default)
  - `types`: ["formation_top_plug"] (initial scope)
  - `preserve_tagging`: true (if any merged step had `tag_required: true`, keep it)
- Tenant overlay (optional):
  - May override `preferences.long_plug_merge.threshold_ft` (e.g., 300, 700) and `enabled`, without redefining behavior.

### Implementation Plan (no code changes yet)
- Kernel (`apps/kernel/services/policy_kernel.py`):
  - Add a post-processing stage after formation-top steps are appended and before materials compute, gated by `preferences.long_plug_merge.enabled`.
  - Merge algorithm (v1 scope):
    - Consider only steps with `type in types` and compatible geometry context (cased-annulus).
    - Sort by depth; for adjacent candidates where the gap ≤ `threshold_ft`, merge into a single step spanning min(bottom) to max(top).
    - Union `regulatory_basis`, carry `placement_basis` compactly, set `details.merged=true`, and record `details.merged_steps=[{formation, top_ft, bottom_ft}...]`.
    - Propagate `tag_required=true` if any source had it.
    - Recompute materials/sacks for the merged interval via the existing cased-annulus path.
    - Do not merge across operational step types (e.g., `squeeze`, `perf_circulate`, `surface_casing_shoe_plug`).
  - Output variants:
    - Continue to produce the standard plan unchanged.
    - Optionally produce a sibling variant `combined_long_plugs` in-memory for API/CLI; or write an additional artifact file.

- Planner (`apps/public_core/management/commands/plan_from_extractions.py`):
  - Optionally add params to request variants and merge threshold (non-breaking):
    - `--variants standard,minimum_wait` and `--merge-threshold 500`.
  - When asked, return `{"variants": {"standard": {...}, "minimum_wait_time": {...}}}` with materials totals computed per variant.

- Orchestration (`apps/public_core/management/commands/get_W3A_from_api.py`):
  - Support `--variants standard,minimum_wait` and `--merge-threshold <ft>`.
  - Continue printing the standard plan; also save `W3A_<api>_plan_min_wait.json` if requested.

### Guardrails
- Start with merging only `formation_top_plug` steps; do not merge across different step types in v1.
- Preserve auditability: retain sources in `details.merged_steps` and union citations.
- Keep tagging semantics: if any source required tag, the merged step remains tagged.

### Acceptance Criteria (variants)
- When `enabled=false` (default): output identical to current standard plan.
- When `enabled=true` with `threshold_ft=500`: adjacent formation-top plugs within 500 ft merge; sacks recomputed; totals equal merged geometry output; citations/tagging preserved.
- API/CLI can request one or both variants; artifacts saved with distinct filenames when both are requested.

### Future Extensions (out of scope for v1)
- Cross-type merge (e.g., UQW isolation with Santa Rosa top) behind a separate allowlist.
- Formation “base” anchoring rules by field (e.g., San Andres base), when overlay supports bottoms in addition to tops.
- Per-tenant UI controls for threshold and enable/disable with plan previews.



