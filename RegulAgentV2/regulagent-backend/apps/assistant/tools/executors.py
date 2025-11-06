"""
Tool executors - implement the actual logic for each AI tool.

These functions are called when the AI decides to use a tool.
Each executor returns a ToolCallResponse with results, risk score, and violations delta.
"""

import logging
from typing import Dict, Any, List, Optional
from django.db import transaction

from apps.public_core.models import PlanSnapshot, WellRegistry, DocumentVector
from apps.assistant.models import ChatThread, PlanModification
from apps.assistant.services.guardrails import enforce_guardrails, GuardrailViolation
from .schemas import ToolCallResponse

# Import materials calculation functions
try:
    from apps.materials.services.material_engine import (
        annulus_capacity_bbl_per_ft,
        SlurryRecipe,
        compute_sacks,
    )
    MATERIALS_AVAILABLE = True
except ImportError:
    MATERIALS_AVAILABLE = False
    logger.warning("Materials engine not available - sack counts will not be calculated")

logger = logging.getLogger(__name__)


# Helper function to convert casing OD to nominal ID
def _get_nominal_casing_id(casing_size_in: Any) -> float:
    """
    Convert casing outer diameter to nominal inner diameter.
    Returns a float ID in inches, or a default of 4.778" if not found.
    """
    NOMINAL_ID_MAP = {
        13.375: 12.515,  # 13 3/8" intermediate
        11.75: 10.965,   # 11 3/4" intermediate  
        10.625: 10.2,
        9.625: 8.681,    # 9 5/8" intermediate (47 lb/ft)
        8.625: 7.921,    # 8 5/8" production
        7.0: 6.094,      # 7" production
        5.5: 4.778       # 5 1/2" production
    }
    
    if casing_size_in is None:
        return 4.778  # Default to 5.5" production casing ID
    
    # Convert to float
    if isinstance(casing_size_in, (int, float)):
        od = float(casing_size_in)
    else:
        try:
            od = float(str(casing_size_in).strip().replace('"', ''))
        except (ValueError, AttributeError):
            return 4.778  # Default
    
    # Exact match
    if od in NOMINAL_ID_MAP:
        return NOMINAL_ID_MAP[od]
    
    # Fuzzy match (within 0.02")
    for od_key, id_val in NOMINAL_ID_MAP.items():
        if abs(od - od_key) < 0.02:
            return id_val
    
    # No match found, return default
    return 4.778


def execute_get_plan_snapshot(plan_id: str, thread: ChatThread) -> Dict[str, Any]:
    """
    Retrieve plan snapshot JSON.
    
    Returns the full plan payload plus metadata.
    Gets the LATEST snapshot if multiple exist.
    """
    try:
        # Get the latest snapshot for this plan_id (handle multiple versions)
        plan = PlanSnapshot.objects.select_related('well').filter(
            plan_id=plan_id,
            tenant_id=thread.tenant_id
        ).order_by('-created_at').first()
        
        if not plan:
            return ToolCallResponse(
                success=False,
                message=f"Plan {plan_id} not found for your tenant"
            ).model_dump()
        
        return ToolCallResponse(
            success=True,
            message=f"Retrieved plan {plan_id}",
            data={
                "plan_id": plan.plan_id,
                "plan": plan.payload,
                "status": plan.status,
                "kind": plan.kind,
                "well": {
                    "api14": plan.well.api14,
                    "operator": plan.well.operator_name,
                    "field": plan.well.field_name,
                    "county": plan.well.county,
                },
                "metadata": {
                    "kernel_version": plan.kernel_version,
                    "policy_id": plan.policy_id,
                    "created_at": plan.created_at.isoformat(),
                }
            }
        ).model_dump()
    
    except PlanSnapshot.DoesNotExist:
        return ToolCallResponse(
            success=False,
            message=f"Plan {plan_id} not found for this tenant"
        ).model_dump()


def execute_answer_fact(
    question: str,
    search_scope: str,
    thread: ChatThread
) -> Dict[str, Any]:
    """
    Answer factual questions using structured data + vector search.
    
    Hybrid approach:
    1. Query ORM for structured facts (depths, formations, etc.)
    2. If needed, search document vectors for additional context
    3. Synthesize answer from both sources
    """
    try:
        plan = thread.current_plan
        well = plan.well
        
        # Build context from structured data
        structured_context = {
            "well": {
                "api": well.api14,
                "operator": well.operator_name,
                "field": well.field_name,
                "county": well.county,
                "district": well.district,
                "lat": float(well.lat) if well.lat else None,
                "lon": float(well.lon) if well.lon else None,
            },
            "plan": {
                "steps_count": len(plan.payload.get('steps', [])),
                "violations_count": len(plan.payload.get('violations', [])),
                "formations_targeted": plan.payload.get('formations_targeted', []),
                "materials_totals": plan.payload.get('materials_totals', {}),
            }
        }
        
        # TODO: Add vector search for document context
        # Similar to: "What does the W-2 say about open hole?"
        # doc_vectors = DocumentVector.objects.filter(
        #     metadata__api14=well.api14,
        #     metadata__tenant_id=thread.tenant_id
        # )
        
        # For MVP, return structured data
        answer = f"Based on structured data for well {well.api14}:\n"
        answer += f"- Operator: {well.operator_name}\n"
        answer += f"- Field: {well.field_name}\n"
        answer += f"- Plan has {len(plan.payload.get('steps', []))} steps\n"
        answer += f"- Formations: {', '.join(plan.payload.get('formations_targeted', []))}\n"
        
        return ToolCallResponse(
            success=True,
            message=answer,
            data=structured_context
        ).model_dump()
    
    except Exception as e:
        logger.exception(f"Error answering fact: {question}")
        return ToolCallResponse(
            success=False,
            message=f"Error answering question: {str(e)}"
        ).model_dump()


