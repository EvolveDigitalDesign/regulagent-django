from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Backfill jurisdiction/doc_type/topic tags for NM OCD NMAC 19.15.25 policy rules."

    def handle(self, *args, **options):
        from apps.policy_ingest.models import PolicyRule

        qs = PolicyRule.objects.filter(rule_id__startswith='nm.nmac.19.15.25.')
        total = qs.count()
        updated = 0

        topic_map = {
            'nm.nmac.19.15.25.1': 'admin_issuing_agency',
            'nm.nmac.19.15.25.2': 'admin_scope',
            'nm.nmac.19.15.25.3': 'admin_statutory_authority',
            'nm.nmac.19.15.25.4': 'admin_duration',
            'nm.nmac.19.15.25.5': 'admin_effective_date',
            'nm.nmac.19.15.25.6': 'admin_objective',
            'nm.nmac.19.15.25.7': 'definitions',
            'nm.nmac.19.15.25.8': 'plugging_requirements',
            'nm.nmac.19.15.25.9': 'plugging_notice',
            'nm.nmac.19.15.25.10': 'plugging',
            'nm.nmac.19.15.25.11': 'plugging_reports',
            'nm.nmac.19.15.25.12': 'temporary_abandonment',
            'nm.nmac.19.15.25.13': 'temporary_abandonment_permit',
            'nm.nmac.19.15.25.14': 'mechanical_integrity',
            'nm.nmac.19.15.25.15': 'fresh_water_wells',
        }

        for rule in qs.iterator():
            changed = False
            if not rule.jurisdiction:
                rule.jurisdiction = 'NM'
                changed = True
            if not rule.doc_type:
                rule.doc_type = 'policy'
                changed = True
            # Only set topic if not already set
            if not rule.topic:
                if rule.rule_id in topic_map:
                    rule.topic = topic_map[rule.rule_id]
                    changed = True
                else:
                    # Handle possible alternate entries with same rule_id prefix
                    for key, val in topic_map.items():
                        if rule.rule_id.startswith(key):
                            rule.topic = val
                            changed = True
                            break
            if changed:
                rule.save(update_fields=['jurisdiction', 'doc_type', 'topic'])
                updated += 1

        self.stdout.write(f"Processed {total} rules; updated {updated}.")
