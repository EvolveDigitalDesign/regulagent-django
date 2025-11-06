# New Plan Modification Tools - CRUD Operations

## Overview

Three new tools have been implemented to provide complete CRUD (Create, Read, Update, Delete) operations on W-3A plan steps:

1. **`remove_steps`** - Delete specific steps from the plan
2. **`add_plug`** - Insert new plugs/steps into the plan
3. **`override_step_materials`** - Set custom sack counts for specific steps

These tools enable users to make complex modifications to plugging plans through natural language chat commands.

---

## 1. `remove_steps` Tool

### Purpose
Remove (delete) specified steps from the plugging plan with automatic step renumbering and materials recalculation.

### When to Use
- Remove CIBP and cap to replace with cement plug
- Delete unnecessary formation plugs
- Remove duplicate or redundant steps
- Clean up automatically generated steps

### Schema
```python
class RemoveStepsTool(BaseModel):
    step_ids: List[int]  # List of step IDs to remove
    reason: str  # Explanation for removal
```

### Example User Commands
- "Remove the CIBP and cap"
- "Delete steps 2 and 3"
- "Remove the bridge plug at 6738 ft"
- "Get rid of the Wolfcamp formation plug"

### AI Interpretation
```json
{
  "tool": "remove_steps",
  "args": {
    "step_ids": [2, 3],
    "reason": "User requested replacement with cement retainer"
  }
}
```

### Implementation Details

**Workflow:**
1. Validates step IDs exist
2. Checks guardrails (`max_steps_removed`)
3. Warns if removing critical regulatory steps (UQW, surface shoe, casing cut)
4. Removes steps from plan array
5. Renumbers remaining steps sequentially (1, 2, 3, ...)
6. Recalculates `materials_totals`
7. Creates new `PlanSnapshot`
8. Creates `PlanModification` record
9. Updates thread's current plan

**Critical Step Detection:**
- `uqw_isolation_plug` - Protects groundwater
- `surface_casing_shoe_plug` - Regulatory requirement
- `cut_casing_below_surface` - Final abandonment step

If any critical steps are removed, warning is included in response.

**Response:**
```json
{
  "success": true,
  "message": "Successfully removed 2 step(s). Plan now has 8 steps. ⚠️ Removing critical regulatory step(s): bridge_plug. Plan may be non-compliant.",
  "data": {
    "removed_count": 2,
    "remaining_count": 8,
    "removed_step_ids": [2, 3],
    "removed_types": ["bridge_plug", "bridge_plug_cap"],
    "total_sacks": 304,
    "warning": "⚠️ Removing critical regulatory step(s): bridge_plug. Plan may be non-compliant."
  },
  "risk_score": 0.4
}
```

**Risk Scoring:**
- `0.2` - Removing non-critical steps
- `0.4` - Removing critical regulatory steps

---

## 2. `add_plug` Tool

### Purpose
Add (insert) a new plug or step into the plugging plan at a specified depth with automatic positioning, materials calculation, and step renumbering.

### When to Use
- Add cement retainer with custom sack count
- Insert additional formation isolation plug
- Add bridge plug at specific depth
- Insert perforate & squeeze plug

### Schema
```python
class AddPlugTool(BaseModel):
    type: Literal["cement_plug", "perforate_and_squeeze_plug", "bridge_plug", "cement_retainer", "formation_top_plug"]
    top_ft: float  # Top depth in feet MD
    bottom_ft: float  # Bottom depth in feet MD (same as top for point devices)
    custom_sacks: Optional[int] = None  # Skip calculation if provided
    cement_class: Optional[Literal["A", "C", "G", "H"]] = None  # Defaults to H (deep) or C (shallow)
    placement_reason: str  # Explanation
```

### Example User Commands
- "Add a cement retainer at 6500 ft with 100 sacks below"
- "Insert a cement plug from 4000-3900 ft"
- "Add a bridge plug at 6738 ft"
- "Put a perforate and squeeze plug from 5500-5400 ft"

### AI Interpretation
```json
{
  "tool": "add_plug",
  "args": {
    "type": "cement_retainer",
    "top_ft": 6500.0,
    "bottom_ft": 6500.0,
    "custom_sacks": 100,
    "cement_class": "H",
    "placement_reason": "Cement retainer to hold 100 sacks below (user specified)"
  }
}
```

### Implementation Details

**Workflow:**
1. Validates plug type and depths (top_ft must be >= bottom_ft)
2. Determines default cement class if not provided:
   - Depth > 3000 ft → Class H (high pressure)
   - Depth ≤ 3000 ft → Class C (normal)
3. Creates step structure with proper fields
4. Calculates materials (unless `custom_sacks` provided):
   - Uses well geometry (casing ID, stinger OD)
   - Calculates annular capacity
   - Applies 40% excess
   - Rounds UP for safety
5. Inserts at correct position (sorted by `top_ft` descending)
6. Renumbers all steps sequentially
7. Recalculates `materials_totals`
8. Creates new `PlanSnapshot`
9. Creates `PlanModification` record
10. Updates thread's current plan

