# W-3A Planning API - End-to-End Flow and Logic

## Purpose
This document explains the production W-3A planning API, its inputs/outputs, and the complete orchestration and decision logic from RRC document fetch → JSON extraction → facts assembly → planning → materials and export.

## Endpoint
- Method: POST
- Path: `/api/plans/w3a/from-api`
- Auth: none (wire real auth later)
- Content types: `multipart/form-data` or `application/json`

### Request fields
- `api10` (string, required): 10-digit API (non-digits allowed; normalized internally). Examples: `4200342706`, `42-329-38004`.
- `plugs_mode` (string, optional): `combined` | `isolated` | `both` (default: `combined`).
- `merge_threshold_ft` (number, optional): Merge gap threshold in feet (default: `500`). Only used when `plugs_mode=combined`.
- `use_gau_override_if_invalid` (boolean, optional): If true and no valid GAU can be found, use `gau_file`.
- `gau_file` (file, optional): GAU PDF or GAU JSON. Only used when `use_gau_override_if_invalid=true` and system GAU is missing/invalid.

### Response shape (single variant)
- Plan summary with:
  - `api`, `jurisdiction`, `district`, `county`, `field`
  - `formation_tops_detected`, `steps`, `materials_totals`, `violations`
  - `rrc_export`: rows suitable for W-3A filing (deepest → shallowest)
  - `extraction`: `{ status, source, output_dir, files[] }` for visibility into RRC fetch

When `plugs_mode=both`, response includes `variants.combined` and `variants.isolated` plans, plus `extraction`.

## Orchestration (high-level)
1. Normalize API to digits (14/10/8 accepted; last 8 used for RRC search).
2. Run RRC completions fetcher to download recent W-2, W-15, GAU PDFs (cached for 14 days per API).
3. Classify each PDF and extract structured JSON (W-2, W-15, GAU); persist as `ExtractedDocument`.
4. Optional GAU override: if system GAU invalid/missing and `gau_file` provided, extract GAU (JSON or PDF) and persist.
5. Assemble facts from latest extractions (see next section).
6. Load effective policy (district/county/field overlay) and preferences.
7. Generate plan using the kernel, apply overrides/geometry/materials.
8. Optionally merge adjacent plugs (combined mode) or keep isolated; compute materials.
9. Produce summary, export rows, and include extraction metadata.

## Extraction subsystem
- Module: `apps/public_core/services/rrc_completions_extractor.py`
- Search: navigates to RRC completions search, queries by last 8 digits of API; selects latest record by submit date.
- Document table scrape: dedupes by type/href; normalizes kinds: `w2`, `w15`, `gau`; skips directional surveys.
- Download: saves PDFs to `MEDIA_ROOT/rrc/completions/<api>/...`. Cache: if any existing PDF in that directory is newer than 14 days, reuse cached set.
- Output to the API: `extraction` object echoes `status`, `source` (cache or rrc_completions), `output_dir`, and file paths.

## Classification and JSON extraction
- For each downloaded file:
  - `classify_document(Path)` determines doc type (`gau`, `w2`, `w15`, `schematic`, `formation_tops`).
  - `extract_json_from_pdf(Path, doc_type)` produces structured JSON, a `model_tag`, and `errors` if any.
- Persist: `ExtractedDocument` with fields: `api_number`, `document_type`, `source_path`, `model_tag`, `status`, `errors`, `json_data`.

## GAU validity and override
- A GAU is considered valid when it has a determination depth and is ≤ 5 years old (lenient date parsing).
- If invalid/missing and `use_gau_override_if_invalid=true` with `gau_file` provided:
  - If `gau_file` is JSON → ingest as-is as GAU.
  - If `gau_file` is PDF → extract GAU JSON from PDF.

## Facts assembly (from W-2/W-15/GAU)
- From W-2 `well_info`: `api14`, `district` (normalized), `county`, `field`, `lease`, `well_no`.
- GAU data:
  - `has_uqw` and `uqw_base_ft` (from GAU or override); parsed intervals from textual recommendation (surface-to and from-to patterns).
- Casing geometry:
  - `surface_shoe_ft` (surface string).
  - `intermediate_shoe_ft` (first intermediate string, if ≥1500 ft; guard logic applied).
  - `production_shoe_ft`:
    - Prefer explicit `production` string `shoe_depth_ft`/`setting_depth_ft`/`bottom_ft`.
    - Fallback to deepest available shoe/setting/bottom across all strings if production not labeled.
