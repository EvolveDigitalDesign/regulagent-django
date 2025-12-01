# PNA Event Type Mappings for W-3 Form

This document describes all event types that pnaexchange can send to RegulAgent for W-3 form generation, along with their template patterns and input placeholders.

## Event Mapping Reference

### 1. Set Intermediate Plug (ID: 4)
- **Display Text**: "Set Intermediate Plug"
- **Work Description ID**: 77
- **Template**: `Plug *_1_* Spot *_2_* class *_3_* cement From *_4_* to *_5_*. Displaced with *_6_*.`
- **Required Inputs**: 6
- **Placeholder Mapping**:
  - `*_1_*` = Plug number
  - `*_2_*` = Spot/depth (top of plug)
  - `*_3_*` = Cement class (A, B, C, etc.)
  - `*_4_*` = Cement from (top depth)
  - `*_5_*` = Cement to (bottom depth)
  - `*_6_*` = Displacement material
- **Transformation**: 
  - Event type: `set_cement_plug`
  - depth_top_ft = *_2_*
  - cement_class = *_3_*
  - sacks/volume calculated from template context

---

### 2. Set Surface Plug (ID: 3)
- **Display Text**: "Set Surface Plug"
- **Work Description ID**: 103
- **Template**: `Plug *_1_* Rigged up pump and circulated *_2_* class *_3_* cement from *_4_* to surface.`
- **Required Inputs**: 4
- **Placeholder Mapping**:
  - `*_1_*` = Plug number
  - `*_2_*` = Sacks/volume of cement
  - `*_3_*` = Cement class (A, B, C, etc.)
  - `*_4_*` = Cement from depth
  - (to surface is implicit = 0 ft)
- **Transformation**:
  - Event type: `set_surface_plug`
  - depth_top_ft = *_4_*
  - depth_bottom_ft = 0 (surface)
  - cement_class = *_3_*
  - sacks = *_2_* (if numeric)

---

### 3. Set Surface Plug - Squeeze (ID: 7)
- **Display Text**: "Set Surface Plug"
- **Work Description ID**: 97
- **Template**: `Plug *_1_* Squeezed *_2_* class *_3_* cement from *_4_* to *_5_*`
- **Required Inputs**: 5
- **Placeholder Mapping**:
  - `*_1_*` = Plug number
  - `*_2_*` = Sacks/volume of cement
  - `*_3_*` = Cement class (A, B, C, etc.)
  - `*_4_*` = Cement from depth
  - `*_5_*` = Cement to depth
- **Transformation**:
  - Event type: `squeeze`
  - depth_top_ft = *_4_*
  - depth_bottom_ft = *_5_*
  - cement_class = *_3_*
  - sacks = *_2_* (if numeric)

---

### 4. Broke Circulation (ID: 2)
- **Display Text**: "Broke Circulation"
- **Work Description ID**: 100
- **Template**: `Broke circulation through surface casings.`
- **Required Inputs**: 0 (no input fields)
- **Transformation**:
  - Event type: `broke_circulation`
  - No depth/cement data required
  - Remarks: "Broke circulation"

---

### 5. Pressure Up (ID: 9)
- **Display Text**: "Pressure Up"
- **Work Description ID**: 93
- **Template**: `Pressure Up`
- **Required Inputs**: 0 (no input fields)
- **Transformation**:
  - Event type: `pressure_up`
  - No depth/cement data required
  - Remarks: "Pressure up on plug"

---

### 6. Set CIBP (ID: 6)
- **Display Text**: "Set CIBP"
- **Work Description ID**: 86
- **Template**: `Set *_1_* CIBP at *_2_*`
- **Required Inputs**: 2
- **Placeholder Mapping**:
  - `*_1_*` = CIBP size/type (e.g., "5.5 in")
  - `*_2_*` = Depth (measured depth)
- **Transformation**:
  - Event type: `set_bridge_plug`
  - depth_bottom_ft = *_2_*
  - remarks = "CIBP set at depth"

---

### 7. Cut Casing (ID: 12)
- **Display Text**: "Cut Casing"
- **Work Description ID**: 74
- **Template**: `Cut casing at *_1_* and pull out of hole`
- **Required Inputs**: 1
- **Placeholder Mapping**:
  - `*_1_*` = Cut depth (where casing was cut)
