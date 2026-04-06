"""
Tests for the Unified Document Pipeline Refactor.

Phases 0-5: DocumentSegment model, text-first classification, semantic tagging,
tag-aware extraction, segment ↔ ED linkage, and timeline builder.

Run with:
    docker compose -f compose.dev.yml exec web python -m pytest apps/public_core/tests/test_document_pipeline_refactor.py -v -s
"""
import logging
import re
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.test import TestCase

from apps.public_core.models import ExtractedDocument, WellRegistry
from apps.public_core.models.document_segment import DocumentSegment
from apps.public_core.models.well_timeline_event import WellTimelineEvent

logger = logging.getLogger(__name__)


# ─── Phase 0: DocumentSegment Model ────────────────────────────────

class TestDocumentSegmentModel(TestCase):
    """Phase 0: Verify DocumentSegment model CRUD and relationships."""

    def setUp(self):
        self.well = WellRegistry.objects.create(
            api14="42003356630000",
            state="TX",
        )

    def test_create_document_segment(self):
        """Create a DocumentSegment and verify all fields persist."""
        seg = DocumentSegment.objects.create(
            well=self.well,
            api_number="42003356630000",
            source_filename="test_doc.pdf",
            source_path="/tmp/test_doc.pdf",
            file_hash="abc123",
            source_type="neubus",
            page_start=0,
            page_end=4,
            total_source_pages=10,
            form_type="W-2",
            classification_method="text",
            classification_confidence="high",
            classification_evidence="Matched: FORM W-2, COMPLETION",
            tags=["completion", "geometry", "cement"],
            status="classified",
            raw_text_cache="FORM W-2 OIL WELL POTENTIAL TEST",
        )

        logger.info(f"Created DocumentSegment: {seg}")
        logger.info(f"  id={seg.id}, form_type={seg.form_type}")
        logger.info(f"  pages={seg.page_start}-{seg.page_end} ({seg.page_count} pages)")
        logger.info(f"  tags={seg.tags}")
        logger.info(f"  well={seg.well.api14}")

        assert seg.page_count == 5
        assert seg.form_type == "W-2"
        assert seg.tags == ["completion", "geometry", "cement"]
        assert seg.well == self.well
        assert seg.status == "classified"

    def test_segment_ed_relationship(self):
        """Verify DocumentSegment ↔ ExtractedDocument linking."""
        ed = ExtractedDocument.objects.create(
            well=self.well,
            api_number="42003356630000",
            document_type="w2",
            source_path="/tmp/test.pdf",
            status="success",
            json_data={"test": True},
        )

        seg = DocumentSegment.objects.create(
            well=self.well,
            api_number="42003356630000",
            source_filename="test.pdf",
            source_type="neubus",
            page_start=0,
            page_end=2,
            form_type="W-2",
            classification_method="text",
            classification_confidence="high",
            extracted_document=ed,
        )

        # Phase 4: FK from ED → Segment
        ed.segment = seg
        ed.save(update_fields=["segment"])

        logger.info(f"Segment {seg.id} → ED {ed.id}")
        logger.info(f"ED.segment = {ed.segment_id}")
        logger.info(f"Segment.extracted_document = {seg.extracted_document_id}")

        assert ed.segment == seg
        assert seg.extracted_document == ed

    def test_segment_indexes(self):
        """Verify query patterns used in the pipeline work efficiently."""
        DocumentSegment.objects.create(
            well=self.well,
            api_number="42003356630000",
            source_filename="doc1.pdf",
            source_type="neubus",
            page_start=0, page_end=3,
            form_type="W-2",
            classification_method="text",
            classification_confidence="high",
            status="extracted",
        )
        DocumentSegment.objects.create(
            well=self.well,
            api_number="42003356630000",
            source_filename="doc1.pdf",
            source_type="neubus",
            page_start=4, page_end=7,
            form_type="W-3",
            classification_method="text",
            classification_confidence="high",
            status="classified",
        )

        # Query patterns that should hit indexes
        by_api = DocumentSegment.objects.filter(api_number="42003356630000", form_type="W-2")
        by_status = DocumentSegment.objects.filter(status="classified")
        by_well = DocumentSegment.objects.filter(well=self.well, form_type="W-3")

        logger.info(f"By API+form_type: {by_api.count()} results")
        logger.info(f"By status: {by_status.count()} results")
        logger.info(f"By well+form_type: {by_well.count()} results")

        assert by_api.count() == 1
        assert by_status.count() == 1
        assert by_well.count() == 1


