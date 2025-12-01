"""
W-3 Event Mapper Service

Converts raw pnaexchange event data into standardized W3Event dataclass instances.
Handles parsing of input values, event types, and transformation rules.

Pnaexchange sends events with:
- event_id: Reference to FormContext event template
- display_text: "Set Intermediate Plug", "Tagged TOC", "Cut Casing", etc.
- form_template_text: Template with *_1_*, *_2_*, etc. placeholders
- input_values: Dict keyed by placeholder position {"1": "5.5", "2": "6997", ...}
- transformation_rules: Rules like {"jump_plugs_to_next_casing": true}
- date, start_time, end_time: Event timing
- work_assignment_id: DWR work assignment reference
- dwr_id: DWR record ID
"""

from __future__ import annotations
from datetime import date, time
from typing import Any, Dict, List, Optional
import logging
import re

from apps.public_core.models.w3_event import W3Event

logger = logging.getLogger(__name__)


# Mapping from pnaexchange event_id to normalized event_type
PNA_EVENT_ID_MAP = {
    4: "set_cement_plug",      # Set Intermediate Plug
    3: "set_surface_plug",     # Set Surface Plug
    7: "squeeze",              # Set Surface Plug - Squeeze
    2: "broke_circulation",    # Broke Circulation
    9: "pressure_up",          # Pressure Up
    6: "set_bridge_plug",      # Set CIBP
    12: "cut_casing",          # Cut Casing
    8: "tag_toc",              # Tag TOC
    5: "tag_toc",              # Tagged TOC
    1: "perforate",            # Perforation
    11: "tag_bridge_plug",     # Tag CIBP
    10: "rrc_approval",        # RRC Approval
}

# Expected input count for each event type
EVENT_INPUT_REQUIREMENTS = {
    4: 6,   # Set Intermediate Plug: plug, spot, class, from, to, displaced_with
    3: 4,   # Set Surface Plug: plug, sacks, class, from
    7: 5,   # Squeeze: plug, sacks, class, from, to
    2: 0,   # Broke Circulation: no inputs
    9: 0,   # Pressure Up: no inputs
    6: 2,   # Set CIBP: cibp_size, depth
    12: 1,  # Cut Casing: cut_depth
    8: 1,   # Tag TOC: depth
    5: 1,   # Tagged TOC: depth
    1: 1,   # Perforation: depth
    11: 1,  # Tag CIBP: depth
    10: 2,  # RRC Approval: approval_type, approval_ref
}


def parse_numeric(value: Any) -> Optional[float]:
    """
    Safely parse a numeric value from various input formats.
    
    Handles:
    - Strings with @ prefix: "@3000'" -> 3000.0
    - Strings with commas: "1,200" -> 1200.0
    - Strings with units: "5.5 in" -> 5.5
    - Already numeric: 123 -> 123.0
    """
    if value is None:
        return None
    
    try:
        # Convert to string and clean
        s = str(value).strip()
        if not s:
            return None
        
        # Remove @ prefix (used by pnaexchange for measured values)
        s = s.lstrip('@')
        
        # Remove commas for thousands separator
        s = s.replace(",", "")
        
        # Extract first numeric part (ignore units like 'ft', 'in', "'", etc)
        match = re.search(r'([-+]?\d+\.?\d*)', s)
        if match:
            return float(match.group(1))
        
        return None
    except (ValueError, TypeError, AttributeError):
        logger.debug(f"Could not parse numeric value: {value}")
        return None


def parse_time(time_str: Optional[str]) -> Optional[time]:
    """Parse time string (HH:MM:SS or HH:MM) into datetime.time object."""
    if not time_str:
        return None
    
    try:
        time_str = str(time_str).strip()
        if not time_str:
            return None
        
        parts = [int(p) for p in time_str.split(':')]
        
        if len(parts) == 2:  # HH:MM
            return time(parts[0], parts[1])
        elif len(parts) == 3:  # HH:MM:SS
            return time(parts[0], parts[1], parts[2])
        else:
            logger.warning(f"Unrecognized time format: {time_str}")
            return None
    
    except (ValueError, TypeError, IndexError):
        logger.warning(f"Could not parse time: {time_str}")
        return None


