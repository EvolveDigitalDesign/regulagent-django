# ⚙️ RegulAgent Data Lifecycle After OpenAI Extraction

This document defines **how RegulAgent processes structured JSON returned by OpenAI** for any regulatory document (W-2, GAU, W-15, Schematic, Formation Tops, etc.) and how it integrates the data into both structured and semantic (vector) storage.

---

## 🧠 Overview

When a document is processed through OpenAI, RegulAgent receives a structured JSON output following the correct schema for that form. That JSON must be:

1. Converted into normalized relational records in the Django ORM (structured data).
2. Embedded semantically into the `DocumentVector` model (vector data) for AI reasoning.

This ensures every data point is both **factually queryable** and **semantically retrievable**.

---

## 🧩 1. Data Processing Flow

### **Step 1 – Extract → JSON**
OpenAI returns a full structured JSON according to the relevant schema (e.g., `W-2`, `GAU`, etc.).

Example:
```json
{
  "header": {...},
  "operator_info": {...},
  "formation_record": [...],
  "perforations": [...]
}
```

---

### **Step 2 – Sync → Structured Models**
For each key in the JSON, RegulAgent should:

1. **Find or create** existing ORM objects.
   - Match on unique identifiers (e.g., `api_number`, `depth_range`, `formation_name`, etc.).
   - Use `update_or_create()` or custom logic to merge duplicates.

2. **Maintain consistency**:
   - If a record already exists → update changed fields.
   - If not → create a new record.

3. This ensures domain-level truth stays accurate and reflects the latest regulatory data.

Example:
```python
for perf in json_data["perforations"]:
    PublicPerforation.objects.update_or_create(
        well=well_obj,
        top_depth_ft=perf["top_ft"],
        bottom_depth_ft=perf["bottom_ft"],
        defaults={
            "formation": perf.get("formation"),
            "status": perf.get("status"),
        }
    )
```

---

### **Step 3 – Generate → DocumentVector Entries**
Once structured data is synced, the system generates embeddings for semantic retrieval.

Each logical section or record in the JSON becomes a **chunk** to embed.

Each chunk should include:
- `well` (FK to well)
- `document_type`
- `section_name`
- `section_text`
- `embedding`
- `metadata` (record references or other identifiers)

Example:
```python
embedding = client.embeddings.create(
    model="text-embedding-3-small",
    input=section_text
).data[0].embedding

DocumentVector.objects.create(
    well=well_obj,
    document_type="W2",
    section_name="formation_record",
    section_text=section_text,
    embedding=embedding,
    metadata={"record_ids": [obj.id for obj in created_records]}
)
```

This allows both structured querying and vector-based AI retrieval.

---

### **Step 4 – Detect & Append New Data**
If new data appears (e.g., an additional perforation, casing interval, or formation):
- The ORM `update_or_create()` logic automatically detects and updates records.
- Optionally, append a **diff record** in `DocumentVector.metadata` to track version deltas.

This makes the system self-learning and cumulative over time.

---

## 🧠 2. Data Model Roles

| Model | Role | Description |
|--------|------|--------------|
| `WellRegistry` | Primary Well Index | Holds well identifiers, API numbers, and associations. |
| `PublicPerforation`, `PublicCasingString`, etc. | Structured Data Models | Contain factual domain data extracted from JSON. |
| `DocumentVector` | Semantic Layer | Holds embeddings for semantic retrieval and reasoning. |

---

## 🧩 3. Example Flow Summary

```python
def process_well_document(file, doc_type, well_obj):
    json_data = extract_json(file, doc_type)  # via GPT-4.1
    sync_structured_models(json_data, well_obj)  # update/create ORM objects
    generate_document_vectors(json_data, well_obj, doc_type)  # embed & store
```

---

## 🔄 4. Lifecycle Diagram

```
 PDF Document
      ↓
 OpenAI Extraction (GPT-4.1)
      ↓
 Structured JSON (Schema-Validated)
      ↓ ↓
 Update Django Models     →     Generate DocumentVector Entries
 (Relational Truth)              (Semantic Knowledge)
      ↓                                 ↓
 Well Data Updated          Vectors Ready for RAG + Search
```

---

## ⚙️ 5. DocumentVector Schema

```python
from pgvector.django import VectorField
from django.contrib.postgres.fields import JSONField

class DocumentVector(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    well = models.ForeignKey('WellRegistry', on_delete=models.CASCADE, related_name='vectors', null=True)
    file_name = models.CharField(max_length=255)
    document_type = models.CharField(max_length=50)
    section_name = models.CharField(max_length=255)
    section_text = models.TextField()
    embedding = VectorField(dimensions=1536)
    metadata = JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
```

---

## 🧮 6. Vector Indexing and Search

```sql
CREATE INDEX ON document_vectors USING hnsw (embedding vector_cosine_ops);
```

Search query example:
```sql
SELECT section_name, section_text, 1 - (embedding <=> query_embedding) AS similarity
FROM document_vectors
ORDER BY embedding <=> query_embedding
LIMIT 5;
```

---

## ✅ 7. Benefits of This Architecture

- **Structured + Semantic Duality** → Factual accuracy meets contextual search.
- **Automatic Versioning** → Detect and append new or changed records.
- **AI-Ready Knowledge Graph** → Supports future RegulAgent reasoning models.
- **Extensible Across File Types** → Works for W-2, W-3A, W-15, GAU, and any new form.

---

## 🚀 8. Future Enhancements

- Add `version` or `revision_date` tracking in both structured and vector models.
- Generate automatic summaries (e.g., 'Changes since last submission').
- Fine-tune embeddings for well similarity, casing strategies, or regulatory compliance.
- Implement RAG agents using this vector layer for contextual retrieval.

---

**In short:**
Every time RegulAgent processes a new document, it updates the structured truth, enriches the semantic layer, and makes the entire regulatory dataset AI-searchable and self-improving over time.