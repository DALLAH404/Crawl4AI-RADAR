"""Integration tests for fetch_articles' S3 vs local-disk output.

Uses the LinkedIn short-circuit path (no Crawl4AI/browser mocking needed —
it never calls AsyncWebCrawler) to exercise the actual write, not just the
writer functions in isolation. Relies on the `store` fixture's already-open
mock_aws() context to also mock S3 — no need for a second one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import boto3
import pytest

from radar_pipeline.config import FetchSettings, S3Settings
from radar_pipeline.db import put_article
from radar_pipeline.fetch.crawl import fetch_articles
from radar_pipeline.models import Article

TEST_BUCKET = "test-radar-raw"


def _linkedin_article(**overrides) -> Article:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    defaults = dict(
        article_hash="li-hash-1",
        title_hash="li-title-hash-1",
        published_at="2026-08-13",
        collected_at=now,
        category="auto",
        competitor_tag="Bosch",
        product_line="Geral",
        title="Bosch launches a new brake pad line",
        action_description="Bosch launches a new brake pad line for aftermarket. Full text.",
        summary_status="pending",
        link="https://www.linkedin.com/posts/bosch_activity-1",
        raw_link="https://www.linkedin.com/posts/bosch_activity-1",
        source_id="bosch-li",
        source_name="Bosch LinkedIn",
        feed_type="linkedin_company",
    )
    defaults.update(overrides)
    return Article(**defaults)


def _s3_config(tmp_path: Path) -> FetchSettings:
    return FetchSettings(
        output_dir=tmp_path,
        s3=S3Settings(bucket=TEST_BUCKET, prefix="raw", region_name="us-east-1"),
    )


@pytest.mark.asyncio
async def test_fetch_writes_to_local_disk_when_no_s3_configured(store, tmp_path: Path):
    put_article(store, _linkedin_article())
    config = FetchSettings(output_dir=tmp_path)

    result = await fetch_articles(store, config)

    assert result == {"total": 1, "fetched": 1, "failed": 0}
    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    assert "Bosch launches a new brake pad line" in files[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_fetch_writes_to_s3_under_run_folder_when_configured(store, tmp_path: Path):
    put_article(store, _linkedin_article())
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=TEST_BUCKET)

    result = await fetch_articles(store, _s3_config(tmp_path), run_timestamp="20260813T090000Z")

    assert result == {"total": 1, "fetched": 1, "failed": 0}

    # Nothing written locally — S3 wins entirely when configured.
    assert list(tmp_path.glob("*.md")) == []

    listing = s3.list_objects_v2(Bucket=TEST_BUCKET, Prefix="raw/20260813T090000Z/")
    keys = [obj["Key"] for obj in listing.get("Contents", [])]
    assert len(keys) == 1
    assert keys[0].startswith("raw/20260813T090000Z/bosch-li/")

    body = s3.get_object(Bucket=TEST_BUCKET, Key=keys[0])["Body"].read().decode("utf-8")
    assert "Bosch launches a new brake pad line" in body


@pytest.mark.asyncio
async def test_fetch_two_runs_land_in_separate_s3_folders(store, tmp_path: Path):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=TEST_BUCKET)
    config = _s3_config(tmp_path)

    put_article(store, _linkedin_article(
        article_hash="li-hash-2",
        link="https://www.linkedin.com/posts/bosch_activity-2",
        raw_link="https://www.linkedin.com/posts/bosch_activity-2",
    ))
    await fetch_articles(store, config, run_timestamp="20260813T060000Z")

    put_article(store, _linkedin_article(
        article_hash="li-hash-3",
        link="https://www.linkedin.com/posts/bosch_activity-3",
        raw_link="https://www.linkedin.com/posts/bosch_activity-3",
    ))
    await fetch_articles(store, config, run_timestamp="20260813T090000Z")

    all_keys = [o["Key"] for o in s3.list_objects_v2(Bucket=TEST_BUCKET).get("Contents", [])]
    run_folders = {key.split("/")[1] for key in all_keys}
    assert run_folders == {"20260813T060000Z", "20260813T090000Z"}