def normalize_event_type(event_id: Optional[int], display_text: str, event_type: Optional[str] = None) -> str:
    """
    Normalize event type using multiple sources in priority order.
    
    Priority:
    1. display_text (most reliable - check for plug keywords first)
    2. event_id (numeric) via PNA_EVENT_ID_MAP
    3. event_type (text field from pnaexchange payload)
    
    Args:
        event_id: Numeric event ID (if available)
        display_text: Display text from event
        event_type: Text event_type field from pnaexchange payload
    
    Returns:
        Normalized event type string
    """
    display_lower = display_text.lower() if display_text else ""
    
    # Priority 1: Check display_text for explicit plug/perforation keywords
    # This overrides event_id since display_text is more reliable
    if "plug" in display_lower and "set" in display_lower:
        if "surface" in display_lower or "circulate" in display_lower:
            return "set_surface_plug"
        elif "intermediate" in display_lower or "cement" in display_lower:
            # Could be intermediate plug or just "set cement plug"
            if "squeeze" in display_lower or "squeezed" in display_lower:
                return "squeeze"
            return "set_cement_plug"
    
    if "perforat" in display_lower:
        return "perforate"
    
    if "tag" in display_lower and "toc" in display_lower:
        return "tag_toc"
    
    if "tag" in display_lower and ("cibp" in display_lower or "bridge" in display_lower):
        return "tag_bridge_plug"
    
    if "pressure" in display_lower:
        return "pressure_up"
    
    if "circulation" in display_lower or "circulate" in display_lower:
        return "broke_circulation"
    
    # Priority 2: Try direct event_id mapping
    if event_id is not None and event_id in PNA_EVENT_ID_MAP:
        return PNA_EVENT_ID_MAP[event_id]
    
    # Try event_type field (primary source from pnaexchange)
    if event_type:
        event_type_lower = event_type.lower()
        
        if "set intermediate plug" in event_type_lower or "set cement plug" in event_type_lower:
            return "set_cement_plug"
        if "set surface plug" in event_type_lower:
            return "set_surface_plug"
        if "squeezed" in event_type_lower or "squeeze" in event_type_lower:
            return "squeeze"
        if "broke circulation" in event_type_lower:
            return "broke_circulation"
        if "pressure up" in event_type_lower:
            return "pressure_up"
        if "set cibp" in event_type_lower or "set bridge plug" in event_type_lower:
            return "set_bridge_plug"
        if "tag cibp" in event_type_lower or "tag bridge plug" in event_type_lower:
            return "tag_bridge_plug"
        if "cut casing" in event_type_lower:
            return "cut_casing"
        if "tag toc" in event_type_lower or "tagged toc" in event_type_lower:
            return "tag_toc"
        if "tag top of cement" in event_type_lower:
            return "tag_toc"
        if "perforation" in event_type_lower or "perforated" in event_type_lower:
            return "perforate"
        if "rrc approval" in event_type_lower:
            return "rrc_approval"
    
    # Fallback: parse from display_text
    display_lower = display_text.lower()
    
    if "set intermediate plug" in display_lower or "set cement plug" in display_lower:
        return "set_cement_plug"
    if "set surface plug" in display_lower:
        return "set_surface_plug"
    if "squeezed" in display_lower or "squeeze" in display_lower:
        return "squeeze"
    if "broke circulation" in display_lower:
        return "broke_circulation"
    if "pressure up" in display_lower:
        return "pressure_up"
    if "set cibp" in display_lower or "set bridge plug" in display_lower:
        return "set_bridge_plug"
    if "tag cibp" in display_lower or "tag bridge plug" in display_lower:
        return "tag_bridge_plug"
    if "cut casing" in display_lower:
        return "cut_casing"
    if "tag toc" in display_lower or "tagged toc" in display_lower:
        return "tag_toc"
    if "perforation" in display_lower or "perforated" in display_lower:
        return "perforate"
    if "rrc approval" in display_lower:
        return "rrc_approval"
    
    # Final fallback
    logger.warning(f"Unknown event: id={event_id}, event_type='{event_type}', display='{display_text}'")
    return display_text.lower().replace(" ", "_")


