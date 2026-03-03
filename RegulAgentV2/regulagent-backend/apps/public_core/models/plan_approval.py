from __future__ import annotations

from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class PlanApproval(models.Model):
    """Tracks approval actions on plan status transitions."""

    plan_snapshot = models.ForeignKey(
        'PlanSnapshot',
        on_delete=models.CASCADE,
        related_name="approvals"
    )
    from_status = models.CharField(max_length=32)
    to_status = models.CharField(max_length=32)
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="plan_approvals"
    )
    approved_at = models.DateTimeField(auto_now_add=True)
    comments = models.TextField(blank=True)
    is_automated = models.BooleanField(default=False)

    class Meta:
        db_table = "public_core_plan_approvals"
        ordering = ["-approved_at"]

    def __str__(self):
        return f"Approval<{self.from_status}->{self.to_status}>"
