"""Tests for apps.public_core.services.text_processing."""
import json
import pytest
from apps.public_core.services.text_processing import json_to_prose, chunk_text


class TestJsonToProse:
    """Test JSON-to-prose conversion."""

    def test_casing_array_formats_prose(self):
        """Casing records produce readable prose using actual field keys."""
        data = json.dumps([
            {
                "casing_type": "Surface",
                "diameter": "9-5/8",
                "weight": "36",
                "bottom": "2500",
                "top": "0",
                "sacks": "150",
            },
            {
                "casing_type": "Production",
                "diameter": "5-1/2",
                "weight": "17",
                "shoe_depth_ft": "8000",
                "top": "0",
                "sacks": "400",
            },
        ])
        result = json_to_prose("casing_record", data)
        # Both diameters should appear in the output
        assert "9-5/8" in result
        assert "5-1/2" in result
        # Casing type labels should appear
        assert "Surface" in result
        assert "Production" in result
        # Should not be raw JSON anymore
        assert not result.startswith("[")

    def test_casing_array_uses_shoe_depth_fallback(self):
        """shoe_depth_ft is used when bottom is absent."""
        data = json.dumps([
            {
                "casing_type": "Intermediate",
                "diameter": "7",
                "weight": "23",
                "shoe_depth_ft": "5500",
                "top": "100",
                "sacks": "200",
            },
        ])
        result = json_to_prose("casing_record", data)
        assert "5500" in result
        assert "Intermediate" in result

    def test_casing_empty_array_falls_back_to_raw_json(self):
        """Empty casing array falls back to the original JSON text."""
        raw = json.dumps([])
        result = json_to_prose("casing_record", raw)
        # No lines produced, returns original json_text
        assert result == raw

    def test_well_info_dict_builds_prose(self):
        """well_info dict sections produce readable comma-separated prose."""
        data = json.dumps({
            "field_name": "Permian Basin",
            "county": "Lea",
            "operator": "Acme Oil",
            "api": "30-015-28692",
        })
        result = json_to_prose("well_info", data)
        assert "Permian Basin" in result
        assert "Lea" in result
        assert "Acme Oil" in result
        assert "30-015-28692" in result
        # Not raw JSON
        assert not result.startswith("{")

    def test_well_info_uses_well_no(self):
        """well_no and operator_name are also handled."""
        data = json.dumps({
            "well_no": "42-A",
            "operator_name": "Pioneer Resources",
        })
        result = json_to_prose("well_info", data)
        assert "42-A" in result
        assert "Pioneer Resources" in result

    def test_well_info_empty_dict_returns_raw(self):
        """A well_info dict with no recognized keys returns raw JSON."""
        data = json.dumps({"unknown_key": "value"})
        result = json_to_prose("well_info", data)
        # parts list will be empty, so falls back to json_text
        assert result == data

    def test_description_dict_returns_work_description(self):
        """description sections extract work_description field."""
        data = json.dumps({
            "work_description": "Recomplete to a shallower zone.",
            "purpose": "Production enhancement",
        })
        result = json_to_prose("description", data)
        assert "Recomplete to a shallower zone." in result
        assert "Production enhancement" in result

    def test_description_falls_back_to_description_key(self):
        """description dict without work_description uses description key."""
        data = json.dumps({"description": "Install downhole pump."})
        result = json_to_prose("description", data)
        assert "Install downhole pump." in result

    def test_description_empty_dict_returns_raw(self):
        """description dict with no description fields returns raw JSON."""
        data = json.dumps({"other": "data"})
        result = json_to_prose("description", data)
        assert result == data

    def test_notice_dict_formats_type_and_description(self):
        """Notice sections produce 'Notice type: X. Y' format."""
        data = json.dumps({
            "type": "Violation",
            "description": "Pressure test failure.",
        })
        result = json_to_prose("notice_type", data)
        assert "Violation" in result
        assert "Pressure test failure." in result

    def test_notice_dict_no_type_returns_raw(self):
        """Notice without a type field returns raw JSON."""
        data = json.dumps({"description": "Something happened."})
        result = json_to_prose("notice", data)
        assert result == data

    def test_plain_text_passthrough(self):
        """Non-JSON text passes through unchanged regardless of section name."""
        text = "This is just plain text with no JSON."
        result = json_to_prose("description", text)
        assert result == text

    def test_empty_string_returns_empty(self):
        """Empty string input returns empty string."""
        result = json_to_prose("section", "")
        assert result == ""

    def test_unrecognized_section_returns_raw_json(self):
        """A section name not matching any branch returns the original JSON text."""
        data = json.dumps({"key": "value"})
        result = json_to_prose("some_unrecognized_section", data)
        assert result == data

    def test_unrecognized_section_with_list_returns_raw_json(self):
        """A list-valued unrecognized section returns the original JSON text."""
        data = json.dumps([1, 2, 3])
        result = json_to_prose("other_section", data)
        assert result == data


