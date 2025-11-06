# Plan Versioning & Learning Architecture

## ✅ YES - We Keep All Versions!

Every plan iteration is preserved as an immutable `PlanSnapshot` linked by `PlanModification` records.

---

## 🔗 Version Chain Structure

```
Baseline PlanSnapshot (kind='baseline')
    ↓ [PlanModification #1: combine_plugs]
Modified PlanSnapshot v1 (kind='post_edit')
    ↓ [PlanModification #2: adjust_depth]  
Modified PlanSnapshot v2 (kind='post_edit')
    ↓ [PlanModification #3: change_materials]
Modified PlanSnapshot v3 (kind='post_edit')
    ↓ [User approves]
Final PlanSnapshot (kind='approved')
```

**Every link is preserved** for:
1. ✅ **Revert**: Go back to any previous version
2. ✅ **Learning**: Track what users change and why
3. ✅ **Audit**: Full provenance chain for compliance

---

## 📊 What We Track Per Modification

```python
PlanModification {
  # Version linking
  source_snapshot: PlanSnapshot (before)
  result_snapshot: PlanSnapshot (after)
  
  # What changed
  op_type: "combine_plugs" | "replace_cibp" | "adjust_interval" | ...
  operation_payload: {
    "step_ids": [5, 11],
    "merge_threshold_ft": 50.0
  }
  diff: {
    "steps": {
      "removed": [5, 11],
      "added": [5],
      "modified": {...}
    },
    "materials_totals": {
      "total_sacks": {"before": 146, "after": 137}
    }
  }
  
  # Impact assessment
  risk_score: 0.15  // 0.0-1.0 (calculated)
  violations_delta: []  // new or resolved violations
  
  # Context (WHY the change was made)
  chat_thread: ChatThread
  chat_message: ChatMessage  // user's request
  description: "Combined formation top plugs for Dean and Lo due to proximity"
  
  # Audit trail
  applied_by: User
  created_at: timestamp
  is_applied: true
  is_reverted: false
}
```

---

## 🔄 Version Control APIs

### 1. Get Version History

**GET /api/plans/{plan_id}/versions**

```json
{
  "baseline_plan_id": "4200346118:combined",
  "current_version": 3,
  "total_versions": 4,
  "versions": [
    {
      "version": 0,
      "snapshot_id": 100,
      "kind": "baseline",
      "status": "draft",
      "created_at": "2025-11-02T10:00:00Z",
      "modification": null
    },
    {
      "version": 1,
      "snapshot_id": 101,
      "kind": "post_edit",
      "created_at": "2025-11-02T10:15:00Z",
      "modification": {
        "id": 1,
        "op_type": "combine_plugs",
        "description": "Combined formation plugs at 6500ft and 9500ft",
        "risk_score": 0.15,
        "violations_delta": []
      }
    },
    {
      "version": 2,
      "snapshot_id": 102,
      "kind": "post_edit",
      "created_at": "2025-11-02T10:30:00Z",
      "modification": {
        "op_type": "adjust_interval",
        "description": "Adjusted surface casing shoe coverage",
        "risk_score": 0.08
      }
    },
    {
      "version": 3,
      "snapshot_id": 103,
      "kind": "post_edit",
      "created_at": "2025-11-02T10:45:00Z",
      "modification": {
        "op_type": "change_materials",
        "description": "Changed cement class from C to H",
        "risk_score": 0.05
      }
    }
  ]
}
```

### 2. Revert to Previous Version

**POST /api/chat/threads/{thread_id}/revert**

```json
{
  "version": 1,  // or "snapshot_id": 101
  "reason": "Reverting to simpler approach after review"
}
```

Response:
```json
{
  "message": "Successfully reverted to previous version",
  "thread_id": 5,
  "previous_snapshot_id": 103,
  "current_snapshot_id": 101,
  "current_plan_id": "4200346118:combined",
  "reason": "Reverting to simpler approach after review"
}
```

**Important**: This does NOT delete versions 2 and 3! They remain in history.

### 3. Compare Versions

**GET /api/plans/compare/{snapshot_id_1}/{snapshot_id_2}**

```json
{
  "snapshot_1": {
    "id": 101,
    "plan_id": "4200346118:combined",
    "kind": "post_edit",
    "created_at": "2025-11-02T10:15:00Z"
  },
  "snapshot_2": {
    "id": 103,
    "plan_id": "4200346118:combined",
    "kind": "post_edit",
    "created_at": "2025-11-02T10:45:00Z"
  },
  "diff": {
    "steps": {
      "removed": 1,
      "added": 0,
      "modified": 2
    },
    "materials_totals": {
      "total_sacks": {"before": 137, "after": 137},
      "total_bbl": {"before": 28, "after": 28}
    }
  },
  "modification": {...}
}
```

---

## 🧠 Learning Strategy (Vectorization)

### Phase 1: Embed Every Modification

