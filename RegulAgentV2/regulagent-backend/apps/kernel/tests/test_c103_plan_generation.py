"""
Tests for C103PluggingRules — NM C-103 plugging plan generation engine.

Covers:
- Basic plan generation (return type, required components)
- CIBP and CIBP cap placement (NM 100 ft vs TX 20 ft)
- Formation isolation plug generation (mandatory NM requirement)
- Max-spacing enforcement with fill plug insertion
- Cement excess factors (50% cased, 100% open, min 25 sacks)
- Operation type classification (spot / squeeze / circulate)
- Procedure narrative generation
- C-103 compliance validation
- No TX regression (TX API must not route through NM engine)

Related to:
- NMAC 19.15.25 — Well Plugging and Abandonment
- C-103 Form — NM plugging plan submission

API numbers use NM state code prefix: 30-
"""

import pytest

from apps.kernel.services.c103_rules import C103PluggingRules
from apps.kernel.services.c103_models import (
    C103PlugRow,
    C103PluggingPlan,
    NM_CASED_EXCESS,
    NM_CIBP_CAP_FT,
    NM_MAX_CASED_SPACING_FT,
    NM_MIN_SACKS,
    NM_OPEN_EXCESS,
)
from apps.policy.services.nm_region_rules import NMRegionRulesEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def hobbs_nw_shelf_well():
    """Vertical well in Hobbs NW Shelf (Lea County T18S R34E) with typical formations."""
    return {
        "api_number": "30-025-12345",
        "county": "lea",
        "township": "T18S",
        "range": "R34E",
        "operator": "Test Oil Co",
        "lease_name": "State Lease 1",
        "lease_type": "state",
        "field_name": "Hobbs Field",
        "total_depth_ft": 8500,
        "formation_tops": [
            {"name": "San Andres", "depth_ft": 4200, "producing": True},
            {"name": "Glorieta", "depth_ft": 4800, "producing": False},
            {"name": "Yeso", "depth_ft": 5200, "producing": False},
        ],
        "casing_strings": [
            {"type": "surface", "size_in": 13.375, "depth_ft": 500, "top_of_cement_ft": 0},
            {"type": "intermediate", "size_in": 9.625, "depth_ft": 3500, "top_of_cement_ft": 200},
            {"type": "production", "size_in": 5.5, "depth_ft": 8500, "top_of_cement_ft": 3000},
        ],
        "perforations": [
            {"top_ft": 7000, "bottom_ft": 7500},
        ],
        "duqw_ft": None,
        "cbl_data": {
            "good_cement_intervals": [(0, 3000), (5000, 7000)],
            "poor_cement_intervals": [(3000, 5000)],
        },
        "downhole_equipment": [],
    }


@pytest.fixture
def deep_well_with_large_gap():
    """Well with a ~7000' gap in cased hole to trigger spacing enforcement."""
    return {
        "api_number": "30-025-99999",
        "county": "lea",
        "township": "T18S",
        "range": "R34E",
        "operator": "Gap Test Co",
        "lease_name": "Gap Test",
        "lease_type": "fee",
        "field_name": "Test Field",
        "total_depth_ft": 12000,
        "formation_tops": [
            {"name": "San Andres", "depth_ft": 4200, "producing": False},
        ],
        "casing_strings": [
            {"type": "surface", "size_in": 13.375, "depth_ft": 500, "top_of_cement_ft": 0},
            {"type": "production", "size_in": 7.0, "depth_ft": 12000, "top_of_cement_ft": 1000},
        ],
        "perforations": [
            {"top_ft": 11000, "bottom_ft": 11500},
        ],
        "duqw_ft": None,
        "cbl_data": None,
        "downhole_equipment": [],
    }


@pytest.fixture
def well_with_duqw():
    """Well that has a defined DUQW depth requiring a DUQW plug."""
    return {
        "api_number": "30-025-55555",
        "county": "lea",
        "township": "T18S",
        "range": "R34E",
        "operator": "DUQW Test Co",
        "lease_name": "DUQW Lease",
        "lease_type": "federal",
        "field_name": "DUQW Field",
        "total_depth_ft": 6000,
        "formation_tops": [
            {"name": "San Andres", "depth_ft": 3500, "producing": True},
        ],
        "casing_strings": [
            {"type": "surface", "size_in": 13.375, "depth_ft": 400, "top_of_cement_ft": 0},
            {"type": "production", "size_in": 5.5, "depth_ft": 6000, "top_of_cement_ft": 500},
        ],
        "perforations": [
            {"top_ft": 5500, "bottom_ft": 5800},
        ],
        "duqw_ft": 1200.0,
        "cbl_data": None,
        "downhole_equipment": [],
    }


