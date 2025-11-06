# Plug Type Conversion Feature

## 🎯 Overview

This feature enables users to flexibly change plug types through the AI chat interface, addressing the key issues:

1. ✅ **Users can now change all plugs, or select specific plugs/formations**
2. ✅ **CIBP generation logic now coordinates with annular gap detection**
3. ✅ **Materials calculation now supports perforate & squeeze plugs**

---

## 🔧 What Was Implemented

### 1. Materials Calculation for Perforate & Squeeze Plugs

**File:** `apps/kernel/services/policy_kernel.py`

Added comprehensive materials calculation for `perforate_and_squeeze_plug` type:

- **Squeeze portion**: Calculates cement behind casing (50 ft perfs) with 1.5x squeeze factor
- **Cap portion**: Calculates cement cap inside casing (50 ft above perfs) with standard excess
- **Total sacks**: Combined squeeze + cap materials

```python
# Example output in materials.slurry:
{
    "total_bbl": 15.8,
    "squeeze_bbl": 9.2,   # Behind casing
    "cap_bbl": 6.6,       # Inside casing
    "sacks": 16,
    "water_bbl": 8.4,
    ...
}
```

### 2. Fixed CIBP Coordination with Annular Gap Detection

**File:** `apps/kernel/services/policy_kernel.py`

Updated `_covered_by_ops()` function to recognize when isolation is already provided by:

- **Perforate & squeeze plugs** (covers total interval from perf bottom to cap top)
- **Cement plugs, formation plugs** (standard coverage)
- **Existing bridge plugs or CIBPs** (within 100 ft tolerance)

**Result:** CIBP is only added when truly needed, not when other isolation methods already cover the depth.

### 3. New Chat Tool: `change_plug_type`

**Files:**
- `apps/assistant/tools/schemas.py` (Pydantic schema + tool definition)
- `apps/assistant/tools/executors.py` (execution logic)
- `apps/assistant/services/openai_service.py` (routing)

**Tool Capabilities:**

Users can now say things like:
- *"Perf and squeeze all plugs"* → `apply_to_all: true`
- *"Change the Wolfcamp and Canyon plugs to perf and squeeze"* → `formations: ["Wolfcamp", "Canyon"]`
- *"Convert steps 3, 5, and 7 to open hole"* → `step_ids: [3, 5, 7]`
- *"Change all plugs back to standard cement"* → `apply_to_all: true, new_type: "cement_plug"`

**Supported Conversions:**
- `cement_plug` ↔ `perforate_and_squeeze_plug`
- `formation_plug` ↔ `perforate_and_squeeze_plug`
- `cement_plug` → `open_hole_plug` (future: requires `hole_d_in` geometry)

**Safety:**
- ✅ Requires `allow_plan_changes: true` (guardrails enforced)
- ✅ Validates steps are eligible for conversion
- ✅ Creates new `PlanSnapshot` with version tracking
- ✅ Creates `PlanModification` audit record
- ✅ Returns risk score and violations delta

---

## 📋 How to Test

### Test 1: Schematic Extraction → Plan Generation → CIBP Present

1. **Clean database**:
```bash
docker exec regulagent_web python manage.py shell -c "
from apps.public_core.models import WellRegistry, ExtractedDocument, PlanSnapshot
from apps.assistant.models import ChatThread, ChatMessage, PlanModification
from apps.kernel.models import WellEngagement, TenantArtifact

WellRegistry.objects.all().delete()
ExtractedDocument.objects.all().delete()
PlanSnapshot.objects.all().delete()
ChatThread.objects.all().delete()
ChatMessage.objects.all().delete()
PlanModification.objects.all().delete()
WellEngagement.objects.all().delete()
TenantArtifact.objects.all().delete()
"
```

2. **Upload schematic and generate plan**:
   - Use API: `4241501493`
   - Mode: `hybrid`
   - Upload: `WBD_Lion_DIAMOND_M_UNIT.png`
   
3. **Verify**:
   - ✅ Annular gaps detected from schematic
   - ✅ Perforate & squeeze plugs generated for gaps
   - ✅ CIBP **not** added redundantly (already covered)
   - ✅ Materials calculated for all perf & squeeze steps

### Test 2: Change Plug Type via Chat

1. **Create chat thread** for the plan
2. **Test "apply_to_all" mode**:
```json
{
    "content": "Can you convert all cement plugs to perforate and squeeze?",
    "allow_plan_changes": true
}
```

**Expected AI behavior**:
- Calls `change_plug_type` with `apply_to_all: true`
- Converts all eligible cement-based plugs
- Returns success message with count

3. **Test "formations" mode**:
```json
{
    "content": "Change only the Wolfcamp and San Andres plugs to perforate and squeeze",
    "allow_plan_changes": true
}
```

**Expected**:
- AI calls `change_plug_type` with `formations: ["Wolfcamp", "San Andres"]`
- Only those formation plugs converted

4. **Test "step_ids" mode**:
```json
{
    "content": "Convert steps 5, 6, and 7 to perforate and squeeze",
    "allow_plan_changes": true
}
```

