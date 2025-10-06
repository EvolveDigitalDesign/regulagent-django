from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts


def approx(a: float, b: float, tol: float = 0.02) -> bool:
    return abs(a - b) <= tol


def test_golden_w3a_7c_coke():
    facts = {
        'api14':   { 'value': '42000000000000' },
        'state':   { 'value': 'TX' },
        'district':{ 'value': '7C' },
        'county':  { 'value': 'Coke County' },
        'use_cibp': { 'value': True },
    }

    policy = get_effective_policy(district='7C', county='Coke County')
    policy['policy_id'] = 'tx.w3a'
    policy['complete'] = True
    # Lock rounding explicitly
    policy.setdefault('preferences', {})['rounding_policy'] = 'nearest'
    policy['preferences'].update({
        'default_recipe': {
            'id': 'class_h_neat_15_8',
            'class': 'H',
            'density_ppg': 15.8,
            'yield_ft3_per_sk': 1.18,
            'water_gal_per_sk': 5.2,
            'additives': [],
        },
        'geometry_defaults': {
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
            'cibp_cap': {
                'casing_id_in': 4.778,
                'stinger_od_in': 2.875,
                'annular_excess': 0.4,
            },
        }
    })

    # Build overrides: 1 perf, 1 open-hole plug, 1 cased plug
    policy.setdefault('effective', {}).setdefault('steps_overrides', {})
    eff = policy['effective']['steps_overrides']
    eff['perf_circulate'] = [
        { 'top_ft': 9000, 'bottom_ft': 9500, 'citations': ['SWR-14'] },
    ]
    eff['cement_plugs'] = [
        # Open hole 100 ft, 8.5" vs 2.875", +35% → ~40 sk
        { 'top_ft': 8100, 'bottom_ft': 8000, 'geometry_context': 'open_hole', 'hole_d_in': 8.5, 'stinger_od_in': 2.875, 'annular_excess': 0.35, 'citations': ['SWR-14'] },
        # Cased 100 ft, 4.778" vs 2.875", +197% → ~20 sk
        { 'top_ft': 7000, 'bottom_ft': 6900, 'geometry_context': 'cased_production', 'casing_id_in': 4.778, 'stinger_od_in': 2.875, 'annular_excess': 1.97, 'citations': ['SWR-14'] },
        # Open-hole piecewise 140 ft (0-40 @8.5", 40-140 @10.0"), +60% → ~87 sk
        { 'top_ft': 0, 'bottom_ft': 140, 'geometry_context': 'open_hole', 'stinger_od_in': 2.875, 'annular_excess': 0.60,
          'segments': [
            { 'top_ft': 0, 'bottom_ft': 40, 'hole_d_in': 8.5, 'stinger_od_in': 2.875, 'annular_excess': 0.60 },
            { 'top_ft': 40, 'bottom_ft': 140, 'hole_d_in': 10.0, 'stinger_od_in': 2.875, 'annular_excess': 0.60 },
          ],
          'citations': ['SWR-14']
        },
    ]
    # Add squeeze via perf override
    eff['squeeze_via_perf'] = { 'interval_ft': [5100, 5160], 'citations': ['SWR-14'] }
    # Override CIBP cap length to 50 ft
    eff['cibp_cap'] = { 'cap_length_ft': 50 }

    out = plan_from_facts(facts, policy)

    # Header
    assert out['jurisdiction'] == 'TX'
    assert out['district'] == '7C'
    assert (out.get('materials_policy') or {}).get('rounding') == 'nearest'

    # 7C operational instructions present on the shoe (from overlay)
    shoe = next((s for s in out['steps'] if s.get('type') == 'surface_casing_shoe_plug'), None)
    assert shoe is not None
    instr = shoe.get('special_instructions', '')
    assert ('Pump via tubing/drill pipe only' in instr) or ('Mud' in instr) or ('Funnel' in instr)

    # Steps (collect by type)
    steps = out['steps']
    plugs = [s for s in steps if s['type'] == 'cement_plug']
    perfs = [s for s in steps if s['type'] == 'perf_circulate']
    squeezes = [s for s in steps if s['type'] == 'squeeze']
    caps = [s for s in steps if s['type'] == 'cibp_cap']

    # perf
    assert any(int(s.get('top_ft')) == 9000 and int(s.get('bottom_ft')) == 9500 and ((s.get('materials') or {}).get('slurry', {}).get('sacks') or 0) == 0 for s in perfs)

    # cement_plug OH 100 ft → ~40 sk
    assert any(int(s.get('top_ft')) == 8100 and int(s.get('bottom_ft')) == 8000 and (s.get('geometry_context') == 'open_hole') and ((s.get('materials') or {}).get('slurry', {}).get('sacks') == 40) for s in plugs)
    # cement_plug cased 100 ft → ~20 sk
    assert any(int(s.get('top_ft')) == 7000 and int(s.get('bottom_ft')) == 6900 and (s.get('geometry_context') == 'cased_production') and ((s.get('materials') or {}).get('slurry', {}).get('sacks') == 20) for s in plugs)
    # cement_plug OH piecewise 140 ft → ~87 sk
    assert any(int(s.get('top_ft')) == 0 and int(s.get('bottom_ft')) == 140 and (s.get('geometry_context') == 'open_hole') and ((s.get('materials') or {}).get('slurry', {}).get('sacks') == 87) for s in plugs)

    # squeeze 60 ft @ factor 1.5 → ~6 sk
    assert any(abs(float(s.get('interval_ft')) - 60.0) < 1e-6 and ((s.get('materials') or {}).get('slurry', {}).get('sacks') == 6) for s in squeezes)

    # cibp_cap 50 ft @ 40% excess → ~5 sk
    assert any(abs(float(s.get('cap_length_ft')) - 50.0) < 1e-6 and ((s.get('materials') or {}).get('slurry', {}).get('sacks') == 5) for s in caps)

    # Context invariants
    # Open-hole plugs should not carry casing_id_in; cased plugs should not carry hole_d_in
    for s in plugs:
        ctx = s.get('geometry_context')
        if ctx == 'open_hole':
            assert 'casing_id_in' not in s
        if ctx and ctx.startswith('cased'):
            assert 'hole_d_in' not in s

    # Formation-top behavior: at least one formation_top_plug step exists
    ft_steps = [s for s in out['steps'] if s.get('type') == 'formation_top_plug']
    assert len(ft_steps) > 0


