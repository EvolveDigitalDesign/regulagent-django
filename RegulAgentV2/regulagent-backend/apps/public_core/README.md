# Public Core App - Public Well Data & Document Extraction

## Purpose

The **public_core** app is the data ingestion and storage layer for public well data from the Texas Railroad Commission. It handles PDF extraction using OpenAI, stores structured well data in relational tables, and maintains vector embeddings for semantic search. This is RegulAgent's "source of truth" layer for public regulator data that feeds into the tenant overlay and kernel.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      PUBLIC_CORE APP                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  EXTERNAL SOURCES                                                │
│    ├─> RRC PDFs (W-2, W-15, GAU, Schematic, Formation Tops)    │
│    └─> RRC Public API (W-3A submissions)                        │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │  PDF Extraction Pipeline (OpenAI-powered)                     ││
│  │  services/openai_extraction.py                                ││
│  │  ├─> classify_document()        Identify doc type            ││
│  │  ├─> extract_json_from_pdf()    OpenAI structured output     ││
│  │  └─> generate_embeddings()      Vector embeddings            ││
│  └──────────────────────────────────────────────────────────────┘│
│           ↓                                                       │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │  Django Models (PostgreSQL + pgvector)                        ││
│  │  ├─> WellRegistry               Well identity (API14, coords)││
│  │  ├─> ExtractedDocument           Raw JSON extractions        ││
│  │  ├─> DocumentVector              Vector embeddings            ││
│  │  ├─> PublicFacts                 Normalized well facts       ││
│  │  ├─> PublicCasingString          Casing geometry             ││
│  │  ├─> PublicWellDepths            Depth measurements          ││
│  │  ├─> PublicPerforation           Perforation intervals       ││
│  │  └─> PlanSnapshot                Generated plan history      ││
│  └──────────────────────────────────────────────────────────────┘│
│           ↓                                                       │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │  API Views (Django REST Framework)                            ││
│  │  ├─> WellRegistry CRUD                                        ││
│  │  ├─> PublicFacts CRUD                                         ││
│  │  ├─> RRC Extractions API                                      ││
│  │  └─> W3A from RRC API                                         ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                   │
│  OUTPUT: Structured well data ready for tenant overlay & kernel  │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### Inputs (From)
- **RRC PDFs** (Local filesystem or S3)
  - Form W-2: Well completion/recompletion report
  - Form W-15: Cementing report
  - GAU: Groundwater advisory unit determination
  - Schematic: Wellbore diagram
  - Formation Tops: Formation depth records
  
- **RRC Public API**
  - W-3A submissions (JSON)
  - Well query results

### Processing
1. **PDF Classification** → Identify document type
2. **OpenAI Extraction** → Convert PDF to structured JSON
3. **Normalization** → Store in relational tables
4. **Vectorization** → Generate embeddings for semantic search

### Outputs (To)
- **Tenant Overlay App** (`tenant_overlay/`)
  - PublicFacts consumed by facts_resolver
  - WellRegistry identity data
  
- **Kernel App** (`kernel/`)
  - Facts for plan generation
  - Geometry for material calculations

- **Vector Search** (Future)
  - Similar well recommendations
  - Historical approval patterns

---

## Key Services

### 1. `openai_extraction.py` - PDF Extraction Engine

#### **`classify_document(file_path)`**
**Purpose:** Identify document type using filename heuristics and optionally OpenAI.

**Logic:**
1. **Filename Heuristics** (fast path):
   - `"w-2"` or `"w2"` → "w2"
   - `"w-15"` or `"cement"` → "w15"
   - `"gau"` → "gau"
   - `"schematic"` or `"diagram"` → "schematic"
   - `"formation"` + `"top"` → "formation_tops"

2. **OpenAI Classification** (fallback):
   - Upload PDF to OpenAI Files API
   - Prompt: "Classify the regulatory document type: one of [gau, w2, w15, schematic, formation_tops]"
   - Model: `gpt-4o-mini` (lightweight classifier)
   - Return: document type or "unknown"

**Example:**
```python
from pathlib import Path
doc_type = classify_document(Path("W2_LION_DIAMOND.pdf"))
# Returns: "w2"
```

---

#### **`extract_json_from_pdf(file_path, document_type)`**
**Purpose:** Extract structured JSON from PDF using OpenAI structured outputs.

**Parameters:**
- `file_path`: Path to PDF file
- `document_type`: One of: gau, w2, w15, schematic, formation_tops

**Logic Flow:**

