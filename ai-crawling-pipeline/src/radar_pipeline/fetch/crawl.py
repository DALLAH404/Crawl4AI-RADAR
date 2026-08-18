"""Article fetcher using Crawl4AI — crawls articles and saves them as Markdown.

Reuses ai_crawling_pipeline.anti_block for block detection and retry.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

from radar_pipeline.db import pending_articles, update_article
from radar_pipeline.fetch.image import fetch_og_image
from radar_pipeline.fetch.writer import upload_article_md, write_article_md
from radar_pipeline.sources.gnews import resolve_google_news_url

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 4


def _s3_client(s3_settings):
    import boto3

    kwargs = {}
    if s3_settings.region_name:
        kwargs["region_name"] = s3_settings.region_name
    if s3_settings.endpoint_url:
        kwargs["endpoint_url"] = s3_settings.endpoint_url
    return boto3.client("s3", **kwargs)


async def fetch_articles(
    store,
    config,
    concurrency: int = DEFAULT_CONCURRENCY,
    run_timestamp: str | None = None,
) -> dict[str, int]:
    articles = pending_articles(store)
    if not articles:
        logger.info("No pending articles to fetch")
        return {"total": 0, "fetched": 0, "failed": 0}

    s3_settings = config.s3 if config else None
    s3_client = _s3_client(s3_settings) if s3_settings and s3_settings.bucket else None
    # One timestamp for every article this call fetches, so they all land
    # under the same run folder in S3 — computed once here, not per-article.
    run_timestamp = run_timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    output_dir = config.output_dir if config else Path("outputs/radar/raw")
    if s3_client is None:
        output_dir.mkdir(parents=True, exist_ok=True)

    browser_kwargs = dict(config.browser) if config else {}
    browser_kwargs.setdefault("headless", True)
    browser_cfg = BrowserConfig(**browser_kwargs)

    from ai_crawling_pipeline.anti_block import crawl_with_retry
    from ai_crawling_pipeline.config import Target, AntiBlockSettings

    semaphore = asyncio.Semaphore(concurrency)
    fetched = 0
    failed = 0

    def _save_article_md(**kwargs) -> None:
        # Same content either way (render_article_markdown) — this just
        # decides where it lands. S3 wins when configured: local disk on
        # Fargate is ephemeral, so writing there in production would just
        # be discarded when the task stops.
        if s3_client is not None:
            upload_article_md(s3_client, s3_settings.bucket, s3_settings.prefix, run_timestamp, **kwargs)
        else:
            write_article_md(output_dir=output_dir, **kwargs)

    async def _fetch_one(article) -> bool:
        nonlocal fetched, failed
        async with semaphore:
            try:
                if article.get("feed_type") == "linkedin_company":
                    # LinkedIn authwalls individual /posts/ URLs for
                    # logged-out crawlers, so there is nothing to crawl here
                    # — the full post text was already captured at collect
                    # time (Article.action_description). Just archive it in
                    # the same shape as every other source, at zero network
                    # cost.
                    _save_article_md(
                        source_id=article.get("source_id") or "unknown",
                        article_hash=article["article_hash"],
                        title=article["title"],
                        url=article["link"],
                        markdown=article.get("action_description") or article["title"],
                        category=article.get("category") or "auto",
                        tag=article.get("competitor_tag") or "",
                        product_line=article.get("product_line") or "Geral",
                        event_type=article.get("event_type") or "",
                        alert_level=article.get("alert_level") or "",
                        date=article.get("published_at") or "",
                        image_url=article.get("image_url") or "",
                    )
                    fetched += 1
                    return True

                target_url = article["link"]
                if target_url and "news.google" in target_url:
                    import httpx
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        target_url = await resolve_google_news_url(target_url, client)

                import hashlib
                article_hash = hashlib.md5(
                    target_url.encode("utf-8")
                ).hexdigest()

                from radar_pipeline.db import find_by_article_hash
                existing = find_by_article_hash(store, article_hash)
                if existing is not None and existing["article_hash"] != article["article_hash"]:
                    logger.info("Skipping duplicate URL for article %s", article["article_hash"])
                    return False

                if article.get("image_url"):
                    image_url = article["image_url"]
                else:
                    import httpx
                    async with httpx.AsyncClient(timeout=15.0) as img_client:
                        image_url = await fetch_og_image(target_url, img_client)
                    if image_url:
                        # Persisted right away, independent of whether the
                        # heavier Crawl4AI crawl below succeeds. These are
                        # two separate fetches against the same URL with very
                        # different failure profiles (a lightweight httpx GET
                        # here vs full browser automation there) — without
                        # this, a successfully-fetched image was silently
                        # discarded whenever the content crawl failed
                        # afterward, since the only other image_url write
                        # sits at the end of the function, gated on the whole
                        # crawl succeeding.
                        update_article(store, article["article_hash"], image_url=image_url)

                target = Target(
                    slug=f"radar_{article['article_hash'][:12]}",
                    url=target_url,
                )

                ab_settings = None
                if config and config.anti_block:
                    ab_settings = AntiBlockSettings(**config.anti_block)

                defaults = dict(config.defaults) if config else {}

                async with AsyncWebCrawler(config=browser_cfg) as crawler:
                    result, exc, attempts, log = await crawl_with_retry(
                        crawler=crawler,
                        target=target,
                        base_run_config=defaults,
                        settings=ab_settings,
                    )

                if exc is not None or result is None or not result.success:
                    logger.warning("Fetch failed for article %s: %s", article["article_hash"], exc or "no result")
                    failed += 1
                    return False

                markdown = result.markdown or ""
                if not markdown.strip():
                    logger.warning("Empty markdown for article %s", article["article_hash"])
                    failed += 1
                    return False

                title = result.metadata.get("title", article["title"]) if result.metadata else article["title"]

                _save_article_md(
                    source_id=article.get("source_id") or "unknown",
                    article_hash=article_hash,
                    title=title,
                    url=target_url,
                    markdown=markdown,
                    category=article.get("category") or "auto",
                    tag=article.get("competitor_tag") or "",
                    product_line=article.get("product_line") or "Geral",
                    event_type=article.get("event_type") or "",
                    alert_level=article.get("alert_level") or "",
                    date=article.get("published_at") or "",
                    image_url=image_url,
                )

                update_article(store, article["article_hash"], image_url=image_url)

                fetched += 1
                return True

            except Exception as exc:
                logger.exception("Fetch error for article %s", article["article_hash"])
                failed += 1
                return False

    tasks = [_fetch_one(a) for a in articles]
    await asyncio.gather(*tasks, return_exceptions=True)

    return {
        "total": len(articles),
        "fetched": fetched,
        "failed": failed,
    }