def execute_combine_plugs(
    step_ids: List[int],
    reason: str,
    thread: ChatThread,
    user,
    allow_plan_changes: bool = False
) -> Dict[str, Any]:
    """
    Combine multiple formation plugs into one long plug.
    
    This is a complex operation that:
    1. Validates plugs can be combined (adjacent, compatible cement)
    2. Computes new interval (min top, max base)
    3. Merges steps in plan JSON
    4. Recalculates materials
    5. Re-runs compliance validation
    6. Creates new PlanSnapshot
    """
    try:
        # Check guardrails
        if not allow_plan_changes:
            raise GuardrailViolation(
                "plan_changes_disabled",
                "Plan modifications disabled. Set allow_plan_changes=true to enable."
            )
        
        source_plan = thread.current_plan
        plan_payload = source_plan.payload.copy()
        
        # Find target steps
        steps = plan_payload.get('steps', [])
        target_steps = [s for s in steps if s.get('step_id') in step_ids]
        
        if len(target_steps) != len(step_ids):
            return ToolCallResponse(
                success=False,
                message=f"Could not find all specified step IDs: {step_ids}"
            ).model_dump()
        
        # Validate all are cement-based plugs (can combine formation_plug, formation_top_plug, cement_plug)
        COMBINABLE_TYPES = ['formation_plug', 'formation_top_plug', 'cement_plug']
        for step in target_steps:
            if step.get('type') not in COMBINABLE_TYPES:
                return ToolCallResponse(
                    success=False,
                    message=f"Step {step.get('step_id')} type '{step.get('type')}' cannot be combined. Only cement-based plugs can be merged."
                ).model_dump()
        
        # Compute merged interval (fill any gaps between plugs)
        # Top = shallowest (largest depth value), Base = deepest (smallest depth value)
        all_tops = [s.get('top', 0) for s in target_steps if s.get('top') is not None]
        all_bases = [s.get('base', 0) for s in target_steps if s.get('base') is not None]
        
        if not all_tops or not all_bases:
            return ToolCallResponse(
                success=False,
                message="Cannot determine depths for all steps. Ensure all steps have valid top/base values."
            ).model_dump()
        
        merged_top = max(all_tops)  # Shallowest point (largest number)
        merged_base = min(all_bases)  # Deepest point (smallest number)
        merged_interval = merged_top - merged_base  # Interval in feet
        
        # Calculate gap to inform user
        depths_sorted = sorted([(s.get('top', 0), s.get('base', 0)) for s in target_steps], key=lambda x: x[1])
        max_gap = 0
        for i in range(len(depths_sorted) - 1):
            gap = depths_sorted[i][0] - depths_sorted[i+1][1]  # Shallower base - deeper top
            if gap > max_gap:
                max_gap = gap
        
        # Create merged step
        merged_step = {
            "step_id": target_steps[0].get('step_id'),  # Keep first step's ID
            "type": "cement_plug",  # Generic cement plug type
            "name": f"Combined Cement Plug ({merged_top}-{merged_base} ft)",
            "top": merged_top,
            "base": merged_base,
            "top_ft": merged_top,  # Add legacy field names for compatibility
            "bottom_ft": merged_base,
            "interval": merged_interval,
            "reason": f"Combined {len(target_steps)} plugs: {reason}",
            "merged_from_step_ids": step_ids,
            "details": {
                "gap_filled_ft": max_gap if max_gap > 0 else None,
                "combined_types": [s.get('type') for s in target_steps],
                "merged": True,  # Mark as merged for identification
            }
        }
        
        # Calculate materials for combined plug
        sacks_calculated = None
        if MATERIALS_AVAILABLE:
            try:
                # Get well geometry from source plan (inherit from original plugs)
                well_geometry = plan_payload.get('well_geometry', {})
                
                # Try to get geometry from one of the target steps (they should all use same geometry)
                example_step = target_steps[0]
                geometry_used = (example_step.get('details') or {}).get('geometry_used', {})
                casing_id = geometry_used.get('casing_id_in')
                stinger_od = geometry_used.get('stinger_od_in')
                
                # Fallback: extract from production casing/tubing if not in step
                if casing_id is None or stinger_od is None:
                    casing_strings = well_geometry.get('casing_strings', [])
                    prod_casing = next((c for c in casing_strings if c.get('string') == 'production'), None)
                    if prod_casing:
                        casing_id = prod_casing.get('size_in')
                    
                    tubing_list = well_geometry.get('tubing', [])
                    if tubing_list and isinstance(tubing_list, list):
                        stinger_od = tubing_list[0].get('size_in')
                
                if casing_id is not None and stinger_od is not None:
                    # Get recipe from original step or use defaults
                    recipe_dict = example_step.get('recipe') or plan_payload.get('default_recipe', {})
                    recipe = SlurryRecipe(
                        recipe_id=recipe_dict.get('id', 'class_h_neat'),
                        cement_class=recipe_dict.get('class', 'H'),
                        density_ppg=float(recipe_dict.get('density_ppg', 15.8)),
                        yield_ft3_per_sk=float(recipe_dict.get('yield_ft3_per_sk', 1.18)),
                        water_gal_per_sk=float(recipe_dict.get('water_gal_per_sk', 5.2)),
                        additives=recipe_dict.get('additives', []),
                    )
                    
                    # Calculate volume: interval × annular capacity × (1 + excess)
                    # Note: cement_plug type does NOT get Texas depth-based excess - only specific types like
                    # formation_top_plug, intermediate_casing_shoe_plug, etc. get that treatment
                    cap_bbl_per_ft = annulus_capacity_bbl_per_ft(float(casing_id), float(stinger_od))
                    annular_excess = float(example_step.get('annular_excess', 0.4))
                    total_bbl = merged_interval * cap_bbl_per_ft * (1.0 + annular_excess)
                    
                    logger.info(
                        f"combine_plugs: Calculated materials for merged plug {merged_top}-{merged_base} ft: "
                        f"interval={merged_interval:.1f} ft, cap={cap_bbl_per_ft:.4f} bbl/ft, "
                        f"excess={annular_excess}, total={total_bbl:.2f} bbl"
                    )
                    
                    # Convert to sacks
                    rounding_mode = recipe_dict.get('rounding', 'nearest')
                    vb = compute_sacks(total_bbl, recipe, rounding=rounding_mode)
                    
                    sacks_calculated = int(vb.sacks)
                    merged_step["sacks"] = sacks_calculated
                    merged_step["materials"] = {
                        "slurry": {
                            "total_bbl": total_bbl,
                            "ft3": vb.ft3,
                            "sacks": vb.sacks,
                            "water_bbl": vb.water_bbl,
                            "additives": vb.additives,
                            "explain": vb.explain,
                        }
                    }
                    merged_step["details"]["geometry_used"] = {
                        "annulus": "production_casing_id_vs_stinger_od",
                        "casing_id_in": float(casing_id),
                        "stinger_od_in": float(stinger_od),
                    }
                    merged_step["details"]["materials_explain"] = {
                        "rounding_mode": rounding_mode,
                        "cap_bbl_per_ft": cap_bbl_per_ft,
                        "annular_excess": annular_excess,
                        "tac_excess_factor": tac_factor,
                        "tac_excess_kft_units": kft_units,
                    }
                    logger.info(
                        f"Calculated materials for combined plug: {sacks_calculated} sacks "
                        f"({merged_interval} ft interval, {total_bbl:.2f} bbl)"
                    )
                else:
                    logger.warning(f"Missing geometry data for materials calculation: casing_id={casing_id}, stinger_od={stinger_od}")
            except Exception as e:
                logger.exception(f"Error calculating materials for combined plug: {e}")
        
        # Fallback if materials not calculated
        if sacks_calculated is None:
            merged_step["materials"] = {
                "note": "Materials calculation pending - geometry data unavailable"
            }
        
        # Remove old steps and add merged step
        new_steps = [s for s in steps if s.get('step_id') not in step_ids]
        new_steps.append(merged_step)
        new_steps.sort(key=lambda s: s.get('base', 0) or 0, reverse=True)  # Re-sort by depth (deepest first)
        
        plan_payload['steps'] = new_steps
        
        # Regenerate rrc_export from updated steps
        # This ensures frontend displays the modified plan correctly
        rrc_export = []
        for idx, step in enumerate(sorted(new_steps, key=lambda s: s.get('base', 0) or 0, reverse=True), start=1):
            step_type = step.get('type')
            regulatory_basis = step.get('regulatory_basis', [])
            
            # Format type for RRC export
            if step_type == 'bridge_plug':
                export_type = 'CIBP'
            elif step_type in ('bridge_plug_cap', 'cibp_cap'):
                export_type = 'CIBP cap'
            else:
                export_type = step_type
            
            # Build remarks from regulatory basis
            remarks_parts = []
            if isinstance(regulatory_basis, list):
                remarks_parts.extend(regulatory_basis)
            
            placement_basis = step.get('reason') or step.get('placement_basis')
            if placement_basis:
                remarks_parts.append(placement_basis)
            
            # Get depths - handle 0 properly (can't use 'or' because 0 is falsy)
            from_ft = step.get('base')
            if from_ft is None:
                from_ft = step.get('bottom_ft')
            
            to_ft = step.get('top')
            if to_ft is None:
                to_ft = step.get('top_ft')
            
            rrc_export.append({
                "plug_no": idx,
                "step_id": step.get('step_id'),
                "type": export_type,
                "from_ft": from_ft,
                "to_ft": to_ft,
                "sacks": step.get('sacks'),
                "remarks": "; ".join(filter(None, remarks_parts)) or None
            })
        
        plan_payload['rrc_export'] = rrc_export
        
        # Recalculate materials_totals for the entire plan
        total_sacks = 0
        total_bbl = 0.0
        for step in new_steps:
            step_sacks = step.get('sacks')
            if step_sacks is not None:
                try:
                    total_sacks += int(step_sacks)
                except (ValueError, TypeError):
                    pass
            
            # Also sum from materials dict if available
            materials = step.get('materials', {})
            if isinstance(materials, dict):
                slurry = materials.get('slurry', {})
                if isinstance(slurry, dict):
                    step_bbl = slurry.get('total_bbl')
                    if step_bbl is not None:
                        try:
                            total_bbl += float(step_bbl)
                        except (ValueError, TypeError):
                            pass
        
        # Update materials_totals
        plan_payload['materials_totals'] = {
            "total_sacks": total_sacks,
            "total_bbl": round(total_bbl, 2),
        }
        
        logger.info(f"Recalculated materials totals: {total_sacks} sacks, {total_bbl:.2f} bbl")
        
        # Create new snapshot
        with transaction.atomic():
            result_snapshot = PlanSnapshot.objects.create(
                tenant_id=thread.tenant_id,
                well=source_plan.well,
                plan_id=source_plan.plan_id,
                kind='post_edit',
                status=PlanSnapshot.STATUS_DRAFT,
                payload=plan_payload,
                kernel_version=source_plan.kernel_version,
                policy_id=source_plan.policy_id,
            )
            
            # Create modification record
            modification = PlanModification.objects.create(
                chat_thread=thread,
                source_snapshot=source_plan,
                result_snapshot=result_snapshot,
                op_type='combine_plugs',
                description=f"Combined {len(step_ids)} plugs: {reason}",
                operation_payload={
                    "step_ids": step_ids,
                    "merged_interval": {
                        "top": merged_top,
                        "base": merged_base,
                        "interval_ft": merged_interval
                    },
                    "gap_filled_ft": max_gap if max_gap > 0 else 0,
                },
                diff={
                    "removed_step_ids": step_ids,
                    "added_step_id": merged_step["step_id"],
                    "type": "merge_steps"
                },
                applied_by=user,
                is_applied=True,
                risk_score=0.3,  # TODO: Calculate actual risk
                violations_delta=[],  # TODO: Compute delta
            )
            
            # Update thread's current plan
            thread.current_plan = result_snapshot
            thread.save(update_fields=['current_plan'])
        
        logger.info(
            f"Combined {len(step_ids)} plugs in thread {thread.id} "
            f"→ modification {modification.id}"
        )
        
        gap_message = f" (filled {max_gap} ft gap)" if max_gap > 0 else ""
        return ToolCallResponse(
            success=True,
            message=f"Successfully combined {len(step_ids)} plugs into single cement plug at {merged_top}-{merged_base} ft{gap_message}",
            data={
                "modification_id": modification.id,
                "new_step": merged_step,
                "merged_interval_ft": merged_interval,
                "gap_filled_ft": max_gap if max_gap > 0 else 0,
            },
            risk_score=0.3,
            violations_delta=[]
        ).model_dump()
    
    except GuardrailViolation as e:
        logger.warning(f"Guardrail violation: {e.violation_type}")
        return ToolCallResponse(
            success=False,
            message=f"Guardrail violation: {str(e)}"
        ).model_dump()
    
    except Exception as e:
        logger.exception(f"Error combining plugs: {step_ids}")
        return ToolCallResponse(
            success=False,
            message=f"Error combining plugs: {str(e)}"
        ).model_dump()


def execute_replace_cibp(
    interval: str,
    custom_top_depth: int | None,
    custom_base_depth: int | None,
    reason: str,
    thread: ChatThread,
    user,
    allow_plan_changes: bool = False
) -> Dict[str, Any]:
    """
    Replace CIBP + cap with long cement plug.
    
    MVP: Returns placeholder - full implementation pending.
    """
    if not allow_plan_changes:
        return ToolCallResponse(
            success=False,
            message="Plan modifications disabled. Set allow_plan_changes=true to enable."
        ).model_dump()
    
    return ToolCallResponse(
        success=False,
        message="CIBP replacement not yet implemented - coming in next sprint"
    ).model_dump()


def execute_recalc_materials(
    revalidate_compliance: bool,
    thread: ChatThread
) -> Dict[str, Any]:
    """
    Recalculate materials and totals.
    
    MVP: Returns success without recalculation - full implementation pending.
    Materials are currently calculated during plan generation.
    """
    return ToolCallResponse(
        success=True,
        message="Materials and compliance are recalculated automatically during plan modifications. No additional action needed.",
        data={
            "note": "Material recalculation is performed by the kernel during plan generation",
            "revalidate_compliance": revalidate_compliance
        }
    ).model_dump()


