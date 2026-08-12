"""Detect blocking and apply retry-with-rotation to crawl4ai calls."""

from __future__ import annotations

import asyncio
import random
from typing import Any

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.models import CrawlResult

from ai_crawling_pipeline.config import AntiBlockSettings, Target


def pick_user_agent(settings: AntiBlockSettings, attempt: int) -> str | None:
    """Return a user-agent string for the given attempt, or None to use the browser default."""
    if not settings.rotate_user_agent or not settings.user_agents:
        return None
    return settings.user_agents[attempt % len(settings.user_agents)]


def is_blocked(result: CrawlResult | None, settings: AntiBlockSettings, exc: BaseException | None = None) -> bool:
    """Return True if the result (or raised exception) indicates blocking/transient failure."""
    if exc is not None:
        msg = str(exc).lower()
        transient_markers = (
            "timeout", "net::", "err_", "connection reset",
            "connection aborted", "temporarily", "forbidden",
        )
        return any(m in msg for m in transient_markers)
    if result is None:
        return True

    status = getattr(result, "status_code", None)
    if status is not None and status in settings.block_status_codes:
        return True

    if not result.success and result.error_message:
        msg = result.error_message.lower()
        if any(m in msg for m in ("timeout", "net::", "err_", "connection reset", "forbidden")):
            return True

    md_len = len((result.markdown or "").strip())
    html_lower = (result.html or "").lower()
    md_lower = (result.markdown or "").lower()
    indicators_lower = [i.lower() for i in settings.block_indicators]

    indicator_hit = any(ind in html_lower or ind in md_lower for ind in indicators_lower)
    if indicator_hit:
        if not result.success:
            return True
        if (status is None or 200 <= status < 400) and md_len < settings.min_content_chars:
            return True

    if (
        result.success
        and md_len < settings.min_content_chars
        and (status is None or 200 <= status < 400)
    ):
        return True

    return False


def build_attempt_config(
    base: dict[str, Any],
    target: Target,
    settings: AntiBlockSettings,
    attempt: int,
) -> CrawlerRunConfig:
    """Build a CrawlerRunConfig for the given attempt, with anti-block tweaks layered in."""
    merged: dict[str, Any] = {}
    merged.update(base)
    merged.update(target.run_kwargs)
    if target.wait_for is not None and "wait_for" not in merged:
        merged["wait_for"] = target.wait_for
    if target.js_code is not None and "js_code" not in merged:
        merged["js_code"] = target.js_code
    if target.session_id is not None and "session_id" not in merged:
        merged["session_id"] = target.session_id

    if settings.enabled:
        if settings.magic and "magic" not in merged:
            merged["magic"] = True
        ua = pick_user_agent(settings, attempt)
        if ua and "user_agent" not in merged:
            merged["user_agent"] = ua

    return CrawlerRunConfig(**merged)


async def crawl_with_retry(
    crawler: AsyncWebCrawler,
    target: Target,
    base_run_config: dict[str, Any],
    settings: AntiBlockSettings | None,
) -> tuple[CrawlResult | None, BaseException | None, int, list[str]]:
    """Run a single target with retry+backoff+UA-rotation when blocking is detected.

    Returns (result, exception, attempts_used, log_lines).
    On success, `result` is set and `exception` is None. On failure, opposite
    (i.e. a terminal blocked result is reported as a RuntimeError, not a
    success-shaped return).
    """
    settings = settings or AntiBlockSettings(enabled=False)
    log: list[str] = []
    max_attempts = max(1, settings.max_retries if settings.enabled else 1)

    for attempt in range(1, max_attempts + 1):
        run_config = build_attempt_config(base_run_config, target, settings, attempt - 1)
        ua = pick_user_agent(settings, attempt - 1) if settings.enabled else None
        prefix = f"   attempt {attempt}/{max_attempts}"
        suffix_bits = []
        if ua:
            suffix_bits.append(f"ua={ua[:30]}...")
        if settings.enabled and settings.magic:
            suffix_bits.append("magic=on")
        if settings.enabled and settings.rotate_user_agent:
            suffix_bits.append("rotate=on")
        suffix = f" ({', '.join(suffix_bits)})" if suffix_bits else ""
        print(f"{prefix}{suffix}")

        try:
            result = await crawler.arun(url=target.url, config=run_config)
        except Exception as exc:  # noqa: BLE001
            log.append(f"attempt {attempt} raised {type(exc).__name__}: {exc}")
            if not settings.enabled or not is_blocked(None, settings, exc):
                print(f"   FAILED (non-retryable): {exc}")
                return None, exc, attempt, log
            if attempt < max_attempts:
                delay = settings.backoff_seconds * (2 ** (attempt - 1)) + random.uniform(0, settings.jitter_seconds)
                print(f"   blocked/transient, retrying in {delay:.1f}s")
                await asyncio.sleep(delay)
                continue
            print(f"   FAILED after {attempt} attempts: {exc}")
            return None, exc, attempt, log

        if not is_blocked(result, settings):
            return result, None, attempt, log

        status = getattr(result, "status_code", None)
        reason = (
            f"blocked: status={status}" if status in settings.block_status_codes
            else "blocked: detector"
        )
        log.append(f"attempt {attempt} {reason}")
        if attempt < max_attempts:
            delay = settings.backoff_seconds * (2 ** (attempt - 1)) + random.uniform(0, settings.jitter_seconds)
            print(f"   {reason}, retrying in {delay:.1f}s")
            await asyncio.sleep(delay)
            continue
        print(f"   FAILED after {attempt} attempts: {reason}")
        return None, RuntimeError(f"blocked after {attempt} attempts: {reason}"), attempt, log
