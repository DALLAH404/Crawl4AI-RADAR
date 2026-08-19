"""Tests for _fetch_items' google_news_query branch — specifically that it
threads the collection-window cutoff into gnews as min_date, so the
relevance-ordered/date-filtered fix in sources/gnews.py actually gets a
cutoff to work with instead of silently defaulting to None.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from radar_pipeline.config import CollectSettings
from radar_pipeline.sources import collector as coll


class _StubGNews:
    """Records what it was called with; returns nothing, no network."""

    def __init__(self):
        self.build_search_url_calls: list[str] = []
        self.parse_rss_detailed_calls: list[dict] = []

    def build_search_url(self, query, when_days=None):
        self.build_search_url_calls.append(query)
        return "https://news.google.com/rss/search?q=x"

    async def fetch_rss(self, url):
        return "<rss><channel></channel></rss>"

    def parse_rss_detailed(self, raw_xml, max_items=None, min_date=None):
        self.parse_rss_detailed_calls.append(
            {"max_items": max_items, "min_date": min_date},
        )
        return [], {"total": 0, "in_window": 0, "kept": 0}


def _source() -> dict:
    return {
        "feed_type": "google_news_query",
        "query_text": "Bosch autopeças",
        "tag": "Bosch",
        "name": "Bosch",
    }


@pytest.mark.asyncio
async def test_fetch_items_passes_collect_cutoff_as_min_date():
    cfg = CollectSettings(hours_back=24)
    gnews = _StubGNews()

    items = await coll._fetch_items(
        _source(), client=None, limiter=None, config=cfg, mode="normal", gnews=gnews,
    )

    assert items == []
    assert len(gnews.parse_rss_detailed_calls) == 1
    min_date = gnews.parse_rss_detailed_calls[0]["min_date"]
    assert min_date is not None

    expected = datetime.now(timezone.utc) - timedelta(hours=24)
    assert abs((min_date - expected).total_seconds()) < 5


@pytest.mark.asyncio
async def test_fetch_items_uses_backfill_days_in_backfill_mode():
    cfg = CollectSettings(hours_back=24, backfill_days=90)
    gnews = _StubGNews()

    await coll._fetch_items(
        _source(), client=None, limiter=None, config=cfg, mode="backfill", gnews=gnews,
    )

    min_date = gnews.parse_rss_detailed_calls[0]["min_date"]
    expected = datetime.now(timezone.utc) - timedelta(days=90)
    assert abs((min_date - expected).total_seconds()) < 5


def test_collect_cutoff_prefers_hours_back_over_days_back():
    cfg = CollectSettings(hours_back=3, days_back=30)
    cutoff = coll._collect_cutoff(cfg, "normal")
    expected = datetime.now(timezone.utc) - timedelta(hours=3)
    assert abs((cutoff - expected).total_seconds()) < 5


def test_collect_cutoff_falls_back_to_days_back_when_hours_back_unset():
    cfg = CollectSettings(hours_back=None, days_back=30)
    cutoff = coll._collect_cutoff(cfg, "normal")
    expected = datetime.now(timezone.utc) - timedelta(days=30)
    assert abs((cutoff - expected).total_seconds()) < 5


def test_collect_cutoff_backfill_mode_ignores_hours_back():
    cfg = CollectSettings(hours_back=3, backfill_days=90)
    cutoff = coll._collect_cutoff(cfg, "backfill")
    expected = datetime.now(timezone.utc) - timedelta(days=90)
    assert abs((cutoff - expected).total_seconds()) < 5
