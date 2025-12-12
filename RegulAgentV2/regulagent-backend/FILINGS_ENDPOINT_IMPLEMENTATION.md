# Well Filings Unified Endpoint Implementation Plan

## Overview
Create a single endpoint that returns all filings (W3A, W3, GAU, etc.) for a specific well with filtering, pagination, and tenant isolation.

**Endpoint**: `GET /api/wells/{api14}/filings/`

---

## Response Structure

```json
{
  "api14": "42-003-01016",
  "total": 15,
  "count": 10,
  "next": "http://api.example.com/api/wells/42-003-01016/filings/?page=2",
  "previous": null,
  "filings": [
    {
      "id": "plan-snap-uuid-001",
      "form_type": "W3A",
      "status": "agency_approved",
      "created_at": "2025-01-15T10:30:00Z",
      "updated_at": "2025-01-16T14:45:00Z",
      "metadata": {
        "plan_id": "42-003-01016:combined",
        "kernel_version": "2.1.0",
        "visibility": "public"
      }
    },
    {
      "id": "w3-form-001",
      "form_type": "W3",
      "status": "submitted",
      "created_at": "2025-01-20T09:15:00Z",
      "updated_at": "2025-01-21T11:22:00Z",
      "metadata": {
        "submitted_by": "john.engineer@company.com",
        "rrc_confirmation_number": "RRC-2025-001234",
        "events_count": 24
      }
    }
  ]
}
```

---

## Query Parameters

### Filtering
- `form_type` - Filter by form type (W3A, W3, GAU, W15, W2, H5)
  - Example: `?form_type=W3A`
  - Multiple values: `?form_type=W3A&form_type=W3`

- `status` - Filter by status
  - W3A: `draft`, `internal_review`, `engineer_approved`, `filed`, `under_agency_review`, `agency_approved`, `agency_rejected`, `revision_requested`, `withdrawn`
  - W3: `draft`, `submitted`, `approved`, `rejected`, `archived`
  - Example: `?status=approved`

### Pagination
- `page` - Page number (default: 1)
- `page_size` - Items per page (default: 25, max: 100)
  - Example: `?page=2&page_size=50`

### Sorting
- `ordering` - Sort field (default: `-created_at`)
  - Options: `created_at`, `-created_at`, `updated_at`, `-updated_at`, `form_type`, `status`
  - Example: `?ordering=-updated_at`

---

## Implementation Details

### 1. Data Sources

**W3A Plans** (from `PlanSnapshot`):
- All snapshots for the well with `kind='baseline'` or `kind='submitted'` or `kind='approved'`
- Status maps to `PlanSnapshot.status`
- Form type: `"W3A"`

**W3 Forms** (from `W3FormORM`):
- All forms linked to the well
- Status from `W3FormORM.status`
- Form type: `"W3"`

**Future Forms** (W15, W2, H5, GAU):
- Will need similar ORM models with consistent schema
- Same status/metadata patterns

### 2. Serializer Design

```python
# apps/public_core/serializers/well_filings.py

from rest_framework import serializers
from ..models import PlanSnapshot, W3FormORM, WellRegistry

class FilingMetadataSerializer(serializers.Serializer):
    """Base metadata for filings"""
    pass

class W3AFilingSerializer(serializers.Serializer):
    """W3A Plan Snapshot"""
    id = serializers.SerializerMethodField()
    form_type = serializers.SerializerMethodField()
    status = serializers.CharField(source='status')
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    metadata = serializers.SerializerMethodField()
    
    def get_id(self, obj):
        return str(obj.id)
    
    def get_form_type(self, obj):
        return "W3A"
    
    def get_metadata(self, obj):
        return {
            "plan_id": obj.plan_id,
            "kernel_version": obj.kernel_version,
            "visibility": obj.visibility,
        }

class W3FilingSerializer(serializers.Serializer):
    """W3 Form ORM"""
    id = serializers.SerializerMethodField()
    form_type = serializers.SerializerMethodField()
    status = serializers.CharField(source='status')
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    metadata = serializers.SerializerMethodField()
    
    def get_id(self, obj):
        return str(obj.id)
    
    def get_form_type(self, obj):
        return "W3"
    
    def get_metadata(self, obj):
        return {
            "submitted_by": obj.submitted_by,
            "submitted_at": obj.submitted_at.isoformat() if obj.submitted_at else None,
            "rrc_confirmation_number": obj.rrc_confirmation_number,
            "events_count": obj.w3_events.count() if hasattr(obj, 'w3_events') else 0,
        }

class WellFilingsSerializer(serializers.Serializer):
    """Unified filings response"""
    api14 = serializers.CharField()
    total = serializers.IntegerField()
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    filings = serializers.ListField(child=serializers.JSONField())
```

### 3. View Implementation

