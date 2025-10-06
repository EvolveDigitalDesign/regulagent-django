from apps.kernel.services.policy_kernel import plan_from_facts


def test_w3a_uqw_isolation_step():
    facts = {
        'api14': {'value': '42000000000000'},
        'state': {'value': 'TX'},
        'has_uqw': {'value': True},
    }
    policy = {
        'policy_id': 'tx.w3a',
        'policy_version': '2025.10.0',
        'jurisdiction': 'TX',
        'form': 'W-3A',
        'base': {},
        'effective': {
            'requirements': {
                'uqw_isolation_min_len_ft': {'value': 100, 'citation_keys': ['tx.tac.16.3.14(g)(1)']},
                'uqw_below_base_ft': {'value': 50, 'citation_keys': ['tx.tac.16.3.14(g)(1)']},
                'uqw_above_base_ft': {'value': 50, 'citation_keys': ['tx.tac.16.3.14(g)(1)']},
            }
        },
        'district': None,
        'complete': True,
        'incomplete_reasons': [],
    }
    out = plan_from_facts(facts, policy)
    types = [s['type'] for s in out['steps']]
    assert 'uqw_isolation_plug' in types
    step = next(s for s in out['steps'] if s['type'] == 'uqw_isolation_plug')
    assert step['min_length_ft'] == 100.0 and step['below_ft'] == 50.0 and step['above_ft'] == 50.0
    assert 'tx.tac.16.3.14(g)(1)' in step['regulatory_basis']

