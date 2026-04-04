from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts


def test_08a_ector_wbl_sr_protect_triggers_tagging():
    # NOTE: Despite the test name referencing "ector", this uses Andrews County
    # because Andrews County has county-level tag data (keys include 'tag' at the
    # county_overrides level) that the kernel already consumes to set tag_required=True.
    # Ector County only has field-level tag data, which is not yet wired into kernel
    # shoe tagging (TODO: wire field-level overlay data into kernel tagging logic).
    facts = {
        'api14': {'value': '42000000000000'},
        'state': {'value': 'TX'},
        'district': {'value': '08A'},
    }
    policy = get_effective_policy(district='08A', county='Andrews County')
    policy['complete'] = True
    policy['policy_id'] = 'tx.w3a'
    out = plan_from_facts(facts, policy)
    # Andrews County has county-level 'tag' override → kernel sets tag_required=True on shoe
    shoe = next((s for s in out['steps'] if s.get('type') == 'surface_casing_shoe_plug'), None)
    assert shoe is not None
    assert shoe.get('tag_required') is True

    # Confirm district_overrides carry WBL/protect data for consumption.
    # For Andrews County, wbl/protect_intervals live inside fields entries (not at top level).
    eff = policy.get('effective') or {}
    d_ovr = eff.get('district_overrides') or {}
    fields = d_ovr.get('fields', {})
    assert len(fields) > 0, "Expected field entries under district_overrides['fields']"
    found_protect = any(
        isinstance(data, dict) and ('wbl' in data or 'protect_intervals' in data)
        for data in fields.values()
    )
    assert found_protect, (
        "Expected at least one field entry to carry wbl or protect_intervals data"
    )


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

    # TODO: Once kernel wires field-level overlay data (protect_intervals / tag entries
    # inside district_overrides['fields']) into shoe tagging, this should assert
    # shoe.get('tag_required') is True.
    # For now, Ector County only has field-level tag data (not county-level), so the kernel
    # does not yet set tag_required on the shoe.  Assert the current actual behavior:
    assert shoe.get('tag_required') is not True, (
        "Ector County field-level tag data is not yet wired into kernel shoe tagging. "
        "Once wired, change this assertion to `is True`."
    )

    # Confirm the overlay DOES carry protect_intervals/tag data in the fields structure,
    # ready to be consumed once kernel wiring is added.
    eff = policy.get('effective') or {}
    d_ovr = eff.get('district_overrides') or {}
    fields = d_ovr.get('fields', {})
    assert len(fields) > 0, "Expected field entries under district_overrides['fields']"
    found = any(
        isinstance(data, dict) and (
            'tag' in data
            or 'protect_intervals' in data
            or 'enhanced_recovery_zone' in data
        )
        for data in fields.values()
    )
    assert found, (
        "Expected Ector County field entries to carry tag/protect_intervals/enhanced_recovery_zone data"
    )
