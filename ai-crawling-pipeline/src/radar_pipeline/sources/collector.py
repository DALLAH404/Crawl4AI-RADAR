"""Async collection engine — concurrent fetch of all active sources.

Replaces coletarLote() / backfillHistorico() from codigo.gs.

Modernized niceties vs. the legacy version:

* One shared ``httpx.AsyncClient`` for the whole run (was: one per
  source, no reuse). Connection pooling + ``httpx``'s transport-level
  limits save real time on 90 sources.
* ``RateLimiter`` (see ``radar_pipeline.sources.ratelimit``) caps the
  in-flight and per-second rate against each host, with a circuit
  breaker for ``news.google.com`` — the previous code hammered it with
  ~2,000 requests/run and immediately tripped Google's "Sorry..."
  block. The breaker now also surfaces 503s as ``status='error'`` in
  ``collection_runs`` instead of silently recording them as successful
  empty runs (the old ``feedparser.parse → []`` behaviour).
* Dedup runs on the *raw* (unresolved) link **before** any call to the
  Google News resolver, restoring the dedup-before-fetch order that
  ``codigo.gs`` had. Roughly 95% of items on recurring runs are
  duplicates; resolving each one was the main 503 amplifier.
* Every non-200 / blocked / timeout is raised as ``FeedFetchError``
  (or ``HostBlockedError``) and recorded as ``run.status='error'``
  with the underlying message. ``radar-pipeline status`` now shows the
  truth.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from radar_pipeline.classify.rules import classify_article, eh_fora_de_escopo
from radar_pipeline.db import find_by_article_hash, find_by_raw_link, put_article
from radar_pipeline.models import Article, CollectionRun, CollectionStats
from radar_pipeline.sources.feedparser import FeedFetchError, fetch_and_parse
from radar_pipeline.sources.gnews import resolve_google_news_url
from radar_pipeline.sources.linkedin import (
    LinkedInBlockedError,
    company_page_url,
    fetch_company_posts,
)
from radar_pipeline.sources.ratelimit import HostBlockedError, RateLimiter

logger = logging.getLogger(__name__)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _gnews_url(query: str) -> str:
    from urllib.parse import quote

    return (
        f"https://news.google.com/rss/search?q={quote(query)}"
        f"&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    )


def _feed_url(source: dict[str, Any]) -> str:
    """Google News RSS search URL for a query-based source — deliberately
    with no `when:` recency operator. Google's search backend returns zero
    results for several of our exact configured queries (confirmed live,
    reproduced cleanly with pauses to rule out rate-limiting: e.g.
    "Bosch autopeças OR aftermarket Brasil" returns 50-60+ items with no
    `when:` at all, and 0 items with `when:3h`, `when:7d`, or `when:30d` —
    same query, every window size, all zero) while the identical query
    without `when:` works every time. There's no query-shape rule that
    predicts which ones break, so rather than gamble per-source, this never
    asks Google to time-scope results at all. `_process_source`'s own
    `cutoff` filter (by each item's real `published_at`) already discards
    anything outside the actual collection window downstream, and
    `find_by_raw_link` dedups the rest — recency scoping doesn't depend on
    Google's `when:` operator working."""
    if source["feed_type"] == "linkedin_company":
        return company_page_url(source["query_text"])

    if source["feed_type"] == "rss_direct" and source["rss_url"]:
        return source["rss_url"]

    q = source["query_text"] or source["tag"] or source["name"]
    return _gnews_url(q)


def _build_limiter(config) -> RateLimiter:
    return RateLimiter(
        per_host_concurrency=config.gnews_concurrency,
        request_delay_seconds=config.request_delay_seconds,
        jitter_seconds=config.request_jitter_seconds,
        block_cooldown_seconds=config.block_cooldown_seconds,
    )


def _build_linkedin_limiter(config) -> RateLimiter:
    li = config.linkedin
    return RateLimiter(
        per_host_concurrency=1,
        request_delay_seconds=li.delay_seconds,
        jitter_seconds=li.jitter_seconds,
        block_cooldown_seconds=li.block_cooldown_seconds,
    )


async def _fetch_items(
    source: dict[str, Any],
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    config,
) -> list[dict[str, Any]]:
    """Fetch raw items for a source, in the ``feedparser`` item shape.

    Every producer either returns items or raises ``FeedFetchError`` — the
    caller (``_process_source``) doesn't need to know which fetch mechanism
    (RSS/httpx vs. LinkedIn/Crawl4AI) produced them.
    """
    if source["feed_type"] == "linkedin_company":
        try:
            return await fetch_company_posts(
                source["query_text"], limiter=limiter, settings=config.linkedin,
            )
        except LinkedInBlockedError as exc:
            raise FeedFetchError(f"linkedin blocked: {exc.reason}", url=_feed_url(source)) from exc

    url = _feed_url(source)
    try:
        return await fetch_and_parse(
            url,
            client,
            limiter=limiter,
            max_retries=config.fetch_max_retries,
            backoff_seconds=config.fetch_backoff_seconds,
        )
    except HostBlockedError as exc:
        raise FeedFetchError(f"host blocked: {exc.host}", url=url) from exc
    except FeedFetchError:
        raise
    except Exception as exc:
        raise FeedFetchError(str(exc), url=url) from exc


async def _process_source(
    store,
    source: dict[str, Any],
    batch_id: str,
    mode: str,
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    config,
) -> CollectionRun:
    t0 = time.monotonic()
    run = CollectionRun(
        run_id=batch_id,
        source_id=source["source_id"],
        source_name=source["name"],
        executed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        mode=mode,
        status="ok",
    )
    is_linkedin = source["feed_type"] == "linkedin_company"

    try:
        items = await _fetch_items(source, client, limiter, config)

        run.items_found = len(items)

        max_items = config.backfill_max_items if mode == "backfill" else config.max_items_per_feed
        items = items[:max_items]

        if mode == "backfill":
            window = timedelta(days=config.backfill_days)
        elif config.hours_back is not None:
            window = timedelta(hours=config.hours_back)
        else:
            window = timedelta(days=config.days_back)
        cutoff = datetime.now(timezone.utc) - window

        new_count = 0
        for item in items:
            if item["published_at"]:
                try:
                    pub_dt = datetime.fromisoformat(item["published_at"])
                    if pub_dt < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass

            text = f"{item['title']} {item['summary_text']}"
            if eh_fora_de_escopo(text):
                continue

            # Fast-path dedup on the unresolved (raw) link — saves a
            # round-trip to news.google.com for the ~95% of items that
            # were already collected on a previous run. LinkedIn post URLs
            # are already canonical, so this is also the only dedup check
            # they need (no resolver step below).
            raw_link = item["link"]
            existing_raw = find_by_raw_link(store, raw_link)
            if existing_raw is not None:
                continue

            if is_linkedin:
                resolved_link = raw_link
            else:
                # New (or unrecorded) raw link — resolve it. The resolver
                # also goes through the limiter.
                try:
                    resolved_link = await resolve_google_news_url(
                        raw_link, client, limiter=limiter,
                    )
                except HostBlockedError as exc:
                    raise FeedFetchError(
                        f"host blocked during resolve: {exc.host}", url=raw_link,
                    ) from exc

            article_hash = _md5(resolved_link)

            existing = find_by_article_hash(store, article_hash)
            if existing is not None:
                continue

            title = item["title"] or (f"{source['name']} LinkedIn post" if is_linkedin else "")

            event_type, alert_level, is_launch = classify_article(
                title, item["summary_text"]
            )

            article = Article(
                article_hash=article_hash,
                title_hash=_md5(title.strip().lower()),
                published_at=item["published_at"] or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                collected_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                category=source.get("category", "auto"),
                competitor_tag=source.get("tag", ""),
                product_line=source.get("product_line", "Geral"),
                title=title,
                action_description=item.get("action_description") or item["summary_text"][:300],
                summary_status="pending",
                event_type=event_type,
                alert_level=alert_level,
                is_launch=is_launch,
                image_url=item["image_url"],
                link=resolved_link,
                raw_link=raw_link,
                ingestion_batch_id=batch_id,
                source_id=source["source_id"],
                source_name=source["name"],
                feed_type=source["feed_type"],
                content_kind=source["content_kind"],
                extra=item.get("extra", ""),
            )

            if put_article(store, article):
                new_count += 1

        run.items_new = new_count
        run.status = "ok"
    except FeedFetchError as exc:
        run.status = "error"
        run.error_message = f"HTTP {exc.status_code}" if exc.status_code else str(exc)
        logger.warning(
            "collect error for %s: %s",
            source["name"], run.error_message,
        )
    except Exception as exc:
        run.status = "error"
        run.error_message = str(exc)[:500]
        logger.exception("collect error for %s", source["name"])

    run.duration_ms = int((time.monotonic() - t0) * 1000)
    return run


def _reduce_results(
    sources: list[dict[str, Any]],
    results: list[CollectionRun | BaseException],
    batch_id: str,
    mode: str,
    stats: CollectionStats,
) -> None:
    # Per-run audit history (the old collection_runs table) isn't persisted
    # to DynamoDB — see the Phase 1 design notes — so each run's outcome is
    # only recorded here, in CollectionStats, and in the structured log
    # below (which lands in CloudWatch Logs once this runs on Fargate).
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            stats.sources_error += 1
            logger.error(
                "collect error for %s: %s", sources[i]["name"], str(result)[:500],
            )
            continue

        run = result
        if run.status == "ok":
            stats.sources_ok += 1
            stats.items_found += run.items_found
            stats.items_new += run.items_new
        else:
            stats.sources_error += 1
            stats.errors.append({
                "source": run.source_name,
                "error": run.error_message,
            })


async def collect_once(store, sources: list, config, mode: str = "normal") -> CollectionStats:
    """`sources` is every active Source the caller wants considered (already
    filtered to `active: true`); this function applies the config-driven
    company scope (`config.companies`) on top of that."""
    start = time.monotonic()
    batch_id = uuid.uuid4().hex[:12]

    if config.companies:
        wanted = set(config.companies)
        sources = [s for s in sources if s.tag in wanted]

    all_sources = [dataclasses.asdict(s) for s in sources]
    if not all_sources:
        logger.warning("No active sources found (after the company filter, if any).")
        return CollectionStats()

    linkedin_sources = [s for s in all_sources if s["feed_type"] == "linkedin_company"]
    if not config.linkedin.enabled:
        linkedin_sources = []
    rss_sources = [s for s in all_sources if s["feed_type"] != "linkedin_company"]

    stats = CollectionStats(sources_total=len(rss_sources) + len(linkedin_sources))

    # RSS / Google-News pass — one shared httpx.AsyncClient, bounded by the
    # general collect concurrency.
    if rss_sources:
        limiter = _build_limiter(config)
        concurrency = max(1, int(getattr(config, "concurrency", 1)))
        semaphore = asyncio.Semaphore(concurrency)
        client_limits = httpx.Limits(
            max_connections=max(8, concurrency * 2),
            max_keepalive_connections=concurrency,
        )

        async def _bounded_rss(s: dict[str, Any]) -> CollectionRun:
            async with semaphore:
                async with httpx.AsyncClient(
                    timeout=30.0,
                    limits=client_limits,
                    follow_redirects=True,
                ) as client:
                    return await _process_source(
                        store, s, batch_id, mode, client, limiter, config,
                    )

        rss_results = await asyncio.gather(
            *(_bounded_rss(s) for s in rss_sources),
            return_exceptions=True,
        )
        _reduce_results(rss_sources, rss_results, batch_id, mode, stats)

    # LinkedIn pass — separate, low-concurrency: each source holds its slot
    # for tens of seconds (browser launch + inter-company delay) and would
    # otherwise starve the shared RSS semaphore. All companies share the
    # www.linkedin.com host, so one RateLimiter across the whole pass both
    # enforces delay_seconds spacing between companies and gives the "a
    # block is IP-wide" circuit-breaker behaviour across the batch.
    if linkedin_sources:
        li_limiter = _build_linkedin_limiter(config)
        li_semaphore = asyncio.Semaphore(max(1, config.linkedin.concurrency))
        # No httpx client is used on this path (Crawl4AI owns its own
        # browser transport); _process_source still takes a `client`
        # parameter for its RSS-only resolve step, which LinkedIn sources
        # never reach.
        async with httpx.AsyncClient(timeout=30.0) as li_client:
            async def _bounded_linkedin(s: dict[str, Any]) -> CollectionRun:
                async with li_semaphore:
                    return await _process_source(
                        store, s, batch_id, mode, li_client, li_limiter, config,
                    )

            linkedin_results = await asyncio.gather(
                *(_bounded_linkedin(s) for s in linkedin_sources),
                return_exceptions=True,
            )
        _reduce_results(linkedin_sources, linkedin_results, batch_id, mode, stats)

    stats.duration_ms = int((time.monotonic() - start) * 1000)
    return stats
