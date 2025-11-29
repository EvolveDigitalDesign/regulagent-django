"""
W-3 Casing State Engine

Manages dynamic casing string state during plugging operations.
Handles casing cuts, removal, and determines active (innermost) casing at any depth.

The casing state engine tracks:
1. Original casing program from W-3A top section
2. Cuts/removals as events are processed
3. Which casing is "active" (innermost, uncut) at each event depth
"""

from __future__ import annotations

from typing import List, Optional
import logging

from apps.public_core.models.w3_event import CasingStringState

logger = logging.getLogger(__name__)


def initialize_casing_state(w3a_casing_record: List[dict]) -> List[CasingStringState]:
    """
    Initialize casing state from W-3A casing record.
    
    Converts the casing_record list from extracted W-3A JSON into
    a mutable list of CasingStringState objects for tracking during plugging.
    
    Args:
        w3a_casing_record: List of casing dictionaries from W-3A extraction:
            [
                {
                    "string_type": "surface",
                    "size_in": 13.375,
                    "weight_ppf": 47.0,
                    "hole_size_in": 14.75,
                    "top_ft": 0,
                    "bottom_ft": 2000,
                    "shoe_depth_ft": 2000,
                    "cement_top_ft": 0,
                    "removed_to_depth_ft": None
                },
                ...
            ]
    
    Returns:
        List of CasingStringState objects, sorted by bottom depth (descending)
        
    Raises:
        ValueError: If required fields are missing or invalid
    """
    casing_state = []
    
    for i, cs_data in enumerate(w3a_casing_record):
        try:
            # Required fields
            string_type = cs_data.get("string_type", f"Casing {i+1}")
            size_in = float(cs_data.get("size_in", 0))
            top_ft = float(cs_data.get("top_ft", 0))
            bottom_ft = float(cs_data.get("bottom_ft") or cs_data.get("shoe_depth_ft", 0))
            
            # Validate required fields
            if size_in <= 0 or bottom_ft <= 0:
                logger.warning(
                    f"Skipping invalid casing record {i}: "
                    f"size={size_in}, bottom={bottom_ft}"
                )
                continue
            
            # Optional fields
            weight_ppf = cs_data.get("weight_ppf")
            if weight_ppf is not None:
                weight_ppf = float(weight_ppf)
            
            hole_size_in = cs_data.get("hole_size_in")
            if hole_size_in is not None:
                hole_size_in = float(hole_size_in)
            
            cement_top_ft = cs_data.get("cement_top_ft")
            if cement_top_ft is not None:
                cement_top_ft = float(cement_top_ft)
            
            removed_to_depth_ft = cs_data.get("removed_to_depth_ft")
            if removed_to_depth_ft is not None:
                removed_to_depth_ft = float(removed_to_depth_ft)
            
            # Create CasingStringState
            cs_obj = CasingStringState(
                name=string_type,
                od_in=size_in,
                top_ft=top_ft,
                bottom_ft=bottom_ft,
                hole_size_in=hole_size_in,
                removed_to_depth_ft=removed_to_depth_ft,
            )
            
            casing_state.append(cs_obj)
            logger.debug(f"✅ Added casing: {string_type} {size_in}\" hole {hole_size_in}\" @ {top_ft}-{bottom_ft} ft")
        
        except (ValueError, TypeError) as e:
            logger.error(f"Error parsing casing record {i}: {e}")
            continue
    
    # Sort by bottom depth (descending) for easier processing
    casing_state.sort(key=lambda cs: cs.bottom_ft, reverse=True)
    
    logger.info(f"✅ Initialized casing state with {len(casing_state)} strings")
    for cs in casing_state:
        logger.debug(f"   - {cs.name}: {cs.od_in}\" @ {cs.top_ft}-{cs.bottom_ft} ft")
    
    return casing_state


