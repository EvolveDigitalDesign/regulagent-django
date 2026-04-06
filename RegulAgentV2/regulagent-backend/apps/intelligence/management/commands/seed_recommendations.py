"""
Management command to load cold-start seed rules from YAML into the database.

Usage:
    docker compose -f compose.dev.yml exec web python manage.py seed_recommendations
    docker compose -f compose.dev.yml exec web python manage.py seed_recommendations --clear  # clear existing cold_start entries first
"""

import os
import yaml
from django.core.management.base import BaseCommand

from apps.intelligence.models import RejectionPattern, Recommendation


FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__),  # commands/
    '..', '..', 'fixtures', 'seed_recommendations.yaml',
)


class Command(BaseCommand):
    help = 'Load cold-start seed recommendations from YAML fixture'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing cold_start recommendations before seeding',
        )

    def handle(self, *args, **options):
        fixture_path = os.path.normpath(FIXTURE_PATH)

        if not os.path.exists(fixture_path):
            self.stderr.write(self.style.ERROR(f'Fixture not found: {fixture_path}'))
            return

        with open(fixture_path, 'r') as f:
            entries = yaml.safe_load(f)

        if not entries:
            self.stdout.write(self.style.WARNING('Fixture is empty — nothing to seed.'))
            return

        if options['clear']:
            deleted_recs = Recommendation.objects.filter(scope='cold_start').delete()
            deleted_pats = RejectionPattern.objects.filter(
                recommendations__isnull=True
            ).delete()
            self.stdout.write(
                self.style.WARNING(
                    f'Cleared {deleted_recs[0]} cold_start recommendations '
                    f'and {deleted_pats[0]} orphaned patterns.'
                )
            )

        created_patterns = 0
        updated_patterns = 0
        created_recs = 0
        updated_recs = 0
        skipped = 0

        for entry in entries:
            try:
                # --- RejectionPattern ---
                pattern_key = {
                    'form_type': entry['form_type'],
                    'field_name': entry['field_name'],
                    'issue_category': entry['issue_category'],
                    'state': entry.get('state', ''),
                    'district': entry.get('district', '') or '',
                    'agency': entry['agency'],
                }

                pattern_defaults = {
                    'issue_subcategory': entry.get('issue_subcategory', ''),
                    'pattern_description': entry.get('pattern_description', ''),
                    'example_bad_value': entry.get('example_bad_value', '') or '',
                    'example_good_value': entry.get('example_good_value', '') or '',
                    'occurrence_count': 0,
                    'tenant_count': 0,
                    'confidence': 0.8,
                }

                pattern, pat_created = RejectionPattern.objects.update_or_create(
                    **pattern_key,
                    defaults=pattern_defaults,
                )

                if pat_created:
                    created_patterns += 1
                    self.stdout.write(
                        f'  [pattern] CREATED  {pattern.form_type}/{pattern.field_name} '
                        f'({pattern.issue_category}, {pattern.agency})'
                    )
                else:
                    updated_patterns += 1
                    self.stdout.write(
                        f'  [pattern] UPDATED  {pattern.form_type}/{pattern.field_name} '
                        f'({pattern.issue_category}, {pattern.agency})'
                    )

                # --- Recommendation ---
                trigger_condition = entry.get('trigger_condition') or {}

                rec_defaults = {
                    'title': entry['title'],
                    'description': entry['description'].strip() if entry.get('description') else '',
                    'suggested_value': entry.get('suggested_value', '') or '',
                    'trigger_condition': trigger_condition,
                    'priority': entry.get('priority', 'medium'),
                    'form_type': entry['form_type'],
                    'field_name': entry['field_name'],
                    'state': entry.get('state', ''),
                    'district': entry.get('district', '') or '',
                    'is_active': True,
                }

                rec, rec_created = Recommendation.objects.update_or_create(
                    pattern=pattern,
                    scope='cold_start',
                    defaults=rec_defaults,
                )

                if rec_created:
                    created_recs += 1
                    self.stdout.write(
                        f'  [rec]     CREATED  "{rec.title}"'
                    )
                else:
                    updated_recs += 1
                    self.stdout.write(
                        f'  [rec]     UPDATED  "{rec.title}"'
                    )

            except KeyError as exc:
                skipped += 1
                self.stderr.write(
                    self.style.ERROR(
                        f'  SKIPPED entry (missing field {exc}): '
                        f'{entry.get("form_type", "?")} / {entry.get("field_name", "?")}'
                    )
                )
            except Exception as exc:  # noqa: BLE001
                skipped += 1
                self.stderr.write(
                    self.style.ERROR(
                        f'  SKIPPED entry due to error ({exc.__class__.__name__}: {exc}): '
                        f'{entry.get("form_type", "?")} / {entry.get("field_name", "?")}'
                    )
                )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done. '
            f'Patterns: {created_patterns} created, {updated_patterns} updated. '
            f'Recommendations: {created_recs} created, {updated_recs} updated. '
            f'Skipped: {skipped}.'
        ))
