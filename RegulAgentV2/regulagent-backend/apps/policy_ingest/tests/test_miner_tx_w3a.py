import json
from io import StringIO

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def _ensure_policy_data(version_tag: str) -> None:
    from apps.policy_ingest.models import PolicyRule, PolicySection
    rule, _ = PolicyRule.objects.get_or_create(
        rule_id='tx.tac.16.3.14',
        version_tag=version_tag,
        defaults={
            'citation': '16 3 14',
            'title': '',
            'source_urls': ['test://local'],
            'jurisdiction': 'TX',
            'doc_type': 'policy',
            'topic': 'plugging',
            'html_sha256': 'deadbeef',
        }
    )
    # Minimal sections with required text fragments
    PolicySection.objects.get_or_create(
        rule=rule, version_tag=version_tag, path='e(2)', order_idx=200,
        defaults={'heading': '', 'text': '... shoe of the surface casing. This plug shall be a minimum of 100 feet ...', 'anchor': ''}
    )
    PolicySection.objects.get_or_create(
        rule=rule, version_tag=version_tag, path='g(1)', order_idx=300,
        defaults={'heading': '', 'text': '... usable quality water ... minimum of 100 feet ... 50 feet below ... 50 feet above ...', 'anchor': ''}
    )
    PolicySection.objects.get_or_create(
        rule=rule, version_tag=version_tag, path='g(3)', order_idx=320,
        defaults={'heading': '', 'text': '... bridge plug ... at least 20 feet of cement placed on top ...', 'anchor': ''}
    )


def _run_miner(version_tag: str = "2025-Q4") -> dict:
    _ensure_policy_data(version_tag)
    out = StringIO()
    call_command("mine_tx_w3a_knobs", version_tag=version_tag, stdout=out)
    return json.loads(out.getvalue())


def test_surface_shoe_plug_from_e2():
    data = _run_miner()
    shoe = data["surface_casing_shoe_plug_min_ft"]
    assert shoe["proposed_value"] == 100
    assert any(hit["path"] == "e(2)" for hit in shoe["hits"])


def test_uqw_isolation_from_g1():
    data = _run_miner()
    uqw = data["uqw_isolation_plug"]
    assert uqw["proposed_value"] == {"min_len_ft": 100, "below_ft": 50, "above_ft": 50}
    assert any(hit["path"] == "g(1)" for hit in uqw["hits"])


def test_cibp_cap_from_g3():
    data = _run_miner()
    cibp = data["cement_above_cibp_min_ft"]
    assert cibp["proposed_value"] == 20
    assert any(hit["path"] == "g(3)" for hit in cibp["hits"])


def test_cap_above_perf_null_in_base():
    data = _run_miner()
    cap = data["cap_above_highest_perf_ft"]
    assert cap["proposed_value"] is None
    assert cap["reason"] == "no_base_rule"
    assert cap["hits"] == []


