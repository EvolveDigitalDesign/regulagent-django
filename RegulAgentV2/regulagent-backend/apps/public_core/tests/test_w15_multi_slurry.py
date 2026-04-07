"""
Failing tests for multi-slurry cement sack extraction from W-15 documents.

Current behaviour (BEFORE fix):
- extract_historic_cement_jobs() reads only the flat `sacks` field per cement job.
- It ignores a `slurries` array entirely.

Expected behaviour (AFTER fix):
1. If `slurries` array is present, preserve it in the output job entry.
2. If top-level `sacks` is None/missing but `slurries` exists, compute the sum
   from the individual slurry `sacks` values.
3. If top-level `sacks` is explicitly provided, use it as-is (don't recompute).
4. Old W-15 JSON with only flat `sacks` and no `slurries` key must still work
   unchanged (backward compatibility).

All tests in this file are expected to FAIL against the current implementation
and PASS once BE2 adds slurry support to extract_historic_cement_jobs().
"""

import unittest
from unittest.mock import Mock, patch


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

# A realistic multi-slurry surface casing cement job where the LLM left
# top-level sacks as None but populated the slurries array.
MULTI_SLURRY_W15_NULL_TOTAL = {
    "cementing_data": [
        {
            "job": "surface",
            "interval_top_ft": 0,
            "interval_bottom_ft": 4243,
            "cement_top_ft": 0,
            "sacks": None,          # LLM didn't sum — function must compute
            "slurry_density_ppg": None,
            "additives": None,
            "yield_ft3_per_sk": None,
            "slurries": [
                {
                    "slurry_no": 1,
                    "sacks": 1700,
                    "cement_class": "H",
                    "additives": ["gel"],
                    "slurry_density_ppg": 15.6,
                    "volume_cuft": 850,
                    "height_ft": 2000,
                },
                {
                    "slurry_no": 2,
                    "sacks": 600,
                    "cement_class": "H",
                    "additives": [],
                    "slurry_density_ppg": 16.2,
                    "volume_cuft": 300,
                    "height_ft": 800,
                },
                {
                    # Third slurry with 0 sacks (spacer / flush row)
                    "slurry_no": 3,
                    "sacks": 0,
                    "cement_class": None,
                    "additives": [],
                    "slurry_density_ppg": 8.33,
                    "volume_cuft": 0,
                    "height_ft": 0,
                },
            ],
        }
    ]
}

# Old-style flat W-15 — no slurries key at all.
FLAT_SACKS_W15 = {
    "cementing_data": [
        {
            "job": "production",
            "interval_top_ft": 100,
            "interval_bottom_ft": 8500,
            "cement_top_ft": 200,
            "sacks": 600,
            "slurry_density_ppg": 15.8,
            "additives": ["silica"],
            "yield_ft3_per_sk": 1.18,
            # No "slurries" key — represents legacy extraction output
        }
    ]
}

# W-15 where the LLM DID sum the total (sacks=2300) AND provided slurries.
# The function should trust the explicit top-level value and not recompute.
MULTI_SLURRY_W15_WITH_TOTAL = {
    "cementing_data": [
        {
            "job": "intermediate",
            "interval_top_ft": 0,
            "interval_bottom_ft": 9800,
            "cement_top_ft": 0,
            "sacks": 2300,          # Already summed by LLM — must be honoured
            "slurry_density_ppg": 15.9,
            "additives": None,
            "yield_ft3_per_sk": None,
            "slurries": [
                {
                    "slurry_no": 1,
                    "sacks": 1700,
                    "cement_class": "H",
                    "additives": [],
                    "slurry_density_ppg": 15.6,
                    "volume_cuft": 850,
                    "height_ft": 2000,
                },
                {
                    "slurry_no": 2,
                    "sacks": 600,
                    "cement_class": "H",
                    "additives": [],
                    "slurry_density_ppg": 16.2,
                    "volume_cuft": 300,
                    "height_ft": 800,
                },
            ],
        }
    ]
}


# ---------------------------------------------------------------------------
# Helper: build the mock chain that extract_historic_cement_jobs() calls
# ---------------------------------------------------------------------------

