from apps.materials.services.material_engine import (
    annulus_capacity_bbl_per_ft,
    cylinder_capacity_bbl_per_ft,
    bridge_plug_cap_bbl,
    squeeze_bbl,
    SlurryRecipe,
    compute_sacks,
)


def approx(a: float, b: float, tol: float = 0.02) -> bool:
    return abs(a - b) <= tol


RECIPE = SlurryRecipe(
    recipe_id="class_h_neat_15_8",
    cement_class="H",
    density_ppg=15.8,
    yield_ft3_per_sk=1.18,
    water_gal_per_sk=5.2,
    additives=[],
)


def test_1_open_hole_unbalanced():
    L = 120.0
    hole = 8.5
    stinger_od = 2.875
    excess = 0.30
    cap = annulus_capacity_bbl_per_ft(hole, stinger_od)
    total_bbl = L * cap * (1.0 + excess)
    vb = compute_sacks(total_bbl, RECIPE, rounding="nearest")
    assert approx(cap, 0.0621288281, 1e-9)
    assert approx(vb.total_bbl, 9.6921, 0.01)
    assert approx(vb.ft3, 54.4173, 0.1)
    assert vb.sacks == 46
    assert approx(vb.water_bbl, 5.6952, 0.02)


def test_2_open_hole_balanced():
    L = 80.0
    hole = 9.875
    stinger_od = 2.875
    stinger_id = 2.441
    excess = 0.50
    ann_cap = annulus_capacity_bbl_per_ft(hole, stinger_od)
    inside_cap = cylinder_capacity_bbl_per_ft(stinger_id)
    total_bbl = (L * ann_cap * (1.0 + excess)) + (L * inside_cap)
    vb = compute_sacks(total_bbl, RECIPE, rounding="nearest")
    assert approx(ann_cap, 0.08666175, 1e-8)
    assert approx(vb.total_bbl, 10.8623, 0.02)
    assert approx(vb.ft3, 60.9873, 0.2)
    assert vb.sacks == 52
    assert approx(vb.water_bbl, 6.4381, 0.03)


def test_3_cased_7in_production():
    L = 150.0
    casing_id = 6.094
    stinger_od = 2.875
    excess = 0.50
    cap = annulus_capacity_bbl_per_ft(casing_id, stinger_od)
    total_bbl = L * cap * (1.0 + excess)
    vb = compute_sacks(total_bbl, RECIPE, rounding="nearest")
    assert approx(cap, 0.0280339459, 1e-9)
    assert approx(vb.total_bbl, 6.3076, 0.02)
    assert approx(vb.ft3, 35.4149, 0.2)
    assert vb.sacks == 30
    assert approx(vb.water_bbl, 3.7143, 0.02)


def test_4_cased_5p5_long_interval():
    L = 600.0
    casing_id = 4.778
    stinger_od = 2.875
    excess = 1.20
    cap = annulus_capacity_bbl_per_ft(casing_id, stinger_od)
    total_bbl = L * cap * (1.0 + excess)
    vb = compute_sacks(total_bbl, RECIPE, rounding="nearest")
    assert approx(cap, 0.0141413129, 1e-9)
    assert approx(vb.total_bbl, 18.6665, 0.03)
    assert approx(vb.ft3, 104.8051, 0.3)
    assert vb.sacks == 89
    assert approx(vb.water_bbl, 11.0190, 0.05)


def test_5_squeeze_perf_retainer():
    L = 60.0
    casing_id = 4.778
    stinger_od = 2.875
    factor = 1.6
    vols = squeeze_bbl(L, casing_id, stinger_od, factor)
    vb = compute_sacks(vols["total_bbl"], RECIPE, rounding="nearest")
    base_cap = annulus_capacity_bbl_per_ft(casing_id, stinger_od)
    assert approx(base_cap, 0.0141413129, 1e-9)
    assert approx(vols["base_bbl"], 0.84848, 0.01)
    assert approx(vb.total_bbl, 1.35757, 0.02)
    assert approx(vb.ft3, 7.6222, 0.1)
    assert vb.sacks == 6
    assert approx(vb.water_bbl, 0.7429, 0.02)


def test_6_cibp_cap():
    cap_len = 50.0
    casing_id = 4.778
    stinger_od = 2.875
    excess = 0.40
    vols = bridge_plug_cap_bbl(cap_len, casing_id, stinger_od, excess)
    vb = compute_sacks(vols["total_bbl"], RECIPE, rounding="nearest")
    ann_cap = annulus_capacity_bbl_per_ft(casing_id, stinger_od)
    assert approx(ann_cap, 0.0141413129, 1e-9)
    assert approx(vb.total_bbl, 0.98989, 0.01)
    assert approx(vb.ft3, 5.5578, 0.05)
    assert vb.sacks == 5
    assert approx(vb.water_bbl, 0.6190, 0.02)


def test_7_open_hole_piecewise():
    # segments: (top, bottom, hole_d, stinger_od)
    segs = [
        (0.0, 40.0, 8.5, 2.875),
        (40.0, 140.0, 10.0, 2.875),
    ]
    excess = 0.60
    base_bbl = 0.0
    for top, bot, hole, od in segs:
        L = bot - top
        cap = annulus_capacity_bbl_per_ft(hole, od)
        base_bbl += L * cap
    total_bbl = base_bbl * (1.0 + excess)
    vb = compute_sacks(total_bbl, RECIPE, rounding="nearest")
    cap1 = annulus_capacity_bbl_per_ft(8.5, 2.875)
    cap2 = annulus_capacity_bbl_per_ft(10.0, 2.875)
    assert approx(cap1, 0.0621288281, 1e-9)
    assert approx(cap2, 0.0890740781, 1e-9)
    # segment bbls
    assert approx(40.0 * cap1, 2.48515, 0.02)
    assert approx(100.0 * cap2, 8.90741, 0.02)
    # totals
    assert approx(vb.total_bbl, 18.22810, 0.05)
    assert approx(vb.ft3, 102.3435, 0.5)
    assert vb.sacks == 87
    assert approx(vb.water_bbl, 10.7714, 0.06)


def test_8_open_hole_tight_annulus():
    L = 100.0
    hole = 3.0
    stinger_od = 2.875
    excess = 0.30
    cap = annulus_capacity_bbl_per_ft(hole, stinger_od)
    total_bbl = L * cap * (1.0 + excess)
    vb = compute_sacks(total_bbl, RECIPE, rounding="nearest")
    assert approx(cap, 0.0007130781, 1e-9)
    assert approx(vb.total_bbl, 0.09270, 0.002)
    assert approx(vb.ft3, 0.5205, 0.01)
    assert vb.sacks == 0
    assert approx(vb.water_bbl, 0.0, 0.001)


def test_9_cased_9_5_8():
    L = 100.0
    casing_id = 8.535
    stinger_od = 2.875
    excess = 0.30
    cap = annulus_capacity_bbl_per_ft(casing_id, stinger_od)
    total_bbl = L * cap * (1.0 + excess)
    vb = compute_sacks(total_bbl, RECIPE, rounding="nearest")
    assert approx(cap, 0.0627077626, 1e-9)
    assert approx(vb.total_bbl, 8.15201, 0.02)
    assert approx(vb.ft3, 45.7703, 0.2)
    assert vb.sacks == 39
    assert approx(vb.water_bbl, 4.8286, 0.03)


