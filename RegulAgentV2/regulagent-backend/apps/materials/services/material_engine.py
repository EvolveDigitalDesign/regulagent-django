from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor
from typing import Dict, List, Optional, Sequence, Tuple


BBL_TO_FT3 = 5.6146


def cylinder_capacity_bbl_per_ft(diameter_in: float) -> float:
    """Capacity for a cylinder of diameter d (inches) in bbl/ft: 0.000971 * d^2."""
    if diameter_in <= 0:
        raise ValueError("diameter_in must be > 0")
    return 0.000971 * (diameter_in ** 2)


def annulus_capacity_bbl_per_ft(hole_diameter_in: float, pipe_od_in: float) -> float:
    """Annular capacity in bbl/ft: 0.000971 * (hole^2 - pipe^2). Clamped to ≥ 0."""
    if hole_diameter_in <= 0 or pipe_od_in < 0:
        raise ValueError("invalid diameters")
    delta = (hole_diameter_in ** 2) - (pipe_od_in ** 2)
    if delta <= 0:
        return 0.0
    return 0.000971 * delta


@dataclass
class SlurryRecipe:
    recipe_id: str
    cement_class: str
    density_ppg: float
    yield_ft3_per_sk: float
    water_gal_per_sk: float
    additives: List[Dict]


@dataclass
class VolumeBreakdown:
    total_bbl: float
    sacks: int
    ft3: float
    water_bbl: float
    additives: Dict[str, float]
    explain: Dict[str, float]


def sacks_from_bbl(total_bbl: float, yield_ft3_per_sk: float, rounding: str = "nearest") -> int:
    if total_bbl < 0 or yield_ft3_per_sk <= 0:
        raise ValueError("invalid total_bbl or yield")
    raw = (total_bbl * BBL_TO_FT3) / yield_ft3_per_sk
    mode = (rounding or "nearest").lower()
    if mode == "ceil":
        return int(ceil(raw))
    if mode == "floor":
        return int(floor(raw))
    # default: nearest, 0.5 up
    return int(floor(raw + 0.5))


def water_bbl_from_sacks(sacks: int, water_gal_per_sk: float) -> float:
    if sacks < 0 or water_gal_per_sk < 0:
        raise ValueError("invalid sacks or water per sk")
    return (sacks * water_gal_per_sk) / 42.0


def additives_totals(sacks: int, additives: List[Dict]) -> Dict[str, float]:
    totals: Dict[str, float] = {}
    for a in additives or []:
        name = a.get("name")
        rate = float(a.get("rate", 0.0))
        if not name:
            continue
        totals[name] = totals.get(name, 0.0) + sacks * rate
    return totals


def balanced_plug_bbl(
    interval_ft: float,
    annulus_cap_bbl_per_ft: float,
    pipe_id_cap_bbl_per_ft: float,
    annular_excess: float,
) -> Dict[str, float]:
    if interval_ft <= 0 or annulus_cap_bbl_per_ft < 0 or pipe_id_cap_bbl_per_ft < 0 or annular_excess < 0:
        raise ValueError("invalid inputs")
    annular_bbl = interval_ft * annulus_cap_bbl_per_ft * (1.0 + annular_excess)
    inside_bbl = interval_ft * pipe_id_cap_bbl_per_ft
    total_bbl = annular_bbl + inside_bbl
    return {
        'annular_bbl': annular_bbl,
        'inside_bbl': inside_bbl,
        'total_bbl': total_bbl,
    }


def bridge_plug_cap_bbl(
    cap_length_ft: float,
    casing_id_in: float,
    stinger_od_in: float,
    annular_excess: float,
) -> Dict[str, float]:
    if cap_length_ft <= 0 or annular_excess < 0:
        raise ValueError("invalid inputs")
    ann_cap = annulus_capacity_bbl_per_ft(casing_id_in, stinger_od_in)
    annular_bbl = cap_length_ft * ann_cap * (1.0 + annular_excess)
    return {
        'annular_bbl': annular_bbl,
        'total_bbl': annular_bbl,
    }


def squeeze_bbl(
    interval_ft: float,
    casing_id_in: float,
    stinger_od_in: float,
    squeeze_factor: float,
) -> Dict[str, float]:
    if interval_ft <= 0 or squeeze_factor < 0:
        raise ValueError("invalid inputs")
    base = interval_ft * annulus_capacity_bbl_per_ft(casing_id_in, stinger_od_in)
    total_bbl = base * squeeze_factor
    return {'base_bbl': base, 'total_bbl': total_bbl}


def compute_sacks(total_bbl: float, recipe: SlurryRecipe, rounding: str = "nearest") -> VolumeBreakdown:
    sk = sacks_from_bbl(total_bbl, recipe.yield_ft3_per_sk, rounding=rounding)
    ft3 = total_bbl * BBL_TO_FT3
    water_bbl = water_bbl_from_sacks(sk, recipe.water_gal_per_sk)
    adds = additives_totals(sk, recipe.additives)
    explain = {
        'yield_ft3_per_sk': recipe.yield_ft3_per_sk,
        'water_gal_per_sk': recipe.water_gal_per_sk,
        'rounding_mode': rounding,
    }
    return VolumeBreakdown(total_bbl=total_bbl, sacks=sk, ft3=ft3, water_bbl=water_bbl, additives=adds, explain=explain)


def spacer_bbl_for_interval(
    interval_ft: float,
    annulus_cap_bbl_per_ft: float,
    min_bbl: float = 5.0,
    spacer_multiple: float = 1.5,
    contact_minutes: Optional[float] = None,
    pump_rate_bpm: Optional[float] = None,
) -> float:
    if interval_ft <= 0 or annulus_cap_bbl_per_ft < 0 or min_bbl < 0 or spacer_multiple < 0:
        raise ValueError("invalid inputs")
    candidates = [min_bbl, spacer_multiple * interval_ft * annulus_cap_bbl_per_ft]
    if contact_minutes is not None and pump_rate_bpm is not None:
        if contact_minutes < 0 or pump_rate_bpm < 0:
            raise ValueError("invalid contact or pump rate")
        candidates.append(contact_minutes * pump_rate_bpm)
    return max(candidates)


def balanced_displacement_bbl(
    interval_ft: float,
    pipe_id_cap_bbl_per_ft: float,
    margin_bbl: float = 0.0,
) -> float:
    if interval_ft <= 0 or pipe_id_cap_bbl_per_ft < 0 or margin_bbl < 0:
        raise ValueError("invalid inputs")
    return interval_ft * pipe_id_cap_bbl_per_ft + margin_bbl


def integrate_annulus_over_segments(
    segments: Sequence[Tuple[float, float, float, float]],
    annular_excess: float,
) -> float:
    """
    Sum annular volumes over piecewise geometry segments.
    segments: [(top_ft, bottom_ft, hole_d_in, pipe_od_in)]
    Returns total_bbl including annular_excess.
    """
    if annular_excess < 0:
        raise ValueError("invalid excess")
    total = 0.0
    for top, bot, hole_d, pipe_od in segments:
        if bot <= top:
            raise ValueError("invalid segment bounds")
        length = bot - top
        cap = annulus_capacity_bbl_per_ft(hole_d, pipe_od)
        total += length * cap * (1.0 + annular_excess)
    return total


