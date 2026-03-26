"""
Backfill DocumentSegment records for existing ExtractedDocuments.

Usage:
    python manage.py backfill_document_segments
    python manage.py backfill_document_segments --dry-run
    python manage.py backfill_document_segments --well 42003356630000
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.public_core.models import ExtractedDocument, WellRegistry
from apps.public_core.models.document_segment import DocumentSegment


class Command(BaseCommand):
    help = "Create DocumentSegment records for existing ExtractedDocuments that lack them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be created without writing to DB",
        )
        parser.add_argument(
            "--well",
            type=str,
            help="Only backfill for a specific well (api14)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Process in batches of N (default: 500)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        well_filter = options["well"]
        batch_size = options["batch_size"]

        # Find EDs without a linked segment
        qs = ExtractedDocument.objects.filter(
            segment__isnull=True,
            status__in=["success", "partial"],
        )
        if well_filter:
            qs = qs.filter(api_number__icontains=well_filter)

        total = qs.count()
        self.stdout.write(f"Found {total} ExtractedDocuments without segments")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no records will be created"))

        created = 0
        skipped = 0

        for i in range(0, total, batch_size):
            batch = list(qs[i:i + batch_size])
            for ed in batch:
                # Determine source_type
                if ed.source_type == ExtractedDocument.SOURCE_NEUBUS:
                    source_type = "neubus"
                elif ed.source_type == ExtractedDocument.SOURCE_TENANT_UPLOAD:
                    source_type = "upload"
                else:
                    source_type = "upload"

                # Determine page range from source_page and form_group_index
                page_start = (ed.source_page - 1) if ed.source_page else 0
                page_end = page_start  # Single page approximation for backfill

                source_filename = ed.neubus_filename or ed.source_path.split("/")[-1] if ed.source_path else ""

                if dry_run:
                    self.stdout.write(
                        f"  Would create: {source_filename} [{ed.document_type}] "
                        f"page {page_start} for ED {ed.id}"
                    )
                else:
                    try:
                        with transaction.atomic():
                            seg = DocumentSegment.objects.create(
                                well=ed.well,
                                api_number=ed.api_number,
                                source_filename=source_filename,
                                source_path=ed.source_path or "",
                                file_hash=ed.file_hash or "",
                                source_type=source_type,
                                page_start=page_start,
                                page_end=page_end,
                                total_source_pages=0,
                                form_type=ed.document_type,
                                classification_method="backfill",
                                classification_confidence="high",
                                classification_evidence="Backfilled from existing ExtractedDocument",
                                tags=[],
                                status="extracted",
                                extracted_document=ed,
                                raw_text_cache="",
                            )
                            ed.segment = seg
                            ed.save(update_fields=["segment"])
                            created += 1
                    except Exception as e:
                        self.stderr.write(f"  Error for ED {ed.id}: {e}")
                        skipped += 1

            if not dry_run:
                self.stdout.write(f"  Processed {min(i + batch_size, total)}/{total}")

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"Would create {total} DocumentSegment records"))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Created {created} DocumentSegment records, skipped {skipped}"
            ))
