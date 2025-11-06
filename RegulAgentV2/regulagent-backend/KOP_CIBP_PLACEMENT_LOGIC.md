# KOP-Based CIBP Placement Logic

## 🎯 Purpose

For **horizontal wells**, the CIBP (Cast Iron Bridge Plug) should be placed 50 feet above the **KOP (Kick-Off Point)** to properly isolate the horizontal section during plugging and abandonment operations.

## Implementation

### 1. **W-2 Extraction Enhancement**
**File**: `apps/public_core/services/openai_extraction.py` (line 256)

Added KOP extraction to W-2 prompt:
```python
"kop:{kop_md_ft,kop_tvd_ft} (Kick-Off Point - look in remarks section for 'KOP' followed by MD and TV/TVD depths); "
```

**Example from W-2 Remarks**:
```
KOP - 8776 MD/ 8758 TV
```

**Extracts to**:
```json
{
  "kop": {
    "kop_md_ft": 8776,
    "kop_tvd_ft": 8758
  }
}
```

### 2. **Facts Assembly**
**File**: `apps/public_core/views/w3a_from_api.py` (lines 894-943)

Extracts KOP from W-2 and adds to facts dictionary:
```python
# Extract KOP (Kick-Off Point) from W-2 for horizontal well CIBP placement
kop_md_ft = None
kop_tvd_ft = None
try:
    kop_data = w2.get("kop") or {}
    if isinstance(kop_data, dict):
        kop_md_ft = kop_data.get("kop_md_ft")
        kop_tvd_ft = kop_data.get("kop_tvd_ft")
        if kop_md_ft is not None:
            kop_md_ft = float(kop_md_ft)
            logger.info(f"📍 KOP extracted: MD={kop_md_ft} ft, TVD={kop_tvd_ft} ft")
except Exception as e:
    logger.warning(f"Failed to extract KOP data: {e}")

# Add to facts if present
if kop_md_ft is not None or kop_tvd_ft is not None:
    facts["kop"] = {
        "kop_md_ft": kop_md_ft,
        "kop_tvd_ft": kop_tvd_ft
    }
```

**Why MD (Measured Depth)?**
- All operational depths (casing, tubing, plugs) are in MD
- MD is what's used for well control and P&A operations
- TVD is extracted for reference but MD is used for calculations

### 3. **CIBP Placement Logic**
**File**: `apps/kernel/services/policy_kernel.py` (lines 245-288)

Updated CIBP detector to consider both perforations and KOP:

```python
# Determine CIBP placement: consider both perforations and KOP (kick-off point)
# Rule: Shallowest depth wins (min of perf-10 and kop-50)
plug_depth_from_perfs = max(float(deepest_prod_top_ft) - 10.0, 0.0)
placement_reason = "perforations (10 ft above top)"

# Check for KOP (Kick-Off Point) - horizontal well consideration
kop_data = facts.get("kop") or {}
kop_md_ft = kop_data.get("kop_md_ft")

if kop_md_ft is not None:
    try:
        kop_md = float(kop_md_ft)
        plug_depth_from_kop = max(kop_md - 50.0, 0.0)
        
        # Shallowest wins (Option A)
        if plug_depth_from_kop < plug_depth_from_perfs:
            plug_depth = plug_depth_from_kop
            placement_reason = f"KOP (50 ft above KOP at {kop_md} ft MD)"
            logger.critical(f"🔧 CIBP DETECTOR: KOP detected at {kop_md} ft MD → CIBP at {plug_depth} ft (KOP - 50)")
        else:
            plug_depth = plug_depth_from_perfs
            logger.critical(f"🔧 CIBP DETECTOR: KOP at {kop_md} ft, but perf-based depth {plug_depth_from_perfs} ft is shallower → using perf-based")
    except (ValueError, TypeError) as e:
        logger.warning(f"🔧 CIBP DETECTOR: Invalid KOP value {kop_md_ft}: {e}")
        plug_depth = plug_depth_from_perfs
else:
    plug_depth = plug_depth_from_perfs
    logger.critical(f"🔧 CIBP DETECTOR: No KOP found, using perf-based depth")
```

### 4. **Placement Priority: "Shallowest Wins"**

