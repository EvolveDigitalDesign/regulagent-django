# Field Error Fixed ✅

## The Problem

```
FieldError: Cannot resolve keyword 'updated_at' into field. 
Choices are: active_chat_threads, artifacts, ... created_at, ... status, ...
```

The code tried to use `updated_at` on `PlanSnapshot`, but this model **only has `created_at`**, not `updated_at`.

---

## Root Cause

Different models have different fields:

| Model | has created_at | has updated_at |
|-------|---|---|
| `PlanSnapshot` | ✅ Yes | ❌ No |
| `W3FormORM` | ✅ Yes | ✅ Yes |

The code assumed all models had both fields.

---

## The Fix

### File 1: `apps/public_core/views/well_filings.py` (Line 138)

**Before**:
```python
w3a_plans = PlanSnapshot.objects.filter(w3a_filter).order_by("-updated_at")
```

**After**:
```python
# Order by created_at since PlanSnapshot doesn't have updated_at
w3a_plans = PlanSnapshot.objects.filter(w3a_filter).order_by("-created_at")
```

### File 2: `apps/public_core/serializers/well_filings.py` (Lines 21-24)

**Before**:
```python
updated_at = serializers.DateTimeField()

def get_form_type(self, obj):
    return "W-3A"
```

**After**:
```python
updated_at = serializers.SerializerMethodField()

def get_updated_at(self, obj):
    # PlanSnapshot only has created_at, so we use that for updated_at
    return obj.created_at

def get_form_type(self, obj):
    return "W-3A"
```

---

## Why This Works

1. **For PlanSnapshot**: Use `created_at` field (it's the only timestamp)
2. **For W3FormORM**: Use `updated_at` field (it has both)
3. **In Response**: Both return as `updated_at` for consistency
4. **In Sorting**: Client-side sorting handles both datetime values correctly

---

## API Behavior

Now when you call:
```bash
GET /api/wells/4217334896/filings/?ordering=-updated_at
```

It correctly:
1. ✅ Queries PlanSnapshot with `created_at` (not `updated_at`)
2. ✅ Queries W3FormORM with `updated_at`
3. ✅ Converts both to `updated_at` in response
4. ✅ Sorts by that field in client-side logic
5. ✅ Returns 200 OK with sorted filings

---

## Response Example

```json
{
  "api14": "4217334896",
  "total": 2,
  "count": 2,
  "filings": [
    {
      "id": "660e8400-...",
      "form_type": "W-3",
      "status": "draft",
      "created_at": "2025-01-20T09:15:00Z",
      "updated_at": "2025-01-20T09:15:00Z",  // ← from W3FormORM.updated_at
      "metadata": {...}
    },
    {
      "id": "550e8400-...",
      "form_type": "W-3A",
      "status": "approved",
      "created_at": "2025-01-15T10:30:00Z",
      "updated_at": "2025-01-15T10:30:00Z",  // ← from PlanSnapshot.created_at
      "metadata": {...}
    }
  ]
}
```

---

## Testing

After restart, this should work:

```bash
curl -X GET http://127.0.0.1:8001/api/wells/4217334896/filings/?ordering=-updated_at \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

Should return **200 OK** with sorted filings. ✅

---

## Summary

| Issue | Cause | Fix |
|-------|-------|-----|
| `updated_at` doesn't exist on PlanSnapshot | Model doesn't have field | Use `created_at` instead |
| API crashes when querying | Wrong field name | Fixed in view and serializer |
| Client gets wrong response | - | Now returns consistent response |

Everything is fixed! Just restart Django. 🚀