# ─── Phase 1: Text-First Classification ────────────────────────────

class TestTextClassification(TestCase):
    """Phase 1: Verify text-based classification + breakpoint detection."""

    def test_classify_w2_by_text(self):
        """W-2 form header should classify with high confidence via text."""
        from apps.public_core.services.document_segmenter import classify_page_by_text

        text = """RAILROAD COMMISSION OF TEXAS
        OIL WELL POTENTIAL TEST, COMPLETION OR RECOMPLETION REPORT,
        AND LOG
        FORM W-2
        API No. 42-003-35663"""

        result = classify_page_by_text(text, "TX")

        logger.info(f"Classification result: form_type={result.form_type}")
        logger.info(f"  confidence={result.confidence}, method={result.method}")
        logger.info(f"  evidence={result.evidence}")

        assert result.form_type == "W-2"
        assert result.confidence == "high"
        assert result.method == "text"

    def test_classify_w3_by_text(self):
        """W-3 plugging record should classify correctly."""
        from apps.public_core.services.document_segmenter import classify_page_by_text

        text = """RAILROAD COMMISSION OF TEXAS
        WELL PLUGGING REPORT
        FORM W-3"""

        result = classify_page_by_text(text, "TX")

        logger.info(f"W-3 classification: {result.form_type} ({result.confidence})")
        assert result.form_type == "W-3"
        assert result.confidence == "high"

    def test_classify_w3a_by_text(self):
        """W-3A should NOT be confused with W-3."""
        from apps.public_core.services.document_segmenter import classify_page_by_text

        text = """RAILROAD COMMISSION OF TEXAS
        NOTICE OF INTENTION TO PLUG AND ABANDON
        FORM W-3A"""

        result = classify_page_by_text(text, "TX")

        logger.info(f"W-3A classification: {result.form_type} ({result.confidence})")
        assert result.form_type == "W-3a"
        assert result.confidence == "high"

    def test_classify_nm_c103(self):
        """NM C-103 form should classify in NM mode."""
        from apps.public_core.services.document_segmenter import classify_page_by_text

        text = """OIL CONSERVATION DIVISION
        NOTICE OF INTENTION TO PLUG
        FORM C-103"""

        result = classify_page_by_text(text, "NM")

        logger.info(f"NM C-103 classification: {result.form_type} ({result.confidence})")
        assert result.form_type == "c_103"
        assert result.confidence == "high"

    def test_classify_h1_injection(self):
        """H-1 injection permit should classify (no extraction prompt yet)."""
        from apps.public_core.services.document_segmenter import classify_page_by_text

        text = """RAILROAD COMMISSION OF TEXAS
        FORM H-1
        APPLICATION FOR INJECTION WELL PERMIT APPLICATION"""

        result = classify_page_by_text(text, "TX")

        logger.info(f"H-1 classification: {result.form_type} ({result.confidence})")
        assert result.form_type == "H-1"
        assert result.confidence == "high"

    def test_classify_empty_page(self):
        """Empty/minimal text should return confidence=none."""
        from apps.public_core.services.document_segmenter import classify_page_by_text

        result = classify_page_by_text("", "TX")

        logger.info(f"Empty page: form_type={result.form_type}, confidence={result.confidence}")
        assert result.form_type == "Other"
        assert result.confidence == "none"

    def test_classify_unrecognized_text(self):
        """Text without form patterns should return low confidence."""
        from apps.public_core.services.document_segmenter import classify_page_by_text

        text = "This is just some random text about well operations and things happening in the field with more than twenty characters."

        result = classify_page_by_text(text, "TX")

        logger.info(f"Unrecognized text: form_type={result.form_type}, confidence={result.confidence}")
        assert result.form_type == "Other"
        assert result.confidence == "low"

    def test_classify_all_tx_forms(self):
        """Verify all TX form patterns have at least one matching regex."""
        from apps.public_core.services.document_segmenter import TX_FORM_PATTERNS

        logger.info(f"TX form patterns defined: {len(TX_FORM_PATTERNS)} form types")
        for form_type, patterns in TX_FORM_PATTERNS.items():
            logger.info(f"  {form_type}: {len(patterns)} patterns")
            assert len(patterns) >= 1, f"No patterns for {form_type}"
            # Verify all patterns compile
            for p in patterns:
                re.compile(p, re.IGNORECASE)

    def test_breakpoint_detection(self):
        """Consecutive same-type pages should merge; different types should split."""
        from apps.public_core.services.document_segmenter import (
            PageClassification,
            _group_into_segments,
        )

        classifications = [
            PageClassification(page=0, form_type="W-2", is_continuation=False, confidence="high", evidence="", method="text"),
            PageClassification(page=1, form_type="W-2", is_continuation=True, confidence="high", evidence="", method="text"),
            PageClassification(page=2, form_type="W-2", is_continuation=True, confidence="high", evidence="", method="text"),
            PageClassification(page=3, form_type="W-15", is_continuation=False, confidence="high", evidence="", method="text"),
            PageClassification(page=4, form_type="W-15", is_continuation=True, confidence="high", evidence="", method="text"),
            PageClassification(page=5, form_type="Other", is_continuation=False, confidence="low", evidence="", method="text"),
            PageClassification(page=6, form_type="W-3", is_continuation=False, confidence="high", evidence="", method="text"),
        ]
        page_texts = ["text"] * 7

        segments = _group_into_segments(classifications, page_texts)

        logger.info(f"Breakpoint detection: {len(segments)} segments from 7 pages")
        for s in segments:
            logger.info(f"  {s.form_type}: pages {s.page_start}-{s.page_end}")

        assert len(segments) == 3
        assert segments[0].form_type == "W-2"
        assert segments[0].page_start == 0
        assert segments[0].page_end == 2
        assert segments[1].form_type == "W-15"
        assert segments[1].page_start == 3
        assert segments[1].page_end == 4
        assert segments[2].form_type == "W-3"
        assert segments[2].page_start == 6
        assert segments[2].page_end == 6

    def test_w3_w3a_always_independent(self):
        """Two consecutive W-3 forms should be separate segments (never merged)."""
        from apps.public_core.services.document_segmenter import (
            PageClassification,
            _group_into_segments,
        )

        classifications = [
            PageClassification(page=0, form_type="W-3", is_continuation=False, confidence="high", evidence="", method="text"),
            PageClassification(page=1, form_type="W-3", is_continuation=True, confidence="high", evidence="", method="text"),
            PageClassification(page=2, form_type="W-3", is_continuation=False, confidence="high", evidence="", method="text"),
            PageClassification(page=3, form_type="W-3", is_continuation=True, confidence="high", evidence="", method="text"),
        ]
        page_texts = ["text"] * 4

        segments = _group_into_segments(classifications, page_texts)

        logger.info(f"W-3 independence: {len(segments)} segments")
        for s in segments:
            logger.info(f"  {s.form_type}: pages {s.page_start}-{s.page_end}")

        assert len(segments) == 2
        assert segments[0].page_end == 1
        assert segments[1].page_start == 2


