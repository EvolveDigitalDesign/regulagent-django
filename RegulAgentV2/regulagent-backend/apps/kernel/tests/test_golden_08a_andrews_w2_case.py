from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts


def test_golden_08a_andrews_minimal():
    facts = {
        'api14':        { 'value': '4200346118' },
        'state':        { 'value': 'TX' },
        'district':     { 'value': '08A' },
        'county':       { 'value': 'Andrews County' },
        'use_cibp':     { 'value': True },
        'has_uqw':      { 'value': True },
    }

    policy = get_effective_policy(district='08A', county='Andrews County')
    policy['policy_id'] = 'tx.w3a'
    policy['complete'] = True
    policy['preferences'] = {
        'default_recipe': {
            'id': 'class_h_neat_15_8',
            'class': 'H',
            'density_ppg': 15.8,
            'yield_ft3_per_sk': 1.18,
            'water_gal_per_sk': 5.2,
            'additives': [],
        },
        'geometry_defaults': {
            'cibp_cap': {
                'casing_id_in': 4.778,   # assumed from 5-1/2" production, typical ID
                'stinger_od_in': 2.875,  # 2-7/8" tubing OD
                'annular_excess': 0.50,
            },
            'squeeze': {
                'casing_id_in': 4.778,
                'stinger_od_in': 2.875,
                'squeeze_factor': 1.5,
                'annular_excess': 0.40,
            },
        }
    }
    # Inject step overrides to match W-2 derived intervals
    policy.setdefault('effective', {}).setdefault('steps_overrides', {})['cibp_cap'] = {
        'cap_length_ft': 100,
        'citations': ['TX SWR-14', 'District 08 overlay: min cap 100 ft above CIBP']
    }
    policy['effective']['steps_overrides']['squeeze_via_perf'] = {
        'interval_ft': [8110, 8194],
        'citations': ['District overlay: cap-above-perf']
    }

    out = plan_from_facts(facts, policy)
    # Basic shape
    assert out['policy_complete'] is True
    steps = out['steps']
    types = [s['type'] for s in steps]
    assert 'surface_casing_shoe_plug' in types
    assert 'cibp_cap' in types
    assert 'uqw_isolation_plug' in types

    # CIBP cap sacks computed (allow small tolerance range)
    cap = next(s for s in steps if s['type'] == 'cibp_cap')
    sacks = (cap.get('materials') or {}).get('slurry', {}).get('sacks')
    assert isinstance(sacks, int)
    assert 9 <= sacks <= 13

    # Squeeze step added via override; sacks computed
    sqz = next(s for s in steps if s['type'] == 'squeeze')
    sqz_sacks = (sqz.get('materials') or {}).get('slurry', {}).get('sacks')
    assert isinstance(sqz_sacks, int)

    # Andrews County should require shoe tagging via explicit override
    shoe = next(s for s in steps if s['type'] == 'surface_casing_shoe_plug')
    assert shoe.get('tag_required') is True

