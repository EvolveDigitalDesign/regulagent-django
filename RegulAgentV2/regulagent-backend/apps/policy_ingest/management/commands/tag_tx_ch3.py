from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Backfill jurisdiction/doc_type/topic tags for TX TAC Chapter 3 policy rules."

    def handle(self, *args, **options):
        from apps.policy_ingest.models import PolicyRule

        qs = PolicyRule.objects.filter(rule_id__startswith='tx.tac.16.3.')
        total = qs.count()
        updated = 0

        topic_map = {
            'tx.tac.16.3.1': 'admin_records',
            'tx.tac.16.3.2': 'enforcement_access',
            'tx.tac.16.3.3': 'identification',
            'tx.tac.16.3.4': 'forms_ids',
            'tx.tac.16.3.5': 'drilling_permits',
            'tx.tac.16.3.6': 'multiple_completion',
            'tx.tac.16.3.7': 'strata_sealed_off',
            'tx.tac.16.3.8': 'water_protection',
            'tx.tac.16.3.9': 'disposal_wells',
            'tx.tac.16.3.10': 'production_restriction_strata',
            'tx.tac.16.3.11': 'directional_surveys',
            'tx.tac.16.3.12': 'survey_company_reports',
            'tx.tac.16.3.13': 'casing_cementing_completion',
            'tx.tac.16.3.14': 'plugging',
            'tx.tac.16.3.15': 'inactive_wells_surface_equipment',
            'tx.tac.16.3.16': 'log_completion_plugging_reports',
            'tx.tac.16.3.17': 'bradenhead_pressure',
            'tx.tac.16.3.18': 'mud_circulation',
            'tx.tac.16.3.19': 'mud_density',
            'tx.tac.16.3.20': 'incident_notification',
            'tx.tac.16.3.21': 'fire_prevention_swabbing',
            'tx.tac.16.3.22': 'protection_of_birds',
            'tx.tac.16.3.23': 'vacuum_pumps',
            'tx.tac.16.3.24': 'check_valves',
            'tx.tac.16.3.25': 'common_storage',
            'tx.tac.16.3.26': 'surface_facilities_commingling',
            'tx.tac.16.3.27': 'gas_measurement',
            'tx.tac.16.3.28': 'gas_deliverability',
            'tx.tac.16.3.29': 'fracking_disclosure',
            'tx.tac.16.3.30': 'mou_tceq',
            'tx.tac.16.3.31': 'gas_reservoirs_allowable',
            'tx.tac.16.3.32': 'gas_utilization',
            'tx.tac.16.3.33': 'geothermal_tests',
            'tx.tac.16.3.34': 'gas_ratable',
            'tx.tac.16.3.35': 'abandoned_logging_tools',
            'tx.tac.16.3.36': 'h2s_areas',
            'tx.tac.16.3.37': 'spacing',
            'tx.tac.16.3.38': 'well_density',
            'tx.tac.16.3.39': 'proration_drilling_units',
            'tx.tac.16.3.40': 'acreage_assignment',
            'tx.tac.16.3.41': 'new_field_designation',
            'tx.tac.16.3.42': 'oil_discovery_allowable',
            'tx.tac.16.3.43': 'temporary_field_rules',
            'tx.tac.16.3.45': 'oil_allowables',
            'tx.tac.16.3.46': 'fluid_injection',
            'tx.tac.16.3.47': 'injection_allowable_transfers',
            'tx.tac.16.3.48': 'eor_capacity_allowables',
            'tx.tac.16.3.49': 'gas_oil_ratio',
            'tx.tac.16.3.50': 'eor_tax_incentive',
            'tx.tac.16.3.51': 'oil_potential_tests',
            'tx.tac.16.3.52': 'oil_allowable_production',
            'tx.tac.16.3.53': 'annual_well_tests_status',
            'tx.tac.16.3.54': 'gas_reports',
            'tx.tac.16.3.55': 'commingling_liquids_before_metering',
            'tx.tac.16.3.56': 'scrubber_oil_skim',
            'tx.tac.16.3.57': 'waste_reclaiming',
            'tx.tac.16.3.58': 'compliance_transport',
            'tx.tac.16.3.59': 'transporter_reports',
            'tx.tac.16.3.60': 'refinery_reports',
            'tx.tac.16.3.61': 'definitions',
            'tx.tac.16.3.62': 'legal_prerequisites',
            'tx.tac.16.3.63': 'sovereign_immunity',
            'tx.tac.16.3.65': 'critical_gas_infrastructure',
            'tx.tac.16.3.66': 'weather_preparedness',
            'tx.tac.16.3.70': 'pipeline_permits',
            'tx.tac.16.3.71': 'negotiation_costs',
            'tx.tac.16.3.72': 'contested_case',
            'tx.tac.16.3.73': 'pipeline_connection_severance',
            'tx.tac.16.3.76': 'mediation_costs',
            'tx.tac.16.3.78': 'fees_financial_security',
            'tx.tac.16.3.79': 'definitions',
            'tx.tac.16.3.80': 'forms_filing_requirements',
            'tx.tac.16.3.81': 'brine_mining_injection',
            'tx.tac.16.3.82': 'brine_production_projects',
            'tx.tac.16.3.83': 'tax_inactive_wells',
            'tx.tac.16.3.84': 'gas_shortage_response',
            'tx.tac.16.3.85': 'transport_manifest',
            'tx.tac.16.3.86': 'horizontal_drainhole_wells',
            'tx.tac.16.3.91': 'spill_cleanup',
            'tx.tac.16.3.93': 'water_quality_certification',
            'tx.tac.16.3.95': 'storage_liquids_salt',
            'tx.tac.16.3.96': 'gas_storage_reservoirs',
            'tx.tac.16.3.97': 'gas_storage_salt',
            'tx.tac.16.3.98': 'hazardous_waste_management',
            'tx.tac.16.3.99': 'cathodic_protection_wells',
            'tx.tac.16.3.100': 'seismic_core_holes',
            'tx.tac.16.3.101': 'tax_high_cost_gas',
            'tx.tac.16.3.102': 'tax_incremental_production',
            'tx.tac.16.3.103': 'tax_casinghead_gas_flare',
            'tx.tac.16.3.106': 'sour_gas_pipeline_permits',
            'tx.tac.16.3.107': 'penalty_guidelines',
        }

        for rule in qs.iterator():
            changed = False
            if not rule.jurisdiction:
                rule.jurisdiction = 'TX'
                changed = True
            if not rule.doc_type:
                rule.doc_type = 'policy'
                changed = True
            # Only set topic if not already set
            if not rule.topic:
                # exact match first, then prefix for duplicated entries
                if rule.rule_id in topic_map:
                    rule.topic = topic_map[rule.rule_id]
                    changed = True
                else:
                    # handle possible alternate URL-duplicated entries with same rule_id
                    for key, val in topic_map.items():
                        if rule.rule_id.startswith(key):
                            rule.topic = val
                            changed = True
                            break
            if changed:
                rule.save(update_fields=['jurisdiction', 'doc_type', 'topic'])
                updated += 1

        self.stdout.write(f"Processed {total} rules; updated {updated}.")


