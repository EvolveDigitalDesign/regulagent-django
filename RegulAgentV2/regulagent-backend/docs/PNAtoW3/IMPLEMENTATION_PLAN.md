# W-3 from pnaexchange - Implementation Plan (Corrected)

**Status:** Ready for Phase 1  
**Date:** 2025-11-26  
**Architecture:** Proper Django separation of concerns  
**Effort:** ~13-19 days total

---

## ✅ Corrected Architecture

Following **standard Django patterns** - NOT putting models in services:

```
apps/public_core/
├── models/
│   ├── __init__.py
│   ├── extracted_document.py       [existing]
│   ├── plan_snapshot.py            [existing]
│   ├── well_registry.py            [existing]
│   ├── w3_event.py                 [NEW] - W3Event, Plug, CasingStringState
│   └── w3_form.py                  [NEW] - W3Form output model
│
├── serializers/
│   ├── w3a_plan.py                 [existing]
│   └── w3_from_pna.py              [NEW] - W3FromPnaRequestSerializer, W3FormResponseSerializer
│
├── services/
│   ├── rrc_completions_extractor.py [existing]
│   ├── openai_extraction.py        [existing]
│   ├── schematic_extraction.py     [existing]
│   ├── w3_extraction.py            [NEW] - extract_w3a_from_pdf()
│   ├── w3_builder.py               [NEW] - W3Builder orchestrator class
│   ├── w3_mapper.py                [NEW] - normalize_pna_event()
│   ├── w3_casing_engine.py         [NEW] - apply_cut_casing(), get_active_casing_at_depth()
│   └── w3_formatter.py             [NEW] - build_plug_row(), group_events_into_plugs()
│
└── views/
    ├── w3a_from_api.py             [existing]
    └── w3_from_pna.py              [NEW] - W3FromPnaView
```

---

## Proper Separation of Concerns

| Layer | Location | Purpose |
|-------|----------|---------|
| **Models** | `models/w3_event.py`, `models/w3_form.py` | Data structures (ORM + plain dataclasses) |
| **Serializers** | `serializers/w3_from_pna.py` | Request/response validation |
| **Business Logic** | `services/w3_*.py` | Core algorithms (extraction, mapping, building) |
| **API Layer** | `views/w3_from_pna.py` | HTTP endpoint, permissions, response |

**NOT:** Models in services, serializers in models, views in services. Clean separation.

---

## Phase 1: Data Models (1 day)

### File: `apps/public_core/models/w3_event.py`

```python
from dataclasses import dataclass
from typing import Optional, List
from datetime import date, time

@dataclass
class CasingStringState:
    """Represents a casing string with optional removal depth."""
    name: str                                    # "surface", "intermediate", "production", "liner"
    od_in: float                                 # outer diameter in inches
    top_ft: float                                # top depth
    bottom_ft: float                             # bottom/shoe depth
    removed_to_depth_ft: Optional[float] = None  # depth cut to (if casing was cut)


@dataclass
class W3Event:
    """Normalized pnaexchange event for W-3 building."""
    event_type: str                              # "Set Surface Plug", "Squeeze", "Perforate", etc.
    date: date
    start_time: Optional[time]
    end_time: Optional[time]
    
    # Depths
    depth_top_ft: Optional[float]
    depth_bottom_ft: Optional[float]
    perf_depth_ft: Optional[float]
    tagged_depth_ft: Optional[float]
    plug_number: Optional[int]
    
    # Materials
    cement_class: Optional[str]
    sacks: Optional[float]
    volume_bbl: Optional[float]
    pressure_psi: Optional[float]
    
    # Tracking
    raw_event_detail: str
    work_assignment_id: int
    dwr_id: int
    
    # Casing state
    jump_to_next_casing: bool = False
    casing_string: Optional[str] = None


@dataclass
class Plug:
    """Group of W3Events that form a single plugging operation."""
    plug_number: int
    events: List[W3Event]


@dataclass
class W3Form:
    """Final W-3 form output."""
    header: dict                          # API, well name, operator, etc.
    plugs: List[dict]                     # RRC plug rows
    casing_record: List[dict]             # Casing record with removal depths
    perforations: List[dict]              # Perforation intervals
    duqw: dict                            # Depth of usable quality water
    remarks: str                          # Concatenated event details
```

