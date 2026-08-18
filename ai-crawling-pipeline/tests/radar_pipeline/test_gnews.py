"""Offline tests for the decoder-backed Google News collector."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from radar_pipeline.sources import gnews as gnews_module
from radar_pipeline.sources.gnews import GNewsCollector, GNewsConfig, GNewsItem


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
  <title>Bosch lança nova linha de pastilhas de freio - AutoData</title>
  <link>https://news.google.com/rss/articles/FAKE1?oc=5</link>
  <pubDate>Mon, 17 Aug 2026 14:30:00 GMT</pubDate>
  <source>AutoData</source>
  <description>&lt;p&gt;A Bosch anunciou uma nova linha.&lt;/p&gt;</description>
</item>
<item>
  <title>ZF amplia portfólio - Automotive Business</title>
  <link>https://news.google.com/rss/articles/FAKE2?oc=5</link>
  <pubDate>Sun, 16 Aug 2026 09:15:00 GMT</pubDate>
  <source>Automotive Business</source>
  <description>A ZF ampliou o portfólio.</description>
</item>
<item>
  <title>Link direto</title>
  <link>https://example.com/article</link>
  <pubDate>Sat, 15 Aug 2026 08:00:00 GMT</pubDate>
  <description>Conteúdo.</description>
</item>
</channel>
</rss>"""


def test_search_url_and_feed_parsing():
    collector = GNewsCollector()

    url = collector.build_search_url("Bosch lançamento", when_days=7)
    assert url.startswith("https://news.google.com/rss/search?q=")
    assert "when%3A7d" in url
    assert "hl=pt-BR" in url and "gl=BR" in url

    items = collector._items_from_feed_text(
        SAMPLE_RSS, max_items=25, min_date=None,
    )
    assert len(items) == 3
    assert items[0].source == "AutoData"
    assert "nova linha" in items[0].summary
    assert items[0].published is not None
    assert items[0].published.tzinfo is not None
    assert not collector._is_google_news_link(items[2].google_link)


def test_min_date_filter_and_html_cleaning():
    collector = GNewsCollector()
    cutoff = datetime(2026, 8, 16, 12, tzinfo=timezone.utc)
    items = collector._items_from_feed_text(
        SAMPLE_RSS, max_items=25, min_date=cutoff,
    )
    assert len(items) == 1
    assert items[0].title.startswith("Bosch")
    assert gnews_module._clean_html("<p>Texto &amp; entidades</p>") == (
        "Texto & entidades"
    )


@pytest.mark.asyncio
async def test_resolve_cache_and_direct_link(monkeypatch):
    calls = {"count": 0}

    def fake_decoder(url, interval=1.0, proxy=None):
        calls["count"] += 1
        return {"status": True, "decoded_url": "https://publisher.example/story"}

    monkeypatch.setattr(gnews_module, "gnewsdecoder", fake_decoder)
    collector = GNewsCollector(
        GNewsConfig(resolve_max_retries=3, resolve_backoff_base=0),
    )
    link = "https://news.google.com/rss/articles/CACHED?oc=5"

    assert await collector.resolve_link(link) == "https://publisher.example/story"
    assert await collector.resolve_link(link) == "https://publisher.example/story"
    assert calls["count"] == 1
    assert await collector.resolve_link("https://publisher.example/direct") == (
        "https://publisher.example/direct"
    )


@pytest.mark.asyncio
async def test_resolve_retries_and_resolve_all_isolates_failures(monkeypatch):
    calls: list[str] = []

    def fake_decoder(url, interval=1.0, proxy=None):
        calls.append(url)
        if "OK" in url:
            return {"status": True, "decoded_url": "https://publisher.example/ok"}
        return {"status": False, "message": "simulated decoder failure"}

    monkeypatch.setattr(gnews_module, "gnewsdecoder", fake_decoder)
    collector = GNewsCollector(
        GNewsConfig(resolve_max_retries=3, resolve_backoff_base=0),
    )
    items = [
        GNewsItem("ok", "https://news.google.com/articles/OK", None, "X"),
        GNewsItem("direct", "https://publisher.example/direct", None, "Y"),
        GNewsItem("bad", "https://news.google.com/articles/BAD", None, "Z"),
    ]

    await collector.resolve_all(items)

    assert items[0].resolve_status == "ok"
    assert items[0].link == "https://publisher.example/ok"
    assert items[1].resolve_status == "skipped"
    assert items[2].resolve_status == "failed"
    assert "simulated decoder failure" in (items[2].resolve_error or "")
    assert calls.count("https://news.google.com/articles/BAD") == 3
