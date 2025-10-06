from django.db import models


class PolicyRule(models.Model):
    """
    One row per TAC rule (e.g., tx.tac.16.3.14) and version tag.
    Stores top-level metadata and a content hash for change detection.
    """

    rule_id = models.CharField(max_length=64, db_index=True)
    citation = models.CharField(max_length=128)
    title = models.TextField(blank=True)
    source_urls = models.JSONField(default=list)

    # Tagging to support broader policy types and queries
    jurisdiction = models.CharField(max_length=8, null=True, blank=True)
    doc_type = models.CharField(max_length=32, default='policy')  # policy|faq|mou|other
    topic = models.CharField(max_length=64, null=True, blank=True)  # e.g., plugging, casing, water

    version_tag = models.CharField(max_length=32, db_index=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)

    html_sha256 = models.CharField(max_length=64, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'policy_rule'
        unique_together = (
            ('rule_id', 'version_tag'),
        )
        indexes = [
            models.Index(fields=['rule_id', 'version_tag']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.rule_id}@{self.version_tag}"


