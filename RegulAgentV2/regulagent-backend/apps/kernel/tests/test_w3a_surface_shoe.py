from apps.kernel.services.policy_kernel import plan_from_facts


def test_w3a_surface_shoe_step_emitted():
    facts = {
        'api14': {'value': '42000000000000'},
        'state': {'value': 'TX'},
    }
    policy = {
        'policy_id': 'tx.w3a',
        'policy_version': '2025.10.0',
        'jurisdiction': 'TX',
        'form': 'W-3A',
        'base': {},
        'effective': {
            'requirements': {
                'surface_casing_shoe_plug_min_ft': {'value': 100, 'citation_keys': ['tx.tac.16.3.14(e)(2)']}
            }
        },
        'district': None,
        'complete': True,
        'incomplete_reasons': [],
    }
    out = plan_from_facts(facts, policy)
    assert out['policy_complete'] is True
    assert out['steps'] and out['steps'][0]['type'] == 'surface_casing_shoe_plug'
    assert out['steps'][0]['min_length_ft'] == 100.0
    assert 'tx.tac.16.3.14(e)(2)' in out['steps'][0]['regulatory_basis']