- Tubing: smallest `size_in` from `tubing_record` → `stinger_od_in`.
- Production interval (if present in W-2): `producing_interval_ft: [from_ft, to_ft]`.
- Formation tops map (W-2 `formation_record`): `{ formation_name_lower: top_ft }`.
- Mechanical awareness from W-2 remarks: `existing_mechanical_barriers` (CIBP/PACKER/DV_TOOL), and `existing_cibp_ft` when parsed.
- Geometry defaults derived from casing/tubing are set in `policy.preferences.geometry_defaults` to enable downstream materials computation.

## Planning kernel (core logic)
- Module: `apps/kernel/services/policy_kernel.py`
- Baseline step generation (W-3A scaffold) comes from `w3a_rules.generate_steps`.
- Mechanical awareness guards:
  - Suppress perf/circulate if `existing_mechanical_barriers` includes `CIBP`.
  - Ensure a `cibp_cap` exists above any existing CIBP.
  - Add isolation plugs around PACKER / DV tool when indicated, without duplicating overlapping plugs.
- District/overlay preferences and explicit step overrides (e.g., squeeze intervals) are applied.
- Suppress formation/cement plugs that are fully covered by perf/circulate intervals.
- Tagging enrichment: add verification waits where needed.
- Materials computation for steps with sufficient geometry (ID/OD/excess/recipe), including segmentation when provided.

### CIBP detection (implemented)
- Goal: require CIBP + cap when a producing zone is exposed below the production shoe and no existing CIBP/cap exists.
- Determine the top of the deepest producing zone as follows:
  - Preferred: when W-2 provides `producing_injection_disposal_interval`, take the interval with the deepest bottom and use its top as the CIBP reference.
  - Fallback (formations-only mode): use the deepest formation top from `formation_tops_map`.
- Exposure check: `top_of_deepest_zone` ≥ `production_shoe_ft`.
- Coverage check: if a squeeze/perf step already covers `top_of_deepest_zone`, do not emit a new CIBP.
- Emission (fixed policy):
  - Place `bridge_plug` at `top_of_deepest_zone - 10 ft`.
  - Place `bridge_plug_cap` (aka `cibp_cap`) from plug depth to `plug_depth + cap_length_ft`.
  - `cap_length_ft`: pulled from policy knob `cement_above_cibp_min_ft` when present; defaults to `100 ft` if unspecified.
  - Add sizing metadata for field ops, e.g., `details.casing_id_in` and `details.recommended_cibp_size_in` (based on casing ID; simple safety delta used).
- Export labeling:
  - In `rrc_export`, `bridge_plug` is labeled `CIBP` and `bridge_plug_cap`/`cibp_cap` as `CIBP cap`.

### Long plug merge (combined mode)
- Optional merging of adjacent formation/top plugs and compatible intervals when gaps ≤ `merge_threshold_ft`.
- Preserves tagging/citations across the merged interval.
- Isolated mode disables merging.

## Outputs and export
- `steps`: sorted deepest → shallowest, with materials summaries where geometry allows.
- `materials_totals`: total sacks and barrels if computable.
- `rrc_export`: ordered rows for filing, including CIBP/CIBP cap when present.
- `violations`: policy and data completeness findings (e.g., missing shoe depth).
- `extraction`: RRC fetch visibility.

## Errors and edge cases
- Missing W-2/GAU → minimal compliant scaffold and `SURFACE_SHOE_DEPTH_UNKNOWN` violation.
- GAU JSON upload accepted when system GAU invalid/missing; PDF extraction path also supported.
- RRC tables occasionally vary; extractor guards for missing links/tables and returns `no_records`/`no_documents` with an empty file list.

## Examples
### Combined plugs with GAU override (JSON)
```bash
curl -X POST http://127.0.0.1:8001/api/plans/w3a/from-api \
  -H "Accept: application/json" \
  -F "api10=4200342706" \
  -F "plugs_mode=combined" \
  -F "merge_threshold_ft=500" \
  -F "use_gau_override_if_invalid=true" \
  -F "gau_file=@/path/to/gau.json;type=application/json"
```

### Both variants
```bash
curl -X POST http://127.0.0.1:8001/api/plans/w3a/from-api \
  -H "Content-Type: application/json" \
  -d '{
    "api10": "42-329-38004",
    "plugs_mode": "both",
    "merge_threshold_ft": 500
  }'
```

## Notes / Future work
- Wire authentication and rate limiting.
- Expand extractor to additional relevant forms when needed.
- Enhance interval inference from W-2 remarks in sparse cases.
- Persist variant artifacts alongside main plan outputs when `both` is requested.

