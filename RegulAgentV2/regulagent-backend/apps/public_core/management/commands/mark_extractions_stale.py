"""Mark ExtractedDocument rows as stale so they are re-extracted on next plan generation."""

from django.core.management.base import BaseCommand

from apps.public_core.models import ExtractedDocument


class Command(BaseCommand):
    help = "Mark ExtractedDocument rows as stale, forcing re-extraction on next plan generation."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Mark ALL documents stale")
        parser.add_argument("--doc-type", type=str, help="Only mark this document type stale (e.g. w2, gau, w15)")
        parser.add_argument("--api", type=str, help="Only mark documents for this API number stale")
        parser.add_argument("--dry-run", action="store_true", help="Show count without updating")

    def handle(self, *args, **options):
        qs = ExtractedDocument.objects.filter(is_stale=False, status="success")

        if not options["all"] and not options["doc_type"] and not options["api"]:
            self.stderr.write(self.style.ERROR("Specify --all, --doc-type, or --api (or a combination)."))
            return

        if options["doc_type"]:
            qs = qs.filter(document_type=options["doc_type"])
        if options["api"]:
            qs = qs.filter(api_number=options["api"])

        count = qs.count()

        if options["dry_run"]:
            self.stdout.write(f"Would mark {count} document(s) as stale.")
            return

        updated = qs.update(is_stale=True)
        self.stdout.write(self.style.SUCCESS(f"Marked {updated} document(s) as stale."))