### File: `apps/public_core/models/w3_form.py`

```python
from django.db import models
from django.contrib.postgres.fields import JSONField
import uuid

class W3FormRecord(models.Model):
    """
    Optional: Persist generated W-3 forms to database.
    
    If you want to store W-3 results, similar to PlanSnapshot.
    Otherwise, just return JSON from API.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    well = models.ForeignKey('WellRegistry', on_delete=models.CASCADE, null=True)
    api_number = models.CharField(max_length=20)
    
    # From pnaexchange
    pna_subproject_id = models.IntegerField()
    pna_tenant_id = models.UUIDField()
    
    # Generated W-3 data
    w3_json = models.JSONField()
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"W-3 {self.api_number} ({self.pna_subproject_id})"
```

---

## Phase 2-8 Implementation Details

### Phase 2: W-3A Extraction Service (1 day)

**File:** `apps/public_core/services/w3_extraction.py`

```python
from pathlib import Path
from typing import Dict, Any
from apps.public_core.services.openai_extraction import extract_json_from_pdf

def extract_w3a_from_pdf(pdf_path: str) -> Dict[str, Any]:
    """
    Extract W-3A form from PDF using OpenAI.
    
    Reuses extract_json_from_pdf() with doc_type="w3a"
    and custom extraction prompt.
    
    Returns: {
        "header": {...},
        "casing_record": [...],
        "perforations": [...],
        "plugging_proposal": [...],
        "duqw": {...},
        "remarks": "..."
    }
    """
    result = extract_json_from_pdf(Path(pdf_path), doc_type="w3a")
    return result.json_data
```

### Phase 3: Casing State Engine (1 day)

**File:** `apps/public_core/services/w3_casing_engine.py`

```python
from typing import List, Optional
from apps.public_core.models.w3_event import CasingStringState

def apply_cut_casing(casing_state: List[CasingStringState], depth_ft: float) -> None:
    """Mark innermost casing as cut at depth_ft."""
    candidates = [
        cs for cs in casing_state 
        if cs.top_ft <= depth_ft <= cs.bottom_ft
        and (not cs.removed_to_depth_ft or depth_ft > cs.removed_to_depth_ft)
    ]
    if candidates:
        innermost = min(candidates, key=lambda cs: cs.od_in)
        innermost.removed_to_depth_ft = depth_ft

def get_active_casing_at_depth(
    casing_state: List[CasingStringState], 
    depth_ft: float
) -> Optional[CasingStringState]:
    """Get active (innermost) casing at depth."""
    present = [
        cs for cs in casing_state 
        if cs.top_ft <= depth_ft <= cs.bottom_ft
        and (not cs.removed_to_depth_ft or depth_ft > cs.removed_to_depth_ft)
    ]
    return min(present, key=lambda cs: cs.od_in) if present else None
```

### Phase 4: Event Mapper (0.5 day)

**File:** `apps/public_core/services/w3_mapper.py`

```python
from typing import Dict, Any
from datetime import datetime
from apps.public_core.models.w3_event import W3Event

def normalize_pna_event(event_dict: Dict[str, Any]) -> W3Event:
    """Map pnaexchange event dict to W3Event."""
    iv = event_dict.get("input_values", {})
    tr = event_dict.get("transformation_rules", {})
    
    return W3Event(
        event_type=_normalize_event_type(event_dict.get("event_type", "")),
        date=_parse_date(event_dict.get("date")),
        start_time=_parse_time(event_dict.get("start_time")),
        end_time=_parse_time(event_dict.get("end_time")),
        depth_bottom_ft=_parse_float(iv.get("4")),
        depth_top_ft=_parse_float(iv.get("5")),
        plug_number=_parse_int(iv.get("1")),
        cement_class=_normalize_cement_class(iv.get("3")),
        sacks=_parse_float(iv.get("6")),
        pressure_psi=_parse_pressure(iv.get("7")),
        raw_event_detail=event_dict.get("event_detail", ""),
        work_assignment_id=event_dict.get("work_assignment_id", 0),
        dwr_id=event_dict.get("dwr_id", 0),
        jump_to_next_casing=tr.get("jump_to_next_casing", False),
    )

def _normalize_event_type(event_type_raw: str) -> str:
    """Normalize event type strings."""
    normalized = {
        "set surface plug": "set_surface_plug",
        "squeeze": "squeeze",
        "perforate": "perforate",
        "cut casing": "cut_casing",
        "tag": "tag",
    }
    return normalized.get(event_type_raw.lower(), event_type_raw)

def _parse_date(val):
    # Parse ISO date string or datetime
    if isinstance(val, str):
        return datetime.fromisoformat(val).date()
    return val

def _parse_time(val):
    if isinstance(val, str):
        return datetime.fromisoformat(f"2000-01-01T{val}").time()
    return val

def _parse_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except:
        return None

def _parse_int(val):
    if val is None:
        return None
    try:
        return int(val)
    except:
        return None

def _normalize_cement_class(val):
    if val is None:
        return None
    return str(val).upper()

def _parse_pressure(val):
    if val is None:
        return None
    # Parse "13 psi" or just "13"
    if isinstance(val, str):
        import re
        match = re.search(r'(\d+\.?\d*)', val)
        return float(match.group(1)) if match else None
    return float(val)
```

