from __future__ import annotations

from django.db import models

from .well_registry import WellRegistry


class ExtractedDocument(models.Model):
    """
    Stores structured JSON extracted from regulatory documents (W-2, GAU, W-15, schematic, formation tops, etc.).

    This acts as the system of record for extracted payloads before (and in addition to) syncing into
    normalized relational tables. Vectorization jobs can read from this table as well.
    
    Tenant Isolation & Validation:
    - RRC-sourced documents: source_type='rrc', uploaded_by_tenant=None, is_validated=True
    - Tenant uploads: source_type='tenant_upload', uploaded_by_tenant=<tenant_uuid>
    - Validated W2/W15/GAU/W3/W3A tenant uploads become public after security scan + API verification
    """
    
    # Source type choices
    SOURCE_RRC = 'rrc'
    SOURCE_TENANT_UPLOAD = 'tenant_upload'
    SOURCE_OPERATOR_PACKET = 'operator_packet'
    SOURCE_NEUBUS = 'neubus'

    SOURCE_TYPE_CHOICES = [
        (SOURCE_RRC, 'RRC - Public Regulator Data'),
        (SOURCE_TENANT_UPLOAD, 'Tenant Upload - User Provided'),
        (SOURCE_OPERATOR_PACKET, 'Operator Packet - Approved Execution Plan'),
        (SOURCE_NEUBUS, 'Neubus - TX RRC Document Archive'),
    ]

    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE, related_name="extracted_documents", null=True, blank=True)

    # Raw identifiers for convenient lookup (may duplicate WellRegistry data for denormalized access)
    api_number = models.CharField(max_length=16, db_index=True)
    document_type = models.CharField(max_length=64, db_index=True)
    tracking_no = models.CharField(
        max_length=64,
        db_index=True,
        null=True,
        blank=True,
        help_text="Tracking No. from W-2 form header (used for revision tracking and consolidation)"
    )

    # Provenance
    source_path = models.TextField(blank=True)  # absolute/relative path where the file was saved
    neubus_filename = models.CharField(max_length=255, blank=True, db_index=True,
        help_text="Original filename from Neubus archive")
    source_page = models.PositiveIntegerField(null=True, blank=True,
        help_text="First page number of this form in the source document")
    file_hash = models.CharField(max_length=64, blank=True,
        help_text="SHA-256 hash of the source file")
    is_stale = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Marked True when extraction prompts change; forces re-extraction on next plan generation.",
    )
    form_group_index = models.PositiveIntegerField(null=True, blank=True,
        help_text="Nth form of this type in the document")
    model_tag = models.CharField(max_length=64, blank=True)  # e.g., gpt-4.1 / gpt-4.1-preview

    # Extraction status
    status = models.CharField(max_length=32, default="success")  # success|error|partial
    errors = models.JSONField(default=list)  # list of error strings or structured findings

    # Extracted JSON blob (schema varies by document_type but must include all required sections)
    json_data = models.JSONField()
    
    # Tenant attribution and validation (Phase 1)
    uploaded_by_tenant = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Tenant who uploaded this file (null for RRC-sourced documents)"
    )
    
    source_type = models.CharField(
        max_length=16,
        choices=SOURCE_TYPE_CHOICES,
        default=SOURCE_RRC,
        db_index=True,
        help_text="Origin of document: RRC public data or tenant upload"
    )
    
    is_validated = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Passed security scan and API verification (W2/W15/GAU/W3/W3A tenant uploads become public when validated)"
    )
    
    validation_errors = models.JSONField(
        default=list,
        help_text="List of validation failure reasons (security scan failures, API mismatches, etc.)"
    )

    segment = models.ForeignKey(
        'public_core.DocumentSegment',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='extractions',
    )

    attribution_confidence = models.CharField(
        max_length=8,
        default="low",
        choices=[("high", "High"), ("medium", "Medium"), ("low", "Low")],
        db_index=True,
        help_text="Confidence level of well attribution: high (extracted API), medium (cross-ref), low (session fallback)",
    )
    attribution_method = models.CharField(
        max_length=32,
        default="session_fallback",
        help_text="Method used to determine well attribution (e.g., extracted_api, well_no+lease_id, session_fallback)",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "public_core_extracted_documents"
        indexes = [
            models.Index(fields=["api_number", "document_type"]),
            models.Index(fields=["api_number", "document_type", "tracking_no"]),
            models.Index(fields=["tracking_no", "document_type"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["uploaded_by_tenant", "source_type"]),
            models.Index(fields=["is_validated", "document_type"]),
            # Index for checking existing extractions (reuse optimization)
            models.Index(fields=["api_number", "source_path", "document_type", "status", "is_stale"]),
            models.Index(fields=["neubus_filename"]),
            models.Index(fields=["attribution_confidence"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"ExtractedDocument<{self.api_number}:{self.document_type}:{self.source_type}:{self.id}>"
    
    def is_public(self) -> bool:
        """
        Determine if this document should be visible to all tenants.

        Rules:
        - RRC-sourced: always public
        - Tenant uploads of W2/W15/GAU/W3/W3A/C-103/C-105: public if validated
        - Tenant uploads of other types: never public (tenant-only)
        """
        if self.source_type == self.SOURCE_RRC:
            return True

        # Tenant uploads - use forms.py constants for consistency
        from apps.public_core.forms import PUBLIC_DOC_TYPES
        if self.document_type.lower() in PUBLIC_DOC_TYPES and self.is_validated:
            return True

        return False