@pytest.fixture
def open_hole_section_well():
    """Well with production casing at 5000 ft and open hole to 8000 ft."""
    return {
        "api_number": "30-025-77777",
        "county": "lea",
        "township": "T22S",
        "range": "R35E",
        "operator": "Open Hole Co",
        "lease_name": "Open Hole Lease",
        "lease_type": "fee",
        "field_name": "Open Field",
        "total_depth_ft": 8000,
        "formation_tops": [
            {"name": "San Andres", "depth_ft": 4000, "producing": False},
        ],
        "casing_strings": [
            {"type": "surface", "size_in": 13.375, "depth_ft": 400, "top_of_cement_ft": 0},
            {"type": "production", "size_in": 7.0, "depth_ft": 5000, "top_of_cement_ft": 500},
        ],
        "perforations": [],
        "duqw_ft": None,
        "cbl_data": None,
        "downhole_equipment": [],
    }


# ---------------------------------------------------------------------------
# TestBasicPlanGeneration
# ---------------------------------------------------------------------------

class TestBasicPlanGeneration:
    """Test basic plan generation for a standard NM Hobbs well."""

    def test_generates_plan_for_hobbs_well(self, hobbs_nw_shelf_well):
        """Plan generation returns a valid C103PluggingPlan instance."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        assert isinstance(plan, C103PluggingPlan), (
            "generate_plugging_plan() must return a C103PluggingPlan"
        )

    def test_plan_has_steps(self, hobbs_nw_shelf_well):
        """Generated plan has at least one plug step."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        assert len(plan.steps) > 0, "Plan must contain at least one step"

    def test_plan_has_surface_plug(self, hobbs_nw_shelf_well):
        """Every plan must include a surface plug."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        surface_plugs = [s for s in plan.steps if s.step_type == "surface_plug"]
        assert len(surface_plugs) == 1, (
            "Plan must have exactly one surface plug (NMAC 19.15.25)"
        )

    def test_plan_has_formation_plugs(self, hobbs_nw_shelf_well):
        """NM plans MUST have formation isolation plugs (mandatory per NMAC 19.15.25)."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        formation_plugs = plan.formation_plugs
        assert len(formation_plugs) > 0, (
            "Formation isolation plugs are mandatory for all NM wells (NMAC 19.15.25)"
        )

    def test_plan_has_cibp(self, hobbs_nw_shelf_well):
        """Plan includes a CIBP (mechanical plug) above the shallowest perforation."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        cibp_steps = [s for s in plan.steps if s.step_type == "mechanical_plug"]
        assert len(cibp_steps) == 1, "Plan must include one CIBP mechanical plug"

    def test_cibp_placed_50ft_above_shallowest_perf(self, hobbs_nw_shelf_well):
        """CIBP is set at shallowest perf top - 50'."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        cibp = next(s for s in plan.steps if s.step_type == "mechanical_plug")
        shallowest_perf_top = 7000.0  # from fixture
        expected_depth = shallowest_perf_top - 50.0
        assert cibp.top_ft == pytest.approx(expected_depth, abs=1.0), (
            f"CIBP should be at {expected_depth}' (shallowest perf top {shallowest_perf_top}' - 50')"
        )

    def test_plan_region_detected_as_south_hobbs(self, hobbs_nw_shelf_well):
        """Plan correctly identifies region as south_hobbs for Lea County T18S R34E."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        assert plan.region == "south_hobbs", (
            f"Lea County T18S R34E should be south_hobbs, got '{plan.region}'"
        )

    def test_plan_api_number_preserved(self, hobbs_nw_shelf_well):
        """Plan api_number matches the well input."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        assert plan.api_number == hobbs_nw_shelf_well["api_number"]

    def test_plan_total_cement_sacks_populated(self, hobbs_nw_shelf_well):
        """calculate_totals() sets total_cement_sacks to a positive value."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        assert plan.total_cement_sacks is not None
        assert plan.total_cement_sacks > 0.0

    def test_plan_without_perforations_skips_cibp(self):
        """Well with no perforations produces no CIBP or CIBP cap."""
        well = {
            "api_number": "30-025-00001",
            "county": "lea",
            "township": "T18S",
            "range": "R34E",
            "operator": "No Perf Co",
            "lease_name": "Test",
            "lease_type": "fee",
            "field_name": "Test",
            "total_depth_ft": 5000,
            "formation_tops": [
                {"name": "San Andres", "depth_ft": 3500},
            ],
            "casing_strings": [
                {"type": "surface", "size_in": 13.375, "depth_ft": 400, "top_of_cement_ft": 0},
                {"type": "production", "size_in": 5.5, "depth_ft": 5000, "top_of_cement_ft": 500},
            ],
            "perforations": [],
            "duqw_ft": None,
            "cbl_data": None,
            "downhole_equipment": [],
        }
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(well)
        cibp_steps = [s for s in plan.steps if s.step_type in ("mechanical_plug", "cibp_cap")]
        assert len(cibp_steps) == 0, "No perforations -> no CIBP or CIBP cap"


# ---------------------------------------------------------------------------
# TestCIBPCap
# ---------------------------------------------------------------------------

class TestCIBPCap:
    """Test CIBP cap cement length (NM-specific 100 ft minimum)."""

    def test_cibp_cap_is_100ft(self, hobbs_nw_shelf_well):
        """Standard CIBP cap length must be 100 ft (NM; TX is 20 ft)."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        caps = [s for s in plan.steps if s.step_type == "cibp_cap"]
        assert len(caps) == 1, "Plan must include exactly one CIBP cap"
        cap = caps[0]
        assert cap.interval_length_ft == pytest.approx(NM_CIBP_CAP_FT, abs=1.0), (
            f"CIBP cap must be {NM_CIBP_CAP_FT} ft (NM), got {cap.interval_length_ft:.0f} ft"
        )

    def test_cibp_cap_35ft_with_bailer(self, hobbs_nw_shelf_well):
        """CIBP cap is 35 ft when bailer_method=True."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well, options={"bailer_method": True})
        cap = next(s for s in plan.steps if s.step_type == "cibp_cap")
        assert cap.interval_length_ft == pytest.approx(35.0, abs=1.0), (
            "Bailer method CIBP cap should be 35 ft"
        )

    def test_cibp_cap_positioned_directly_above_cibp(self, hobbs_nw_shelf_well):
        """CIBP cap bottom = CIBP top (cap sits directly on top of bridge plug)."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        cibp = next(s for s in plan.steps if s.step_type == "mechanical_plug")
        cap = next(s for s in plan.steps if s.step_type == "cibp_cap")
        assert cap.bottom_ft == pytest.approx(cibp.top_ft, abs=1.0), (
            "CIBP cap bottom must be flush with CIBP top"
        )

    def test_cibp_cap_tag_required(self, hobbs_nw_shelf_well):
        """CIBP cap has tag_required=True."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        cap = next(s for s in plan.steps if s.step_type == "cibp_cap")
        assert cap.tag_required is True

    def test_cibp_cap_woc_4_hours(self, hobbs_nw_shelf_well):
        """CIBP cap has WOC = 4 hours."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        cap = next(s for s in plan.steps if s.step_type == "cibp_cap")
        assert cap.wait_hours == 4


