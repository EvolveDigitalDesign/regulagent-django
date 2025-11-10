# 🧠 RegulAgent Extraction Configuration

This document defines how the OpenAI API will handle automatic data extraction from uploaded regulatory documents. It includes:
- The **model** we’ll use.
- The **file types currently supported**.
- The **prompt templates** for each document type.

---

## ⚙️ Model Selection

| Purpose | Model | Reason |
|----------|--------|--------|
| **Primary Extraction** | `gpt-4.1` | Highest accuracy and layout reasoning across scanned PDFs, tables, and text-based filings. |
| **High-Volume / Batch Extraction** | `gpt-4.1-preview` | Faster and cheaper; good for parallel processing. |
| **Document Classification** | `gpt-4o-mini` | Lightweight for identifying file types before extraction. |

---

## 📂 Currently Supported File Types

| File Type | Description | Schema Key |
|------------|--------------|-------------|
| GAU | Groundwater Protection Determination (Form GW-2) | `gau` |
| W-2 | Oil Well Potential Test / Completion Report | `w2` |
| W-15 | Cementing Report | `w15` |
| Schematic | Vertical Wellbore Diagram | `schematic` |
| Formation Tops | Depth chart of formations encountered | `formation_tops` |

---

## 🧩 Prompt Templates by File Type

Each prompt is designed for use with the **OpenAI Chat Completions API** using the following base call pattern:

```python
response = client.chat.completions.create(
  model="gpt-4.1",
  messages=[
    {
      "role": "system",
      "content": "You are a regulatory data extraction model for the Texas Railroad Commission and associated filings. Return all factual values exactly as shown in the document. Do not infer or normalize. Return valid JSON only, matching the schema provided."
    },
    {
      "role": "user",
      "content": [
        {"type": "input_text", "text": "<INSERT PROMPT TEMPLATE BELOW>"},
        {"type": "input_file", "file_id": "<PDF_FILE_ID>"}
      ]
    }
  ],
  response_format={"type": "json_object"}
)
```

---

### 1️⃣ GAU – Groundwater Protection Determination (Form GW-2)

```text
Extract all visible factual data from this document.  
Return it as JSON following this schema:

{
  "header": {
    "form_name": "Groundwater Protection Determination",
    "form_id": "GW-2",
    "issuing_agency": "",
    "date_issued": "",
    "gau_number": ""
  },
  "operator_info": {
    "attention": "",
    "address": "",
    "operator_number": ""
  },
  "well_info": {
    "api_number": "",
    "county": "",
    "lease_name": "",
    "lease_number": "",
    "well_number": "",
    "total_vertical_depth_ft": "",
    "latitude": "",
    "longitude": "",
    "datum": ""
  },
  "purpose_and_location": {
    "purpose": "",
    "legal_description": ""
  },
  "recommendation": {
    "base_of_usable_quality_water_ft": "",
    "fresh_water_isolation_interval_ft": {"from": "", "to": ""},
    "usd_w_base_depth_ft": "",
    "notes": []
  },
  "footnotes": {
    "applicability": "",
    "contact_info": "",
    "revision_date": ""
  }
}

Rules:
- Preserve all numeric and text values exactly as printed.
- Extract the GAU Number and Date Issued even if embedded in headers.
- Capture recommendation paragraphs fully.
- If data missing, return null.
Return JSON only.
```

---

### 2️⃣ W-2 – Oil Well Potential Test / Completion Report

```text
Extract all factual data from this Form W-2 PDF.  
Return it in structured JSON exactly matching this schema:

{
  "header": {...},
  "operator_info": {...},
  "well_info": {...},
  "filing_info": {...},
  "completion_info": {...},
  "surface_casing_determination": {...},
  "initial_potential_test": {...},
  "casing_record": [...],
  "liner_record": {...},
  "tubing_record": [...],
  "producing_injection_disposal_interval": [...],
  "acid_fracture_operations": {...},
  "formation_record": [...],
  "commingling_and_h2s": {...},
  "remarks": {...},
  "rrc_remarks": {...},
  "operator_certification": {...}
}

Rules:
1. Extract every numeric, date, and text field verbatim.
2. Include each row of the casing, tubing, and formation tables as list entries.
3. Preserve units (ft, cu ft, sacks, etc.).
4. Do not compute or infer.
5. Return clean JSON only.
```

