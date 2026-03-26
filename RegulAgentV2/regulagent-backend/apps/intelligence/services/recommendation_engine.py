"""
RecommendationEngine: Generates and serves field-level recommendations
based on cross-tenant rejection patterns.

- generate_recommendations(): daily batch job converting RejectionPatterns to Recommendation records
- get_recommendations_for_context(): ranked retrieval for a form editing context
- check_field_value(): lightweight real-time field check
"""

import logging
import re

from apps.public_core.services.openai_config import (
    DEFAULT_CHAT_MODEL,
    TEMPERATURE_CREATIVE,
    get_openai_client,
)

logger = logging.getLogger(__name__)


class RecommendationEngine:
    """Generates and serves recommendations based on rejection patterns."""

    # Minimum occurrences for a pattern to be turned into a recommendation
    MIN_OCCURRENCE_COUNT = 2
    # Minimum unique tenants for cross-tenant recommendations (privacy guard)
    MIN_TENANT_COUNT_CROSS_TENANT = 3

    # Scoring weights
    _W_TRIGGER_MATCH = 3.0
    _W_DISTRICT = 2.0
    _W_STATE = 1.5
    _W_ANY_GEO = 1.0
    _W_EMBEDDING_SIMILARITY = 1.2

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def generate_recommendations(self) -> dict:
        """
        Convert RejectionPatterns into Recommendation records.
        Run daily at 2am via Celery Beat.

        1. Query RejectionPattern.objects.filter(occurrence_count__gte=2)
        2. For each pattern without an active recommendation:
           - Generate user-facing title + description via AI
           - Create trigger_condition from pattern data
           - Create Recommendation(scope='cross_tenant', pattern=pattern)
        3. Cross-tenant privacy: only create if pattern.tenant_count >= 3
        4. Return stats: {created, updated, skipped}
        """
        from apps.intelligence.models import RejectionPattern, Recommendation

        stats = {"created": 0, "updated": 0, "skipped": 0}

        patterns = RejectionPattern.objects.filter(
            occurrence_count__gte=self.MIN_OCCURRENCE_COUNT,
        ).prefetch_related("recommendations")

        for pattern in patterns:
            # Privacy guard for cross-tenant scope
            if pattern.tenant_count < self.MIN_TENANT_COUNT_CROSS_TENANT:
                stats["skipped"] += 1
                logger.debug(
                    "[RecommendationEngine] Skipping pattern %s — tenant_count=%d < %d",
                    pattern.id,
                    pattern.tenant_count,
                    self.MIN_TENANT_COUNT_CROSS_TENANT,
                )
                continue

            # Check for existing active recommendation for this pattern
            existing = pattern.recommendations.filter(is_active=True).first()

            trigger_condition = self._build_trigger_condition(pattern)
            priority = self._derive_priority(pattern)

            if existing:
                # Update trigger + priority if pattern stats changed
                existing.trigger_condition = trigger_condition
                existing.priority = priority
                existing.save(update_fields=["trigger_condition", "priority", "updated_at"])
                stats["updated"] += 1
                continue

            # Generate user-facing title + description
            try:
                title, description = self._generate_content(pattern)
            except Exception:
                logger.exception(
                    "[RecommendationEngine] AI content generation failed for pattern %s — "
                    "using template fallback.",
                    pattern.id,
                )
                title, description = self._template_content(pattern)

            Recommendation.objects.create(
                pattern=pattern,
                form_type=pattern.form_type,
                field_name=pattern.field_name,
                state=pattern.state,
                district=pattern.district,
                title=title,
                description=description,
                suggested_value=pattern.example_good_value,
                trigger_condition=trigger_condition,
                scope="cross_tenant",
                priority=priority,
            )
            stats["created"] += 1
            logger.info(
                "[RecommendationEngine] Created recommendation for pattern %s (%s/%s).",
                pattern.id,
                pattern.form_type,
                pattern.field_name,
            )

        logger.info("[RecommendationEngine] generate_recommendations complete: %s", stats)
        return stats

    def get_recommendations_for_context(
        self,
        form_type: str,
        state: str = "",
        district: str = "",
        field_values: dict = None,
    ) -> list[dict]:
        """
        Ranked retrieval of recommendations for a form editing context.

        Scoring layers (combined):
        1. Exact trigger match → highest weight
        2. Geographic match (district > state > any)
        3. Embedding similarity for novel patterns

        Privacy filter: exclude scope='cross_tenant' where pattern.tenant_count < 3

        Returns list of recommendation dicts sorted by score descending.
        """
        from apps.intelligence.models import Recommendation

        if field_values is None:
            field_values = {}

        qs = Recommendation.objects.filter(
            form_type=form_type,
            is_active=True,
        ).select_related("pattern")

        # Privacy filter
        recs = [
            r for r in qs
            if not (
                r.scope == "cross_tenant"
                and r.pattern
                and r.pattern.tenant_count < self.MIN_TENANT_COUNT_CROSS_TENANT
            )
        ]

        # Score each recommendation
        scored = []
        for rec in recs:
            score = self._score_recommendation(rec, form_type, state, district, field_values)
            if score > 0:
                scored.append((score, rec))

        # Augment with embedding-similar patterns for novel field values
        if field_values:
            embedding_hits = self._embedding_augment(form_type, field_values, limit=5)
            rec_ids_already = {rec.id for _, rec in scored}
            for hit in embedding_hits:
                pattern_id = hit.get("pattern_id")
                if not pattern_id:
                    continue
                extra_recs = Recommendation.objects.filter(
                    pattern_id=pattern_id,
                    is_active=True,
                ).select_related("pattern")
                for rec in extra_recs:
                    if rec.id not in rec_ids_already:
                        base_score = (
                            hit.get("similarity_score", 0.5)
                            * self._W_EMBEDDING_SIMILARITY
                            * rec.acceptance_rate
                            if rec.acceptance_rate
                            else hit.get("similarity_score", 0.5) * self._W_EMBEDDING_SIMILARITY
                        )
                        scored.append((base_score, rec))
                        rec_ids_already.add(rec.id)

        scored.sort(key=lambda x: x[0], reverse=True)

        return [self._rec_to_dict(rec, score) for score, rec in scored]

    def check_field_value(
        self,
        form_type: str,
        field_name: str,
        value: str,
        state: str = "",
        district: str = "",
    ) -> list[dict]:
        """
        Lightweight real-time field check.

        1. Find active Recommendations matching form_type + field_name + geo
        2. Check if value matches trigger_condition
        3. Return matching recommendations
        """
        from apps.intelligence.models import Recommendation

        qs = Recommendation.objects.filter(
            form_type=form_type,
            field_name=field_name,
            is_active=True,
        ).select_related("pattern")

        # Geo filter: district-specific, state-specific, or no geo restriction
        geo_qs = qs.filter(
            **self._geo_filter_kwargs(state, district)
        )
        if not geo_qs.exists():
            # Fallback: state-only
            geo_qs = qs.filter(state=state, district="") if state else qs.filter(state="", district="")

        results = []
        for rec in geo_qs:
            # Privacy guard
            if (
                rec.scope == "cross_tenant"
                and rec.pattern
                and rec.pattern.tenant_count < self.MIN_TENANT_COUNT_CROSS_TENANT
            ):
                continue
            if self._match_trigger(rec, value):
                results.append(self._rec_to_dict(rec))

        return results

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _match_trigger(self, recommendation: "Recommendation", value: str) -> bool:
        """Check if a value matches the recommendation's trigger_condition."""
        condition = recommendation.trigger_condition or {}

        # Check trigger_values (exact list match)
        trigger_values = condition.get("trigger_values", [])
        if trigger_values and isinstance(trigger_values, list):
            if value in trigger_values:
                return True

        # Check trigger_pattern (regex match)
        trigger_pattern = condition.get("trigger_pattern", "")
        if trigger_pattern:
            try:
                if re.search(trigger_pattern, value, re.IGNORECASE):
                    return True
            except re.error:
                logger.warning(
                    "[RecommendationEngine] Invalid regex in trigger_pattern for rec %s: %s",
                    recommendation.id,
                    trigger_pattern,
                )

        # No trigger defined → always matches (informational recommendation)
        if not trigger_values and not trigger_pattern:
            return True

        return False

    def _score_recommendation(
        self,
        rec: "Recommendation",
        form_type: str,
        state: str,
        district: str,
        field_values: dict,
    ) -> float:
        """Calculate relevance score for ranking."""
        score = 1.0

        # Geographic specificity bonus
        if district and rec.district == district:
            score *= self._W_DISTRICT
        elif state and rec.state == state:
            score *= self._W_STATE
        elif not rec.state and not rec.district:
            score *= self._W_ANY_GEO
        else:
            # Pattern is for a different geo — deprioritise
            score *= 0.3

        # Trigger match bonus
        value = field_values.get(rec.field_name, "")
        if value and self._match_trigger(rec, value):
            score *= self._W_TRIGGER_MATCH

        # Pattern confidence factor
        if rec.pattern:
            score *= max(rec.pattern.confidence, 0.1)

        # Acceptance rate factor (neutral at 0)
        if rec.acceptance_rate:
            score *= (0.5 + rec.acceptance_rate)

        return score

    def _build_trigger_condition(self, pattern: "RejectionPattern") -> dict:
        """Derive trigger_condition JSON from a RejectionPattern."""
        condition: dict = {"field_name": pattern.field_name}

        if pattern.example_bad_value:
            condition["trigger_values"] = [pattern.example_bad_value]

        if pattern.issue_category in ("formatting", "precision"):
            # Build a loose regex for formatting/precision issues
            if pattern.example_bad_value:
                escaped = re.escape(pattern.example_bad_value)
                condition["trigger_pattern"] = f"^{escaped}$"

        return condition

    def _derive_priority(self, pattern: "RejectionPattern") -> str:
        """Map pattern stats to a priority level."""
        if pattern.rejection_rate >= 0.5 or pattern.is_trending:
            return "high"
        if pattern.occurrence_count >= 10 or pattern.rejection_rate >= 0.2:
            return "medium"
        return "low"

    def _geo_filter_kwargs(self, state: str, district: str) -> dict:
        """Build the most specific geo kwargs for a queryset filter."""
        if district:
            return {"state": state, "district": district}
        if state:
            return {"state": state}
        return {"state": "", "district": ""}

    def _generate_content(self, pattern: "RejectionPattern") -> tuple[str, str]:
        """Use AI to generate user-facing title + description for a pattern."""
        client = get_openai_client(operation="recommendation_content_gen")

        prompt = (
            f"You are a regulatory compliance assistant for oil and gas operators.\n"
            f"Generate a short, actionable recommendation title (max 10 words) and a "
            f"clear description (2-3 sentences) for operators filling out form "
            f"'{pattern.form_type}' in the field '{pattern.field_name}'.\n\n"
            f"Pattern details:\n"
            f"- Issue: {pattern.issue_category} / {pattern.issue_subcategory}\n"
            f"- Description: {pattern.pattern_description}\n"
            f"- Bad example: {pattern.example_bad_value or 'N/A'}\n"
            f"- Good example: {pattern.example_good_value or 'N/A'}\n"
            f"- Agency: {pattern.agency}\n\n"
            f"Return JSON: {{\"title\": \"...\", \"description\": \"...\"}}"
        )

        response = client.chat.completions.create(
            model=DEFAULT_CHAT_MODEL,
            temperature=TEMPERATURE_CREATIVE,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )

        import json
        data = json.loads(response.choices[0].message.content)
        return data["title"], data["description"]

    def _template_content(self, pattern: "RejectionPattern") -> tuple[str, str]:
        """Fallback template-based content generation."""
        title = f"Check {pattern.field_name} — {pattern.issue_category} issue"
        description = (
            f"This field has been flagged {pattern.occurrence_count} times for "
            f"{pattern.issue_category} issues on {pattern.agency} {pattern.form_type} filings. "
            f"{pattern.pattern_description}"
        )
        if pattern.example_good_value:
            description += f" Recommended value format: {pattern.example_good_value}."
        return title, description

    def _embedding_augment(
        self, form_type: str, field_values: dict, limit: int = 5
    ) -> list[dict]:
        """Use embedding similarity to find patterns relevant to current field values."""
        try:
            from apps.intelligence.services.rejection_embedder import RejectionEmbedder

            query_parts = [f"Form: {form_type}"]
            for field, val in list(field_values.items())[:5]:  # limit context
                query_parts.append(f"{field}: {val}")
            query_text = "\n".join(query_parts)

            embedder = RejectionEmbedder()
            return embedder.find_similar_patterns(query_text, limit=limit)
        except Exception:
            logger.warning(
                "[RecommendationEngine] Embedding augment failed (non-fatal).",
                exc_info=True,
            )
            return []

    def _rec_to_dict(self, rec: "Recommendation", score: float = 0.0) -> dict:
        """Serialize a Recommendation to a dict for API responses."""
        return {
            "id": str(rec.id),
            "title": rec.title,
            "description": rec.description,
            "form_type": rec.form_type,
            "field_name": rec.field_name,
            "state": rec.state,
            "district": rec.district,
            "suggested_value": rec.suggested_value,
            "priority": rec.priority,
            "scope": rec.scope,
            "acceptance_rate": rec.acceptance_rate,
            "trigger_condition": rec.trigger_condition,
            "relevance_score": round(score, 4),
            "pattern_description": (
                rec.pattern.pattern_description if rec.pattern else ""
            ),
        }
