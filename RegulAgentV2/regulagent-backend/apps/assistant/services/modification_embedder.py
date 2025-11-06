"""
Generate rich embeddings for plan modifications with contextual metadata.

Enables targeted similarity queries by:
- Regulatory sections (tx.tac.16.3.14(g)(1))
- Plug types (surface, intermediate, production, bridge_plug)
- Geological context (formations, depth ranges)
- Operator/field/district
- Outcomes (risk, violations, approval)
"""

import logging
from typing import Dict, Any, List, Optional
from apps.public_core.models import DocumentVector
from apps.assistant.models import PlanModification

logger = logging.getLogger(__name__)


def extract_regulatory_sections(payload: Dict[str, Any]) -> List[str]:
    """
    Extract all regulatory citations from plan steps.
    
    Returns: ["tx.tac.16.3.14(g)(1)", "tx.tac.16.3.14(e)(2)", ...]
    """
    sections = set()
    
    for step in payload.get('steps', []):
        regulatory_basis = step.get('regulatory_basis', [])
        for rule in regulatory_basis:
            # Extract TAC sections
            if 'tx.tac' in rule:
                sections.add(rule)
            # Extract district rules
            elif 'rrc.district' in rule:
                sections.add(rule)
    
    return sorted(list(sections))


def extract_step_types(payload: Dict[str, Any]) -> Dict[str, int]:
    """
    Count step types in the plan.
    
    Returns: {
        "cement_plug": 5,
        "bridge_plug": 1,
        "surface_casing_shoe_plug": 1,
        ...
    }
    """
    step_types = {}
    
    for step in payload.get('steps', []):
        step_type = step.get('type', 'unknown')
        step_types[step_type] = step_types.get(step_type, 0) + 1
    
    return step_types