---

### 3️⃣ Schematic – Vertical Well Diagram

```text
Extract every piece of textual and numeric information from this schematic diagram.  
Return JSON structured as follows:

{
  "header": {
    "title": "",
    "well_name": "",
    "report_printed": "",
    "generated_by": "",
    "spud_date": ""
  },
  "location_info": {
    "district": "",
    "field_name": "",
    "api_number": "",
    "county": "",
    "state": "",
    "legal_location": {
      "section": "",
      "block": "",
      "survey": "",
      "abstract": "",
      "north_south_distance_ft": "",
      "north_south_reference": "",
      "east_west_distance_ft": "",
      "east_west_reference": ""
    }
  },
  "schematic_data": [
    {
      "type": "",
      "description": "",
      "depth_interval_ft": "",
      "date": "",
      "notes": ""
    }
  ]
}

Rules:
- Extract all equipment, perforations, cement intervals, packers, bridge plugs, and depths.
- Include manufacturer annotations or comments if present.
- Convert all depth ranges to string form (e.g., “8110-10914”).
- Do not infer formation names unless explicitly labeled.
```

---

### 4️⃣ W-15 – Cementing Report

```text
Extract all factual information from this Cementing Report (Form W-15).  
Return JSON in this structure:

{
  "header": {
    "form_type": "Form W-15",
    "title": "Cementing Report",
    "issuing_agency": "",
    "revision": ""
  },
  "operator_info": {
    "operator_name": "",
    "operator_p5_number": "",
    "cementer_name": "",
    "cementer_p5_number": ""
  },
  "well_info": {
    "district_no": "",
    "county": "",
    "api_number": "",
    "drilling_permit_no": "",
    "lease_name": "",
    "lease_no": "",
    "field_name": "",
    "field_no": ""
  },
  "cementing_data": [
    {
      "section": "",
      "casing_type": "",
      "hole_size_in": "",
      "casing_size_in": "",
      "setting_depth_ft": "",
      "slurries": [
        {
          "slurry_no": "",
          "no_of_sacks": "",
          "class": "",
          "additives": "",
          "volume_cuft": "",
          "height_ft": ""
        }
      ]
    }
  ],
  "cementing_to_squeeze": {
    "cementing_date": "",
    "plugs": [
      {
        "plug_number": "",
        "pipe_size_in": "",
        "depth_bottom_ft": "",
        "cement_used_sacks": "",
        "slurry_volume_cuft": "",
        "calculated_top_ft": "",
        "measured_top_ft": "",
        "slurry_weight_lbs_per_gal": "",
        "class_type": "",
        "perforate_and_squeeze": ""
      }
    ]
  },
  "certifications": {
    "cementer": {...},
    "operator": {...}
  },
  "instructions_section": ""
}

Rules:
- Capture every numeric and text value exactly.
- Preserve section headers (I, II, III, etc.).
- Include all signatures, names, addresses, and phone numbers.
- Include remarks if present.
```

---

### 5️⃣ Formation Tops Record

```text
Extract the formation list, depths, and metadata from this Formation Tops document.  
Return JSON structured as follows:

{
  "header": {
    "title": "Formation Record",
    "well_name": ""
  },
  "formation_record": [
    {"formation": "", "depth_tvd_ft": ""}
  ],
  "h2s_flag": "",
  "downhole_commingled": "",
  "remarks": ""
}

Rules:
- Include all visible formations and depths.
- Preserve units and decimal precision.
- Include any H2S or commingling flags.
- Capture all remarks exactly.
```

---

## ✅ Implementation Notes

- **Classification First:** Use `gpt-4o-mini` to detect document type before schema extraction.
- **Adaptive Prompts:** Dynamically insert the corresponding prompt from this guide based on classification result.
- **No Derived Data:** All outputs must be factual extractions.
- **Use `response_format={"type": "json_object"}`** to enforce valid JSON responses.

---

This configuration ensures consistent, schema-aligned structured extraction across all current Texas RRC document types handled by RegulAgent.