```python
# apps/public_core/views/well_filings.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import JWTAuthentication, SessionAuthentication
from rest_framework.pagination import PageNumberPagination
from django.db.models import Q
from django.shortcuts import get_object_or_404

from ..models import WellRegistry, PlanSnapshot, W3FormORM
from ..serializers.well_filings import W3AFilingSerializer, W3FilingSerializer, WellFilingsSerializer

class WellFilingsPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    page_size_query_max = 100
    page_query_param = 'page'

class WellFilingsView(APIView):
    """
    GET /api/wells/{api14}/filings/
    
    Returns unified list of all filings (W3A, W3, etc.) for a well.
    Supports filtering, pagination, and sorting.
    Tenant-isolated by default.
    """
    
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    pagination_class = WellFilingsPagination
    
    def get(self, request, api14):
        # Get well
        well = get_object_or_404(WellRegistry, api14=api14)
        
        # Start with empty filings list
        all_filings = []
        
        # Get W3A plans (PlanSnapshot)
        w3a_filter = Q(well=well)
        
        # Tenant isolation: only show public snapshots or tenant's own snapshots
        # (assuming tenant_id field exists on PlanSnapshot)
        tenant_id = getattr(request.user, 'tenant_id', None)
        if tenant_id:
            w3a_filter &= Q(
                Q(visibility='public') | Q(tenant_id=tenant_id)
            )
        else:
            w3a_filter &= Q(visibility='public')
        
        w3a_plans = PlanSnapshot.objects.filter(w3a_filter)
        
        # Serialize W3A plans
        for plan in w3a_plans:
            serializer = W3AFilingSerializer(plan)
            all_filings.append(serializer.data)
        
        # Get W3 forms
        w3_forms = W3FormORM.objects.filter(well=well)
        
        # Serialize W3 forms
        for form in w3_forms:
            serializer = W3FilingSerializer(form)
            all_filings.append(serializer.data)
        
        # Apply filtering
        form_type_filter = request.query_params.get('form_type')
        if form_type_filter:
            form_types = form_type_filter.split(',')
            all_filings = [
                f for f in all_filings 
                if f['form_type'] in form_types
            ]
        
        status_filter = request.query_params.get('status')
        if status_filter:
            statuses = status_filter.split(',')
            all_filings = [
                f for f in all_filings 
                if f['status'] in statuses
            ]
        
        # Apply sorting
        ordering = request.query_params.get('ordering', '-created_at')
        reverse = ordering.startswith('-')
        sort_field = ordering.lstrip('-')
        
        all_filings.sort(
            key=lambda x: x.get(sort_field, ''),
            reverse=reverse
        )
        
        # Paginate
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(all_filings, request)
        
        if page is not None:
            response_data = {
                'api14': well.api14,
                'total': paginator.page.paginator.count,
                'count': len(page),
                'next': paginator.get_next_link(),
                'previous': paginator.get_previous_link(),
                'filings': page,
            }
            return paginator.get_paginated_response(response_data)
        
        response_data = {
            'api14': well.api14,
            'total': len(all_filings),
            'count': len(all_filings),
            'next': None,
            'previous': None,
            'filings': all_filings,
        }
        
        return Response(response_data, status=status.HTTP_200_OK)
```

### 4. URL Configuration

```python
# In your main urls.py or api_urls.py

from apps.public_core.views.well_filings import WellFilingsView

urlpatterns = [
    # ... other patterns ...
    path('api/wells/<str:api14>/filings/', WellFilingsView.as_view(), name='well-filings'),
]
```

---

## Migration Strategy

### Phase 1: W3A Integration (Immediate)
- ✅ Use existing `PlanSnapshot` model
- ✅ Implement endpoint returning W3A plans
- ✅ Test with W3A data

### Phase 2: W3 Integration (After W3FormORM Populated)
- Add W3FormORM to endpoint
- Ensure W3FormORM.created_at/updated_at are set correctly
- Test with W3 data

### Phase 3: Future Forms (W15, W2, H5, GAU)
- Create ORM models following same pattern
- Add serializers
- Integrate into endpoint

---

## Frontend Integration

Once endpoint is ready, update `src/lib/api/wells.ts`:

```typescript
export interface Filing {
  id: string
  form_type: "W3A" | "W3" | "W15" | "W2" | "H5" | "GAU"
  status: string
  created_at: string
  updated_at: string
  metadata: Record<string, any>
}

export interface WellFilingsResponse {
  api14: string
  total: number
  count: number
  next: string | null
  previous: string | null
  filings: Filing[]
}

export async function fetchWellFilings(
  api14: string,
  params?: {
    form_type?: string[]
    status?: string[]
    page?: number
    page_size?: number
    ordering?: string
  }
): Promise<WellFilingsResponse> {
  const queryParams = new URLSearchParams()
  if (params?.form_type) {
    params.form_type.forEach(ft => queryParams.append('form_type', ft))
  }
  if (params?.status) {
    params.status.forEach(s => queryParams.append('status', s))
  }
  if (params?.page) queryParams.set('page', params.page.toString())
  if (params?.page_size) queryParams.set('page_size', params.page_size.toString())
  if (params?.ordering) queryParams.set('ordering', params.ordering)
  
  const url = `/api/wells/${api14}/filings/?${queryParams.toString()}`
  return apiGet<WellFilingsResponse>(url)
}
```

---

## Testing Checklist

- [ ] W3A plans appear in filings list
- [ ] W3 forms appear in filings list (once populated)
- [ ] Filter by form_type works
- [ ] Filter by status works
- [ ] Pagination works
- [ ] Sorting works
- [ ] Tenant isolation respected (W3A visibility, etc.)
- [ ] Returns 404 for non-existent wells
- [ ] Requires authentication

---

## Questions for Implementation

1. **Tenant Isolation for W3FormORM**: Does W3FormORM have a tenant_id field or should we infer from related user?
2. **W3 Events Count**: Should metadata.events_count come from W3FormORM.events or W3EventORM count?
3. **Form Type Extensibility**: Should we store form_type as an enum or string field for future extensibility?
4. **Filtering Performance**: For large well histories, should we add database-level filtering before serialization?


