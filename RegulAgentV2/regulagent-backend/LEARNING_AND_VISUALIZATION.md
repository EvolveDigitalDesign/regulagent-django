# Learning, Feedback Loops, and Visualization

This document describes three key enhancements to the AI-assisted planning system:

1. **Contextual Embedding Filters** - Rich metadata for targeted similarity queries
2. **Learning Feedback Loop** - RRC outcome tracking to bias toward approved patterns
3. **Version Diff Visualization** - JSON patch diffs for UI highlighting

---

## 1. Contextual Embedding Filters

### Problem
Vector embeddings without rich metadata can't answer targeted queries like:
- "Show me modifications that **combined plugs in District 08A** that were **approved by RRC**"
- "Find modifications using **tx.tac.16.3.14(g)(1)** that **reduced materials**"
- "Show me modifications affecting **surface casing** that **introduced no violations**"

### Solution: Multi-Dimensional Metadata

**File**: `apps/assistant/services/modification_embedder.py`

When embedding a plan modification, we attach rich metadata with multiple dimensions:

```python
metadata = {
    # Core
    "type": "plan_modification",
    "modification_id": 123,
    "tenant_id": "uuid",
    
    # Well context
    "well_context": {
        "api": "42003461118",
        "operator": "XTO Energy",
        "field": "TXL Spraberry",
        "county": "Andrews",
        "lat": 32.242052,
        "lon": -102.282218
    },
    
    # District/jurisdiction
    "district": "08A",
    "jurisdiction": "TX",
    
    # Operation
    "operation": {
        "type": "combine_plugs",
        "description": "Combined formation top plugs at 6500ft and 9500ft",
        "risk_score": 0.15
    },
    
    # Regulatory context (NEW)
    "regulatory": {
        "sections_cited": [
            "tx.tac.16.3.14(g)(1)",
            "tx.tac.16.3.14(e)(2)"
        ],
        "primary_section": "tx.tac.16.3.14(g)(1)",
        "section_count": 2
    },
    
    # Step context (NEW)
    "steps": {
        "types": {
            "cement_plug": 3,
            "bridge_plug": 1,
            "surface_casing_shoe_plug": 1
        },
        "types_affected": ["cement_plug"],
        "count_before": 7,
        "count_after": 5,
        "count_delta": -2
    },
    
    # Depth context (NEW)
    "depths": {
        "min_depth_ft": 0,
        "max_depth_ft": 11200,
        "avg_depth_ft": 5600,
        "depth_bins": {
            "shallow": 2,      # 0-2000 ft
            "intermediate": 3,  # 2000-7000 ft
            "deep": 4           # >7000 ft
        }
    },
    
    # Geological context (NEW)
    "formations": {
        "targeted": ["Dean", "Strawn", "Lo"],
        "detected": ["Dean", "Strawn", "Lo", "Wolfcamp A"],
        "count": 3
    },
    
    # Materials impact
    "materials": {
        "sacks_before": 400,
        "sacks_after": 250,
        "sacks_delta": -150,
        "delta_percent": -37.5
    },
    
    # Violations
    "violations": {
        "count_before": 1,
        "count_after": 0,
        "delta": -1,
        "introduced_new": false
    },
    
    # Outcome (learning)
    "outcome": {
        "user_accepted": true,
        "user_reverted": false,
        "regulator_accepted": true,      # From RRC
        "regulator_status": "approved",
        "confidence": 0.85               # Weighted by outcomes
    }
}
```

### Targeted Queries

```python
# Query 1: Find approved modifications for combining plugs in District 08A
query_similar_modifications(
    query_context={
        "operation_type": "combine_plugs",
        "district": "08A",
    },
    filters={
        "outcome.regulator_accepted": True,
        "materials.delta_percent": {"$lt": -20}  # >20% savings
    }
)

# Query 2: Find modifications citing specific regulatory section
query_similar_modifications(
    query_context={
        "regulatory_section": "tx.tac.16.3.14(g)(1)"
    },
    filters={
        "outcome.confidence": {"$gte": 0.7},
        "violations.introduced_new": False
    }
)

# Query 3: Find modifications affecting surface casing
query_similar_modifications(
    query_context={
        "step_types": ["surface_casing_shoe_plug"],
        "depth_range": [0, 2000]
    },
    filters={
        "outcome.regulator_accepted": True
    }
)
```

---

