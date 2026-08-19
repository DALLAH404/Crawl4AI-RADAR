"""Offline tests for the decoder-backed Google News collector."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from radar_pipeline.sources import gnews as gnews_module
from radar_pipeline.sources.feedparser import FeedFetchError
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


def test_parse_rss_defaults_to_two_items():
    collector = GNewsCollector(GNewsConfig(max_items_per_query=2))
    items = collector.parse_rss(SAMPLE_RSS)
    assert len(items) == 2
    assert items[0].title.startswith("Bosch")
    assert items[1].title.startswith("ZF")


def test_parse_rss_respects_explicit_max_items():
    collector = GNewsCollector(GNewsConfig(max_items_per_query=2))
    items = collector.parse_rss(SAMPLE_RSS, max_items=1)
    assert len(items) == 1
    assert items[0].title.startswith("Bosch")


@pytest.mark.asyncio
async def test_fetch_rss_returns_xml_body_and_fetch_html_returns_body():
    RSS_BODY = b"<?xml version='1.0'?><rss><channel></channel></rss>"
    HTML_BODY = b"<html><body>article</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        if "news.google.com" in str(request.url):
            return httpx.Response(200, content=RSS_BODY)
        return httpx.Response(200, content=HTML_BODY)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        collector = GNewsCollector(client=client)

        raw_xml = await collector.fetch_rss(
            "https://news.google.com/rss/search?q=x&hl=pt-BR&gl=BR"
        )
        assert raw_xml == RSS_BODY.decode()

        html = await collector.fetch_html("https://publisher.example/story")
        assert html == HTML_BODY.decode()


@pytest.mark.asyncio
async def test_fetch_rss_non_200_raises_feed_fetch_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="blocked")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        collector = GNewsCollector(client=client)
        with pytest.raises(FeedFetchError) as ei:
            await collector.fetch_rss("https://news.google.com/rss/search?q=x")
        assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_search_uses_staged_fetch_rss_then_parse_rss(monkeypatch):
    collector = GNewsCollector(GNewsConfig(max_items_per_query=2))
    stages: list[str] = []

    async def fake_fetch_rss(url: str) -> str:
        stages.append("fetch")
        return SAMPLE_RSS

    def fake_parse_rss(raw_xml: str, max_items=None, min_date=None):
        stages.append("parse")
        return [GNewsItem("x", "https://news.google.com/articles/X", None, "s")]

    monkeypatch.setattr(collector, "fetch_rss", fake_fetch_rss)
    monkeypatch.setattr(collector, "parse_rss", fake_parse_rss)

    items = await collector.search("Bosch")
    assert stages == ["fetch", "parse"]
    assert len(items) == 1