def execute_change_plug_type(
    new_type: str,
    apply_to_all: bool,
    step_ids: List[int] | None,
    formations: List[str] | None,
    reason: str,
    thread: ChatThread,
    user,
    allow_plan_changes: bool = False
) -> Dict[str, Any]:
    """
    Change plug type(s) in the plan.
    
    Supports:
    - apply_to_all: Convert ALL cement-based plugs
    - step_ids: Convert specific steps
    - formations: Convert plugs at specific formations
    
    Performs:
    1. Identifies target steps
    2. Validates conversion feasibility
    3. Updates type and parameters
    4. Recalculates materials
    5. Creates new snapshot
    """
    try:
        # Check guardrails
        if not allow_plan_changes:
            raise GuardrailViolation(
                "plan_changes_disabled",
                "Plan modifications disabled. Set allow_plan_changes=true to enable."
            )
        
        source_plan = thread.current_plan
        plan_payload = source_plan.payload.copy()
        
        # Find target steps
        steps = plan_payload.get('steps', [])
        target_steps = []
        
        # Eligible types for conversion
        CONVERTIBLE_TYPES = ['cement_plug', 'formation_plug', 'formation_top_plug', 'perforate_and_squeeze_plug', 'perf_and_circulate_to_surface']
        
        if apply_to_all:
            # Convert all eligible plugs
            target_steps = [s for s in steps if s.get('type') in CONVERTIBLE_TYPES]
            if not target_steps:
                return ToolCallResponse(
                    success=False,
                    message="No eligible cement-based plugs found to convert."
                ).model_dump()
            logger.info(f"change_plug_type: apply_to_all mode, found {len(target_steps)} eligible steps")
        
        elif step_ids:
            # Convert specific step IDs
            target_steps = [s for s in steps if s.get('step_id') in step_ids]
            if len(target_steps) != len(step_ids):
                return ToolCallResponse(
                    success=False,
                    message=f"Could not find all specified step IDs: {step_ids}"
                ).model_dump()
            # Validate all are convertible
            for step in target_steps:
                if step.get('type') not in CONVERTIBLE_TYPES:
                    return ToolCallResponse(
                        success=False,
                        message=f"Step {step.get('step_id')} type '{step.get('type')}' cannot be converted. Only cement-based plugs are eligible."
                    ).model_dump()
        
        elif formations:
            # Convert plugs at specific formations
            for step in steps:
                step_formation = step.get('formation') or (step.get('details', {}) or {}).get('formation')
                if step_formation and step_formation in formations and step.get('type') in CONVERTIBLE_TYPES:
                    target_steps.append(step)
            
            if not target_steps:
                return ToolCallResponse(
                    success=False,
                    message=f"No eligible plugs found at formations: {formations}"
                ).model_dump()
            logger.info(f"change_plug_type: formations mode, found {len(target_steps)} plugs at {formations}")
        
        else:
            return ToolCallResponse(
                success=False,
                message="Must specify either apply_to_all=true, step_ids, or formations."
            ).model_dump()
        
        # Perform conversion for each target step
        converted_count = 0
        for step in target_steps:
            old_type = step.get('type')
            
            # Update type
            step['type'] = new_type
            
            # Add/update parameters based on new type
            if new_type == 'perforate_and_squeeze_plug':
                # Convert to perf & squeeze
                top_ft = step.get('top_ft') or step.get('top', 0)
                bottom_ft = step.get('bottom_ft') or step.get('base', 0)
                interval_length = abs(top_ft - bottom_ft)
                
                # For perf & squeeze: perforate entire interval + 50 ft cap above
                # Perforation interval = original plug interval
                perf_bottom = bottom_ft
                perf_top = top_ft
                
                # Cap goes above the perforation interval
                cap_bottom = perf_top
                cap_top = cap_bottom + 50
                
                step['total_top_ft'] = cap_top
                step['total_bottom_ft'] = perf_bottom
                step['requires_perforation'] = True
                step['details'] = step.get('details', {})
                step['details']['perforation_interval'] = {
                    'top_ft': perf_top,
                    'bottom_ft': perf_bottom,
                    'length_ft': interval_length
                }
                step['details']['cement_cap_inside_casing'] = {
                    'top_ft': cap_top,
                    'bottom_ft': cap_bottom,
                    'height_ft': 50
                }
                step['details']['converted_from'] = old_type
                # Note: squeeze_factor will be calculated during materials computation using Texas depth rule
                
                # Add regulatory basis
                if 'regulatory_basis' not in step:
                    step['regulatory_basis'] = []
                if 'tx.tac.16.3.14(g)(2)' not in step['regulatory_basis']:
                    step['regulatory_basis'].append('tx.tac.16.3.14(g)(2)')
            
            elif new_type == 'perf_and_circulate_to_surface':
                # Convert to perf & circulate to surface (annulus circulation)
                top_ft = step.get('top_ft') or step.get('top', 0)
                bottom_ft = step.get('bottom_ft') or step.get('base', 0)
                
                # For perf & circulate: typically from shoe to near-surface
                # If not specified, default top to 3 ft
                if top_ft == bottom_ft or top_ft > bottom_ft - 50:
                    top_ft = 3.0  # Default near-surface
                
                step['top_ft'] = top_ft
                step['bottom_ft'] = bottom_ft
                step['name'] = "Cement Surface Plug (Perforate and Circulate)"
                step['perforation_depth_ft'] = bottom_ft
                step['geometry_context'] = 'annulus_circulation'
                
                step['details'] = step.get('details', {})
                step['details']['method'] = 'perforate_and_circulate'
                step['details']['target_annulus'] = 'surface_to_intermediate'
                step['details']['perforation_location'] = f"Above {bottom_ft} ft shoe"
                step['details']['circulation_target'] = "Returns to surface"
                step['details']['converted_from'] = old_type
                
                # Add regulatory basis
                if 'regulatory_basis' not in step:
                    step['regulatory_basis'] = []
                if 'tx.tac.16.3.14(e)(2)' not in step['regulatory_basis']:
                    step['regulatory_basis'].append('tx.tac.16.3.14(e)(2)')
            
            elif new_type == 'cement_plug':
                # Convert to standard cement plug
                # Remove perf & squeeze specific fields
                step.pop('total_top_ft', None)
                step.pop('total_bottom_ft', None)
                step.pop('requires_perforation', None)
                step.pop('squeeze_factor', None)
                
                # Ensure standard top/bottom fields exist
                if 'top_ft' not in step and 'top' in step:
                    step['top_ft'] = step['top']
                if 'bottom_ft' not in step and 'base' in step:
                    step['bottom_ft'] = step['base']
                
                step['details'] = step.get('details', {})
                step['details']['converted_from'] = old_type
            
            converted_count += 1
            logger.info(f"Converted step {step.get('step_id')} from {old_type} to {new_type}")
        
        # Recalculate materials for converted steps
        if MATERIALS_AVAILABLE:
            try:
                well_geometry = plan_payload.get('well_geometry', {})
                
                # Get default geometry from well (used as fallback for all steps)
                casing_strings = well_geometry.get('casing_strings', [])
                prod_casing = next((c for c in casing_strings if c.get('string') == 'production'), None)
                default_casing_id = prod_casing.get('size_in') if prod_casing else None
                
                tubing_list = well_geometry.get('tubing', [])
                default_stinger_od = tubing_list[0].get('size_in') if tubing_list and isinstance(tubing_list, list) else None
                
                logger.info(f"Default geometry: casing_id={default_casing_id}, stinger_od={default_stinger_od}")
                
                for step in target_steps:
                    # Try to get geometry from step details first
                    geometry_used = (step.get('details') or {}).get('geometry_used', {})
                    casing_id = geometry_used.get('casing_id_in') or default_casing_id
                    stinger_od = geometry_used.get('stinger_od_in') or default_stinger_od
                    
                    logger.info(f"Step {step.get('step_id')}: casing_id={casing_id}, stinger_od={stinger_od}, new_type={new_type}")
                    
                    if casing_id and stinger_od and new_type == 'perforate_and_squeeze_plug':
                        # Calculate materials for perf & squeeze (two parts)
                        perf_interval = step['details']['perforation_interval']
                        cap_interval = step['details']['cement_cap_inside_casing']
                        
                        perf_len = abs(perf_interval['top_ft'] - perf_interval['bottom_ft'])
                        cap_len = abs(cap_interval['top_ft'] - cap_interval['bottom_ft'])
                        
                        # Use decision tree to determine squeeze context
                        perf_bottom = perf_interval['bottom_ft']
                        
                        try:
                            from apps.kernel.services.w3a_rules import _get_casing_strings_at_depth
                            # Build minimal facts dict for decision tree
                            facts_for_decision = {"casing_strings": casing_strings}
                            casing_context = _get_casing_strings_at_depth(facts_for_decision, perf_bottom)
                        except Exception as e:
                            logger.warning(f"Failed to get casing context: {e}")
                            casing_context = {"context": "unknown", "count": 0}
                        
                        # Texas TAC §3.14(d)(11): 1 + 10% per 1000 ft of depth
                        depth_kft = int((perf_bottom + 999.0) / 1000.0)
                        texas_excess_factor = 1.0 + (0.10 * depth_kft)
                        
                        squeeze_context = casing_context.get("context", "unknown")
                        logger.info(
                            f"change_plug_type: Squeeze at {perf_bottom} ft → "
                            f"context={squeeze_context}, texas_factor={texas_excess_factor}x"
                        )
                        
                        # Calculate annular capacity based on context
                        if casing_context["context"] == "annulus_squeeze":
                            # TWO STRINGS: Cement between inner OD and outer ID
                            inner = casing_context.get("inner_string", {})
                            outer = casing_context.get("outer_string", {})
                            
                            if outer.get("id_in") and inner.get("size_in"):
                                outer_id = float(outer["id_in"])
                                inner_od = float(inner["size_in"])
                                ann_cap = annulus_capacity_bbl_per_ft(outer_id, inner_od)
                                logger.info(
                                    f"change_plug_type: Annulus squeeze - {inner['name']} {inner_od}\" OD "
                                    f"inside {outer['name']} {outer_id}\" ID"
                                )
                            else:
                                # Fallback: use production casing ID
                                ann_cap = annulus_capacity_bbl_per_ft(float(casing_id), float(stinger_od))
                                logger.warning(f"change_plug_type: Annulus squeeze fallback to production casing")
                                
                        elif casing_context["context"] == "open_hole_squeeze":
                            # ONE STRING: Cement into formation (use hole diameter)
                            inner = casing_context.get("inner_string", {})
                            prod_casing = next((c for c in casing_strings if c.get('string') == 'production'), None)
                            hole_size = prod_casing.get("hole_size_in") if prod_casing else None
                            
                            if hole_size and inner.get("size_in"):
                                inner_od = float(inner["size_in"])
                                ann_cap = annulus_capacity_bbl_per_ft(float(hole_size), inner_od)
                                logger.info(
                                    f"change_plug_type: Open-hole squeeze - {inner['name']} {inner_od}\" OD "
                                    f"in {hole_size}\" hole"
                                )
                            else:
                                # Fallback: estimate hole size as casing OD + 2"
                                inner_od = float(inner.get("size_in", casing_id)) if inner.get("size_in") else float(casing_id)
                                estimated_hole = inner_od + 2.0
                                ann_cap = annulus_capacity_bbl_per_ft(estimated_hole, inner_od)
                                logger.warning(f"change_plug_type: Open-hole squeeze estimated hole={estimated_hole}\"")
                        else:
                            # Unknown context - use default casing geometry
                            ann_cap = annulus_capacity_bbl_per_ft(float(casing_id), float(stinger_od))
                            logger.warning(f"change_plug_type: Unknown context, using default casing geometry")
                        
                        # Calculate squeeze volume with Texas depth-based excess
                        base_volume = perf_len * ann_cap
                        squeeze_bbl = base_volume * texas_excess_factor
                        
                        # Cap: use standard cased excess (0.4 or 40%)
                        cap_excess = 0.4
                        cap_bbl = cap_len * ann_cap * (1.0 + cap_excess)
                        
                        total_bbl = squeeze_bbl + cap_bbl
                        
                        # Get recipe (default to Class H)
                        recipe = SlurryRecipe(
                            recipe_id="class_h_neat_15_8",
                            cement_class="H",
                            density_ppg=15.8,
                            yield_ft3_per_sk=1.18,
                            water_gal_per_sk=5.2,
                            additives=[]
                        )
                        
                        vb = compute_sacks(total_bbl, recipe, rounding="up")  # Round up for safety
                        step['sacks'] = int(vb.sacks)
                        
                        # Store squeeze context in step details for transparency
                        step['details']['squeeze_context'] = squeeze_context
                        step['details']['texas_excess_factor'] = texas_excess_factor
                        step['details']['depth_kft'] = depth_kft
                        step['details']['base_volume_bbl'] = base_volume
                        
                        logger.info(
                            f"Recalculated step {step.get('step_id')}: {vb.sacks} sacks for perf & squeeze "
                            f"[{squeeze_context}, texas_factor={texas_excess_factor}x at {depth_kft}kft] "
                            f"(perf_len={perf_len:.1f} ft, base={base_volume:.2f} bbl, squeeze={squeeze_bbl:.2f} bbl + cap={cap_bbl:.2f} bbl = {total_bbl:.2f} bbl total)"
                        )
                    
                    elif new_type == 'perf_and_circulate_to_surface':
                        # Calculate materials for annulus circulation
                        # Find intermediate and surface casing
                        intermediate_casing = next((c for c in casing_strings if 'intermediate' in c.get('string', '').lower()), None)
                        surface_casing = next((c for c in casing_strings if 'surface' in c.get('string', '').lower()), None)
                        
                        if intermediate_casing and surface_casing:
                            top_ft = step.get('top_ft', 3.0)
                            bottom_ft = step.get('bottom_ft')
                            interval_ft = abs(top_ft - bottom_ft)
                            
                            # Get IDs for annulus calculation
                            intermediate_od = float(intermediate_casing.get('size_in', 9.625))
                            surface_id = _get_nominal_casing_id(surface_casing.get('size_in'))
                            
                            # Annulus capacity: outer ID vs inner OD
                            ann_cap = annulus_capacity_bbl_per_ft(surface_id, intermediate_od)
                            
                            # Texas depth excess using bottom depth (shoe)
                            depth_kft = int((bottom_ft + 999.0) / 1000.0)
                            texas_excess_factor = 1.0 + (0.10 * depth_kft)
                            
                            # Operational top-off for circulation (5% default)
                            operational_topoff = 1.05
                            
                            # Calculate volume
                            base_volume_bbl = interval_ft * ann_cap
                            total_bbl = base_volume_bbl * texas_excess_factor * operational_topoff
                            
                            recipe = SlurryRecipe(
                                recipe_id="class_h_neat_15_8",
                                cement_class="H",
                                density_ppg=15.8,
                                yield_ft3_per_sk=1.18,
                                water_gal_per_sk=5.2,
                                additives=[]
                            )
                            
                            vb = compute_sacks(total_bbl, recipe, rounding="nearest")
                            
                            # Round to nearest 5 sacks for operational convenience
                            sacks_rounded = int(round(vb.sacks / 5.0) * 5)
                            step['sacks'] = sacks_rounded
                            
                            # Store geometry in step
                            step['outer_string'] = 'surface'
                            step['inner_string'] = 'intermediate'
                            step['outer_casing_id_in'] = surface_id
                            step['inner_casing_od_in'] = intermediate_od
                            step['details']['texas_excess_factor'] = texas_excess_factor
                            step['details']['depth_kft'] = depth_kft
                            step['details']['operational_topoff'] = operational_topoff
                            
                            logger.info(
                                f"Recalculated step {step.get('step_id')}: {sacks_rounded} sacks for perf_and_circulate_to_surface "
                                f"({interval_ft:.1f} ft annulus, {surface_id:.3f}\" ID - {intermediate_od:.3f}\" OD, "
                                f"ann_cap={ann_cap:.4f} bbl/ft, texas_factor={texas_excess_factor:.2f}x, total={total_bbl:.2f} bbl)"
                            )
                        else:
                            logger.warning(f"Skipped step {step.get('step_id')} - could not find intermediate/surface casing")
                    
                    elif casing_id and stinger_od and new_type == 'cement_plug':
                        # Calculate materials for standard cement plug
                        top_ft = step.get('top_ft') or step.get('top', 0)
                        bottom_ft = step.get('bottom_ft') or step.get('base', 0)
                        interval_ft = abs(top_ft - bottom_ft)
                        
                        ann_cap = annulus_capacity_bbl_per_ft(float(casing_id), float(stinger_od))
                        excess = 0.4  # Standard 40% excess
                        total_bbl = interval_ft * ann_cap * (1.0 + excess)
                        
                        recipe = SlurryRecipe(
                            recipe_id="class_c_neat_15_6",
                            cement_class="C",
                            density_ppg=15.6,
                            yield_ft3_per_sk=1.18,
                            water_gal_per_sk=5.2,
                            additives=[]
                        )
                        
                        vb = compute_sacks(total_bbl, recipe, rounding="nearest")
                        step['sacks'] = int(vb.sacks)
                        
                        logger.info(f"Recalculated step {step.get('step_id')}: {vb.sacks} sacks for cement plug ({total_bbl:.2f} bbl)")
                    else:
                        logger.warning(f"Skipped step {step.get('step_id')} - missing geometry: casing_id={casing_id}, stinger_od={stinger_od}, new_type={new_type}")
                
                # Recalculate materials_totals for the entire plan
                total_sacks = sum(s.get('sacks', 0) for s in steps if s.get('sacks') is not None)
                plan_payload['materials_totals'] = {
                    'total_sacks': total_sacks,
                    'note': 'Recalculated after plug type conversion'
                }
                logger.info(f"Updated materials_totals: {total_sacks} total sacks")
                
            except Exception as e:
                logger.exception(f"Error recalculating materials: {e}")
                # Continue anyway - materials may be recalculated later
        
        plan_payload['steps'] = steps
        
        # Create new PlanSnapshot
        new_snapshot = PlanSnapshot.objects.create(
            tenant_id=thread.tenant_id,
            well=source_plan.well,
            plan_id=source_plan.plan_id,
            kind='post_edit',
            status=PlanSnapshot.STATUS_DRAFT,
            payload=plan_payload,
            kernel_version=source_plan.kernel_version,
            policy_id=source_plan.policy_id,
            extraction_meta=source_plan.extraction_meta
        )
        
        # Create PlanModification record
        modification = PlanModification.objects.create(
            source_snapshot=source_plan,
            result_snapshot=new_snapshot,
            op_type='change_materials',  # Using closest existing type
            description=f"Changed {converted_count} plug(s) to {new_type}: {reason}",
            operation_payload={
                'new_type': new_type,
                'apply_to_all': apply_to_all,
                'step_ids': step_ids,
                'formations': formations,
                'converted_count': converted_count
            },
            diff={
                'changed_steps': [s.get('step_id') for s in target_steps],
                'old_types': [s.get('details', {}).get('converted_from') for s in target_steps],
                'new_type': new_type
            },
            risk_score=0.2,  # Low risk for plug type changes
            chat_thread=thread,
            applied_by=user if user else None
        )
        
        # Update thread's current plan
        thread.current_plan = new_snapshot
        thread.save()
        
        total_sacks = plan_payload.get('materials_totals', {}).get('total_sacks', 0)
        
        return ToolCallResponse(
            success=True,
            message=f"Successfully converted {converted_count} plug(s) to {new_type}. Materials recalculated: {total_sacks} total sacks.",
            data={
                'converted_count': converted_count,
                'total_sacks': total_sacks,
                'target_step_ids': [s.get('step_id') for s in target_steps],
                'new_snapshot_id': new_snapshot.id,
                'modification_id': modification.id
            },
            risk_score=0.2,
            violations_delta=[]
        ).model_dump()
    
    except GuardrailViolation as e:
        logger.warning(f"Guardrail violation: {e}")
        return ToolCallResponse(
            success=False,
            message=str(e)
        ).model_dump()
    
    except Exception as e:
        logger.exception("change_plug_type failed")
        return ToolCallResponse(
            success=False,
            message=f"Error changing plug type: {str(e)}"
        ).model_dump()