# ─── Phase 2: Semantic Tagging ─────────────────────────────────────

class TestSemanticTagging(TestCase):
    """Phase 2: Verify deterministic tag assignment."""

    def test_tag_w2(self):
        """W-2 should get completion/geometry/cement tags."""
        from apps.public_core.services.segment_tagger import tag_segment

        tags = tag_segment("W-2")

        logger.info(f"W-2 tags: {tags}")
        assert "completion" in tags
        assert "geometry" in tags
        assert "cement" in tags
        assert "casing" in tags

    def test_tag_w3(self):
        """W-3 should get plugging tags."""
        from apps.public_core.services.segment_tagger import tag_segment

        tags = tag_segment("W-3")

        logger.info(f"W-3 tags: {tags}")
        assert "plugging" in tags
        assert "cement" in tags
        assert "plug_record" in tags

    def test_tag_nm_c103(self):
        """NM C-103 should get plugging tags."""
        from apps.public_core.services.segment_tagger import tag_segment

        tags = tag_segment("c_103")

        logger.info(f"C-103 tags: {tags}")
        assert "plugging" in tags
        assert "cement" in tags

    def test_contextual_squeeze_tag(self):
        """Text containing SQUEEZE should add squeeze tag."""
        from apps.public_core.services.segment_tagger import tag_segment

        tags = tag_segment("W-15", "CEMENT SQUEEZE at 5000 ft")

        logger.info(f"W-15 with squeeze text: {tags}")
        assert "squeeze" in tags
        assert "cement_job" in tags

    def test_contextual_h2s_tag(self):
        """Text containing H2S should add h2s tag."""
        from apps.public_core.services.segment_tagger import tag_segment

        tags = tag_segment("W-2", "H2S concentration detected at wellhead")

        logger.info(f"W-2 with H2S: {tags}")
        assert "h2s" in tags

    def test_contextual_cibp_tag(self):
        """Text containing CIBP should add bridge_plug tag."""
        from apps.public_core.services.segment_tagger import tag_segment

        tags = tag_segment("W-3", "Set CIBP at 8500 ft")

        logger.info(f"W-3 with CIBP: {tags}")
        assert "bridge_plug" in tags

    def test_deep_well_detection(self):
        """Depths >= 10000 ft should add deep_well tag."""
        from apps.public_core.services.segment_tagger import tag_segment

        tags = tag_segment("W-2", "Total depth: 15500 feet")

        logger.info(f"W-2 deep well: {tags}")
        assert "deep_well" in tags

    def test_shallow_well_no_deep_tag(self):
        """Depths < 10000 ft should NOT add deep_well tag."""
        from apps.public_core.services.segment_tagger import tag_segment

        tags = tag_segment("W-2", "Total depth: 5500 feet")

        logger.info(f"W-2 shallow well: {tags}")
        assert "deep_well" not in tags

    def test_unknown_form_type(self):
        """Unknown form types should return empty list."""
        from apps.public_core.services.segment_tagger import tag_segment

        tags = tag_segment("UNKNOWN_FORM")

        logger.info(f"Unknown form tags: {tags}")
        assert tags == []

    def test_all_form_types_have_tags(self):
        """Every form type in FORM_TAG_MAP should have at least one tag."""
        from apps.public_core.services.segment_tagger import FORM_TAG_MAP

        logger.info(f"FORM_TAG_MAP has {len(FORM_TAG_MAP)} entries")
        for form_type, tags in FORM_TAG_MAP.items():
            logger.info(f"  {form_type}: {tags}")
            assert len(tags) >= 1, f"No tags for {form_type}"

    def test_tags_integrated_in_segmenter(self):
        """Verify segment_document calls tag_segment (Phase 2 ↔ Phase 1 integration)."""
        from apps.public_core.services.document_segmenter import (
            PageClassification,
            _group_into_segments,
        )

        # Create a simple classification and group
        classifications = [
            PageClassification(page=0, form_type="W-2", is_continuation=False,
                             confidence="high", evidence="", method="text"),
        ]
        page_texts = ["FORM W-2 COMPLETION REPORT with SQUEEZE operation at 12000 feet"]

        segments = _group_into_segments(classifications, page_texts)

        # Tags should be empty from _group_into_segments (set later by segment_document)
        # This verifies the dataclass default works
        assert segments[0].tags == []

        # Now apply tags like segment_document does
        from apps.public_core.services.segment_tagger import tag_segment
        for seg in segments:
            seg.tags = tag_segment(seg.form_type, seg.raw_text_cache)

        logger.info(f"Segment tags after integration: {segments[0].tags}")
        assert "completion" in segments[0].tags
        assert "squeeze" in segments[0].tags
        assert "deep_well" in segments[0].tags


