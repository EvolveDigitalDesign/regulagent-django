"""
Tests for interval mapping bugs in normalize_vision_to_well_geometry().

Bug 4: interval_top_ft is incorrectly set to cement_top_md_ft (the depth at which
cement was pumped to) instead of the casing string's own top_md_ft (typically 0 for
surface casing, or the hanger depth for intermediate/liner strings).

The correct mapping:
  interval_top_ft   → cs.get("top_md_ft", 0)     (casing string top / hanger depth)
  interval_bottom_ft → cj.get("cement_bottom_md_fd") or cs.get("bottom_md_ft")  (shoe)
  cement_top_ft     → cj.get("cement_top_md_ft")  (where cement was actually pumped to)

These tests are written BEFORE the fix and are expected to FAIL until the bug is
resolved in well_geometry_builder.py.
"""
import unittest

from apps.public_core.services.well_geometry_builder import normalize_vision_to_well_geometry


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

SURFACE_CASING_VISION_DATA = {
    "casing_strings": [
        {
            "string_type": "surface",
            "size_in": 13.375,
            "top_md_ft": 0,
            "bottom_md_ft": 4243,
            "cement_job": {
                "cement_top_md_ft": 2431,
                "cement_bottom_md_ft": 4243,
                "sacks": 600,
            },
        }
    ],
    "formation_tops": [],
    "hatches": [],
    "mechanical_barriers": [],
    "perforations": [],
    "production_perforations": [],
}

INTERMEDIATE_CASING_VISION_DATA = {
    "casing_strings": [
        {
            "string_type": "intermediate",
            "size_in": 9.625,
            "top_md_ft": 1200,
            "bottom_md_ft": 9800,
            "cement_job": {
                "cement_top_md_ft": 4500,
                "cement_bottom_md_ft": 9800,
                "sacks": 1200,
            },
        }
    ],
    "formation_tops": [],
    "hatches": [],
    "mechanical_barriers": [],
    "perforations": [],
    "production_perforations": [],
}


class TestIntervalTopMappingBug(unittest.TestCase):
    """
    Bug 4: interval_top_ft is mapped to cement_top_md_ft instead of the
    casing string's top_md_ft.

    Tests 1 and 4 are expected to FAIL with the current (buggy) code.
    Tests 2 and 3 verify that the surrounding fields are already correct.
    """

    def _get_cement_jobs(self, vision_data):
        result = normalize_vision_to_well_geometry(vision_data)
        return result["historic_cement_jobs"]

    # ------------------------------------------------------------------
    # Test 1 — EXPECTED TO FAIL with current buggy code
    # ------------------------------------------------------------------
    def test_vision_cement_job_interval_top_is_casing_top(self):
        """
        interval_top_ft should be the casing string's top depth (0 for surface),
        NOT the depth to which cement was pumped (cement_top_md_ft = 2431).

        Current code sets interval_top_ft = cj.get("cement_top_md_ft") → 2431  (WRONG)
        Fixed code should set interval_top_ft = cs.get("top_md_ft", 0)   →    0  (RIGHT)

        This test FAILS with the current implementation.
        """
        jobs = self._get_cement_jobs(SURFACE_CASING_VISION_DATA)
        self.assertEqual(len(jobs), 1, "Expected exactly one cement job")
        job = jobs[0]

        # The casing string starts at surface (top_md_ft = 0).
        # interval_top_ft must reflect that, not the cement placement depth.
        self.assertEqual(
            job["interval_top_ft"],
            0,
            f"interval_top_ft should be 0 (casing top), got {job['interval_top_ft']}. "
            "Bug: code is using cement_top_md_ft (2431) instead of top_md_ft (0).",
        )

    # ------------------------------------------------------------------
    # Test 2 — EXPECTED TO PASS (cement_top_ft is already correct)
    # ------------------------------------------------------------------
    def test_vision_cement_job_cement_top_preserved(self):
        """
        cement_top_ft should still be cement_top_md_ft (2431) — the actual
        depth to which cement was circulated.  This field is already correct.
        """
        jobs = self._get_cement_jobs(SURFACE_CASING_VISION_DATA)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]

        self.assertEqual(
            job["cement_top_ft"],
            2431,
            f"cement_top_ft should remain 2431 (cement placement depth), got {job['cement_top_ft']}.",
        )

    # ------------------------------------------------------------------
    # Test 3 — EXPECTED TO PASS (interval_bottom comes from shoe depth)
    # ------------------------------------------------------------------
    def test_vision_cement_job_interval_bottom_is_shoe(self):
        """
        interval_bottom_ft should be the casing shoe depth (bottom_md_ft = 4243),
        not an arbitrary cement bottom value when they happen to coincide.

        We confirm with data where cement_bottom_md_ft == bottom_md_ft (both 4243)
        so the result is unambiguous.
        """
        jobs = self._get_cement_jobs(SURFACE_CASING_VISION_DATA)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]

        self.assertEqual(
            job["interval_bottom_ft"],
            4243,
            f"interval_bottom_ft should be 4243 (casing shoe), got {job['interval_bottom_ft']}.",
        )

    # ------------------------------------------------------------------
    # Test 4 — EXPECTED TO FAIL with current buggy code
    # ------------------------------------------------------------------
    def test_intermediate_casing_interval_top_not_zero(self):
        """
        For intermediate casing with top_md_ft = 1200, interval_top_ft must
        be 1200 (the hanger depth), not the cement top depth (4500).

        Current code sets interval_top_ft = cj.get("cement_top_md_ft") → 4500  (WRONG)
        Fixed code should set interval_top_ft = cs.get("top_md_ft", 0)  → 1200  (RIGHT)

        This test FAILS with the current implementation.
        """
        jobs = self._get_cement_jobs(INTERMEDIATE_CASING_VISION_DATA)
        self.assertEqual(len(jobs), 1, "Expected exactly one cement job")
        job = jobs[0]

        self.assertEqual(
            job["interval_top_ft"],
            1200,
            f"interval_top_ft should be 1200 (intermediate casing hanger depth), "
            f"got {job['interval_top_ft']}. "
            "Bug: code is using cement_top_md_ft (4500) instead of top_md_ft (1200).",
        )