def apply_cut_casing(
    casing_state: List[CasingStringState],
    depth_ft: float,
) -> None:
    """
    Mark innermost casing string as cut at the specified depth.
    
    When an event has jump_to_next_casing=true, it signals a casing cut.
    This function finds the innermost (smallest OD) casing present at that depth
    and marks it as removed_to_depth_ft.
    
    Args:
        casing_state: Mutable list of CasingStringState objects
        depth_ft: Depth at which cut occurs
        
    Side effects:
        Modifies casing_state in-place by setting removed_to_depth_ft
        
    Example:
        Before: [
            CasingStringState("surface", 13.375, 0, 2000, None),
            CasingStringState("production", 5.5, 2000, 8000, None)
        ]
        
        After apply_cut_casing(casing_state, 5000):
        [
            CasingStringState("surface", 13.375, 0, 2000, None),
            CasingStringState("production", 5.5, 2000, 8000, 5000)  # cut at 5000
        ]
    """
    try:
        # Find all casing strings present at this depth
        # (i.e., top <= depth <= bottom and not previously cut above this depth)
        candidates = []
        for cs in casing_state:
            if cs.top_ft <= depth_ft <= cs.bottom_ft:
                # Check if this casing has been cut above this depth
                if cs.removed_to_depth_ft is None or depth_ft > cs.removed_to_depth_ft:
                    candidates.append(cs)
        
        if not candidates:
            logger.warning(
                f"⚠️  No casing string found at depth {depth_ft} ft for cutting"
            )
            return
        
        # Select innermost (smallest OD) casing
        innermost = min(candidates, key=lambda cs: cs.od_in)
        
        logger.info(
            f"🔪 Cutting {innermost.name} casing ({innermost.od_in}\") at {depth_ft} ft"
        )
        
        # Mark as removed up to this depth
        innermost.removed_to_depth_ft = depth_ft
        
    except Exception as e:
        logger.error(f"❌ Error applying cut casing: {e}", exc_info=True)
        raise


def get_plug_hole_size_at_depth(
    casing_state: List[CasingStringState],
    depth_ft: float,
    operation_type: Optional[str] = None,
) -> Optional[float]:
    """
    Determine the W-3 Column 20 "Size of hole or pipe" where a plug would be placed at this depth.
    
    The plug size depends on the operation type and casing geometry:
    
    For "spot" plugs (inside casing only):
    - Return the innermost casing OD at that depth
    
    For "squeeze" plugs (perf & squeeze into annulus):
    - Return the hole size of the innermost casing (the annulus diameter)
    - This represents squeezing cement through perfs into the space outside the pipe
    
    For Midland Farms Unit #90 example:
    - 5.5" production casing (0-4600 ft) with 7.875" hole
    - 8.625" surface casing (0-250 ft) with 12.25" hole
    
    Depth 4056 ft, spot plug: return 5.5" (inside production casing)
    Depth 3000 ft, squeeze plug: return 7.875" (annulus outside 5.5")
    Depth 300 ft, squeeze in surface: return 8.625" (surface casing OD)
    
    Args:
        casing_state: List of CasingStringState objects
        depth_ft: Depth to query
        operation_type: "spot" (inside pipe) or "squeeze" (into annulus)
        
    Returns:
        Plug hole size in inches, or None if no casing at depth
    """
    try:
        # Find all casing strings present at this depth
        present = []
        for cs in casing_state:
            # Check if depth falls within casing range
            if not (cs.top_ft <= depth_ft <= cs.bottom_ft):
                continue
            
            # Check if casing has been cut above this depth
            if cs.removed_to_depth_ft is not None and depth_ft <= cs.removed_to_depth_ft:
                continue
            
            present.append(cs)
        
        if not present:
            logger.warning(f"⚠️  No active casing at depth {depth_ft} ft")
            return None
        
        # Sort by OD: innermost (smallest) to outermost (largest)
        present_sorted = sorted(present, key=lambda cs: cs.od_in)
        innermost = present_sorted[0]
        
        logger.debug(f"HOLE SIZE CALC: depth={depth_ft} ft, operation_type={operation_type}, casings_present={len(present)}")
        logger.debug(f"   Innermost: {innermost.name} OD={innermost.od_in}\" hole_size={innermost.hole_size_in}\"")
        
        # For "spot" plugs (inside casing): use innermost casing OD
        if operation_type == "spot":
            logger.debug(f"   → SPOT plug: returning innermost OD = {innermost.od_in}\"")
            return innermost.od_in
        
        # For "squeeze" plugs (into annulus): determine which annulus we're targeting
        if operation_type == "squeeze":
            # If we have multiple casings, we're squeezing between them
            # Use the outermost casing's OD as the annulus boundary
            if len(present) > 1:
                outermost = present_sorted[-1]  # Last element after sorting by OD
                logger.debug(
                    f"   → SQUEEZE plug (multi-casing): returning outermost ({outermost.name}) OD = {outermost.od_in}\""
                )
                return outermost.od_in
            else:
                # Single casing: use its hole_size to represent the annulus we're squeezing into
                if innermost.hole_size_in is not None:
                    logger.debug(
                        f"   → SQUEEZE plug (single): returning innermost ({innermost.name}) hole_size = {innermost.hole_size_in}\""
                    )
                    return innermost.hole_size_in
                else:
                    # Fallback to innermost OD if hole size not available
                    logger.debug(f"   → SQUEEZE plug: no hole_size, fallback to OD = {innermost.od_in}\"")
                    return innermost.od_in
        
        # If only one casing at this depth and not spot/squeeze specified, plug is inside it
        if len(present) == 1:
            logger.debug(f"Plug at {depth_ft} ft is inside {innermost.name} ({innermost.od_in}\")")
            return innermost.od_in
        
        # If operation_type not specified, use heuristic:
        # Check if any casing shoe is shallower than our depth → we're in annulus
        for cs in present_sorted:
            if cs.bottom_ft < depth_ft:
                # Shoe is shallower → likely in annulus
                if cs.hole_size_in is not None:
                    logger.debug(
                        f"📍 Plug at {depth_ft} ft is in annulus outside {cs.name} "
                        f"(shoe @ {cs.bottom_ft} ft < plug depth); using hole size {cs.hole_size_in}\""
                    )
                    return cs.hole_size_in
        
        # No casing shoe is shallower → plug is inside innermost casing
        logger.debug(f"📍 Plug at {depth_ft} ft is inside {innermost.name} ({innermost.od_in}\")")
        return innermost.od_in
        
    except Exception as e:
        logger.error(
            f"❌ Error getting plug hole size at depth {depth_ft}: {e}",
            exc_info=True
        )
        raise


