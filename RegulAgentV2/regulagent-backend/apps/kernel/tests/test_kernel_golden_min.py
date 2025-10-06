from apps.kernel.services.policy_kernel import plan_from_facts


def test_policy_incomplete_returns_constraints():
    facts = {
        'api14': {'value': '42000000000000'},
        'state': {'value': 'TX'},
    }
    policy = {
        'policy_id': 'tx.w3a',
        'policy_version': '2025.10.0',
        'jurisdiction': 'TX',
        'form': 'W-3A',
        'base': {
            'citations': {},
            'requirements': {},
            'cement_class': {},
        },
        'effective': {},
        'district': None,
        'complete': False,
        'incomplete_reasons': ['base.requirements.casing_shoe_coverage_ft'],
    }
    out = plan_from_facts(facts, policy)
    assert out['policy_complete'] is False
    assert out['constraints'] and out['constraints'][0]['code'] == 'policy_incomplete'