def extract_cement_plug_depths(
    event_id: Optional[int],
    input_values: Dict[str, Any],
    event_type: str
) -> tuple[Optional[float], Optional[float]]:
    """
    Extract depths for cement plug events.
    
    For W-3 form Column 20 & 21:
    - Column 20 "Depth to Bottom of Tubing or DP": where the cement plug interval starts
    - Column 21 "Depth to Top of Plug": initially the design depth, updated by Tag TOC events
    
    From pnaexchange events "From X' to Y'" format:
    - X = *_4_* = bottom of DP where cement starts filling
    - Y = *_5_* = the target/design top of cement before being squeezed
    
    The actual measured TOC comes later from "Tag TOC" events.
    
    Template mapping:
    - Set Intermediate Plug (id=4): *_4_* = from, *_5_* = to
    - Set Surface Plug (id=3): *_4_* = from, *_5_* = to
    - Squeeze (id=7): *_4_* = from, *_5_* = to
    
    Returns:
        (depth_top_ft, depth_bottom_ft) where:
        - depth_top_ft: Bottom of DP = W-3 Column 20
        - depth_bottom_ft: Design top of cement = will be updated by measured TOC later
    """
    depth_top = None
    depth_bottom = None
    
    # Try numeric event_id first
    if event_id is not None and event_id in (3, 4, 7):  # All plug types
        # *_4_* is FROM (bottom of DP)
        depth_top = parse_numeric(input_values.get("4"))
        # *_5_* is TO (design top of cement before squeeze)
        depth_bottom = parse_numeric(input_values.get("5"))
    
    # Fallback: use event_type to determine extraction method
    elif event_type in ("set_cement_plug", "set_surface_plug", "squeeze"):
        # All these types use positions 4 and 5 for depths
        # *_4_* is FROM (bottom of DP)
        depth_top = parse_numeric(input_values.get("4"))
        # *_5_* is TO (design top of cement before squeeze)
        depth_bottom = parse_numeric(input_values.get("5"))
    
    return depth_top, depth_bottom


def extract_cement_class_and_sacks(
    event_id: Optional[int],
    event_type: str,
    input_values: Dict[str, Any]
) -> tuple[Optional[str], Optional[float]]:
    """
    Extract cement class and sacks for cement plug events.
    
    - Cement class: usually single letter (A, B, C, G, etc.)
    - Sacks: quantity of cement
    
    Template placeholder conventions:
    - Set Intermediate Plug (id=4): *_3_* = class, *_2_* = sacks, *_6_* = volume displaced (bbl)
    - Set Surface Plug (id=3): *_2_* = sacks, *_3_* = class, *_6_* = volume displaced (bbl)
    - Squeeze (id=7): *_2_* = sacks, *_3_* = class, *_6_* = volume displaced (bbl)
    """
    cement_class = None
    sacks = None
    
    # Try numeric event_id first
    if event_id == 4:  # Set Intermediate Plug
        # *_3_* is cement class
        cement_class_raw = input_values.get("3")
        if cement_class_raw:
            cement_class = str(cement_class_raw).strip().upper()
        # Sacks from *_2_*
        sacks = parse_numeric(input_values.get("2"))
    
    elif event_id == 3:  # Set Surface Plug
        # *_2_* = sacks, *_3_* = class
        sacks = parse_numeric(input_values.get("2"))
        cement_class_raw = input_values.get("3")
        if cement_class_raw:
            cement_class = str(cement_class_raw).strip().upper()
    
    elif event_id == 7:  # Squeeze
        # *_2_* = sacks, *_3_* = class
        sacks = parse_numeric(input_values.get("2"))
        cement_class_raw = input_values.get("3")
        if cement_class_raw:
            cement_class = str(cement_class_raw).strip().upper()
    
    # Fallback: use event_type to determine extraction method
    elif event_type == "set_cement_plug":  # Set Intermediate Plug template
        # *_3_* is cement class, *_2_* is sacks
        cement_class_raw = input_values.get("3")
        if cement_class_raw:
            cement_class = str(cement_class_raw).strip().upper()
        sacks = parse_numeric(input_values.get("2"))
    
    elif event_type in ("set_surface_plug", "squeeze"):  # Set Surface Plug / Squeeze templates
        # *_2_* = sacks, *_3_* = class
        sacks = parse_numeric(input_values.get("2"))
        cement_class_raw = input_values.get("3")
        if cement_class_raw:
            cement_class = str(cement_class_raw).strip().upper()
    
    return cement_class, sacks


def extract_volume_displaced(
    event_id: Optional[int],
    event_type: str,
    input_values: Dict[str, Any]
) -> Optional[float]:
    """
    Extract volume displaced (slurry volume in barrels) for cement plug events.
    
    Template placeholder conventions:
    - Set Intermediate Plug (id=4): *_6_* = volume displaced (bbl)
    - Set Surface Plug (id=3): *_6_* = volume displaced (bbl)
    - Squeeze (id=7): *_6_* = volume displaced (bbl)
    """
    # Try numeric event_id first
    if event_id is not None and event_id in (3, 4, 7):
        return parse_numeric(input_values.get("6"))
    
    # Fallback: use event_type to determine extraction method
    if event_type in ("set_cement_plug", "set_surface_plug", "squeeze"):
        return parse_numeric(input_values.get("6"))
    
    return None


