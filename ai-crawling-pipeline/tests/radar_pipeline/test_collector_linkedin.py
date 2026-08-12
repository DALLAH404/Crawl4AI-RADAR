"""Tests for LinkedIn integration in the collect stage.

Confirms a linkedin_company source routes through fetch_company_posts (not
fetch_and_parse / resolve_google_news_url), that raw post URLs are used
as-is (no Google News resolution), and that the `extra` JSON payload
round-trips into the inserted article row.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from radar_pipeline.config import CollectSettings
from radar_pipeline.db import connect, init_schema, insert_source
from radar_pipeline.models import Source


@pytest.mark.asyncio
async def test_linkedin_source_skips_resolver_and_stores_extra(monkeypatch):
    con = connect(Path(tempfile.mktemp(suffix=".db")))
    init_schema(con, dim=768)

    s = Source(
        source_id="bosch-li", name="Bosch LinkedIn", source_type="concorrente",
        tag="Bosch", product_line="Geral", category="auto",
        feed_type="linkedin_company", query_text="bosch",
    )
    insert_source(con, s)
    con.commit()

    from radar_pipeline.sources import collector as coll

    resolver_calls: list[str] = []

    async def spy_resolve(url, client=None, *, limiter=None):
        resolver_calls.append(url)
        return "https://should-not-be-called.example/" + url

    async def fake_fetch_company_posts(slug, *, limiter, settings):
        assert slug == "bosch"
        return [{
            "title": "Bosch launches a new brake pad line",
            "link": "https://www.linkedin.com/posts/bosch_activity-1",
            "published_at": "2026-08-10",
            "summary_text": "Bosch launches a new brake pad line for aftermarket.",
            "image_url": "https://media.licdn.com/img.jpg",
            "action_description": "Bosch launches a new brake pad line for aftermarket. Full text here.",
            "extra": json.dumps({"reactions": 12, "comments": 3, "linkedin_slug": "bosch"}),
        }]

    monkeypatch.setattr(coll, "resolve_google_news_url", spy_resolve)
    monkeypatch.setattr(coll, "fetch_company_posts", fake_fetch_company_posts)

    cfg = CollectSettings()
    cfg.linkedin.delay_seconds = 0.0
    cfg.linkedin.jitter_seconds = 0.0

    stats = await coll.collect_once(con, cfg, mode="normal")

    assert resolver_calls == []
    assert stats.items_new == 1
    assert stats.sources_ok == 1

    row = con.execute(
        "SELECT * FROM articles WHERE link = ?",
        ("https://www.linkedin.com/posts/bosch_activity-1",),
    ).fetchone()
    assert row is not None
    assert row["raw_link"] == row["link"]
    assert row["title"] == "Bosch launches a new brake pad line"
    assert row["action_description"] == (
        "Bosch launches a new brake pad line for aftermarket. Full text here."
    )
    extra = json.loads(row["extra"])
    assert extra["reactions"] == 12

    con.close()


@pytest.mark.asyncio
async def test_linkedin_media_only_post_falls_back_to_source_name_title(monkeypatch):
    con = connect(Path(tempfile.mktemp(suffix=".db")))
    init_schema(con, dim=768)

    s = Source(
        source_id="valeo-li", name="Valeo LinkedIn", source_type="concorrente",
        tag="Valeo", product_line="Geral", category="auto",
        feed_type="linkedin_company", query_text="valeo",
    )
    insert_source(con, s)
    con.commit()

    from radar_pipeline.sources import collector as coll

    async def fake_fetch_company_posts(slug, *, limiter, settings):
        return [{
            "title": "",
            "link": "https://www.linkedin.com/posts/valeo_activity-2",
            "published_at": "2026-08-11",
            "summary_text": "",
            "image_url": "https://media.licdn.com/img2.jpg",
            "action_description": "",
            "extra": json.dumps({"reactions": 0, "comments": 0, "linkedin_slug": "valeo"}),
        }]

    monkeypatch.setattr(coll, "fetch_company_posts", fake_fetch_company_posts)

    cfg = CollectSettings()
    cfg.linkedin.delay_seconds = 0.0
    cfg.linkedin.jitter_seconds = 0.0

    stats = await coll.collect_once(con, cfg, mode="normal")

    assert stats.items_new == 1
    row = con.execute(
        "SELECT * FROM articles WHERE link = ?",
        ("https://www.linkedin.com/posts/valeo_activity-2",),
    ).fetchone()
    assert row is not None
    assert row["title"] == "Valeo LinkedIn LinkedIn post"

    con.close()