**Materials Calculation:**
```python
interval_ft = abs(top_ft - bottom_ft)
ann_cap = annulus_capacity_bbl_per_ft(casing_id, stinger_od)
excess = 0.4  # 40%
total_bbl = interval_ft * ann_cap * (1.0 + excess)
sacks = ceil(total_bbl × 5.615 / 1.18)  # Convert to sacks, round up
```

**Point Devices:**
For mechanical devices (bridge plugs, cement retainers), use same value for `top_ft` and `bottom_ft`:
```json
{
  "type": "cement_retainer",
  "top_ft": 6500.0,
  "bottom_ft": 6500.0,
  "custom_sacks": 100
}
```

**Response:**
```json
{
  "success": true,
  "message": "Successfully added cement_retainer at 6500.0-6500.0 ft. Plan now has 11 steps.",
  "data": {
    "new_step_id": 2,
    "type": "cement_retainer",
    "top_ft": 6500.0,
    "bottom_ft": 6500.0,
    "sacks": 100,
    "total_steps": 11,
    "total_sacks": 404
  },
  "risk_score": 0.3
}
```

**Flags in Step Details:**
```json
{
  "details": {
    "user_added": true,
    "placement_reason": "Cement retainer to hold 100 sacks below",
    "materials_override": true,  // If custom_sacks provided
    "custom_sacks": 100,
    "cement_class": "H",
    "geometry_used": {  // If calculated
      "casing_id_in": 4.778,
      "stinger_od_in": 2.375
    }
  }
}
```

---

## 3. `override_step_materials` Tool

### Purpose
Override calculated materials with a custom sack count for a specific step when the user provides exact quantities.

### When to Use
- User has field data showing different sack needs
- Accounting for poor hole conditions or washouts
- Matching vendor quote or AFE
- RRC reviewer requested specific quantity
- Operational experience suggests different volume

### Schema
```python
class OverrideMaterialsTool(BaseModel):
    step_id: int  # Step ID to modify
    sacks: int  # New sack count (must be positive)
    reason: str  # Explanation for override
```

### Example User Commands
- "Change step 4 to use 120 sacks instead"
- "Override step 7 materials to 150 sacks"
- "Set step 5 to 55 sacks to match the approved W-3A"
- "Use 200 sacks for the surface plug"

### AI Interpretation
```json
{
  "tool": "override_step_materials",
  "args": {
    "step_id": 4,
    "sacks": 120,
    "reason": "User specified custom sack count to match field conditions"
  }
}
```

### Implementation Details

**Workflow:**
1. Validates `sacks > 0`
2. Finds step by `step_id`
3. Stores original sack count for audit trail
4. Updates `step['sacks']` to new value
5. Flags step as `materials_override: true`
6. Stores override reason in details
7. Recalculates `materials_totals` for entire plan
8. Creates new `PlanSnapshot`
9. Creates `PlanModification` record
10. Updates thread's current plan

**Audit Trail:**
The override is clearly marked in the step's details:
```json
{
  "step_id": 4,
  "sacks": 120,  // New value
  "details": {
    "materials_override": true,
    "original_sacks": 77,  // Original calculated value
    "override_reason": "User specified custom sack count to match field conditions"
  }
}
```

This ensures RRC reviewers know the override is intentional, not a calculation error.

**Response:**
```json
{
  "success": true,
  "message": "Successfully overrode step 4 materials from 77 to 120 sacks (+43). Total plan: 347 sacks.",
  "data": {
    "step_id": 4,
    "original_sacks": 77,
    "new_sacks": 120,
    "delta_sacks": 43,
    "total_sacks": 347
  },
  "risk_score": 0.1
}
```

**Warning Threshold:**
If override is < 50% or > 200% of calculated value, a warning could be added (future enhancement).

---

## Complete Example Workflow

### User Request:
> "Remove the CIBP and cap, then add a cement retainer at 6500 ft with 100 sacks below the retainer"

### AI Processing:
```json
{
  "tool_calls": [
    {
      "tool": "remove_steps",
      "args": {
        "step_ids": [2, 3],
        "reason": "User requested replacement with cement retainer at 6500 ft"
      }
    },
    {
      "tool": "add_plug",
      "args": {
        "type": "cement_retainer",
        "top_ft": 6500.0,
        "bottom_ft": 6500.0,
        "custom_sacks": 100,
        "cement_class": "H",
        "placement_reason": "Cement retainer to hold 100 sacks below (user specified)"
      }
    }
  ]
}
```

### Result:
1. Steps 2 and 3 (CIBP + cap) are removed
2. Remaining steps are renumbered
3. New cement retainer is inserted at step 2 (6500 ft)
4. All subsequent steps are renumbered
5. Materials totals updated
6. Two `PlanModification` records created for audit trail

---

## Guardrails & Safety

### Global Guardrails
All three tools respect:
- `allow_plan_changes` flag (must be `true`)
- Session modification limits
- Tenant guardrail policies

### Risk Scoring

| Tool | Typical Risk Score | Increases If |
|------|-------------------|--------------|
| `remove_steps` | 0.2 | 0.4 if removing critical regulatory steps |
| `add_plug` | 0.3 | Higher if adding mechanical devices |
| `override_step_materials` | 0.1 | Future: if override is extreme (< 50% or > 200%) |

