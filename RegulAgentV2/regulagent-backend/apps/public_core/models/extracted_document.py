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
    
    SOURCE_TYPE_CHOICES = [
        (SOURCE_RRC, 'RRC - Public Regulator Data'),
        (SOURCE_TENANT_UPLOAD, 'Tenant Upload - User Provided'),
    ]

    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE, related_name="extracted_documents", null=True, blank=True)

    # Raw identifiers for convenient lookup (may duplicate WellRegistry data for denormalized access)
    api_number = models.CharField(max_length=16, db_index=True)
    document_type = models.CharField(max_length=64, db_index=True)

    # Provenance
    source_path = models.TextField(blank=True)  # absolute/relative path where the file was saved
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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "public_core_extracted_documents"
        indexes = [
            models.Index(fields=["api_number", "document_type"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["uploaded_by_tenant", "source_type"]),
            models.Index(fields=["is_validated", "document_type"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"ExtractedDocument<{self.api_number}:{self.document_type}:{self.source_type}:{self.id}>"
    
    def is_public(self) -> bool:
        """
        Determine if this document should be visible to all tenants.
        
        Rules:
        - RRC-sourced: always public
        - Tenant uploads of W2/W15/GAU/W3/W3A: public if validated
        - Tenant uploads of other types: never public (tenant-only)
        """
        if self.source_type == self.SOURCE_RRC:
            return True
        
        # Tenant uploads
        PUBLIC_DOC_TYPES = ['w2', 'w15', 'gau', 'w3', 'w3a']
        if self.document_type.lower() in PUBLIC_DOC_TYPES and self.is_validated:
            return True
        
        return False


