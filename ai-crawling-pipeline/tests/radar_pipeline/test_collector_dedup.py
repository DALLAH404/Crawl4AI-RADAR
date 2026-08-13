"""Tests for the dedup-before-resolve behavior of the collect stage.

We mock the feed fetch (always returns two entries) and the Google News
resolver (spies on calls). The point is to confirm the legacy
"dedup by raw link first, resolve only the leftovers" order is restored —
the assertion that motivated this fix: every duplicate raw_link must
short-circuit the resolver.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from radar_pipeline.config import CollectSettings
from radar_pipeline.db import find_by_raw_link, put_article
from radar_pipeline.models import Article, Source


RSS_BODY = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>x</title>
<item><title>Bosch anuncia nova linha de pastilhas de freio</title>
<link>https://news.google.com/articles/EXISTING</link>
<description>Bosch amplia linha</description></item>
<item><title>Schaeffler investe R$ 200 mi em nova planta</title>
<link>https://news.google.com/articles/NEW</link>
<description>fabrica</description></item>
</channel></rss>"""


def _make_article(link: str, raw_link: str, title: str) -> Article:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Article(
        article_hash=f"hash-{title[:6]}",
        title_hash=f"th-{title[:6]}",
        published_at="2026-08-01",
        collected_at=now,
        category="auto",
        competitor_tag="Bosch",
        product_line="Eletrica",
        title=title,
        action_description="desc",
        summary_status="pending",
        event_type="Lancamento",
        alert_level="Alto",
        is_launch=1,
        image_url="",
        link=link,
        raw_link=raw_link,
        ingestion_batch_id="batch-x",
        source_id="t",
        source_name="t",
        feed_type="google_news_query",
    )


@pytest.mark.asyncio
async def test_known_raw_link_skips_resolver(store, monkeypatch):
    """Insert one article with raw_link = https://news.google.com/articles/EXISTING.

    Run a collect; the resolver spy should be called for the OTHER item
    only — never for the one we already saw.
    """
    put_article(store, _make_article(
        link="https://real.example.com/old",
        raw_link="https://news.google.com/articles/EXISTING",
        title="Bosch antiga linha amortecedor",
    ))

    s = Source(
        source_id="t", name="t", source_type="concorrente",
        tag="t", product_line="Geral", category="auto",
        feed_type="google_news_query", query_text="x",
    )

    # Mock the feed transport: always returns RSS_BODY.
    def feed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=RSS_BODY)

    feed_calls: list[str] = []

    from radar_pipeline.sources import feedparser as fp
    from radar_pipeline.sources import collector as coll
    from radar_pipeline.sources import gnews as gnews_mod

    async def spy_resolve(url, client=None, *, limiter=None):
        feed_calls.append(url)
        return "https://real.example.com/resolved-" + url.rsplit("/", 1)[-1]

    monkeypatch.setattr(gnews_mod, "resolve_google_news_url", spy_resolve)
    # collector.py imports the symbol at the top of its module, so the
    # reference inside coll is the original — patch the bound name too.
    monkeypatch.setattr(coll, "resolve_google_news_url", spy_resolve)

    cfg = CollectSettings(
        concurrency=1,
        max_items_per_feed=10,
        backfill_max_items=100,
        days_back=30,
        backfill_days=90,
        gnews_concurrency=1,
        request_delay_seconds=0.0,
        request_jitter_seconds=0.0,
        block_cooldown_seconds=60.0,
        fetch_max_retries=0,
        fetch_backoff_seconds=0.0,
    )

    transport = httpx.MockTransport(feed_handler)

    # Monkey-patch _process_source's httpx.AsyncClient creation by
    # patching the feedparser to use our transport.
    class _TransportClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(coll.httpx, "AsyncClient", _TransportClient)
    monkeypatch.setattr(gnews_mod.httpx, "AsyncClient", _TransportClient)
    monkeypatch.setattr(fp.httpx, "AsyncClient", _TransportClient)

    stats = await coll.collect_once(store, [s], cfg, mode="normal")

    # Crucial assertion: resolver was NOT called for the EXISTING item
    # because find_by_raw_link hit the store first.
    assert "https://news.google.com/articles/EXISTING" not in feed_calls, (
        feed_calls
    )
    assert "https://news.google.com/articles/NEW" in feed_calls

    # And the new article was inserted.
    row = find_by_raw_link(store, "https://news.google.com/articles/NEW")
    assert row is not None
    assert row["link"] == "https://real.example.com/resolved-NEW"
