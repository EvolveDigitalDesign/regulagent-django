## Post-OpenAI Extraction Data Flow

This document summarizes what happens in the application after structured JSON is produced by `apps/public_core/services/openai_extraction.py`.

### 1) Classification and Extraction
- `classify_document(file_path)`: Infers doc type (`gau`, `w2`, `w15`, `schematic`, `formation_tops`) using filename heuristics, with optional OpenAI fallback.
- `extract_json_from_pdf(file_path, doc_type)`: Sends the PDF to OpenAI Responses API, requests strict JSON per schema, retries on malformed JSON, and ensures all required sections exist for the given document type.
- `iter_json_sections_for_embedding(doc_type, data)`: Yields `(section_name, section_text)` pairs from the JSON for vectorization.

Entry points:
- API: `apps/public_core/views/rrc_extractions.py` → `RRCCompletionsExtractView.post`
  - Orchestrates download and iteration over files for a given API number (via `extract_completions_all_documents`).
  - Runs classification → extraction per file.

### 2) Persistence: ExtractedDocument
- Model: `apps/public_core/models/extracted_document.py` (`ExtractedDocument`)
  - Stores the extracted JSON blob, status/errors, provenance (file path, model tag), and identifiers (`api_number`, `document_type`, optional `well` FK).
- Flow (in `RRCCompletionsExtractView.post`):
  - For each file classified as a supported type:
    - Call `extract_json_from_pdf`.
    - Create `ExtractedDocument` with the returned JSON and metadata inside a transaction.

Code reference:
```13:71:regulagent-django/RegulAgentV2/regulagent-backend/apps/public_core/views/rrc_extractions.py
class RRCCompletionsExtractView(APIView):
    ...
    def post(self, request):
        ...
        for f in files:
            ...
            doc_type = classify_document(Path(path))
            if doc_type not in ("gau", "w2", "w15", "schematic", "formation_tops"):
                continue
            ext = extract_json_from_pdf(Path(path), doc_type)
            with transaction.atomic():
                ed = ExtractedDocument.objects.create(
                    well=well,
                    api_number=api,
                    document_type=doc_type,
                    source_path=path,
                    model_tag=ext.model_tag,
                    status="success" if not ext.errors else "error",
                    errors=ext.errors,
                    json_data=ext.json_data,
                )
                ...
```

### 3) Vectorization: DocumentVector
- Model: `apps/public_core/models/document_vector.py` (`DocumentVector`)
  - One row per logical section chunk; stores `embedding` (pgvector), `section_text`, `document_type`, `file_name`, optional `well` FK, and metadata (`extracted_document_id`).
- Flow (in `RRCCompletionsExtractView.post`):
  - For each `(section_name, section_text)` from `iter_json_sections_for_embedding`, create an embedding with `MODEL_EMBEDDING` and insert a `DocumentVector` row.

Code reference:
```45:67:regulagent-django/RegulAgentV2/regulagent-backend/apps/public_core/views/rrc_extractions.py
# Vectorize required sections
try:
    from openai import OpenAI as _C
    client = _C(api_key=os.getenv("OPENAI_API_KEY"))
    from apps.public_core.services.openai_extraction import MODEL_EMBEDDING
    for section_name, section_text in iter_json_sections_for_embedding(doc_type, ext.json_data):
        emb = client.embeddings.create(model=MODEL_EMBEDDING, input=section_text).data[0].embedding
        DocumentVector.objects.create(
            well=well,
            file_name=Path(path).name,
            document_type=doc_type,
            section_name=section_name,
            section_text=section_text,
            embedding=emb,
            metadata={"extracted_document_id": str(ed.id)},
        )
except Exception:
    pass
```

### 4) Downstream Planning: Building a Plugging Plan
- Management command: `apps/public_core/management/commands/plan_from_extractions.py`
  - Loads latest `ExtractedDocument` rows for an API (`w2`, `gau`, optionally `w15`).
  - Derives kernel facts:
    - Identity: `api14`, `district` (with 08/08A normalization), `county`, `field`, `lease`, `well_no`.
    - Regulatory drivers: `has_uqw`, `uqw_base_ft` (from GAU else W-2 fallback), `use_cibp`, `surface_shoe_ft` (from W‑2 casing record).
  - Builds policy via `get_effective_policy` and sets preferences (e.g., rounding `nearest`, geometry defaults using parsed casing/tubing sizes).
  - Adds step overrides if inferable from extracted data (e.g., `perf_circulate`, `squeeze_via_perf`, operational mud weight).
  - Calls `plan_from_facts(facts, policy)` to produce deterministic plan steps and materials.

Code reference:
```25:66:regulagent-django/RegulAgentV2/regulagent-backend/apps/public_core/management/commands/plan_from_extractions.py
def handle(...):
    ...
    w2_doc = latest("w2")
    gau_doc = latest("gau")
    w2 = (w2_doc and w2_doc.json_data) or {}
    gau = (gau_doc and gau_doc.json_data) or {}
    ...
    facts = {
        "api14": wrap(api14),
        "state": wrap("TX"),
        "district": wrap(district),
        "county": wrap(county),
        "field": wrap(field),
        "lease": wrap(lease),
        "well_no": wrap(well_no),
        "has_uqw": wrap(bool(gau or uqw_depth)),
        "uqw_base_ft": wrap(uqw_depth),
        "use_cibp": wrap(True),
        "surface_shoe_ft": wrap(surface_shoe_ft),
    }
    policy = get_effective_policy(...)
    policy["policy_id"] = "tx.w3a"
    policy["complete"] = True
    policy.setdefault("preferences", {})["rounding_policy"] = "nearest"
    ...
    out = plan_from_facts(facts, policy)
```

### 5) Where This Is Used/Validated
- API endpoint `RRCCompletionsExtractView` returns a list of created `ExtractedDocument` IDs along with the original downloader result.
- Tests: `apps/kernel/tests/test_extracted_data_golden.py` asserts a plan can be built deterministically from latest `ExtractedDocument` rows, checking step presence and materials invariants.

### 6) Storage/Infra Notes
- pgvector is enabled via migration `0004_enable_vector_extension`.
- `DocumentVector.embedding` uses dimension 1536 (compatible with `text-embedding-3-small`).
- Vector search is not yet wired in views; embeddings are stored for future semantic retrieval.

### 7) Summary
- OpenAI JSON is stored in `ExtractedDocument`, chunked and embedded into `DocumentVector`, and can drive deterministic plan generation through the kernel via `plan_from_extractions`.