When a modification is applied:

```python
# 1. Generate embedding text
embedding_text = f"""
Modification Type: {modification.op_type}
Description: {modification.description}

Well Context:
- API: {well.api14}
- Operator: {well.operator_name}
- Field: {well.field_name}
- County: {well.county}
- District: {district}

Geological Context:
- Formation targets: {formations_targeted}
- Depth range: {depth_min}-{depth_max} ft

Operation Details:
- Steps affected: {len(modified_steps)}
- Materials delta: {materials_delta} sacks
- Risk score: {risk_score}

User Rationale: {chat_message.content}

Outcome:
- Violations before: {violations_before}
- Violations after: {violations_after}
- User accepted: True
- Regulator approved: {regulator_status}  // future
"""

# 2. Generate embedding
embedding = openai.embeddings.create(
    input=embedding_text,
    model="text-embedding-3-small"
)

# 3. Store with rich metadata
DocumentVector.objects.create(
    vector=embedding.data[0].embedding,
    metadata={
        "type": "plan_modification",
        "modification_id": modification.id,
        "tenant_id": str(tenant_id),
        
        "well_context": {
            "api": well.api14,
            "operator": well.operator_name,
            "field": well.field_name,
            "county": well.county,
            "district": district,
            "lat": well.lat,
            "lon": well.lon
        },
        
        "operation": {
            "type": modification.op_type,
            "steps_affected": len(modified_steps),
            "depth_range": [depth_min, depth_max],
            "formations": formations_targeted,
            "materials_delta": materials_delta,
            "risk_score": risk_score
        },
        
        "outcome": {
            "violations_before": violations_before,
            "violations_after": violations_after,
            "user_accepted": True,
            "applied_at": modification.applied_at.isoformat(),
            "regulator_accepted": None  // future: track RRC approval
        },
        
        "provenance": {
            "baseline_plan_id": baseline.plan_id,
            "kernel_version": kernel_version,
            "chat_thread_id": thread.id
        }
    }
)
```

### Phase 2: Query Similar Modifications

When user asks: "Can I combine these formation plugs?"

```python
# 1. Build query context
query_text = f"""
User wants to: combine formation plugs
Well: {api} in {county} County, District {district}
Operator: {operator}
Formations: {formations}
Current plan has {step_count} steps
"""

# 2. Generate query embedding
query_embedding = openai.embeddings.create(
    input=query_text,
    model="text-embedding-3-small"
)

# 3. Search for similar modifications
similar_mods = DocumentVector.objects.filter(
    metadata__type='plan_modification',
    metadata__operation__type='combine_plugs',
    metadata__well_context__district=district,
    metadata__well_context__county=county
).annotate(
    similarity=CosineDistance('vector', query_embedding)
).order_by('similarity')[:10]

# 4. Analyze results
total_found = len(similar_mods)
avg_risk = mean([m.metadata['operation']['risk_score'] for m in similar_mods])
avg_materials_savings = mean([m.metadata['operation']['materials_delta'] for m in similar_mods])
violations_introduced = sum([
    1 for m in similar_mods 
    if m.metadata['outcome']['violations_after'] > m.metadata['outcome']['violations_before']
])

# 5. Return evidence to user
return {
    "recommendation": "✅ Safe to proceed",
    "confidence": "high",
    "evidence": {
        "similar_modifications": total_found,
        "avg_risk_score": avg_risk,
        "avg_materials_savings": avg_materials_savings,
        "violations_introduced": violations_introduced,
        "success_rate": f"{(total_found - violations_introduced) / total_found * 100:.0f}%"
    },
    "examples": [
        {
            "operator": mod.metadata['well_context']['operator'],
            "field": mod.metadata['well_context']['field'],
            "outcome": "No violations, saved 9 sacks",
            "date": mod.metadata['outcome']['applied_at']
        }
        for mod in similar_mods[:3]
    ]
}
```

### Phase 3: Learn Tenant Preferences

Over time, detect patterns:

```python
# Analyze modifications for Demo Company tenant
tenant_mods = PlanModification.objects.filter(
    chat_thread__tenant_id=demo_tenant_id,
    is_applied=True,
    is_reverted=False
).values('op_type').annotate(count=Count('id'))

"""
Results:
- combine_plugs: 45 times (always for Dean/Lo formations)
- replace_cibp: 0 times (never used)
- adjust_interval: 12 times (mostly +50ft surface casing shoe)
"""

# Create tenant overlay rule (opt-in)
TenantOverlayRule.objects.create(
    tenant_id=demo_tenant_id,
    trigger="district=08A AND formations CONTAINS 'Dean' AND formations CONTAINS 'Lo'",
    adjustment={
        "action": "suggest_combine_plugs",
        "confidence": 0.95,
        "evidence": "Tenant applied this 45 times with 100% success rate"
    },
    enabled=False,  # Requires tenant approval
    provenance={
        "learned_from": "user_modifications",
        "sample_size": 45,
        "success_rate": 1.0,
        "avg_risk_score": 0.12
    }
)
```