def execute_remove_steps(
    step_ids: List[int],
    reason: str,
    thread: ChatThread,
    user,
    allow_plan_changes: bool = False
) -> Dict[str, Any]:
    """
    Remove specified steps from the plan.
    
    Performs:
    1. Validates step IDs exist
    2. Checks guardrails (max_steps_removed)
    3. Warns if removing critical regulatory steps
    4. Removes steps from plan
    5. Renumbers remaining steps
    6. Recalculates materials_totals
    7. Creates new snapshot
    """
    try:
        # Check guardrails
        if not allow_plan_changes:
            raise GuardrailViolation(
                "plan_changes_disabled",
                "Plan modifications disabled. Set allow_plan_changes=true to enable."
            )
        
        source_plan = thread.current_plan
        plan_payload = source_plan.payload.copy()
        
        # Find target steps
        steps = plan_payload.get('steps', [])
        steps_to_remove = [s for s in steps if s.get('step_id') in step_ids]
        
        if len(steps_to_remove) != len(step_ids):
            missing_ids = set(step_ids) - {s.get('step_id') for s in steps_to_remove}
            return ToolCallResponse(
                success=False,
                message=f"Could not find step IDs: {list(missing_ids)}"
            ).model_dump()
        
        # Warn if removing critical regulatory steps
        CRITICAL_TYPES = ['uqw_isolation_plug', 'surface_casing_shoe_plug', 'cut_casing_below_surface']
        critical_steps = [s for s in steps_to_remove if s.get('type') in CRITICAL_TYPES]
        warning_msg = None
        if critical_steps:
            critical_types = [s.get('type') for s in critical_steps]
            warning_msg = f"⚠️ Removing critical regulatory step(s): {', '.join(critical_types)}. Plan may be non-compliant."
            logger.warning(f"remove_steps: {warning_msg}")
        
        # Remove steps
        remaining_steps = [s for s in steps if s.get('step_id') not in step_ids]
        
        # Renumber remaining steps sequentially
        for idx, step in enumerate(remaining_steps, start=1):
            step['step_id'] = idx
        
        plan_payload['steps'] = remaining_steps
        
        # Recalculate materials_totals
        total_sacks = sum(s.get('sacks', 0) for s in remaining_steps if s.get('sacks') is not None)
        plan_payload['materials_totals'] = {
            'total_sacks': total_sacks,
            'note': f'Recalculated after removing {len(step_ids)} step(s)'
        }
        
        # Create new PlanSnapshot
        new_snapshot = PlanSnapshot.objects.create(
            tenant_id=thread.tenant_id,
            well=source_plan.well,
            plan_id=source_plan.plan_id,
            kind='post_edit',
            status=PlanSnapshot.STATUS_DRAFT,
            payload=plan_payload,
            kernel_version=source_plan.kernel_version,
            policy_id=source_plan.policy_id,
            extraction_meta=source_plan.extraction_meta
        )
        
        # Create PlanModification record
        modification = PlanModification.objects.create(
            source_snapshot=source_plan,
            result_snapshot=new_snapshot,
            op_type='remove_steps',
            description=f"Removed {len(step_ids)} step(s): {reason}",
            operation_payload={
                'removed_step_ids': step_ids,
                'removed_types': [s.get('type') for s in steps_to_remove],
                'reason': reason
            },
            diff={
                'removed_steps': [{
                    'step_id': s.get('step_id'),
                    'type': s.get('type'),
                    'top_ft': s.get('top_ft'),
                    'bottom_ft': s.get('bottom_ft')
                } for s in steps_to_remove]
            },
            risk_score=0.4 if critical_steps else 0.2,
            chat_thread=thread,
            applied_by=user if user else None
        )
        
        # Update thread's current plan
        thread.current_plan = new_snapshot
        thread.save()
        
        msg = f"Successfully removed {len(step_ids)} step(s). Plan now has {len(remaining_steps)} steps."
        if warning_msg:
            msg = f"{msg} {warning_msg}"
        
        return ToolCallResponse(
            success=True,
            message=msg,
            data={
                'removed_count': len(step_ids),
                'remaining_count': len(remaining_steps),
                'removed_step_ids': step_ids,
                'removed_types': [s.get('type') for s in steps_to_remove],
                'total_sacks': total_sacks,
                'new_snapshot_id': new_snapshot.id,
                'modification_id': modification.id,
                'warning': warning_msg
            },
            risk_score=0.4 if critical_steps else 0.2,
            violations_delta=[]
        ).model_dump()
    
    except GuardrailViolation as e:
        logger.warning(f"Guardrail violation in remove_steps: {e}")
        return ToolCallResponse(
            success=False,
            message=str(e)
        ).model_dump()
    
    except Exception as e:
        logger.exception(f"Error in remove_steps: {e}")
        return ToolCallResponse(
            success=False,
            message=f"Error removing steps: {str(e)}"
        ).model_dump()