**Step 1: Extract PDF Text**
```python
def _extract_pdf_text(file_path, max_chars=20000):
    # Try pdfplumber first
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    
    # Fallback to PyMuPDF if pdfplumber fails
    if not text:
        doc = fitz.open(file_path)
        for page in doc:
            text += page.get_text() or ""
    
    # Truncate to 20k chars (API limits)
    return text[:max_chars]
```

**Step 2: Build JSON Schema**
```python
def _json_schema_for(doc_type):
    # Enforce required sections per document type
    if doc_type == "w2":
        required = [
            "header", "operator_info", "well_info",
            "casing_record", "formation_record", ...
        ]
    
    schema = {
        "type": "object",
        "properties": {section: {...} for section in required},
        "required": required,
        "strict": True
    }
    return schema
```

**Step 3: Load Extraction Prompt**
```python
# Prompts stored in openai_extraction_prompts.md
PROMPTS = {
    "w2": """
        Extract all sections from the W-2 form:
        - header: RRC district, form type
        - operator_info: name, address, operator number
        - well_info: API, county, field, lease, well number
        - casing_record: [{type, diameter_in, weight_ppf, shoe_depth_ft, top_cement_ft}]
        - formation_record: [{formation_name, top_ft, bottom_ft}]
        ...
    """,
    "gau": "Extract GAU determination sections...",
    ...
}
```

**Step 4: Call OpenAI Structured Output**
```python
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

response = client.chat.completions.create(
    model="gpt-4.1-mini",  # Supports structured outputs
    messages=[
        {"role": "system", "content": "You are a regulatory document extractor."},
        {"role": "user", "content": f"{PROMPTS[doc_type]}\n\nPDF Text:\n{text}"}
    ],
    response_format={
        "type": "json_schema",
        "json_schema": _json_schema_for(doc_type)
    },
    temperature=0
)

json_data = json.loads(response.choices[0].message.content)
```

**Step 5: Validate & Return**
```python
errors = []
if not json_data.get("well_info"):
    errors.append("Missing well_info section")

return ExtractionResult(
    document_type=doc_type,
    json_data=json_data,
    model_tag="gpt-4.1-mini",
    errors=errors
)
```

**Supported Document Types:**

**W-2 (Well Completion Report):**
- Operator info, well identity, completion data
- Casing record: sizes, weights, shoe depths, top cement
- Formation record: formation tops/bottoms
- Initial potential test, H2S flags
- Commingling data, remarks

**W-15 (Cementing Report):**
- Cementing operations data
- Cement job details: volumes, densities, returns
- Service company, cement type
- Squeeze operations

**GAU (Groundwater Advisory Unit):**
- Surface casing determination
- UQW depth recommendation
- Operator info, well location
- Geologist recommendation

**Schematic (Wellbore Diagram):**
- Visual casing/tubing layout parsed into structured data
- Packer depths, bridge plug locations
- String sizes and setting depths

**Formation Tops:**
- Formation name and depth records
- H2S presence flags
- Commingling indicators

**Example JSON Output (W-2 snippet):**
```json
{
  "well_info": {
    "api": "42-000-12345-00-00",
    "county": "Andrews",
    "field": "Spraberry (Trend Area)",
    "lease": "LION DIAMOND 'M' UNIT",
    "well_no": "1234",
    "district": "08"
  },
  "casing_record": [
    {
      "type_of_casing": "Surface",
      "diameter_in": 11.75,
      "weight_ppf": 54.5,
      "shoe_depth_ft": 1200,
      "top_cement_ft": 0,
      "sacks_cement": 450
    },
    {
      "type_of_casing": "Production",
      "diameter_in": 5.5,
      "weight_ppf": 15.5,
      "shoe_depth_ft": 9850,
      "top_cement_ft": 7500,
      "sacks_cement": 850
    }
  ],
  "formation_record": [
    {"formation": "Spraberry", "top_ft": 7200, "bottom_ft": 8450},
    {"formation": "Dean", "top_ft": 8450, "bottom_ft": 9800}
  ]
}
```

---

#### **`generate_embeddings(text)`**
**Purpose:** Generate vector embeddings for semantic search.

**Logic:**
```python
client = OpenAI()
response = client.embeddings.create(
    model="text-embedding-3-small",  # 1536 dimensions
    input=text
)
return response.data[0].embedding  # List[float] of length 1536
```

**Use Cases:**
- Find similar wells by completion description
- Semantic search: "Find wells with Wolfcamp production"
- Retrieve historical approvals for reference

---

### 2. `rrc_completions_extractor.py` - Legacy Parser

**Purpose:** Fallback text-based extraction when OpenAI unavailable (dev/testing).

**Methods:**
- `parse_w2_text(text)` - Regex-based W-2 parsing
- `parse_gau_text(text)` - GAU text extraction
- `parse_casing_table(text)` - Table parsing