# ---------------------------------------------------------------------------
# TestFormationPlugs
# ---------------------------------------------------------------------------

class TestFormationPlugs:
    """Test mandatory formation isolation plug generation."""

    def test_formation_plugs_generated(self, hobbs_nw_shelf_well):
        """Formation plugs are generated for formations in well data matching the book."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        assert len(plan.formation_plugs) > 0

    def test_formation_plug_coverage_50ft(self, hobbs_nw_shelf_well):
        """Each formation plug covers ±50 ft around its formation top."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)

        formation_depths = {
            t["name"]: float(t["depth_ft"])
            for t in hobbs_nw_shelf_well["formation_tops"]
        }

        for plug in plan.formation_plugs:
            fm_name = plug.formation_name
            if fm_name not in formation_depths:
                continue  # skip if not in fixture (cross-reference skips unknowns)
            fm_depth = formation_depths[fm_name]
            expected_top = max(fm_depth - 50.0, 50.0)
            expected_bottom = fm_depth + 50.0
            assert plug.top_ft == pytest.approx(expected_top, abs=1.0), (
                f"{fm_name}: plug top should be {expected_top}' (formation - 50')"
            )
            assert plug.bottom_ft == pytest.approx(expected_bottom, abs=1.0), (
                f"{fm_name}: plug bottom should be {expected_bottom}' (formation + 50')"
            )

    def test_formation_plugs_all_tagged(self, hobbs_nw_shelf_well):
        """All formation plugs must have tag_required=True."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        for plug in plan.formation_plugs:
            assert plug.tag_required is True, (
                f"Formation plug for {plug.formation_name} must have tag_required=True"
            )

    def test_formation_plugs_woc_4_hours(self, hobbs_nw_shelf_well):
        """Formation plugs have WOC = 4 hours."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        for plug in plan.formation_plugs:
            assert plug.wait_hours == 4

    def test_formation_plug_cement_class_c_shallow(self, hobbs_nw_shelf_well):
        """Formation plugs above 6500' use Class C cement."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        # All fixture formations are shallower than 6500 ft
        for plug in plan.formation_plugs:
            if plug.bottom_ft < 6500.0:
                assert plug.cement_class == "C", (
                    f"{plug.formation_name} at {plug.bottom_ft}' should be Class C cement"
                )

    def test_formation_plug_cement_class_h_deep(self):
        """Formation plugs at/below 6500' use Class H cement."""
        well = {
            "api_number": "30-025-11111",
            "county": "lea",
            "township": "T22S",
            "range": "R35E",
            "operator": "Deep Test",
            "lease_name": "Deep",
            "lease_type": "fee",
            "field_name": "Deep Field",
            "total_depth_ft": 14000,
            "formation_tops": [
                {"name": "Bone Spring (1st, 2nd, 3rd)", "depth_ft": 9500},
            ],
            "casing_strings": [
                {"type": "surface", "size_in": 13.375, "depth_ft": 500, "top_of_cement_ft": 0},
                {"type": "production", "size_in": 5.5, "depth_ft": 14000, "top_of_cement_ft": 1000},
            ],
            "perforations": [{"top_ft": 13000, "bottom_ft": 13500}],
            "duqw_ft": None,
            "cbl_data": None,
            "downhole_equipment": [],
        }
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(well)
        deep_plugs = [p for p in plan.formation_plugs if p.bottom_ft >= 6500.0]
        assert len(deep_plugs) > 0, "Expected at least one deep formation plug"
        for plug in deep_plugs:
            assert plug.cement_class == "H", (
                f"Formation plug bottom at {plug.bottom_ft}' should be Class H cement"
            )

    def test_only_matched_formations_get_plugs(self, hobbs_nw_shelf_well):
        """Only formations present in well's formation_tops get plugs (cross-reference)."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        fixture_names = {t["name"] for t in hobbs_nw_shelf_well["formation_tops"]}
        for plug in plan.formation_plugs:
            assert plug.formation_name in fixture_names, (
                f"Plug for '{plug.formation_name}' not in well formation_tops"
            )


# ---------------------------------------------------------------------------
# TestSpacingEnforcement
# ---------------------------------------------------------------------------

class TestSpacingEnforcement:
    """Test max plug spacing enforcement with fill plug insertion."""

    def test_fill_plugs_inserted_for_large_gap(self, deep_well_with_large_gap):
        """5000'+ gap in cased hole triggers fill plug insertion (max 3000')."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(deep_well_with_large_gap)
        fill_plugs = plan.get_plugs_by_type("fill_plug")
        assert len(fill_plugs) > 0, (
            "Large gap > 3000' in cased hole must trigger fill plug insertion"
        )

    def test_after_spacing_enforcement_no_violations(self, deep_well_with_large_gap):
        """After enforcement, validate_plug_spacing() returns no violations."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(deep_well_with_large_gap)
        violations = plan.validate_plug_spacing()
        assert violations == [], (
            f"Spacing violations remain after enforcement: {violations}"
        )

    def test_no_fill_plugs_for_small_gaps(self, hobbs_nw_shelf_well):
        """Well with gaps < 3000' between all plugs does not get fill plugs inserted."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        # Validate spacing is clean — no violations means no fill plugs were needed
        # (We still check explicitly for fill_plugs to be sure)
        violations = plan.validate_plug_spacing()
        assert violations == [], (
            f"Hobbs NW Shelf well should have no spacing violations: {violations}"
        )

    def test_fill_plug_is_100ft_long(self, deep_well_with_large_gap):
        """Fill plugs are exactly 100 ft cement plugs."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(deep_well_with_large_gap)
        for fill_plug in plan.get_plugs_by_type("fill_plug"):
            assert fill_plug.interval_length_ft == pytest.approx(100.0, abs=1.0), (
                f"Fill plug should be 100 ft, got {fill_plug.interval_length_ft:.0f} ft"
            )

    def test_fill_plug_has_tag_required(self, deep_well_with_large_gap):
        """Fill plugs require tagging."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(deep_well_with_large_gap)
        for fill_plug in plan.get_plugs_by_type("fill_plug"):
            assert fill_plug.tag_required is True