def extract_plug_number(
    event_id: Optional[int],
    event_type: str,
    input_values: Dict[str, Any]
) -> Optional[int]:
    """
    Extract plug number from event inputs.
    
    For most plug events, *_1_* is the plug number.
    """
    # Try numeric event_id first
    if event_id is not None and event_id in (3, 4, 7):  # Plug events have *_1_* as plug number
        plug_num_raw = input_values.get("1")
        if plug_num_raw is not None:
            plug_num = parse_numeric(plug_num_raw)
            if plug_num is not None:
                return int(plug_num)
    
    # Fallback: use event_type to determine extraction method
    if event_type in ("set_cement_plug", "set_surface_plug", "squeeze"):
        plug_num_raw = input_values.get("1")
        if plug_num_raw is not None:
            plug_num = parse_numeric(plug_num_raw)
            if plug_num is not None:
                return int(plug_num)
    
    return None


def extract_tag_depth(
    event_id: Optional[int],
    event_type: str,
    input_values: Dict[str, Any]
) -> Optional[float]:
    """
    Extract tagged depth from Tag TOC or similar events.
    
    - Tag TOC (id=8): *_2_* is depth
    - Tagged TOC (id=5): *_1_* is depth
    - Tag CIBP (id=11): *_2_* is depth
    
    For event_type fallback:
    - tag_toc: Try *_2_* first, then *_1_*
    - tag_bridge_plug: Try *_2_* first, then *_1_*
    """
    # Try numeric event_id first
    if event_id == 8:  # Tag TOC: *_2_*
        return parse_numeric(input_values.get("2"))
    elif event_id == 5:  # Tagged TOC: *_1_*
        return parse_numeric(input_values.get("1"))
    elif event_id == 11:  # Tag CIBP: *_2_*
        return parse_numeric(input_values.get("2"))
    
    # Fallback: use event_type to determine extraction method
    if event_type == "tag_toc":
        # Try position 2 first (standard Tag TOC position), then position 1 (fallback)
        depth = parse_numeric(input_values.get("2"))
        if depth is not None:
            return depth
        return parse_numeric(input_values.get("1"))
    
    if event_type == "tag_bridge_plug":
        # Try position 2 first, then position 1
        depth = parse_numeric(input_values.get("2"))
        if depth is not None:
            return depth
        return parse_numeric(input_values.get("1"))
    
    return None