- **Transformation Rules**:
  - Event type: `cut_casing`
  - `jump_plugs_to_next_casing` = **true** (critical!)
  - depth_bottom_ft = *_1_*
  - **Effect**: Applies cut to casing state engine, moving active zone to next inner casing

---

### 8. Tag TOC (ID: 8)
- **Display Text**: "Tag TOC"
- **Work Description ID**: 72
- **Template**: `Tagged top of cement at *_2_*`
- **Required Inputs**: 1
- **Placeholder Mapping**:
  - `*_2_*` = Tagged depth
- **Transformation**:
  - Event type: `tag_toc`
  - tagged_depth_ft = *_2_*
  - remarks = "Tagged top of cement"

---

### 9. Tagged TOC (ID: 5)
- **Display Text**: "Tagged TOC"
- **Work Description ID**: 52
- **Template**: `Tagged top of cement at *_1_*`
- **Required Inputs**: 1
- **Placeholder Mapping**:
  - `*_1_*` = Tagged depth
- **Transformation**:
  - Event type: `tag_toc`
  - tagged_depth_ft = *_1_*
  - remarks = "Tagged top of cement"

---

### 10. Perforation (ID: 1)
- **Display Text**: "Perforation"
- **Work Description ID**: 24
- **Template**: `Perforated at *_1_* ft.`
- **Required Inputs**: 1
- **Placeholder Mapping**:
  - `*_1_*` = Perforation depth
- **Transformation**:
  - Event type: `perforate`
  - perf_depth_ft = *_1_*
  - remarks = "Perforation event"

---

### 11. Tag CIBP (ID: 11)
- **Display Text**: "Tag CIBP"
- **Work Description ID**: 21
- **Template**: `Tagged CIBP at *_2_*`
- **Required Inputs**: 1
- **Placeholder Mapping**:
  - `*_2_*` = Tagged depth of CIBP
- **Transformation**:
  - Event type: `tag_bridge_plug`
  - tagged_depth_ft = *_2_*
  - remarks = "Tagged bridge plug"

---

### 12. RRC Approval (ID: 10)
- **Display Text**: "RRC Approval"
- **Work Description ID**: 18
- **Template**: `*_1_* approved to : *_2_*`
- **Required Inputs**: 2
- **Placeholder Mapping**:
  - `*_1_*` = Approval type/status
  - `*_2_*` = Approval depth/reference
- **Transformation**:
  - Event type: `rrc_approval`
  - remarks = template text filled in
  - No depth mapping required

---

## Input Value Extraction Strategy

When pnaexchange sends event data, it will include:
- `event_id`: ID from above (1-12)
- `display_text`: Human-readable event name
- `form_template_text`: Template with *_N_* placeholders
- `input_values`: Dictionary keyed by placeholder position (e.g., {"1": "5.5", "2": "6997", ...})
- `transformation_rules`: Rules like `jump_plugs_to_next_casing: true`
- `date`: Event date (ISO format)
- `start_time`, `end_time`: Optional times
- `work_assignment_id`: Reference to DWR work assignment
- `dwr_id`: Reference to DWR record

## Key Transformation Logic

### Cement Plug Events
For "Set Plug" events, extract:
- Plug number from *_1_*
- Depths from *_4_* and *_5_* (or implicit surface)
- Cement class from *_3_*
- Sacks/volume from *_2_* or context

### Casing Cuts
When `jump_plugs_to_next_casing` is true:
1. Call casing engine's `apply_cut_casing(casing_state, depth)`
2. Update active casing for subsequent plugs
3. Track that plugs below cut depth use inner casing

### Tag TOC Events
Used to validate cement job success:
- Record tagged depth
- Compare to pump pressure logs if available
- Mark plug as "tag_required: true" if subsequent plug depends on this TOC

---

## Notes

- More event types may be added in future (payload extensibility)
- Input placeholders are 1-indexed (*_1_*, *_2_*, etc.)
- Depths are always in feet (MD)
- Cement class is typically single letter (A, B, C, G, etc.)
- All timestamps should be preserved for audit trail