---

## Deep Dive: Complete Logic Flow and Internals

This section documents, in exhaustive detail, every step, data contract, and algorithm used when the API is invoked.

### 1) End-to-end sequence

```
Client → API (POST /api/plans/w3a/from-api)
  → normalize api10 → api (digits)
  → RRC extractor: extract_completions_all_documents(api, allowed_kinds=[w2,w15,gau])
      ↳ cache hit? return cached files (≤14 days)
      ↳ else headless fetch → latest row → document table → download PDFs
  → classify & extract per PDF → ExtractedDocument rows
  → optional GAU override ingest (JSON/PDF)
  → assemble facts from latest W-2/W-15/GAU ExtractedDocuments
  → load effective policy (district/county/field overlay) + preferences
  → kernel.plan_from_facts(facts, policy)
      ↳ generate base steps (w3a_rules)
      ↳ mechanical awareness (CIBP present, PACKER/DV tool)
      ↳ CIBP detector (exposure-based, top-of-interval - 10 ft)
      ↳ district overrides + explicit steps overrides
      ↳ suppress overlaps (perf/circ coverage)
      ↳ tagging enrichment
      ↳ defaults → compute materials
      ↳ optional long-plug merge (combined mode)
  → build plan summary + rrc_export + extraction meta
  → return JSON to client
```

### 2) Request validation and normalization

- `api10`:
  - Remove non-digits; must resolve to exactly 10 digits for validation.
  - Internally, RRC search uses `api[-8:]` (last 8). Storage/persistence uses the provided normalized digits.
- `plugs_mode`: enum; defaults to `combined`.
- `merge_threshold_ft`: numeric; must be ≥ 0; default `500`.
- `use_gau_override_if_invalid` → requires `gau_file` present.
- `gau_file`:
  - PDF: processed via extractor → JSON
  - JSON: must parse into object; stored directly as GAU `json_data`.

### 3) RRC extractor details

- Cache directory: `${MEDIA_ROOT}/rrc/completions/<api>/`.
- Cache hit when any PDF file in dir has `mtime ≤ 14 days`.
- Page flow:
  - GET `RRC_COMPLETIONS_SEARCH` → fill API field with `api[-8:]` → click Search.
  - Choose latest table row by submit date token (MM/DD/YYYY) scanning.
  - Follow row link → detect Form/Attachment table.
  - For each row with a PDF link, dedupe by type and href, skip directional surveys.
  - Normalize type/kind (W‑2/W‑15/GAU) by URL and cell text heuristics.
  - Download PDF into cache dir.
- Return `{status, api, api_search, output_dir, files[], source}`.

### 4) Classification and extraction

- `classify_document(Path)` returns a doc type string. Unknown/unsupported types skipped.
- `extract_json_from_pdf(Path, type)` returns `{ json_data, model_tag, errors[] }`.
- Persisted in `ExtractedDocument` with original `source_path` and `status` derived from `errors`.

### 5) Facts mapping (exhaustive)

- Identity: `api14`, `state='TX'`, `district` (normalized; e.g., map 08→08A for Andrews), `county`, `field`, `lease`, `well_no`.
- GAU-derived:
  - `has_uqw` = bool(GAU present or UQW depth parsed)
  - `uqw_base_ft` (if GAU valid ≤ 5 years)
  - `gau_protect_intervals` parsed from GAU recommendation text with regex:
    - `surface to <N> feet` → interval `[0, N]`
    - `from <A> feet to <B> feet` → interval `[min(A,B), max(A,B)]`
- Casing:
  - `surface_shoe_ft` from surface string.
  - `intermediate_shoe_ft` from first intermediate; included only if ≥1500 ft (stability guard).
  - `production_shoe_ft` from production string; fallback to deepest shoe/setting/bottom across all strings if production not explicitly labeled.
- Tubing: smallest `size_in` → `stinger_od_in`.
- Production interval: W‑2 `producing_injection_disposal_interval` (list) → store `[from_ft, to_ft]`.
- Formation tops: map lowercase name → `top_ft`.
- Mechanical detection from W‑2 remarks: set `existing_mechanical_barriers` (CIBP/PACKER/DV_TOOL), `existing_cibp_ft`, `packer_ft`, `dv_tool_ft` when parsed.
- Preferences geometry defaults: populate per-step defaults (`casing_id_in`, `stinger_od_in`, `annular_excess`) for `surface_casing_shoe_plug`, `cibp_cap`, `cement_plug`, `formation_top_plug`, `squeeze` to enable materials.

