from django.db import models
from .policy_rule import PolicyRule


class PolicySection(models.Model):
    """
    Atomic section/subsection of a rule version (e.g., (b)(1)).
    """

    rule = models.ForeignKey(PolicyRule, on_delete=models.CASCADE, related_name='sections')
    version_tag = models.CharField(max_length=32)

    path = models.TextField()  # structured path like b, b(1), b(1)(A)
    heading = models.TextField(blank=True)
    text = models.TextField()
    anchor = models.CharField(max_length=128, blank=True)
    order_idx = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'policy_section'
        unique_together = (
            ('rule', 'version_tag', 'path', 'order_idx'),
        )
        indexes = [
            models.Index(fields=['rule', 'version_tag', 'order_idx']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.rule.rule_id}@{self.version_tag}:{self.path}"


