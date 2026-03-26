"""
Embed RejectionPattern descriptions into DocumentVector for similarity search.

Uses the same DocumentVector table with document_type='rejection_pattern'.
Follows the pattern established in apps/assistant/services/modification_embedder.py.
"""

import logging
from typing import TYPE_CHECKING

from apps.public_core.services.openai_config import (
    DEFAULT_EMBEDDING_MODEL,
    check_rate_limit,
    get_openai_client,
)

if TYPE_CHECKING:
    from apps.intelligence.models import RejectionPattern
    from apps.public_core.models import DocumentVector

logger = logging.getLogger(__name__)

# Estimated tokens per embedding request (used for rate-limit check)
_ESTIMATED_EMBEDDING_TOKENS = 512


class RejectionEmbedder:
    """
    Embeds RejectionPattern descriptions into DocumentVector for similarity search.
    Uses the same DocumentVector table with document_type='rejection_pattern'.
    """

    def embed_pattern(self, pattern: "RejectionPattern") -> "DocumentVector":
        """
        Create/update a DocumentVector for this pattern.

        1. Generate embedding text from pattern fields.
        2. Call OpenAI embeddings API (text-embedding-3-large, 3072 dims).
        3. Create/update DocumentVector (document_type='rejection_pattern').
        4. Link DocumentVector to pattern via pattern.embedding_vector FK.
        5. Return the DocumentVector.
        """
        from apps.public_core.models import DocumentVector

        embedding_text = self._generate_embedding_text(pattern)

        check_rate_limit(estimated_tokens=_ESTIMATED_EMBEDDING_TOKENS)

        client = get_openai_client(operation="rejection_pattern_embed")
        response = client.embeddings.create(
            input=embedding_text,
            model=DEFAULT_EMBEDDING_MODEL,
        )
        vector = response.data[0].embedding

        section_name = f"{pattern.form_type}/{pattern.field_name}/{pattern.issue_category}"

        metadata = {
            "pattern_id": str(pattern.id),
            "form_type": pattern.form_type,
            "field_name": pattern.field_name,
            "issue_category": pattern.issue_category,
            "issue_subcategory": pattern.issue_subcategory,
            "state": pattern.state,
            "district": pattern.district,
            "agency": pattern.agency,
            "occurrence_count": pattern.occurrence_count,
            "tenant_count": pattern.tenant_count,
            "rejection_rate": pattern.rejection_rate,
            "confidence": pattern.confidence,
            "is_trending": pattern.is_trending,
            "trend_direction": pattern.trend_direction,
            "first_observed": (
                pattern.first_observed.isoformat() if pattern.first_observed else None
            ),
            "last_observed": (
                pattern.last_observed.isoformat() if pattern.last_observed else None
            ),
        }

        if pattern.embedding_vector_id:
            # Update existing DocumentVector in-place
            doc_vector = DocumentVector.objects.get(id=pattern.embedding_vector_id)
            doc_vector.section_text = embedding_text
            doc_vector.embedding = vector
            doc_vector.metadata = metadata
            doc_vector.save(update_fields=["section_text", "embedding", "metadata"])
        else:
            doc_vector = DocumentVector.objects.create(
                well=None,
                file_name=f"rejection_pattern_{pattern.id}",
                document_type="rejection_pattern",
                section_name=section_name,
                section_text=embedding_text,
                embedding=vector,
                metadata=metadata,
            )

        # Link pattern -> DocumentVector
        pattern.embedding_vector = doc_vector
        pattern.save(update_fields=["embedding_vector", "updated_at"])

        logger.info(
            "[RejectionEmbedder] Embedded pattern %s (%s/%s/%s).",
            pattern.id,
            pattern.form_type,
            pattern.field_name,
            pattern.issue_category,
        )
        return doc_vector

    def find_similar_patterns(self, query_text: str, limit: int = 5) -> list[dict]:
        """
        Find similar rejection patterns using cosine similarity on embeddings.

        1. Embed query_text.
        2. Query DocumentVector WHERE document_type='rejection_pattern'
           ORDER BY cosine distance to query embedding.
        3. Return pattern info + similarity score.
        """
        from pgvector.django import CosineDistance

        from apps.public_core.models import DocumentVector

        check_rate_limit(estimated_tokens=_ESTIMATED_EMBEDDING_TOKENS)

        client = get_openai_client(operation="rejection_pattern_search")
        response = client.embeddings.create(
            input=query_text,
            model=DEFAULT_EMBEDDING_MODEL,
        )
        query_vector = response.data[0].embedding

        similar_vectors = (
            DocumentVector.objects.filter(document_type="rejection_pattern")
            .annotate(distance=CosineDistance("embedding", query_vector))
            .order_by("distance")[:limit]
        )

        results = []
        for dv in similar_vectors:
            results.append({
                "pattern_id": dv.metadata.get("pattern_id"),
                "form_type": dv.metadata.get("form_type"),
                "field_name": dv.metadata.get("field_name"),
                "issue_category": dv.metadata.get("issue_category"),
                "state": dv.metadata.get("state"),
                "agency": dv.metadata.get("agency"),
                "occurrence_count": dv.metadata.get("occurrence_count"),
                "confidence": dv.metadata.get("confidence"),
                "similarity_score": round(1.0 - float(dv.distance), 4),
                "section_text": dv.section_text,
            })

        logger.info(
            "[RejectionEmbedder] Similarity search returned %d results for query: %.80s",
            len(results),
            query_text,
        )
        return results

    def _generate_embedding_text(self, pattern: "RejectionPattern") -> str:
        """Generate structured text for embedding."""
        lines = [
            f"Rejection Pattern: {pattern.form_type} / {pattern.field_name}",
            f"Issue Category: {pattern.issue_category}",
        ]

        if pattern.issue_subcategory:
            lines.append(f"Issue Subcategory: {pattern.issue_subcategory}")

        lines.append(f"Agency: {pattern.agency}")

        geo_parts = []
        if pattern.state:
            geo_parts.append(f"State: {pattern.state}")
        if pattern.district:
            geo_parts.append(f"District: {pattern.district}")
        if geo_parts:
            lines.append("Geography: " + ", ".join(geo_parts))

        if pattern.pattern_description:
            lines.append(f"\nDescription: {pattern.pattern_description}")

        if pattern.example_bad_value:
            lines.append(f"Example Bad Value: {pattern.example_bad_value}")

        if pattern.example_good_value:
            lines.append(f"Example Good Value: {pattern.example_good_value}")

        lines.extend([
            f"\nStats:",
            f"- Occurrences: {pattern.occurrence_count}",
            f"- Operators Affected: {pattern.tenant_count}",
            f"- Rejection Rate: {pattern.rejection_rate:.1%}",
            f"- Confidence: {pattern.confidence:.2f}",
            f"- Trending: {'Yes' if pattern.is_trending else 'No'}",
        ])

        return "\n".join(lines)
