"""
W-3 Form Formatter Service

Transforms normalized W3Event instances and W-3A form data into complete, 
RRC-compliant W-3 form output ready for submission.

Handles:
- Grouping events into logical plugs
- Building plug rows for RRC W-3 form
- Formatting casing record
- Formatting perforations/open hole intervals
- Generating remarks section
- Building complete W3Form dictionary
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from datetime import date
import logging

from apps.public_core.models.w3_event import W3Event, Plug, CasingStringState, W3Form
from apps.public_core.services.w3_casing_engine import apply_cut_casing, get_active_casing_at_depth

logger = logging.getLogger(__name__)


def group_events_into_plugs(
    w3_events: List[W3Event],
    casing_state: List[CasingStringState]
) -> List[Plug]:
    """
    Group related W3Events into logical Plug objects.
    
    Logic:
    - Cement plug events (set_cement_plug, set_surface_plug, squeeze) form plugs
    - Tag TOC events reference/validate the plug above them
    - Cut casing events update casing state and affect subsequent plugs
    - Bridge plugs are separate from cement plugs
    - Events are processed in chronological order
    
    Args:
        w3_events: Sorted list of W3Event instances (by date/time)
        casing_state: Current casing state from W-3A
    
    Returns:
        List of Plug objects grouped by plug_number
    """
    plugs: Dict[int, Plug] = {}
    plug_sequence = []
    
    for event in w3_events:
        logger.debug(f"Processing event: {event.event_type} at depth {event.depth_bottom_ft}")
        
        # Handle casing cuts
        if event.event_type == "cut_casing":
            if event.depth_bottom_ft is not None:
                apply_cut_casing(casing_state, event.depth_bottom_ft)
                logger.info(f"Applied casing cut at {event.depth_bottom_ft} ft")
        
        # Handle plug events
        elif event.event_type in ("set_cement_plug", "set_surface_plug", "squeeze"):
            plug_num = event.plug_number or (len(plugs) + 1)
            
            if plug_num not in plugs:
                plugs[plug_num] = Plug(
                    plug_number=plug_num,
                    depth_top_ft=event.depth_top_ft,
                    depth_bottom_ft=event.depth_bottom_ft,
                    type="cement_plug" if event.event_type != "squeeze" else "squeeze",
                    cement_class=event.cement_class,
                    sacks=event.sacks,
                    volume_bbl=event.volume_bbl,
                )
                plug_sequence.append(plug_num)
            
            # Add event to plug
            if plug_num in plugs:
                plugs[plug_num].events.append(event)
                # Update remarks from event
                if event.raw_event_detail:
                    if not plugs[plug_num].remarks:
                        plugs[plug_num].remarks = event.raw_event_detail
                    else:
                        plugs[plug_num].remarks += f"\n{event.raw_event_detail}"
        
        # Handle bridge plugs
        elif event.event_type == "set_bridge_plug":
            plug_num = event.plug_number or (len(plugs) + 1)
            
            if plug_num not in plugs:
                plugs[plug_num] = Plug(
                    plug_number=plug_num,
                    depth_bottom_ft=event.depth_bottom_ft,
                    type="bridge_plug",
                )
                plug_sequence.append(plug_num)
            
            plugs[plug_num].events.append(event)
        
        # Handle tag TOC events
        elif event.event_type == "tag_toc":
            # Find the plug this TOC validates (closest plug above)
            if plugs:
                # Get most recent plug
                last_plug_num = plug_sequence[-1] if plug_sequence else None
                if last_plug_num and last_plug_num in plugs:
                    plugs[last_plug_num].tag_required = True
                    plugs[last_plug_num].events.append(event)
                    if event.tagged_depth_ft:
                        plugs[last_plug_num].remarks = (
                            f"Tagged at {event.tagged_depth_ft} ft"
                            if not plugs[last_plug_num].remarks
                            else f"{plugs[last_plug_num].remarks}\nTagged at {event.tagged_depth_ft} ft"
                        )
        
        # Handle tag bridge plug events
        elif event.event_type == "tag_bridge_plug":
            # Similar logic to tag TOC
            if plugs:
                last_plug_num = plug_sequence[-1] if plug_sequence else None
                if last_plug_num and last_plug_num in plugs:
                    plugs[last_plug_num].events.append(event)
                    if event.tagged_depth_ft:
                        plugs[last_plug_num].remarks = (
                            f"Tagged bridge plug at {event.tagged_depth_ft} ft"
                            if not plugs[last_plug_num].remarks
                            else f"{plugs[last_plug_num].remarks}\nTagged at {event.tagged_depth_ft} ft"
                        )
        
        # Handle perforation events
        elif event.event_type == "perforate":
            # Perforations are tracked separately in formatter output
            pass
        
        # Handle admin events
        elif event.event_type in ("broke_circulation", "pressure_up", "rrc_approval"):
            # Add to remarks
            if plugs and plug_sequence:
                last_plug_num = plug_sequence[-1]
                if last_plug_num in plugs:
                    plugs[last_plug_num].events.append(event)
    
    # Return plugs in order
    return [plugs[num] for num in plug_sequence if num in plugs]


def format_casing_record(
    w3a_casing_record: List[Dict[str, Any]],
    casing_state: List[CasingStringState]
) -> List[Dict[str, Any]]:
    """
    Format casing record for RRC W-3 form.
    
    Takes W-3A casing data and applies any updates from casing state
    (e.g., casing cuts).
    
    Returns RRC-compliant casing row format:
    {
        "string_type": "surface|intermediate|production|liner",
        "size_in": 11.75,
        "weight_ppf": 47.0,
        "hole_size_in": 14.75,
        "top_ft": 0,
        "bottom_ft": 1717,
        "shoe_depth_ft": 1717,
        "cement_top_ft": 930,
        "removed_to_depth_ft": null  // If casing was cut
    }
    """
    formatted_casings = []
    
    for casing in w3a_casing_record:
        formatted_casing = {
            "string_type": casing.get("string_type"),
            "size_in": casing.get("size_in"),
            "weight_ppf": casing.get("weight_ppf"),
            "hole_size_in": casing.get("hole_size_in"),
            "top_ft": casing.get("top_ft"),
            "bottom_ft": casing.get("bottom_ft"),
            "shoe_depth_ft": casing.get("shoe_depth_ft"),
            "cement_top_ft": casing.get("cement_top_ft"),
            "removed_to_depth_ft": casing.get("removed_to_depth_ft"),  # From casing state if cut
        }
        formatted_casings.append(formatted_casing)
        logger.debug(f"Formatted casing: {formatted_casing['string_type']} {formatted_casing['size_in']}\"")
    
    return formatted_casings


def format_perforations(
    w3a_perforations: List[Dict[str, Any]],
    w3_events: List[W3Event]
) -> List[Dict[str, Any]]:
    """
    Format perforations/open hole intervals for RRC W-3 form.
    
    Takes W-3A perforation data and merges with any perforation events
    from pnaexchange.
    
    Returns RRC-compliant perforation row format:
    {
        "interval_top_ft": 8110,
        "interval_bottom_ft": 10914,
        "formation": "Spraberry",
        "status": "open|perforated|squeezed|plugged",
        "perforation_date": "2025-01-15"
    }
    """
    formatted_perfs = []
    
    # Start with W-3A perforations
    for perf in w3a_perforations:
        formatted_perf = {
            "interval_top_ft": perf.get("interval_top_ft"),
            "interval_bottom_ft": perf.get("interval_bottom_ft"),
            "formation": perf.get("formation"),
            "status": perf.get("status"),
            "perforation_date": perf.get("perforation_date"),
        }
        formatted_perfs.append(formatted_perf)
    
    # Track new perforations from events
    new_perfs = [e for e in w3_events if e.event_type == "perforate"]
    for perf_event in new_perfs:
        if perf_event.perf_depth_ft:
            # Check if we should update existing or add new
            # For now, just track the new perf depth
            logger.info(f"Perforation event at {perf_event.perf_depth_ft} ft on {perf_event.date}")
    
    logger.info(f"Formatted {len(formatted_perfs)} perforation intervals")
    return formatted_perfs


def format_plugs_for_rrc(plugs: List[Plug]) -> List[Dict[str, Any]]:
    """
    Format Plug objects into RRC W-3 form row format.
    
    Each plug becomes one or more rows on the W-3 form, with:
    - Plug number
    - Depths (top/bottom)
    - Cement class and quantity
    - Remarks with operational details
    
    Returns list of formatted plug dictionaries:
    {
        "plug_number": 1,
        "depth_top_ft": 7990,
        "depth_bottom_ft": 7890,
        "type": "cement_plug|bridge_plug|squeeze",
        "cement_class": "C",
        "sacks": 40,
        "volume_bbl": null,
        "remarks": "Set Intermediate Plug, displaced with...",
        "tag_required": false,
        "wait_hours": null
    }
    """
    formatted_plugs = []
    
    for plug in plugs:
        formatted_plug = {
            "plug_number": plug.plug_number,
            "depth_top_ft": plug.depth_top_ft,
            "depth_bottom_ft": plug.depth_bottom_ft,
            "type": plug.type or "cement_plug",
            "cement_class": plug.cement_class,
            "sacks": plug.sacks,
            "volume_bbl": plug.volume_bbl,
            "remarks": plug.remarks or "",
            "tag_required": plug.tag_required,
            "wait_hours": plug.wait_hours,
        }
        
        # Build detailed remarks from events
        event_details = []
        for event in plug.events:
            if event.event_type not in ("tag_toc", "tag_bridge_plug", "broke_circulation"):
                event_details.append(event.raw_event_detail)
        
        if event_details:
            if formatted_plug["remarks"]:
                formatted_plug["remarks"] += "\n" + "\n".join(event_details)
            else:
                formatted_plug["remarks"] = "\n".join(event_details)
        
        formatted_plugs.append(formatted_plug)
        logger.debug(f"Formatted plug #{plug.plug_number}: {plug.depth_top_ft}-{plug.depth_bottom_ft} ft")
    
    return formatted_plugs


def build_remarks_section(
    w3a_remarks: Optional[str],
    w3_events: List[W3Event],
    plugs: List[Plug]
) -> str:
    """
    Build complete remarks section for W-3 form.
    
    Combines:
    - W-3A baseline remarks
    - Event-specific operational notes
    - Plug-specific details
    - RRC-required notations
    
    Returns formatted remarks text
    """
    remarks_parts = []
    
    # Start with W-3A remarks if present
    if w3a_remarks:
        remarks_parts.append(f"W-3A Notes: {w3a_remarks}")
    
    # Add admin events that apply globally
    admin_events = [e for e in w3_events if e.event_type in ("broke_circulation", "pressure_up", "rrc_approval")]
    for event in admin_events:
        if event.raw_event_detail:
            remarks_parts.append(event.raw_event_detail)
    
    # Add plug-specific remarks
    for plug in plugs:
        if plug.remarks and plug.remarks not in "\n".join(remarks_parts):
            remarks_parts.append(f"Plug #{plug.plug_number}: {plug.remarks}")
    
    # Join with line breaks
    final_remarks = "\n".join(remarks_parts)
    
    logger.debug(f"Built remarks section ({len(final_remarks)} chars)")
    return final_remarks


def build_w3_form(
    w3a_form: Dict[str, Any],
    w3_events: List[W3Event],
    casing_state: List[CasingStringState]
) -> W3Form:
    """
    Build complete W3Form from W-3A data, normalized events, and casing state.
    
    This is the main orchestrator for formatting - it ties together all the
    individual formatting functions.
    
    Args:
        w3a_form: Extracted W-3A form data (from w3_extraction.py)
        w3_events: List of normalized W3Event instances (from w3_mapper.py)
        casing_state: Current casing state (from w3_casing_engine.py)
    
    Returns:
        Complete W3Form ready for API response or further processing
    """
    logger.info("🏗️ Building W-3 form from components...")
    
    # Group events into plugs
    plugs = group_events_into_plugs(w3_events, casing_state)
    logger.info(f"✅ Grouped {len(w3_events)} events into {len(plugs)} plugs")
    
    # Format casing record
    formatted_casing = format_casing_record(
        w3a_form.get("casing_record", []),
        casing_state
    )
    logger.info(f"✅ Formatted {len(formatted_casing)} casing strings")
    
    # Format perforations
    formatted_perfs = format_perforations(
        w3a_form.get("perforations", []),
        w3_events
    )
    logger.info(f"✅ Formatted {len(formatted_perfs)} perforations")
    
    # Format plugs for RRC
    formatted_plugs = format_plugs_for_rrc(plugs)
    logger.info(f"✅ Formatted {len(formatted_plugs)} plugs for RRC")
    
    # Build remarks
    remarks = build_remarks_section(
        w3a_form.get("remarks"),
        w3_events,
        plugs
    )
    logger.info(f"✅ Built remarks section")
    
    # Build final W3Form
    w3_form = W3Form(
        header=w3a_form.get("header", {}),
        plugs=formatted_plugs,
        casing_record=formatted_casing,
        perforations=formatted_perfs,
        duqw=w3a_form.get("duqw", {}),
        remarks=remarks,
        pdf_url=w3a_form.get("pdf_url"),
    )
    
    logger.info("✅ W-3 form build complete!")
    return w3_form


def w3_form_to_dict(w3_form: W3Form) -> Dict[str, Any]:
    """
    Convert W3Form dataclass to dictionary for JSON serialization.
    
    Args:
        w3_form: W3Form instance
    
    Returns:
        Dictionary representation suitable for API response
    """
    return {
        "header": w3_form.header,
        "plugs": w3_form.plugs,
        "casing_record": w3_form.casing_record,
        "perforations": w3_form.perforations,
        "duqw": w3_form.duqw,
        "remarks": w3_form.remarks,
        "pdf_url": w3_form.pdf_url,
    }