def execute_add_formation_plugs(
    formations: List[Dict[str, Any]],
    placement_reason: str,
    thread: ChatThread,
    user,
    allow_plan_changes: bool = False
) -> Dict[str, Any]:
    """
    Add multiple formation top plugs in a single operation.
    
    Performs:
    1. Validates formation data (names, depths)
    2. Creates formation_top_plug steps for each formation
    3. Uses standard ±50 ft interval around top (or custom base if provided)
    4. Calculates materials for each plug
    5. Inserts all plugs at correct positions (sorted by depth)
    6. Renumbers all steps sequentially
    7. Recalculates materials_totals
    8. Creates new snapshot
    """
    try:
        # Check guardrails
        if not allow_plan_changes:
            raise GuardrailViolation(
                "plan_changes_disabled",
                "Plan modifications disabled. Set allow_plan_changes=true to enable."
            )
        
        if not formations or len(formations) == 0:
            return ToolCallResponse(
                success=False,
                message="No formations provided. Please provide at least one formation with name and top_ft."
            ).model_dump()
        
        source_plan = thread.current_plan
        plan_payload = source_plan.payload.copy()
        well_geometry = plan_payload.get('well_geometry', {})
        steps = plan_payload.get('steps', [])
        
        # Get geometry for materials calculation
        casing_strings = well_geometry.get('casing_strings', [])
        prod_casing = next((c for c in casing_strings if c.get('string') == 'production'), None)
        casing_id = prod_casing.get('size_in') if prod_casing else 4.778
        
        tubing_list = well_geometry.get('tubing', [])
        stinger_od = tubing_list[0].get('size_in') if tubing_list and isinstance(tubing_list, list) else 2.375
        
        # Create new steps for each formation
        new_steps = []
        formation_names = []
        
        for formation in formations:
            formation_name = formation.get('name')
            top_ft = formation.get('top_ft')
            base_ft = formation.get('base_ft')
            
            if not formation_name or top_ft is None:
                continue
            
            formation_names.append(formation_name)
            
            # Use custom base if provided, otherwise ±50 ft around top
            if base_ft is None:
                plug_top = top_ft + 50  # 50 ft above formation top
                plug_bottom = top_ft - 50  # 50 ft below formation top
            else:
                plug_top = top_ft
                plug_bottom = base_ft
            
            # Ensure proper depth ordering (top is shallower, bottom is deeper)
            if plug_top > plug_bottom:
                plug_top, plug_bottom = plug_bottom, plug_top
            
            # Determine cement class based on depth
            cement_class = 'H' if plug_bottom > 3000 else 'C'
            
            # Create step structure
            new_step = {
                'type': 'formation_top_plug',
                'top_ft': plug_top,
                'bottom_ft': plug_bottom,
                'top': plug_top,
                'base': plug_bottom,
                'details': {
                    'formation': formation_name,
                    'user_added': True,
                    'batch_added': True,
                    'placement_reason': placement_reason,
                    'cement_class': cement_class
                },
                'regulatory_basis': [],
                'special_instructions': None
            }
            
            # Calculate materials if available
            if MATERIALS_AVAILABLE:
                try:
                    interval_ft = abs(plug_bottom - plug_top)
                    ann_cap = annulus_capacity_bbl_per_ft(float(casing_id), float(stinger_od))
                    
                    # 40% excess for formation plugs
                    excess = 0.4
                    
                    # Get recipe for this cement class
                    recipe = SlurryRecipe(
                        recipe_id=f"class_{cement_class.lower()}_neat",
                        cement_class=cement_class,
                        density_ppg=15.8 if cement_class == 'H' else 15.6,
                        yield_ft3_per_sk=1.18,
                        water_gal_per_sk=5.2,
                        additives=[]
                    )
                    
                    # Calculate initial sacks
                    total_bbl = interval_ft * ann_cap * (1.0 + excess)
                    vb = compute_sacks(total_bbl, recipe)
                    calculated_sacks = int(vb.sacks)
                    
                    # Texas 25-sack minimum: expand interval if needed
                    TEXAS_MIN_SACKS = 25
                    if calculated_sacks < TEXAS_MIN_SACKS:
                        # Calculate required interval for 25 sacks
                        required_bbl = TEXAS_MIN_SACKS * recipe.yield_ft3_per_sk / 5.615  # Convert ft³ to bbl
                        required_interval = required_bbl / (ann_cap * (1.0 + excess))
                        
                        # Expand interval symmetrically around formation top
                        center = top_ft  # Original formation top
                        plug_top = center - (required_interval / 2)
                        plug_bottom = center + (required_interval / 2)
                        
                        # Recalculate with new interval
                        interval_ft = abs(plug_bottom - plug_top)
                        total_bbl = interval_ft * ann_cap * (1.0 + excess)
                        vb = compute_sacks(total_bbl, recipe)
                        calculated_sacks = int(vb.sacks)
                        
                        # Update step depths
                        new_step['top_ft'] = plug_top
                        new_step['bottom_ft'] = plug_bottom
                        new_step['top'] = plug_top
                        new_step['base'] = plug_bottom
                        new_step['details']['texas_25_sack_minimum_applied'] = True
                        new_step['details']['original_interval_ft'] = abs(plug_bottom - plug_top)
                        
                        logger.info(
                            f"add_formation_plugs: Expanded {formation_name} interval from ±50 ft to "
                            f"±{required_interval/2:.0f} ft ({plug_top:.0f}-{plug_bottom:.0f} ft) to meet 25-sack minimum"
                        )
                    
                    new_step['sacks'] = calculated_sacks
                    new_step['details']['materials_calculated'] = True
                    new_step['details']['geometry'] = {
                        'casing_id_in': float(casing_id),
                        'stinger_od_in': float(stinger_od),
                        'annular_excess': excess
                    }
                    
                    logger.info(
                        f"add_formation_plugs: Calculated {calculated_sacks} sacks for {formation_name} "
                        f"({plug_top:.0f}-{plug_bottom:.0f} ft, {interval_ft:.0f} ft)"
                    )
                except Exception as e:
                    logger.warning(f"add_formation_plugs: Failed to calculate materials for {formation_name}: {e}")
                    new_step['sacks'] = None
            
            new_steps.append(new_step)
        
        if not new_steps:
            return ToolCallResponse(
                success=False,
                message="No valid formation steps could be created. Check formation data (name and top_ft required)."
            ).model_dump()
        
        # Add new steps to plan and sort by depth (deepest first)
        steps.extend(new_steps)
        # Handle None values in sorting - treat as 0 (surface level)
        steps.sort(key=lambda s: s.get('bottom_ft') or s.get('base') or 0, reverse=True)
        
        # Renumber steps
        for i, step in enumerate(steps, start=1):
            step['step_id'] = i
        
        plan_payload['steps'] = steps
        
        # Recalculate materials totals
        total_sacks = sum(s.get('sacks', 0) or 0 for s in steps if isinstance(s.get('sacks'), int))
        plan_payload.setdefault('materials_totals', {})['total_sacks'] = total_sacks
        
        # Create new PlanSnapshot
        new_snapshot = PlanSnapshot.objects.create(
            tenant_id=thread.tenant_id,
            well=source_plan.well,
            plan_id=source_plan.plan_id,
            kind='post_edit',
            status=PlanSnapshot.STATUS_DRAFT,
            payload=plan_payload,
            kernel_version=source_plan.kernel_version,
            policy_id=source_plan.policy_id,
            extraction_meta=source_plan.extraction_meta
        )
        
        # Create PlanModification record
        modification = PlanModification.objects.create(
            source_snapshot=source_plan,
            result_snapshot=new_snapshot,
            chat_thread=thread,
            applied_by=user,
            op_type='add_formation_plugs',
            description=f"Added {len(new_steps)} formation plugs: {', '.join(formation_names)}",
            details={
                'formations_added': formations,
                'placement_reason': placement_reason,
                'steps_added': len(new_steps)
            },
            risk_score=0.3  # Medium-low risk for formation plugs
        )
        
        # Update thread to point to new plan
        thread.current_plan = new_snapshot
        thread.save()
        
        logger.info(
            f"add_formation_plugs: Successfully added {len(new_steps)} formation plugs "
            f"for plan {source_plan.plan_id}"
        )
        
        return ToolCallResponse(
            success=True,
            message=(
                f"✅ Added {len(new_steps)} formation plugs: {', '.join(formation_names)}. "
                f"Plan updated with {total_sacks} total sacks."
            ),
            data={
                'plan_id': new_snapshot.plan_id,
                'snapshot_id': new_snapshot.id,
                'formations_added': formation_names,
                'steps_added': len(new_steps),
                'total_steps': len(steps),
                'total_sacks': total_sacks
            },
            risk_score=0.3
        ).model_dump()
        
    except GuardrailViolation as e:
        return ToolCallResponse(success=False, message=str(e)).model_dump()
    except Exception as e:
        logger.exception(f"add_formation_plugs failed: {e}")
        return ToolCallResponse(
            success=False,
            message=f"Failed to add formation plugs: {str(e)}"
        ).model_dump()


