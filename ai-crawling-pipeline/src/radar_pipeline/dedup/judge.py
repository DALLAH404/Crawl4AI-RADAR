"""OpenAI-compatible LLM judge for ambiguous cosine cases (layer 4)."""

from __future__ import annotations

import logging
import os
import re

from openai import AsyncOpenAI

from radar_pipeline.config import LLMSettings

logger = logging.getLogger(__name__)

VALID_VERDICTS = {"same", "different", "related"}


async def judge(
    title_a: str,
    title_b: str,
    excerpt_a: str,
    excerpt_b: str,
    llm_settings: LLMSettings,
    system_prompt: str,
) -> str:
    client = AsyncOpenAI(
        base_url=llm_settings.base_url,
        api_key=os.environ.get(llm_settings.api_key_env) or "",
    )

    prompt_user = (
        f"Item A title: {title_a}\n"
        f"Item A excerpt: {(excerpt_a or '')[:400]}\n\n"
        f"Item B title: {title_b}\n"
        f"Item B excerpt: {(excerpt_b or '')[:400]}\n\n"
        "Respond with exactly one of: same, different, related."
    )

    try:
        resp = await client.chat.completions.create(
            model=llm_settings.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_user},
            ],
            temperature=llm_settings.temperature,
            max_tokens=llm_settings.max_tokens,
        )
    except Exception as exc:
        logger.warning("LLM judge failed, treating as different: %s", exc)
        return "different"

    if not resp.choices:
        return "different"

    text = (resp.choices[0].message.content or "").strip().lower()
    cleaned = re.sub(r"[^a-z]+", " ", text).strip()
    for word in cleaned.split():
        if word in VALID_VERDICTS:
            return word
    return "different"