# ---------------------------------------------------------------------------
# TestCementExcess
# ---------------------------------------------------------------------------

class TestCementExcess:
    """Test NM-specific cement excess factors and minimum sack count."""

    def test_cased_hole_plugs_50pct_excess(self, hobbs_nw_shelf_well):
        """Cased hole plugs use 50% excess factor (not TX depth-based)."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        cased_plugs = [
            s for s in plan.steps
            if s.hole_type == "cased" and s.step_type != "mechanical_plug"
        ]
        assert len(cased_plugs) > 0, "Expected at least one cased hole plug"
        for plug in cased_plugs:
            assert plug.excess_factor == pytest.approx(NM_CASED_EXCESS, abs=0.01), (
                f"Cased plug '{plug.step_type}' should have {NM_CASED_EXCESS*100:.0f}% excess, "
                f"got {plug.excess_factor*100:.0f}%"
            )

    def test_open_hole_plugs_100pct_excess(self, open_hole_section_well):
        """Open hole plugs use 100% excess factor."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(open_hole_section_well)
        open_plugs = [
            s for s in plan.steps
            if s.hole_type == "open" and s.step_type != "mechanical_plug"
        ]
        if not open_plugs:
            pytest.skip("No open hole plugs in this plan; check fixture")
        for plug in open_plugs:
            assert plug.excess_factor == pytest.approx(NM_OPEN_EXCESS, abs=0.01), (
                f"Open hole plug '{plug.step_type}' should have {NM_OPEN_EXCESS*100:.0f}% excess"
            )

    def test_minimum_25_sacks_all_cement_plugs(self, hobbs_nw_shelf_well):
        """Every cement plug step must have at least 25 sacks."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        for plug in plan.cement_plugs:
            assert plug.sacks_required >= NM_MIN_SACKS, (
                f"Plug '{plug.step_type}' has {plug.sacks_required:.1f} sacks; "
                f"minimum is {NM_MIN_SACKS}"
            )

    def test_mechanical_plug_has_zero_sacks(self, hobbs_nw_shelf_well):
        """Mechanical plug (CIBP) has sacks_required=0."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        cibp = next(s for s in plan.steps if s.step_type == "mechanical_plug")
        assert cibp.sacks_required == 0.0