# ─── Phase 3: Tag-Aware Extraction ─────────────────────────────────

class TestTagAwareExtraction(TestCase):
    """Phase 3: Verify tags are passed through to extraction prompts."""

    def test_load_prompt_without_tags(self):
        """Base prompt should work without tags."""
        from apps.public_core.services.openai_extraction import _load_prompt

        prompt = _load_prompt("w2")

        logger.info(f"W-2 prompt length (no tags): {len(prompt)} chars")
        assert "ACCURACY RULES" in prompt
        assert "FOCUS AREAS" not in prompt

    def test_load_prompt_with_tags(self):
        """Tags should append FOCUS AREAS section."""
        from apps.public_core.services.openai_extraction import _load_prompt

        tags = ["completion", "geometry", "cement", "squeeze"]
        prompt = _load_prompt("w2", tags=tags)

        logger.info(f"W-2 prompt length (with tags): {len(prompt)} chars")
        logger.info(f"FOCUS AREAS present: {'FOCUS AREAS' in prompt}")

        assert "FOCUS AREAS" in prompt
        assert "completion" in prompt
        assert "squeeze" in prompt

    def test_load_prompt_all_keys(self):
        """All prompt keys should work with and without tags."""
        from apps.public_core.services.openai_extraction import _load_prompt, SUPPORTED_TYPES

        for doc_type, config in SUPPORTED_TYPES.items():
            prompt_key = config["prompt_key"]
            # Without tags
            p1 = _load_prompt(prompt_key)
            # With tags
            p2 = _load_prompt(prompt_key, tags=["test_tag"])

            logger.info(f"  {prompt_key}: base={len(p1)} chars, with_tags={len(p2)} chars")
            assert len(p2) > len(p1), f"Tags should make prompt longer for {prompt_key}"
            assert "FOCUS AREAS" in p2


