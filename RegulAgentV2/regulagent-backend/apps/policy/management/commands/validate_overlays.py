from django.core.management.base import BaseCommand
from apps.policy.services.validate_overlays import validate_policy_file


class Command(BaseCommand):
    help = "Validate policy overlays against minimal knob/citation rules."

    def add_arguments(self, parser):
        parser.add_argument('--rel-path', default='tx/w3a/draft.yml', help='Relative path under apps/policy/packs')

    def handle(self, *args, **options):
        rel = options['rel_path']
        errors = validate_policy_file(rel)
        if errors:
            for e in errors:
                self.stdout.write(f"ERROR: {e}")
            raise SystemExit(1)
        self.stdout.write("OK: overlays valid")


