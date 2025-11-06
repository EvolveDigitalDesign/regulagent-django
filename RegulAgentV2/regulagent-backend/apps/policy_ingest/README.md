# Policy Ingest App - Regulatory Rule Ingestion & Mining

## Purpose

The **policy_ingest** app fetches, parses, and stores regulatory rules from external sources (primarily Texas Administrative Code via Cornell Law). It maintains versioned snapshots of TAC Chapter 3 rules in the database, enabling change tracking, citation lookup, and AI-assisted mining of regulatory requirements. This provides the source-of-truth foundation for policy packs and ensures RegulAgent stays synchronized with regulatory changes.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    POLICY_INGEST APP                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  EXTERNAL SOURCES                                                │
│    └─> https://www.law.cornell.edu/regulations/texas/title-16   │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │  Management Commands                                          ││
│  │  ├─> fetch_tx_ch3          Scrape TAC Chapter 3 from Cornell ││
│  │  ├─> create_314_sections   Parse §3.14 into PolicySections  ││
│  │  ├─> tag_tx_ch3            Tag rules with metadata           ││
│  │  ├─> mine_tx_w3a_knobs     AI-mine requirements from text   ││
│  │  └─> update_policy_titles  Enrich rule metadata             ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                   │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │  Django Models (PostgreSQL)                                   ││
│  │  ├─> PolicyRule         TAC rules with versioning            ││
│  │  └─> PolicySection      Subsections (3.14(a), 3.14(b), ...)  ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                   │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │  Services                                                      ││
│  │  └─> extract_policy_yaml.py   AI-powered requirement mining  ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                   │
│  OUTPUT: Structured PolicyRule + PolicySection records in DB     │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### Inputs (From)
- **Cornell Law Website** - Texas Administrative Code HTML
  - URL: `https://www.law.cornell.edu/regulations/texas/title-16/part-1/chapter-3`
  - TAC §3.14 (Plugging), §3.13 (Casing), etc.
  
- **Local Text Files** - Manual regulatory excerpts
  - `3.14.txt` - Full text of TAC §3.14 for reference

### Outputs (To)
- **PostgreSQL Database** - Structured regulatory data
  - `policy_rule` table: Rule metadata, version tags, HTML hashes
  - `policy_section` table: Subsections with hierarchical paths

- **Policy Pack Builder** - Source material for YAML generation
  - AI mining extracts numeric requirements
  - Citations map to TAC subsections

### Data Sources:
- **Web scraping** (BeautifulSoup + requests)
- **HTML parsing** with regex-based nesting detection
- **Version control** via SHA256 hashing

---

## Key Models

### 1. `PolicyRule` Model

**Purpose:** Store top-level TAC rules with versioning and change detection.

**Fields:**
```python
class PolicyRule(models.Model):
    rule_id = models.CharField(max_length=64, db_index=True)
        # Example: "tx.tac.16.3.14"
    
    citation = models.CharField(max_length=128)
        # Human-readable: "16 3 14"
    
    title = models.TextField(blank=True)
        # Example: "Plugging"
    
    source_urls = models.JSONField(default=list)
        # ["https://www.law.cornell.edu/..."]
    
    jurisdiction = models.CharField(max_length=8, null=True, blank=True)
        # "TX", "NM", "OK"
    
    doc_type = models.CharField(max_length=32, default='policy')
        # "policy", "faq", "mou", "other"
    
    topic = models.CharField(max_length=64, null=True, blank=True)
        # "plugging", "casing", "water"
    
    version_tag = models.CharField(max_length=32, db_index=True)
        # "2025-Q1", "manual", "2024-11-15"
    
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    
    html_sha256 = models.CharField(max_length=64, db_index=True)
        # Change detection: SHA256 of source HTML
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

**Unique Together:** `(rule_id, version_tag)`
- Allows multiple versions of same rule
- Example: tx.tac.16.3.14@2024-Q4, tx.tac.16.3.14@2025-Q1

**Indexes:**
- `(rule_id, version_tag)` - Fast version lookup
- `html_sha256` - Change detection queries

---

### 2. `PolicySection` Model

**Purpose:** Store individual subsections with hierarchical paths (e.g., 3.14(a), 3.14(a)(1), 3.14(a)(1)(A)).

**Fields:**
```python
class PolicySection(models.Model):
    rule = models.ForeignKey(PolicyRule, on_delete=models.CASCADE, 
                             related_name='sections')
    
    version_tag = models.CharField(max_length=32)
        # Must match parent PolicyRule.version_tag
    
    path = models.CharField(max_length=128)
        # "a", "a(1)", "a(1)(A)", "b(2)(C)"
    
    heading = models.TextField(blank=True)
        # Section title (if any)
    
    text = models.TextField()
        # Full text content of subsection
    
    anchor = models.CharField(max_length=128, blank=True)
        # HTML anchor for deep linking
    
    order_idx = models.IntegerField()
        # Sequential ordering within rule
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