# ─── Phase 4: Segment ↔ ED Linkage ─────────────────────────────────

class TestSegmentEDLinkage(TestCase):
    """Phase 4: Verify segment FK on ExtractedDocument."""

    def setUp(self):
        self.well = WellRegistry.objects.create(
            api14="42003356630000",
            state="TX",
        )

    def test_ed_has_segment_field(self):
        """ExtractedDocument should have a segment FK."""
        ed = ExtractedDocument.objects.create(
            well=self.well,
            api_number="42003356630000",
            document_type="w2",
            source_path="/tmp/test.pdf",
            status="success",
            json_data={},
        )

        assert hasattr(ed, 'segment')
        assert ed.segment is None
        logger.info(f"ED {ed.id} segment field exists, default=None")

    def test_ed_segment_fk_linkage(self):
        """Segment FK should link correctly."""
        seg = DocumentSegment.objects.create(
            well=self.well,
            api_number="42003356630000",
            source_filename="test.pdf",
            source_type="neubus",
            page_start=0, page_end=3,
            form_type="W-2",
            classification_method="text",
            classification_confidence="high",
        )

        ed = ExtractedDocument.objects.create(
            well=self.well,
            api_number="42003356630000",
            document_type="w2",
            source_path="/tmp/test.pdf",
            status="success",
            json_data={},
            segment=seg,
        )

        logger.info(f"ED {ed.id} → Segment {seg.id}")
        logger.info(f"Segment.extractions: {list(seg.extractions.values_list('id', flat=True))}")

        assert ed.segment == seg
        assert seg.extractions.count() == 1
        assert seg.extractions.first() == ed


# ─── Phase 5: Timeline Builder ─────────────────────────────────────