## 2. Learning Feedback Loop (RRC Outcomes)

### Problem
Without feedback from RRC approvals/rejections, the AI can't learn which modifications work in practice. We need to bias suggestions toward **regulator-approved patterns**.

### Solution: RegulatorOutcome Model + Confidence Propagation

**File**: `apps/assistant/models/regulator_outcome.py`

#### Track RRC Outcomes

```python
class RegulatorOutcome(models.Model):
    """
    Tracks RRC approval/rejection for filed plans.
    """
    plan_snapshot = models.OneToOneField(PlanSnapshot)
    
    # Status
    status = models.CharField(choices=[
        'pending',
        'under_review',
        'approved',         # ✅
        'rejected',         # ❌
        'revision_requested',
        'withdrawn'
    ])
    
    # Timeline
    filed_at = models.DateTimeField()
    reviewed_at = models.DateTimeField()
    approved_at = models.DateTimeField()
    review_duration_days = models.IntegerField()
    
    # Feedback
    reviewer_notes = models.TextField()
    reviewer_name = models.CharField(max_length=128)
    revision_count = models.IntegerField(default=0)
    
    # Learning
    confidence_score = models.FloatField(default=0.5)  # 0.0-1.0
    influenced_by_modifications = models.ManyToManyField(PlanModification)
```

#### Workflow

```
1. User generates plan with AI modifications
2. User files plan with RRC
3. RRC reviews and approves/rejects
4. System updates RegulatorOutcome.status
5. Trigger learning feedback loop
6. Update confidence for similar modifications
7. Re-embed with new confidence weights
8. Future suggestions biased toward approved patterns
```

#### Confidence Formula

```python
def update_modification_confidence(modification, outcome_approved):
    """
    Update confidence based on regulator outcomes.
    
    Formula:
    - Start: 0.5 (neutral)
    - Approved: confidence = approval_rate
    - Multiple approvals (≥3): +0.1 bonus
    - Any rejections: -0.1 penalty
    - Range: 0.0-1.0
    """
    outcomes = modification.influenced_outcomes.all()
    approved_count = outcomes.filter(status='approved').count()
    rejected_count = outcomes.filter(status='rejected').count()
    total_count = approved_count + rejected_count
    
    approval_rate = approved_count / total_count if total_count > 0 else 0.5
    confidence = approval_rate
    
    if approved_count >= 3:
        confidence = min(confidence + 0.1, 1.0)  # Proven pattern
    if rejected_count > 0:
        confidence = max(confidence - 0.1, 0.0)  # Has failures
    
    return confidence
```

#### Propagation to Similar Modifications

**File**: `apps/assistant/services/learning_feedback.py`

```python
def propagate_confidence_to_similar(
    source_modification,
    outcome_approved,
    propagation_factor=0.5
):
    """
    When a modification is approved/rejected, update confidence for
    similar modifications.
    
    Similarity based on:
    - Same operation type
    - Same district
    - Same regulatory sections
    - Similar depths/formations
    - Vector similarity score
    """
    similar_mods = find_similar_modifications(source_modification)
    delta = 0.1 * propagation_factor if outcome_approved else -0.1 * propagation_factor
    
    for mod in similar_mods:
        # Update confidence in DocumentVector metadata
        vectors = DocumentVector.objects.filter(metadata__modification_id=mod.id)
        for vector in vectors:
            current = vector.metadata['outcome']['confidence']
            new = max(0.0, min(1.0, current + delta))
            vector.metadata['outcome']['confidence'] = new
            vector.save()
```

#### Usage in Suggestions

```python
def get_confidence_weighted_suggestions(query_context, min_confidence=0.5):
    """
    Get suggestions sorted by confidence * similarity.
    
    Biases toward regulator-approved patterns.
    """
    suggestions = DocumentVector.objects.filter(
        metadata__type='plan_modification',
        metadata__outcome__confidence__gte=min_confidence,
        metadata__district=query_context['district']
    ).annotate(
        similarity=CosineDistance('vector', query_embedding),
        weighted_score=F('similarity') * F('metadata__outcome__confidence')
    ).order_by('-weighted_score')[:10]
    
    return suggestions
```

#### Marking Outcomes

