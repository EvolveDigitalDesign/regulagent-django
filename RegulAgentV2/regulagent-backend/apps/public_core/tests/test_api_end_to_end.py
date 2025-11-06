from __future__ import annotations

import json
from django.test import TestCase, Client


class TestApiEndToEnd(TestCase):
    def setUp(self):
        self.client = Client()

    def test_from_api_snapshot_artifacts_and_export(self):
        # Run plan build (known golden well)
        resp = self.client.post(
            "/api/plans/w3a/from-api",
            data={
                "api10": "4200346118",
                "plugs_mode": "combined",
                "merge_threshold_ft": 500,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = json.loads(resp.content.decode("utf-8"))
        self.assertIn("steps", data)
        self.assertTrue(len(data["steps"]) > 0)

        # History must have at least one baseline snapshot
        hist = self.client.get("/api/plans/4200346118/history")
        self.assertEqual(hist.status_code, 200, hist.content)
        h = json.loads(hist.content.decode("utf-8"))
        self.assertGreaterEqual(h.get("count", 0), 1)

        # Filing export should return rrc_export section
        filing = self.client.get("/api/plans/4200346118/filing/export")
        self.assertEqual(filing.status_code, 200, filing.content)
        f = json.loads(filing.content.decode("utf-8"))
        self.assertIn("filing", f)
        self.assertTrue(len(f.get("filing", [])) > 0)


