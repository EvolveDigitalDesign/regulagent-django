from apps.kernel.services.policy_kernel import plan_from_facts


def test_w3a_golden_with_materials():
    facts = {
        'api14': {'value': '42000000000000'},
        'state': {'value': 'TX'},
        'use_cibp': {'value': True},
        'has_uqw': {'value': True},
    }
    policy = {
        'policy_id': 'tx.w3a',
        'policy_version': '2025.10.0',
        'jurisdiction': 'TX',
        'form': 'W-3A',
        'base': {},
        'effective': {
            'requirements': {
                'surface_casing_shoe_plug_min_ft': {'value': 100, 'citation_keys': ['tx.tac.16.3.14(e)(2)']},
                'cement_above_cibp_min_ft': {'value': 20, 'citation_keys': ['tx.tac.16.3.14(g)(3)']},
                'uqw_isolation_min_len_ft': {'value': 100, 'citation_keys': ['tx.tac.16.3.14(g)(1)']},
                'uqw_below_base_ft': {'value': 50, 'citation_keys': ['tx.tac.16.3.14(g)(1)']},
                'uqw_above_base_ft': {'value': 50, 'citation_keys': ['tx.tac.16.3.14(g)(1)']},
            }
        },
        'preferences': {
            'geometry_defaults': {
                'cibp_cap': {'casing_id_in': 6.094, 'stinger_od_in': 2.875, 'annular_excess': 0.5},
                'surface_casing_shoe_plug': {'casing_id_in': 6.094, 'stinger_od_in': 2.875, 'annular_excess': 0.4, 'cap_length_ft': 100},
                'uqw_isolation_plug': {'casing_id_in': 6.094, 'stinger_od_in': 2.875, 'annular_excess': 0.5},
            },
            'default_recipe': {
                'id': 'class_h_neat', 'class': 'H', 'density_ppg': 15.8, 'yield_ft3_per_sk': 1.18, 'water_gal_per_sk': 5.2, 'additives': []
            }
        },
        'district': None,
        'complete': True,
        'incomplete_reasons': [],
    }
    out = plan_from_facts(facts, policy)
    # Assert materials are present with sacks computed
    for s in out['steps']:
        assert 'materials' in s and 'slurry' in s['materials']
        # For cap length 20 ft, expect small but non-zero sacks
    cap = next(s for s in out['steps'] if s['type'] == 'cibp_cap')
    assert cap['materials']['slurry']['sacks'] >= 1


