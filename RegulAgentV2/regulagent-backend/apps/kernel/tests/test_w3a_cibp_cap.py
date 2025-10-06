from apps.kernel.services.policy_kernel import plan_from_facts


def test_w3a_cibp_cap_emitted_when_cibp_used():
    facts = {
        'api14': {'value': '42000000000000'},
        'state': {'value': 'TX'},
        'use_cibp': {'value': True},
    }
    policy = {
        'policy_id': 'tx.w3a',
        'policy_version': '2025.10.0',
        'jurisdiction': 'TX',
        'form': 'W-3A',
        'base': {},
        'effective': {
            'requirements': {
                'cement_above_cibp_min_ft': {'value': 20, 'citation_keys': ['tx.tac.16.3.14(g)(3)']}
            }
        },
        'district': None,
        'complete': True,
        'incomplete_reasons': [],
    }
    out = plan_from_facts(facts, policy)
    types = [s['type'] for s in out['steps']]
    assert 'cibp_cap' in types
    cap_step = next(s for s in out['steps'] if s['type'] == 'cibp_cap')
    assert cap_step['cap_length_ft'] == 20.0
    assert 'tx.tac.16.3.14(g)(3)' in cap_step['regulatory_basis']