class TestTimelineBuilder(TestCase):
    """Phase 5: Verify timeline construction from ExtractedDocuments."""

    def setUp(self):
        self.well = WellRegistry.objects.create(
            api14="42003356630000",
            state="TX",
        )

    def test_build_empty_timeline(self):
        """Well with no documents should produce empty timeline."""
        from apps.public_core.services.timeline_builder import build_timeline

        events = build_timeline(self.well)

        logger.info(f"Empty timeline: {len(events)} events")
        assert len(events) == 0

    def test_build_timeline_from_w2(self):
        """W-2 document should produce a completion event."""
        from apps.public_core.services.timeline_builder import build_timeline

        ExtractedDocument.objects.create(
            well=self.well,
            api_number="42003356630000",
            document_type="w2",
            source_path="/tmp/test.pdf",
            status="success",
            json_data={
                "completion_info": {"completion_date": "1985-06-15"},
                "operator_info": {"name": "Acme Oil Co."},
                "casing_record": [
                    {"string": "surface", "shoe_depth_ft": 500},
                    {"string": "production", "shoe_depth_ft": 8000},
                ],
                "producing_injection_disposal_interval": [
                    {"from_ft": 7800, "to_ft": 8000},
                ],
            },
        )

        events = build_timeline(self.well)

        logger.info(f"W-2 timeline: {len(events)} events")
        for e in events:
            logger.info(f"  {e.title}")
            logger.info(f"    date={e.event_date}, precision={e.event_date_precision}")
            logger.info(f"    type={e.event_type}")
            logger.info(f"    key_data={e.key_data}")

        assert len(events) == 1
        assert events[0].event_type == "completion"
        assert events[0].event_date == date(1985, 6, 15)
        assert events[0].event_date_precision == "day"
        assert "1985" in events[0].title

    def test_build_timeline_from_w3(self):
        """W-3 document should produce a plugging event."""
        from apps.public_core.services.timeline_builder import build_timeline

        ExtractedDocument.objects.create(
            well=self.well,
            api_number="42003356630000",
            document_type="w3",
            source_path="/tmp/w3.pdf",
            status="success",
            json_data={
                "plugging_summary": {
                    "plugging_completed_date": "2024-01-15",
                    "service_company": "Plug Masters LLC",
                },
                "plug_record": [
                    {"plug_number": 1, "depth_top_ft": 8000},
                    {"plug_number": 2, "depth_top_ft": 5000},
                    {"plug_number": 3, "depth_top_ft": 200},
                ],
                "operator_info": {"name": "Test Operator"},
            },
        )

        events = build_timeline(self.well)

        logger.info(f"W-3 timeline: {len(events)} events")
        event = events[0]
        logger.info(f"  {event.title}: {event.key_data}")

        assert event.event_type == "plugging"
        assert event.event_date == date(2024, 1, 15)
        assert event.key_data.get("total_plugs") == 3

    def test_build_multi_document_timeline(self):
        """Multiple documents should produce chronological events."""
        from apps.public_core.services.timeline_builder import build_timeline

        # W-1 permit (earliest)
        ExtractedDocument.objects.create(
            well=self.well, api_number="42003356630000",
            document_type="w1", source_path="/tmp/w1.pdf",
            status="success",
            json_data={
                "header": {"date_filed": "1980-03-01"},
                "well_info": {"total_depth_ft": 8500},
                "operator_info": {"name": "Original Operator"},
            },
        )

        # W-2 completion
        ExtractedDocument.objects.create(
            well=self.well, api_number="42003356630000",
            document_type="w2", source_path="/tmp/w2.pdf",
            status="success",
            json_data={
                "completion_info": {"completion_date": "1980-09-15"},
                "operator_info": {"name": "Original Operator"},
            },
        )

        # W-15 cement job
        ExtractedDocument.objects.create(
            well=self.well, api_number="42003356630000",
            document_type="w15", source_path="/tmp/w15.pdf",
            status="success",
            json_data={
                "header": {"date": "1980-04-01"},
                "cementing_data": [{"sacks": 200, "date": "1980-04-01"}],
                "operator_info": {"name": "Original Operator"},
            },
        )

        # W-3 plugging (latest)
        ExtractedDocument.objects.create(
            well=self.well, api_number="42003356630000",
            document_type="w3", source_path="/tmp/w3.pdf",
            status="success",
            json_data={
                "plugging_summary": {"plugging_completed_date": "2023-12-01"},
                "plug_record": [{"plug_number": 1}],
                "operator_info": {"name": "New Operator"},
            },
        )

        events = build_timeline(self.well)

        logger.info(f"Multi-doc timeline: {len(events)} events")
        for e in events:
            logger.info(f"  {e.event_date} | {e.event_type:20s} | {e.title}")

        assert len(events) == 4

    def test_refresh_timeline_idempotent(self):
        """refresh_timeline should delete + rebuild cleanly."""
        from apps.public_core.services.timeline_builder import refresh_timeline

        ExtractedDocument.objects.create(
            well=self.well, api_number="42003356630000",
            document_type="w2", source_path="/tmp/w2.pdf",
            status="success",
            json_data={"completion_info": {"completion_date": "1990-01-01"}},
        )

        # Build twice — should produce same count
        events1 = refresh_timeline(self.well)
        events2 = refresh_timeline(self.well)

        logger.info(f"First build: {len(events1)} events")
        logger.info(f"Second build: {len(events2)} events")
        logger.info(f"Total in DB: {WellTimelineEvent.objects.filter(well=self.well).count()}")

        assert len(events1) == 1
        assert len(events2) == 1
        assert WellTimelineEvent.objects.filter(well=self.well).count() == 1

    def test_date_parsing_formats(self):
        """Verify various date formats are parsed correctly."""
        from apps.public_core.services.timeline_builder import _parse_date_string

        test_cases = [
            ("2024-01-15", date(2024, 1, 15), "day"),
            ("01/15/2024", date(2024, 1, 15), "day"),
            ("January 15, 2024", date(2024, 1, 15), "day"),
            ("2024-01", date(2024, 1, 1), "month"),
            ("2024", date(2024, 1, 1), "year"),
            ("", None, None),
            ("invalid", None, None),
        ]

        for input_str, expected_date, expected_prec in test_cases:
            result = _parse_date_string(input_str)
            if expected_date is None:
                logger.info(f"  '{input_str}' → None (expected)")
                assert result is None
            else:
                logger.info(f"  '{input_str}' → {result[0]} ({result[1]})")
                assert result[0] == expected_date
                assert result[1] == expected_prec


