# W3A From API Endpoint Specification

## Overview
Generate a W3A (plugging) plan by providing a 10-digit API number. The system will automatically extract documents from the RRC or accept user-uploaded documents.

---

## Endpoint

```
POST /api/plans/w3a/from-api
```

### Authentication
- **Required**: Yes (JWT Bearer token)
- Header: `Authorization: Bearer <token>`

---

## Request Parameters

### Required Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `api10` | string | **10-digit API number** (e.g., "4200346118") |

### Optional Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `plugs_mode` | string | `"combined"` | Plug merging strategy:<br>• `"combined"` - Merge nearby plugs<br>• `"isolated"` - Keep plugs separate<br>• `"both"` - Return both variants |
| `input_mode` | string | `"extractions"` | Document source:<br>• `"extractions"` - Auto-fetch from RRC<br>• `"user_files"` - Use uploaded files only<br>• `"hybrid"` - Prefer uploads, fallback to RRC |
| `merge_threshold_ft` | float | `500.0` | Distance (feet) within which plugs can be merged when `plugs_mode="combined"` |
| `confirm_fact_updates` | boolean | `false` | If `true`, automatically apply well data updates from extracted documents |
| `allow_precision_upgrades_only` | boolean | `true` | Only allow updates that improve precision (prevent data loss) |
| `use_gau_override_if_invalid` | boolean | `false` | If `true` and GAU file is provided, use it even if existing GAU is valid |

### File Uploads (Optional)

| Parameter | Type | Description |
|-----------|------|-------------|
| `gau_file` | file | GAU (Groundwater Advisory Unit) letter PDF |
| `w2_file` | file | W-2 (Well Completion Report) PDF |
| `w15_file` | file | W-15 (Cementing Report) PDF |
| `schematic_file` | file | Well schematic PDF (future use) |
| `formation_tops_file` | file | Formation tops PDF (future use) |

---

## Request Examples

### Example 1: Basic Request (RRC Auto-Extract)

**HTTP Request:**
```http
POST /api/plans/w3a/from-api
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
Content-Type: application/json

{
  "api10": "4200346118"
}
```

**cURL:**
```bash
curl -X POST http://127.0.0.1:8001/api/plans/w3a/from-api \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "api10": "4200346118"
  }'
```

### Example 2: Request Both Variants

```json
{
  "api10": "4200346118",
  "plugs_mode": "both"
}
```

### Example 3: Upload Custom Documents

**HTTP Request:**
```http
POST /api/plans/w3a/from-api
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
Content-Type: multipart/form-data

api10=4200346118
input_mode=user_files
gau_file=<binary GAU PDF>
w2_file=<binary W-2 PDF>
w15_file=<binary W-15 PDF>
```

**cURL:**
```bash
curl -X POST http://127.0.0.1:8001/api/plans/w3a/from-api \
  -H "Authorization: Bearer $TOKEN" \
  -F "api10=4200346118" \
  -F "input_mode=user_files" \
  -F "gau_file=@/path/to/GAU_LETTER.pdf" \
  -F "w2_file=@/path/to/W-2.pdf" \
  -F "w15_file=@/path/to/W-15.pdf"
```

### Example 4: Hybrid Mode with Custom Merge Threshold

```json
{
  "api10": "4200346118",
  "plugs_mode": "combined",
  "input_mode": "hybrid",
  "merge_threshold_ft": 300.0,
  "gau_file": "<uploaded file>"
}
```

---

## Response Format

### Success Response (200 OK)

#### Single Variant (`plugs_mode="combined"` or `"isolated"`)

