# District Code Normalization Fix

## 🚨 Issue Identified

**Problem**: District code variations ("08", "8", "08A", "8A") were not consistently matched to policy overlay files, causing formation plugs to not be generated.

**Example**:
- W-2 extracted: `district: "08"`
- Policy file exists: `08a__auto.yml`
- **No match** → No district-specific formation plugs generated

## Root Cause

The policy loader (`apps/policy/services/loader.py`) was using raw district codes for file lookups:
```python
# OLD CODE (lines 181-182)
d_lower = str(district).lower()  # "08" → "08", "8A" → "8a"
combined_name = f"{d_lower}__auto.yml"  # Looks for "08__auto.yml"
```

**Problem**: Texas RRC uses multiple district code formats:
- Some documents use: `"08"`, `"8"`
- Policy files use: `"08a"`, `"8a"` (with letter suffix)
- **No normalization** → mismatches

## Fix Implemented

### 1. Added District Normalization Function
**File**: `apps/policy/services/loader.py` (lines 159-196)

```python
def _normalize_district(district: str) -> str:
    """
    Normalize district code to standard format for policy lookups.
    
    Handles variations: "08", "8", "08A", "8A" all normalize to "08a"
    
    Examples:
        "08" → "08a"
        "8" → "08a"  
        "08A" → "08a"
        "8A" → "08a"
        "7C" → "07c"
        "7" → "07a"
    
    Returns lowercase, zero-padded, with letter suffix.
    """
    if not district:
        return ""
    
    d = str(district).strip().upper()
    
    # Extract numeric and letter parts
    match = re.match(r'^(\d+)([A-Z]?)$', d)
    if not match:
        return district.lower()  # Return as-is if non-standard
    
    num_part, letter_part = match.groups()
    
    # Zero-pad single digit
    if len(num_part) == 1:
        num_part = f"0{num_part}"
    
    # Default to 'A' if no letter (most TX RRC districts use A)
    if not letter_part:
        letter_part = 'A'
    
    return f"{num_part}{letter_part}".lower()
```

### 2. Updated District File Lookups
**Lines 213-230**: District overlay lookups
```python
# NEW CODE
d_normalized = _normalize_district(district)
combined_name = f"{d_normalized}__auto.yml"
combined_path = os.path.join(ext_dir, combined_name)
```

**Lines 240-246**: County file lookups
```python
# NEW CODE  
d_normalized_for_county = _normalize_district(district)
file_name = f"{d_normalized_for_county}__{safe_county}.yml"
ext_path = os.path.join(ext_dir, file_name)
```

**Removed**: Old zero-padding fallback logic (no longer needed)

## Normalization Examples

| Input District | Normalized | Policy File Matched |
|----------------|------------|---------------------|
| `"08"` | `"08a"` | `08a__auto.yml` ✅ |
| `"8"` | `"08a"` | `08a__auto.yml` ✅ |
| `"08A"` | `"08a"` | `08a__auto.yml` ✅ |
| `"8A"` | `"08a"` | `08a__auto.yml` ✅ |
| `"7C"` | `"07c"` | `07c__auto.yml` ✅ |
| `"7"` | `"07a"` | `07a__auto.yml` ✅ |
| `"10B"` | `"10b"` | `10b__auto.yml` ✅ |

## Impact

### Before Fix
```json
{
  "district": "08",  // From W-2
  "formations_targeted": [],  // ← EMPTY! No match
  "steps": [
    // No formation plugs generated
  ]
}
```

### After Fix
```json
{
  "district": "08",  // From W-2
  "formations_targeted": ["Bone Springs", "Bell Canyon", ...],  // ← POPULATED!
  "steps": [
    {
      "type": "formation_top_plug",
      "formation": "Bone Springs",
      "top_ft": 9320
    },
    // ... more formation plugs
  ]
}
```

## Testing

To verify the fix works:

1. **Re-generate plan for well 4230132998** (LOVING County, District 08)
2. **Verify formation plugs appear** for Delaware Basin formations:
   - Bone Springs @ 9320 ft
   - Brushy Canyon @ 7826 ft
   - Cherry Canyon @ 6601 ft
   - Bell Canyon @ 5424 ft
   - Red Bluff @ 3900 ft

3. **Check policy resolution logs**:
```
District "08" → Normalized to "08a"
Loading: packs/tx/w3a/district_overlays/08a__auto.yml ✅
Loading: packs/tx/w3a/district_overlays/08a__loving.yml ✅
```

## Related Files
- `apps/policy/services/loader.py` - Policy loader with normalization
- `packs/tx/w3a/district_overlays/08a__auto.yml` - District 8A policy
- `apps/kernel/services/policy_kernel.py` - Uses normalized policy

## Notes

- **Backward Compatible**: Function tries original district code first, then normalized
- **Zero-Padding**: Single-digit districts auto-padded (`"8"` → `"08a"`)
- **Default Letter**: Districts without letter get `"A"` suffix (`"08"` → `"08a"`)
- **Non-Standard Codes**: Returned lowercase as-is if format doesn't match `^\d+[A-Z]?$`

---

**Implementation Date**: November 4, 2025  
**Priority**: High - Affects formation plug generation for all wells

