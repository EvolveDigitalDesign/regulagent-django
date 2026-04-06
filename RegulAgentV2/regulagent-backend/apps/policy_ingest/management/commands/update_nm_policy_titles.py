from django.core.management.base import BaseCommand
from apps.policy_ingest.models import PolicyRule


class Command(BaseCommand):
    help = 'Backfill titles on NM OCD NMAC 19.15.25 policy rules.'

    def handle(self, *args, **options):
        rule_titles = {
            'nm.nmac.19.15.25.1': 'Issuing Agency',
            'nm.nmac.19.15.25.2': 'Scope',
            'nm.nmac.19.15.25.3': 'Statutory Authority',
            'nm.nmac.19.15.25.4': 'Duration',
            'nm.nmac.19.15.25.5': 'Effective Date',
            'nm.nmac.19.15.25.6': 'Objective',
            'nm.nmac.19.15.25.7': 'Definitions',
            'nm.nmac.19.15.25.8': 'Wells to Be Properly Abandoned',
            'nm.nmac.19.15.25.9': 'Notice of Plugging',
            'nm.nmac.19.15.25.10': 'Plugging',
            'nm.nmac.19.15.25.11': 'Reports for Plugging and Abandonment',
            'nm.nmac.19.15.25.12': 'Approved Temporary Abandonment',
            'nm.nmac.19.15.25.13': 'Request for Approval and Permit for Approved Temporary Abandonment',
            'nm.nmac.19.15.25.14': 'Demonstrating Mechanical Integrity',
            'nm.nmac.19.15.25.15': 'Wells to Be Used for Fresh Water',
        }

        for rule_id, title in rule_titles.items():
            updated = PolicyRule.objects.filter(rule_id=rule_id).update(title=title)
            if updated:
                self.stdout.write(self.style.SUCCESS(f"Updated title for {rule_id}: {title}"))
            else:
                self.stdout.write(self.style.WARNING(f"No rule found for {rule_id}"))