def map_pna_event_to_w3event(pna_event: Dict[str, Any]) -> W3Event:
    """
    Map a raw pnaexchange event dictionary to a W3Event dataclass instance.
    
    Handles both old format (event_id numeric) and new format (event_type text).
    
    Args:
        pna_event: Dictionary with structure:
            {
                "event_id": 4,  # Optional numeric ID
                "event_type": "Set Intermediate Plug",  # Text event type (new format)
                "display_text": "Set Intermediate Plug",
                "form_template_text": "Plug *_1_* ...",
                "input_values": {"1": "5", "2": "6997", ...},
                "transformation_rules": {"jump_plugs_to_next_casing": false},
                "date": "2025-01-15",
                "start_time": "09:30:00",
                "end_time": "10:15:00",
                "work_assignment_id": 12345,
                "dwr_id": 67890
            }
    
    Returns:
        W3Event instance
    """
    event_id = pna_event.get("event_id")
    event_type_field = pna_event.get("event_type", "")  # NEW: Extract event_type text field
    display_text = pna_event.get("display_text", "unknown")
    input_values = pna_event.get("input_values", {})
    transformation_rules = pna_event.get("transformation_rules", {})
    
    # Normalize event type with new event_type parameter
    event_type = normalize_event_type(event_id, display_text, event_type_field)
    
    # Parse dates and times
    try:
        event_date = date.fromisoformat(pna_event["date"]) if "date" in pna_event else date.today()
    except (ValueError, TypeError):
        event_date = date.today()
    
    start_time = parse_time(pna_event.get("start_time"))
    end_time = parse_time(pna_event.get("end_time"))
    
    # Initialize depth values
    depth_top_ft = None
    depth_bottom_ft = None
    perf_depth_ft = None
    tagged_depth_ft = None
    plug_number = None
    cement_class = None
    sacks = None
    
    # Extract event-specific values
    if event_type in ("set_cement_plug", "set_surface_plug", "squeeze"):
        depth_top_ft, depth_bottom_ft = extract_cement_plug_depths(event_id, input_values, event_type)
        cement_class, sacks = extract_cement_class_and_sacks(event_id, event_type, input_values)
        plug_number = extract_plug_number(event_id, event_type, input_values)
        # Extract slurry volume displaced (bbl)
        volume_bbl = extract_volume_displaced(event_id, event_type, input_values)
    
    elif event_type == "set_bridge_plug":
        # *_2_* is depth
        depth_bottom_ft = parse_numeric(input_values.get("2"))
    
    elif event_type == "cut_casing":
        # *_1_* is cut depth
        depth_bottom_ft = parse_numeric(input_values.get("1"))
    
    elif event_type in ("tag_toc", "tag_bridge_plug"):
        # Extract tagged depth
        tagged_depth_ft = extract_tag_depth(event_id, event_type, input_values)
    
    elif event_type == "perforate":
        # *_1_* is perforation depth
        perf_depth_ft = parse_numeric(input_values.get("1"))
    
    # Extract jump rule (critical for casing engine)
    jump_to_next_casing = transformation_rules.get("jump_plugs_to_next_casing", False)
    if not jump_to_next_casing and event_type == "cut_casing":
        jump_to_next_casing = True  # Force for cut casing events
    
    # Build the W3Event (volume_bbl may be set above for cement events)
    if 'volume_bbl' not in locals():
        volume_bbl = None
    
    # Use event_detail if available, otherwise fall back to display_text
    event_detail = pna_event.get('event_detail', '')
    raw_detail = event_detail if event_detail else display_text
    
    w3_event = W3Event(
        event_type=event_type,
        date=event_date,
        start_time=start_time,
        end_time=end_time,
        depth_top_ft=depth_top_ft,
        depth_bottom_ft=depth_bottom_ft,
        perf_depth_ft=perf_depth_ft,
        tagged_depth_ft=tagged_depth_ft,
        plug_number=plug_number,
        cement_class=cement_class,
        sacks=sacks,
        volume_bbl=volume_bbl,  # Slurry volume in barrels from "Displaced with" field
        pressure_psi=None,  # Not provided by pnaexchange
        raw_event_detail=raw_detail,  # Use actual event_detail from pnaexchange
        work_assignment_id=pna_event.get("work_assignment_id"),
        dwr_id=pna_event.get("dwr_id"),
        jump_to_next_casing=jump_to_next_casing,
        raw_input_values=input_values,
        raw_transformation_rules=transformation_rules,
    )
    
    logger.debug(
        f"✅ Mapped pna_event (id={event_id}, type_field='{event_type_field}') -> W3Event: "
        f"{w3_event.event_type} at depths {w3_event.depth_top_ft}-{w3_event.depth_bottom_ft} ft, "
        f"plug#{plug_number}, class={cement_class}, sacks={sacks}"
    )
    
    return w3_event


def map_pna_events_to_w3events(pna_events: List[Dict[str, Any]]) -> List[W3Event]:
    """
    Map a list of raw pnaexchange event dictionaries to a list of W3Event instances.
    
    Args:
        pna_events: List of event dictionaries from pnaexchange
    
    Returns:
        List of W3Event instances
    """
    w3_events = []
    
    for i, pna_event in enumerate(pna_events):
        try:
            event_type_field = pna_event.get("event_type", "")
            display_text = pna_event.get("display_text", "")
            logger.debug(f"📍 Processing event[{i}]: event_type='{event_type_field}', display_text='{display_text}'")
            
            w3_event = map_pna_event_to_w3event(pna_event)
            w3_events.append(w3_event)
            
            logger.debug(f"   ✅ Mapped to W3Event: {w3_event.event_type}, plug#{w3_event.plug_number}")
        except Exception as e:
            logger.error(f"❌ Error mapping pna_event[{i}]: {e}", exc_info=True)
            # Continue processing other events
            continue
    
    logger.info(f"✅ Mapped {len(pna_events)} pnaexchange events to {len(w3_events)} W3Events")
    return w3_events


def validate_event_inputs(event_id: int, input_values: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Validate that required inputs are present for the event type.
    
    Args:
        event_id: pnaexchange event ID
        input_values: Dictionary of input values
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    required_count = EVENT_INPUT_REQUIREMENTS.get(event_id, 0)
    
    if required_count == 0:
        return True, None
    
    # Count non-null inputs
    provided = sum(1 for v in input_values.values() if v is not None)
    
    if provided < required_count:
        return False, f"Event {event_id} requires {required_count} inputs, got {provided}"
    
    return True, None