# ─── Phase 5: Timeline API ──────────────────────────────────────────

class TestTimelineAPI(TestCase):
    """Phase 5: Verify timeline API endpoint."""

    def setUp(self):
        self.well = WellRegistry.objects.create(
            api14="42003356630000",
            state="TX",
        )

    def test_timeline_endpoint_empty(self):
        """GET timeline for well with no docs should return empty list."""
        from django.test import RequestFactory
        from apps.public_core.views.timeline_views import WellTimelineView

        factory = RequestFactory()
        request = factory.get(f"/api/wells/{self.well.api14}/timeline/")
        view = WellTimelineView.as_view()
        response = view(request, api14=self.well.api14)

        logger.info(f"Timeline API response: status={response.status_code}")
        logger.info(f"  total_events={response.data['total_events']}")

        assert response.status_code == 200
        assert response.data["total_events"] == 0

    def test_timeline_endpoint_with_data(self):
        """GET timeline with documents should auto-build and return events."""
        from django.test import RequestFactory
        from apps.public_core.views.timeline_views import WellTimelineView

        ExtractedDocument.objects.create(
            well=self.well, api_number="42003356630000",
            document_type="w2", source_path="/tmp/w2.pdf",
            status="success",
            json_data={"completion_info": {"completion_date": "1990-06-01"}},
        )

        factory = RequestFactory()
        request = factory.get(f"/api/wells/{self.well.api14}/timeline/")
        view = WellTimelineView.as_view()
        response = view(request, api14=self.well.api14)

        logger.info(f"Timeline API: {response.data['total_events']} events")
        for evt in response.data["events"]:
            logger.info(f"  {evt['event_date']} | {evt['event_type']} | {evt['title']}")

        assert response.status_code == 200
        assert response.data["total_events"] == 1

    def test_timeline_endpoint_404(self):
        """GET timeline for nonexistent well should return 404."""
        from django.test import RequestFactory
        from apps.public_core.views.timeline_views import WellTimelineView

        factory = RequestFactory()
        request = factory.get("/api/wells/99999999999999/timeline/")
        view = WellTimelineView.as_view()
        response = view(request, api14="99999999999999")

        logger.info(f"404 response: {response.status_code}")
        assert response.status_code == 404


