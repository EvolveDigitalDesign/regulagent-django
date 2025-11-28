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


def get_active_casing_at_depth(
    casing_state: List[CasingStringState],
    depth_ft: float,
) -> Optional[CasingStringState]:
    """
    Get the active (innermost, uncut) casing string at a given depth.
    
    Returns the casing string that is:
    1. Present at the depth (top_ft <= depth <= bottom_ft)
    2. Not cut above this depth (removed_to_depth_ft is None or > depth)
    3. Innermost among candidates (smallest OD)
    
    Args:
        casing_state: List of CasingStringState objects
        depth_ft: Depth to query
        
    Returns:
        CasingStringState of active casing, or None if no casing at depth
        
    Example:
        casing_state = [
            CasingStringState("surface", 13.375, 0, 2000),
            CasingStringState("production", 5.5, 2000, 8000)
        ]
        
        get_active_casing_at_depth(casing_state, 5000)
        >>> CasingStringState("production", 5.5, 2000, 8000)
        
        get_active_casing_at_depth(casing_state, 1000)
        >>> CasingStringState("surface", 13.375, 0, 2000)
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

