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

    # Update DocumentVector metadata with new confidence
    vectors = DocumentVector.objects.filter(
        document_type="plan_modification",
        metadata__modification_id=str(modification.id)
    )
    for vector in vectors:
        # Update metadata with outcome info
        metadata = vector.metadata.copy()
        if 'outcome' not in metadata:
            metadata['outcome'] = {}
        metadata['outcome']['confidence'] = confidence
        metadata['outcome']['approved_count'] = approved_count
        metadata['outcome']['rejected_count'] = rejected_count
        metadata['outcome']['regulator_status'] = outcome_instance.status if outcome_instance else None
        vector.metadata = metadata
        vector.save(update_fields=['metadata'])
        logger.debug(f"Updated DocumentVector {vector.id} confidence to {confidence:.2f}")

    return confidence


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
    # Use vector similarity to find similar modifications
    similar_results = find_similar_modifications(source_modification, top_k=20)

    delta = 0.1 * propagation_factor if outcome_approved else -0.1 * propagation_factor
    updated_count = 0

    for mod, similarity in similar_results:
        # Scale delta by similarity score - more similar = more impact
        scaled_delta = delta * similarity

        # Update confidence in DocumentVector
        vectors = DocumentVector.objects.filter(
            document_type="plan_modification",
            metadata__modification_id=str(mod.id)
        )
        for vector in vectors:
            metadata = vector.metadata.copy()
            current_confidence = metadata.get('outcome', {}).get('confidence', 0.5)
            new_confidence = max(0.0, min(1.0, current_confidence + scaled_delta))

            if 'outcome' not in metadata:
                metadata['outcome'] = {}
            metadata['outcome']['confidence'] = new_confidence
            metadata['outcome']['propagated_from'] = str(source_modification.id)
            metadata['outcome']['propagation_similarity'] = similarity
            vector.metadata = metadata
            vector.save(update_fields=['metadata'])
            updated_count += 1

        logger.debug(
            f"Propagated confidence delta {scaled_delta:+.3f} to modification {mod.id} "
            f"(similarity={similarity:.2f})"
        )

    logger.info(
        f"Propagated confidence to {len(similar_results)} similar modifications "
        f"({updated_count} vectors updated)"
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
        query_context: Query parameters (district, formations, op_type, etc.)
        min_confidence: Minimum confidence threshold
        limit: Max results

    Returns:
        List of suggestions with confidence scores
    """
    from pgvector.django import CosineDistance
    from apps.public_core.services.openai_config import get_openai_client

    # Build query text from context
    text_parts = []
    if query_context.get('op_type'):
        text_parts.append(f"Operation: {query_context['op_type']}")
    if query_context.get('district'):
        text_parts.append(f"District: {query_context['district']}")
    if query_context.get('formations'):
        text_parts.append(f"Formations: {', '.join(query_context['formations'])}")
    if query_context.get('description'):
        text_parts.append(f"Description: {query_context['description']}")

    if not text_parts:
        logger.warning("Empty query context for confidence-weighted suggestions")
        return []

    query_text = "\n".join(text_parts)

    # Generate embedding for query
    try:
        client = get_openai_client()
        response = client.embeddings.create(
            input=query_text,
            model="text-embedding-3-small"
        )
        query_embedding = response.data[0].embedding
    except Exception as e:
        logger.exception(f"Error generating embedding for query: {e}")
        return []

    # Query for similar modifications with confidence weighting
    similar_docs = DocumentVector.objects.filter(
        document_type="plan_modification"
    ).annotate(
        distance=CosineDistance('embedding', query_embedding)
    ).order_by('distance')[:limit * 3]  # Get more candidates for filtering

    # Filter and weight by confidence
    suggestions = []
    for doc in similar_docs:
        outcome = doc.metadata.get('outcome', {})
        confidence = outcome.get('confidence', 0.5)

        # Skip low confidence modifications
        if confidence < min_confidence:
            continue

        similarity = 1.0 - float(doc.distance)
        weighted_score = similarity * confidence

        # Get the modification details
        mod_id = doc.metadata.get('modification_id')
        if not mod_id:
            continue

        try:
            modification = PlanModification.objects.select_related(
                'source_snapshot', 'result_snapshot'
            ).get(id=mod_id)

            suggestions.append({
                'modification_id': mod_id,
                'op_type': modification.op_type,
                'description': modification.description,
                'similarity': round(similarity, 3),
                'confidence': round(confidence, 3),
                'weighted_score': round(weighted_score, 3),
                'approved_count': outcome.get('approved_count', 0),
                'rejected_count': outcome.get('rejected_count', 0),
                'risk_score': modification.risk_score,
            })
        except PlanModification.DoesNotExist:
            continue

    # Sort by weighted score (confidence * similarity)
    suggestions.sort(key=lambda x: x['weighted_score'], reverse=True)
    suggestions = suggestions[:limit]

    logger.info(
        f"Found {len(suggestions)} confidence-weighted suggestions "
        f"(min_confidence={min_confidence}, limit={limit})"
    )

    return suggestions


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


def find_similar_modifications(modification, top_k: int = 5):
    """
    Find similar past modifications using pgvector cosine similarity.

    Args:
        modification: PlanModification to find similar to
        top_k: Number of results to return

    Returns:
        List of (PlanModification, similarity_score) tuples
    """
    from pgvector.django import CosineDistance
    from apps.public_core.models import DocumentVector
    from apps.assistant.models import PlanModification
    from apps.assistant.services.modification_embedder import embed_modification

    # Get or create embedding for query modification
    query_doc = DocumentVector.objects.filter(
        document_type="plan_modification",
        metadata__modification_id=str(modification.id)
    ).first()

    if not query_doc:
        query_doc = embed_modification(modification)

    query_embedding = query_doc.embedding

    # Find similar modifications using cosine distance
    similar_docs = DocumentVector.objects.filter(
        document_type="plan_modification"
    ).exclude(
        metadata__modification_id=str(modification.id)
    ).annotate(
        distance=CosineDistance('embedding', query_embedding)
    ).order_by('distance')[:top_k]

    # Convert to PlanModification objects with similarity scores
    results = []
    for doc in similar_docs:
        mod_id = doc.metadata.get('modification_id')
        if mod_id:
            try:
                plan_mod = PlanModification.objects.get(id=mod_id)
                similarity = 1.0 - float(doc.distance)
                results.append((plan_mod, similarity))
            except PlanModification.DoesNotExist:
                continue

    return results
