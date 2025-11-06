"""
Generate detailed JSON patch diffs for plan version visualization.

Produces:
- Precomputed JSON patches (RFC 6902)
- Highlighted step-by-step changes
- Human-readable summaries
- UI-friendly visualization data
"""

import logging
import json
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass
import jsonpatch

logger = logging.getLogger(__name__)


@dataclass
class StepDiff:
    """Represents a change to a single step."""
    step_id: int
    change_type: str  # 'added', 'removed', 'modified', 'unchanged'
    field_changes: List[Dict[str, Any]]
    summary: str


@dataclass
class PlanDiff:
    """Complete plan diff with visualization data."""
    json_patch: List[Dict[str, Any]]  # RFC 6902 JSON Patch
    step_diffs: List[StepDiff]
    summary: Dict[str, Any]
    human_readable: str


def generate_json_patch(
    source_payload: Dict[str, Any],
    target_payload: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Generate RFC 6902 JSON Patch between two plan payloads.
    
    Example output:
    [
        {"op": "remove", "path": "/steps/5"},
        {"op": "remove", "path": "/steps/11"},
        {"op": "replace", "path": "/materials_totals/total_sacks", "value": 250},
        {"op": "add", "path": "/steps/5/note", "value": "Combined with step 11"}
    ]
    """
    patch = jsonpatch.make_patch(source_payload, target_payload)
    return list(patch)


def categorize_step_changes(
    source_steps: List[Dict[str, Any]],
    target_steps: List[Dict[str, Any]]
) -> List[StepDiff]:
    """
    Categorize changes to individual steps for UI visualization.
    
    Returns list of StepDiff objects showing what changed in each step.
    """
    step_diffs = []
    
    # Index steps by step_id for comparison
    source_by_id = {step.get('step_id'): step for step in source_steps}
    target_by_id = {step.get('step_id'): step for step in target_steps}
    
    # Find added, removed, modified
    source_ids = set(source_by_id.keys())
    target_ids = set(target_by_id.keys())
    
    added_ids = target_ids - source_ids
    removed_ids = source_ids - target_ids
    common_ids = source_ids & target_ids
    
    # Process removed steps
    for step_id in sorted(removed_ids):
        step = source_by_id[step_id]
        step_diffs.append(StepDiff(
            step_id=step_id,
            change_type='removed',
            field_changes=[],
            summary=f"Step {step_id} removed: {step.get('type', 'unknown')} at {step.get('top_ft', '?')}-{step.get('bottom_ft', '?')} ft"
        ))
    
    # Process added steps
    for step_id in sorted(added_ids):
        step = target_by_id[step_id]
        step_diffs.append(StepDiff(
            step_id=step_id,
            change_type='added',
            field_changes=[],
            summary=f"Step {step_id} added: {step.get('type', 'unknown')} at {step.get('top_ft', '?')}-{step.get('bottom_ft', '?')} ft"
        ))
    
    # Process modified steps
    for step_id in sorted(common_ids):
        source_step = source_by_id[step_id]
        target_step = target_by_id[step_id]
        
        field_changes = []
        
        # Compare all fields
        all_keys = set(source_step.keys()) | set(target_step.keys())
        for key in all_keys:
            source_val = source_step.get(key)
            target_val = target_step.get(key)
            
            if source_val != target_val:
                field_changes.append({
                    'field': key,
                    'old_value': source_val,
                    'new_value': target_val
                })
        
        if field_changes:
            summary = f"Step {step_id} modified: {', '.join(f'{c['field']}' for c in field_changes[:3])}"
            step_diffs.append(StepDiff(
                step_id=step_id,
                change_type='modified',
                field_changes=field_changes,
                summary=summary
            ))
        else:
            step_diffs.append(StepDiff(
                step_id=step_id,
                change_type='unchanged',
                field_changes=[],
                summary=f"Step {step_id} unchanged"
            ))
    
    return step_diffs


def generate_human_readable_summary(
    source_payload: Dict[str, Any],
    target_payload: Dict[str, Any],
    step_diffs: List[StepDiff]
) -> str:
    """
    Generate human-readable summary of changes.
    
    Example:
    "Combined 2 formation top plugs (steps 5, 11) → saved 150 sacks cement.
     Changed CIBP at 11,200 ft from 9-5/8" to bridge plug.
     Removed 1 violation: Formation top coverage (tx.tac.16.3.14(g)(1))."
    """
    summary_lines = []
    
    # Step changes
    added = [d for d in step_diffs if d.change_type == 'added']
    removed = [d for d in step_diffs if d.change_type == 'removed']
    modified = [d for d in step_diffs if d.change_type == 'modified']
    
    if removed:
        summary_lines.append(f"Removed {len(removed)} step(s): {', '.join(str(d.step_id) for d in removed[:3])}")
    
    if added:
        summary_lines.append(f"Added {len(added)} step(s): {', '.join(str(d.step_id) for d in added[:3])}")
    
    if modified:
        summary_lines.append(f"Modified {len(modified)} step(s)")
    
    # Materials change
    source_sacks = source_payload.get('materials_totals', {}).get('total_sacks', 0)
    target_sacks = target_payload.get('materials_totals', {}).get('total_sacks', 0)
    sacks_delta = target_sacks - source_sacks
    
    if sacks_delta != 0:
        summary_lines.append(
            f"Materials: {abs(sacks_delta)} sacks {'saved' if sacks_delta < 0 else 'added'} "
            f"({source_sacks} → {target_sacks})"
        )
    
    # Violations change
    source_violations = len(source_payload.get('violations', []))
    target_violations = len(target_payload.get('violations', []))
    violations_delta = target_violations - source_violations
    
    if violations_delta != 0:
        summary_lines.append(
            f"Violations: {abs(violations_delta)} {'removed' if violations_delta < 0 else 'added'} "
            f"({source_violations} → {target_violations})"
        )
    
    return ". ".join(summary_lines) + "."


def generate_plan_diff(
    source_payload: Dict[str, Any],
    target_payload: Dict[str, Any]
) -> PlanDiff:
    """
    Generate comprehensive diff between two plan versions.
    
    Returns:
        PlanDiff with JSON patch, step diffs, summary, and human-readable text
    """
    # Generate JSON patch
    json_patch = generate_json_patch(source_payload, target_payload)
    
    # Categorize step changes
    source_steps = source_payload.get('steps', [])
    target_steps = target_payload.get('steps', [])
    step_diffs = categorize_step_changes(source_steps, target_steps)
    
    # Generate summary stats
    summary = {
        "steps_added": len([d for d in step_diffs if d.change_type == 'added']),
        "steps_removed": len([d for d in step_diffs if d.change_type == 'removed']),
        "steps_modified": len([d for d in step_diffs if d.change_type == 'modified']),
        "steps_unchanged": len([d for d in step_diffs if d.change_type == 'unchanged']),
        "materials_delta": (
            target_payload.get('materials_totals', {}).get('total_sacks', 0) -
            source_payload.get('materials_totals', {}).get('total_sacks', 0)
        ),
        "violations_delta": (
            len(target_payload.get('violations', [])) -
            len(source_payload.get('violations', []))
        ),
        "json_patch_ops": len(json_patch),
    }
    
    # Generate human-readable text
    human_readable = generate_human_readable_summary(
        source_payload, target_payload, step_diffs
    )
    
    return PlanDiff(
        json_patch=json_patch,
        step_diffs=step_diffs,
        summary=summary,
        human_readable=human_readable
    )


def generate_visualization_data(diff: PlanDiff) -> Dict[str, Any]:
    """
    Generate UI-friendly visualization data.
    
    Returns:
        {
            "json_patch": [...],
            "steps": [
                {
                    "step_id": 5,
                    "change_type": "removed",
                    "highlight_color": "#ff4444",
                    "summary": "Step 5 removed: cement_plug at 6500-6550 ft",
                    "field_changes": []
                },
                ...
            ],
            "summary": {
                "steps_added": 0,
                "steps_removed": 2,
                "steps_modified": 1,
                "materials_delta": -150,
                "violations_delta": -1,
                "human_readable": "..."
            }
        }
    """
    # Map change types to UI colors
    color_map = {
        'added': '#44ff44',      # Green
        'removed': '#ff4444',    # Red
        'modified': '#ffaa44',   # Orange
        'unchanged': '#dddddd'   # Gray
    }
    
    steps_viz = []
    for step_diff in diff.step_diffs:
        steps_viz.append({
            "step_id": step_diff.step_id,
            "change_type": step_diff.change_type,
            "highlight_color": color_map.get(step_diff.change_type, '#dddddd'),
            "summary": step_diff.summary,
            "field_changes": step_diff.field_changes
        })
    
    return {
        "json_patch": diff.json_patch,
        "steps": steps_viz,
        "summary": {
            **diff.summary,
            "human_readable": diff.human_readable
        }
    }


def compare_plan_versions(
    plan_id: str,
    version_a_id: int,
    version_b_id: int
) -> Dict[str, Any]:
    """
    Compare two versions of a plan and return visualization data.
    
    Args:
        plan_id: Plan ID
        version_a_id: PlanSnapshot ID for version A (older)
        version_b_id: PlanSnapshot ID for version B (newer)
    
    Returns:
        Diff visualization data
    """
    from apps.public_core.models import PlanSnapshot
    
    version_a = PlanSnapshot.objects.get(id=version_a_id, plan_id=plan_id)
    version_b = PlanSnapshot.objects.get(id=version_b_id, plan_id=plan_id)
    
    # Generate diff
    diff = generate_plan_diff(version_a.payload, version_b.payload)
    
    # Generate visualization data
    viz_data = generate_visualization_data(diff)
    
    # Add version metadata
    viz_data['version_a'] = {
        "id": version_a.id,
        "kind": version_a.kind,
        "status": version_a.status,
        "created_at": version_a.created_at.isoformat()
    }
    viz_data['version_b'] = {
        "id": version_b.id,
        "kind": version_b.kind,
        "status": version_b.status,
        "created_at": version_b.created_at.isoformat()
    }
    
    logger.info(
        f"Generated diff for plan {plan_id}: "
        f"{diff.summary['steps_removed']} removed, "
        f"{diff.summary['steps_added']} added, "
        f"{diff.summary['steps_modified']} modified"
    )
    
    return viz_data

