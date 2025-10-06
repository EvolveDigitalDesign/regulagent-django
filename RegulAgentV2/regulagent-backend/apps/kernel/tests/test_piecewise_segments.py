from apps.kernel.services.policy_kernel import _compute_materials_for_steps


def approx(a: float, b: float, tol: float = 0.02) -> bool:
    return abs(a - b) <= tol


def test_piecewise_open_hole_segments_sum():
    steps = [
        {
            "type": "cement_plug",
            "geometry_context": "open_hole",
            "top_ft": 0.0,
            "bottom_ft": 140.0,
            "stinger_od_in": 2.875,
            "annular_excess": 0.60,
            "recipe": {
                "id": "class_h_neat_15_8",
                "class": "H",
                "density_ppg": 15.8,
                "yield_ft3_per_sk": 1.18,
                "water_gal_per_sk": 5.2,
                "additives": [],
                "rounding": "nearest",
            },
            "segments": [
                {"top_ft": 0.0, "bottom_ft": 40.0, "hole_d_in": 8.5, "stinger_od_in": 2.875, "annular_excess": 0.60},
                {"top_ft": 40.0, "bottom_ft": 140.0, "hole_d_in": 10.0, "stinger_od_in": 2.875, "annular_excess": 0.60},
            ],
        }
    ]
    out = _compute_materials_for_steps(steps)
    m = (out[0].get("materials") or {}).get("slurry") or {}
    total_bbl = float(m.get("total_bbl") or 0.0)
    ft3 = float(m.get("ft3") or 0.0)
    sacks = int(m.get("sacks") or 0)
    assert approx(total_bbl, 18.22810, 0.05)
    assert approx(ft3, 102.3435, 0.5)
    assert sacks == 87
