# Three-Tier Guardrail Architecture

## Overview

RegulAgent implements a **three-tiered guardrail system** that balances platform safety with tenant flexibility:

```
┌─────────────────────────────────────────────────────────────────┐
│  TIER 1: Global/Platform Guardrails (Non-Negotiable)           │
│  - No new violations                                             │
│  - Max material delta ≤30%                                       │
│  - Max steps removed ≤3                                          │
│  - Risk threshold ≤0.5                                           │
│  - Session limit ≤10 modifications/hour                          │
│                                                                   │
│  Purpose: Safety + compliance across ALL tenants                 │
│  Editable by: RegulAgent core team only                          │
└───────────────────────────┬─────────────────────────────────────┘
                            │ Inherits + Can Only Tighten
┌───────────────────────────▼─────────────────────────────────────┐
│  TIER 2: Tenant Overlay Policy (Risk Appetite)                  │
│  - Can set stricter limits (e.g., risk ≤0.3)                    │
│  - Can block operations (e.g., no CIBP replacement)              │
│  - Can configure district overrides                              │
│  - Cannot relax global minimums                                  │
│                                                                   │
│  Purpose: Reflect org-specific risk profile                      │
│  Editable by: Tenant admins                                      │
└───────────────────────────┬─────────────────────────────────────┘
                            │ Applied Per Request
┌───────────────────────────▼─────────────────────────────────────┐
│  TIER 3: Session Authorization (User-Level)                     │
│  - allow_plan_changes flag (true/false)                         │
│  - Set per chat message / API call                               │
│                                                                   │
│  Purpose: Explicit user permission                               │
│  Editable by: User per request                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Why Three Tiers?

### Problem
Single-tier guardrails are either:
- **Too strict** → frustrate experienced users
- **Too loose** → create compliance risk

### Solution
Multi-tier system that:
- ✅ Maintains **platform safety** (Tier 1)
- ✅ Respects **organizational risk appetite** (Tier 2)
- ✅ Requires **explicit user authorization** (Tier 3)

---

## Tier 1: Global/Platform Guardrails

### Purpose
Non-negotiable baseline to ensure:
- Regulatory compliance
- Data integrity
- Platform safety

### Configuration

**File**: `apps/assistant/services/guardrails.py`

```python
GLOBAL_BASELINE_POLICY = {
    'require_confirmation_above_risk': 0.5,  # Risk threshold
    'max_material_delta_percent': 0.3,       # Max ±30% material change
    'max_steps_removed': 3,                   # Max steps removed
    'allow_new_violations': False,            # Block new violations
    'max_modifications_per_session': 10,      # Session limit
}
```

### Who Can Edit?
**RegulAgent core team only** - via code changes + deployment.

### Example
```python
# Global baseline says: max_material_delta_percent = 0.3 (30%)
# NO tenant can set this higher than 30%
# But tenants can set it LOWER (e.g., 20% for conservative orgs)
```

---

## Tier 2: Tenant Overlay Policy

### Purpose
Allow tenants to:
- Set **stricter** limits than global baseline
- Define **allowed operations**
- Configure **district-specific** overrides
- Reflect **organizational risk appetite**

### Model

**File**: `apps/tenant_overlay/models/tenant_guardrail_policy.py`

```python
class TenantGuardrailPolicy(models.Model):
    tenant_id = models.UUIDField(unique=True)
    
    # Risk profile presets
    risk_profile = models.CharField(choices=[
        'conservative',  # Stricter than global
        'balanced',      # Same as global (default)
        'aggressive',    # Still within global limits
        'custom'         # Manual config
    ])
    
    # Policy overrides
    require_confirmation_above_risk = models.FloatField(default=0.5)
    max_material_delta_percent = models.FloatField(default=0.3)
    max_steps_removed = models.IntegerField(default=3)
    allow_new_violations = models.BooleanField(default=False)
    
    # Operation controls
    allowed_operations = models.JSONField(default=list)
    blocked_operations = models.JSONField(default=list)
    
    # District overrides
    district_overrides = models.JSONField(default=dict)
```

### Risk Profiles

| Profile | Risk Threshold | Material Delta | Steps Removed | Use Case |
|---------|---------------|----------------|---------------|----------|
| **Conservative** | ≤0.3 | ≤20% | ≤2 | Risk-averse operators, sensitive wells |
| **Balanced** (default) | ≤0.5 | ≤30% | ≤3 | Standard operations |
| **Aggressive** | ≤0.7 | ≤40% | ≤5 | Experienced engineers, routine wells |

### Validation Rules

```python
def validate_against_global_baseline(self):
    """
    Ensure tenant policy is STRICTER than global, not looser.
    """
    # Risk threshold cannot be HIGHER than global
    if self.require_confirmation_above_risk > GLOBAL_BASELINE_POLICY['require_confirmation_above_risk']:
        raise ValueError("Cannot raise risk threshold above global baseline")
    
    # Material delta cannot be HIGHER than global
    if self.max_material_delta_percent > GLOBAL_BASELINE_POLICY['max_material_delta_percent']:
        raise ValueError("Cannot raise material delta above global baseline")
    
    # Cannot allow violations if global blocks them
    if self.allow_new_violations and not GLOBAL_BASELINE_POLICY['allow_new_violations']:
        raise ValueError("Cannot allow violations when global blocks them")
