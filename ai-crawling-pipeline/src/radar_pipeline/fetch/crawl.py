"""Article fetcher using Crawl4AI — crawls articles and saves them as Markdown.

Reuses ai_crawling_pipeline.anti_block for block detection and retry.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

from radar_pipeline.db import pending_articles
from radar_pipeline.fetch.image import fetch_og_image
from radar_pipeline.fetch.writer import write_article_md
from radar_pipeline.sources.gnews import resolve_google_news_url

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 4


async def fetch_articles(
    con,
    config,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> dict[str, int]:
    articles = pending_articles(con)
    if not articles:
        logger.info("No pending articles to fetch")
        return {"total": 0, "fetched": 0, "failed": 0}

    output_dir = config.output_dir if config else Path("outputs/radar/raw")
    output_dir.mkdir(parents=True, exist_ok=True)

    browser_kwargs = dict(config.browser) if config else {}
    browser_kwargs.setdefault("headless", True)
    browser_cfg = BrowserConfig(**browser_kwargs)

    from ai_crawling_pipeline.anti_block import crawl_with_retry
    from ai_crawling_pipeline.config import Target, AntiBlockSettings

    semaphore = asyncio.Semaphore(concurrency)
    fetched = 0
    failed = 0

    async def _fetch_one(article) -> bool:
        nonlocal fetched, failed
        async with semaphore:
            try:
                if article.get("feed_type") == "linkedin_company":
                    # LinkedIn authwalls individual /posts/ URLs for
                    # logged-out crawlers, so there is nothing to crawl here
                    # — the full post text was already captured at collect
                    # time (Article.action_description). Just archive it in
                    # the same outputs/raw/<source_id>/*.md shape as every
                    # other source, at zero network cost.
                    write_article_md(
                        output_dir=output_dir,
                        source_id=article["source_id"] or "unknown",
                        article_hash=article["article_hash"],
                        title=article["title"],
                        url=article["link"],
                        markdown=article["action_description"] or article["title"],
                        category=article["category"] or "auto",
                        tag=article["competitor_tag"] or "",
                        product_line=article["product_line"] or "Geral",
                        event_type=article["event_type"] or "",
                        alert_level=article["alert_level"] or "",
                        date=article["published_at"] or "",
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
                existing = find_by_article_hash(con, article_hash)
                if existing is not None and existing["id"] != article["id"]:
                    logger.info("Skipping duplicate URL for article %d", article["id"])
                    return False

                if article.get("image_url"):
                    image_url = article["image_url"]
                else:
                    import httpx
                    async with httpx.AsyncClient(timeout=15.0) as img_client:
                        image_url = await fetch_og_image(target_url, img_client)

                target = Target(
                    slug=f"radar_{article['id']}",
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
                    logger.warning("Fetch failed for article %d: %s", article["id"], exc or "no result")
                    failed += 1
                    return False

                markdown = result.markdown or ""
                if not markdown.strip():
                    logger.warning("Empty markdown for article %d", article["id"])
                    failed += 1
                    return False

                title = result.metadata.get("title", article["title"]) if result.metadata else article["title"]

                write_article_md(
                    output_dir=output_dir,
                    source_id=article["source_id"] or "unknown",
                    article_hash=article_hash,
                    title=title,
                    url=target_url,
                    markdown=markdown,
                    category=article["category"] or "auto",
                    tag=article["competitor_tag"] or "",
                    product_line=article["product_line"] or "Geral",
                    event_type=article["event_type"] or "",
                    alert_level=article["alert_level"] or "",
                    date=article["published_at"] or "",
                    image_url=image_url,
                )

                con.execute(
                    "UPDATE articles SET image_url = ? WHERE id = ?",
                    (image_url, article["id"]),
                )

                fetched += 1
                return True

            except Exception as exc:
                logger.exception("Fetch error for article %d", article["id"])
                failed += 1
                return False

    tasks = [_fetch_one(a) for a in articles]
    await asyncio.gather(*tasks, return_exceptions=True)

    con.commit()

    return {
        "total": len(articles),
        "fetched": fetched,
        "failed": failed,
    }
