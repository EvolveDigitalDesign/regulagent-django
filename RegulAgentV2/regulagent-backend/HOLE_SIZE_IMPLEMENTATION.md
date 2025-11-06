# Hole Size Implementation for Accurate Open Hole Cement Calculations

## 🚨 Critical Issue Discovered

**Problem**: Material calculations for open hole cement operations (especially perforate & squeeze below casing shoe) were using **casing ID** instead of **hole diameter**, causing **50-180% errors** in cement volume calculations.

## Why This Matters

### Before (WRONG):
```python
# Using production casing ID (4.778") for open hole squeeze
ann_cap = π/4 * (4.778² - 2.375²) / 1029 ≈ 0.015 bbl/ft
# Result: Severely underestimated cement volumes
```

### After (CORRECT):
```python
# Using actual hole size (7.875") for open hole squeeze
ann_cap = π/4 * (7.875² - 2.375²) / 1029 ≈ 0.042 bbl/ft
# Result: Accurate cement volumes (180% more capacity!)
```

## Changes Implemented

### 1. **W-2 Extraction Enhancement** 
**File**: `apps/public_core/services/openai_extraction.py`

Added `hole_size_in` and `weight_per_ft` to casing_record extraction:
```python
"casing_record:[{
    string:'surface|intermediate|production|liner', 
    size_in,           # Casing OD
    weight_per_ft,     # NEW: Casing weight (for ID calculation)
    hole_size_in,      # NEW: Drilled hole diameter
    top_ft, 
    bottom_ft, 
    shoe_depth_ft, 
    cement_top_ft
}]"
```

### 2. **Schematic Extraction Enhancement**
**File**: `apps/public_core/services/schematic_extraction.py`

Added `hole_size_in` to Vision API prompt with guidance:
```python
{
  "casing_strings": [{
    "string_type": "surface|intermediate|production|liner",
    "size_in": float,
    "weight_ppf": float,
    "hole_size_in": float,  # NEW
    "grade": "string",
    ...
  }]
}
```

**Extraction Instructions**:
- Look for annotations like "12.25 in hole", "7.875 in hole", "Hole: 8.5 in"
- Common pairings: 9.625" casing in 12.25" hole, 5.5" casing in 7.875" hole
- If not explicitly stated, estimate as `casing_size + 2 inches`

### 3. **Materials Calculation Fix - Kernel**
**File**: `apps/kernel/services/policy_kernel.py` (lines 748-778)

Updated `_compute_materials_for_steps()` for perforate_and_squeeze_plug:

```python
# Calculate annular capacity based on context
if squeeze_context == "open_hole":
    # For open hole, use hole diameter (not casing ID)
    hole_size = prod_casing.get("hole_size_in") if prod_casing else None
    if hole_size:
        ann_cap = annulus_capacity_bbl_per_ft(float(hole_size), float(stinger_od))
        details["geometry_for_squeeze"] = {
            "hole_size_in": float(hole_size),
            "tubing_od_in": float(stinger_od),
            "context": "open_hole"
        }
    else:
        # Fallback: estimate hole size as casing_size + 2"
        casing_od = prod_casing.get("size_in") if prod_casing else casing_id
        estimated_hole = float(casing_od) + 2.0
        ann_cap = annulus_capacity_bbl_per_ft(estimated_hole, float(stinger_od))
        details["geometry_for_squeeze"] = {
            "estimated_hole_size_in": estimated_hole,
            "tubing_od_in": float(stinger_od),
            "context": "open_hole_estimated"
        }
else:
    # For cased hole, use casing ID (existing behavior)
    ann_cap = annulus_capacity_bbl_per_ft(float(casing_id), float(stinger_od))
```

### 4. **Materials Calculation Fix - Executor**
**File**: `apps/assistant/tools/executors.py` (lines 749-766)

Applied same fix to `execute_change_plug_type()` for consistency.

## Common Hole Sizes by Casing Size

| Casing OD | Typical Hole Size | Use Case |
|-----------|-------------------|----------|
| 4.5"      | 6.0" - 6.5"       | Small production |
| 5.5"      | 7.875" - 8.5"     | Standard production |
| 7"        | 8.75" - 9.875"    | Intermediate/Production |
| 9.625"    | 12.25"            | Surface casing |
| 13.375"   | 17.5"             | Conductor/Surface |

## Backward Compatibility

**For existing wells without hole_size_in data:**
- System automatically estimates as `casing_od + 2"`
- Falls back gracefully with warning log
- Marks calculation as `"context": "open_hole_estimated"` in step details

## Impact on Cement Calculations

### Example: 5.5" Production Casing, 2.375" Tubing

| Scenario | Diameter Used | Ann. Cap | 100 ft Squeeze | Sacks (Class H) |
|----------|---------------|----------|----------------|-----------------|
| **Before (Wrong)** | 4.778" (casing ID) | 0.015 bbl/ft | 1.5 bbl | 7 sacks |
| **After (Correct)** | 7.875" (hole size) | 0.042 bbl/ft | 4.2 bbl | 20 sacks |
| **Error** | - | **180%** | **180%** | **186%** |

## Testing Required

1. **Re-extract test well data** to populate hole_size_in fields
2. **Verify perforate & squeeze calculations** for open hole sections
3. **Check fallback logic** for wells without hole size data
4. **Compare old vs new material totals** for accuracy validation

## Next Steps

- [ ] Re-run extraction for all test wells (4241501493, 4200346118)
- [ ] Verify schematic extraction captures hole sizes correctly
- [ ] Document standard hole sizes in operator-specific guidelines
- [ ] Update materials documentation with hole size requirements

## Related Files
- `PERF_AND_SQUEEZE_MATERIALS_LOGIC.md` - Materials calculation documentation
- `apps/kernel/services/materials.py` - Core materials calculation functions
- `apps/public_core/models/plan_snapshot.py` - Plan data structure

---

**Implementation Date**: November 4, 2025  
**Critical Priority**: This fix directly impacts regulatory compliance and material ordering accuracy.