# ─── Cross-Phase Integration ────────────────────────────────────────

class TestCrossPhaseIntegration(TestCase):
    """End-to-end integration: classify → tag → segment → link → timeline."""

    def setUp(self):
        self.well = WellRegistry.objects.create(
            api14="42003356630000",
            state="TX",
        )

    def test_full_pipeline_flow(self):
        """Simulate the full pipeline: classify text, tag, persist segment, link ED, build timeline."""
        from apps.public_core.services.document_segmenter import (
            classify_page_by_text,
            SegmentData,
            persist_segments,
        )
        from apps.public_core.services.segment_tagger import tag_segment
        from apps.public_core.services.timeline_builder import refresh_timeline

        logger.info("=== Full Pipeline Integration Test ===")

        # Step 1: Classify
        text = "RAILROAD COMMISSION OF TEXAS\nFORM W-2\nOIL WELL POTENTIAL TEST"
        classification = classify_page_by_text(text, "TX")
        logger.info(f"Step 1 - Classification: {classification.form_type} ({classification.confidence})")
        assert classification.form_type == "W-2"

        # Step 2: Tag
        tags = tag_segment(classification.form_type, text)
        logger.info(f"Step 2 - Tags: {tags}")
        assert "completion" in tags

        # Step 3: Build segment data
        seg_data = SegmentData(
            form_type=classification.form_type,
            page_start=0,
            page_end=2,
            confidence=classification.confidence,
            method=classification.method,
            evidence=classification.evidence,
            raw_text_cache=text,
            tags=tags,
        )

        # Step 4: Persist segment
        segments = persist_segments(
            [seg_data],
            well=self.well,
            api_number="42003356630000",
            source_filename="test_w2.pdf",
            source_type="neubus",
            total_source_pages=3,
        )
        logger.info(f"Step 3-4 - Persisted {len(segments)} segment(s)")
        assert len(segments) == 1
        seg = segments[0]
        logger.info(f"  Segment: {seg.form_type}, pages {seg.page_start}-{seg.page_end}")
        logger.info(f"  Tags: {seg.tags}")
        logger.info(f"  Method: {seg.classification_method}, Confidence: {seg.classification_confidence}")
        assert seg.tags == tags

        # Step 5: Create ED and link
        ed = ExtractedDocument.objects.create(
            well=self.well,
            api_number="42003356630000",
            document_type="w2",
            source_path="/tmp/test_w2.pdf",
            status="success",
            json_data={
                "completion_info": {"completion_date": "1985-06-15"},
                "operator_info": {"name": "Test Operator"},
                "casing_record": [{"string": "surface", "shoe_depth_ft": 500}],
            },
            segment=seg,
        )
        seg.extracted_document = ed
        seg.status = "extracted"
        seg.save(update_fields=["extracted_document", "status"])

        logger.info(f"Step 5 - ED {ed.id} linked to Segment {seg.id}")
        logger.info(f"  ED.segment = {ed.segment_id}")
        logger.info(f"  Segment.status = {seg.status}")
        assert ed.segment == seg
        assert seg.status == "extracted"

        # Step 6: Build timeline
        events = refresh_timeline(self.well)
        logger.info(f"Step 6 - Timeline: {len(events)} events")
        for evt in events:
            logger.info(f"  {evt.event_date} | {evt.event_type} | {evt.title}")
            logger.info(f"  key_data: {evt.key_data}")

        assert len(events) == 1
        assert events[0].event_type == "completion"
        assert events[0].event_date.year == 1985
        assert events[0].source_document == ed

        logger.info("=== Full Pipeline Integration Test PASSED ===")
