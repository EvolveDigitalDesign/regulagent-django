# W-3A Orchestration Flow Analysis

## Complete W-3A Generation Pipeline from w3a_from_api.py

This document preserves the complete logic flow from `w3a_from_api.py` POST method for extraction into a reusable orchestrator service.

### Flow Phases (Order is Critical)

#### PHASE 1: Request Validation & Normalization (Lines 102-129)
- Parse request via `W3AFromApiRequestSerializer`
- Extract parameters: api10, plugs_mode, input_mode, merge_threshold_ft, confirm_fact_updates, etc.
- Normalize API using `_normalize_api()` - handles 10, 14, 8-digit formats
- Result: `api_in` (canonical API for database lookups)

#### PHASE 2: Document Acquisition (Lines 131-307)
Sub-phases depending on `input_mode`:

**2a) RRC Document Extraction** (if input_mode in "extractions", "hybrid")
- Call `extract_completions_all_documents(api_in, allowed_kinds=["w2", "w15", "gau"])`
- Get list of downloaded PDF files
- For each file:
  - Classify via `classify_document(Path(path))`
  - Extract JSON via `extract_json_from_pdf(Path(path), doc_type)`
  - Create `ExtractedDocument` in DB with atomic transaction
  - Vectorize via `vectorize_extracted_document(ed)` for semantic search
  - Track in `created` list
  - Fallback: Create `TenantArtifact` if vectorization fails

**2b) User File Upload Ingestion** (if input_mode in "user_files", "hybrid")
- For each uploaded file (w2_file, w15_file, gau_file, schematic_file, formation_tops_file):
  - Detect if JSON or PDF
  - If JSON: Parse and detect type via `_detect_doc_type_from_json()`
  - If PDF: Save to uploads dir via `_save_upload()`, then extract JSON
  - Create `ExtractedDocument` with atomic transaction
  - Vectorize if possible
  - Track in `uploaded_refs` list

**Key Invariants:**
- Each `ExtractedDocument` tied to `well` and `api_number`
- Atomic transactions for consistency
- Non-blocking vectorization failures (catch and log)

#### PHASE 3: WellRegistry Ensurance & Enrichment (Lines 309-442)
Critical phase - builds/updates well metadata before plan generation

**3a) Create/Get WellRegistry**
- Look up latest W-2 and GAU extractions for this API
- Extract from W-2: api14, county, district, operator, field, lat/lon
- Extract from GAU: fallback lat/lon
- Get or create `WellRegistry` with:
  - api14 (canonical 14-digit)
  - state="TX"
  - defaults: county from W-2

**3b) Propose & Apply Fact Updates**
- Build `proposed_changes` dict tracking before/after/source:
  - county (from W-2)
  - district (from W-2, RRC field)
  - operator_name (from W-2)
  - field_name (from W-2)
  - lat/lon (from W-2 or GAU)

- If `confirm_fact_updates=true`:
  - Apply precision upgrade policy:
    - Fill empty fields unconditionally
    - Update coordinates if `allow_precision_upgrades_only` and small delta
  - Save to DB

- Backfill created `ExtractedDocument` records to point to `well`

**3c) Enrich from Documents**
- Call `enrich_well_registry_from_documents(well, extracted_docs)`
- May fill in additional fields

#### PHASE 4: GAU Validity Check & Override (Lines 444-488)
- Check if latest GAU is valid: <= 5 years old + has determination depth
- If invalid AND `use_gau_override_if_invalid` AND user provided gau_file:
  - Parse user's GAU file (JSON or PDF)
  - Create `ExtractedDocument` with model_tag="user_uploaded_json" or "user_uploaded_pdf"
  - Atomic transaction

#### PHASE 5: Plan Building (Lines 500-619)
Call helper `_build_plan(api, merge_enabled, merge_threshold_ft)` for each variant needed

