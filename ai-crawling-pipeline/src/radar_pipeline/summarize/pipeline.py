"""Summarization pipeline — processes pending articles via LLM.

Replaces the manual N8N/LEA Studio step. Runs automatically: for each
article with summary_status='pending', calls the LLM, and updates the
database with the generated summary, competitor_analysis, event_type,
alert_level, and relevance flag.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from radar_pipeline.config import SummarizeSettings
from radar_pipeline.db import (
    mark_article_failed,
    mark_article_irrelevant,
    pending_articles,
    update_article,
)
from radar_pipeline.summarize.client import summarize_one
from radar_pipeline.summarize.prompts import DEFAULT_SYSTEM_PROMPT
from radar_pipeline.summarize.writer import write_summary_json

logger = logging.getLogger(__name__)


async def run_summarize(
    store,
    settings: SummarizeSettings,
) -> dict[str, int]:
    articles = pending_articles(store)
    if not articles:
        logger.info("No pending articles to summarize")
        return {"total": 0, "summarized": 0, "irrelevant": 0, "failed": 0}

    concurrency = settings.concurrency
    semaphore = asyncio.Semaphore(concurrency)
    system_prompt = settings.system_prompt or DEFAULT_SYSTEM_PROMPT

    summarized = 0
    irrelevant = 0
    failed = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def _process_one(article) -> None:
        nonlocal summarized, irrelevant, failed
        async with semaphore:
            content = article["action_description"] or ""
            if article["summary"]:
                content = f"{article['summary']}\n\n{content}"

            result = await summarize_one(
                title=article["title"],
                content=content,
                llm_settings=settings.llm,
                system_prompt=system_prompt,
                max_chars=settings.max_input_chars,
            )

            if not result.ok:
                mark_article_failed(store, article["article_hash"], result.error)
                failed += 1
                logger.warning("Summarize failed for article %s: %s", article["article_hash"], result.error)
                return

            if not result.relevant:
                mark_article_irrelevant(store, article["article_hash"])
                irrelevant += 1
                return

            update_article(
                store,
                article["article_hash"],
                summary=result.summary,
                competitor_analysis=result.competitor_analysis,
                event_type=result.event_type,
                alert_level=result.alert_level,
                summary_status="ai_generated",
                ai_model=settings.llm.model,
                ai_processed_at=now,
            )
            write_summary_json(
                output_dir=settings.output_dir,
                article_hash=article["article_hash"],
                source_id=article["source_id"] or "unknown",
                source_name=article["source_name"] or "",
                title=article["title"],
                url=article["link"] or "",
                summary=result.summary,
                competitor_analysis=result.competitor_analysis,
                category=article["category"] or "auto",
                tag=article["competitor_tag"] or "",
                product_line=article["product_line"] or "Geral",
                event_type=result.event_type,
                alert_level=result.alert_level,
                date=article["published_at"] or "",
                image_url=article["image_url"] or "",
                ai_model=settings.llm.model,
                ai_processed_at=now,
            )
            summarized += 1

    tasks = [_process_one(a) for a in articles]
    await asyncio.gather(*tasks)

    return {
        "total": len(articles),
        "summarized": summarized,
        "irrelevant": irrelevant,
        "failed": failed,
    }
