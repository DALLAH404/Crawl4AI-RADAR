"""Tests for summarize.client — strict JSON output contract.

Uses a fake AI4ALLLiteLlm client (no network) by monkeypatching the
``AI4ALLLiteLlm`` symbol inside ``radar_pipeline.summarize.client``.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from radar_pipeline.config import LLMSettings
from radar_pipeline.summarize.client import summarize_one
from radar_pipeline.summarize.prompts import DEFAULT_SYSTEM_PROMPT

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_client_cache():
    from radar_pipeline.summarize import client as client_mod

    client_mod._clients.clear()
    yield
    client_mod._clients.clear()


def _llm_settings() -> LLMSettings:
    return LLMSettings(
        base_url="https://example.invalid/v1",
        model="test-model",
        api_key_env="OPENAI_API_KEY",
    )


def _fake_resp(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class _FakeClient:
    """Records kwargs passed to ``acomplete`` and returns a queued response."""

    def __init__(self, next_content: str):
        self._next = next_content
        self.calls: list[dict] = []

    async def acomplete(self, messages, **kwargs):
        self.calls.append(kwargs)
        return _fake_resp(self._next)


def _patch_client(next_content: str):
    fake_client = _FakeClient(next_content)
    return patch(
        "radar_pipeline.summarize.client.AI4ALLLiteLlm",
        return_value=fake_client,
    ), fake_client


VALID_PAYLOAD = (
    '{"summary": "Bosch launches brake pads.", '
    '"competitor_analysis": "Pressure on prices.", '
    '"title": "Bosch launches new brake pad line", '
    '"event_type": "Lancamento", '
    '"alert_level": "Alto", '
    '"relevant": true}'
)


async def _call(next_content: str):
    patcher, fake = _patch_client(next_content)
    with patcher:
        result = await summarize_one(
            title="t",
            content="c",
            llm_settings=_llm_settings(),
            system_prompt=DEFAULT_SYSTEM_PROMPT,
        )
    return result, fake


class TestSummarizeOneHappyPath:
    async def test_valid_json_returns_structured_result(self):
        result, _ = await _call(VALID_PAYLOAD)
        assert result.ok is True
        assert result.summary == "Bosch launches brake pads."
        assert result.competitor_analysis == "Pressure on prices."
        assert result.title == "Bosch launches new brake pad line"
        assert result.event_type == "Lancamento"
        assert result.alert_level == "Alto"
        assert result.relevant is True
        assert result.error == ""

    async def test_response_format_json_object_passed(self):
        _, fake = await _call(VALID_PAYLOAD)
        assert fake.calls, "create() was never called"
        assert fake.calls[0].get("response_format") == {"type": "json_object"}


class TestSummarizeOneFailStop:
    async def test_non_json_marks_failed(self):
        result, _ = await _call("the article is about brakes, not JSON")
        assert result.ok is False
        assert "valid JSON" in result.error
        # The historical bug: this would have set ok=True and stored raw[:900].
        assert result.summary == ""

    async def test_missing_summary_marks_failed(self):
        result, _ = await _call('{"competitor_analysis": "x", "relevant": true}')
        assert result.ok is False
        assert "summary" in result.error.lower()

    async def test_empty_choices_marks_failed(self):
        class _EmptyChoicesClient:
            async def acomplete(self, messages, **kwargs):  # noqa: ARG002
                return SimpleNamespace(choices=[])

        with patch(
            "radar_pipeline.summarize.client.AI4ALLLiteLlm",
            return_value=_EmptyChoicesClient(),
        ):
            result = await summarize_one(
                title="t",
                content="c",
                llm_settings=_llm_settings(),
                system_prompt=DEFAULT_SYSTEM_PROMPT,
            )
        assert result.ok is False
        assert "empty choices" in result.error


class TestSummarizeOneCoercions:
    async def test_string_false_relevant_is_false(self):
        payload = (
            '{"summary": "x", "competitor_analysis": "", '
            '"event_type": "Atualizacao", "alert_level": "Baixo", '
            '"relevant": "false"}'
        )
        result, _ = await _call(payload)
        assert result.ok is True
        assert result.relevant is False  # would have been True historically

    async def test_invalid_event_type_normalized(self):
        payload = (
            '{"summary": "x", "competitor_analysis": "", '
            '"event_type": "Lançamento", "alert_level": "Baixo", '
            '"relevant": true}'
        )
        result, _ = await _call(payload)
        assert result.ok is True
        assert result.event_type == "Atualizacao"

    async def test_invalid_alert_level_normalized(self):
        payload = (
            '{"summary": "x", "competitor_analysis": "", '
            '"event_type": "Preco", "alert_level": "high", '
            '"relevant": true}'
        )
        result, _ = await _call(payload)
        assert result.ok is True
        assert result.alert_level == "Baixo"

    async def test_json_inside_markdown_fence_works(self):
        result, _ = await _call("```json\n" + VALID_PAYLOAD + "\n```")
        assert result.ok is True
        assert result.summary == "Bosch launches brake pads."

    async def test_missing_title_defaults_to_empty(self):
        payload = (
            '{"summary": "x", "competitor_analysis": "", '
            '"event_type": "Atualizacao", "alert_level": "Baixo", '
            '"relevant": true}'
        )
        result, _ = await _call(payload)
        assert result.ok is True
        assert result.title == ""