| Scenario | Perf-Based | KOP-Based | Winner | Reason |
|----------|-----------|-----------|--------|---------|
| **Vertical well, no KOP** | 9912 ft (perf - 10) | N/A | **9912 ft** | No KOP, use perfs |
| **Horizontal, KOP shallower** | 9912 ft | 8726 ft (KOP - 50) | **8726 ft** | KOP wins (shallower) |
| **Horizontal, KOP deeper** | 8500 ft | 9200 ft (KOP - 50) | **8500 ft** | Perfs win (shallower) |

**Rationale**: The shallowest placement ensures isolation of all critical zones (both perforations AND horizontal section).

### 5. **CIBP Details Enhancement**

The bridge plug step now includes placement reasoning:
```json
{
  "type": "bridge_plug",
  "depth_ft": 8726.0,
  "regulatory_basis": ["tx.tac.16.3.14(g)(3)"],
  "details": {
    "new_cibp_required": true,
    "placement_reason": "KOP (50 ft above KOP at 8776 ft MD)",
    "kop_considered": true,
    "casing_id_in": 4.778,
    "recommended_cibp_size_in": 4.53
  }
}
```

## Example Scenarios

### Example 1: Horizontal Well with KOP Above Perforations

**Well Data**:
```
KOP: 8776 MD / 8758 TVD
Deepest Perforation Top: 9922 ft
```

**Calculation**:
```
CIBP from perforations: 9922 - 10 = 9912 ft
CIBP from KOP: 8776 - 50 = 8726 ft
Winner: min(9912, 8726) = 8726 ft ← KOP-based
```

**Result**: CIBP placed at **8726 ft** (50 ft above KOP)

### Example 2: Vertical Well (No KOP)

**Well Data**:
```
KOP: Not present
Deepest Perforation Top: 9922 ft
```

**Calculation**:
```
CIBP from perforations: 9922 - 10 = 9912 ft
CIBP from KOP: N/A
Winner: 9912 ft ← Perforation-based
```

**Result**: CIBP placed at **9912 ft** (10 ft above deepest perforation)

### Example 3: Horizontal Well with KOP Below Perforations (rare)

**Well Data**:
```
KOP: 10000 MD
Deepest Perforation Top: 8500 ft
```

**Calculation**:
```
CIBP from perforations: 8500 - 10 = 8490 ft
CIBP from KOP: 10000 - 50 = 9950 ft
Winner: min(8490, 9950) = 8490 ft ← Perforation-based
```

**Result**: CIBP placed at **8490 ft** (perforations are shallower)

## Logging & Debugging

When KOP is used, logs will show:
```
🔧 CIBP DETECTOR: KOP detected at 8776.0 ft MD → CIBP at 8726.0 ft (KOP - 50)
🔧 CIBP DETECTOR: ✅ ALL CONDITIONS MET - Adding bridge_plug at 8726.0 ft (KOP (50 ft above KOP at 8776.0 ft MD))
```

When KOP is present but perfs are shallower:
```
🔧 CIBP DETECTOR: KOP at 10000.0 ft, but perf-based depth 8490.0 ft is shallower → using perf-based
```

When no KOP:
```
🔧 CIBP DETECTOR: No KOP found, using perf-based depth
```

## Testing

To test the KOP logic:

1. **Extract a W-2 with KOP in remarks** (like API 4230132998)
2. **Verify extraction**:
   - Check ExtractedDocument for `kop` field
   - Should see: `{"kop": {"kop_md_ft": 8776, "kop_tvd_ft": 8758}}`

3. **Verify CIBP placement**:
   - Check plan step for `bridge_plug`
   - `depth_ft` should be `kop_md_ft - 50` if it's shallower than `deepest_prod_top_ft - 10`
   - `details.placement_reason` should mention KOP

4. **Check logs** for KOP detection messages

## Files Modified

1. ✅ `apps/public_core/services/openai_extraction.py` - W-2 prompt for KOP extraction
2. ✅ `apps/public_core/views/w3a_from_api.py` - Facts assembly with KOP data
3. ✅ `apps/kernel/services/policy_kernel.py` - CIBP placement logic with KOP consideration

## Related Regulations

- **TAC §3.14(g)(3)**: Requirements for CIBP placement above perforations
- **Industry Best Practice**: KOP-based placement for horizontal wells to ensure proper isolation of horizontal section during P&A

---

**Implementation Date**: November 4, 2025  
**Priority**: High - Critical for horizontal well P&A compliance