```python
# Example: Mark plan as approved by RRC
outcome = RegulatorOutcome.objects.get(plan_snapshot=plan_snapshot)
outcome.mark_approved(
    approved_at=timezone.now(),
    reviewer_notes="Approved - meets all requirements"
)

# This automatically:
# 1. Sets status='approved'
# 2. Boosts confidence_score
# 3. Calls _update_similar_modification_confidence(boost=True)
# 4. Propagates confidence to similar modifications
# 5. Re-embeds with new weights
```

---

## 3. Version Diff Visualization (JSON Patch)

### Problem
The API returns diffs, but frontend needs:
- **JSON Patch** (RFC 6902) for programmatic application
- **Step-by-step changes** with color coding
- **Human-readable summaries** for quick understanding

### Solution: Precomputed Diff Service

**File**: `apps/assistant/services/plan_differ.py`

#### Generate JSON Patch

```python
def generate_json_patch(source_payload, target_payload):
    """
    Generate RFC 6902 JSON Patch operations.
    
    Example output:
    [
        {"op": "remove", "path": "/steps/5"},
        {"op": "remove", "path": "/steps/11"},
        {"op": "replace", "path": "/materials_totals/total_sacks", "value": 250},
        {"op": "add", "path": "/steps/5/note", "value": "Combined with step 11"}
    ]
    """
    patch = jsonpatch.make_patch(source_payload, target_payload)
    return list(patch)
```

#### Categorize Step Changes

```python
def categorize_step_changes(source_steps, target_steps):
    """
    Categorize each step change for UI visualization.
    
    Returns:
    [
        StepDiff(
            step_id=5,
            change_type='removed',
            field_changes=[],
            summary="Step 5 removed: cement_plug at 6500-6550 ft"
        ),
        StepDiff(
            step_id=3,
            change_type='modified',
            field_changes=[
                {'field': 'top_ft', 'old_value': 6500, 'new_value': 6450},
                {'field': 'bottom_ft', 'old_value': 6550, 'new_value': 6600}
            ],
            summary="Step 3 modified: top_ft, bottom_ft"
        )
    ]
    """
```

#### Human-Readable Summary

```python
def generate_human_readable_summary(source, target, step_diffs):
    """
    Generate plain English summary.
    
    Example:
    "Removed 2 steps: 5, 11. Materials: 150 sacks saved (400 → 250). 
     Violations: 1 removed (1 → 0)."
    """
```

#### API Response

**GET /api/plans/compare/{snapshot_id_1}/{snapshot_id_2}**

```json
{
  "snapshot_1": {
    "id": 10,
    "plan_id": "abc123",
    "kind": "baseline",
    "created_at": "2025-11-02T10:00:00Z"
  },
  "snapshot_2": {
    "id": 15,
    "plan_id": "abc123",
    "kind": "post_edit",
    "created_at": "2025-11-02T10:30:00Z"
  },
  
  "json_patch": [
    {"op": "remove", "path": "/steps/5"},
    {"op": "remove", "path": "/steps/11"},
    {"op": "replace", "path": "/materials_totals/total_sacks", "value": 250},
    {"op": "add", "path": "/steps/5/note", "value": "Combined with step 11"}
  ],
  
  "steps": [
    {
      "step_id": 5,
      "change_type": "removed",
      "highlight_color": "#ff4444",
      "summary": "Step 5 removed: cement_plug at 6500-6550 ft",
      "field_changes": []
    },
    {
      "step_id": 11,
      "change_type": "removed",
      "highlight_color": "#ff4444",
      "summary": "Step 11 removed: cement_plug at 9500-9550 ft",
      "field_changes": []
    },
    {
      "step_id": 3,
      "change_type": "modified",
      "highlight_color": "#ffaa44",
      "summary": "Step 3 modified: top_ft, bottom_ft",
      "field_changes": [
        {"field": "top_ft", "old_value": 6500, "new_value": 6450},
        {"field": "bottom_ft", "old_value": 6550, "new_value": 6600}
      ]
    }
  ],
  
  "summary": {
    "steps_added": 0,
    "steps_removed": 2,
    "steps_modified": 1,
    "steps_unchanged": 4,
    "materials_delta": -150,
    "violations_delta": -1,
    "json_patch_ops": 4,
    "human_readable": "Removed 2 steps: 5, 11. Materials: 150 sacks saved (400 → 250). Violations: 1 removed (1 → 0)."
  },
  
  "modification": {
    "id": 5,
    "op_type": "combine_plugs",
    "description": "Combined formation top plugs at 6500ft and 9500ft",
    "risk_score": 0.15,
    ...
  }
}
```