**Unique Together:** `(rule, version_tag, path)`
- One record per subsection per version

**Example Records:**
```
rule_id: tx.tac.16.3.14, version: 2025-Q1
├─ path: "a",       text: "Definitions. In this section..."
├─ path: "b",       text: "General requirements..."
├─ path: "e",       text: "Surface casing..."
├─ path: "e(1)",    text: "Surface casing shall be..."
├─ path: "e(2)",    text: "At least 100 feet of..."
├─ path: "g",       text: "Usable-quality water..."
└─ path: "g(1)",    text: "Water shall be isolated..."
```

---

## Management Commands

### 1. `fetch_tx_ch3` - Web Scraper

**Purpose:** Fetch Texas TAC Chapter 3 rules from Cornell Law and persist to database.

**Location:** `management/commands/fetch_tx_ch3.py`

**Usage:**
```bash
# Fetch all Chapter 3 rules
docker exec regulagent_web python manage.py fetch_tx_ch3 --write --version-tag 2025-Q1

# Fetch only §3.14
docker exec regulagent_web python manage.py fetch_tx_ch3 --rule tx.tac.16.3.14 --write

# Dry run (preview)
docker exec regulagent_web python manage.py fetch_tx_ch3 --dry-run
```

**Arguments:**
- `--rule`: Limit to specific rule_id (e.g., tx.tac.16.3.14)
- `--write`: Persist to database (default: dry run)
- `--dry-run`: Print summaries without writing
- `--version-tag`: Version identifier (default: "manual")

**Logic Flow:**

**Step 1: Fetch Index Page**
```python
def fetch_html(url: str) -> str:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text
```
- GET `https://www.law.cornell.edu/regulations/texas/title-16/part-1/chapter-3`
- Returns HTML with links to all §3.x pages

**Step 2: Parse Chapter Index**
```python
def parse_chapter_index(html: str) -> List[Tuple[str, str]]:
```
- Use BeautifulSoup to find all `<a>` tags
- Extract section number from link text: `§ 3.14` → `"14"`
- Match href patterns: `/regulations/texas/title-16/part-1/chapter-3/section-3-14`
- Build rule_id: `tx.tac.16.3.{num}`
- Return list of (rule_id, url) tuples

**Example Output:**
```python
[
  ('tx.tac.16.3.1', 'https://www.law.cornell.edu/.../section-3-1'),
  ('tx.tac.16.3.13', 'https://www.law.cornell.edu/.../section-3-13'),
  ('tx.tac.16.3.14', 'https://www.law.cornell.edu/.../section-3-14'),
  ...
]
```

**Step 3: Parse Rule Sections**
```python
def parse_rule_sections(html: str) -> Iterable[Section]:
```

**Nesting Detection Algorithm:**
1. Extract content containers: `<article>`, `<main>`, `#content`
2. Find all `<p>`, `<li>`, `<div>` nodes
3. **Regex pattern:** `^\((?P<tok>[a-z]|[0-9]+|[A-Z])\)\s*`
   - Matches: `(a)`, `(1)`, `(A)` at line start
4. **Determine nesting level by token type:**
   - Lowercase letters (a-z) → Level 0
   - Digits (0-9) → Level 1
   - Uppercase letters (A-Z) → Level 2
5. **Maintain nesting stack:**
   - Stack: `['a', '1', 'A']` → Path: `a(1)(A)`
   - When same/higher level encountered → flush buffer
6. **Skip boilerplate:** "Notes", "State Regulations Toolbox"
7. Return ordered Section objects

**Example Parsing:**
```
Input HTML:
  (a) Definitions. In this section...
  (b) General requirements. Each well...
  (1) The operator shall...
  (2) All cement must...

Output Sections:
  Section(path='a', text='Definitions. In this section...', order_idx=0)
  Section(path='b', text='General requirements. Each well...', order_idx=1)
  Section(path='b(1)', text='The operator shall...', order_idx=2)
  Section(path='b(2)', text='All cement must...', order_idx=3)
```

