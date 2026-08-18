"""Regression tests for fetch_articles' image_url persistence.

fetch_og_image (a lightweight httpx GET) and the main Crawl4AI content crawl
(full browser automation) are two independent fetches against the same URL,
with very different failure profiles. These mock both out — no real network,
no real browser — to prove image_url is saved even when the content crawl
fails, which previously discarded it silently (see fetch/crawl.py's
_fetch_one: the only update_article(image_url=...) call sat at the very end,
reached only after the whole crawl succeeded).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from radar_pipeline.config import FetchSettings
from radar_pipeline.db import get_article, put_article
from radar_pipeline.fetch.crawl import fetch_articles
from radar_pipeline.models import Article

FAKE_IMAGE_URL = "https://example.com/og-image.png"


def _news_article(**overrides) -> Article:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    defaults = dict(
        article_hash="news-hash-1",
        title_hash="news-title-hash-1",
        published_at="2026-08-18",
        collected_at=now,
        category="tecnologia",
        competitor_tag="",
        product_line="Geral",
        title="Some article headline",
        action_description="Some article snippet.",
        summary_status="pending",
        link="https://canaltech.com.br/some-article/",
        raw_link="https://canaltech.com.br/some-article/",
        source_id="canaltech",
        source_name="Canaltech",
        feed_type="rss_direct",
    )
    defaults.update(overrides)
    return Article(**defaults)


class _FakeAsyncWebCrawler:
    """Stands in for crawl4ai.AsyncWebCrawler — never actually used since
    crawl_with_retry is mocked too, just needs to support `async with`."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch_common():
    """Patches shared by both tests: the og:image fetch succeeds, and
    AsyncWebCrawler is a no-op context manager (crawl_with_retry, patched
    separately per test, is what actually decides success/failure)."""
    return (
        patch("radar_pipeline.fetch.crawl.fetch_og_image", return_value=FAKE_IMAGE_URL),
        patch("radar_pipeline.fetch.crawl.AsyncWebCrawler", _FakeAsyncWebCrawler),
    )


@pytest.mark.asyncio
async def test_image_url_persisted_even_when_content_crawl_fails(store, tmp_path: Path):
    put_article(store, _news_article())
    config = FetchSettings(output_dir=tmp_path)

    async def failing_crawl_with_retry(**kwargs):
        return None, RuntimeError("blocked"), 3, ["attempt 1 blocked", "attempt 2 blocked"]

    patch_og_image, patch_crawler = _patch_common()
    with patch_og_image, patch_crawler, patch(
        "ai_crawling_pipeline.anti_block.crawl_with_retry", failing_crawl_with_retry
    ):
        result = await fetch_articles(store, config)

    assert result == {"total": 1, "fetched": 0, "failed": 1}

    updated = get_article(store, "news-hash-1")
    assert updated["image_url"] == FAKE_IMAGE_URL, (
        "image_url fetched successfully via fetch_og_image must survive even "
        "though the separate content crawl for the same article failed"
    )


@pytest.mark.asyncio
async def test_image_url_persisted_on_full_success(store, tmp_path: Path):
    put_article(store, _news_article(article_hash="news-hash-2", link="https://canaltech.com.br/other/"))
    config = FetchSettings(output_dir=tmp_path)

    fake_result = SimpleNamespace(success=True, markdown="# Full article text", metadata={"title": "Some article headline"})

    async def succeeding_crawl_with_retry(**kwargs):
        return fake_result, None, 1, []

    patch_og_image, patch_crawler = _patch_common()
    with patch_og_image, patch_crawler, patch(
        "ai_crawling_pipeline.anti_block.crawl_with_retry", succeeding_crawl_with_retry
    ):
        result = await fetch_articles(store, config)

    assert result == {"total": 1, "fetched": 1, "failed": 0}

    updated = get_article(store, "news-hash-2")
    assert updated["image_url"] == FAKE_IMAGE_URL
