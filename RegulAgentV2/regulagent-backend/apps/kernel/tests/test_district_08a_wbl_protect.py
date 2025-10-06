from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts


def test_08a_ector_wbl_sr_protect_triggers_tagging():
    facts = {
        'api14': {'value': '42000000000000'},
        'state': {'value': 'TX'},
        'district': {'value': '08A'},
    }
    policy = get_effective_policy(district='08A', county='Andrews County')
    policy['complete'] = True
    policy['policy_id'] = 'tx.w3a'
    out = plan_from_facts(facts, policy)
    # We expect at least a surface_s casing shoe plug step to inherit tagging requirement due to overrides.tag
    shoe = next((s for s in out['steps'] if s.get('type') == 'surface_casing_shoe_plug'), None)
    assert shoe is not None
    assert shoe.get('tag_required') is True

    # Confirm district_overrides carry WBL/protect data for consumption (wired later in kernel)
    eff = policy.get('effective') or {}
    d_ovr = eff.get('district_overrides') or {}
    assert 'wbl' in d_ovr or 'protect_intervals' in d_ovr


def test_08a_ector_shoe_tagging_from_protect_or_er():
    facts = {
        'api14': {'value': '42000000000000'},
        'state': {'value': 'TX'},
        'district': {'value': '08A'},
    }
    policy = get_effective_policy(district='08A', county='Ector County')
    policy['complete'] = True
    policy['policy_id'] = 'tx.w3a'
    out = plan_from_facts(facts, policy)
    shoe = next((s for s in out['steps'] if s.get('type') == 'surface_casing_shoe_plug'), None)
    assert shoe is not None
    # With protect_intervals or enhanced_recovery overrides, shoe should be tagged
    assert shoe.get('tag_required') is True