**5a) Plan Building Internal Flow** (via `_build_plan` method, lines 669-1326)
- Fetch latest extractions: W-2, W-15, GAU, Schematic
- Extract well geometry data from W-2:
  - Casing strings: surface/intermediate/production sizes and depths
  - Formations map: name → top_ft
  - Producing interval
  - Tubing/stinger record

- Parse GAU data:
  - Base UQW depth (if <= 5 years old)
  - Protection intervals from recommendation text

- Extract existing mechanical barriers from W-2 remarks:
  - CIBP (Cast Iron Bridge Plug) at depth
  - Packer at depth
  - DV tool at depth
  - Check acid_fracture_operations for squeeze indicators

- Extract KOP (Kick-Off Point) for horizontal wells:
  - MD and TVD depths

- Build facts dictionary for kernel:
  - api14, state, district, county, field, lease, well_no
  - has_uqw, uqw_base_ft
  - surface_shoe_ft, production_shoe_ft, intermediate_shoe_ft
  - existing_mechanical_barriers, cibp_ft, packer_ft, dv_tool_ft
  - annular_gaps (from schematic)
  - gau_protect_intervals
  - producing_interval_ft, formation_tops_map
  - kop (if horizontal)

- Get effective policy:
  - Call `get_effective_policy(district, county, field)`
  - Set policy_id="tx.w3a", complete=True
  - Configure preferences:
    - rounding_policy="nearest"
    - default_recipe (Class H neat cement)
    - long_plug_merge settings
    - geometry_defaults (casing IDs, stinger OD, annular excess)

- **Call kernel**: `plan_from_facts(facts, policy)`
  - Returns steps with type, depths, materials, regulatory basis, etc.

- **Format output**:
  - Normalize depth field names
  - Build RRC export format with plug numbers, cement class, sacks, etc.
  - Extract formations targeted by kernel
  - Build plan_notes from existing tools + policy overrides
  - Sort steps by depth (deepest to shallowest)
  - Assign sequential step_ids in procedural order

**5b) Variant Handling**
- If `plugs_mode="both"`: Generate both "combined" (merged) and "isolated" variants
- Else: Generate single variant per merge_enabled flag

#### PHASE 6: PlanSnapshot Persistence (Lines 513-619)
- Create `PlanSnapshot` for each variant:
  - well, plan_id, kind=BASELINE
  - payload (full plan output)
  - kernel_version, policy_id="tx.w3a", extraction_meta
  - visibility=PUBLIC (shareable for learning)
  - tenant_id (from request.user)
  - status=DRAFT

- Link `TenantArtifact` records (extracted docs) to snapshot
- Track well engagement:
  - Call `track_well_interaction()` with:
    - tenant_id, well, interaction_type=W3A_GENERATED
    - metadata_update with plan_id, snapshot_id, plugs_mode

#### PHASE 7: Response Building (Lines 559-619)
- Validate response structure via serializer
- Return success response with:
  - steps (array with step_ids, depths, materials, etc.)
  - extraction (RRC download metadata)
  - facts_update_preview (if confirm_fact_updates=false)
  - kernel_version, violations

### Critical Dependencies

**Models:**
- `WellRegistry` - canonical well data
- `ExtractedDocument` - raw JSON from PDFs
- `PlanSnapshot` - baseline plans with lineage
- `TenantArtifact` - artifact tracking
- `WellEngagement` - engagement history

**Services:**
- `rrc_completions_extractor.extract_completions_all_documents()` - RRC API calls
- `openai_extraction.classify_document()` - ML document classification
- `openai_extraction.extract_json_from_pdf()` - OpenAI structured extraction
- `openai_extraction.vectorize_extracted_document()` - Semantic embeddings
- `well_registry_enrichment.enrich_well_registry_from_documents()` - Metadata enrichment
- `policy.services.loader.get_effective_policy()` - Policy selection
- `kernel.services.policy_kernel.plan_from_facts()` - **THE CORE ALGORITHM**
- `engagement_tracker.track_well_interaction()` - Audit logging