def execute_add_plug(
    type: str,
    top_ft: float,
    bottom_ft: float,
    custom_sacks: Optional[int],
    cement_class: Optional[str],
    placement_reason: str,
    thread: ChatThread,
    user,
    allow_plan_changes: bool = False
) -> Dict[str, Any]:
    """
    Add a new plug/step to the plan at specified depth.
    
    Performs:
    1. Validates plug type and depths
    2. Creates step structure
    3. Calculates materials (unless custom_sacks provided)
    4. Inserts at correct position (sorted by depth)
    5. Renumbers all steps
    6. Recalculates materials_totals
    7. Creates new snapshot
    """
    logger.info(f"🚨 execute_add_plug CALLED: type={type}, top_ft={top_ft}, bottom_ft={bottom_ft}, MATERIALS_AVAILABLE={MATERIALS_AVAILABLE}")
    try:
        # Check guardrails
        if not allow_plan_changes:
            raise GuardrailViolation(
                "plan_changes_disabled",
                "Plan modifications disabled. Set allow_plan_changes=true to enable."
            )
        
        source_plan = thread.current_plan
        plan_payload = source_plan.payload.copy()
        well_geometry = plan_payload.get('well_geometry', {})
        
        # If custom_sacks is provided but bottom_ft is invalid or same as top_ft,
        # we need to calculate bottom_ft from the sack count
        auto_calculate_bottom = False
        if custom_sacks is not None and top_ft == bottom_ft:
            auto_calculate_bottom = True
            logger.info(f"add_plug: Will auto-calculate bottom_ft from {custom_sacks} sacks starting at top_ft={top_ft}")
        
        # If we're auto-calculating bottom, calculate it now
        if auto_calculate_bottom:
            try:
                # Get geometry
                casing_strings = well_geometry.get('casing_strings', [])
                prod_casing = next((c for c in casing_strings if c.get('string') == 'production'), None)
                casing_id = prod_casing.get('size_in') if prod_casing else 4.778
                
                tubing_list = well_geometry.get('tubing', [])
                stinger_od = tubing_list[0].get('size_in') if tubing_list and isinstance(tubing_list, list) else 2.375
                
                # Calculate annular capacity
                ann_cap = annulus_capacity_bbl_per_ft(float(casing_id), float(stinger_od))
                
                # Determine cement class for this depth
                if not cement_class:
                    cement_class = 'H' if top_ft > 3000 else 'C'
                
                # Get recipe
                recipe = SlurryRecipe(
                    recipe_id=f"class_{cement_class.lower()}_neat",
                    cement_class=cement_class,
                    density_ppg=15.8 if cement_class == 'H' else 15.6,
                    yield_ft3_per_sk=1.18,
                    water_gal_per_sk=5.2,
                    additives=[]
                )
                
                # Calculate total volume from sacks
                bbl_per_sk = recipe.yield_ft3_per_sk / 5.615  # Convert ft³ to bbl
                total_bbl = custom_sacks * bbl_per_sk
                
                # Calculate required interval (with 40% excess)
                excess = 0.4
                ann_cap_with_excess = ann_cap * (1.0 + excess)
                required_interval_ft = total_bbl / ann_cap_with_excess
                
                # Calculate bottom_ft (deeper = larger number)
                # Cement below retainer means bottom_ft > top_ft
                bottom_ft = top_ft + required_interval_ft
                
                # Validate against wellbore limits
                prod_shoe = prod_casing.get('bottom_ft') if prod_casing else None
                if prod_shoe and bottom_ft > prod_shoe:
                    logger.warning(f"add_plug: Calculated bottom_ft={bottom_ft:.1f} exceeds production shoe at {prod_shoe} ft")
                    # Don't error, just warn - AI can adjust if needed
                
                logger.info(
                    f"add_plug: Calculated bottom_ft={bottom_ft:.1f} from {custom_sacks} sacks "
                    f"(interval={required_interval_ft:.1f} ft, ann_cap={ann_cap:.4f} bbl/ft, "
                    f"casing_id={casing_id}\", tubing_od={stinger_od}\")"
                )
                
            except Exception as e:
                logger.exception(f"add_plug: Failed to auto-calculate bottom_ft: {e}")
                return ToolCallResponse(
                    success=False,
                    message=f"Cannot calculate plug interval from {custom_sacks} sacks: {str(e)}"
                ).model_dump()
        
        # Validate depths (in oil & gas, top_ft is shallower so should be <= bottom_ft)
        if top_ft > bottom_ft:
            return ToolCallResponse(
                success=False,
                message=f"Invalid depths: top_ft ({top_ft}) must be <= bottom_ft ({bottom_ft}). Remember: top_ft is the shallower depth (smaller number), bottom_ft is the deeper depth (larger number)."
            ).model_dump()
        
        # Determine default cement class if not provided
        if not cement_class:
            cement_class = 'H' if bottom_ft > 3000 else 'C'
        
        # Create new step structure
        new_step = {
            'type': type,
            'top_ft': top_ft,
            'bottom_ft': bottom_ft,
            'top': top_ft,
            'base': bottom_ft,
            'details': {
                'user_added': True,
                'placement_reason': placement_reason,
                'cement_class': cement_class
            },
            'regulatory_basis': [],
            'special_instructions': None
        }
        
        # Special handling for perforate_and_squeeze_plug: set up proper structure
        if type == 'perforate_and_squeeze_plug':
            interval_length = abs(top_ft - bottom_ft)
            
            # For perf & squeeze: perforate entire interval + 50 ft cap above
            # Perforation interval = the specified interval
            perf_bottom = bottom_ft
            perf_top = top_ft
            
            # Cap goes above the perforation interval
            cap_bottom = perf_top
            cap_top = cap_bottom + 50
            
            new_step['total_top_ft'] = cap_top
            new_step['total_bottom_ft'] = perf_bottom
            new_step['requires_perforation'] = True
            new_step['details']['perforation_interval'] = {
                'top_ft': perf_top,
                'bottom_ft': perf_bottom,
                'length_ft': interval_length
            }
            new_step['details']['cement_cap_inside_casing'] = {
                'top_ft': cap_top,
                'bottom_ft': cap_bottom,
                'height_ft': 50
            }
            
            # Add regulatory basis
            if 'tx.tac.16.3.14(g)(2)' not in new_step['regulatory_basis']:
                new_step['regulatory_basis'].append('tx.tac.16.3.14(g)(2)')
            
            logger.info(
                f"add_plug: Set up perforate_and_squeeze_plug structure - "
                f"perfs {perf_top}-{perf_bottom} ft, cap {cap_top}-{cap_bottom} ft"
            )
        
        # Special handling for perf_and_circulate_to_surface: annulus circulation operation
        if type == 'perf_and_circulate_to_surface':
            # This is an annulus fill from shoe to surface (not inside-casing)
            # Set up structure for annulus circulation
            new_step['name'] = "Cement Surface Plug (Perforate and Circulate)"
            new_step['perforation_depth_ft'] = bottom_ft  # Perforate at shoe depth
            new_step['geometry_context'] = 'annulus_circulation'
            
            # Determine which strings (will be populated from well_geometry during materials calc)
            new_step['details']['method'] = 'perforate_and_circulate'
            new_step['details']['target_annulus'] = 'surface_to_intermediate'  # Default, will be refined
            new_step['details']['perforation_location'] = f"Above {bottom_ft} ft shoe"
            new_step['details']['circulation_target'] = "Returns to surface"
            
            # Add regulatory basis
            if 'tx.tac.16.3.14(e)(2)' not in new_step['regulatory_basis']:
                new_step['regulatory_basis'].append('tx.tac.16.3.14(e)(2)')
            
            logger.info(
                f"add_plug: Set up perf_and_circulate_to_surface structure - "
                f"annulus circulation from {bottom_ft}→{top_ft} ft"
            )
        
        # Calculate materials or use custom sacks
        logger.info(f"🔍 Materials calc entry: custom_sacks={custom_sacks}, type={type}, MATERIALS_AVAILABLE={MATERIALS_AVAILABLE}")
        if custom_sacks is not None:
            new_step['sacks'] = custom_sacks
            new_step['details']['materials_override'] = True
            new_step['details']['custom_sacks'] = custom_sacks
            if auto_calculate_bottom:
                new_step['details']['bottom_depth_calculated'] = True
                new_step['details']['calculation_basis'] = f"{custom_sacks} sacks in production casing annulus"
            logger.info(f"add_plug: Using custom sacks {custom_sacks} for new {type} at {top_ft}-{bottom_ft} ft")
        elif MATERIALS_AVAILABLE and type not in ('bridge_plug', 'cement_retainer'):
            # Calculate materials for cement-based plugs
            logger.info(f"🔍 ENTERING materials calculation block for type={type}")
            try:
                casing_strings = well_geometry.get('casing_strings', [])
                prod_casing = next((c for c in casing_strings if c.get('string') == 'production'), None)
                
                tubing_list = well_geometry.get('tubing', [])
                stinger_od = tubing_list[0].get('size_in') if tubing_list and isinstance(tubing_list, list) else 2.875
                
                recipe = SlurryRecipe(
                    recipe_id=f"class_{cement_class.lower()}_neat",
                    cement_class=cement_class,
                    density_ppg=15.8 if cement_class == 'H' else 15.6,
                    yield_ft3_per_sk=1.18,
                    water_gal_per_sk=5.2,
                    additives=[]
                )
                
                if type == 'perforate_and_squeeze_plug':
                    logger.info(f"🔍 PERFORATE_AND_SQUEEZE materials calc: perf_interval={new_step['details'].get('perforation_interval')}")
                    # Special calculation for perforate & squeeze using decision tree + Texas depth rule
                    perf_interval = new_step['details']['perforation_interval']
                    cap_interval = new_step['details']['cement_cap_inside_casing']
                    
                    perf_len = abs(perf_interval['top_ft'] - perf_interval['bottom_ft'])
                    cap_len = abs(cap_interval['top_ft'] - cap_interval['bottom_ft'])
                    perf_bottom = perf_interval['bottom_ft']
                    
                    # Use decision tree to determine squeeze context
                    try:
                        from apps.kernel.services.w3a_rules import _get_casing_strings_at_depth
                        facts_for_decision = {"casing_strings": casing_strings}
                        casing_context = _get_casing_strings_at_depth(facts_for_decision, perf_bottom)
                    except Exception as e:
                        logger.warning(f"add_plug: Failed to get casing context: {e}")
                        casing_context = {"context": "unknown", "count": 0}
                    
                    # Texas TAC §3.14(d)(11): 1 + 10% per 1000 ft of depth
                    depth_kft = int((perf_bottom + 999.0) / 1000.0)
                    texas_excess_factor = 1.0 + (0.10 * depth_kft)
                    
                    squeeze_context = casing_context.get("context", "unknown")
                    logger.info(f"🔍 Decision tree result: squeeze_context={squeeze_context}, depth_kft={depth_kft}, texas_excess_factor={texas_excess_factor:.2f}, casing_count={casing_context.get('count', 0)}")
                    
                    # Calculate annular capacity based on context
                    if casing_context["context"] == "annulus_squeeze":
                        # TWO STRINGS: Cement between inner OD and outer ID
                        inner = casing_context.get("inner_string", {})
                        outer = casing_context.get("outer_string", {})
                        
                        if outer.get("id_in") and inner.get("size_in"):
                            outer_id = float(outer["id_in"])
                            inner_od = float(inner["size_in"])
                            ann_cap = annulus_capacity_bbl_per_ft(outer_id, inner_od)
                            logger.info(
                                f"add_plug: Annulus squeeze - {inner['name']} {inner_od}\" OD "
                                f"inside {outer['name']} {outer_id}\" ID at {perf_bottom} ft"
                            )
                        else:
                            # Fallback: use production casing ID
                            prod_id = _get_nominal_casing_id(prod_casing.get('size_in') if prod_casing else None)
                            ann_cap = annulus_capacity_bbl_per_ft(float(prod_id), float(stinger_od))
                            logger.warning(f"add_plug: Annulus squeeze fallback to production casing ID={prod_id}\"")
                            
                    elif casing_context["context"] == "open_hole_squeeze":
                        # ONE STRING: Cement into formation (use hole diameter)
                        inner = casing_context.get("inner_string", {})
                        hole_size = prod_casing.get("hole_size_in") if prod_casing else None
                        
                        if hole_size and inner.get("size_in"):
                            inner_od = float(inner["size_in"])
                            ann_cap = annulus_capacity_bbl_per_ft(float(hole_size), inner_od)
                            logger.info(
                                f"add_plug: Open-hole squeeze - {inner['name']} {inner_od}\" OD "
                                f"in {hole_size}\" hole at {perf_bottom} ft"
                            )
                        else:
                            # Fallback: estimate hole size as casing OD + 2"
                            inner_od = float(inner.get("size_in", prod_casing.get('size_in'))) if inner.get("size_in") else float(prod_casing.get('size_in', 5.5))
                            estimated_hole = inner_od + 2.0
                            ann_cap = annulus_capacity_bbl_per_ft(estimated_hole, inner_od)
                            logger.warning(f"add_plug: Open-hole squeeze estimated hole={estimated_hole}\"")
                            
                    elif casing_context["context"] == "open_hole":
                        # NO CASING: Bare open hole - perforate & squeeze is technically invalid
                        # But handle gracefully using production casing + estimated hole size as fallback
                        logger.warning(
                            f"add_plug: No casing found at {perf_bottom} ft - perforate & squeeze may be invalid. "
                            f"Using fallback geometry for material calculation."
                        )
                        prod_od = float(prod_casing.get('size_in', 5.5)) if prod_casing else 5.5
                        hole_size = prod_casing.get("hole_size_in") if prod_casing else (prod_od + 2.0)
                        ann_cap = annulus_capacity_bbl_per_ft(float(hole_size), prod_od)
                        logger.info(f"add_plug: Using fallback - {prod_od}\" OD in estimated {hole_size}\" hole")
                        
                    else:
                        # Unknown context - use default production casing ID
                        prod_id = _get_nominal_casing_id(prod_casing.get('size_in') if prod_casing else None)
                        ann_cap = annulus_capacity_bbl_per_ft(float(prod_id), float(stinger_od))
                        logger.warning(f"add_plug: Unknown context '{casing_context.get('context')}', using default production casing ID={prod_id}\"")
                    
                    # Calculate squeeze volume with Texas depth-based excess
                    base_volume = perf_len * ann_cap
                    squeeze_bbl = base_volume * texas_excess_factor
                    
                    # Cap: use standard cased excess (0.4)
                    cap_excess = 0.4
                    cap_bbl = cap_len * ann_cap * (1.0 + cap_excess)
                    
                    total_bbl = squeeze_bbl + cap_bbl
                    vb = compute_sacks(total_bbl, recipe, rounding="up")
                    new_step['sacks'] = int(vb.sacks)
                    
                    # Store context in details
                    new_step['details']['squeeze_context'] = squeeze_context
                    new_step['details']['texas_excess_factor'] = texas_excess_factor
                    new_step['details']['depth_kft'] = depth_kft
                    
                    logger.info(
                        f"add_plug: Calculated {vb.sacks} sacks for perforate_and_squeeze_plug "
                        f"[{squeeze_context}, texas_factor={texas_excess_factor}x at {depth_kft}kft] "
                        f"(perf_len={perf_len:.1f} ft, base={base_volume:.2f} bbl, "
                        f"squeeze={squeeze_bbl:.2f} bbl + cap={cap_bbl:.2f} bbl = {total_bbl:.2f} bbl total)"
                    )
                
                elif type == 'perf_and_circulate_to_surface':
                    logger.info(f"🔍 PERF_AND_CIRCULATE_TO_SURFACE materials calc")
                    # Annulus circulation: outer casing ID vs inner casing OD
                    # Find intermediate and surface casing from well geometry
                    intermediate_casing = next((c for c in casing_strings if 'intermediate' in c.get('string', '').lower()), None)
                    surface_casing = next((c for c in casing_strings if 'surface' in c.get('string', '').lower()), None)
                    
                    if intermediate_casing and surface_casing:
                        # Get IDs for annulus calculation
                        intermediate_od = float(intermediate_casing.get('size_in', 9.625))
                        surface_id = _get_nominal_casing_id(surface_casing.get('size_in'))
                        
                        # Annulus capacity: outer ID vs inner OD
                        ann_cap = annulus_capacity_bbl_per_ft(surface_id, intermediate_od)
                        
                        interval_ft = abs(top_ft - bottom_ft)
                        
                        # Texas depth excess using bottom depth (shoe)
                        depth_kft = int((bottom_ft + 999.0) / 1000.0)
                        texas_excess_factor = 1.0 + (0.10 * depth_kft)
                        
                        # Operational top-off for circulation (5% default)
                        operational_topoff = 1.05
                        
                        # Calculate volume
                        base_volume_bbl = interval_ft * ann_cap
                        total_bbl = base_volume_bbl * texas_excess_factor * operational_topoff
                        
                        vb = compute_sacks(total_bbl, recipe, rounding="nearest")
                        
                        # Round to nearest 5 sacks for operational convenience
                        sacks_rounded = int(round(vb.sacks / 5.0) * 5)
                        new_step['sacks'] = sacks_rounded
                        
                        # Store details
                        new_step['details']['geometry_used'] = {
                            'outer_casing': 'surface',
                            'outer_casing_id_in': surface_id,
                            'inner_casing': 'intermediate',
                            'inner_casing_od_in': intermediate_od,
                            'annular_capacity_bbl_per_ft': ann_cap,
                        }
                        new_step['details']['texas_excess_factor'] = texas_excess_factor
                        new_step['details']['depth_kft'] = depth_kft
                        new_step['details']['operational_topoff'] = operational_topoff
                        new_step['outer_string'] = 'surface'
                        new_step['inner_string'] = 'intermediate'
                        new_step['outer_casing_id_in'] = surface_id
                        new_step['inner_casing_od_in'] = intermediate_od
                        
                        logger.info(
                            f"add_plug: Calculated {sacks_rounded} sacks for perf_and_circulate_to_surface "
                            f"({interval_ft:.1f} ft annulus, {surface_id:.3f}\" ID - {intermediate_od:.3f}\" OD, "
                            f"ann_cap={ann_cap:.4f} bbl/ft, texas_factor={texas_excess_factor:.2f}x, "
                            f"topoff={operational_topoff:.2f}x, total={total_bbl:.2f} bbl)"
                        )
                    else:
                        # Fallback if casing strings not found
                        logger.warning(f"add_plug: Could not find intermediate/surface casing for perf_and_circulate_to_surface")
                        new_step['sacks'] = None
                    
                else:
                    # Standard cement plug calculation
                    casing_id = prod_casing.get('size_in') if prod_casing else 4.778
                    interval_ft = abs(top_ft - bottom_ft)
                    ann_cap = annulus_capacity_bbl_per_ft(float(casing_id), float(stinger_od))
                    excess = 0.4
                    total_bbl = interval_ft * ann_cap * (1.0 + excess)
                    
                    vb = compute_sacks(total_bbl, recipe, rounding="up")
                    new_step['sacks'] = int(vb.sacks)
                    
                    new_step['details']['geometry_used'] = {
                        'casing_id_in': float(casing_id),
                        'stinger_od_in': float(stinger_od)
                    }
                    
                    logger.info(f"add_plug: Calculated {vb.sacks} sacks for new {type} at {top_ft}-{bottom_ft} ft")
                    
            except Exception as e:
                logger.error(f"❌ add_plug: Could not calculate materials: {e}", exc_info=True)
                new_step['sacks'] = None
        else:
            logger.info(f"⚠️ SKIPPING materials calc: MATERIALS_AVAILABLE={MATERIALS_AVAILABLE}, type={type}")
            new_step['sacks'] = None
        
        # Insert new step at correct position
        steps = plan_payload.get('steps', [])
        steps.append(new_step)
        steps.sort(key=lambda s: s.get('top_ft', 0), reverse=True)
        
        # Renumber all steps sequentially
        for idx, step in enumerate(steps, start=1):
            step['step_id'] = idx
        
        plan_payload['steps'] = steps
        
        # Recalculate materials_totals
        total_sacks = sum(s.get('sacks', 0) for s in steps if s.get('sacks') is not None)
        plan_payload['materials_totals'] = {
            'total_sacks': total_sacks,
            'note': f'Recalculated after adding {type}'
        }
        
        # Create new PlanSnapshot
        new_snapshot = PlanSnapshot.objects.create(
            tenant_id=thread.tenant_id,
            well=source_plan.well,
            plan_id=source_plan.plan_id,
            kind='post_edit',
            status=PlanSnapshot.STATUS_DRAFT,
            payload=plan_payload,
            kernel_version=source_plan.kernel_version,
            policy_id=source_plan.policy_id,
            extraction_meta=source_plan.extraction_meta
        )
        
        # Create PlanModification record
        modification = PlanModification.objects.create(
            source_snapshot=source_plan,
            result_snapshot=new_snapshot,
            op_type='add_step',
            description=f"Added {type} at {top_ft}-{bottom_ft} ft: {placement_reason}",
            operation_payload={
                'type': type,
                'top_ft': top_ft,
                'bottom_ft': bottom_ft,
                'custom_sacks': custom_sacks,
                'cement_class': cement_class,
                'placement_reason': placement_reason
            },
            diff={'added_step': new_step},
            risk_score=0.3,
            chat_thread=thread,
            applied_by=user if user else None
        )
        
        # Update thread's current plan
        thread.current_plan = new_snapshot
        thread.save()
        
        new_step_id = new_step.get('step_id')
        
        logger.info(f"✅ execute_add_plug COMPLETE: step_id={new_step_id}, sacks={new_step.get('sacks')}, snapshot_id={new_snapshot.id}")
        
        return ToolCallResponse(
            success=True,
            message=f"Successfully added {type} at {top_ft}-{bottom_ft} ft. Plan now has {len(steps)} steps.",
            data={
                'new_step_id': new_step_id,
                'type': type,
                'top_ft': top_ft,
                'bottom_ft': bottom_ft,
                'sacks': new_step.get('sacks'),
                'total_steps': len(steps),
                'total_sacks': total_sacks,
                'new_snapshot_id': new_snapshot.id,
                'modification_id': modification.id
            },
            risk_score=0.3,
            violations_delta=[]
        ).model_dump()
    
    except GuardrailViolation as e:
        logger.warning(f"Guardrail violation in add_plug: {e}")
        return ToolCallResponse(
            success=False,
            message=str(e)
        ).model_dump()
    
    except Exception as e:
        logger.exception(f"Error in add_plug: {e}")
        return ToolCallResponse(
            success=False,
            message=f"Error adding plug: {str(e)}"
        ).model_dump()


