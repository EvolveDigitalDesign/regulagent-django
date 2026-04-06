from apps.policy.services.loader import get_effective_policy


def test_08a_enhanced_recovery_overrides_present():
    policy = get_effective_policy(district='08A', county='Gaines County')
    eff = policy.get('effective') or {}
    d_ovr = eff.get('district_overrides') or {}
    # district_overrides for 08A Gaines County has shape:
    #   {'fields': {'Adair': {...}, 'Amrow': {...}, ...}}
    # tag/enhanced_recovery_zone/protect_intervals are nested inside per-field entries,
    # not at the top level of d_ovr.
    # Verify the fields structure is present and at least one field carries
    # tag or enhanced_recovery_zone data (kernel wiring will consume field-level data later).
    fields = d_ovr.get('fields', {})
    assert len(fields) > 0, "Expected field entries under district_overrides['fields']"
    found = any(
        isinstance(data, dict) and (
            'tag' in data
            or 'enhanced_recovery_zone' in data
            or 'protect_intervals' in data
        )
        for data in fields.values()
    )
    assert found, (
        "Expected at least one field entry under district_overrides['fields'] to contain "
        "tag, enhanced_recovery_zone, or protect_intervals data"
    )