def _make_mock_filter(json_data: dict) -> Mock:
    """
    Returns a mock that satisfies:
        ExtractedDocument.objects.filter(...).order_by(...).first()
    and whose result has .json_data == json_data.
    """
    mock_doc = Mock()
    mock_doc.json_data = json_data

    mock_filter = Mock()
    mock_filter.order_by.return_value.first.return_value = mock_doc
    return mock_filter


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestW15MultiSlurryExtraction(unittest.TestCase):
    """
    Tests for multi-slurry support in extract_historic_cement_jobs().

    All four tests FAIL against the current code and are designed to PASS
    once BE2 adds slurry handling.
    """

    _PATCH_TARGET = (
        "apps.public_core.services.well_geometry_builder"
        ".ExtractedDocument.objects.filter"
    )

    # ------------------------------------------------------------------
    # Test 1: sacks must be computed as the sum across slurries when the
    #         top-level sacks field is None.
    # ------------------------------------------------------------------

    def test_multi_slurry_sacks_summed(self):
        """
        W-15 JSON with slurries=[1700, 600, 0] and top-level sacks=None.
        After fix: job_entry['sacks'] == 2300 (sum of slurry sacks).

        FAILS now because current code returns sacks=None (passes None through).
        """
        from apps.public_core.services.well_geometry_builder import (
            extract_historic_cement_jobs,
        )

        with patch(self._PATCH_TARGET, return_value=_make_mock_filter(MULTI_SLURRY_W15_NULL_TOTAL)):
            result = extract_historic_cement_jobs("4238933390")

        self.assertEqual(len(result), 1, "Expected exactly one cement job entry")
        job = result[0]

        # This assertion FAILS against current code (returns None).
        self.assertEqual(
            job["sacks"],
            2300,
            f"Expected sacks=2300 (1700+600+0), got {job['sacks']!r}. "
            "Function must sum slurry sacks when top-level sacks is None.",
        )

    # ------------------------------------------------------------------
    # Test 2: old flat W-15 without slurries must continue to work.
    # ------------------------------------------------------------------

    def test_backward_compat_flat_sacks(self):
        """
        Old W-15 JSON with only flat sacks=600 and no 'slurries' key.
        After fix: job_entry['sacks'] == 600 (unchanged).

        Expected to PASS against current code but is included here to guard
        regressions — if this breaks after the fix, the fix is wrong.
        """
        from apps.public_core.services.well_geometry_builder import (
            extract_historic_cement_jobs,
        )

        with patch(self._PATCH_TARGET, return_value=_make_mock_filter(FLAT_SACKS_W15)):
            result = extract_historic_cement_jobs("4238933390")

        self.assertEqual(len(result), 1, "Expected exactly one cement job entry")
        job = result[0]

        self.assertEqual(
            job["sacks"],
            600,
            f"Backward compat broken: expected sacks=600, got {job['sacks']!r}.",
        )

        # Also confirm no spurious 'slurries' key injected for flat records.
        # (If the key is absent from the input, it should either be absent
        #  in the output OR be None/empty — but never a non-empty list.)
        slurries_val = job.get("slurries")
        self.assertFalse(
            slurries_val,
            f"Expected no slurries in output for flat record, got {slurries_val!r}.",
        )

    # ------------------------------------------------------------------
    # Test 3: per-slurry detail must be preserved in the output entry.
    # ------------------------------------------------------------------

    def test_slurries_preserved_in_output(self):
        """
        W-15 JSON with slurries array.
        After fix: job_entry['slurries'] contains the original slurry dicts.

        FAILS now because current code never writes a 'slurries' key.
        """
        from apps.public_core.services.well_geometry_builder import (
            extract_historic_cement_jobs,
        )

        with patch(self._PATCH_TARGET, return_value=_make_mock_filter(MULTI_SLURRY_W15_NULL_TOTAL)):
            result = extract_historic_cement_jobs("4238933390")

        self.assertEqual(len(result), 1, "Expected exactly one cement job entry")
        job = result[0]

        # Key must exist.
        self.assertIn(
            "slurries",
            job,
            "job_entry must contain a 'slurries' key when W-15 has slurry detail.",
        )

        slurries = job["slurries"]

        # Must be a list with all three slurry rows.
        self.assertIsInstance(slurries, list, "'slurries' value must be a list.")
        self.assertEqual(
            len(slurries),
            3,
            f"Expected 3 slurry entries, got {len(slurries)}.",
        )

        # Spot-check first slurry's fields are preserved.
        first = slurries[0]
        self.assertEqual(first.get("slurry_no"), 1)
        self.assertEqual(first.get("sacks"), 1700)
        self.assertEqual(first.get("cement_class"), "H")
        self.assertAlmostEqual(first.get("slurry_density_ppg"), 15.6)

        # Spot-check second slurry.
        second = slurries[1]
        self.assertEqual(second.get("slurry_no"), 2)
        self.assertEqual(second.get("sacks"), 600)

    # ------------------------------------------------------------------
    # Test 4: explicit top-level sacks must not be overridden by sum.
    # ------------------------------------------------------------------

    def test_top_level_sacks_overrides_if_present(self):
        """
        W-15 JSON with sacks=2300 AND slurries=[1700, 600].
        After fix: job_entry['sacks'] == 2300 (explicit value wins, no recompute).

        FAILS now because current code returns 2300 accidentally (it just copies
        the field), but the slurries key is still missing — this test also
        checks that slurries are preserved, which will fail.
        """
        from apps.public_core.services.well_geometry_builder import (
            extract_historic_cement_jobs,
        )

        with patch(self._PATCH_TARGET, return_value=_make_mock_filter(MULTI_SLURRY_W15_WITH_TOTAL)):
            result = extract_historic_cement_jobs("4238933390")

        self.assertEqual(len(result), 1, "Expected exactly one cement job entry")
        job = result[0]

        # Top-level value must be preserved verbatim.
        self.assertEqual(
            job["sacks"],
            2300,
            f"Expected sacks=2300 (explicit top-level), got {job['sacks']!r}.",
        )

        # Slurries must still be present (2 rows in this fixture).
        self.assertIn(
            "slurries",
            job,
            "job_entry must contain 'slurries' key even when top-level sacks is set.",
        )
        self.assertEqual(
            len(job["slurries"]),
            2,
            f"Expected 2 slurry entries, got {len(job['slurries'])}.",
        )


if __name__ == "__main__":
    unittest.main()
