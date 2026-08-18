"""Tests for the strict LLM-response schema in radar_pipeline.summarize.schema."""
from __future__ import annotations

import pytest

from radar_pipeline.summarize.schema import (
    ALERT_LEVELS,
    DEFAULT_ALERT_LEVEL,
    DEFAULT_EVENT_TYPE,
    EVENT_TYPES,
    SchemaError,
    coerce_relevant,
    normalize_summary_dict,
    parse_llm_json,
    validate_alert_level,
    validate_event_type,
)


class TestParseLlmJson:
    def test_pure_json(self):
        d = parse_llm_json('{"summary": "x", "relevant": true}')
        assert d == {"summary": "x", "relevant": True}

    def test_json_inside_prose(self):
        d = parse_llm_json('Here is the answer:\n{"summary": "x"}\nThanks!')
        assert d == {"summary": "x"}

    def test_json_inside_markdown_fence(self):
        d = parse_llm_json('```json\n{"summary": "x", "event_type": "Preco"}\n```')
        assert d == {"summary": "x", "event_type": "Preco"}

    def test_empty(self):
        assert parse_llm_json("") is None
        assert parse_llm_json("   ") is None

    def test_non_json_returns_none(self):
        assert parse_llm_json("the article is about brakes") is None

    def test_json_array_returns_none(self):
        assert parse_llm_json('["a", "b"]') is None

    def test_json_string_returns_none(self):
        assert parse_llm_json('"just a string"') is None


class TestEventTypes:
    @pytest.mark.parametrize("value", list(EVENT_TYPES))
    def test_valid_values_pass_through(self, value):
        assert validate_event_type(value) == value

    def test_invalid_falls_back_to_default(self):
        assert validate_event_type("Lançamento") == DEFAULT_EVENT_TYPE  # accented
        assert validate_event_type("launch") == DEFAULT_EVENT_TYPE
        assert validate_event_type("") == DEFAULT_EVENT_TYPE
        assert validate_event_type(None) == DEFAULT_EVENT_TYPE


class TestAlertLevels:
    @pytest.mark.parametrize("value", list(ALERT_LEVELS))
    def test_valid_values_pass_through(self, value):
        assert validate_alert_level(value) == value

    def test_invalid_falls_back_to_default(self):
        assert validate_alert_level("high") == DEFAULT_ALERT_LEVEL
        assert validate_alert_level("") == DEFAULT_ALERT_LEVEL
        assert validate_alert_level(None) == DEFAULT_ALERT_LEVEL


class TestCoerceRelevant:
    def test_bool_passthrough(self):
        assert coerce_relevant(True) is True
        assert coerce_relevant(False) is False

    def test_string_true(self):
        assert coerce_relevant("true") is True
        assert coerce_relevant("TRUE") is True
        assert coerce_relevant("Yes") is True
        assert coerce_relevant("1") is True

    def test_string_false(self):
        # the historical bug: string "false" was truthy. Now must be False.
        assert coerce_relevant("false") is False
        assert coerce_relevant("FALSE") is False
        assert coerce_relevant("no") is False
        assert coerce_relevant("0") is False

    def test_garbage_defaults_true(self):
        assert coerce_relevant("maybe") is True
        assert coerce_relevant(None) is True
        assert coerce_relevant(["false"]) is True


class TestNormalizeSummaryDict:
    def _full_payload(self, **overrides):
        payload = {
            "summary": "Bosch launches new brake pads.",
            "competitor_analysis": "Pressure on aftermarket pricing.",
            "event_type": "Lancamento",
            "alert_level": "Alto",
            "relevant": True,
        }
        payload.update(overrides)
        return payload

    def test_full_payload(self):
        out = normalize_summary_dict(self._full_payload())
        assert out == {
            "summary": "Bosch launches new brake pads.",
            "competitor_analysis": "Pressure on aftermarket pricing.",
            "title": "",
            "event_type": "Lancamento",
            "alert_level": "Alto",
            "relevant": True,
        }

    def test_missing_summary_raises(self):
        payload = self._full_payload(summary="")
        with pytest.raises(SchemaError):
            normalize_summary_dict(payload)

    def test_missing_summary_key_raises(self):
        payload = {"competitor_analysis": "...", "relevant": True}
        with pytest.raises(SchemaError):
            normalize_summary_dict(payload)

    def test_string_false_relevant_coerced(self):
        out = normalize_summary_dict(self._full_payload(relevant="false"))
        assert out["relevant"] is False

    def test_invalid_event_type_normalized(self):
        out = normalize_summary_dict(self._full_payload(event_type="Lançamento"))
        assert out["event_type"] == DEFAULT_EVENT_TYPE

    def test_invalid_alert_level_normalized(self):
        out = normalize_summary_dict(self._full_payload(alert_level="high"))
        assert out["alert_level"] == DEFAULT_ALERT_LEVEL

    def test_summary_truncated(self):
        out = normalize_summary_dict(self._full_payload(summary="x" * 2000))
        assert len(out["summary"]) == 900

    def test_competitor_analysis_truncated(self):
        out = normalize_summary_dict(
            self._full_payload(competitor_analysis="y" * 2000)
        )
        assert len(out["competitor_analysis"]) == 500

    def test_competitor_analysis_missing_becomes_empty(self):
        out = normalize_summary_dict(
            {"summary": "ok", "relevant": True}
        )
        assert out["competitor_analysis"] == ""

    def test_non_string_competitor_analysis_becomes_empty(self):
        out = normalize_summary_dict(
            self._full_payload(competitor_analysis=["a", "b"])
        )
        assert out["competitor_analysis"] == ""

    def test_title_passes_through(self):
        out = normalize_summary_dict(self._full_payload(title="Bosch launches brake pads for aftermarket"))
        assert out["title"] == "Bosch launches brake pads for aftermarket"

    def test_title_truncated(self):
        out = normalize_summary_dict(self._full_payload(title="x" * 200))
        assert len(out["title"]) == 120

    def test_title_missing_becomes_empty(self):
        out = normalize_summary_dict(self._full_payload())
        assert out["title"] == ""

    def test_blank_title_becomes_empty(self):
        out = normalize_summary_dict(self._full_payload(title="   "))
        assert out["title"] == ""

    def test_non_string_title_becomes_empty(self):
        out = normalize_summary_dict(self._full_payload(title=["a", "b"]))
        assert out["title"] == ""