def execute_override_materials(
    step_id: int,
    sacks: int,
    reason: str,
    thread: ChatThread,
    user,
    allow_plan_changes: bool = False
) -> Dict[str, Any]:
    """
    Override calculated materials with custom sack count for a specific step.
    """
    try:
        if not allow_plan_changes:
            raise GuardrailViolation(
                "plan_changes_disabled",
                "Plan modifications disabled. Set allow_plan_changes=true to enable."
            )
        
        if sacks <= 0:
            return ToolCallResponse(
                success=False,
                message=f"Sacks must be positive (got {sacks})"
            ).model_dump()
        
        source_plan = thread.current_plan
        plan_payload = source_plan.payload.copy()
        
        steps = plan_payload.get('steps', [])
        target_step = next((s for s in steps if s.get('step_id') == step_id), None)
        
        if not target_step:
            return ToolCallResponse(
                success=False,
                message=f"Step {step_id} not found in plan"
            ).model_dump()
        
        original_sacks = target_step.get('sacks')
        
        target_step['sacks'] = sacks
        if 'details' not in target_step:
            target_step['details'] = {}
        target_step['details']['materials_override'] = True
        target_step['details']['original_sacks'] = original_sacks
        target_step['details']['override_reason'] = reason
        
        total_sacks = sum(s.get('sacks', 0) for s in steps if s.get('sacks') is not None)
        plan_payload['materials_totals'] = {
            'total_sacks': total_sacks,
            'note': f'Includes material override for step {step_id}'
        }
        
        new_snapshot = PlanSnapshot.objects.create(
            tenant_id=thread.tenant_id,
            well=source_plan.well,
            plan_id=source_plan.plan_id,
            kind='post_edit',
            status=PlanSnapshot.STATUS_DRAFT,
            payload=plan_payload,
            kernel_version=source_plan.kernel_version,
            policy_id=source_plan.policy_id,
            extraction_meta=source_plan.extraction_meta
        )
        
        modification = PlanModification.objects.create(
            source_snapshot=source_plan,
            result_snapshot=new_snapshot,
            op_type='override_materials',
            description=f"Overrode step {step_id} materials to {sacks} sacks: {reason}",
            operation_payload={
                'step_id': step_id,
                'original_sacks': original_sacks,
                'new_sacks': sacks,
                'reason': reason
            },
            diff={'step_id': step_id, 'original_sacks': original_sacks, 'new_sacks': sacks},
            risk_score=0.1,
            chat_thread=thread,
            applied_by=user if user else None
        )
        
        thread.current_plan = new_snapshot
        thread.save()
        
        delta_sacks = sacks - (original_sacks or 0)
        delta_str = f"+{delta_sacks}" if delta_sacks > 0 else str(delta_sacks)
        
        return ToolCallResponse(
            success=True,
            message=f"Successfully overrode step {step_id} materials from {original_sacks} to {sacks} sacks ({delta_str}). Total plan: {total_sacks} sacks.",
            data={
                'step_id': step_id,
                'original_sacks': original_sacks,
                'new_sacks': sacks,
                'delta_sacks': delta_sacks,
                'total_sacks': total_sacks,
                'new_snapshot_id': new_snapshot.id,
                'modification_id': modification.id
            },
            risk_score=0.1,
            violations_delta=[]
        ).model_dump()
    
    except GuardrailViolation as e:
        logger.warning(f"Guardrail violation in override_materials: {e}")
        return ToolCallResponse(
            success=False,
            message=str(e)
        ).model_dump()
    
    except Exception as e:
        logger.exception(f"Error in override_materials: {e}")
        return ToolCallResponse(
            success=False,
            message=f"Error overriding materials: {str(e)}"
        ).model_dump()