### 6) Planning pipeline (step-by-step)

1. Base generation (`w3a_rules.generate_steps`) produces core W‑3A scaffold: shoe coverage, top plug, productive horizon isolation, etc.
2. Mechanical awareness:
   - If `CIBP` exists, remove perf/circ that would conflict; ensure a cap above the existing CIBP when missing.
   - Add isolation plugs near PACKER/DV tool depths if not already spanned by a cement plug.
3. CIBP detector (no heuristics; fixed policy):
   - Determine producing top:
     - Preferred: from W‑2 intervals → choose interval with deepest bottom, use its top.
     - Fallback: deepest formation top.
   - Exposure: `producing_top >= production_shoe_ft`.
   - Coverage: if a squeeze/perf step spans `producing_top`, skip new CIBP.
   - Emit:
     - `bridge_plug` at `producing_top - 10 ft`.
     - `bridge_plug_cap` from plug depth to `plug_depth + cap_len` (cap_len from policy knob or default = 100 ft).
     - Add `details.casing_id_in` and `details.recommended_cibp_size_in` for field sizing.
4. District overrides: tagging hints, formation-top plug injection, operational instructions.
5. Explicit overrides: squeeze intervals and caps from W‑15/W‑2 data, sacks overrides, etc.
6. Overlap suppression: drop plugs fully covered by perf/circ cemented intervals.
7. Tagging enrichment: add tag waits to steps requiring TAG.
8. Apply geometry defaults and compute materials (slurry volumes, sacks, spacers) where geometry permits.
9. Long-plug merge (combined mode only): `_merge_adjacent_plugs` groups compatible plugs when gaps ≤ threshold; union citations; preserve tagging.

### 7) Materials computation (formulas)

- Volumetrics use `apps/materials/services/material_engine.py`:
  - Cased annulus capacity: `annulus_capacity_bbl_per_ft(casing_id, stinger_od)`
  - Open-hole annulus capacity when relevant: `annulus_capacity_bbl_per_ft(hole_d, stinger_od)`
  - Sacks via `compute_sacks(total_bbl, recipe, rounding)` with per-step/plan rounding policy.
  - TAC §3.14(d)(11): +10% per 1000 ft for certain cased plugs; units computed in step explain.
  - Squeeze volumes via `squeeze_bbl` with step defaults.

### 8) Export mapping

- Steps are sorted by depth (deepest → shallowest) for `rrc_export`.
- Type labels:
  - `bridge_plug` → `CIBP`
  - `bridge_plug_cap` or `cibp_cap` → `CIBP cap`
- From/To:
  - Use `(bottom_ft, top_ft)` when present; otherwise `depth_ft` for both on point depth steps.
- Remarks combine citations and placement basis when available.

### 9) Violations and findings

- Examples:
  - `SURFACE_SHOE_DEPTH_UNKNOWN`: emitted when `surface_shoe_ft` is missing yet shoe coverage is required.
  - `blocked_by_existing_cibp`: informational when perf/circ suppressed due to existing CIBP.
- Violations appear in `plan.violations[]` with code, severity, message, and optional context.

### 10) Observability & troubleshooting

- Response includes `extraction` with `{status, source, output_dir, files[]}` to verify RRC fetch and caching behavior.
- Kernel logs (info/debug) note policy ID, preferences applied, overrides chosen, and materials computation paths.
- Inspect persisted `ExtractedDocument` rows for `json_data`, `status`, and `errors` when a document fails extraction.

### 11) Performance & reliability

- RRC fetch is headless (Playwright). Timeouts and table detection guards reduce flakiness; returns `no_records/no_documents` gracefully.
- Caching avoids redundant downloads for 14 days.
- Planning is deterministic given the same facts/policy.

### 12) Examples (full)

#### Successful extraction + planning (combined)
- Inputs: `api10=42-329-38004`, `plugs_mode=combined`, `merge_threshold_ft=500`.
- Returns: plan with CIBP at `producing_top - 10 ft` and `CIBP cap`, UQW plug, shoe plug, surface GAU interval, with `extraction.files` showing W‑2/W‑15/GAU PDFs.

#### GAU override (JSON) with combined
- Inputs: `use_gau_override_if_invalid=true`, `gau_file=@gau.json`.
- Returns: GAU JSON ingested; plan uses GAU intervals when system GAU was missing/invalid.

