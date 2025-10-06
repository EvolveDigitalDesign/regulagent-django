from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts


def test_golden_approved_w3a_08a_andrews():
    # Header/context from approved W-3A
    facts = {
        'api14':   { 'value': '4200346118' },
        'state':   { 'value': 'TX' },
        'district':{ 'value': '08A' },
        'county':  { 'value': 'Andrews County' },
        'use_cibp':{ 'value': True },
        'has_uqw': { 'value': True },
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
            # Production string geometry assumptions for materials
            'cement_plug': {
                'casing_id_in': 4.778,
                'stinger_od_in': 2.875,
                'annular_excess': 0.4,
            },
            'squeeze': {
                'casing_id_in': 4.778,
                'stinger_od_in': 2.875,
                'squeeze_factor': 1.5,
                'annular_excess': 0.4,
            },
        }
    }

    # Build the approved sequence as overrides
    policy.setdefault('effective', {}).setdefault('steps_overrides', {})
    eff = policy['effective']['steps_overrides']
    eff['perf_circulate'] = [
        { 'top_ft': 8110, 'bottom_ft': 10914, 'citations': ['SWR-14'] },
    ]
    eff['cement_plugs'] = [
        # 100 ft, target 40 sk: open hole with ~35% excess
        { 'top_ft': 7990, 'bottom_ft': 7890, 'geometry_context': 'open_hole', 'hole_d_in': 8.5, 'stinger_od_in': 2.875, 'annular_excess': 0.35, 'citations': ['SWR-14'] },
        # 100 ft, target 20 sk: cased production with ~197% excess
        { 'top_ft': 7047, 'bottom_ft': 6947, 'geometry_context': 'cased_production', 'casing_id_in': 4.778, 'stinger_od_in': 2.875, 'annular_excess': 1.97, 'citations': ['SWR-14'] },
        # 612 ft, target 110 sk: cased production with ~167% excess
        { 'top_ft': 5582, 'bottom_ft': 4970, 'geometry_context': 'cased_production', 'casing_id_in': 4.778, 'stinger_od_in': 2.875, 'annular_excess': 1.67, 'citations': ['SWR-14'] },
        # 200 ft, target 63 sk: open hole with ~7% excess
        { 'top_ft': 4500, 'bottom_ft': 4300, 'geometry_context': 'open_hole', 'hole_d_in': 8.5, 'stinger_od_in': 2.875, 'annular_excess': 0.07, 'citations': ['SWR-14'] },
        # 100 ft, target 20 sk: cased production with ~197% excess
        { 'top_ft': 3638, 'bottom_ft': 3538, 'geometry_context': 'cased_production', 'casing_id_in': 4.778, 'stinger_od_in': 2.875, 'annular_excess': 1.97, 'citations': ['SWR-14'] },
        # 300 ft, target 90 sk: open hole with ~2% excess
        { 'top_ft': 1850, 'bottom_ft': 1550, 'geometry_context': 'open_hole', 'hole_d_in': 8.5, 'stinger_od_in': 2.875, 'annular_excess': 0.02, 'citations': ['SWR-14'] },
        # 300 ft, target 87 sk: cased production with ~331% excess
        { 'top_ft': 1250, 'bottom_ft': 950,  'geometry_context': 'cased_production', 'casing_id_in': 4.778, 'stinger_od_in': 2.875, 'annular_excess': 3.31, 'citations': ['SWR-14'] },
        # 347 ft, target 100 sk: cased production with ~328% excess (approx)
        { 'top_ft': 350,  'bottom_ft': 3,    'geometry_context': 'cased_production', 'casing_id_in': 4.778, 'stinger_od_in': 2.875, 'annular_excess': 3.28, 'citations': ['SWR-14'] },
    ]

    out = plan_from_facts(facts, policy)

    # Header parity
    assert out['inputs_summary']['api14'] == '4200346118'
    assert out['jurisdiction'] == 'TX'
    assert out['district'] == '08A'
    # Rounding policy honored
    assert (out.get('materials_policy') or {}).get('rounding') == 'nearest'

    # Steps mapping & order (9 steps)
    steps = [s for s in out['steps'] if s['type'] in ('perf_circulate','cement_plug')]
    assert len(steps) == 9

    expected = [
        ('perf_circulate', 8110, 10914, 0),
        ('cement_plug',    7990, 7890, 40),
        ('cement_plug',    7047, 6947, 20),
        ('cement_plug',    5582, 4970, 110),
        ('cement_plug',    4500, 4300, 63),
        ('cement_plug',    3638, 3538, 20),
        ('cement_plug',    1850, 1550, 90),
        ('cement_plug',    1250, 950,  87),
        ('cement_plug',    350,  3,    100),
    ]

    mismatches = []
    for i, (etype, top, bot, sacks_exp) in enumerate(expected):
        s = steps[i]
        if s['type'] != etype:
            mismatches.append(f"step {i+1} type {s['type']} != {etype}")
        if int(s.get('top_ft', top)) != top or int(s.get('bottom_ft', bot)) != bot:
            mismatches.append(f"step {i+1} interval {s.get('top_ft')}–{s.get('bottom_ft')} != {top}–{bot}")
        sacks = (s.get('materials') or {}).get('slurry', {}).get('sacks')
        sacks_val = int(sacks) if isinstance(sacks, int) else 0
        if etype == 'perf_circulate':
            if sacks_val != 0:
                mismatches.append(f"step {i+1} perf_circulate sacks {sacks_val} != 0")
        else:
            if sacks_val != sacks_exp:
                mismatches.append(f"step {i+1} sacks {sacks_val} != {sacks_exp}")

    assert not mismatches, f"Approved W-3A golden mismatches: {mismatches}"

    # Context branch invariants: open-hole steps should not carry casing_id_in
    # Steps 2, 5, 7 are open-hole in this golden
    assert 'casing_id_in' not in steps[1]
    assert 'casing_id_in' not in steps[4]
    assert 'casing_id_in' not in steps[6]