```json
{
  "api": "4200346118",
  "field": "SPRABERRY (TREND AREA)",
  "county": "ANDREWS",
  "district": "08A",
  "jurisdiction": "TX",
  "kernel_version": "0.1.0",
  
  "steps": [
    {
      "type": "uqw_isolation_plug",
      "top_ft": null,
      "bottom_ft": null,
      "sacks": null,
      "regulatory_basis": [
        "tx.tac.16.3.14(g)(1)",
        "rrc.district.08a.andrews:tag.required"
      ],
      "details": {
        "verification": {
          "action": "TAG",
          "required_wait_hr": 4
        },
        "uqw_base_source": "none",
        "uqw_base_age_days": 4246
      },
      "special_instructions": null
    }
    // ... more steps (typically 10-15 steps)
  ],
  
  "violations": [],
  
  "materials_totals": {
    "total_sacks": 146,
    "total_bbl": 31.0
  },
  
  "rrc_export": [
    {
      "plug_no": 1,
      "type": "productive_horizon_isolation_plug",
      "from_ft": 10864.0,
      "to_ft": 10964.0,
      "sacks": null,
      "remarks": "tx.tac.16.3.14(k)"
    }
    // ... more plugs (formatted for RRC submission)
  ],
  
  "formations_targeted": [
    "Dean",
    "Lo",
    "San Andres",
    "Santa Rosa",
    "Up"
  ],
  
  "formation_tops_detected": [
    "atoka",
    "clearfork",
    "dean",
    "grayburg",
    "queen",
    "san andres - co2 flood, high flows, h2s, corrosive",
    "seven rivers",
    "spraberry",
    "strawn",
    "wolfcamp",
    "yates"
  ],
  
  "gau_protect_intervals": [
    {
      "source": "gau",
      "top_ft": 300.0,
      "bottom_ft": 0.0
    }
  ],
  
  "field_resolution": {
    "method": "nearest_county",
    "matched_field": "SPRABERRY (TREND AREA)",
    "requested_field": "SPRABERRY (TREND AREA)",
    "matched_in_county": "Martin County",
    "nearest_distance_km": 64.53
  },
  
  "extraction": {
    "status": "success",
    "source": "cache",
    "output_dir": "/app/ra_config/mediafiles/uploads/rrc/completions/4200346118",
    "files": [
      "/app/ra_config/mediafiles/uploads/rrc/completions/4200346118/GAU_LETTER_4200346118.pdf",
      "/app/ra_config/mediafiles/uploads/rrc/completions/4200346118/W-2_4200346118.pdf",
      "/app/ra_config/mediafiles/uploads/rrc/completions/4200346118/W-15_4200346118.pdf"
    ]
  },
  
  "facts_update_preview": {
    "lat": {
      "before": null,
      "after": 32.1875,
      "source": "w2"
    },
    "lon": {
      "before": null,
      "after": -102.225,
      "source": "w2"
    },
    "field_name": {
      "before": "",
      "after": "SPRABERRY (TREND AREA)",
      "source": "w2"
    }
  }
}
```

#### Both Variants (`plugs_mode="both"`)

```json
{
  "variants": {
    "combined": {
      // ... same structure as single variant above
    },
    "isolated": {
      // ... same structure as single variant above
    }
  },
  "extraction": {
    // ... extraction metadata
  },
  "facts_update_preview": {
    // ... proposed well data updates
  }
}
```

---

## Response Fields Explained

### Core Plan Data

| Field | Type | Description |
|-------|------|-------------|
| `api` | string | API number of the well |
| `field` | string | Oil/gas field name |
| `county` | string | County name |
| `district` | string | RRC district (e.g., "08A") |
| `jurisdiction` | string | State code (e.g., "TX") |
| `kernel_version` | string | Policy kernel version used |

### Steps Array

Each step object contains:

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Step type (e.g., `"cement_plug"`, `"bridge_plug"`, `"formation_top_plug"`) |
| `top_ft` | float/null | Top depth in feet |
| `bottom_ft` | float/null | Bottom depth in feet |
| `sacks` | int/null | Cement sacks required |
| `cement_class` | string/null | Cement class (e.g., "C", "H") |
| `regulatory_basis` | array | Rule citations (e.g., `["tx.tac.16.3.14(g)(1)"]`) |
| `details` | object | Additional step-specific metadata |
| `special_instructions` | string/null | Special notes or instructions |

### Violations Array

If plan has violations:

```json
"violations": [
  {
    "severity": "error",
    "rule_id": "tx.tac.16.3.14(e)(2)",
    "message": "Surface casing shoe coverage required",
    "context": {
      "required_ft": 50,
      "actual_ft": 0
    }
  }
]
```

### Materials Totals

| Field | Type | Description |
|-------|------|-------------|
| `total_sacks` | int | Total cement sacks across all plugs |
| `total_bbl` | float | Total barrels of cement |

### RRC Export Array

Pre-formatted plugs for RRC W-3A submission. Each entry:

| Field | Type | Description |
|-------|------|-------------|
| `plug_no` | int | Sequential plug number |
| `type` | string | Plug type (RRC terminology) |
| `from_ft` | float | Bottom depth |
| `to_ft` | float | Top depth |
| `sacks` | int/null | Cement sacks |
| `remarks` | string | Combined regulatory basis and notes |

### Extraction Metadata

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Extraction status (`"success"`, `"partial"`, `"error"`) |
| `source` | string | Data source (`"cache"`, `"rrc"`, `"user_upload"`) |
| `output_dir` | string | Directory where extracted files are stored |
| `files` | array | List of file paths used |

---

## Error Responses

### 400 Bad Request