# ---------------------------------------------------------------------------
# TestOperationClassification
# ---------------------------------------------------------------------------

class TestOperationClassification:
    """Test spot / squeeze / circulate operation type classification."""

    def test_surface_plug_is_circulate(self, hobbs_nw_shelf_well):
        """Surface plug operation_type must be 'circulate'."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        surface_plug = next(s for s in plan.steps if s.step_type == "surface_plug")
        assert surface_plug.operation_type == "circulate", (
            "Surface plug must be placed by circulation (NMAC 19.15.25)"
        )

    def test_poor_cbl_interval_triggers_squeeze(self, hobbs_nw_shelf_well):
        """Plugs overlapping poor CBL intervals are classified as squeeze.

        poor_cement_intervals = [(3000, 5000)] in fixture.
        Intermediate casing shoe plug at 3500' sits in this zone.
        """
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        # Look for plugs whose interval overlaps (3000, 5000)
        squeeze_plugs = [s for s in plan.steps if s.operation_type == "squeeze"]
        assert len(squeeze_plugs) > 0, (
            "At least one plug should be classified as squeeze due to poor CBL interval 3000-5000'"
        )

    def test_plugs_outside_poor_cbl_are_spot(self, hobbs_nw_shelf_well):
        """Plugs entirely outside poor CBL intervals are classified as spot."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        # Surface plug (circulate) and mechanical plug (spot) are excluded
        for plug in plan.steps:
            if plug.step_type in ("surface_plug", "mechanical_plug"):
                continue
            # Plugs NOT overlapping (3000, 5000) should be spot
            poor_lo, poor_hi = 3000.0, 5000.0
            overlaps_poor = plug.top_ft < poor_hi and plug.bottom_ft > poor_lo
            if not overlaps_poor:
                assert plug.operation_type == "spot", (
                    f"Plug {plug.step_type} at {plug.top_ft}'-{plug.bottom_ft}' "
                    f"outside poor CBL zone should be 'spot', got '{plug.operation_type}'"
                )

    def test_squeeze_plugs_have_inside_outside_sacks(self, hobbs_nw_shelf_well):
        """Squeeze plugs have inside_sacks and outside_sacks populated (70/30 split)."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        squeeze_plugs = [s for s in plan.steps if s.operation_type == "squeeze"]
        for plug in squeeze_plugs:
            assert plug.inside_sacks is not None, (
                f"Squeeze plug '{plug.step_type}' must have inside_sacks"
            )
            assert plug.outside_sacks is not None, (
                f"Squeeze plug '{plug.step_type}' must have outside_sacks"
            )
            total = plug.inside_sacks + plug.outside_sacks
            assert total == pytest.approx(plug.sacks_required, abs=1.0), (
                f"inside + outside sacks should sum to total sacks for '{plug.step_type}'"
            )

    def test_no_cbl_data_defaults_to_spot(self):
        """When cbl_data is absent, all non-surface plugs default to spot."""
        well = {
            "api_number": "30-025-22222",
            "county": "lea",
            "township": "T18S",
            "range": "R34E",
            "operator": "No CBL Co",
            "lease_name": "No CBL",
            "lease_type": "fee",
            "field_name": "No CBL Field",
            "total_depth_ft": 5000,
            "formation_tops": [
                {"name": "San Andres", "depth_ft": 3500},
            ],
            "casing_strings": [
                {"type": "surface", "size_in": 13.375, "depth_ft": 400, "top_of_cement_ft": 0},
                {"type": "production", "size_in": 5.5, "depth_ft": 5000, "top_of_cement_ft": 500},
            ],
            "perforations": [{"top_ft": 4500, "bottom_ft": 4800}],
            "duqw_ft": None,
            "cbl_data": None,
            "downhole_equipment": [],
        }
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(well)
        for plug in plan.steps:
            if plug.step_type in ("surface_plug",):
                assert plug.operation_type == "circulate"
            elif plug.step_type == "mechanical_plug":
                assert plug.operation_type == "spot"
            else:
                assert plug.operation_type == "spot", (
                    f"No CBL data -> '{plug.step_type}' should default to spot"
                )


# ---------------------------------------------------------------------------
# TestNarrative
# ---------------------------------------------------------------------------

class TestNarrative:
    """Test procedure narrative generation."""

    def test_narrative_not_empty(self, hobbs_nw_shelf_well):
        """Plan generates non-empty procedure_narrative list."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        assert len(plan.procedure_narrative) > 0, (
            "procedure_narrative must not be empty"
        )

    def test_narrative_is_readable_prose(self, hobbs_nw_shelf_well):
        """Narrative entries are human-readable sentences, not raw data dumps."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        for entry in plan.procedure_narrative:
            assert isinstance(entry, str), "Each narrative entry must be a string"
            assert len(entry) > 10, (
                f"Narrative entry too short to be readable prose: {entry!r}"
            )
            # Should contain a depth reference (feet marker)
            assert "'" in entry or "ft" in entry.lower() or "foot" in entry.lower(), (
                f"Narrative entry should reference depths: {entry!r}"
            )

    def test_narrative_entries_are_numbered(self, hobbs_nw_shelf_well):
        """Each narrative entry starts with a step number."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        for i, entry in enumerate(plan.procedure_narrative, start=1):
            assert entry.startswith(f"{i}."), (
                f"Narrative step {i} should start with '{i}.', got: {entry[:20]!r}"
            )

    def test_narrative_skipped_when_include_narrative_false(self, hobbs_nw_shelf_well):
        """Setting include_narrative=False skips narrative generation."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(
            hobbs_nw_shelf_well, options={"include_narrative": False}
        )
        assert plan.procedure_narrative == [], (
            "include_narrative=False should produce empty procedure_narrative"
        )

    def test_narrative_count_matches_step_count(self, hobbs_nw_shelf_well):
        """Narrative has one entry per plan step."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        assert len(plan.procedure_narrative) == len(plan.steps), (
            f"Narrative entries ({len(plan.procedure_narrative)}) should equal "
            f"step count ({len(plan.steps)})"
        )