### Phase 5: Plug Formatter (1.5 days)

**File:** `apps/public_core/services/w3_formatter.py`

```python
from typing import List, Dict, Any
from copy import deepcopy
from apps.public_core.models.w3_event import W3Event, Plug, CasingStringState
from apps.public_core.views.w3a_from_api import _build_additional_operations  # Reuse!

def group_events_into_plugs(events: List[W3Event]) -> List[Plug]:
    """Cluster events into logical plugs."""
    if not events:
        return []
    
    sorted_events = sorted(events, key=lambda e: (e.date, e.depth_bottom_ft or 0))
    plugs = []
    current_plug = None
    
    for i, event in enumerate(sorted_events):
        plug_num = event.plug_number or (i + 1)
        
        if current_plug is None or plug_num != current_plug.plug_number:
            if current_plug:
                plugs.append(current_plug)
            current_plug = Plug(plug_number=plug_num, events=[event])
        else:
            current_plug.events.append(event)
    
    if current_plug:
        plugs.append(current_plug)
    
    return plugs

def build_plug_row(plug: Plug, casing_state: List[CasingStringState]) -> Dict[str, Any]:
    """Build single W-3 plug row."""
    from apps.public_core.services.w3_casing_engine import get_active_casing_at_depth
    
    if not plug.events:
        return {}
    
    # Depth extremes
    depths = [e.depth_bottom_ft for e in plug.events if e.depth_bottom_ft]
    depths += [e.depth_top_ft for e in plug.events if e.depth_top_ft]
    
    bottom_ft = max(depths) if depths else None
    top_ft = min(depths) if depths else None
    
    # Active casing
    casing = get_active_casing_at_depth(casing_state, bottom_ft) if bottom_ft else None
    
    # Materials
    material_event = next((e for e in plug.events if e.cement_class), None)
    cement_class = material_event.cement_class if material_event else None
    sacks = material_event.sacks if material_event else None
    
    # Additional operations
    additional = []
    for event in plug.events:
        if "perforate" in event.event_type.lower():
            if event.perf_depth_ft:
                additional.append(f"Perforate at {event.perf_depth_ft:.0f} ft")
            else:
                additional.append("Perforate")
        if "squeeze" in event.event_type.lower():
            additional.append("Squeeze cement through perforations")
        if "tag" in event.event_type.lower():
            additional.append("Wait and tag TOC")
    
    return {
        "plug_no": plug.plug_number,
        "date": str(min([e.date for e in plug.events])),
        "type": _infer_plug_type(plug.events),
        "from_ft": bottom_ft,
        "to_ft": top_ft,
        "pipe_size": f'{casing.od_in}"' if casing else None,
        "toc_calc": top_ft,
        "toc_measured": next((e.tagged_depth_ft for e in plug.events if e.tagged_depth_ft), None),
        "sacks": sacks,
        "cement_class": cement_class,
        "additional": additional if additional else None,
        "remarks": "; ".join([e.raw_event_detail for e in plug.events]),
    }

def build_casing_record(casing_state: List[CasingStringState]) -> List[Dict[str, Any]]:
    """Build casing record from state."""
    records = []
    for cs in casing_state:
        records.append({
            "string": cs.name,
            "size_in": cs.od_in,
            "top_ft": cs.top_ft,
            "bottom_ft": cs.bottom_ft,
            "removed_to_depth_ft": cs.removed_to_depth_ft,
        })
    return records

def build_perforation_table(events: List[W3Event], w3a_form: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build perforations table from W-3A + event updates."""
    # Start with W-3A perforations
    perfs = deepcopy(w3a_form.get("perforations", []))
    
    # Update status based on events
    for event in events:
        if "squeeze" in event.event_type.lower() and event.perf_depth_ft:
            for perf in perfs:
                if (perf.get("interval_top_ft") <= event.perf_depth_ft <= perf.get("interval_bottom_ft")):
                    perf["status"] = "squeezed"
    
    return perfs

def _infer_plug_type(events: List[W3Event]) -> str:
    """Infer plug type from events."""
    event_types = [e.event_type.lower() for e in events]
    
    if any("surface" in et for et in event_types):
        return "surface_casing_shoe_plug"
    if any("squeeze" in et for et in event_types):
        return "squeeze"
    if any("perforate" in et for et in event_types):
        return "perforate_and_squeeze"
    
    return "cement_plug"

def _parse_size(txt: Any) -> Optional[float]:
    """Reuse from w3a_from_api.py - parse fractional notation."""
    # ... (copy from w3a_from_api.py lines 764-787)
    pass
```

