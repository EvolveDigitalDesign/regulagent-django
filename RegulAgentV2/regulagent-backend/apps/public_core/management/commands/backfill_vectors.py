from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.public_core.models import ExtractedDocument
from apps.public_core.models.document_vector import DocumentVector
from apps.public_core.services.openai_extraction import vectorize_extracted_document


class Command(BaseCommand):
    help = "Vectorize ExtractedDocuments into DocumentVector if not already embedded (by ed_id)."

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=200, help='Max documents to process (0 for all).')
        parser.add_argument('--doc-type', type=str, default='', help='Filter by document_type (w2|w15|gau|schematic|formation_tops)')

    def handle(self, *args, **options):
        limit = int(options.get('limit') or 0)
        doc_type = (options.get('doc_type') or '').strip().lower()
        qs = ExtractedDocument.objects.all().order_by('-created_at')
        if doc_type:
            qs = qs.filter(document_type=doc_type)
        processed = 0
        created = 0
        for ed in (qs if limit == 0 else qs[:limit]):
            processed += 1
            exists = DocumentVector.objects.filter(metadata__ed_id=str(ed.id)).exists()
            if exists:
                continue
            created += vectorize_extracted_document(ed)
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} docs, created {created} vectors"))