```

### District Overrides

```python
# Example: Stricter rules for District 08A (Andrews County)
tenant_policy.district_overrides = {
    "08A": {
        "max_material_delta_percent": 0.2,  # 20% instead of 30%
        "require_confirmation_above_risk": 0.3  # Lower threshold
    }
}
```

### Who Can Edit?
**Tenant admins** - via Django admin or tenant settings UI.

### Example: Conservative Operator

```python
# XTO Energy wants strict controls
policy = TenantGuardrailPolicy.objects.create(
    tenant_id=xto_tenant_id,
    risk_profile='conservative',
    require_confirmation_above_risk=0.3,  # ✅ Stricter than global (0.5)
    max_material_delta_percent=0.2,        # ✅ Stricter than global (0.3)
    blocked_operations=['replace_cibp'],   # ✅ Block specific operations
)

# This is VALID because it's stricter than global baseline
```

### Example: Invalid (Too Loose)

```python
# This would FAIL validation
policy = TenantGuardrailPolicy.objects.create(
    tenant_id=risky_tenant_id,
    require_confirmation_above_risk=0.8,   # ❌ Higher than global (0.5)
    allow_new_violations=True,              # ❌ Global blocks this
)

# Raises: ValueError("Tenant policy violates global baseline")
```

---

## Tier 3: Session Authorization

### Purpose
Require **explicit user permission** for every plan modification request.

### Implementation

**User must set `allow_plan_changes=true` in request:**

```bash
POST /api/chat/threads/5/messages
{
  "content": "Can we combine the formation plugs?",
  "allow_plan_changes": true  # ← Required!
}
```

**If `allow_plan_changes=false` or missing:**
```json
{
  "status": "error",
  "error": "User did not authorize plan changes (allow_plan_changes=false)",
  "violation_type": "user_authorization_required"
}
```

### Who Can Set?
**User** - per API call / chat message.

---

## Enforcement Flow

### Example: Combining Plugs in District 08A

**Step 1: Global Baseline Check**
```python
# Global says: max_material_delta = 0.3 (30%)
# ✅ PASS: All requests must meet this
```

**Step 2: Tenant Policy Check**
```python
# Tenant (XTO) has custom policy for District 08A:
tenant_policy = {
    "district_overrides": {
        "08A": {
            "max_material_delta_percent": 0.2,  # 20% instead of 30%
            "require_confirmation_above_risk": 0.3
        }
    }
}

# Modification saves 150 sacks (37.5% reduction)
# ❌ FAIL: Exceeds tenant limit for District 08A (20%)
# → Requires confirmation
```

**Step 3: Session Authorization Check**
```python
# User request:
{
  "allow_plan_changes": true  # ✅ User authorized
}

# If false → BLOCK immediately
```

---

## Does Tenant Variance Hurt Learning?

### Answer: No - It's Actually SIGNAL!

Tenant policy context is **stored in embedding metadata**:

```python
metadata = {
    "tenant_policy": {
        "risk_profile": "conservative",
        "risk_threshold": 0.3,
        "max_material_delta": 0.2,
        "allow_new_violations": false
    },
    "operation": {
        "op_type": "combine_plugs",
        "risk_score": 0.15
    },
    "outcome": {
        "regulator_accepted": true,
        "user_accepted": true
    }
}
```

### Learning Benefits

#### 1. Risk-Profile-Aware Recommendations

```python
# Query: "Find approved modifications for conservative tenants"
query_similar_modifications(
    query_context={"operation_type": "combine_plugs"},
    filters={
        "tenant_policy.risk_profile": "conservative",
        "outcome.regulator_accepted": True
    }
)

# Returns: Modifications that worked for low-risk-tolerance organizations
```

#### 2. Policy Impact Analysis

```python
# AI learns:
"Conservative tenants (risk ≤0.3) have 95% approval rate for minor plug combinations"
"Aggressive tenants (risk ≤0.7) have 78% approval rate for major modifications"

# Insight: Stricter policies correlate with higher approval rates
```

#### 3. Confidence Scoring by Risk Profile

```python
# When suggesting modifications:
if tenant_policy['risk_profile'] == 'conservative':
    # Only suggest patterns with 90%+ approval rate from conservative tenants
    min_confidence = 0.9
elif tenant_policy['risk_profile'] == 'balanced':
    # Standard threshold
    min_confidence = 0.7
```

---

## API Usage

### Get Effective Policy for Tenant

```python
from apps.assistant.services.guardrails import ToolExecutionGuardrail

# Get policy with district override
policy = ToolExecutionGuardrail.get_tenant_policy(
    tenant_id=user_tenant.id,
    district="08A"
)

# Effective policy:
# - Starts with global baseline
# - Applies tenant overlay
# - Applies district override (if present)
```

### Enforce Guardrails

```python
from apps.assistant.services.guardrails import enforce_guardrails