### Warnings & Validation

**`remove_steps`:**
- ⚠️ Warns if removing: `uqw_isolation_plug`, `surface_casing_shoe_plug`, `cut_casing_below_surface`
- Returns `warning` field in response

**`add_plug`:**
- ✅ Validates `top_ft >= bottom_ft`
- ✅ Automatic cement class selection
- ✅ Position validation (doesn't overlap existing plugs) - *future enhancement*

**`override_step_materials`:**
- ✅ Validates `sacks > 0`
- ✅ Stores original value for audit
- ⚠️ Could add warning for extreme overrides - *future enhancement*

---

## Database Impact

### Models Modified

1. **`PlanSnapshot`** (new record created for each operation)
   - `kind='post_edit'`
   - `status='draft'`
   - Updated `payload` with modified steps

2. **`PlanModification`** (audit trail)
   - `op_type`: `'remove_steps'`, `'add_step'`, or `'override_materials'`
   - `description`: Human-readable explanation
   - `operation_payload`: Tool arguments
   - `diff`: Detailed changes
   - `risk_score`: Calculated risk
   - `chat_thread`: Linked to conversation
   - `applied_by`: User who made the change

3. **`ChatThread`** (updated current plan pointer)
   - `current_plan` → points to new `PlanSnapshot`

---

## Frontend Integration

### API Requests
Frontend sends chat messages with `allow_plan_changes: true`:

```javascript
POST /api/chat/threads/{thread_id}/messages/
{
  "content": "Remove the CIBP and cap and add a cement retainer at 6500 ft with 100 sacks",
  "allow_plan_changes": true,
  "max_tool_calls": 10
}
```

### Response Handling
Backend processes tool calls asynchronously (Celery) and creates:
- User message record
- Assistant message record (with tool call results)
- New plan snapshot (if modifications made)
- Plan modification records

Frontend polls or uses WebSocket to get:
```javascript
GET /api/chat/threads/{thread_id}/messages/
{
  "messages": [
    {
      "role": "user",
      "content": "Remove the CIBP and cap..."
    },
    {
      "role": "assistant",
      "content": "I've removed steps 2 and 3 (CIBP and cap) and added a cement retainer at 6500 ft with 100 sacks...",
      "tool_calls": [...],
      "tool_results": [...]
    }
  ]
}
```

### Plan Refresh
After modifications, frontend should:
1. Refresh the plan view
2. Show modification audit trail
3. Display risk scores and warnings
4. Allow undo/revert to previous snapshot

---

## Testing

### Test Commands

1. **Remove Steps:**
```
"Remove steps 2 and 3"
"Delete the CIBP"
"Remove the bridge plug and its cap"
```

2. **Add Plug:**
```
"Add a cement retainer at 6500 ft with 100 sacks below"
"Insert a cement plug from 4000-3900 ft"
"Add a perforate and squeeze plug from 5500-5400 ft with class H cement"
```

3. **Override Materials:**
```
"Change step 4 to 120 sacks"
"Override step 7 materials to 55 sacks"
"Set step 5 to use 150 sacks to match the approved plan"
```

### Expected Behavior
- ✅ Steps are correctly added/removed/modified
- ✅ Renumbering is sequential (1, 2, 3, ...)
- ✅ Materials totals are recalculated
- ✅ New snapshot is created
- ✅ Modification record is logged
- ✅ Thread's current plan is updated
- ✅ Guardrails are enforced
- ✅ Warnings are shown for critical operations

---

## Future Enhancements

### 1. **Position Validation (add_plug)**
- Check for overlapping plugs
- Warn if new plug conflicts with existing steps
- Suggest merge if plugs are adjacent

### 2. **Extreme Override Detection (override_step_materials)**
```python
if new_sacks < original_sacks * 0.5 or new_sacks > original_sacks * 2.0:
    warning = "⚠️ Override is > 50% different from calculated value. Verify this is intentional."
```

### 3. **Bulk Operations**
- `remove_steps_by_type` - Remove all steps of a certain type
- `add_multiple_plugs` - Insert several plugs at once

### 4. **Undo/Redo**
- Use `PlanModification` chain to revert changes
- "Undo last change" command

### 5. **Smart Suggestions**
- AI suggests specific step IDs when user says "remove the CIBP"
- AI warns if removing step will cause compliance violations

---

## Summary

| Tool | Purpose | Primary Use Case |
|------|---------|------------------|
| `remove_steps` | Delete steps | Remove CIBP to replace with cement plug |
| `add_plug` | Insert steps | Add cement retainer with custom sack count |
| `override_step_materials` | Update materials | Match approved W-3A or field data |

These three tools complete the CRUD operations:
- **C**reate: `add_plug`
- **R**ead: `get_plan_snapshot` (existing)
- **U**pdate: `change_plug_type`, `override_step_materials`, `combine_plugs` (existing)
- **D**elete: `remove_steps`

Users can now make any modification to a plugging plan through natural language chat commands.

