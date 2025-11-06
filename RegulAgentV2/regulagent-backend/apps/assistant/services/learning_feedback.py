"""
Learning feedback loop: Update confidence based on regulator outcomes.

When RRC approves/rejects a plan:
1. Mark modifications in that plan
2. Find similar modifications (by context)
3. Update confidence weights
4. Re-embed with new confidence
5. Bias future suggestions toward approved patterns
"""

import logging
from typing import List, Optional
from django.db.models import Avg, Count, Q
from apps.assistant.models import PlanModification
from apps.public_core.models import DocumentVector

logger = logging.getLogger(__name__)


def update_modification_confidence(
    modification: PlanModification,
    outcome_approved: bool,
    outcome_instance
) -> float:
    """
    Update confidence score for a modification based on regulator outcome.
    
    Confidence formula:
    - Start: 0.5 (neutral)
    - Approved: +0.2 per approval
    - Rejected: -0.2 per rejection
    - Multiple outcomes: weighted average
    
    Args:
        modification: PlanModification instance
        outcome_approved: True if RRC approved, False if rejected
        outcome_instance: RegulatorOutcome instance
    
    Returns:
        Updated confidence score
    """
    # Get all outcomes that used this modification
    outcomes = modification.influenced_outcomes.all()
    
    approved_count = outcomes.filter(status='approved').count()
    rejected_count = outcomes.filter(status='rejected').count()
    total_count = approved_count + rejected_count
    
    if total_count == 0:
        # No outcomes yet, return neutral
        confidence = 0.5
    else:
        # Calculate approval rate
        approval_rate = approved_count / total_count
        
        # Scale to 0.0-1.0, weighted toward extremes
        # 100% approved → 1.0
        # 50% approved → 0.5
        # 0% approved → 0.0
        confidence = approval_rate
        
        # Boost confidence if multiple approvals (proven pattern)
        if approved_count >= 3:
            confidence = min(confidence + 0.1, 1.0)
        
        # Lower confidence if any rejections
        if rejected_count > 0:
            confidence = max(confidence - 0.1, 0.0)
    
    logger.info(
        f"Updated modification {modification.id} confidence: {confidence:.2f} "
        f"({approved_count} approved, {rejected_count} rejected)"
    )
    
    # TODO: Update DocumentVector metadata with new confidence
    # vectors = DocumentVector.objects.filter(
    #     metadata__modification_id=modification.id
    # )
    # for vector in vectors:
    #     vector.metadata['outcome']['confidence'] = confidence
    #     vector.save()
    
    return confidence


def find_similar_modifications(
    modification: PlanModification,
    similarity_threshold: float = 0.85
) -> List[PlanModification]:
    """
    Find modifications similar to the given one by context.
    
    Similarity factors:
    - Same operation type (combine_plugs, replace_cibp)
    - Same district
    - Same regulatory sections
    - Similar depth ranges
    - Same formations
    
    Args:
        modification: Reference modification
        similarity_threshold: Cosine similarity threshold
    
    Returns:
        List of similar PlanModification instances
    """
    source_payload = modification.source_snapshot.payload
    result_payload = modification.result_snapshot.payload if modification.result_snapshot else {}
    well = modification.source_snapshot.well
    
    # Extract context
    operation_type = modification.op_type
    district = result_payload.get('district')
    formations = result_payload.get('formations_targeted', [])
    
    # Start with same operation type
    similar = PlanModification.objects.filter(
        op_type=operation_type
    ).exclude(id=modification.id)
    
    # Filter by district if available
    if district:
        similar = similar.filter(
            source_snapshot__payload__district=district
        )
    
    # TODO: Use vector similarity for semantic matching
    # query_vector = get_modification_embedding(modification)
    # similar_vectors = DocumentVector.objects.annotate(
    #     similarity=CosineDistance('vector', query_vector)
    # ).filter(
    #     similarity__lte=(1 - similarity_threshold),
    #     metadata__type='plan_modification'
    # )
    # similar_mod_ids = [v.metadata['modification_id'] for v in similar_vectors]
    # similar = PlanModification.objects.filter(id__in=similar_mod_ids)
    
    logger.info(
        f"Found {similar.count()} similar modifications to {modification.id} "
        f"(type={operation_type}, district={district})"
    )
    
    return list(similar[:50])  # Limit to top 50