def extract_depth_ranges(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract depth statistics from plan.
    
    Returns: {
        "min_depth_ft": 0,
        "max_depth_ft": 11200,
        "avg_depth_ft": 5600,
        "depth_bins": {
            "shallow": 2,     # 0-2000 ft
            "intermediate": 3, # 2000-7000 ft
            "deep": 4         # >7000 ft
        }
    }
    """
    depths = []
    
    for step in payload.get('steps', []):
        top = step.get('top_ft')
        bottom = step.get('bottom_ft')
        if top is not None:
            depths.append(top)
        if bottom is not None:
            depths.append(bottom)
    
    if not depths:
        return {}
    
    min_depth = min(depths)
    max_depth = max(depths)
    avg_depth = sum(depths) / len(depths)
    
    # Bin depths
    shallow = sum(1 for d in depths if d < 2000)
    intermediate = sum(1 for d in depths if 2000 <= d < 7000)
    deep = sum(1 for d in depths if d >= 7000)
    
    return {
        "min_depth_ft": min_depth,
        "max_depth_ft": max_depth,
        "avg_depth_ft": round(avg_depth, 1),
        "depth_bins": {
            "shallow": shallow,
            "intermediate": intermediate,
            "deep": deep
        }
    }


def build_modification_metadata(
    modification: PlanModification,
    regulator_outcome: Optional[Dict[str, Any]] = None,
    tenant_policy: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Build rich metadata for modification embedding.
    
    This metadata enables targeted similarity queries like:
    - "Find modifications that combined plugs in District 08A"
    - "Find modifications using tx.tac.16.3.14(g)(1) that were approved"
    - "Find modifications affecting surface casing that reduced materials"
    
    Includes tenant policy context to enable learning by risk profile:
    - Conservative tenants: Learn what works for low-risk appetite
    - Aggressive tenants: Learn what works for high-risk appetite
    - Tenant variance is SIGNAL, not noise
    """
    source_payload = modification.source_snapshot.payload
    result_payload = modification.result_snapshot.payload if modification.result_snapshot else {}
    well = modification.source_snapshot.well
    
    # Get tenant policy for context (if not provided)
    if not tenant_policy and modification.chat_thread:
        try:
            from apps.tenant_overlay.models.tenant_guardrail_policy import TenantGuardrailPolicy
            tenant_policy_obj = TenantGuardrailPolicy.get_for_tenant(modification.chat_thread.tenant_id)
            tenant_policy = tenant_policy_obj.to_metadata_dict()
        except Exception:
            tenant_policy = None
    
    # Extract contextual dimensions
    regulatory_sections = extract_regulatory_sections(result_payload)
    step_types = extract_step_types(result_payload)
    depth_ranges = extract_depth_ranges(result_payload)
    
    # Materials impact
    source_sacks = source_payload.get('materials_totals', {}).get('total_sacks', 0)
    result_sacks = result_payload.get('materials_totals', {}).get('total_sacks', 0)
    materials_delta = result_sacks - source_sacks
    materials_delta_percent = (materials_delta / source_sacks * 100) if source_sacks > 0 else 0
    
    # Violations impact
    source_violations = len(source_payload.get('violations', []))
    result_violations = len(result_payload.get('violations', []))
    violations_delta = result_violations - source_violations
    
    # Formation context
    formations_targeted = result_payload.get('formations_targeted', [])
    formations_detected = result_payload.get('formation_tops_detected', [])
    
    # Build comprehensive metadata
    metadata = {
        # Document type
        "type": "plan_modification",
        "modification_id": modification.id,
        
        # Tenant context
        "tenant_id": str(modification.chat_thread.tenant_id) if modification.chat_thread else None,
        
        # Tenant policy context (NEW - enables learning by risk profile)
        "tenant_policy": tenant_policy if tenant_policy else {
            "risk_profile": "unknown",
            "risk_threshold": 0.5,
            "max_material_delta": 0.3,
            "allow_new_violations": False,
        },
        
        # Well context
        "well_context": {
            "api": well.api14,
            "operator": well.operator_name,
            "field": well.field_name,
            "county": well.county,
            "state": well.state,
            "lat": float(well.lat) if well.lat else None,
            "lon": float(well.lon) if well.lon else None,
        },
        
        # District/jurisdiction
        "district": result_payload.get('district'),
        "jurisdiction": result_payload.get('jurisdiction', 'TX'),
        
        # Operation details
        "operation": {
            "type": modification.op_type,
            "description": modification.description,
            "risk_score": modification.risk_score,
        },
        
        # Regulatory context (NEW)
        "regulatory": {
            "sections_cited": regulatory_sections,
            "primary_section": regulatory_sections[0] if regulatory_sections else None,
            "section_count": len(regulatory_sections),
        },
        
        # Step context (NEW)
        "steps": {
            "types": step_types,
            "types_affected": list(modification.operation_payload.get('step_types', [])),
            "count_before": len(source_payload.get('steps', [])),
            "count_after": len(result_payload.get('steps', [])),
            "count_delta": len(result_payload.get('steps', [])) - len(source_payload.get('steps', [])),
        },
        
        # Depth context (NEW)
        "depths": depth_ranges,
        
        # Geological context (NEW)
        "formations": {
            "targeted": formations_targeted,
            "detected": formations_detected,
            "count": len(formations_targeted),
        },
        
        # Materials impact
        "materials": {
            "sacks_before": source_sacks,
            "sacks_after": result_sacks,
            "sacks_delta": materials_delta,
            "delta_percent": round(materials_delta_percent, 1),
        },
        
        # Violations impact
        "violations": {
            "count_before": source_violations,
            "count_after": result_violations,
            "delta": violations_delta,
            "introduced_new": violations_delta > 0,
        },
        
        # Outcome (learning)
        "outcome": {
            "user_accepted": modification.is_applied,
            "user_reverted": modification.is_reverted,
            "regulator_accepted": regulator_outcome.get('approved') if regulator_outcome else None,
            "regulator_status": regulator_outcome.get('status') if regulator_outcome else None,
            "confidence": regulator_outcome.get('confidence', 0.5) if regulator_outcome else 0.5,
        },
        
        # Timestamps
        "created_at": modification.created_at.isoformat(),
        "applied_at": modification.applied_at.isoformat() if modification.applied_at else None,
    }
    
    return metadata


def generate_embedding_text(
    modification: PlanModification,
    metadata: Dict[str, Any]
) -> str:
    """
    Generate rich text for embedding that captures all contextual dimensions.
    """
    well_ctx = metadata['well_context']
    operation = metadata['operation']
    regulatory = metadata['regulatory']
    steps = metadata['steps']
    materials = metadata['materials']
    violations = metadata['violations']
    formations = metadata['formations']
    outcome = metadata['outcome']
    
    # Build comprehensive description
    text = f"""
Plan Modification: {operation['type']}

Description: {operation['description']}

Well Context:
- API: {well_ctx['api']}
- Operator: {well_ctx['operator']}
- Field: {well_ctx['field']}
- Location: {well_ctx['county']} County, District {metadata['district']}

Regulatory Citations:
{chr(10).join(f"- {section}" for section in regulatory['sections_cited'][:5])}

Step Types Involved:
{chr(10).join(f"- {step_type}: {count}" for step_type, count in steps['types'].items())}

Steps Changed:
- Before: {steps['count_before']} steps
- After: {steps['count_after']} steps
- Delta: {steps['count_delta']}

Geological Context:
- Formations: {', '.join(formations['targeted'])}

Materials Impact:
- Sacks: {materials['sacks_before']} → {materials['sacks_after']} ({materials['delta_percent']:+.1f}%)
- Delta: {materials['sacks_delta']:+d} sacks

Violations:
- Before: {violations['count_before']}
- After: {violations['count_after']}
- New violations: {'Yes' if violations['introduced_new'] else 'No'}

Risk Assessment:
- Risk Score: {operation['risk_score']:.2f}

Outcome:
- User accepted: {'Yes' if outcome['user_accepted'] else 'No'}
- User reverted: {'Yes' if outcome['user_reverted'] else 'No'}
- Regulator approved: {outcome['regulator_accepted'] if outcome['regulator_accepted'] is not None else 'Pending'}
- Confidence: {outcome['confidence']:.2f}

Rationale:
{modification.description}
"""
    
    return text.strip()


async def embed_modification_with_rich_metadata(
    modification: PlanModification,
    regulator_outcome: Optional[Dict[str, Any]] = None
) -> DocumentVector:
    """
    Generate and store embedding with rich contextual metadata.
    
    Args:
        modification: PlanModification instance
        regulator_outcome: Optional RRC outcome data
            {
                "approved": bool,
                "status": "approved" | "rejected" | "revision_requested",
                "confidence": float,  # 0.0-1.0
                "reviewer_notes": str
            }
    
    Returns:
        DocumentVector instance
    """
    # Build rich metadata
    metadata = build_modification_metadata(modification, regulator_outcome)
    
    # Generate embedding text
    embedding_text = generate_embedding_text(modification, metadata)
    
    # TODO: Generate embedding with OpenAI
    # from openai import OpenAI
    # client = OpenAI()
    # response = client.embeddings.create(
    #     input=embedding_text,
    #     model="text-embedding-3-small"
    # )
    # vector = response.data[0].embedding
    
    # Placeholder: Create without actual embedding for now
    # doc_vector = DocumentVector.objects.create(
    #     vector=vector,
    #     metadata=metadata
    # )
    
    logger.info(
        f"Generated rich metadata for modification {modification.id}: "
        f"{len(metadata['regulatory']['sections_cited'])} regulatory sections, "
        f"{len(metadata['formations']['targeted'])} formations, "
        f"risk {metadata['operation']['risk_score']:.2f}"
    )
    
    # Return metadata for now (TODO: return DocumentVector when implemented)
    return metadata


def query_similar_modifications(
    query_context: Dict[str, Any],
    filters: Optional[Dict[str, Any]] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Query similar modifications with rich filtering.
    
    Args:
        query_context: Context for similarity search
            {
                "operation_type": "combine_plugs",
                "district": "08A",
                "formations": ["Dean", "Lo"],
                "depth_range": [6000, 10000]
            }
        filters: Optional metadata filters
            {
                "regulatory.primary_section": "tx.tac.16.3.14(g)(1)",
                "outcome.regulator_accepted": True,
                "steps.types.cement_plug": {"$gte": 2}
            }
        limit: Max results
    
    Returns:
        List of similar modifications with metadata
    """
    # TODO: Implement vector search with pgvector
    # query_embedding = generate_embedding(query_context)
    # 
    # similar = DocumentVector.objects.filter(
    #     metadata__type='plan_modification',
    #     metadata__district=filters.get('district'),
    #     metadata__outcome__regulator_accepted=True,  # Filter to approved only
    #     ...
    # ).annotate(
    #     similarity=CosineDistance('vector', query_embedding)
    # ).order_by('similarity')[:limit]
    
    logger.info(
        f"Querying similar modifications: {query_context.get('operation_type')} "
        f"in {query_context.get('district')}"
    )
    
    return []  # Placeholder

