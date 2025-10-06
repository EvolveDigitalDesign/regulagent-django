from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts


def test_7c_coke_formation_top_plugs():
    facts = {
        'api14': {'value': '42000000000000'},
        'state': {'value': 'TX'},
        'district': {'value': '7C'},
    }
    policy = get_effective_policy(district='7C', county='Coke County')
    policy['complete'] = True
    policy['policy_id'] = 'tx.w3a'
    out = plan_from_facts(facts, policy)
    # Expect at least one formation_top_plug step
    ft_steps = [s for s in out['steps'] if s.get('type') == 'formation_top_plug']
    assert len(ft_steps) > 0
    # Ensure Coleman Junction is treated as a single formation name if present
    if any(s.get('formation') == 'Coleman Junction' for s in ft_steps):
        cj = next(s for s in ft_steps if s.get('formation') == 'Coleman Junction')
        assert 'tag_required' in cj and cj['tag_required'] is True