class TestChunkText:
    """Test text chunking logic."""

    def test_short_text_returns_single_chunk(self):
        """Text shorter than max_chars is returned as-is in a one-element list."""
        text = "Short text"
        chunks = chunk_text(text, max_chars=500)
        assert chunks == [text]

    def test_text_equal_to_max_chars_returns_single_chunk(self):
        """Text exactly at max_chars boundary returns a single chunk."""
        text = "a" * 500
        chunks = chunk_text(text, max_chars=500)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_produces_multiple_chunks(self):
        """Long text is split into multiple chunks."""
        # 50 repetitions of ~20 chars = ~1000 chars total
        text = "This is a sentence. " * 50
        chunks = chunk_text(text, max_chars=200, overlap=50)
        assert len(chunks) > 1

    def test_chunks_respect_max_chars_with_tolerance(self):
        """Each chunk is at or near max_chars (sentence-break tolerance allowed)."""
        text = "This is a sentence. " * 50
        chunks = chunk_text(text, max_chars=200, overlap=50)
        # Allow ~50% over to account for sentence-boundary search range
        for chunk in chunks:
            assert len(chunk) <= 300

    def test_overlap_preserves_context(self):
        """Consecutive chunks share overlapping text."""
        # Produce clearly separable words spread over a long string
        text = "Alpha. Bravo. Charlie. Delta. Echo. Foxtrot. Golf. Hotel. India. Juliet. " * 5
        chunks = chunk_text(text, max_chars=100, overlap=30)
        if len(chunks) >= 2:
            end_of_first = chunks[0][-30:]
            start_of_second = chunks[1][:60]
            # At least one word from the overlap window appears in chunk 2's opening
            assert any(word in start_of_second for word in end_of_first.split())

    def test_no_empty_chunks(self):
        """Chunking never produces whitespace-only strings."""
        text = "Word. " * 200
        chunks = chunk_text(text, max_chars=100, overlap=20)
        for chunk in chunks:
            assert chunk.strip() != ""

    def test_empty_text_returns_at_most_one_element(self):
        """Empty text returns either [] or [''] — never more than one element."""
        result = chunk_text("", max_chars=500)
        assert len(result) <= 1

    def test_empty_text_no_non_empty_chunks(self):
        """If empty text produces a chunk, that chunk is also empty (or absent)."""
        result = chunk_text("", max_chars=500)
        # filter(None, result) should be empty
        assert not any(c.strip() for c in result)

    def test_all_content_preserved(self):
        """No text from the original is silently dropped across all chunks."""
        # Use unique tokens we can track
        tokens = [f"TOKEN{i}" for i in range(20)]
        text = ". ".join(tokens) + "."
        chunks = chunk_text(text, max_chars=60, overlap=15)
        combined = " ".join(chunks)
        for token in tokens:
            assert token in combined

    def test_single_word_longer_than_max_chars(self):
        """A single token longer than max_chars is returned as a single hard chunk."""
        long_word = "x" * 600
        chunks = chunk_text(long_word, max_chars=200, overlap=50)
        # The whole word must appear across chunks (hard-break case)
        combined = "".join(chunks)
        assert long_word in combined

    def test_custom_overlap_default_params(self):
        """Default params (max_chars=500, overlap=100) work without keyword args."""
        text = "Sentence one. " * 80
        chunks = chunk_text(text)
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.strip() != ""
