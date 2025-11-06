"""
Pydantic schemas for OpenAI function calling tools.

Using structured outputs (strict=True) for 100% reliable tool calls.
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class GetPlanSnapshotTool(BaseModel):
    """
    Retrieve the current W3A plan snapshot with all details.
    
    Returns:
    - Full plan JSON with steps, materials, violations
    - Provenance (kernel version, overlays applied)
    - Well context (API, operator, field)
    """
    
    plan_id: str = Field(
        description="Plan ID to retrieve (e.g., '4200346118:combined')"
    )


class AnswerFactTool(BaseModel):
    """
    Answer factual questions about the well, formations, or plan.
    
    Queries structured data and documents to answer:
    - "What's the production casing depth?"
    - "Is there open hole behind the production casing?"
    - "What formations are targeted?"
    - "What's the UQW base?"
    
    Uses both structured ORM data and document vectors for hybrid search.
    """
    
    question: str = Field(
        description="Specific factual question about well, formations, or plan"
    )
    search_scope: Literal["well", "plan", "documents", "all"] = Field(
        description="Where to search for the answer (use 'all' to search everywhere)"
    )


class CombinePlugsTool(BaseModel):
    """
    Combine multiple adjacent formation plugs into a single long plug.
    
    This tool:
    1. Validates plugs can be safely combined (adjacent, compatible)
    2. Merges intervals
    3. Recalculates materials (sacks, volumes)
    4. Re-runs compliance validation
    5. Returns risk score and violations delta
    
    Requires explicit user confirmation if risk_score > tenant threshold.
    """
    
    step_ids: List[int] = Field(
        description="List of step IDs to combine (must be formation plugs)"
    )
    reason: str = Field(
        description="Explanation for why these plugs should be combined"
    )


class ReplaceCIBPTool(BaseModel):
    """
    Replace Cast Iron Bridge Plug + cap with a single long cement plug.
    
    This is a common optimization that:
    1. Removes CIBP step and cap step
    2. Adds long cement plug across producing interval
    3. Recalculates materials
    4. Validates compliance
    
    Often reduces cost and complexity while maintaining compliance.
    """
    
    interval: Literal["producing", "intermediate", "custom"] = Field(
        description="Which interval to target for CIBP replacement"
    )
    custom_top_depth: int = Field(
        description="Custom top depth in feet (required if interval='custom', otherwise ignored)"
    )
    custom_base_depth: int = Field(
        description="Custom base depth in feet (required if interval='custom', otherwise ignored)"
    )
    reason: str = Field(
        description="Explanation for why CIBP should be replaced"
    )


class RecalcMaterialsTool(BaseModel):
    """
    Recalculate materials and export totals after any plan modification.
    
    This tool:
    1. Recomputes sacks for all cement steps
    2. Updates volumes
    3. Recalculates totals
    4. Re-runs violation checks
    5. Updates export summary
    
    Should be called after any modification that changes intervals or materials.
    """
    
    revalidate_compliance: bool = Field(
        description="Whether to re-run full compliance validation (usually true)"
    )


class ChangePlugTypeTool(BaseModel):
    """
    Change the type of one or more plugs in the plan.
    
    Supported conversions:
    - cement_plug <-> perforate_and_squeeze_plug
    - cement_plug <-> open_hole (requires hole_d_in geometry)
    - formation_plug <-> perforate_and_squeeze_plug
    - Any cement-based plug can be converted
    
    This tool:
    1. Validates the conversion is technically feasible
    2. Updates plug type and required parameters
    3. Recalculates materials automatically
    4. Re-runs compliance validation
    5. Returns risk score and violations delta
    
    User can specify:
    - apply_to_all: Convert ALL cement-based plugs
    - step_ids: Convert specific steps by ID
    - formations: Convert plugs at specific formations (e.g., "Wolfcamp", "Canyon")
    
    Requires explicit user confirmation if risk_score > tenant threshold.
    """
    
    new_type: Literal["cement_plug", "perforate_and_squeeze_plug", "perf_and_circulate_to_surface", "open_hole_plug"] = Field(
        description="Target plug type to convert to. Use 'perf_and_circulate_to_surface' for annulus circulation to surface."
    )
    apply_to_all: bool = Field(
        default=False,
        description="If true, convert ALL eligible cement-based plugs to new type"
    )
    step_ids: Optional[List[int]] = Field(
        default=None,
        description="Specific step IDs to convert (ignored if apply_to_all=true)"
    )
    formations: Optional[List[str]] = Field(
        default=None,
        description="Convert plugs at specific formations (e.g., ['Wolfcamp', 'Canyon'], ignored if apply_to_all=true or step_ids provided)"
    )
    reason: str = Field(
        description="Explanation for why plug type should be changed"
    )


class RemoveStepsTool(BaseModel):
    """
    Remove (delete) specific steps from the plugging plan.
    
    This tool:
    1. Validates step IDs exist
    2. Checks guardrails (max_steps_removed per session)
    3. Warns if removing critical regulatory steps
    4. Removes steps from plan
    5. Renumbers remaining steps sequentially
    6. Recalculates materials_totals
    7. Creates new plan snapshot
    
    Common use cases:
    - Remove CIBP to replace with cement plug
    - Remove unnecessary formation plugs
    - Delete duplicate or redundant steps
    
    Requires explicit user confirmation if risk_score > tenant threshold.
    """
    
    step_ids: List[int] = Field(
        description="List of step IDs to remove from the plan (e.g., [2, 3] to remove steps 2 and 3)"
    )
    reason: str = Field(
        description="Explanation for why these steps should be removed"
    )


class AddPlugTool(BaseModel):
    """
    Add (insert) a new plug or step into the plugging plan.
    
    This tool:
    1. Validates plug type and depth parameters
    2. Creates step structure with proper fields
    3. Calculates materials (unless custom_sacks provided)
    4. Inserts at correct position (sorted by depth)
    5. Renumbers all steps sequentially
    6. Recalculates materials_totals
    7. Creates new plan snapshot
    
    Supported plug types:
    - cement_plug: Standard cement plug
    - perforate_and_squeeze_plug: Perf & squeeze with cap
    - bridge_plug: Mechanical device (CIBP)
    - cement_retainer: Mechanical device to hold cement
    - formation_top_plug: Formation isolation plug
    
    Common use cases:
    - Add cement retainer with custom sack count
    - Insert additional formation plug
    - Add bridge plug at specific depth
    
    Requires explicit user confirmation if risk_score > tenant threshold.
    """
    
    type: Literal["cement_plug", "perforate_and_squeeze_plug", "perf_and_circulate_to_surface", "bridge_plug", "cement_retainer", "formation_top_plug"] = Field(
        description="Type of plug to add. Use 'perf_and_circulate_to_surface' for annulus circulation to surface."
    )
    top_ft: float = Field(
        description=(
            "Top depth in feet MD. IMPORTANT: 'top' is the SHALLOWER end (smaller depth number). "
            "Example: for a plug from 6600-7500 ft, top_ft=6600 (closer to surface). "
            "For point devices (retainers/bridge plugs), use the device depth for both top and bottom. "
            "For cement plugs with custom_sacks, you can set top_ft=bottom_ft and the system will auto-calculate the interval."
        )
    )
    bottom_ft: float = Field(
        description=(
            "Bottom depth in feet MD. IMPORTANT: 'bottom' is the DEEPER end (larger depth number). "
            "Example: for a plug from 6600-7500 ft, bottom_ft=7500 (farther from surface). "
            "For point devices, use the same value as top_ft. "
            "For cement plugs with custom_sacks, you can set bottom_ft=top_ft and the system will auto-calculate based on sack count and geometry."
        )
    )
    custom_sacks: Optional[int] = Field(
        default=None,
        description=(
            "Custom sack count (if provided, materials will not be auto-calculated). "
            "Use when user specifies exact material quantity (e.g., '150 sacks'). "
            "When custom_sacks is provided with top_ft=bottom_ft, the system will automatically calculate bottom_ft based on wellbore geometry."
        )
    )
    cement_class: Optional[Literal["A", "C", "G", "H"]] = Field(
        default=None,
        description="Cement class (A=surface, C=normal, G=deep, H=high pressure). Defaults to H for deep plugs, C for shallow."
    )
    placement_reason: str = Field(
        description="Explanation for why this plug is being added and where it will be placed"
    )


class FormationPlugEntry(BaseModel):
    """Single formation entry for batch formation plug addition."""
    
    name: str = Field(
        description="Formation name (e.g., 'Bone Springs', 'Wolfcamp A', 'San Andres')"
    )
    top_ft: float = Field(
        description="Formation top depth in feet MD - the plug will be created as ±50 ft around this depth (top-50 to top+50)"
    )
    base_ft: Optional[float] = Field(
        default=None,
        description="LEAVE AS NULL for standard ±50 ft plugs. Only provide if user explicitly specifies a custom base depth different from top+50 ft."
    )


class AddFormationPlugsTool(BaseModel):
    """
    Add multiple formation top plugs in a single operation.
    
    ⚠️ IMPORTANT: Call this tool ONCE with ALL formations. Do NOT:
    - Call this tool multiple times for the same batch of formations
    - Call add_plug individually for formations after using this tool
    - Create duplicate entries in the formations array
    
    This tool:
    1. Validates formation data (names, depths)
    2. Creates formation_top_plug steps for each formation
    3. Uses standard ±50 ft interval around top (or custom base if provided)
    4. Calculates materials for each plug
    5. Inserts all plugs at correct positions (sorted by depth)
    6. Renumbers all steps sequentially
    7. Recalculates materials_totals
    8. Creates new plan snapshot
    
    Common use cases:
    - User provides formation list with tops from their own survey
    - RRC reviewer identifies missing formation plugs
    - Operator has updated formation tops from recent offset well
    - Adding formation plugs for county where no formation data exists in system
    
    Example - CORRECT usage (call once with all formations):
      formations: [
        {"name": "Bone Springs", "top_ft": 9320},
        {"name": "Bell Canyon", "top_ft": 5424},
        {"name": "Brushy Canyon", "top_ft": 7826}
      ]
    
    Each plug will be created as ±50 ft minimum around the top (e.g., 9270-9370 ft for Bone Springs).
    
    TEXAS 25-SACK MINIMUM: If a ±50 ft interval doesn't yield 25 sacks, the system will 
    automatically expand the interval symmetrically until it reaches 25 sacks (typically ~200 ft).
    
    Requires explicit user confirmation if risk_score > tenant threshold.
    """
    
    formations: List[FormationPlugEntry] = Field(
        description="Complete list of ALL formations to add as plugs in this single operation. Include ALL formations the user specified - do not call this tool multiple times."
    )
    placement_reason: str = Field(
        description="Explanation for why these formation plugs are being added"
    )


class OverrideMaterialsTool(BaseModel):
    """
    Override calculated materials with custom sack count for a specific step.
    
    This tool:
    1. Finds the specified step
    2. Updates sack count to custom value
    3. Flags step as 'materials_override' in details
    4. Recalculates materials_totals for entire plan
    5. Creates new plan snapshot
    
    Common use cases:
    - User has field data showing different sack needs
    - Accounting for poor hole conditions
    - Matching vendor quote or AFE
    - RRC reviewer requested specific quantity
    
    The override is clearly marked in the plan so reviewers know it's intentional.
    """
    
    step_id: int = Field(
        description="Step ID to override materials for"
    )
    sacks: int = Field(
        description="New sack count to use (must be positive)"
    )
    reason: str = Field(
        description="Explanation for why materials are being manually overridden"
    )


# Tool call response schemas (for structured outputs from model)

class ToolCallResponse(BaseModel):
    """Base response for all tool calls."""
    
    success: bool
    message: str
    data: Optional[dict] = None
    risk_score: Optional[float] = Field(
        default=None,
        description="Risk score 0.0-1.0 for modifications"
    )
    violations_delta: Optional[List[str]] = Field(
        default=None,
        description="New or resolved violations"
    )


# Helper to ensure schema is strict-mode compliant
def make_strict_schema(schema: dict) -> dict:
    """
    Add additionalProperties: false to all objects for OpenAI strict mode.
    Also ensures all properties are in the required array for strict validation.
    """
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            schema["additionalProperties"] = False
            # OpenAI strict mode requires all properties to be in the required array
            properties = schema.get("properties", {})
            if properties:
                # Ensure 'required' includes ALL property keys
                schema["required"] = list(properties.keys())
        for key, value in schema.items():
            if isinstance(value, dict):
                make_strict_schema(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        make_strict_schema(item)
    return schema


# OpenAI tool definitions (JSON schema format)

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_plan_snapshot",
            "description": "Retrieve the current W3A plan snapshot with all details including steps, materials, violations, and provenance",
            "strict": True,
            "parameters": make_strict_schema(GetPlanSnapshotTool.model_json_schema())
        }
    },
    {
        "type": "function",
        "function": {
            "name": "answer_fact",
            "description": "Answer factual questions about the well, formations, or plan by querying structured data and documents",
            "strict": True,
            "parameters": make_strict_schema(AnswerFactTool.model_json_schema())
        }
    },
    {
        "type": "function",
        "function": {
            "name": "combine_plugs",
            "description": "Combine multiple adjacent formation plugs into a single long plug with automatic material recalculation and compliance validation",
            "strict": True,
            "parameters": make_strict_schema(CombinePlugsTool.model_json_schema())
        }
    },
    {
        "type": "function",
        "function": {
            "name": "replace_cibp_with_long_plug",
            "description": "Replace Cast Iron Bridge Plug and cap with a single long cement plug to reduce cost and complexity",
            "strict": True,
            "parameters": make_strict_schema(ReplaceCIBPTool.model_json_schema())
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recalc_materials_and_export",
            "description": "Recalculate materials, volumes, totals, and re-run compliance validation after plan modifications",
            "strict": True,
            "parameters": make_strict_schema(RecalcMaterialsTool.model_json_schema())
        }
    },
    {
        "type": "function",
        "function": {
            "name": "change_plug_type",
            "description": "Change cement plug types (cement <-> perforate & squeeze <-> open hole) for all plugs, specific steps, or formations",
            "strict": True,
            "parameters": make_strict_schema(ChangePlugTypeTool.model_json_schema())
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_steps",
            "description": "Remove (delete) specific steps from the plugging plan with automatic renumbering and materials recalculation",
            "strict": True,
            "parameters": make_strict_schema(RemoveStepsTool.model_json_schema())
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_plug",
            "description": "Add (insert) a new plug or step into the plugging plan at specified depth with automatic positioning and materials calculation",
            "strict": True,
            "parameters": make_strict_schema(AddPlugTool.model_json_schema())
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_formation_plugs",
            "description": "Add multiple formation top plugs in a single batch operation. IMPORTANT: Call this ONCE with ALL formations in a single array - do NOT call multiple times or use add_plug individually after this. Each plug will be ±50 ft around the formation top.",
            "strict": True,
            "parameters": make_strict_schema(AddFormationPlugsTool.model_json_schema())
        }
    },
    {
        "type": "function",
        "function": {
            "name": "override_step_materials",
            "description": "Override calculated materials with custom sack count for a specific step when user provides exact quantity",
            "strict": True,
            "parameters": make_strict_schema(OverrideMaterialsTool.model_json_schema())
        }
    }
]