#### UI Usage

```javascript
// Apply JSON Patch to live plan
import { applyPatch } from 'fast-json-patch';

const modifiedPlan = applyPatch(originalPlan, response.json_patch).newDocument;

// Highlight steps by change type
response.steps.forEach(step => {
  const stepEl = document.getElementById(`step-${step.step_id}`);
  stepEl.style.backgroundColor = step.highlight_color;
  stepEl.title = step.summary;
});

// Show summary
alert(response.summary.human_readable);
```

---

## Integration Points

### 1. When Modification is Created

```python
# In ChatMessage.post() or plan modification endpoint
modification = PlanModification.objects.create(...)

# Embed with rich metadata (async)
from apps.assistant.tasks import embed_plan_modification
embed_plan_modification.delay(modification.id)
```

### 2. When Plan is Filed

```python
# User clicks "File with RRC"
outcome = RegulatorOutcome.objects.create(
    plan_snapshot=plan_snapshot,
    status='pending',
    filed_at=timezone.now()
)

# Link modifications that influenced this plan
modifications = PlanModification.get_modification_chain(plan_snapshot)
outcome.influenced_by_modifications.set(modifications)
```

### 3. When RRC Responds

```python
# Webhook or manual update
outcome = RegulatorOutcome.objects.get(plan_snapshot=plan_snapshot)
if rrc_approved:
    outcome.mark_approved(
        approved_at=timezone.now(),
        reviewer_notes="Approved - complies with all requirements"
    )
else:
    outcome.mark_rejected(
        reviewed_at=timezone.now(),
        reviewer_notes="Formation top coverage insufficient"
    )

# This automatically propagates confidence to similar modifications
```

### 4. When Suggesting Modifications

```python
# AI suggests modifications, filtered by confidence
from apps.assistant.services.learning_feedback import get_confidence_weighted_suggestions

suggestions = get_confidence_weighted_suggestions(
    query_context={
        "district": "08A",
        "operation_type": "combine_plugs",
        "formations": ["Dean", "Strawn"]
    },
    min_confidence=0.7  # Only suggest proven patterns
)

# Show to user with confidence scores
for suggestion in suggestions:
    print(f"Suggestion: {suggestion['description']}")
    print(f"Confidence: {suggestion['outcome']['confidence']:.0%}")
    print(f"Approval rate: {suggestion['approval_rate']:.0%}")
```

---

## Database Schema

### RegulatorOutcome Migration

```bash
cd /Users/ru/Git/JMR/RegulatoryAgent/regulagent-django/RegulAgentV2/regulagent-backend
docker exec regulagent_web python manage.py makemigrations assistant
docker exec regulagent_web python manage.py migrate
```

---

## Testing

### 1. Create Modification with Rich Metadata

```python
modification = PlanModification.objects.create(...)
metadata = build_modification_metadata(modification)
print(json.dumps(metadata, indent=2))
```

### 2. Mark Outcome and Propagate

```python
outcome = RegulatorOutcome.objects.create(plan_snapshot=snapshot, status='pending')
outcome.mark_approved()

# Check confidence updates
similar_mods = find_similar_modifications(modification)
for mod in similar_mods:
    vectors = DocumentVector.objects.filter(metadata__modification_id=mod.id)
    print(f"Mod {mod.id}: confidence={vectors[0].metadata['outcome']['confidence']}")
```

### 3. Compare Versions

```bash
curl -X GET http://localhost:8001/api/plans/compare/10/15/ \
  -H "Authorization: Bearer $TOKEN"
```

---

## Summary

| Feature | File | Status | Benefit |
|---------|------|--------|---------|
| **Contextual Embeddings** | `modification_embedder.py` | ✅ Complete | Targeted similarity queries |
| **Learning Feedback** | `learning_feedback.py`, `regulator_outcome.py` | ✅ Complete | Bias toward approved patterns |
| **Diff Visualization** | `plan_differ.py` | ✅ Complete | UI-friendly JSON patches |

**Next Steps**:
1. Run migrations for `RegulatorOutcome` model
2. Install `jsonpatch` package (`pip install jsonpatch`)
3. Implement OpenAI embedding generation
4. Set up RRC outcome webhooks/polling
5. Build frontend visualizations using diff data

**Status**: ✅ Infrastructure complete, awaiting OpenAI integration  
**Date**: 2025-11-02