---

## 🎯 Benefits for AI Learning

### 1. **Supervised Learning**
- Every modification is a labeled example: (context, action, outcome)
- User acceptance/rejection is explicit feedback
- RRC approval (future) is ground truth

### 2. **Few-Shot Learning**
- Show AI 3-5 similar successful modifications
- AI learns patterns without explicit rules

### 3. **Tenant-Specific Behavior**
- Detect preferences (e.g., "Demo Co. always combines Dean/Lo plugs")
- Suggest proactively: "Based on 45 similar wells, I recommend..."

### 4. **Risk Calibration**
- Learn what risk scores correlate with violations
- Improve risk scoring over time

### 5. **Anomaly Detection**
- Flag when user modification diverges from learned patterns
- "⚠️ This is unusual - only 2 similar modifications in history, both introduced violations"

---

## 📊 Metrics We Can Track

From modification history:

| Metric | Query |
|--------|-------|
| Most common modifications | `GROUP BY op_type` |
| Average risk by operator | `GROUP BY operator, AVG(risk_score)` |
| Success rate by district | `WHERE violations_after = 0 GROUP BY district` |
| Materials savings | `SUM(materials_delta)` |
| Time-to-approval | `approved_at - created_at` (future) |
| Revert rate | `COUNT(is_reverted=True) / COUNT(*)` |

---

## 🔐 Privacy & Tenant Isolation

### Public vs Private Modifications

```python
# Tenant-specific (PRIVATE)
- Draft and internal_review modifications
- Only used for that tenant's learning

# Public (after RRC approval)
- Approved plans become public
- Anonymized aggregates shared across tenants
- "5 operators in Andrews County combined these plugs successfully"
```

### Vectorization Strategy

```python
# Create TWO embeddings per modification:

# 1. Tenant-private embedding (full context)
tenant_vector = DocumentVector.objects.create(
    vector=embedding,
    metadata={...full context, tenant_id, user_id...},
    tenant_id=tenant_id  # Only this tenant can query
)

# 2. Public embedding (after approval, anonymized)
if plan.status == 'agency_approved':
    public_vector = DocumentVector.objects.create(
        vector=embedding,
        metadata={
            ...anonymized context (no tenant_id, no user_id),
            "approval_confirmed": True
        },
        tenant_id=None  # All tenants can query
    )
```

---

## 🚀 Implementation Roadmap

### ✅ Phase 1: Version Control (DONE)
- PlanSnapshot chain with PlanModification links
- Revert to previous version API
- Version history API
- Compare versions API

### 🚧 Phase 2: Learning Infrastructure (NEXT)
- Embed modifications after application
- Store metadata for filtering
- Query similar modifications
- Show evidence to users

### 🔮 Phase 3: Tenant Learning (FUTURE)
- Detect tenant patterns
- Propose TenantOverlayRules
- Opt-in learned defaults
- Risk score calibration

### 🔮 Phase 4: Regulator Outcomes (FUTURE)
- Ingest RRC approval/rejection data
- Update modification outcomes
- Improve suggestions based on approval rate
- KPI dashboards

---

## 📝 Example: Full Lifecycle

```
1. Baseline plan generated
   └─ PlanSnapshot(kind='baseline', payload={...})

2. User: "Can I combine the formation plugs?"
   └─ Query similar modifications (vectorized search)
   └─ AI: "✅ Yes, 15 similar wells did this successfully"

3. User accepts suggestion
   └─ PlanModification(op_type='combine_plugs', risk_score=0.15)
   └─ New PlanSnapshot(kind='post_edit', payload={...modified...})
   └─ Embed modification for future learning

4. User: "Actually, revert to original"
   └─ ChatThread.current_plan → baseline snapshot
   └─ Versions 1-3 still exist in history

5. User makes different change
   └─ New modification from baseline
   └─ Branching history preserved

6. User approves final plan
   └─ PlanSnapshot(kind='approved', status='engineer_approved')
   └─ File with RRC (future)

7. RRC approves plan (future)
   └─ Update modification outcomes
   └─ Create public embedding (anonymized)
   └─ Next user benefits from this precedent
```

---

## 🎓 Key Takeaways

1. ✅ **All versions preserved** - Can revert to any point
2. ✅ **Full context tracked** - Why, who, when, outcome
3. ✅ **Learning-ready** - Vectorize for similarity search
4. ✅ **Tenant-specific** - Learn preferences per tenant
5. ✅ **Privacy-aware** - Private until approved
6. ✅ **Audit-compliant** - Full provenance chain
7. ✅ **Improvement over time** - More data = better suggestions

---

**Status**: ✅ Version control complete, learning infrastructure ready to implement  
**Next**: Implement modification embedding service  
**Date**: 2025-11-02

