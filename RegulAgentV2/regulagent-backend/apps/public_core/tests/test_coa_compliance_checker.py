"""Tests for COAComplianceChecker."""
import pytest
from apps.public_core.services.coa_compliance_checker import (
    RuleStatus,
    RuleResult,
    ComplianceResult,
    check,
    _r1_cement_class_depth,
    _r2_min_plug_length,
    _r3_woc_time,
    _r4_tag_requirement,
    _r6_max_spacing,
    _r7_surface_plug,
)


def _make_plug(plug_number, top=None, bottom=None, cement_class=None,
               plug_type="cement_plug", woc_hours=None, tagged=False):
    """Helper to create a plug dict matching reconciliation format."""
    return {
        "plug_number": plug_number,
        "plug_type": plug_type,
        "step_type": plug_type,
        "depth_top_ft": top,
        "depth_bottom_ft": bottom,
        "top_ft": top,
        "bottom_ft": bottom,
        "cement_class": cement_class,
        "woc_hours": woc_hours,
        "woc_tagged": tagged,
        "tagged_depth_ft": top if tagged else None,
    }


class TestR1CementClassDepth:
    def test_pass_shallow_class_a(self):
        plugs = [_make_plug(1, top=100, bottom=200, cement_class="A")]
        result = _r1_cement_class_depth(plugs)
        assert result.status == RuleStatus.PASS

    def test_pass_deep_class_h(self):
        plugs = [_make_plug(1, top=6000, bottom=6100, cement_class="H")]
        result = _r1_cement_class_depth(plugs)
        assert result.status == RuleStatus.PASS

    def test_fail_deep_class_a(self):
        plugs = [_make_plug(1, top=6000, bottom=6100, cement_class="A")]
        result = _r1_cement_class_depth(plugs)
        assert result.status == RuleStatus.FAIL

    def test_skipped_no_data(self):
        plugs = [_make_plug(1)]
        result = _r1_cement_class_depth(plugs)
        assert result.status == RuleStatus.SKIPPED


class TestR2MinPlugLength:
    def test_pass_100ft_plug(self):
        plugs = [_make_plug(1, top=5000, bottom=5100)]
        result = _r2_min_plug_length(plugs)
        assert result.status == RuleStatus.PASS

    def test_fail_30ft_plug(self):
        plugs = [_make_plug(1, top=5000, bottom=5030)]
        result = _r2_min_plug_length(plugs)
        assert result.status == RuleStatus.FAIL

    def test_cibp_exempt(self):
        plugs = [_make_plug(1, top=5000, bottom=5000, plug_type="cibp")]
        result = _r2_min_plug_length(plugs)
        assert result.status == RuleStatus.SKIPPED  # CIBP has 0 length but is exempt

    def test_boundary_50ft(self):
        plugs = [_make_plug(1, top=5000, bottom=5050)]
        result = _r2_min_plug_length(plugs)
        assert result.status == RuleStatus.PASS


class TestR3WocTime:
    def test_pass_sufficient_woc(self):
        plugs = [_make_plug(1, woc_hours=12)]
        result = _r3_woc_time(plugs, {})
        assert result.status == RuleStatus.PASS

    def test_fail_insufficient_woc(self):
        plugs = [_make_plug(1, woc_hours=4)]
        result = _r3_woc_time(plugs, {})
        assert result.status == RuleStatus.FAIL

    def test_warning_no_data(self):
        plugs = [_make_plug(1)]
        result = _r3_woc_time(plugs, {})
        assert result.status == RuleStatus.WARNING


class TestR4TagRequirement:
    def test_pass_tagged(self):
        plugs = [_make_plug(1, top=5000, tagged=True)]
        result = _r4_tag_requirement(plugs)
        assert result.status == RuleStatus.PASS

    def test_warning_not_tagged(self):
        plugs = [_make_plug(1, top=5000)]
        result = _r4_tag_requirement(plugs)
        assert result.status == RuleStatus.WARNING

    def test_surface_plug_exempt(self):
        plugs = [_make_plug(1, top=0, plug_type="surface_plug")]
        result = _r4_tag_requirement(plugs)
        assert result.status == RuleStatus.SKIPPED


class TestR6MaxSpacing:
    def test_pass_close_plugs(self):
        plugs = [
            _make_plug(1, top=0, bottom=100),
            _make_plug(2, top=500, bottom=600),
        ]
        result = _r6_max_spacing(plugs)
        assert result.status == RuleStatus.PASS

    def test_warning_large_gap(self):
        plugs = [
            _make_plug(1, top=0, bottom=100),
            _make_plug(2, top=5000, bottom=5100),
        ]
        result = _r6_max_spacing(plugs)
        assert result.status == RuleStatus.WARNING

    def test_skipped_single_plug(self):
        plugs = [_make_plug(1, top=0, bottom=100)]
        result = _r6_max_spacing(plugs)
        assert result.status == RuleStatus.SKIPPED


class TestR7SurfacePlug:
    def test_pass_surface_plug(self):
        plugs = [_make_plug(1, top=0, bottom=100, plug_type="surface_plug")]
        result = _r7_surface_plug(plugs)
        assert result.status == RuleStatus.PASS

    def test_fail_no_surface_plug(self):
        plugs = [_make_plug(1, top=5000, bottom=5100)]
        result = _r7_surface_plug(plugs)
        assert result.status == RuleStatus.FAIL

    def test_pass_any_plug_near_surface(self):
        plugs = [_make_plug(1, top=10, bottom=100)]
        result = _r7_surface_plug(plugs)
        assert result.status == RuleStatus.PASS


class TestCheckIntegration:
    def test_full_check_returns_compliance_result(self):
        reconciliation = {
            "comparisons": [
                {
                    "plug_number": 1,
                    "plug_type": "surface_plug",
                    "step_type": "surface_plug",
                    "depth_top_ft": 0,
                    "depth_bottom_ft": 100,
                    "top_ft": 0,
                    "bottom_ft": 100,
                    "cement_class": "A",
                    "woc_hours": 12,
                    "woc_tagged": True,
                    "tagged_depth_ft": 5,
                },
                {
                    "plug_number": 2,
                    "plug_type": "cement_plug",
                    "step_type": "cement_plug",
                    "depth_top_ft": 5000,
                    "depth_bottom_ft": 5100,
                    "top_ft": 5000,
                    "bottom_ft": 5100,
                    "cement_class": "A",
                    "woc_hours": 10,
                    "woc_tagged": True,
                    "tagged_depth_ft": 5005,
                },
            ]
        }
        result = check(
            reconciliation_result=reconciliation,
            parse_result={"days": []},
            formation_audit={},
            payload={},
            api_number="42-000-00001",
        )
        assert isinstance(result, ComplianceResult)
        assert result.rules_checked == 7
        assert result.api_number == "42-000-00001"
        assert result.overall_status in ("compliant", "warnings", "non_compliant", "insufficient_data")

    def test_empty_reconciliation(self):
        result = check(
            reconciliation_result={"comparisons": []},
            parse_result={"days": []},
            formation_audit={},
            payload={},
            api_number="42-000-00002",
        )
        assert isinstance(result, ComplianceResult)
        assert result.rules_checked == 7