**Note:** Prefer OpenAI extraction for production; this is for offline testing.

---

## Key Models

### 1. `WellRegistry` Model

**Purpose:** Master well identity registry (API14, location, operator).

**Fields:**
```python
class WellRegistry(models.Model):
    api14 = models.CharField(max_length=16, unique=True, db_index=True)
        # 14-digit API: "42-000-12345-00"
    
    state = models.CharField(max_length=2)          # "TX"
    county = models.CharField(max_length=64)        # "Andrews"
    
    operator = models.CharField(max_length=256, blank=True)
    field = models.CharField(max_length=256, blank=True)
    lease = models.CharField(max_length=256, blank=True)
    well_number = models.CharField(max_length=64, blank=True)
    
    lat = models.DecimalField(max_digits=10, decimal_places=7, null=True)
    lon = models.DecimalField(max_digits=10, decimal_places=7, null=True)
    
    rrc_district = models.CharField(max_length=8, blank=True)
    
    status = models.CharField(max_length=32, blank=True)
        # "active", "plugged", "drilling", "shut-in"
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

**Relationships:**
- `public_facts` (reverse FK): Facts scraped from regulator
- `extracted_documents` (reverse FK): PDF extractions
- `engagements` (reverse FK via tenant_overlay): Tenant work sessions

---

### 2. `ExtractedDocument` Model

**Purpose:** Store raw JSON extractions from PDFs (denormalized for speed).

**Fields:**
```python
class ExtractedDocument(models.Model):
    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE, null=True)
    
    api_number = models.CharField(max_length=16, db_index=True)
    document_type = models.CharField(max_length=64, db_index=True)
        # "w2", "w15", "gau", "schematic", "formation_tops"
    
    source_path = models.TextField(blank=True)
        # "/path/to/W2_LION_DIAMOND.pdf"
    
    model_tag = models.CharField(max_length=64, blank=True)
        # "gpt-4.1-mini"
    
    status = models.CharField(max_length=32, default="success")
        # "success", "error", "partial"
    
    errors = models.JSONField(default=list)
        # ["Missing casing_record", "Invalid API format"]
    
    json_data = models.JSONField()
        # Full extracted JSON blob
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

**Indexes:**
- `(api_number, document_type)` - Fast lookup of latest extraction
- `created_at` - Temporal queries

**Query Pattern:**
```python
# Get latest W-2 for a well
w2 = ExtractedDocument.objects.filter(
    api_number=api14,
    document_type="w2"
).order_by("-created_at").first()

casing_data = w2.json_data.get("casing_record", [])
```

---

### 3. `DocumentVector` Model

**Purpose:** Store vector embeddings for semantic search (pgvector).

**Fields:**
```python
class DocumentVector(models.Model):
    document = models.ForeignKey(ExtractedDocument, on_delete=models.CASCADE)
    
    vector = VectorField(dimensions=1536)
        # pgvector: Nearest neighbor search
    
    metadata = models.JSONField(default=dict)
        # {"customer": "XTO", "field": "Spraberry", "county": "Andrews"}
    
    created_at = models.DateTimeField(auto_now_add=True)
```

**Vector Operations:**
```python
from pgvector.django import MaxInnerProduct

# Find similar wells (cosine similarity)
similar = DocumentVector.objects.order_by(
    MaxInnerProduct("vector", query_embedding)
)[:10]
```

---

### 4. `PublicFacts` Model

**Purpose:** Normalized public facts (one row per well + fact_key).

**Fields:**
```python
class PublicFacts(models.Model):
    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE)
    fact_key = models.CharField(max_length=128)
        # "surface_shoe_ft", "production_shoe_ft", "uqw_base_ft"
    
    value = models.JSONField()
        # Typed value: 1200 (int), "Spraberry" (str), [7200, 8450] (list)
    
    units = models.CharField(max_length=32, blank=True)
        # "ft", "ppg", "gal/min"
    
    provenance = models.JSONField(default=dict)
        # {"source": "w2", "page": 1, "bbox": [100, 200, 300, 400]}
    
    source = models.CharField(max_length=256, blank=True)
        # "rrc.w2.casing_record"
    
    as_of = models.DateTimeField(null=True, blank=True)
        # When fact was current
```

**Unique Together:** `(well, fact_key)`

**Example Records:**
```
API: 42-000-12345, fact_key: surface_shoe_ft, value: 1200, units: ft
API: 42-000-12345, fact_key: production_shoe_ft, value: 9850, units: ft
API: 42-000-12345, fact_key: producing_formation, value: "Spraberry", units: ""
```