**Invalid API number:**
```json
{
  "api10": [
    "api10 must contain exactly 10 digits"
  ]
}
```

**Missing required file:**
```json
{
  "non_field_errors": [
    "use_gau_override_if_invalid requires gau_file to be provided"
  ]
}
```

### 401 Unauthorized

```json
{
  "detail": "Authentication credentials were not provided."
}
```

### 500 Internal Server Error

```json
{
  "detail": "Failed to extract documents from RRC: connection timeout"
}
```

---

## Side Effects

When this endpoint is called:

1. **Creates/Updates `WellRegistry`** - Well metadata is registered/enriched
2. **Creates `ExtractedDocument`** records - Stores extracted W-2, W-15, GAU data
3. **Creates `PlanSnapshot`** - Baseline plan snapshot (status: `draft`, visibility: `public`)
4. **Creates `WellEngagement`** - Tracks tenant interaction with the well
5. **Creates `DocumentVector`** - Generates embeddings for semantic search (async)

---

## Frontend Integration Tips

### 1. Basic Flow

```typescript
// Step 1: Generate plan
const response = await fetch('/api/plans/w3a/from-api', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    api10: '4200346118',
    plugs_mode: 'combined',
  }),
});

const plan = await response.json();

// Step 2: Get the plan_id from well engagement
const planId = `${plan.api}:combined`;

// Step 3: Retrieve full plan with well geometry
const fullPlanResponse = await fetch(`/api/plans/${planId}/`, {
  method: 'GET',
  headers: {
    'Authorization': `Bearer ${token}`,
  },
});

const fullPlan = await fullPlanResponse.json();
// fullPlan.payload contains the plan
// fullPlan.well_geometry contains casing, formations, etc.
```

### 2. Progress Indication

This endpoint can take 5-30 seconds depending on:
- Document availability (cache vs. RRC fetch)
- Number of documents to extract
- Complexity of well geometry

**Recommended UI:**
- Show loading spinner
- Display status message: "Extracting documents from RRC..."
- After 10s: "Analyzing well geometry..."
- After 20s: "Generating compliance plan..."

### 3. Error Handling

```typescript
try {
  const response = await fetch('/api/plans/w3a/from-api', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ api10: userInput }),
  });

  if (!response.ok) {
    const error = await response.json();
    if (response.status === 400) {
      // Validation error - show user-friendly message
      alert(`Invalid input: ${JSON.stringify(error)}`);
    } else if (response.status === 404) {
      // Well not found or no documents available
      alert('No documents found for this well. Try uploading documents manually.');
    } else {
      // Server error
      alert('Failed to generate plan. Please try again later.');
    }
    return;
  }

  const plan = await response.json();
  // Success - proceed with displaying plan
} catch (err) {
  // Network error
  alert('Network error. Please check your connection.');
}
```

### 4. File Upload Example

```typescript
const formData = new FormData();
formData.append('api10', '4200346118');
formData.append('input_mode', 'user_files');
formData.append('gau_file', gauFileBlob, 'GAU_LETTER.pdf');
formData.append('w2_file', w2FileBlob, 'W-2.pdf');
formData.append('w15_file', w15FileBlob, 'W-15.pdf');

const response = await fetch('/api/plans/w3a/from-api', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    // Note: Do NOT set Content-Type for FormData
  },
  body: formData,
});
```

---

## Testing

**Demo Credentials:**
- Email: `demo@example.com`
- Password: `demo123`

**Test API Numbers:**
- `4200346118` - Complete data (W-2, W-15, GAU)
- Try your own Texas RRC API numbers!

---

## Related Endpoints

After generating a plan with this endpoint:

- **Get full plan with geometry**: `GET /api/plans/{plan_id}/`
- **Get plan status**: `GET /api/plans/{plan_id}/status/`
- **Modify plan (start editing)**: `POST /api/plans/{plan_id}/status/modify/`
- **Approve plan**: `POST /api/plans/{plan_id}/status/approve/`
- **File plan**: `POST /api/plans/{plan_id}/status/file/`
- **Get well history**: `GET /api/tenant/wells/history/`
- **Get specific well**: `GET /api/tenant/wells/{api14}/`

---

## Notes

- **Caching**: Documents are cached after first extraction. Subsequent requests for the same API are fast.
- **Tenant Isolation**: Plans are attributed to the authenticated tenant. Baseline plans are public (shareable), but work-in-progress edits are private.
- **Auto-enrichment**: Well metadata (operator, field, lease, lat/lon) is automatically extracted and stored.
- **Compliance**: All plans are validated against TX RRC District-specific rules and TAC Chapter 3.14.

---

**Last Updated**: 2025-11-02
**API Version**: v1

