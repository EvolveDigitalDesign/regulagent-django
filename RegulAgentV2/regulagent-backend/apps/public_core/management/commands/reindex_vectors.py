"""Re-embed all extracted documents using the current embedding model.

Differences from backfill_vectors:
- Deletes existing vectors before re-creating them (true re-index, not skip-if-exists).
- Supports --resume to skip documents that already have vectors (opt-in backfill mode).
- Supports --api-prefix filter for jurisdiction-scoped runs (e.g. 30 for NM wells).
- Supports --dry-run to preview scope without mutating any data.
- Supports --batch-size (cosmetic: controls progress-log frequency; each doc is still
  processed individually to match the existing vectorize_extracted_document signature).

Usage:
    python manage.py reindex_vectors
    python manage.py reindex_vectors --batch-size 20
    python manage.py reindex_vectors --doc-type gau
    python manage.py reindex_vectors --api-prefix 30   # NM wells only
    python manage.py reindex_vectors --resume          # skip docs that already have vectors
    python manage.py reindex_vectors --dry-run
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from apps.public_core.models import ExtractedDocument
from apps.public_core.models.document_vector import DocumentVector
from apps.public_core.services.openai_extraction import (
    iter_json_sections_for_embedding,
    vectorize_extracted_document,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Delete and re-embed all extracted document vectors using the current embedding model."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Number of documents between progress log lines (default: 50).",
        )
        parser.add_argument(
            "--doc-type",
            type=str,
            default=None,
            help="Filter by document_type (e.g. gau, w2, w15, schematic, formation_tops).",
        )
        parser.add_argument(
            "--api-prefix",
            type=str,
            default=None,
            help="Filter by API number prefix (e.g. '30' for NM wells).",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Skip documents that already have at least one DocumentVector row (backfill mode).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview which documents would be processed without making any changes.",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        doc_type = (options["doc_type"] or "").strip().lower() or None
        api_prefix = (options["api_prefix"] or "").strip() or None
        resume = options["resume"]
        dry_run = options["dry_run"]

        qs = ExtractedDocument.objects.filter(status="success").order_by("created_at")
        if doc_type:
            qs = qs.filter(document_type=doc_type)
        if api_prefix:
            qs = qs.filter(api_number__startswith=api_prefix)

        total = qs.count()
        self.stdout.write(
            f"Found {total} extracted document(s) matching filters"
            + (f" [doc_type={doc_type}]" if doc_type else "")
            + (f" [api_prefix={api_prefix}]" if api_prefix else "")
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("[DRY RUN] No changes will be made."))

        processed = 0
        skipped = 0
        errors = 0
        vectors_deleted = 0
        vectors_created = 0

        for ed in qs.iterator():
            # --resume: skip documents that already have vector rows
            if resume:
                already_indexed = DocumentVector.objects.filter(
                    metadata__ed_id=str(ed.id)
                ).exists()
                if already_indexed:
                    skipped += 1
                    continue

            if dry_run:
                section_pairs = iter_json_sections_for_embedding(
                    ed.document_type, ed.json_data or {}
                )
                self.stdout.write(
                    f"  [DRY] {ed.api_number} / {ed.document_type}"
                    f" — {len(section_pairs)} section(s) would be embedded"
                )
                processed += 1
                if processed % batch_size == 0:
                    self.stdout.write(f"  Progress: {processed}/{total} (dry run)")
                continue

            try:
                # Delete existing vectors for this document before re-creating them
                deleted, _ = DocumentVector.objects.filter(
                    metadata__ed_id=str(ed.id)
                ).delete()
                vectors_deleted += deleted

                # Delegate to the canonical vectorization function so all metadata
                # enrichment (district, tenant_id, well context, etc.) is applied
                # consistently with the live pipeline.
                count = vectorize_extracted_document(ed)
                vectors_created += count
                processed += 1

                if processed % batch_size == 0:
                    self.stdout.write(
                        f"  Progress: {processed}/{total}"
                        f" (+{vectors_created} created,"
                        f" -{vectors_deleted} deleted,"
                        f" {skipped} skipped,"
                        f" {errors} errors)"
                    )

            except Exception as exc:
                errors += 1
                logger.error("reindex_vectors: error processing ED %s: %s", ed.id, exc)
                self.stderr.write(
                    f"  ERROR: ED {ed.id} ({ed.api_number} / {ed.document_type}): {exc}"
                )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"\n[DRY RUN] Would process {processed} document(s), "
                    f"skip {skipped}."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nDone."
                    f" Processed: {processed},"
                    f" Skipped: {skipped},"
                    f" Errors: {errors},"
                    f" Vectors deleted: {vectors_deleted},"
                    f" Vectors created: {vectors_created}"
                )
            )