---

### 5. `PublicCasingString` Model

**Purpose:** Store casing geometry (sizes, weights, depths).

**Fields:**
```python
class PublicCasingString(models.Model):
    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE)
    
    string_type = models.CharField(max_length=64)
        # "Surface", "Intermediate", "Production"
    
    diameter_in = models.DecimalField(max_digits=6, decimal_places=3, null=True)
    weight_ppf = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    shoe_depth_ft = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    top_cement_ft = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    sacks_cement = models.IntegerField(null=True)
    
    provenance = models.JSONField(default=dict)
```

---

### 6. `PlanSnapshot` Model

**Purpose:** Store immutable plan snapshots for audit, baseline comparison, and outcomes.

**Fields (aligned with implementation):**
```python
class PlanSnapshot(models.Model):
    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE, related_name="plan_snapshots")
    
    plan_id = models.CharField(max_length=64, db_index=True)
        # Stable identifier for this plan lineage (e.g., api14 + short uuid)
    
    kind = models.CharField(max_length=16, choices=[
        ("baseline", "baseline"),
        ("post_edit", "post_edit"),
        ("submitted", "submitted"),
        ("approved", "approved"),
    ], db_index=True)
        # Snapshot type
    
    payload = models.JSONField()
        # Full plan response JSON returned to the client
    
    kernel_version = models.CharField(max_length=32, blank=True)
    policy_id = models.CharField(max_length=64, blank=True)
    overlay_id = models.CharField(max_length=128, blank=True)
    extraction_meta = models.JSONField(default=dict)
        # Echo of extraction visibility: {status, source, output_dir, files[]}
    
    created_at = models.DateTimeField(auto_now_add=True)
```

**Use Cases:**
- Preserve baseline (standard) plan right after creation to anchor edits
- Historical comparison and diffs vs baseline and outcomes
- Audit trail for regulatory submissions and approvals

---

## Management Commands

### 1. `extract_local_rrc` - Batch PDF Extraction

**Purpose:** Extract all PDFs in a directory using OpenAI.

**Usage:**
```bash
# Extract from default directory
docker exec regulagent_web python manage.py extract_local_rrc --write

# Extract from custom directory
docker exec regulagent_web python manage.py extract_local_rrc \
  --dir /path/to/pdfs \
  --write

# Override API14 for all docs
docker exec regulagent_web python manage.py extract_local_rrc \
  --api 42000012345678 \
  --write

# Dry run (no database writes)
docker exec regulagent_web python manage.py extract_local_rrc --dry
```

**Logic:**
1. Scan directory for `*.pdf` files
2. For each PDF:
   - Classify document type
   - Extract JSON via OpenAI
   - Guess API14 from filename or JSON content
   - Create ExtractedDocument record
3. Trigger plan generation for last extracted API

---

### 2. `get_W3A_from_api` - Fetch from RRC API

**Purpose:** Fetch W-3A submissions from RRC public API.

**Usage:**
```bash
docker exec regulagent_web python manage.py get_W3A_from_api --api 42000012345678
```

**Logic:**
1. Query RRC API: `https://webapps.rrc.texas.gov/CMPL/publicSearchAction.do`
2. Parse JSON response
3. Store in ExtractedDocument with document_type="w3a"

---

### 3. `plan_from_extractions` - Generate Plan

**Purpose:** Load extractions for a well and generate plugging plan.

**Usage:**
```bash
docker exec regulagent_web python manage.py plan_from_extractions \
  --api 42000012345678
```

**Logic:**
1. Load ExtractedDocument records (W-2, GAU, schematic)
2. Build facts dictionary from extractions
3. Call PolicyApplicator.from_extractions()
4. Save plan to PlanSnapshot

---

### 4. `create_and_set_superuser` - Admin Setup

**Purpose:** Create Django superuser for admin panel.

**Usage:**
```bash
docker exec regulagent_web python manage.py create_and_set_superuser
```

---

## API Views

### 1. `WellRegistryViewSet` - Well CRUD

**Endpoints:**
- `GET /api/wells/` - List wells
- `GET /api/wells/{api14}/` - Retrieve well
- `POST /api/wells/` - Create well
- `PUT /api/wells/{api14}/` - Update well
- `DELETE /api/wells/{api14}/` - Delete well

**Filters:**
- `?state=TX`
- `?county=Andrews`
- `?rrc_district=08A`
- `?field=Spraberry`

---

### 2. `PublicFactsViewSet` - Facts CRUD

**Endpoints:**
- `GET /api/wells/{api14}/facts/` - List facts for well
- `POST /api/wells/{api14}/facts/` - Create fact
- `PUT /api/wells/{api14}/facts/{fact_key}/` - Update fact