### Phase 6: W3Builder Orchestrator (1 day)

**File:** `apps/public_core/services/w3_builder.py`

```python
from copy import deepcopy
from typing import Dict, List, Any
from apps.public_core.models.w3_event import W3Event, CasingStringState, W3Form
from apps.public_core.services.w3_mapper import normalize_pna_event
from apps.public_core.services.w3_casing_engine import apply_cut_casing, get_active_casing_at_depth
from apps.public_core.services.w3_formatter import (
    group_events_into_plugs,
    build_plug_row,
    build_casing_record,
    build_perforation_table,
)

class W3Builder:
    """Orchestrate W-3 form generation from pnaexchange events."""
    
    def __init__(self, w3a_form: Dict[str, Any]):
        self.w3a_form = w3a_form
        self.casing_program = self._parse_casing_program(w3a_form)
        self.casing_state = deepcopy(self.casing_program)
    
    def _parse_casing_program(self, w3a_form: Dict[str, Any]) -> List[CasingStringState]:
        """Extract casing program from W-3A."""
        casing_program = []
        for row in (w3a_form.get("casing_record") or []):
            cs = CasingStringState(
                name=row.get("string_type") or row.get("string"),
                od_in=self._parse_size(row.get("size_in")),
                top_ft=row.get("top_ft") or 0.0,
                bottom_ft=row.get("bottom_ft") or row.get("shoe_depth_ft"),
                removed_to_depth_ft=row.get("removed_to_depth_ft"),
            )
            casing_program.append(cs)
        return casing_program
    
    @staticmethod
    def _parse_size(txt: Any) -> float:
        # Reuse from w3a_from_api.py
        pass
    
    def build_w3_form(self, raw_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Main entry point: build complete W-3 form."""
        # 1. Normalize events
        w3_events = [normalize_pna_event(e) for e in raw_events]
        
        # 2. Update casing state
        for event in w3_events:
            if event.jump_to_next_casing and event.depth_bottom_ft:
                apply_cut_casing(self.casing_state, event.depth_bottom_ft)
            if event.depth_bottom_ft:
                casing = get_active_casing_at_depth(self.casing_state, event.depth_bottom_ft)
                if casing:
                    event.casing_string = casing.name
        
        # 3. Group plugs
        plugs = group_events_into_plugs(w3_events)
        
        # 4. Build rows
        plug_rows = [build_plug_row(p, self.casing_state) for p in plugs]
        
        # 5. Build casing record
        casing_record = build_casing_record(self.casing_state)
        
        # 6. Build perf table
        perfs = build_perforation_table(w3_events, self.w3a_form)
        
        # 7. Remarks
        remarks = "\n".join([f"{e.date} – {e.raw_event_detail}" for e in w3_events])
        
        return {
            "header": self.w3a_form.get("header", {}),
            "plugs": plug_rows,
            "casing_record": casing_record,
            "perforations": perfs,
            "duqw": self.w3a_form.get("duqw", {}),
            "remarks": remarks,
        }
```