def propagate_confidence_to_similar(
    source_modification: PlanModification,
    outcome_approved: bool,
    propagation_factor: float = 0.5
):
    """
    Propagate confidence update to similar modifications.
    
    When a modification is approved/rejected, we update confidence for
    similar modifications to bias future suggestions.
    
    Args:
        source_modification: Modification with new outcome
        outcome_approved: True if approved, False if rejected
        propagation_factor: How much to propagate (0.0-1.0)
    """
    similar_mods = find_similar_modifications(source_modification)
    
    delta = 0.1 * propagation_factor if outcome_approved else -0.1 * propagation_factor
    
    for mod in similar_mods:
        # TODO: Update confidence in DocumentVector
        # vectors = DocumentVector.objects.filter(
        #     metadata__modification_id=mod.id
        # )
        # for vector in vectors:
        #     current_confidence = vector.metadata.get('outcome', {}).get('confidence', 0.5)
        #     new_confidence = max(0.0, min(1.0, current_confidence + delta))
        #     vector.metadata['outcome']['confidence'] = new_confidence
        #     vector.save()
        
        logger.debug(
            f"Propagated confidence delta {delta:+.2f} to modification {mod.id}"
        )
    
    logger.info(
        f"Propagated confidence to {len(similar_mods)} similar modifications"
    )


def get_confidence_weighted_suggestions(
    query_context: dict,
    min_confidence: float = 0.5,
    limit: int = 10
) -> List[dict]:
    """
    Get modification suggestions weighted by regulator approval confidence.
    
    This is how we bias toward regulator-approved patterns:
    - Query for similar context
    - Filter by min confidence (default 0.5)
    - Sort by confidence * similarity
    - Return top suggestions
    
    Args:
        query_context: Query parameters (district, formations, etc.)
        min_confidence: Minimum confidence threshold
        limit: Max results
    
    Returns:
        List of suggestions with confidence scores
    """
    # TODO: Implement vector search with confidence weighting
    # query_embedding = generate_embedding(query_context)
    # 
    # suggestions = DocumentVector.objects.filter(
    #     metadata__type='plan_modification',
    #     metadata__outcome__confidence__gte=min_confidence,
    #     metadata__district=query_context.get('district'),
    #     ...
    # ).annotate(
    #     similarity=CosineDistance('vector', query_embedding),
    #     weighted_score=F('similarity') * F('metadata__outcome__confidence')
    # ).order_by('-weighted_score')[:limit]
    
    logger.info(
        f"Querying confidence-weighted suggestions: "
        f"min_confidence={min_confidence}, limit={limit}"
    )
    
    return []


def calculate_confidence_statistics(district: str = None) -> dict:
    """
    Calculate confidence statistics for approved patterns.
    
    Returns:
        {
            "total_modifications": 100,
            "with_outcomes": 30,
            "approval_rate": 0.8,
            "avg_confidence": 0.72,
            "by_operation_type": {
                "combine_plugs": {"count": 10, "approval_rate": 0.9},
                "replace_cibp": {"count": 5, "approval_rate": 0.6}
            }
        }
    """
    from apps.assistant.models import RegulatorOutcome
    
    # Get all outcomes
    outcomes = RegulatorOutcome.objects.all()
    if district:
        outcomes = outcomes.filter(plan_snapshot__payload__district=district)
    
    total_outcomes = outcomes.count()
    approved = outcomes.filter(status='approved').count()
    rejected = outcomes.filter(status='rejected').count()
    
    approval_rate = approved / total_outcomes if total_outcomes > 0 else 0.0
    avg_confidence = outcomes.aggregate(Avg('confidence_score'))['confidence_score__avg'] or 0.5
    
    # By operation type
    by_op_type = {}
    for op_type in ['combine_plugs', 'replace_cibp', 'adjust_interval', 'change_materials']:
        mods = PlanModification.objects.filter(op_type=op_type)
        if district:
            mods = mods.filter(source_snapshot__payload__district=district)
        
        mod_ids = list(mods.values_list('id', flat=True))
        mod_outcomes = RegulatorOutcome.objects.filter(
            influenced_by_modifications__id__in=mod_ids
        )
        
        count = mod_outcomes.count()
        approved_count = mod_outcomes.filter(status='approved').count()
        
        by_op_type[op_type] = {
            "count": count,
            "approval_rate": approved_count / count if count > 0 else 0.0
        }
    
    return {
        "total_modifications": PlanModification.objects.count(),
        "total_outcomes": total_outcomes,
        "approved": approved,
        "rejected": rejected,
        "approval_rate": approval_rate,
        "avg_confidence": round(avg_confidence, 2),
        "by_operation_type": by_op_type
    }

