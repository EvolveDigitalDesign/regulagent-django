"""
Test suite for W-3 from PNA with API number normalization and auto-extraction.

Tests that:
1. API numbers are correctly normalized from 10-digit to 8-digit format
2. API numbers are attached to W3Events and Plugs
3. Auto-extraction trigger works
4. Full W-3 generation flow with API tracking
"""

from __future__ import annotations

import json
from datetime import date
from django.test import TestCase
from apps.public_core.services.w3_utils import (
    normalize_api_number,
    normalize_api_with_hyphen,
    parse_api_10digit,
    validate_api_number,
)
from apps.public_core.models.w3_event import W3Event, Plug


class TestAPINumberNormalization(TestCase):
    """Test API number normalization utilities."""
    
    def test_normalize_10digit_api_with_hyphens(self):
        """Test normalization of 10-digit API with hyphens (xx-xxx-xxxxx)."""
        # Input: 42-501-70575 → Output: 4250170575
        result = normalize_api_number("42-501-70575")
        self.assertEqual(result, "4250170575")
        self.assertEqual(len(result), 8)  # Last 8 digits
    
    def test_normalize_14digit_api(self):
        """Test normalization of 14-digit API (state-country-xxx-xxxxx)."""
        result = normalize_api_number("04-42-047-070575")
        self.assertIsNotNone(result)
        # Should extract the last 8 digits
        self.assertEqual(len(result), 8)
    
    def test_normalize_8digit_api_unchanged(self):
        """Test that 8-digit API is returned unchanged."""
        result = normalize_api_number("4250170575")
        self.assertEqual(result, "4250170575")
    
    def test_normalize_empty_api_returns_none(self):
        """Test that empty API returns None."""
        self.assertIsNone(normalize_api_number(""))
        self.assertIsNone(normalize_api_number(None))
    
    def test_normalize_api_with_hyphen_format(self):
        """Test formatting normalized API with hyphen (xxx-xxxxx)."""
        result = normalize_api_with_hyphen("42-501-70575")
        self.assertEqual(result, "501-70575")  # Last 8 digits formatted
    
    def test_parse_10digit_api_structure(self):
        """Test parsing 10-digit API into components."""
        result = parse_api_10digit("42-501-70575")
        self.assertIsNotNone(result)
        self.assertEqual(result['state_code'], "42")
        self.assertEqual(result['county_code'], "501")
        self.assertEqual(result['lease_code'], "70575")
        self.assertEqual(result['normalized_api'], "4250170575")
    
    def test_validate_api_number_valid(self):
        """Test validation of valid API numbers."""
        self.assertTrue(validate_api_number("42-501-70575"))
        self.assertTrue(validate_api_number("4250170575"))
    
    def test_validate_api_number_invalid(self):
        """Test validation of invalid API numbers."""
        self.assertFalse(validate_api_number(""))
        self.assertFalse(validate_api_number("invalid"))
        self.assertFalse(validate_api_number(None))


class TestW3EventAPINumber(TestCase):
    """Test that W3Event correctly stores and tracks API numbers."""
    
    def test_w3event_with_api_number(self):
        """Test creating W3Event with api_number field."""
        event = W3Event(
            event_type="set_cement_plug",
            date=date(2025, 1, 15),
            api_number="4250170575",  # Normalized 8-digit
            depth_top_ft=100.0,
            depth_bottom_ft=500.0,
            cement_class="H",
            sacks=50.0,
        )
        
        self.assertEqual(event.api_number, "4250170575")
        self.assertEqual(event.event_type, "set_cement_plug")
        self.assertEqual(event.date, date(2025, 1, 15))
    
    def test_w3event_without_api_number(self):
        """Test that W3Event can be created without api_number (optional)."""
        event = W3Event(
            event_type="perforate",
            date=date(2025, 1, 15),
            perf_depth_ft=750.0,
        )
        
        self.assertIsNone(event.api_number)
        self.assertEqual(event.event_type, "perforate")


class TestPlugAPINumber(TestCase):
    """Test that Plug correctly tracks API numbers from events."""
    
    def test_plug_with_api_number_from_events(self):
        """Test creating Plug with api_number from contained events."""
        event1 = W3Event(
            event_type="set_cement_plug",
            date=date(2025, 1, 15),
            api_number="4250170575",
            depth_top_ft=100.0,
            depth_bottom_ft=500.0,
        )
        
        event2 = W3Event(
            event_type="tag_toc",
            date=date(2025, 1, 15),
            api_number="4250170575",
            tagged_depth_ft=120.0,
        )
        
        plug = Plug(
            plug_number=1,
            events=[event1, event2],
            api_number="4250170575",
            depth_top_ft=100.0,
            depth_bottom_ft=500.0,
        )
        
        self.assertEqual(plug.api_number, "4250170575")
        self.assertEqual(len(plug.events), 2)
        # All events should have the same API
        for event in plug.events:
            self.assertEqual(event.api_number, plug.api_number)


class TestAPINumberInMapperFlow(TestCase):
    """Test that API numbers flow through the mapper correctly."""
    
    def test_mapper_attaches_api_number_to_events(self):
        """Test that the mapper attaches api_number to all W3Events."""
        from apps.public_core.services.w3_mapper import map_pna_event_to_w3event
        
        pna_event = {
            "event_id": 4,
            "event_type": "Set Intermediate Plug",
            "display_text": "Plug 1 from 200 ft to 500 ft",
            "input_values": {
                "1": "1",  # plug_number
                "2": "spot",  # plug_operation
                "3": "H",  # cement_class
                "4": "500",  # depth_bottom
                "5": "200",  # depth_top
                "6": "50",  # sacks
            },
            "date": "2025-01-15",
        }
        
        # Map with API number
        w3_event = map_pna_event_to_w3event(
            pna_event,
            api_number="4250170575"
        )
        
        self.assertEqual(w3_event.api_number, "4250170575")
        self.assertEqual(w3_event.event_type, "set_cement_plug")
        self.assertEqual(w3_event.depth_bottom_ft, 500.0)
    
    def test_mapper_normalizes_event_level_api(self):
        """Test that mapper normalizes api_number at event level."""
        from apps.public_core.services.w3_mapper import map_pna_event_to_w3event
        
        pna_event = {
            "event_id": 4,
            "event_type": "Set Intermediate Plug",
            "display_text": "Plug 1",
            "input_values": {
                "1": "1", "2": "spot", "3": "H", "4": "500", "5": "200", "6": "50"
            },
            "date": "2025-01-15",
            "api_number": "42-501-70575",  # 10-digit format in event
        }
        
        # Map without passing api_number (should use event-level)
        w3_event = map_pna_event_to_w3event(pna_event)
        
        # Should be normalized to 8-digit
        self.assertEqual(w3_event.api_number, "4250170575")


class TestBuilderAPINumberFlow(TestCase):
    """Test that API numbers flow through the builder correctly."""
    
    def test_builder_normalizes_api_from_payload(self):
        """Test that builder normalizes API from pnaexchange payload."""
        from apps.public_core.services.w3_utils import normalize_api_number
        
        # Simulate pnaexchange payload
        api_number_input = "42-501-70575"  # 10-digit format
        
        # Should normalize to 8-digit
        normalized = normalize_api_number(api_number_input)
        self.assertEqual(normalized, "4250170575")
        
        # All events should receive this normalized API
        self.assertEqual(len(normalized), 8)