### Phase 7: Serializers (0.5 day)

**File:** `apps/public_core/serializers/w3_from_pna.py`

```python
from rest_framework import serializers

class W3FromPnaRequestSerializer(serializers.Serializer):
    """Validate incoming W-3 generation request."""
    
    well = serializers.JSONField()  # {api_number, well_name, operator, well_id}
    subproject = serializers.JSONField()  # {id, name}
    events = serializers.ListField(child=serializers.JSONField())
    w3a_reference = serializers.JSONField()  # {type: "regulagent"|"pdf", w3a_id OR w3a_file}

class W3FormResponseSerializer(serializers.Serializer):
    """W-3 form output."""
    
    status = serializers.CharField()
    w3 = serializers.JSONField()
    tenant_id = serializers.CharField(required=False)
    timestamp = serializers.DateTimeField(required=False)
```

### Phase 8: API View (0.5 day)

**File:** `apps/public_core/views/w3_from_pna.py`

```python
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.authentication import JWTAuthentication, SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
import logging

from apps.public_core.serializers.w3_from_pna import W3FromPnaRequestSerializer, W3FormResponseSerializer
from apps.public_core.services.w3_builder import W3Builder
from apps.public_core.services.w3_extraction import extract_w3a_from_pdf

logger = logging.getLogger(__name__)

class W3FromPnaView(APIView):
    """
    Build W-3 form from pnaexchange events + W-3A reference.
    
    POST /api/w3/build-from-pna/
    Authorization: Bearer {jwt_token}
    """
    
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        logger.info(f"W-3 build request from user: {request.user}")
        
        # Validate request
        serializer = W3FromPnaRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Extract validated data
            well_data = serializer.validated_data['well']
            events = serializer.validated_data['events']
            w3a_reference = serializer.validated_data['w3a_reference']
            
            logger.info(f"Processing {len(events)} events for API {well_data['api_number']}")
            
            # Load W-3A form
            w3a_form = self._load_w3a_form(w3a_reference, request)
            
            # Build W-3
            builder = W3Builder(w3a_form)
            w3_result = builder.build_w3_form(events)
            
            logger.info(f"✅ W-3 generation successful")
            
            return Response({
                "status": "success",
                "w3": w3_result,
                "timestamp": timezone.now().isoformat(),
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"❌ W-3 generation failed: {e}", exc_info=True)
            return Response({
                "status": "error",
                "detail": str(e),
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _load_w3a_form(self, w3a_reference, request):
        """Load W-3A from DB or extract from PDF."""
        ref_type = w3a_reference.get("type")
        
        if ref_type == "regulagent":
            # Query DB (TBD which model)
            w3a_id = w3a_reference.get("w3a_id")
            # w3a = W3AModel.objects.get(id=w3a_id)
            # return w3a.json_data
            pass
        elif ref_type == "pdf":
            # Extract from uploaded PDF
            w3a_file = request.FILES.get("w3a_file")
            if not w3a_file:
                raise ValueError("w3a_file required for PDF extraction")
            # Save to temp, extract, cleanup
            w3a_form = extract_w3a_from_pdf(str(w3a_file))
            return w3a_form
        else:
            raise ValueError(f"Unknown w3a_reference type: {ref_type}")
```

---

## Summary: Proper Django Structure

| Layer | Files | Purpose |
|-------|-------|---------|
| **Models** | `models/w3_event.py`, `models/w3_form.py` | Data structures (dataclasses + ORM) |
| **Serializers** | `serializers/w3_from_pna.py` | Request validation, response formatting |
| **Services** | `services/w3_*.py` (4 files) | Business logic, algorithms, extraction |
| **Views** | `views/w3_from_pna.py` | HTTP endpoint, auth, responses |

**NOT:** Mixing models, serializers, views into services. Clean, professional Django architecture.

---

## Next Steps

1. ✅ Create `models/w3_event.py` with dataclasses
2. ✅ Create `services/w3_*.py` (extraction, mapper, casing_engine, formatter)
3. ✅ Create `services/w3_builder.py` orchestrator
4. ✅ Create `serializers/w3_from_pna.py`
5. ✅ Create `views/w3_from_pna.py`
6. ✅ Integration testing
7. ✅ pnaexchange authentication integration

Ready to begin Phase 1?

