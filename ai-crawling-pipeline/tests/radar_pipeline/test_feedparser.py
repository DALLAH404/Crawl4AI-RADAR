"""Tests for radar_pipeline.sources.feedparser.

These tests use httpx.MockTransport to exercise the retry / backoff /
503-recovery path without touching the network. The two failure modes
that matter most are:

* retry-after-503 -> 200 (must recover, not raise),
* persistent 503 (must raise FeedFetchError with the status preserved),
* non-retryable 404 (must raise immediately, no retries).
"""

from __future__ import annotations

import httpx
import pytest

from radar_pipeline.sources.feedparser import (
    FeedFetchError,
    fetch_and_parse,
    strip_html,
)

RSS_BODY = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>x</title>
<item><title>Bosch announces new line of brake pads</title>
<link>https://example.com/a1</link>
<description>short</description></item>
</channel></rss>"""


@pytest.mark.asyncio
async def test_503_then_200_recovers():
    seen = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["n"] += 1
        if seen["n"] == 1:
            return httpx.Response(503, text="blocked")
        return httpx.Response(200, content=RSS_BODY)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        items = await fetch_and_parse(
            "https://example.com/feed",
            client,
            max_retries=2,
            backoff_seconds=0.01,
            max_backoff_seconds=0.05,
        )

    assert len(items) == 1
    assert seen["n"] == 2


@pytest.mark.asyncio
async def test_persistent_503_raises_with_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="blocked")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(FeedFetchError) as ei:
            await fetch_and_parse(
                "https://example.com/feed",
                client,
                max_retries=2,
                backoff_seconds=0.01,
                max_backoff_seconds=0.05,
            )
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_404_does_not_retry():
    seen = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["n"] += 1
        return httpx.Response(404, text="no")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(FeedFetchError) as ei:
            await fetch_and_parse(
                "https://example.com/feed",
                client,
                max_retries=3,
                backoff_seconds=0.01,
            )
    assert ei.value.status_code == 404
    assert seen["n"] == 1, "404 must not be retried"


@pytest.mark.asyncio
async def test_honors_retry_after_header():
    seen = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["n"] += 1
        if seen["n"] < 2:
            return httpx.Response(
                503, text="blocked", headers={"retry-after": "0"},
            )
        return httpx.Response(200, content=RSS_BODY)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        items = await fetch_and_parse(
            "https://example.com/feed",
            client,
            max_retries=3,
            backoff_seconds=0.01,
        )
    assert len(items) == 1


@pytest.mark.asyncio
async def test_200_empty_feed_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<?xml version='1.0'?><rss></rss>")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        items = await fetch_and_parse("https://example.com/feed", client)
    assert items == []


@pytest.mark.asyncio
async def test_blocked_limiter_raises_feed_fetch_error():
    from radar_pipeline.sources.ratelimit import RateLimiter

    limiter = RateLimiter()
    limiter.record_failure("https://blocked.example.com/feed", status=503)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=RSS_BODY)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(FeedFetchError):
            await fetch_and_parse(
                "https://blocked.example.com/feed",
                client,
                limiter=limiter,
            )


class TestStripHtml:
    def test_strips_tags_and_entities(self):
        assert strip_html("<p>Bosch &amp; Schaeffler<em>lança</em></p>") == \
               "Bosch & Schaeffler lança"
