"""Async OpenAI-compatible client wrapper for summarization."""

from __future__ import annotations

import asyncio
import logging
import os

from openai import APIError, APIConnectionError, AsyncOpenAI

from radar_pipeline.config import LLMSettings
from radar_pipeline.models import SummarizeResult
from radar_pipeline.summarize.schema import (
    SchemaError,
    normalize_summary_dict,
    parse_llm_json,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BASE_DELAY = 1.0


async def summarize_one(
    title: str,
    content: str,
    llm_settings: LLMSettings,
    system_prompt: str,
    max_chars: int = 20000,
) -> SummarizeResult:
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n[...truncated...]"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Title: {title}\n\nContent:\n{content}"},
    ]

    client = AsyncOpenAI(
        base_url=llm_settings.base_url,
        api_key=os.environ.get(llm_settings.api_key_env) or "",
    )

    last_error = ""
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.chat.completions.create(
                model=llm_settings.model,
                messages=messages,
                temperature=llm_settings.temperature,
                max_tokens=llm_settings.max_tokens,
                response_format={"type": "json_object"},
            )
        except (APIError, APIConnectionError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < MAX_RETRIES - 1:
                delay = BASE_DELAY * (2**attempt)
                await asyncio.sleep(delay)
                continue
            return SummarizeResult(ok=False, error=last_error)
        except Exception as exc:
            return SummarizeResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        if not resp.choices:
            return SummarizeResult(ok=False, error="empty choices in LLM response")

        raw = (resp.choices[0].message.content or "").strip()

        data = parse_llm_json(raw)
        if data is None:
            return SummarizeResult(
                ok=False,
                error="LLM did not return valid JSON",
            )

        try:
            norm = normalize_summary_dict(data)
        except SchemaError as exc:
            return SummarizeResult(ok=False, error=str(exc))

        return SummarizeResult(
            ok=True,
            summary=norm["summary"],
            competitor_analysis=norm["competitor_analysis"],
            title=norm["title"],
            event_type=norm["event_type"],
            alert_level=norm["alert_level"],
            relevant=norm["relevant"],
        )

    return SummarizeResult(ok=False, error=last_error)