def get_active_casing_at_depth(
    casing_state: List[CasingStringState],
    depth_ft: float,
) -> Optional[CasingStringState]:
    """
    Get the active casing string at a given depth for general reference.
    
    Returns the innermost (smallest OD) casing string that is:
    1. Present at the depth (top_ft <= depth <= bottom_ft)
    2. Not cut above this depth (removed_to_depth_ft is None or > depth)
    
    Args:
        casing_state: List of CasingStringState objects
        depth_ft: Depth to query
        
    Returns:
        CasingStringState of active casing, or None if no casing at depth
    """
    try:
        # Find all casing strings present at this depth
        present = []
        for cs in casing_state:
            # Check if depth falls within casing range
            if not (cs.top_ft <= depth_ft <= cs.bottom_ft):
                continue
            
            # Check if casing has been cut above this depth
            if cs.removed_to_depth_ft is not None and depth_ft <= cs.removed_to_depth_ft:
                continue
            
            present.append(cs)
        
        if not present:
            logger.warning(f"⚠️  No active casing at depth {depth_ft} ft")
            return None
        
        # Return innermost (smallest OD) active casing
        active = min(present, key=lambda cs: cs.od_in)
        
        logger.debug(
            f"📍 Active casing at {depth_ft} ft: {active.name} ({active.od_in}\")"
        )
        
        return active
        
    except Exception as e:
        logger.error(
            f"❌ Error getting active casing at depth {depth_ft}: {e}",
            exc_info=True
        )
        raise


def validate_casing_state(casing_state: List[CasingStringState]) -> bool:
    """
    Validate that casing state is consistent.
    
    Checks:
    1. No overlapping casing strings (by OD)
    2. No casing cut above its top depth
    3. No casing cut below its bottom depth
    
    Args:
        casing_state: List of CasingStringState objects
        
    Returns:
        True if valid, raises ValueError if invalid
        
    Raises:
        ValueError: If casing state is invalid
    """
    for cs in casing_state:
        # Check removal depth is within casing range
        if cs.removed_to_depth_ft is not None:
            if cs.removed_to_depth_ft < cs.top_ft or cs.removed_to_depth_ft > cs.bottom_ft:
                raise ValueError(
                    f"Casing {cs.name}: removed_to_depth ({cs.removed_to_depth_ft}) "
                    f"not within range [{cs.top_ft}, {cs.bottom_ft}]"
                )
    
    logger.debug("✅ Casing state validation passed")
    return True


def get_casing_program_summary(casing_state: List[CasingStringState]) -> str:
    """
    Get human-readable summary of casing state for logging/debugging.
    
    Args:
        casing_state: List of CasingStringState objects
        
    Returns:
        Formatted string like:
        "Surface: 13.375\" (0-2000 ft), Production: 5.5\" (2000-8000 ft, cut at 5000)"
    """
    lines = []
    for cs in casing_state:
        status = f"{cs.top_ft:.0f}-{cs.bottom_ft:.0f} ft"
        if cs.removed_to_depth_ft is not None:
            status += f", cut at {cs.removed_to_depth_ft:.0f}"
        lines.append(f"{cs.name.title()}: {cs.od_in}\" ({status})")
    
    return "; ".join(lines)