**Step 4: Persist to Database**
```python
rule_obj, _ = PolicyRule.objects.update_or_create(
    rule_id=rule_id,
    version_tag=version_tag,
    defaults={
        'citation': rule_id.replace('tx.tac.', '').replace('.', ' '),
        'title': '',
        'source_urls': [url],
        'jurisdiction': 'TX',
        'doc_type': 'policy',
        'topic': 'plugging' if rule_id.endswith('3.14') else None,
        'html_sha256': html_sha,
    }
)

# Replace sections for this version
PolicySection.objects.filter(rule=rule_obj, version_tag=version_tag).delete()
batch = [PolicySection(rule=rule_obj, version_tag=version_tag, ...) for s in sections]
PolicySection.objects.bulk_create(batch, batch_size=500)
```

**Change Detection:**
- SHA256 hash of HTML content
- If hash unchanged, skip re-parse (optimization for future runs)

---

### 2. `create_314_sections` - Section Loader

**Purpose:** Parse local `3.14.txt` file into PolicySection records (alternative to web scraping).

**Usage:**
```bash
docker exec regulagent_web python manage.py create_314_sections
```

**Logic:**
1. Read `apps/policy_ingest/3.14.txt`
2. Parse with same nesting algorithm as fetch_tx_ch3
3. Create/update PolicyRule for tx.tac.16.3.14
4. Bulk create PolicySection records

**Use Case:**
- Offline development without network access
- Working with pre-downloaded regulatory text
- Testing parser improvements

---

### 3. `tag_tx_ch3` - Metadata Enrichment

**Purpose:** Add topic and doc_type tags to existing PolicyRule records.

**Usage:**
```bash
docker exec regulagent_web python manage.py tag_tx_ch3
```

**Tagging Rules:**
- `tx.tac.16.3.14` → topic="plugging"
- `tx.tac.16.3.13` → topic="casing"
- `tx.tac.16.3.8` → topic="water"
- Default → topic=None

---

### 4. `mine_tx_w3a_knobs` - AI-Powered Requirement Mining

**Purpose:** Use AI (OpenAI GPT) to extract structured requirements from PolicySection text.

**Usage:**
```bash
docker exec regulagent_web python manage.py mine_tx_w3a_knobs \
  --version-tag 2025-Q1 \
  --output requirements.json
```

**Arguments:**
- `--version-tag`: PolicyRule version to mine
- `--output`: JSON file path for extracted requirements

**Logic Flow:**

**Step 1: Load PolicySections**
```python
sections = PolicySection.objects.filter(
    rule__rule_id='tx.tac.16.3.14',
    version_tag=version_tag
).order_by('order_idx')
```

**Step 2: Build Context Prompt**
```python
prompt = f"""
Extract structured requirements from Texas TAC §3.14:

{section.path}: {section.text}

Extract:
- Numeric requirements (minimum feet, hours, depths)
- Boolean requirements (required/not required)
- Cement class specifications
- Coverage requirements
- Tagging/verification requirements

Format as JSON:
{{
  "surface_casing_shoe_plug_min_ft": {{
    "value": 100,
    "citation": "tx.tac.16.3.14(e)(2)",
    "text": "at least 100 feet of cement"
  }}
}}
"""
```

**Step 3: Call OpenAI API**
```python
from openai import OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": "You are a regulatory requirements extractor."},
        {"role": "user", "content": prompt}
    ],
    response_format={"type": "json_object"}
)

requirements = json.loads(response.choices[0].message.content)
```

**Step 4: Validate & Aggregate**
- Merge requirements from all subsections
- Validate numeric ranges (min_ft > 0, hours ≥ 0)
- Cross-reference citations with PolicySection.path
- Emit warnings for ambiguous text

**Step 5: Output JSON**
```json
{
  "surface_casing_shoe_plug_min_ft": {
    "value": 100,
    "citation_keys": ["tx.tac.16.3.14(e)(2)"],
    "source_text": "at least 100 feet of cement",
    "confidence": "high"
  },
  "cement_above_cibp_min_ft": {
    "value": 20,
    "citation_keys": ["tx.tac.16.3.14(g)(3)"],
    "source_text": "at least 20 feet of cement above",
    "confidence": "high"
  },
  "uqw_isolation_min_len_ft": {
    "value": 100,
    "citation_keys": ["tx.tac.16.3.14(g)(1)"],
    "source_text": "isolate water by placing at least 100 feet",
    "confidence": "high"
  },
  "tag_wait_hours": {
    "value": 4,
    "citation_keys": ["tx.tac.16.3.14(d)(11)"],
    "source_text": "wait at least 4 hours before tagging",
    "confidence": "medium"
  }
}
```

