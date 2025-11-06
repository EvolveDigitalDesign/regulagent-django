from django.test import TestCase
from apps.kernel.services.policy_kernel import plan_from_facts


class W3AGoldenSimplePlanTestCase(TestCase):

    def test_w3a_golden_simple_plan(self):
        facts = {
            'api14': {'value': '42000000000000'},
            'state': {'value': 'TX'},
            'use_cibp': {'value': True},
            'has_uqw': {'value': True},
        }
        policy = {
            'policy_id': 'tx.w3a',
            'policy_version': '2025.10.0',
            'jurisdiction': 'TX',
            'form': 'W-3A',
            # Load effective from the complete base policy pack
            'effective': {},
            'district': None,
            'complete': True,
            'incomplete_reasons': [],
        }
        # Call loader to fetch complete effect from base policy pack
        effective_policy = loader.get_effective_policy()
        policy['effective'] = effective_policy.get('base') or {}
        out = plan_from_facts(facts, policy)
        expected = {
            'kernel_version': out['kernel_version'],
            'overlay_version': '2025.10.0',
            'jurisdiction': 'TX',
            'form': 'W-3A',
            'district': None,
            'policy_complete': True,
            'constraints': [],
            'violations': [],
            'rounding_policy': {'sacks': 'ceil_per_step'},
            'safety_stock_sacks': 0,
            'citations': [],
            'inputs_summary': {'api14': '42000000000000', 'state': 'TX'},
            'steps': [
                {
                    'type': 'surface_casing_shoe_plug',
                    'min_length_ft': 100.0,
                    'regulatory_basis': ['tx.tac.16.3.14(e)(2)'],
                    'materials': {'slurry': {}, 'fluids': {}},
                },
                {
                    'type': 'cibp_cap',
                    'cap_length_ft': 20.0,
                    'regulatory_basis': ['tx.tac.16.3.14(g)(3)'],
                    'materials': {'slurry': {}, 'fluids': {}},
                },
                {
                    'type': 'uqw_isolation_plug',
                    'min_length_ft': 100.0,
                    'below_ft': 50.0,
                    'above_ft': 50.0,
                    'regulatory_basis': ['tx.tac.16.3.14(g)(1)'],
                    'materials': {'slurry': {}, 'fluids': {}},
                },
            ],
        }
        self.assertEqual(out, expected)


