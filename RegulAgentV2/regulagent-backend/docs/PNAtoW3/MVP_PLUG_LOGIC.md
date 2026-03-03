# MVP Plug Logic - W-3 Form Generation

## Overview

The MVP plug generation logic enriches W-3A plugging proposal data with additional fields derived from:
1. **W-3A extraction** (via OpenAI PDF extraction)
2. **pnaexchange events** (operational data from DWR)
3. **Calculated values** (cement yield, slurry properties)

---

## Data Flow for Each Plug

```
W-3A plugging_proposal row
    ↓
    ├─ plug_number: 1
    ├─ depth_top_ft: 7990 (from W-3A)
    ├─ depth_bottom_ft: 7890 (from W-3A)
    ├─ type: "cement_plug" (from W-3A)
    ├─ sacks: 40 (from W-3A)
    └─ cement_class: "C" (from pnaexchange event, NOT W-3A)
    
    ↓
    Enrichment Layer
    
    ├─ hole_size_in: 7.875" (from casing_record via get_active_casing_at_depth)
    ├─ slurry_weight_ppg: 14.8 (default if not specified in pnaexchange)
    ├─ calculated_top_of_plug_ft: 7948 (from calculate_top_of_plug)
    └─ measured_top_of_plug_ft: 7947 (from pnaexchange "Tag TOC" event)
    
    ↓
    Output
    
    ├─ top_of_plug_ft: 7947 (measured, preferred)
    └─ toc_variance_ft: -1 (measured - calculated)
```

---

## Field Determination

### **1. Hole Size** 
```
1. Use plug's depth_bottom_ft
2. Call: active_casing = get_active_casing_at_depth(casing_state, depth_bottom_ft)
3. Extract: hole_size_in = active_casing.hole_size_in

Logic: Almost always the innermost casing at that depth, UNLESS casing was cut and pulled.
```

### **2. Cement Class**
```
Source: pnaexchange event JSON (not W-3A)

Why? The W-3A is a proposal (before operations). pnaexchange has the actual cement 
used during operations. The operational data is always more authoritative.

Extracted by: w3_mapper.py extract_cement_class_and_sacks()
```

### **3. Slurry Weight**
```
Source: pnaexchange event (if specified), else default

Default: 14.8 lbs/gal

Formula: slurry_weight_ppg = event.slurry_weight_ppg or 14.8
```

### **4. Calculated Top of Plug (TOC)**
```
Formula: TOC = plug_bottom + (sacks × yield_per_sack) / hole_volume_per_ft

Cement yields (bbl per sack):
  - Class A, B, G: 1.15
  - Class C: 1.35
  - Class H: 1.19

MVP Simplified: Use 1.3 bbl/sack average

Hole volumes (bbl per foot):
  - 7.875" hole: 0.58
  - 7" hole: 0.52
  - 5.5" hole: 0.30
  
MVP Simplified: Use 0.4 bbl/ft average

Example:
  plug_bottom_ft = 7890
  sacks = 40
  plug_height = (40 × 1.3) / 0.4 = 130 ft
  calculated_toc = 7890 + 130 = 8020 ft

Function: calculate_top_of_plug() in w3_formatter.py
```

### **5. Measured Top of Plug (TOC)**
```
Source: pnaexchange "Tag TOC" event

When: Drilling crew physically tags top of plug after setting and WOC (Wait On Cement)

Extraction: tag_toc event.tagged_depth_ft → measured_top_of_plug_ft

Why two values? 
- Measured TOC is more accurate (field reality)
- Calculated TOC is deterministic (for validation)
- Variance highlights potential issues
```

### **6. TOC Variance**
```
Formula: toc_variance_ft = measured_top_of_plug_ft - calculated_top_of_plug_ft

Interpretation:
  - 0: Perfect match (unlikely but good)
  - Positive: Measured is shallower than calculated (plug height less than expected)
  - Negative: Measured is deeper than calculated (plug height more than expected)

Use cases:
- Validation: Flag large variances (>10 ft) for review
- Quality control: Track if slurry yield assumptions are accurate
- Audit trail: Stored in output for RRC submission

Example:
  calculated_toc = 8020 ft
  measured_toc = 8015 ft
  variance = -5 ft (measured 5 ft deeper - more cement than expected)
```

---

## Output Fields for RRC Submission

Each plug in the `plugs` array includes:

```json
{
  "plug_number": 1,
  "depth_top_ft": 7990,
  "depth_bottom_ft": 7890,
  "type": "cement_plug",
  "cement_class": "C",
  "sacks": 40,
  "slurry_weight_ppg": 14.8,
  "hole_size_in": 7.875,
  
  "top_of_plug_ft": 8015,            // For RRC: use measured if available
  "measured_top_of_plug_ft": 8015,   // Field measurement
  "calculated_top_of_plug_ft": 8020, // Formula-based
  "toc_variance_ft": -5,             // Audit trail
  
  "remarks": "Set Intermediate Plug from 7990 to 7890 ft..."
}
```

---

## Data Sources Summary

| Field | W-3A | pnaexchange | Calculated | Default |
|-------|------|-------------|-----------|---------|
| plug_number | ✅ | | | |
| depth_top_ft | ✅ | | | |
| depth_bottom_ft | ✅ | | | |
| type | ✅ | | | |
| sacks | ✅ | | | |
| cement_class | | ✅ | | |
| slurry_weight_ppg | | ✅ | | 14.8 |
| hole_size_in | ✅* | | ✅ | |
| calculated_top_of_plug_ft | | | ✅ | |
| measured_top_of_plug_ft | | ✅ | | |
| toc_variance_ft | | | ✅ | |

*From casing_record matched via depth

---

## Implementation

### Key Functions

1. **`get_active_casing_at_depth()`** - w3_casing_engine.py
   - Determines which casing is active at a given depth
   - Handles cut casings (removed_to_depth_ft)

2. **`calculate_top_of_plug()`** - w3_formatter.py
   - Computes TOC from sacks using simplified yield model
   - Returns depth in feet

3. **`format_plugs_for_rrc()`** - w3_formatter.py
   - Main orchestrator for plug enrichment
   - Calls both functions above
   - Returns formatted plug dictionaries ready for API response

### Code Flow

```
group_events_into_plugs()
    ├─ Creates Plug objects from W3Events
    ├─ Extracts cement_class from events
    └─ Stores measured_top_of_plug_ft from tag_toc events

↓

format_plugs_for_rrc(plugs, casing_state)
    ├─ get_active_casing_at_depth() → hole_size_in
    ├─ calculate_top_of_plug() → calculated_top_of_plug_ft
    ├─ Calculate toc_variance_ft
    └─ Return formatted plug dictionaries
```

---

## Future Enhancements (Post-MVP)

1. **Hole volume accuracy** - Use actual hole sizes from casing record, not average
2. **Cement yield tables** - Look up actual yields by class and temperature
3. **Slurry density calculations** - Compute from cement type and additives
4. **Pressure testing** - Incorporate squeeze pressure for validation
5. **Casing record updates** - Track casing removal and new perforations
6. **Batch calculations** - Optimize for large number of plugs
7. **Variance thresholds** - Configurable alerts for TOC mismatches