**Use Case:**
- Bootstrap new policy packs from regulatory text
- Validate existing YAML packs against source
- Track regulatory changes by comparing mined requirements across versions

---

### 5. `update_policy_titles` - Title Enrichment

**Purpose:** Populate PolicyRule.title field from HTML headings or manual mapping.

**Usage:**
```bash
docker exec regulagent_web python manage.py update_policy_titles
```

**Logic:**
- Query all PolicyRule records with empty title
- Lookup title mapping: `{'3.14': 'Plugging', '3.13': 'Casing', ...}`
- Update PolicyRule.title field
- Save changes

---

## Services

### 1. `extract_policy_yaml.py` - YAML Generation Assistant

**Purpose:** AI-assisted conversion of mined requirements to YAML policy pack format.

**Location:** `services/extract_policy_yaml.py` (assumed, not shown in snippet)

**Functions:**

#### **`mine_requirements_from_sections(rule_id, version_tag)`**
**Purpose:** Extract structured requirements from PolicySection records.

**Logic:** Same as `mine_tx_w3a_knobs` command but as library function

---

#### **`generate_yaml_pack(requirements_json)`**
**Purpose:** Convert JSON requirements to YAML policy pack structure.

**Logic:**
1. Load JSON requirements
2. Build YAML structure:
   - `policy_id`, `version`, `jurisdiction`
   - `base.requirements` from JSON
   - `base.cement_class` from JSON
   - `base.citations` aggregated
3. Write YAML file to `apps/policy/packs/tx/`

**Example Output:**
```yaml
policy_id: tx.w3a
policy_version: 2025-Q1

base:
  requirements:
    surface_casing_shoe_plug_min_ft:
      value: 100
      citation_keys: [tx.tac.16.3.14(e)(2)]
  
  cement_class:
    cutoff_ft: 4000
    shallow_class: A
    deep_class: H
```

---

## API Endpoints (Django REST Framework)

### `GET /api/policy-rules/`
**Purpose:** List all PolicyRule records with filtering.

**Query Params:**
- `rule_id`: Filter by rule_id (e.g., `tx.tac.16.3.14`)
- `version_tag`: Filter by version
- `jurisdiction`: Filter by jurisdiction (TX, NM, OK)
- `topic`: Filter by topic (plugging, casing, water)

**Response:**
```json
[
  {
    "id": 1,
    "rule_id": "tx.tac.16.3.14",
    "citation": "16 3 14",
    "title": "Plugging",
    "jurisdiction": "TX",
    "topic": "plugging",
    "version_tag": "2025-Q1",
    "source_urls": ["https://www.law.cornell.edu/..."],
    "effective_from": "2025-01-01",
    "html_sha256": "abc123...",
    "section_count": 45
  }
]
```

---

### `GET /api/policy-rules/{rule_id}/sections/`
**Purpose:** Retrieve all sections for a specific rule and version.

**Response:**
```json
[
  {
    "path": "a",
    "heading": "Definitions",
    "text": "In this section, the following words...",
    "order_idx": 0
  },
  {
    "path": "e(2)",
    "heading": "",
    "text": "At least 100 feet of cement shall be placed...",
    "order_idx": 12
  }
]
```

---

### `POST /api/policy-rules/mine/`
**Purpose:** Trigger AI mining for a specific rule.

**Request:**
```json
{
  "rule_id": "tx.tac.16.3.14",
  "version_tag": "2025-Q1"
}
```

**Response:**
```json
{
  "status": "success",
  "requirements": { ... },
  "confidence": "high"
}
```

---

## Testing

**Test Files:**
- `test_miner_tx_w3a.py` - AI mining validation

**Run tests:**
```bash
docker exec regulagent_web python manage.py test apps.policy_ingest.tests
```

**Coverage:**
- HTML parsing with nested subsections
- Change detection (SHA256 hashing)
- Section ordering
- Nesting stack logic
- AI prompt/response validation

---

## Integration Points

### Provides To:
- **Policy Pack Builder** - Source material for YAML generation
- **Citation Lookup** - Deep links to regulatory source
- **Change Tracking** - Version diffs for regulatory updates

### Consumes From:
- **Cornell Law Website** - Live regulatory text
- **OpenAI API** - AI-powered requirement extraction
- **Local text files** - Offline regulatory snapshots

---

## Key Concepts