---

### 3. `RRCExtractionsView` - Extraction API

**Endpoint:** `POST /api/rrc-extractions/`

**Request:**
```json
{
  "pdf_path": "/path/to/W2.pdf",
  "api14": "42000012345678"
}
```

**Response:**
```json
{
  "extraction_id": 123,
  "document_type": "w2",
  "status": "success",
  "json_data": { ... }
}
```

---

### 4. `W3AFromAPIView` - RRC API Proxy

**Endpoint:** `GET /api/w3a-from-rrc/?api={api14}`

**Response:**
```json
{
  "api14": "42000012345678",
  "w3a_submissions": [ ... ]
}
```

---

## Integration Points

### Provides To:
- **Tenant Overlay** (`tenant_overlay/`) → PublicFacts, WellRegistry
- **Kernel** (`kernel/`) → Facts for plan generation
- **Policy** (`policy/`) → Field/county context

### Consumes From:
- **OpenAI API** → PDF extraction
- **RRC Public API** → W-3A submissions
- **Local filesystem** → PDF files

---

## Testing

**Test Files:**
- Unit tests for extraction logic
- Integration tests for full PDF→Plan flow

**Run tests:**
```bash
docker exec regulagent_web python manage.py test apps.public_core.tests
```

---

## File Structure

```
apps/public_core/
├── models/
│   ├── __init__.py
│   ├── well_registry.py
│   ├── extracted_document.py
│   ├── document_vector.py
│   ├── public_facts.py
│   ├── public_casing_string.py
│   ├── public_well_depths.py
│   ├── public_perforation.py
│   ├── public_artifacts.py
│   └── plan_snapshot.py
├── services/
│   ├── openai_extraction.py      # Main extraction engine
│   └── rrc_completions_extractor.py
├── views/
│   ├── well_registry.py
│   ├── public_facts.py
│   ├── rrc_extractions.py
│   └── w3a_from_api.py
├── serializers/
│   └── ... (DRF serializers)
├── management/
│   └── commands/
│       ├── extract_local_rrc.py
│       ├── get_W3A_from_api.py
│       ├── plan_from_extractions.py
│       └── create_and_set_superuser.py
├── migrations/
│   ├── 0001_initial.py
│   ├── 0003_documentvector_extracteddocument.py
│   └── 0004_enable_vector_extension.py  # pgvector setup
└── admin.py
```

---

## Example Usage

### Extract PDFs and Generate Plan

```bash
# 1. Place PDFs in ra_config/mediafiles/rrc/
# Files: W2_LION_DIAMOND.pdf, GAU_LION_DIAMOND.pdf

# 2. Extract
docker exec regulagent_web python manage.py extract_local_rrc --write

# 3. Verify extraction
docker exec regulagent_web python manage.py shell
>>> from apps.public_core.models import ExtractedDocument
>>> ExtractedDocument.objects.filter(document_type="w2").count()
1
>>> w2 = ExtractedDocument.objects.filter(document_type="w2").first()
>>> w2.json_data["well_info"]["api"]
'42-000-12345-00-00'

# 4. Plan generated automatically by extract_local_rrc
```

---

### Query Public Facts

```python
from apps.public_core.models import WellRegistry, PublicFacts

well = WellRegistry.objects.get(api14="42000012345678")

# Get all facts
facts = PublicFacts.objects.filter(well=well)

# Get specific fact
surface_shoe = PublicFacts.objects.get(well=well, fact_key="surface_shoe_ft")
print(surface_shoe.value)  # 1200
print(surface_shoe.units)  # "ft"
print(surface_shoe.provenance)  # {"source": "w2", ...}
```

---

## Future Enhancements

1. **Vector-Based Recommendations** - "Wells similar to this one"
2. **Automated PDF Monitoring** - Poll RRC for new filings
3. **OCR Fallback** - Tesseract for scanned PDFs
4. **Multi-Format Support** - Excel, CSV, XML uploads
5. **Real-Time RRC Sync** - Webhook-based updates
6. **Quality Scoring** - Confidence metrics for extractions

---

## Maintenance Notes

- **Update extraction prompts** in `openai_extraction_prompts.md`
- **Add new document types** in SUPPORTED_TYPES dict
- **Tune JSON schemas** for better structured output adherence
- **Monitor OpenAI costs** via API usage dashboard

---

## Questions / Support

For questions about public_core:
1. Check ExtractedDocument records in Django admin
2. Review extraction errors in `errors` JSON field
3. Validate JSON schemas match OpenAI response format
4. Test extraction locally before batch processing

