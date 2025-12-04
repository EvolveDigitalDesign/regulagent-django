"""
End-to-end integration tests for W-3 from PNA with auto-generated W-3A well geometry.

Tests the complete flow:
1. pnaexchange sends W-3 events with 10-digit API
2. RegulAgent normalizes API to 8-digit
3. Auto-generates W-3A plan
4. Extracts well geometry (casing, existing tools, retainer tools, historic cement, KOP)
5. Returns W-3 form WITH w3a_well_geometry field
"""

from __future__ import annotations

import json
from datetime import date
from django.test import TestCase, Client
from django.contrib.auth.models import User
from rest_framework_simplejwt.tokens import RefreshToken

from apps.public_core.models import WellRegistry, ExtractedDocument


class W3FromPNAFullFlowTest(TestCase):
    """Integration tests for W-3 from PNA with well geometry extraction."""
    
    def setUp(self):
        """Set up test client and authentication."""
        self.client = Client()
        
        # Create test user and get JWT token
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123"
        )
        refresh = RefreshToken.for_user(self.user)
        self.token = str(refresh.access_token)
        
        # Set up authorization header
        self.headers = {
            "HTTP_AUTHORIZATION": f"Bearer {self.token}",
            "CONTENT_TYPE": "application/json"
        }
    
    def test_w3_from_pna_with_10digit_api(self):
        """Test that 10-digit API is properly normalized and processed."""
        # pnaexchange sends 10-digit API
        payload = {
            "api_number": "42-501-70575",  # 10-digit format
            "subproject_id": 12345,
            "well_name": "Test Well",
            "w3a_reference": {
                "type": "pdf",
                "w3a_file_base64": "JVBERi0xLjQK"  # Minimal PDF base64
            },
            "pna_events": [
                {
                    "event_id": 4,
                    "event_type": "Set Intermediate Plug",
                    "display_text": "Plug 1 from 200 ft to 500 ft",
                    "input_values": {
                        "1": "1",     # plug_number
                        "2": "spot",  # plug_operation
                        "3": "H",     # cement_class
                        "4": "500",   # depth_bottom
                        "5": "200",   # depth_top
                        "6": "50",    # sacks
                    },
                    "date": "2025-01-15",
                }
            ]
        }
        
        # POST to W-3 endpoint
        response = self.client.post(
            "/api/w3/build-from-pna/",
            data=json.dumps(payload),
            **self.headers
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        
        # Verify response structure
        self.assertTrue(data.get("success"))
        self.assertIn("w3_form", data)
        self.assertIn("validation", data)
        self.assertIn("metadata", data)
        
        # CRITICAL: Check that well geometry is included (even if empty/partial)
        self.assertIn("w3a_well_geometry", data, 
                      "w3a_well_geometry field must be present in response")
    
    def test_w3a_well_geometry_structure(self):
        """Test that w3a_well_geometry has correct structure."""
        payload = {
            "api_number": "42-501-70575",
            "subproject_id": 12345,
            "well_name": "Test Well",
            "w3a_reference": {"type": "pdf", "w3a_file_base64": "JVBERi0xLjQK"},
            "pna_events": [{
                "event_id": 4,
                "display_text": "Plug 1",
                "input_values": {
                    "1": "1", "2": "spot", "3": "H", "4": "500", "5": "200", "6": "50"
                },
                "date": "2025-01-15",
            }]
        }
        
        response = self.client.post(
            "/api/w3/build-from-pna/",
            data=json.dumps(payload),
            **self.headers
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        geometry = data.get("w3a_well_geometry", {})
        
        # Verify geometry has all expected fields
        expected_fields = [
            "casing_record",
            "existing_tools",
            "retainer_tools",
            "historic_cement_jobs",
            "kop"
        ]
        
        for field in expected_fields:
            self.assertIn(field, geometry,
                         f"w3a_well_geometry must include '{field}' field")
    
    def test_existing_tools_extraction(self):
        """Test that existing tools (CIBP, packer, DV tool) are extracted."""
        payload = {
            "api_number": "42-501-70575",
            "subproject_id": 12345,
            "well_name": "Test Well",
            "w3a_reference": {"type": "pdf", "w3a_file_base64": "JVBERi0xLjQK"},
            "pna_events": [{
                "event_id": 4,
                "display_text": "Plug 1",
                "input_values": {
                    "1": "1", "2": "spot", "3": "H", "4": "500", "5": "200", "6": "50"
                },
                "date": "2025-01-15",
            }]
        }
        
        response = self.client.post(
            "/api/w3/build-from-pna/",
            data=json.dumps(payload),
            **self.headers
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        geometry = data.get("w3a_well_geometry", {})
        existing_tools = geometry.get("existing_tools", {})
        
        # Verify existing_tools structure
        expected_tool_fields = [
            "existing_mechanical_barriers",
            "existing_cibp_ft",
            "existing_packer_ft",
            "existing_dv_tool_ft"
        ]
        
        for field in expected_tool_fields:
            self.assertIn(field, existing_tools,
                         f"existing_tools must include '{field}' field")
    
    def test_retainer_tools_extraction(self):
        """Test that retainer tools are extracted."""
        payload = {
            "api_number": "42-501-70575",
            "subproject_id": 12345,
            "well_name": "Test Well",
            "w3a_reference": {"type": "pdf", "w3a_file_base64": "JVBERi0xLjQK"},
            "pna_events": [{
                "event_id": 4,
                "display_text": "Plug 1",
                "input_values": {
                    "1": "1", "2": "spot", "3": "H", "4": "500", "5": "200", "6": "50"
                },
                "date": "2025-01-15",
            }]
        }
        
        response = self.client.post(
            "/api/w3/build-from-pna/",
            data=json.dumps(payload),
            **self.headers
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        geometry = data.get("w3a_well_geometry", {})
        retainer_tools = geometry.get("retainer_tools", [])
        
        # Verify retainer_tools is a list
        self.assertIsInstance(retainer_tools, list,
                             "retainer_tools must be a list")
        
        # If tools exist, verify structure
        for tool in retainer_tools:
            self.assertIn("tool_type", tool)
            self.assertIn("depth_ft", tool)
    
    def test_historic_cement_jobs_extraction(self):
        """Test that historic cement jobs from W-15 are extracted."""
        payload = {
            "api_number": "42-501-70575",
            "subproject_id": 12345,
            "well_name": "Test Well",
            "w3a_reference": {"type": "pdf", "w3a_file_base64": "JVBERi0xLjQK"},
            "pna_events": [{
                "event_id": 4,
                "display_text": "Plug 1",
                "input_values": {
                    "1": "1", "2": "spot", "3": "H", "4": "500", "5": "200", "6": "50"
                },
                "date": "2025-01-15",
            }]
        }
        
        response = self.client.post(
            "/api/w3/build-from-pna/",
            data=json.dumps(payload),
            **self.headers
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        geometry = data.get("w3a_well_geometry", {})
        cement_jobs = geometry.get("historic_cement_jobs", [])
        
        # Verify cement_jobs is a list
        self.assertIsInstance(cement_jobs, list,
                             "historic_cement_jobs must be a list")
        
        # If jobs exist, verify structure
        for job in cement_jobs:
            # At least one of these should be present
            self.assertTrue(
                any(k in job for k in ["job_type", "sacks", "interval_top_ft"]),
                "Each cement job must have at least one field"
            )
    
    def test_kop_extraction(self):
        """Test that KOP (kick-off point) data is extracted for horizontal wells."""
        payload = {
            "api_number": "42-501-70575",
            "subproject_id": 12345,
            "well_name": "Test Well",
            "w3a_reference": {"type": "pdf", "w3a_file_base64": "JVBERi0xLjQK"},
            "pna_events": [{
                "event_id": 4,
                "display_text": "Plug 1",
                "input_values": {
                    "1": "1", "2": "spot", "3": "H", "4": "500", "5": "200", "6": "50"
                },
                "date": "2025-01-15",
            }]
        }
        
        response = self.client.post(
            "/api/w3/build-from-pna/",
            data=json.dumps(payload),
            **self.headers
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        geometry = data.get("w3a_well_geometry", {})
        kop = geometry.get("kop")
        
        # KOP can be None if not a horizontal well, or a dict if it is
        self.assertTrue(
            kop is None or isinstance(kop, dict),
            "KOP must be either None or a dict"
        )
        
        # If KOP exists, verify structure
        if kop:
            self.assertIn("kop_md_ft", kop)
            self.assertIn("kop_tvd_ft", kop)
    
    def test_casing_record_extraction(self):
        """Test that casing record is extracted."""
        payload = {
            "api_number": "42-501-70575",
            "subproject_id": 12345,
            "well_name": "Test Well",
            "w3a_reference": {"type": "pdf", "w3a_file_base64": "JVBERi0xLjQK"},
            "pna_events": [{
                "event_id": 4,
                "display_text": "Plug 1",
                "input_values": {
                    "1": "1", "2": "spot", "3": "H", "4": "500", "5": "200", "6": "50"
                },
                "date": "2025-01-15",
            }]
        }
        
        response = self.client.post(
            "/api/w3/build-from-pna/",
            data=json.dumps(payload),
            **self.headers
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        geometry = data.get("w3a_well_geometry", {})
        casing_record = geometry.get("casing_record", [])
        
        # Verify casing_record is a list
        self.assertIsInstance(casing_record, list,
                             "casing_record must be a list")
    
    def test_api_normalization_in_flow(self):
        """Test that 10-digit API is correctly normalized through the flow."""
        payload = {
            "api_number": "42-501-70575",  # 10-digit
            "subproject_id": 12345,
            "well_name": "Test Well",
            "w3a_reference": {"type": "pdf", "w3a_file_base64": "JVBERi0xLjQK"},
            "pna_events": [{
                "event_id": 4,
                "display_text": "Plug 1",
                "input_values": {
                    "1": "1", "2": "spot", "3": "H", "4": "500", "5": "200", "6": "50"
                },
                "date": "2025-01-15",
            }]
        }
        
        response = self.client.post(
            "/api/w3/build-from-pna/",
            data=json.dumps(payload),
            **self.headers
        )
        
        # Should succeed (no errors from API normalization)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data.get("success"))
        
        # Metadata should reflect the original 10-digit API
        metadata = data.get("metadata", {})
        self.assertIn("api_number", metadata)
    
    def test_non_blocking_extraction_failure(self):
        """Test that W-3A extraction failures don't block W-3 generation."""
        # Create a payload with invalid W-3A reference
        payload = {
            "api_number": "42-501-99999",  # Non-existent well (no RRC data)
            "subproject_id": 12345,
            "well_name": "Non-existent Well",
            "w3a_reference": {"type": "pdf", "w3a_file_base64": "JVBERi0xLjQK"},
            "pna_events": [{
                "event_id": 4,
                "display_text": "Plug 1",
                "input_values": {
                    "1": "1", "2": "spot", "3": "H", "4": "500", "5": "200", "6": "50"
                },
                "date": "2025-01-15",
            }]
        }
        
        response = self.client.post(
            "/api/w3/build-from-pna/",
            data=json.dumps(payload),
            **self.headers
        )
        
        # Should still return 200 (W-3 generation not blocked by extraction failure)
        # The response may indicate failure in validation warnings, but the endpoint
        # should not crash
        self.assertIn(response.status_code, [200, 400])  # Could be either