**Expected**:
- AI calls `change_plug_type` with `step_ids: [5, 6, 7]`
- Only those specific steps converted

5. **Test conversion back to cement**:
```json
{
    "content": "Actually, change everything back to standard cement plugs",
    "allow_plan_changes": true
}
```

**Expected**:
- AI calls `change_plug_type` with `new_type: "cement_plug"`, `apply_to_all: true`
- All perf & squeeze plugs converted back

### Test 3: Verify Materials Calculation

1. **Query plan after conversion**:
```bash
GET /api/plans/4241501493:combined/
```

2. **Check each perforate_and_squeeze_plug step**:
```json
{
    "type": "perforate_and_squeeze_plug",
    "sacks": 16,  // ✅ Should NOT be null
    "materials": {
        "slurry": {
            "total_bbl": 15.8,
            "squeeze_bbl": 9.2,  // ✅ Squeeze component
            "cap_bbl": 6.6,      // ✅ Cap component
            "sacks": 16,
            ...
        }
    },
    "details": {
        "perforation_interval": {
            "top_ft": 6915,
            "bottom_ft": 6865,
            "length_ft": 50
        },
        "cement_cap_inside_casing": {
            "top_ft": 6965,
            "bottom_ft": 6915,
            "height_ft": 50
        }
    }
}
```

3. **Check `materials_totals`**:
```json
{
    "materials_totals": {
        "total_sacks": 145,  // ✅ Should include all perf & squeeze sacks
        "total_bbl": 136.2,
        ...
    }
}
```

---

## 🎨 Frontend Integration

### Displaying Perforate & Squeeze Plugs

The frontend should display these as **compound operations** with two sub-components:

```
Step 5: Perforate & Squeeze (6,865 - 6,965 ft) — 16 sacks
  ├─ Perforate & Squeeze Behind Pipe (6,865 - 6,915 ft)
  └─ Cement Cap Inside Casing (6,915 - 6,965 ft)
```

**Fields to check:**
- `step.type === "perforate_and_squeeze_plug"`
- `step.total_top_ft` / `step.total_bottom_ft` (overall interval)
- `step.details.perforation_interval` (squeeze portion)
- `step.details.cement_cap_inside_casing` (cap portion)
- `step.sacks` (total materials)

### Chat UI - Plug Type Buttons

Consider adding quick action buttons for common operations:

```
[Perf & Squeeze All] [Cement All] [Perf & Squeeze Selected]
```

---

## 🔍 Debugging

### Check if tool is registered:
```bash
docker exec regulagent_web python manage.py shell -c "
from apps.assistant.tools.schemas import TOOL_DEFINITIONS
print([t['function']['name'] for t in TOOL_DEFINITIONS])
"
```

Should include: `change_plug_type`

### Check materials calculation:
```bash
docker exec regulagent_web python manage.py shell -c "
from apps.kernel.services.policy_kernel import _compute_materials_for_steps
from apps.public_core.models import PlanSnapshot

plan = PlanSnapshot.objects.filter(api14='4241501493').first()
steps = plan.payload['steps']
perf_squeeze_steps = [s for s in steps if s['type'] == 'perforate_and_squeeze_plug']
print(f'Found {len(perf_squeeze_steps)} perf & squeeze steps')
for s in perf_squeeze_steps:
    print(f'Step {s.get(\"step_id\")}: sacks={s.get(\"sacks\")}, materials={s.get(\"materials\", {})}')
"
```

---

## 🚀 Next Steps

1. **Test the full flow** (see Test 1-3 above)
2. **Update frontend** to display perforate & squeeze plugs properly
3. **Add open hole conversion** support (requires `hole_d_in` geometry)
4. **Consider adding UI buttons** for quick plug type changes
5. **Monitor PlanModification records** for audit trail

---

## 📝 API Examples

### Direct API Call (bypass chat):

This is useful for testing the executor directly:

```python
from apps.assistant.models import ChatThread
from apps.assistant.tools import executors
from apps.public_core.models import User

thread = ChatThread.objects.get(id=YOUR_THREAD_ID)
user = User.objects.first()

result = executors.execute_change_plug_type(
    new_type='perforate_and_squeeze_plug',
    apply_to_all=True,
    step_ids=None,
    formations=None,
    reason='Testing conversion',
    thread=thread,
    user=user,
    allow_plan_changes=True
)

print(result)
```

---

## ✅ Success Criteria

- [ ] Schematic extraction detects annular gaps
- [ ] Perforate & squeeze plugs generated for gaps
- [ ] CIBP only added when actually needed
- [ ] All perf & squeeze steps have calculated sacks (not null)
- [ ] Chat tool can convert all/select/formations
- [ ] PlanModification records created for audit
- [ ] Materials totals include all conversions
- [ ] Frontend displays compound plugs correctly

---

**Built:** 2025-11-03  
**Implemented by:** AI Assistant (Claude Sonnet 4.5)  
**Status:** ✅ Ready for Testing