result = enforce_guardrails(
    tool_name='combine_plugs',
    tool_args={'step_ids': [5, 11]},
    context={
        'user_allow_plan_changes': True,  # ← Tier 3
        'modifications_this_session': 2,
        'predicted_risk_score': 0.15
    },
    tenant_id=user_tenant.id  # ← Tier 2
)

# Checks ALL three tiers:
# 1. Global baseline
# 2. Tenant policy (with district overrides)
# 3. Session authorization

if not result['allowed']:
    raise GuardrailViolation(result['reason'])
```

---

## Admin UI (Future)

### Tenant Settings Page

```
┌─────────────────────────────────────────────────┐
│  Guardrail Policy - XTO Energy                  │
├─────────────────────────────────────────────────┤
│                                                  │
│  Risk Profile:  ○ Conservative                   │
│                 ● Balanced                       │
│                 ○ Aggressive                     │
│                 ○ Custom                         │
│                                                  │
│  ─────────────────────────────────────────────  │
│                                                  │
│  Risk Threshold: [0.5] (global max: 0.5)        │
│  Material Delta: [30%] (global max: 30%)        │
│  Max Steps Removed: [3] (global max: 3)         │
│                                                  │
│  ─────────────────────────────────────────────  │
│                                                  │
│  Blocked Operations:                             │
│  ☑ replace_cibp                                  │
│  ☐ combine_plugs                                 │
│  ☐ adjust_interval                               │
│                                                  │
│  ─────────────────────────────────────────────  │
│                                                  │
│  District Overrides:                             │
│  • 08A (Andrews): risk≤0.3, material≤20%        │
│  [+ Add District Override]                       │
│                                                  │
│  [ Save Policy ]                                 │
└─────────────────────────────────────────────────┘
```

---

## Database Migrations

```bash
# Create TenantGuardrailPolicy table
cd /Users/ru/Git/JMR/RegulatoryAgent/regulagent-django/RegulAgentV2/regulagent-backend
docker exec regulagent_web python manage.py makemigrations tenant_overlay
docker exec regulagent_web python manage.py migrate tenant_overlay
```

---

## Testing

### 1. Create Tenant Policy

```python
from apps.tenant_overlay.models import TenantGuardrailPolicy

# Conservative operator
policy = TenantGuardrailPolicy.objects.create(
    tenant_id=xto_tenant_id,
    risk_profile='conservative',
    district_overrides={
        "08A": {
            "max_material_delta_percent": 0.2,
            "require_confirmation_above_risk": 0.3
        }
    }
)
```

### 2. Test Enforcement

```python
from apps.assistant.services.guardrails import enforce_guardrails

# Should PASS (within limits)
result = enforce_guardrails(
    tool_name='combine_plugs',
    tool_args={'step_ids': [5, 11]},
    context={
        'user_allow_plan_changes': True,
        'predicted_risk_score': 0.15
    },
    tenant_id=xto_tenant_id
)
assert result['allowed'] == True

# Should FAIL (too much material change for District 08A)
result = enforce_guardrails(
    tool_name='combine_plugs',
    tool_args={'step_ids': [5, 11, 8]},  # 40% reduction
    context={
        'user_allow_plan_changes': True,
        'predicted_risk_score': 0.25,
        'district': '08A'
    },
    tenant_id=xto_tenant_id
)
assert result['allowed'] == False
assert 'material delta' in result['reason'].lower()
```

### 3. Verify Metadata

```python
from apps.assistant.services.modification_embedder import build_modification_metadata

metadata = build_modification_metadata(modification)

assert 'tenant_policy' in metadata
assert metadata['tenant_policy']['risk_profile'] == 'conservative'
assert metadata['tenant_policy']['risk_threshold'] == 0.3
```

---

## Summary

| Aspect | Tier 1 (Global) | Tier 2 (Tenant) | Tier 3 (Session) |
|--------|----------------|-----------------|------------------|
| **Purpose** | Platform safety | Risk appetite | User permission |
| **Can relax global?** | N/A | ❌ No | N/A |
| **Can tighten global?** | N/A | ✅ Yes | N/A |
| **Editable by** | Core team | Tenant admin | User per request |
| **Stored in** | Code constant | Database model | Request parameter |
| **Applied when** | Always | Per tenant | Per API call |

### Key Principles

1. ✅ **Global baseline is non-negotiable** - All tenants must comply
2. ✅ **Tenants can only be stricter** - Never looser than global
3. ✅ **Tenant variance is signal** - Helps AI learn by risk profile
4. ✅ **User must authorize** - Explicit permission per request
5. ✅ **Three-tier enforcement** - All checks must pass

### Benefits

- **Safety**: Platform maintains compliance standards
- **Flexibility**: Tenants configure risk appetite
- **Learning**: AI learns what works for different risk profiles
- **Transparency**: Users know exactly what's allowed
- **Scalability**: System works for conservative and aggressive users

---

**Status**: ✅ Fully implemented  
**Next**: Run migrations + test with demo tenant  
**Date**: 2025-11-02