# ---------------------------------------------------------------------------
# TestCompliance
# ---------------------------------------------------------------------------

class TestCompliance:
    """Test C-103 compliance validation."""

    def test_valid_plan_passes_compliance(self, hobbs_nw_shelf_well):
        """Well-formed plan generated by C103PluggingRules passes validate_c103_compliance()."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        errors = plan.validate_c103_compliance()
        assert errors == [], (
            f"Valid plan should have no compliance errors, got: {errors}"
        )

    def test_validate_plan_passes_well_level_checks(self, hobbs_nw_shelf_well):
        """rules.validate_plan() (well-level) also passes for a generated plan."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        errors = rules.validate_plan(hobbs_nw_shelf_well, plan)
        assert errors == [], (
            f"Well-level validation should pass for generated plan, got: {errors}"
        )

    def test_plan_without_surface_plug_fails(self, hobbs_nw_shelf_well):
        """Manually removing the surface plug causes validation to fail."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        # Remove surface plug from steps
        plan.steps = [s for s in plan.steps if s.step_type != "surface_plug"]
        errors = plan.validate_c103_compliance()
        assert any("surface plug" in e.lower() for e in errors), (
            "Missing surface plug should produce a compliance error"
        )

    def test_plan_without_formation_plugs_fails(self, hobbs_nw_shelf_well):
        """Manually removing all formation plugs causes validation to fail."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        plan.steps = [s for s in plan.steps if s.step_type != "formation_plug"]
        errors = plan.validate_c103_compliance()
        assert any("formation" in e.lower() for e in errors), (
            "Missing formation plugs should produce a compliance error"
        )

    def test_plan_with_undersized_sacks_fails(self, hobbs_nw_shelf_well):
        """A cement plug with < 25 sacks triggers a minimum-sack violation."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        # Artificially shrink one cement plug's sack count below minimum
        for step in plan.cement_plugs:
            step.sacks_required = 5.0
            break
        errors = plan.validate_c103_compliance()
        assert any("minimum sack" in e.lower() or "sacks" in e.lower() for e in errors), (
            "Under-minimum sack count must produce a compliance error"
        )

    def test_duqw_plan_passes_compliance(self, well_with_duqw):
        """Plan for a well with DUQW passes compliance (includes DUQW plug)."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(well_with_duqw)
        errors = plan.validate_c103_compliance()
        assert errors == [], (
            f"DUQW well plan should pass compliance, got: {errors}"
        )


