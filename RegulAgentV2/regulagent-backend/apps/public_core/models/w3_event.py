"""
W-3 Form Generation Data Models

Dataclasses for representing pnaexchange events and W-3 form output.
These are NOT Django ORM models - they're plain Python dataclasses for 
type safety and structured data handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List
from datetime import date, time


@dataclass
class CasingStringState:
    """
    Represents a casing string with optional removal/cut depth.
    
    Used in dynamic casing state tracking to determine which casing
    is "active" at any given depth during plugging operations.
    
    Attributes:
        name: Casing type ("surface", "intermediate", "production", "liner")
        od_in: Outer diameter in inches (e.g., 5.5, 9.625, 13.375)
        top_ft: Top depth of casing in feet (usually 0)
        bottom_ft: Bottom/shoe depth in feet (where casing ends)
        removed_to_depth_ft: If casing was cut, depth of removal (None = not cut)
    """
    name: str
    od_in: float
    top_ft: float
    bottom_ft: float
    removed_to_depth_ft: Optional[float] = None
    
    def is_present_at_depth(self, depth_ft: float) -> bool:
        """Check if this casing string is present (not cut) at given depth."""
        # Check if depth is within casing range
        if not (self.top_ft <= depth_ft <= self.bottom_ft):
            return False
        
        # Check if casing has been cut above this depth
        if self.removed_to_depth_ft is not None and depth_ft <= self.removed_to_depth_ft:
            return False
        
        return True


@dataclass
class W3Event:
    """
    Normalized pnaexchange event for W-3 form generation.
    
    This dataclass normalizes data from pnaexchange's input_values dict
    into structured, typed fields. pnaexchange sends data as:
    {
        "input_values": {
            "1": plug_number,
            "3": cement_class,
            "4": depth_bottom,
            "5": depth_top,
            "6": sacks,
            "7": pressure,
            ...
        }
    }
    
    This class converts that into strongly-typed fields.
    """
    # Event identification
    event_type: str                           # "Set Surface Plug", "Squeeze", "Perforate", etc.
    date: date                                # Work date
    start_time: Optional[time] = None         # Work start time
    end_time: Optional[time] = None           # Work end time
    
    # Depths (in feet)
    depth_top_ft: Optional[float] = None      # Top of interval (TOC or calculation)
    depth_bottom_ft: Optional[float] = None   # Bottom of interval
    perf_depth_ft: Optional[float] = None     # Perforation depth (if different from interval)
    tagged_depth_ft: Optional[float] = None   # Measured TOC if tagged
    plug_number: Optional[int] = None         # Plug sequence number
    
    # Materials
    cement_class: Optional[str] = None        # "A", "B", "C", "G", "H", etc. (uppercase)
    sacks: Optional[float] = None             # Cement sacks
    volume_bbl: Optional[float] = None        # Cement volume in barrels (calculated if needed)
    pressure_psi: Optional[float] = None      # Squeeze pressure in PSI
    
    # Tracking
    raw_event_detail: str = ""                # Original event description text
    work_assignment_id: int = 0               # pnaexchange work assignment ID
    dwr_id: int = 0                           # Daily work report ID
    
    # Casing state
    jump_to_next_casing: bool = False         # Signal to cut/remove inner casing
    casing_string: Optional[str] = None       # Active casing at event depth (filled by engine)


@dataclass
class Plug:
    """
    Group of W3Events forming a single plugging operation.
    
    Multiple events (e.g., perforate + squeeze + tag) may belong to one plug.
    This class groups them together with a plug number for RRC W-3 reporting.
    
    Attributes:
        plug_number: Sequential plug number (1, 2, 3, ...)
        events: List of W3Event objects for this plug
    """
    plug_number: int
    events: List[W3Event] = field(default_factory=list)
    
    @property
    def earliest_date(self) -> Optional[date]:
        """Get earliest date from plug events."""
        if not self.events:
            return None
        return min(e.date for e in self.events)
    
    @property
    def latest_date(self) -> Optional[date]:
        """Get latest date from plug events."""
        if not self.events:
            return None
        return max(e.date for e in self.events)
    
    @property
    def deepest_depth(self) -> Optional[float]:
        """Get deepest depth from plug events."""
        depths = []
        for e in self.events:
            if e.depth_bottom_ft is not None:
                depths.append(e.depth_bottom_ft)
            if e.depth_top_ft is not None:
                depths.append(e.depth_top_ft)
        return max(depths) if depths else None
    
    @property
    def shallowest_depth(self) -> Optional[float]:
        """Get shallowest depth from plug events."""
        depths = []
        for e in self.events:
            if e.depth_bottom_ft is not None:
                depths.append(e.depth_bottom_ft)
            if e.depth_top_ft is not None:
                depths.append(e.depth_top_ft)
        return min(depths) if depths else None


@dataclass
class W3Form:
    """
    Final W-3 form output - ready for RRC submission.
    
    This represents the complete W-3 form with all required sections:
    - Header (well info, API, operator, RRC district, county)
    - Plugging proposal (plugs table)
    - Casing record (with removal depths)
    - Record of perforated intervals / open hole
    - Depth of usable quality water (DUQW)
    - Remarks
    
    This is the return value from W3Builder.build_w3_form().
    """
    header: dict                          # {api, well_name, operator, rrc_district, county, ...}
    plugs: List[dict]                     # RRC plug rows with type, depths, sacks, cement, etc.
    casing_record: List[dict]             # Casing strings with sizes, depths, removal info
    perforations: List[dict]              # Perforation intervals with status (open, squeezed, plugged)
    duqw: dict                            # {depth_ft, formation, determination_method}
    remarks: str = ""                     # Concatenated event details/notes
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "header": self.header,
            "plugs": self.plugs,
            "casing_record": self.casing_record,
            "perforations": self.perforations,
            "duqw": self.duqw,
            "remarks": self.remarks,
        }