**Helpers in View:**
- `_has_valid_gau()` - GAU age/validity check
- `_persist_upload_to_tmp_pdf()` - Temp file for user uploads
- `_build_plan()` - Orchestrates everything above for one variant
- `_build_additional_operations()` - RRC export format helper
- Closures: `_normalize_api`, `_ensure_dir`, `_sha256_*`, `_save_upload`, `_detect_doc_type_from_json`

### Exception Handling Strategy

**Non-Fatal Failures (Continue):**
- Vectorization of ExtractedDocument
- WellRegistry enrichment
- TenantArtifact creation
- Well engagement tracking
- Fact update persistence
- KOP extraction

**Fatal Failures (Raise):**
- Request validation
- Document acquisition failures
- GAU override parsing
- Plan building
- Kernel execution
- Response serialization

### Key Design Patterns

1. **Atomic Transactions**: All DB writes wrapped in `@transaction.atomic()`
2. **Progressive Enrichment**: Facts built incrementally, policy selected before kernel call
3. **Public Visibility**: All baseline plans PUBLIC for learning/comparison
4. **Tenant Awareness**: All operations track tenant_id for multi-tenant support
5. **Source Attribution**: proposed_changes track before/after/source for transparency
6. **Lazy Kernel**: Don't call kernel until all facts assembled
7. **Fallback Values**: Use defaults where data missing (district="08A", etc.)
8. **Depth Normalization**: Extract multiple depth field formats from extractions

### Sequencing Constraints (ORDER MATTERS)

1. ✅ Validate request + normalize API
2. ✅ Extract documents (must happen before creating ExtractedDocuments)
3. ✅ Classify & extract JSON (must happen before creating DB records)
4. ✅ Create ExtractedDocuments (must happen before enrichment)
5. ✅ Ensure WellRegistry exists (must happen before backling ExtractedDocuments)
6. ✅ Backfill ExtractedDocuments to well (must happen before enrichment)
7. ✅ Enrich WellRegistry (must happen before building facts)
8. ✅ Check GAU validity (must happen before override)
9. ✅ Build plan (must happen after all facts ready)
10. ✅ Create PlanSnapshot (must happen after plan built)
11. ✅ Track engagement (must happen after snapshot created)
12. ✅ Return response (must happen after all DB writes)

---

## Orchestrator Function Signature

```python
def generate_w3a_for_api(
    api_number: str,                              # 10-digit API from pnaexchange
    plugs_mode: str = "combined",                 # "combined", "isolated", or "both"
    input_mode: str = "extractions",              # "extractions", "user_files", "hybrid"
    merge_threshold_ft: float = 500.0,
    request = None,                               # HTTP request for user/tenant context
    confirm_fact_updates: bool = False,           # Apply well metadata updates?
    allow_precision_upgrades_only: bool = True,   # Conservative update policy?
    use_gau_override_if_invalid: bool = False,    # Accept user GAU if RRC invalid?
    gau_file = None,                              # User GAU upload
    w2_file = None,                               # User W-2 upload
    w15_file = None,                              # User W-15 upload
    schematic_file = None,                        # User schematic upload
    formation_tops_file = None,                   # User formation tops upload
) -> Dict[str, Any]:
    """
    Generate complete W-3A plan with all extractions and enrichment.
    
    Returns:
    {
        "success": bool,
        "plan_data": {  # If success=true
            "variants": {
                "combined": {...},  # if plugs_mode in ("both", "combined")
                "isolated": {...}   # if plugs_mode in ("both", "isolated")
            } OR single plan dict,
            "extraction": {...},
            "facts_update_preview": {...}
        },
        "snapshot_id": uuid,  # For linking to W-3 events
        "auto_generated": bool,
        "error": str,  # If success=false
    }
    """
```

---

## Next: Extraction Plan

1. Create `w3a_orchestrator.py` with `generate_w3a_for_api()` function
2. Move all helper functions from W3AFromApiView into orchestrator
3. Refactor W3AFromApiView.post() to call orchestrator
4. Update w3_from_pna.py auto-trigger to call orchestrator
5. Link generated W-3A snapshot to W-3 response

