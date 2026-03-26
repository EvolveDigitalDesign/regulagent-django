"""
Aggregate parsed RejectionRecords into cross-tenant RejectionPatterns.

Groups parsed issues by (form_type, field_name, issue_category, state, district, agency)
and updates RejectionPattern stats, trends, and confidence scores.
"""

import logging
import math
from collections import defaultdict
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class PatternAggregator:
    """
    Aggregates parsed RejectionRecords into cross-tenant RejectionPatterns.
    Groups by (form_type, field_name, issue_category, state, district, agency).
    """

    def aggregate(self) -> dict:
        """
        Main aggregation method. Run periodically (every 6 hours).

        1. Query RejectionRecords with parse_status in ('parsed', 'verified').
        2. For each record, iterate over parsed_issues (JSONField list).
        3. Group issues by (form_type, field_name, issue_category, state, district, agency).
        4. For each group: count occurrences, distinct tenants, date range, example values.
        5. update_or_create RejectionPattern for each group.
        6. Detect trends for updated patterns.
        7. Dispatch embed_rejection_pattern task for each updated pattern.
        8. Return summary stats.
        """
        from apps.intelligence.models import RejectionRecord, RejectionPattern

        records = list(
            RejectionRecord.objects.filter(
                parse_status__in=["parsed", "verified"]
            ).values(
                "id",
                "form_type",
                "state",
                "district",
                "agency",
                "tenant_id",
                "rejection_date",
                "created_at",
                "parsed_issues",
            )
        )

        logger.info("[PatternAggregator] Processing %d parsed rejection records.", len(records))

        # Group issues by key dimensions
        # key -> list of issue dicts (with injected record metadata)
        groups: dict[tuple, list[dict]] = defaultdict(list)

        for record in records:
            issues = record.get("parsed_issues") or []
            if not isinstance(issues, list):
                continue
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                field_name = issue.get("field_name", "")
                issue_category = issue.get("issue_category", "")
                if not field_name or not issue_category:
                    continue

                key = (
                    record["form_type"],
                    field_name,
                    issue_category,
                    record.get("state", ""),
                    record.get("district", ""),
                    record["agency"],
                )
                groups[key].append({
                    **issue,
                    "_tenant_id": str(record["tenant_id"]),
                    "_rejection_date": record.get("rejection_date"),
                    "_created_at": record.get("created_at"),
                })

        updated_pattern_ids: list[str] = []
        patterns_created = 0
        patterns_updated = 0

        for key, issue_list in groups.items():
            form_type, field_name, issue_category, state, district, agency = key

            tenant_ids = {e["_tenant_id"] for e in issue_list}
            occurrence_count = len(issue_list)
            tenant_count = len(tenant_ids)

            # Date range
            dates = [
                e["_created_at"]
                for e in issue_list
                if e.get("_created_at") is not None
            ]
            first_observed = min(dates) if dates else None
            last_observed = max(dates) if dates else None

            # Example values from highest-confidence issue
            best_issue = max(
                issue_list,
                key=lambda e: float(e.get("confidence", 0)),
                default=issue_list[0],
            )
            example_bad_value = str(best_issue.get("bad_value", ""))[:255]
            example_good_value = str(best_issue.get("good_value", ""))[:255]

            # Build pattern description
            pattern_description = best_issue.get("description") or (
                f"{issue_category} issue on field '{field_name}' "
                f"for {form_type} filings in {state or 'all states'} "
                f"({agency}). Seen {occurrence_count} time(s) across "
                f"{tenant_count} operator(s)."
            )

            # Rejection rate: rejections for this form_type+state / total filings
            rejection_rate = self._calculate_rejection_rate(
                form_type=form_type,
                state=state,
                agency=agency,
            )

            with transaction.atomic():
                pattern, created = RejectionPattern.objects.update_or_create(
                    form_type=form_type,
                    field_name=field_name,
                    issue_category=issue_category,
                    state=state,
                    district=district,
                    agency=agency,
                    defaults={
                        "pattern_description": pattern_description,
                        "example_bad_value": example_bad_value,
                        "example_good_value": example_good_value,
                        "occurrence_count": occurrence_count,
                        "tenant_count": tenant_count,
                        "rejection_rate": rejection_rate,
                        "first_observed": first_observed,
                        "last_observed": last_observed,
                    },
                )

            if created:
                patterns_created += 1
            else:
                patterns_updated += 1

            # Recalculate confidence and trend
            pattern.confidence = self._calculate_confidence(pattern)
            self._detect_trends(pattern)
            pattern.save(update_fields=["confidence", "is_trending", "trend_direction", "updated_at"])

            updated_pattern_ids.append(str(pattern.id))

        # Dispatch embedding tasks for all updated patterns
        self._dispatch_embed_tasks(updated_pattern_ids)

        result = {
            "status": "success",
            "records_processed": len(records),
            "groups_found": len(groups),
            "patterns_created": patterns_created,
            "patterns_updated": patterns_updated,
            "embed_tasks_dispatched": len(updated_pattern_ids),
        }
        logger.info("[PatternAggregator] Aggregation complete: %s", result)
        return result

    def _calculate_rejection_rate(self, form_type: str, state: str, agency: str) -> float:
        """
        Calculate the rejection rate as: rejected filings / total filings
        for the given form_type + state + agency combination.
        """
        from apps.intelligence.models import FilingStatusRecord

        qs = FilingStatusRecord.objects.filter(
            form_type=form_type,
            agency=agency,
        )
        if state:
            qs = qs.filter(state=state)

        total = qs.count()
        if total == 0:
            return 0.0

        rejected = qs.filter(status__in=["rejected", "revision_requested", "deficiency"]).count()
        return round(rejected / total, 4)

    def _detect_trends(self, pattern: "RejectionPattern") -> None:
        """
        Trend detection: compare 30-day rolling count vs 90-day baseline.

        is_trending = True when 30-day rate > 1.5x the 90-day average daily rate.
        trend_direction = slope (positive = increasing, negative = decreasing).
        """
        from apps.intelligence.models import RejectionRecord

        now = timezone.now()
        day_30_ago = now - timedelta(days=30)
        day_90_ago = now - timedelta(days=90)

        base_qs = RejectionRecord.objects.filter(
            form_type=pattern.form_type,
            agency=pattern.agency,
            parse_status__in=["parsed", "verified"],
        )
        if pattern.state:
            base_qs = base_qs.filter(state=pattern.state)
        if pattern.district:
            base_qs = base_qs.filter(district=pattern.district)

        count_30d = base_qs.filter(created_at__gte=day_30_ago).count()
        count_60_to_90d = base_qs.filter(
            created_at__gte=day_90_ago,
            created_at__lt=day_30_ago,
        ).count()

        # Daily rates
        rate_30d = count_30d / 30.0
        rate_baseline = count_60_to_90d / 60.0

        if rate_baseline > 0:
            ratio = rate_30d / rate_baseline
            pattern.is_trending = ratio > 1.5
            # Slope: difference in daily rates
            pattern.trend_direction = round(rate_30d - rate_baseline, 4)
        else:
            # No baseline data — treat rising from zero as trending if any recent activity
            pattern.is_trending = count_30d > 0
            pattern.trend_direction = round(rate_30d, 4)

    def _calculate_confidence(self, pattern: "RejectionPattern") -> float:
        """
        Confidence formula:
          base(occurrence_count) * recency_decay * consistency + breadth_bonus(tenant_count)

        - base: log scale of occurrence_count (diminishing returns above 20)
        - recency_decay: 1.0 if last_observed < 30 days, decays to 0.3 at 180 days
        - consistency: ratio of issues with confidence > 0.7 (approximated from pattern data)
        - breadth_bonus: 0.1 * min(tenant_count, 5) — capped at 0.5
        """
        # Base score: logarithmic scale, max ~1.0 around occurrence_count=20+
        base = min(math.log(max(pattern.occurrence_count, 1) + 1) / math.log(21), 1.0)

        # Recency decay
        recency_decay = 1.0
        if pattern.last_observed:
            now = timezone.now()
            # Ensure last_observed is timezone-aware for comparison
            last_obs = pattern.last_observed
            if timezone.is_naive(last_obs):
                last_obs = timezone.make_aware(last_obs)
            days_since = (now - last_obs).days
            if days_since <= 30:
                recency_decay = 1.0
            elif days_since >= 180:
                recency_decay = 0.3
            else:
                # Linear decay from 1.0 to 0.3 between 30 and 180 days
                recency_decay = 1.0 - (0.7 * (days_since - 30) / 150)

        # Consistency: approximated as 0.8 (we don't re-query individual issues here)
        # A future enhancement could pass consistency from the aggregation loop
        consistency = 0.8

        # Breadth bonus: more tenants = more reliable signal
        breadth_bonus = 0.1 * min(pattern.tenant_count, 5)

        confidence = (base * recency_decay * consistency) + breadth_bonus
        return round(min(confidence, 1.0), 4)

    def _dispatch_embed_tasks(self, pattern_ids: list[str]) -> None:
        """Dispatch embed_rejection_pattern task for each updated pattern."""
        from apps.intelligence.tasks import embed_rejection_pattern

        for pattern_id in pattern_ids:
            try:
                embed_rejection_pattern.delay(pattern_id)
            except Exception:
                logger.exception(
                    "[PatternAggregator] Failed to dispatch embed task for pattern %s.",
                    pattern_id,
                )
