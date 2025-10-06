from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts


def test_08a_ector_surface_shoe_tagging():
    facts = {
        'api14': {'value': '42000000000000'},
        'state': {'value': 'TX'},
        'district': {'value': '08A'},
    }
    policy = get_effective_policy(district='08A', county='Andrews')
    # Mark policy complete for test purposes
    policy['complete'] = True
    policy['policy_id'] = 'tx.w3a'
    out = plan_from_facts(facts, policy)
    # Find surface shoe step and verify tagging or instructions present when overrides dictate
    shoe = next((s for s in out['steps'] if s.get('type') == 'surface_casing_shoe_plug'), None)
    assert shoe is not None
    assert shoe.get('tag_required') is True

