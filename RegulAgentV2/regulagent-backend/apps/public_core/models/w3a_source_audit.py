from __future__ import annotations

from django.conf import settings
from django.db import models


class W3ASourceAudit(models.Model):
    """
    Internal audit log for W3A initial endpoint outcomes.

    Tracks every call to the W3A initial endpoint, recording
    what was found (or not found), where it came from, and
    who triggered the request. Read-only — never mutated after
    creation.
    """

    # Outcome constants
    OUTCOME_SUCCESS = "success"
    OUTCOME_NO_RECORDS = "no_records"
    OUTCOME_NO_DOCUMENTS = "no_documents"
    OUTCOME_ERROR = "error"

    OUTCOME_CHOICES = [
        (OUTCOME_SUCCESS, "Success — documents found and returned"),
        (OUTCOME_NO_RECORDS, "No Records — API number not found in registry"),
        (OUTCOME_NO_DOCUMENTS, "No Documents — records found but no PDFs attached"),
        (OUTCOME_ERROR, "Error — exception or downstream failure"),
    ]

    # Input mode constants
    INPUT_MODE_EXTRACTIONS = "extractions"
    INPUT_MODE_USER_FILES = "user_files"
    INPUT_MODE_HYBRID = "hybrid"
    INPUT_MODE_MANUAL = "manual"

    INPUT_MODE_CHOICES = [
        (INPUT_MODE_EXTRACTIONS, "Extractions — sourced from automated extraction pipeline"),
        (INPUT_MODE_USER_FILES, "User Files — sourced from user-uploaded documents"),
        (INPUT_MODE_HYBRID, "Hybrid — combination of extractions and user files"),
        (INPUT_MODE_MANUAL, "Manual — manually supplied data"),
    ]

    # Source constants
    SOURCE_RRC = "rrc"
    SOURCE_CACHE = "cache"
    SOURCE_USER_UPLOAD = "user_upload"

    SOURCE_CHOICES = [
        (SOURCE_RRC, "RRC — fetched live from Texas RRC"),
        (SOURCE_CACHE, "Cache — returned from cached/stored data"),
        (SOURCE_USER_UPLOAD, "User Upload — provided by user file upload"),
    ]

    # Core fields
    api_number = models.CharField(
        max_length=14,
        db_index=True,
        help_text="14-digit API number that was queried",
    )

    jurisdiction = models.CharField(
        max_length=4,
        help_text="Jurisdiction of the well (TX or NM)",
    )

    input_mode = models.CharField(
        max_length=20,
        choices=INPUT_MODE_CHOICES,
        help_text="How input documents were sourced for this request",
    )

    outcome = models.CharField(
        max_length=20,
        choices=OUTCOME_CHOICES,
        db_index=True,
        help_text="Overall result of the W3A initial endpoint call",
    )

    outcome_detail = models.TextField(
        blank=True,
        help_text="Human-readable detail about what happened (e.g. error message, reason for no-records)",
    )

    document_count = models.IntegerField(
        default=0,
        help_text="Number of PDFs found or combined during this request",
    )

    source = models.CharField(
        max_length=20,
        blank=True,
        choices=SOURCE_CHOICES,
        help_text="Where the documents originated (rrc / cache / user_upload)",
    )

    # Attribution
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="w3a_source_audits",
        help_text="User who triggered this request (null for system/background requests)",
    )

    tenant_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Tenant that made this request",
    )

    # Timestamp
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "public_core_w3a_source_audit"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["outcome", "created_at"]),
            models.Index(fields=["api_number", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"W3ASourceAudit<{self.api_number}:{self.jurisdiction}:{self.outcome}>"