### 1. **Versioned Snapshots**
- Each scrape creates new version_tag
- Multiple versions of same rule coexist
- Compare versions to detect changes

### 2. **Hierarchical Nesting**
- Subsections follow legal convention: (a), (1), (A)
- Nesting stack maintains parent path
- Paths like `a(1)(A)` enable precise citation

### 3. **Change Detection**
- SHA256 hash of source HTML
- If unchanged, skip expensive re-parse
- Track when rules were last updated

### 4. **AI-Powered Mining**
- Extract structured requirements from natural language
- Cross-validate against multiple subsections
- Confidence scoring for ambiguous text

---

## File Structure

```
apps/policy_ingest/
├── models/
│   ├── __init__.py
│   ├── policy_rule.py           # PolicyRule model
│   └── policy_section.py        # PolicySection model
├── management/
│   └── commands/
│       ├── fetch_tx_ch3.py      # Web scraper
│       ├── create_314_sections.py
│       ├── tag_tx_ch3.py
│       ├── mine_tx_w3a_knobs.py
│       └── update_policy_titles.py
├── services/
│   └── extract_policy_yaml.py   # YAML generation
├── migrations/
│   ├── 0001_initial.py
│   └── 0002_policyrule_doc_type_...py
├── tests/
│   └── test_miner_tx_w3a.py
├── 3.14.txt                     # Local TAC §3.14 snapshot
├── tx_rrc_w3a_base_policy_pack.yaml  # Reference pack
├── admin.py                     # Django admin config
├── serializers.py               # DRF serializers
├── urls.py                      # API routes
└── views.py                     # API views
```

---

## Example Usage

### Scrape Latest TAC §3.14

```bash
# Fetch and persist
docker exec regulagent_web python manage.py fetch_tx_ch3 \
  --rule tx.tac.16.3.14 \
  --write \
  --version-tag 2025-Q2

# Verify sections created
docker exec regulagent_web python manage.py shell
>>> from apps.policy_ingest.models import PolicyRule, PolicySection
>>> rule = PolicyRule.objects.get(rule_id='tx.tac.16.3.14', version_tag='2025-Q2')
>>> rule.sections.count()
47
>>> rule.sections.filter(path__startswith='e').values_list('path', 'text')[:5]
[('e', 'Surface casing...'), ('e(1)', 'Surface casing shall...'), ...]
```

---

### Mine Requirements

```bash
# Run AI mining
docker exec regulagent_web python manage.py mine_tx_w3a_knobs \
  --version-tag 2025-Q2 \
  --output /tmp/requirements.json

# Review output
cat /tmp/requirements.json | jq '.surface_casing_shoe_plug_min_ft'
{
  "value": 100,
  "citation_keys": ["tx.tac.16.3.14(e)(2)"],
  "confidence": "high"
}
```

---

### Compare Versions

```python
from apps.policy_ingest.models import PolicyRule

# Load two versions
v1 = PolicyRule.objects.get(rule_id='tx.tac.16.3.14', version_tag='2024-Q4')
v2 = PolicyRule.objects.get(rule_id='tx.tac.16.3.14', version_tag='2025-Q1')

# Compare HTML hashes
if v1.html_sha256 != v2.html_sha256:
    print("Rule changed between versions!")
    
    # Find changed sections
    v1_sections = {s.path: s.text for s in v1.sections.all()}
    v2_sections = {s.path: s.text for s in v2.sections.all()}
    
    for path in v1_sections.keys() | v2_sections.keys():
        if v1_sections.get(path) != v2_sections.get(path):
            print(f"Changed: {path}")
```

---

## Future Enhancements

1. **Automated Change Alerts** - Email/Slack when RRC updates TAC
2. **Diff Visualization** - Side-by-side comparison UI for versions
3. **Multi-Jurisdiction** - Scrape New Mexico, Oklahoma regulations
4. **Citation Graph** - Map cross-references between rules
5. **Natural Language Search** - "Find rules about water isolation"
6. **Regulatory Calendar** - Track effective dates for upcoming changes

---

## Maintenance Notes

- **Update scraper** if Cornell Law HTML structure changes
- **Adjust nesting patterns** if legal formatting conventions change
- **Tune AI prompts** for better requirement extraction
- **Add new topics** in tagging logic for new rule families

---

## Questions / Support

For questions about policy ingestion:
1. Check fetch_tx_ch3.py for scraping logic
2. Review PolicySection records in Django admin
3. Validate HTML parsing with --dry-run flag
4. Test AI mining on small text samples first

