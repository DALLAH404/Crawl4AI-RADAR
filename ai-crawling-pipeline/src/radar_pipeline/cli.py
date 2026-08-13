"""CLI entry point for the Radar Aftermarket Pipeline."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from radar_pipeline.config import S3Settings, load_radar_config
from radar_pipeline.db import (
    article_count,
    article_count_by_category,
    article_count_by_status,
    connect,
    ensure_table,
)
from radar_pipeline.observability.logging import setup_logging
from radar_pipeline.observability.metrics import MetricsCollector

logger = logging.getLogger("radar_pipeline")


def _active_sources(config):
    from radar_pipeline.sources import catalog

    return catalog.list_sources(config.sources.yaml_path, active_only=True)


def cmd_collect(store, config, metrics: MetricsCollector, mode: str = "normal") -> None:
    from radar_pipeline.sources.collector import collect_once

    sources = _active_sources(config)
    run = metrics.start("collect")
    print(f"Collecting from {config.sources.yaml_path} (mode={mode})...")
    stats = asyncio.run(collect_once(store, sources, config.collect, mode=mode))
    run.finish(
        success=stats.sources_error == 0,
        sources_ok=stats.sources_ok,
        sources_error=stats.sources_error,
        items_found=stats.items_found,
        items_new=stats.items_new,
        duration_ms=stats.duration_ms,
    )
    if stats.errors:
        for e in stats.errors[:5]:
            print(f"  error: {e['source']}: {e['error']}")
    print(
        f"Done: {stats.sources_ok} sources OK, {stats.sources_error} errors, "
        f"{stats.items_found} items found, {stats.items_new} new"
    )


def cmd_fetch(store, config, metrics: MetricsCollector) -> None:
    from radar_pipeline.fetch.crawl import fetch_articles

    if config.fetch is None:
        print("No fetch section in config; skipping.")
        return

    fetch_settings = config.fetch
    s3_bucket_override = os.environ.get("RADAR_FETCH_S3_BUCKET")
    if s3_bucket_override:
        # Same idea as RADAR_TABLE_NAME — lets the task definition point at
        # a bucket per environment without rebuilding the image or shipping
        # a different configs/radar.yaml.
        s3 = dataclasses.replace(fetch_settings.s3 or S3Settings(), bucket=s3_bucket_override)
        fetch_settings = dataclasses.replace(fetch_settings, s3=s3)

    run = metrics.start("fetch")
    print("Fetching articles...")
    result = asyncio.run(fetch_articles(store, fetch_settings))
    run.finish(**result)
    print(
        f"Done: {result['total']} pending, {result['fetched']} fetched, "
        f"{result['failed']} failed"
    )


def cmd_dedup(store, config, metrics: MetricsCollector) -> None:
    from radar_pipeline.dedup.layers import run_dedup

    if config.dedup is None:
        print("No dedup section in config; skipping.")
        return

    run = metrics.start("dedup")
    print("Running dedup...")
    result = asyncio.run(run_dedup(store, config.dedup))
    run.finish(**result)
    print(
        f"Done: {result['total']} processed, {result['new']} new, "
        f"{result['duplicates']} duplicates"
    )


def cmd_summarize(store, config, metrics: MetricsCollector) -> None:
    from radar_pipeline.summarize.pipeline import run_summarize

    if config.summarize is None:
        print("No summarize section in config; skipping.")
        return

    run = metrics.start("summarize")
    print(f"Summarizing articles (model={config.summarize.llm.model})...")
    result = asyncio.run(run_summarize(store, config.summarize))
    run.finish(**result)
    print(
        f"Done: {result['total']} pending, {result['summarized']} summarized, "
        f"{result['irrelevant']} irrelevant, {result['failed']} failed"
    )


def cmd_retry_failed(store, config, metrics: MetricsCollector) -> None:
    from radar_pipeline.db import retry_failed_articles

    run = metrics.start("retry-failed")
    print("Resetting failed articles back to pending...")
    reset = retry_failed_articles(store)
    run.finish(reset=reset)
    print(f"Done: {reset} article(s) reset to pending (will be retried on the next --summarize)")


def cmd_classify(store, config, metrics: MetricsCollector) -> None:
    from radar_pipeline.classify.rules import classify_article
    from radar_pipeline.db import pending_articles, update_article

    run = metrics.start("classify")
    articles = pending_articles(store)
    print(f"Re-classifying {len(articles)} pending articles...")
    for a in articles:
        event_type, alert_level, is_launch = classify_article(
            a["title"], a["action_description"]
        )
        update_article(
            store, a["article_hash"],
            event_type=event_type, alert_level=alert_level, is_launch=is_launch,
        )
    run.finish(updated=len(articles))
    print(f"Done: {len(articles)} articles re-classified")


def cmd_status(store, config, metrics: MetricsCollector) -> None:
    sources = _active_sources(config)
    total_articles = article_count(store)
    by_status = article_count_by_status(store)
    by_category = article_count_by_category(store)

    print("=== Status ===")
    print(f"Sources: {len(sources)} active (per {config.sources.yaml_path})")
    print(f"Articles: {total_articles} active (non-irrelevant)")
    print(f"  by status: {by_status}")
    print(f"  by category: {by_category}")


def cmd_sources(config, args) -> None:
    from radar_pipeline.sources import catalog

    sources = catalog.list_sources(config.sources.yaml_path, active_only=args.active_only)
    for s in sources:
        status = "active" if s.active else "disabled"
        print(f"  [{status}] {s.source_id}: {s.name} ({s.source_type}/{s.category})")


def cmd_validate_feeds(config, metrics: MetricsCollector) -> None:
    from radar_pipeline.sources.feedparser import fetch_and_parse
    from radar_pipeline.sources.collector import _feed_url

    async def _validate():
        from radar_pipeline.sources.feedparser import FeedFetchError
        from radar_pipeline.sources.ratelimit import HostBlockedError

        sources = [dataclasses.asdict(s) for s in _active_sources(config)]
        import httpx
        results = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for s in sources:
                url = _feed_url(s, "normal", config.collect.days_back, config.collect.backfill_days, config.collect.hours_back)
                if s["feed_type"] == "linkedin_company":
                    # httpx probing is meaningless against a browser-rendered
                    # LinkedIn page; use --collect to actually validate these.
                    results.append((s["name"], -3, url, ""))
                    continue
                try:
                    items = await fetch_and_parse(
                        url, client, max_retries=0,
                    )
                    results.append((s["name"], len(items), url, ""))
                except (FeedFetchError, HostBlockedError) as exc:
                    results.append((s["name"], -1, url, str(exc)[:120]))
        return results

    run = metrics.start("validate-feeds")
    results = asyncio.run(_validate())
    run.finish(total=len(results))
    for name, count, url, err in results:
        if count == -3:
            print(f"  [SKIP] {name}: linkedin_company (browser-based, use --collect) (url={url[:80]}...)")
        elif count >= 0:
            print(f"  [OK]   {name}: {count} items (url={url[:80]}...)")
        else:
            print(f"  [FAIL] {name}: {err} (url={url[:80]}...)")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="radar-pipeline",
        description="Radar Aftermarket Pipeline — news aggregation with AI, on DynamoDB",
    )
    p.add_argument(
        "--config", default="configs/radar.yaml",
        help="Path to radar.yaml config (default: configs/radar.yaml)",
    )
    p.add_argument("--collect", action="store_true", help="Poll all active sources")
    p.add_argument("--classify", action="store_true", help="Re-run keyword classifier")
    p.add_argument("--fetch", action="store_true", help="Crawl articles that have a URL")
    p.add_argument("--dedup", action="store_true", help="Run dedup on pending articles")
    p.add_argument("--summarize", action="store_true", help="Summarize pending articles via LLM")
    p.add_argument(
        "--retry-failed", action="store_true",
        help="Reset articles whose summarize call failed back to pending, so the "
        "next --summarize retries them (a failed article is never retried "
        "automatically otherwise)",
    )
    p.add_argument("--status", action="store_true", help="Print source and article statistics")
    p.add_argument(
        "--validate-feeds", action="store_true",
        help="Probe every active source URL (no inserts)",
    )
    p.add_argument(
        "--backfill", action="store_true",
        help="Scope --collect to config.collect.backfill_days (a wide manual "
        "catch-up range) instead of the normal narrow schedule window",
    )
    p.add_argument(
        "--companies", default=None,
        help="Comma-separated company tags to scope --collect to (overrides "
        "collect.companies in the config); omit for every active source",
    )
    p.add_argument(
        "--hours-back", type=int, default=None,
        help="Override collect.hours_back for this run, e.g. 3 for the "
        "every-3-hours schedule (normal mode only)",
    )

    sub = p.add_subparsers(dest="sources_action", help="Source catalog inspection")
    sub.add_parser("sources-list", help="List sources from the YAML catalog").add_argument(
        "--active-only", action="store_true"
    )

    return p


def main() -> None:
    load_dotenv()
    setup_logging()

    parser = _build_parser()
    args = parser.parse_args()

    cfg_path = Path(args.config) if args.config else Path("configs/radar.yaml")
    config = load_radar_config(cfg_path)

    if args.companies is not None:
        config.collect.companies = [c.strip() for c in args.companies.split(",") if c.strip()]
    if args.hours_back is not None:
        config.collect.hours_back = args.hours_back

    metrics = MetricsCollector()

    stage_flags = [
        args.collect, args.classify, args.fetch,
        args.dedup, args.retry_failed, args.summarize, args.status,
        args.validate_feeds, args.sources_action is not None,
    ]
    run_default = not any(stage_flags)

    if run_default:
        args.collect = args.fetch = args.dedup = args.summarize = True

    mode = "backfill" if args.backfill else "normal"

    # sources-list and --validate-feeds only ever touch the YAML catalog /
    # outbound feed URLs — never the store — so they shouldn't need a
    # DynamoDB connection (or a region/credentials) at all. Connect lazily,
    # only for the stages that actually read or write articles.
    needs_store = (
        args.collect or args.classify or args.fetch or args.dedup
        or args.retry_failed or args.summarize or args.status
    )
    store = None
    if needs_store:
        # RADAR_TABLE_NAME lets the same image target a different table per
        # environment (e.g. a dev table) via the ECS task definition's
        # environment variables, without rebuilding the image or shipping a
        # different configs/radar.yaml. Region needs no equivalent override
        # here — boto3 already resolves AWS_DEFAULT_REGION on its own when
        # region_name isn't passed explicitly.
        table_name = os.environ.get("RADAR_TABLE_NAME") or config.db.table_name
        store = connect(table_name, config.db.region_name or None, config.db.endpoint_url or None)
        if config.db.endpoint_url:
            # Local/dev endpoint (dynamodb-local, moto) — create the table if
            # it's missing. Against real AWS the table is created once via
            # the console/CLI steps in DEPLOYMENT.md, not by the app on every
            # boot.
            ensure_table(store)

    try:
        if args.collect:
            cmd_collect(store, config, metrics, mode=mode)

        if args.classify:
            cmd_classify(store, config, metrics)

        if args.fetch:
            cmd_fetch(store, config, metrics)

        if args.dedup:
            cmd_dedup(store, config, metrics)

        if args.retry_failed:
            cmd_retry_failed(store, config, metrics)

        if args.summarize:
            cmd_summarize(store, config, metrics)

        if args.status:
            cmd_status(store, config, metrics)

        if args.validate_feeds:
            cmd_validate_feeds(config, metrics)

        if args.sources_action == "sources-list":
            args.active_only = getattr(args, "active_only", False)
            cmd_sources(config, args)

    except Exception as exc:
        logger.exception("Pipeline failed")
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        print(metrics.summary())


if __name__ == "__main__":
    main()
