import math
import pytest

from apps.materials.services.material_engine import (
    cylinder_capacity_bbl_per_ft,
    annulus_capacity_bbl_per_ft,
    balanced_plug_bbl,
    bridge_plug_cap_bbl,
    squeeze_bbl,
    SlurryRecipe,
    compute_sacks,
    spacer_bbl_for_interval,
    balanced_displacement_bbl,
    integrate_annulus_over_segments,
)


CLASS_H_NEAT = SlurryRecipe(
    recipe_id="class_h_neat",
    cement_class="H",
    density_ppg=15.8,
    yield_ft3_per_sk=1.18,
    water_gal_per_sk=5.2,
    additives=[{"name": "defoamer", "unit": "gal/sk", "rate": 0.05}],
)

CLASS_H_EXT = SlurryRecipe(
    recipe_id="class_h_ext",
    cement_class="H",
    density_ppg=15.0,
    yield_ft3_per_sk=1.36,
    water_gal_per_sk=6.5,
    additives=[],
)


def approx(a, b, tol=0.02):
    return abs(a - b) <= tol


def test_open_hole_spot_plug():
    oh_d = 8.5
    L = 100.0
    ann_excess = 0.5
    cap = cylinder_capacity_bbl_per_ft(oh_d)
    annular_bbl = L * cap * (1 + ann_excess)
    vb = compute_sacks(annular_bbl, CLASS_H_NEAT)
    assert approx(annular_bbl, 10.5232)
    assert vb.sacks == 51
    assert approx(vb.water_bbl, 6.31, tol=0.05)


def test_bridge_plug_cap_7in():
    cap_len = 100.0
    casing_id = 6.094
    stinger_od = 2.875
    ann_excess = 0.5
    res = bridge_plug_cap_bbl(cap_len, casing_id, stinger_od, ann_excess)
    assert approx(res["total_bbl"], 4.2051, tol=0.02)
    vb = compute_sacks(res["total_bbl"], CLASS_H_NEAT)
    assert vb.sacks == 21
    assert approx(vb.water_bbl, 2.60, tol=0.05)


def test_balanced_plug_in_7in_casing():
    L = 100.0
    casing_id = 6.094
    stinger_od = 2.875
    stinger_id = 2.441
    ann_excess = 0.3
    ann_cap = annulus_capacity_bbl_per_ft(casing_id, stinger_od)
    id_cap = cylinder_capacity_bbl_per_ft(stinger_id)
    res = balanced_plug_bbl(L, ann_cap, id_cap, ann_excess)
    assert approx(res["annular_bbl"], 3.6444, tol=0.02)
    assert approx(res["inside_bbl"], 0.5786, tol=0.02)
    assert approx(res["total_bbl"], 4.2230, tol=0.02)
    vb = compute_sacks(res["total_bbl"], CLASS_H_NEAT)
    assert vb.sacks == 21


def test_squeeze_via_perf():
    interval = 50.0
    casing_id = 6.094
    stinger_od = 2.875
    sq = squeeze_bbl(interval, casing_id, stinger_od, 1.5)
    assert approx(sq["base_bbl"], 1.4017, tol=0.02)
    assert approx(sq["total_bbl"], 2.1025, tol=0.02)
    vb = compute_sacks(sq["total_bbl"], CLASS_H_NEAT)
    assert vb.sacks == 11


def test_balanced_open_hole_through_pipe_extended():
    oh_d = 8.5
    stinger_od = 2.875
    stinger_id = 2.441
    L = 60.0
    ann_excess = 1.0
    ann_cap = annulus_capacity_bbl_per_ft(oh_d, stinger_od)
    id_cap = cylinder_capacity_bbl_per_ft(stinger_id)
    res = balanced_plug_bbl(L, ann_cap, id_cap, ann_excess)
    assert approx(res["total_bbl"], 7.8026, tol=0.03)
    vb = compute_sacks(res["total_bbl"], CLASS_H_EXT)
    assert vb.sacks == 33


def test_spacer_sizing():
    interval = 50.0
    ann_cap = 0.02803
    spacer = spacer_bbl_for_interval(interval, ann_cap, min_bbl=5.0, spacer_multiple=1.5, contact_minutes=10, pump_rate_bpm=3)
    assert approx(spacer, 30.0, tol=0.01)


def test_displacement_for_balanced():
    L = 100.0
    pipe_id_cap = 0.00579
    disp = balanced_displacement_bbl(L, pipe_id_cap, margin_bbl=0.25)
    assert approx(disp, 0.829, tol=0.02)


def test_annulus_guard_zero():
    cap = annulus_capacity_bbl_per_ft(2.875, 2.875)
    assert cap == 0.0


def test_invalid_interval_raises():
    with pytest.raises(ValueError):
        balanced_plug_bbl(0, 0.01, 0.01, 0.3)


def test_segmented_open_hole():
    segs = [
        (0.0, 40.0, 8.5, 0.0),
        (40.0, 100.0, 10.0, 0.0),
    ]
    total = integrate_annulus_over_segments(segs, annular_excess=0.6)
    expected = 40 * cylinder_capacity_bbl_per_ft(8.5) * 1.6 + 60 * cylinder_capacity_bbl_per_ft(10.0) * 1.6
    assert approx(total, expected, tol=0.02)