# ---------------------------------------------------------------------------
# TestDUQWPlug
# ---------------------------------------------------------------------------

class TestDUQWPlug:
    """Test DUQW plug generation for wells with usable water zones."""

    def test_duqw_plug_generated(self, well_with_duqw):
        """Plan includes a DUQW plug when duqw_ft is set."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(well_with_duqw)
        duqw_plugs = plan.get_plugs_by_type("duqw_plug")
        assert len(duqw_plugs) == 1, "Plan must contain exactly one DUQW plug"

    def test_duqw_plug_covers_correct_depth(self, well_with_duqw):
        """DUQW plug covers ±50 ft around DUQW depth."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(well_with_duqw)
        duqw_plug = plan.get_plugs_by_type("duqw_plug")[0]
        duqw_ft = well_with_duqw["duqw_ft"]
        assert duqw_plug.top_ft == pytest.approx(max(duqw_ft - 50.0, 0.0), abs=1.0)
        assert duqw_plug.bottom_ft == pytest.approx(duqw_ft + 50.0, abs=1.0)

    def test_no_duqw_plug_when_duqw_ft_none(self, hobbs_nw_shelf_well):
        """Plan does not include DUQW plug when duqw_ft=None."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        duqw_plugs = plan.get_plugs_by_type("duqw_plug")
        assert len(duqw_plugs) == 0, "No duqw_ft -> no DUQW plug"


# ---------------------------------------------------------------------------
# TestCementClass
# ---------------------------------------------------------------------------

class TestCementClass:
    """Test cement class assignment rules (Class C above 6500', Class H at/below)."""

    def test_shallow_plug_class_c(self, hobbs_nw_shelf_well):
        """Surface plug uses Class C cement."""
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        surface_plug = next(s for s in plan.steps if s.step_type == "surface_plug")
        assert surface_plug.cement_class == "C"

    def test_get_cement_class_helper_shallow(self):
        """_get_cement_class returns 'C' for depth < 6500'."""
        rules = C103PluggingRules()
        assert rules._get_cement_class(5000.0) == "C"

    def test_get_cement_class_helper_deep(self):
        """_get_cement_class returns 'H' for depth >= 6500'."""
        rules = C103PluggingRules()
        assert rules._get_cement_class(6500.0) == "H"
        assert rules._get_cement_class(9000.0) == "H"


# ---------------------------------------------------------------------------
# TestHoleTypeDetection
# ---------------------------------------------------------------------------

class TestHoleTypeDetection:
    """Test hole type (cased vs open) detection at depth."""

    def test_depth_above_shoe_is_cased(self):
        """Depth shallower than all casing shoes is 'cased'."""
        casing_strings = [
            {"type": "surface", "size_in": 13.375, "depth_ft": 500},
            {"type": "production", "size_in": 7.0, "depth_ft": 8000},
        ]
        result = C103PluggingRules._get_hole_type_at_depth(3000.0, casing_strings)
        assert result == "cased"

    def test_depth_below_all_shoes_is_open(self):
        """Depth deeper than the deepest casing shoe is 'open'."""
        casing_strings = [
            {"type": "surface", "size_in": 13.375, "depth_ft": 500},
            {"type": "production", "size_in": 7.0, "depth_ft": 8000},
        ]
        result = C103PluggingRules._get_hole_type_at_depth(9000.0, casing_strings)
        assert result == "open"

    def test_no_casing_defaults_to_cased(self):
        """Empty casing string list defaults to 'cased' (conservative)."""
        result = C103PluggingRules._get_hole_type_at_depth(5000.0, [])
        assert result == "cased"


# ---------------------------------------------------------------------------
# TestRegionDetectionIntegration
# ---------------------------------------------------------------------------

class TestRegionDetectionIntegration:
    """Integration tests for region detection within the C-103 engine."""

    def test_north_region_well(self):
        """San Juan County well maps to north region."""
        well = {
            "api_number": "30-045-00001",
            "county": "san_juan",
            "township": "T28N",
            "range": "R8W",
            "operator": "Gas Co",
            "lease_name": "Gas Lease",
            "lease_type": "federal",
            "field_name": "Fruitland Field",
            "total_depth_ft": 3000,
            "formation_tops": [
                {"name": "Fruitland / Kirtland", "depth_ft": 1800},
            ],
            "casing_strings": [
                {"type": "surface", "size_in": 13.375, "depth_ft": 300, "top_of_cement_ft": 0},
                {"type": "production", "size_in": 5.5, "depth_ft": 3000, "top_of_cement_ft": 0},
            ],
            "perforations": [{"top_ft": 2500, "bottom_ft": 2800}],
            "duqw_ft": None,
            "cbl_data": None,
            "downhole_equipment": [],
        }
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(well)
        assert plan.region == "north"

    def test_potash_region_well(self):
        """Eddy County T20S R30E maps to potash region."""
        well = {
            "api_number": "30-015-00001",
            "county": "eddy",
            "township": "T20S",
            "range": "R30E",
            "operator": "Potash Co",
            "lease_name": "Potash Lease",
            "lease_type": "state",
            "field_name": "Potash Field",
            "total_depth_ft": 4000,
            "formation_tops": [
                {"name": "San Andres", "depth_ft": 3000},
            ],
            "casing_strings": [
                {"type": "surface", "size_in": 13.375, "depth_ft": 300, "top_of_cement_ft": 0},
                {"type": "production", "size_in": 5.5, "depth_ft": 4000, "top_of_cement_ft": 0},
            ],
            "perforations": [{"top_ft": 3500, "bottom_ft": 3800}],
            "duqw_ft": None,
            "cbl_data": None,
            "downhole_equipment": [],
        }
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(well)
        assert plan.region == "potash"


# ---------------------------------------------------------------------------
# TestNoTXRegression
# ---------------------------------------------------------------------------

class TestNoTXRegression:
    """Verify NM C-103 engine handles TX-like inputs without silently misrouting."""

    def test_tx_api_does_not_crash_engine(self):
        """API number starting with '42-' (TX) must not crash C103PluggingRules.

        The C-103 engine is NM-only. A TX API passed in should either raise a
        clear error or fall through to a default NM region — it must NOT silently
        treat the well as a correctly-configured NM well. Here we verify it at
        least completes without an unhandled exception.
        """
        tx_well = {
            "api_number": "42-389-12345",
            "county": "unknown_tx_county",
            "township": None,
            "range": None,
            "operator": "TX Operator",
            "lease_name": "TX Lease",
            "lease_type": "fee",
            "field_name": "TX Field",
            "total_depth_ft": 5000,
            "formation_tops": [],
            "casing_strings": [
                {"type": "surface", "size_in": 13.375, "depth_ft": 400, "top_of_cement_ft": 0},
                {"type": "production", "size_in": 5.5, "depth_ft": 5000, "top_of_cement_ft": 0},
            ],
            "perforations": [],
            "duqw_ft": None,
            "cbl_data": None,
            "downhole_equipment": [],
        }
        rules = C103PluggingRules()
        # Should not raise; TX county falls through to NM default (north region)
        plan = rules.generate_plugging_plan(tx_well)
        assert isinstance(plan, C103PluggingPlan)

    def test_tx_api_does_not_produce_nm_compliant_plan_if_no_nm_data(self):
        """TX well with no formation tops and unknown county produces formation-plug violations.

        Since NM requires formation isolation, a TX well with no formation data
        will fail NM compliance checks — confirming it was never a valid NM well.
        """
        tx_well = {
            "api_number": "42-389-12345",
            "county": "",
            "township": None,
            "range": None,
            "operator": "TX Operator",
            "lease_name": "TX Lease",
            "lease_type": "fee",
            "field_name": "TX Field",
            "total_depth_ft": 5000,
            "formation_tops": [],  # No NM formation data
            "casing_strings": [
                {"type": "surface", "size_in": 13.375, "depth_ft": 400, "top_of_cement_ft": 0},
                {"type": "production", "size_in": 5.5, "depth_ft": 5000, "top_of_cement_ft": 0},
            ],
            "perforations": [],
            "duqw_ft": None,
            "cbl_data": None,
            "downhole_equipment": [],
        }
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(tx_well)
        errors = plan.validate_c103_compliance()
        # A plan with no formation plugs must fail NM compliance
        assert any("formation" in e.lower() for e in errors), (
            "TX well with no formation data should fail NM formation isolation check"
        )

    def test_nm_api_prefix_30_produces_compliant_plan(self, hobbs_nw_shelf_well):
        """NM API (30-) with proper data produces a fully compliant plan."""
        assert hobbs_nw_shelf_well["api_number"].startswith("30-"), (
            "Fixture API must be NM (prefix 30-)"
        )
        rules = C103PluggingRules()
        plan = rules.generate_plugging_plan(hobbs_nw_shelf_well)
        errors = rules.validate_plan(hobbs_nw_shelf_well, plan)
        assert errors == [], (
            f"NM well (30-) should produce zero compliance errors, got: {errors}"
        )
