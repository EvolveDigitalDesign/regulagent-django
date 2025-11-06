from __future__ import annotations

import uuid
from django.db import models
from pgvector.django import VectorField

from .well_registry import WellRegistry


class DocumentVector(models.Model):
    """
    Semantic vector entries for extracted regulatory documents.
    Each row represents one logical section/chunk embedded for ANN search.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    well = models.ForeignKey(WellRegistry, on_delete=models.CASCADE, related_name="document_vectors", null=True, blank=True)

    file_name = models.CharField(max_length=255)
    document_type = models.CharField(max_length=50, db_index=True)
    section_name = models.CharField(max_length=255, db_index=True)
    section_text = models.TextField()

    # Embedding size 1536 for text-embedding-3-*; adjust via migration if model changes
    embedding = VectorField(dimensions=1536)

    metadata = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "public_core_document_vectors"
        indexes = [
            models.Index(fields=["well", "document_type"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"DocumentVector<{self.document_type}:{self.section_name}:{self.id}>"


