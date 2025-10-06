from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts


def test_7c_ops_and_must_tag_instructions():
    facts = {
        'api14': {'value': '42000000000000'},
        'state': {'value': 'TX'},
        'district': {'value': '7C'},
    }
    policy = get_effective_policy(district='7C', county='Coke County')
    policy['complete'] = True
    policy['policy_id'] = 'tx.w3a'
    out = plan_from_facts(facts, policy)
    shoe = next((s for s in out['steps'] if s.get('type') == 'surface_casing_shoe_plug'), None)
    assert shoe is not None
    # special instructions should include tubing-only and mud/funnel specs
    instr = shoe.get('special_instructions', '')
    assert 'Pump via tubing/drill pipe only' in instr or 'Mud' in instr or 'Funnel' in instr
