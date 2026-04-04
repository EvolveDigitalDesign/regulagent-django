from django.test import TestCase
from apps.kernel.services.policy_kernel import plan_from_facts
from apps.policy.services import loader


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
        # Call loader to fetch complete effective from base policy pack
        effective_policy = loader.get_effective_policy()
        policy['effective'] = effective_policy.get('base') or {}
        out = plan_from_facts(facts, policy)

        # Verify high-level envelope fields
        self.assertEqual(out['kernel_version'], out['kernel_version'])  # just ensure present
        self.assertEqual(out['overlay_version'], '2025.10.0')
        self.assertEqual(out['jurisdiction'], 'TX')
        self.assertEqual(out['form'], 'W-3A')
        self.assertIsNone(out['district'])
        self.assertTrue(out['policy_complete'])
        self.assertEqual(out['citations'], [])
        self.assertEqual(out['inputs_summary'], {'api14': '42000000000000', 'state': 'TX'})
        self.assertEqual(out['safety_stock_sacks'], 0)

        # Verify required steps are present with correct regulatory basis
        step_types = [s['type'] for s in out['steps']]
        self.assertIn('surface_casing_shoe_plug', step_types)
        self.assertIn('cibp_cap', step_types)
        self.assertIn('uqw_isolation_plug', step_types)

        shoe = next(s for s in out['steps'] if s['type'] == 'surface_casing_shoe_plug')
        self.assertEqual(shoe['min_length_ft'], 100.0)
        self.assertIn('tx.tac.16.3.14(e)(2)', shoe['regulatory_basis'])
        self.assertIn('slurry', shoe['materials'])
        self.assertIn('fluids', shoe['materials'])

        cibp = next(s for s in out['steps'] if s['type'] == 'cibp_cap')
        self.assertEqual(cibp['cap_length_ft'], 20.0)
        self.assertIn('tx.tac.16.3.14(g)(3)', cibp['regulatory_basis'])

        uqw = next(s for s in out['steps'] if s['type'] == 'uqw_isolation_plug')
        self.assertEqual(uqw['min_length_ft'], 100.0)
        self.assertEqual(uqw['below_ft'], 50.0)
        self.assertEqual(uqw['above_ft'], 50.0)
        self.assertIn('tx.tac.16.3.14(g)(1)', uqw['regulatory_basis'])
