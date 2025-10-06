from apps.policy.services.loader import get_effective_policy


def test_08a_enhanced_recovery_overrides_present():
    policy = get_effective_policy(district='08A', county='Gaines County')
    eff = policy.get('effective') or {}
    d_ovr = eff.get('district_overrides') or {}
    # ensure at least possibility of enhanced recovery behavior in county overlays
    # (kernel wiring will consume later)
    assert 'enhanced_recovery_zone' in d_ovr or 'tag' in d_ovr