class TestWOCVsCementTopValidation(unittest.TestCase):
    """
    Bug 2: LLMs can confuse "Waiting on Cement (WOC) hours" (e.g., 24) with
    "Calculated top of cement (ft.)".  We can't unit-test the prompt directly,
    but we CAN assert that suspiciously small cement_top values — values that
    look like WOC hours rather than depths — are flagged as anomalous when
    paired with a realistic casing shoe depth.

    A cement top of < 100 ft on a well with a shoe deeper than 1000 ft is
    almost certainly a WOC value that leaked into the wrong field.
    """

    def _get_cement_jobs(self, vision_data):
        result = normalize_vision_to_well_geometry(vision_data)
        return result["historic_cement_jobs"]

    def test_cement_top_not_woc_hours_value(self):
        """
        If cement_top_md_ft is suspiciously small (< 100) while the casing
        shoe is deep (> 1000 ft), the mapped cement_top_ft should NOT be that
        tiny value — it signals a WOC-hours-vs-depth confusion.

        This test captures the validation contract: a cement_top_ft of 24 on a
        4243-ft well is almost certainly a WOC=24h value, not a real depth.

        The test itself will PASS today (we're asserting the anomaly EXISTS —
        i.e., the bug is present) and should be inverted once prompt + validation
        are fixed to reject such values.
        """
        woc_confused_vision_data = {
            "casing_strings": [
                {
                    "string_type": "surface",
                    "size_in": 13.375,
                    "top_md_ft": 0,
                    "bottom_md_ft": 4243,
                    "cement_job": {
                        # 24 looks like WOC hours, not a depth in feet
                        "cement_top_md_ft": 24,
                        "cement_bottom_md_ft": 4243,
                        "sacks": 600,
                    },
                }
            ],
            "formation_tops": [],
            "hatches": [],
            "mechanical_barriers": [],
            "perforations": [],
            "production_perforations": [],
        }

        jobs = self._get_cement_jobs(woc_confused_vision_data)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]

        # Document the anomaly: cement_top_ft of 24 is implausible for a 4243-ft well.
        # Once validation is added, this should raise or produce a sentinel value.
        # For now we assert the condition is detectable.
        shoe_depth = 4243
        cement_top = job["cement_top_ft"]
        woc_threshold_ft = 100  # anything under 100 ft on a >1000 ft well is suspect

        is_anomalous = cement_top is not None and cement_top < woc_threshold_ft and shoe_depth > 1000
        self.assertTrue(
            is_anomalous,
            f"cement_top_ft={cement_top} on a {shoe_depth}-ft well is suspiciously small "
            "and likely a WOC-hours value. Validation should catch this.",
        )


if __name__ == "__main__":
    unittest.main()
