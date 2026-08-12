"""CLI entry point for the Radar Aftermarket Pipeline."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from radar_pipeline.config import load_radar_config
from radar_pipeline.db import (
    article_count_by_category,
    article_count_by_status,
    connect,
    init_schema,
    run_migrations,
)
from radar_pipeline.observability.logging import setup_logging
from radar_pipeline.observability.metrics import MetricsCollector

logger = logging.getLogger("radar_pipeline")


def cmd_collect(con, config, metrics: MetricsCollector) -> None:
    from radar_pipeline.sources.catalog import seed_from_yaml
    from radar_pipeline.sources.collector import collect_once

    if config.sources.auto_seed:
        seed_from_yaml(con, config.sources.yaml_path)

    run = metrics.start("collect")
    print(f"Collecting from {config.sources.yaml_path}...")
    stats = asyncio.run(collect_once(con, config.collect))
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


def cmd_fetch(con, config, metrics: MetricsCollector) -> None:
    from radar_pipeline.fetch.crawl import fetch_articles

    if config.fetch is None:
        print("No fetch section in config; skipping.")
        return

    run = metrics.start("fetch")
    print("Fetching articles...")
    result = asyncio.run(fetch_articles(con, config.fetch))
    run.finish(**result)
    print(
        f"Done: {result['total']} pending, {result['fetched']} fetched, "
        f"{result['failed']} failed"
    )


def cmd_dedup(con, config, metrics: MetricsCollector) -> None:
    from radar_pipeline.dedup.layers import run_dedup

    if config.dedup is None:
        print("No dedup section in config; skipping.")
        return

    run = metrics.start("dedup")
    print("Running dedup...")
    result = asyncio.run(run_dedup(con, config.dedup))
    run.finish(**result)
    print(
        f"Done: {result['total']} processed, {result['new']} new, "
        f"{result['duplicates']} duplicates"
    )


def cmd_summarize(con, config, metrics: MetricsCollector) -> None:
    from radar_pipeline.summarize.pipeline import run_summarize

    if config.summarize is None:
        print("No summarize section in config; skipping.")
        return

    run = metrics.start("summarize")
    print(f"Summarizing articles (model={config.summarize.llm.model})...")
    result = asyncio.run(run_summarize(con, config.summarize))
    run.finish(**result)
    print(
        f"Done: {result['total']} pending, {result['summarized']} summarized, "
        f"{result['irrelevant']} irrelevant, {result['failed']} failed"
    )


def cmd_classify(con, config, metrics: MetricsCollector) -> None:
    from radar_pipeline.classify.rules import classify_article

    run = metrics.start("classify")
    updated = 0
    from radar_pipeline.db import pending_articles
    articles = pending_articles(con)
    print(f"Re-classifying {len(articles)} pending articles...")
    for a in articles:
        event_type, alert_level, is_launch = classify_article(
            a["title"], a["action_description"]
        )
        con.execute(
            "UPDATE articles SET event_type = ?, alert_level = ?, is_launch = ? WHERE id = ?",
            (event_type, alert_level, int(is_launch), a["id"]),
        )
        updated += 1
    con.commit()
    run.finish(updated=updated)
    print(f"Done: {updated} articles re-classified")


def cmd_status(con, config, metrics: MetricsCollector) -> None:
    from radar_pipeline.db import (
        article_count,
        article_count_by_category,
        article_count_by_status,
        get_all_sources,
    )

    sources = get_all_sources(con)
    active = [s for s in sources if s["active"]]
    total_articles = article_count(con)
    by_status = article_count_by_status(con)
    by_category = article_count_by_category(con)

    stale = con.execute(
        "SELECT source_id, name, source_type, last_success FROM vw_stale_sources"
    ).fetchall()

    print("=== Status ===")
    print(f"Sources: {len(active)} active / {len(sources)} total")
    print(f"Articles: {total_articles} active (non-irrelevant)")
    print(f"  by status: {by_status}")
    print(f"  by category: {by_category}")
    if stale:
        print(f"Stale sources (>7 days since last success): {len(stale)}")
        for s in stale:
            print(f"  - {s['name']} ({s['source_type']}) last: {s['last_success']}")


def cmd_sources(con, args, config) -> None:
    from radar_pipeline.sources import catalog

    if args.sources_action == "list":
        sources = catalog.list_sources(con, active_only=args.active_only)
        for s in sources:
            status = "active" if s["active"] else "disabled"
            print(f"  [{status}] {s['source_id']}: {s['name']} ({s['source_type']}/{s['category']})")
    elif args.sources_action == "enable":
        ok = catalog.set_active(con, args.source_id, True)
        print(f"{'Enabled' if ok else 'Not found'}: {args.source_id}")
    elif args.sources_action == "disable":
        ok = catalog.set_active(con, args.source_id, False)
        print(f"{'Disabled' if ok else 'Not found'}: {args.source_id}")
    elif args.sources_action == "import-yaml":
        count = catalog.seed_from_yaml(con, config.sources.yaml_path)
        print(f"Imported {count} sources from {config.sources.yaml_path}")


def cmd_validate_feeds(con, config, metrics: MetricsCollector) -> None:
    from radar_pipeline.db import get_active_sources
    from radar_pipeline.sources.feedparser import fetch_and_parse
    from radar_pipeline.sources.collector import _feed_url

    async def _validate():
        from radar_pipeline.sources.feedparser import FeedFetchError
        from radar_pipeline.sources.ratelimit import HostBlockedError

        sources = get_active_sources(con)
        import httpx
        results = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for s in sources:
                url = _feed_url(s, "normal", config.collect.days_back, config.collect.backfill_days)
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
        description="Radar Aftermarket Pipeline — local-first news aggregation with AI",
    )
    p.add_argument(
        "--config", default="configs/radar.yaml",
        help="Path to radar.yaml config (default: configs/radar.yaml)",
    )
    p.add_argument("--collect", action="store_true", help="Poll all active sources")
    p.add_argument("--classify", action="store_true", help="Re-run keyword classifier")
    p.add_argument("--fetch", action="store_true", help="Crawl articles that have a URL")
    p.add_argument("--dedup", action="store_true", help="Run 5-layer dedup on pending articles")
    p.add_argument("--summarize", action="store_true", help="Summarize pending articles via LLM")
    p.add_argument("--status", action="store_true", help="Print source and article statistics")
    p.add_argument(
        "--validate-feeds", action="store_true",
        help="Probe every active source URL (no inserts)",
    )

    sub = p.add_subparsers(dest="sources_action", help="Source catalog management")
    sub.add_parser("sources-list", help="List all sources").add_argument(
        "--active-only", action="store_true"
    )
    sub.add_parser("sources-import-yaml", help="Re-import from YAML")
    e = sub.add_parser("sources-enable", help="Enable a source")
    e.add_argument("source_id", help="Source ID to enable")
    d = sub.add_parser("sources-disable", help="Disable a source")
    d.add_argument("source_id", help="Source ID to disable")

    return p


def main() -> None:
    load_dotenv()
    setup_logging()

    parser = _build_parser()
    args = parser.parse_args()

    cfg_path = Path(args.config) if args.config else Path("configs/radar.yaml")
    config = load_radar_config(cfg_path)

    con = connect(config.db.path)
    try:
        init_schema(con, dim=config.db.embedding_dim)
        run_migrations(con)
    except Exception as exc:
        logger.error("Schema initialization failed: %s", exc)
        sys.exit(1)

    metrics = MetricsCollector()

    stage_flags = [
        args.collect, args.classify, args.fetch,
        args.dedup, args.summarize, args.status,
        args.validate_feeds, args.sources_action is not None,
    ]
    run_default = not any(stage_flags)

    if run_default:
        args.collect = args.fetch = args.dedup = args.summarize = True

    try:
        if args.collect:
            cmd_collect(con, config, metrics)

        if args.classify:
            cmd_classify(con, config, metrics)

        if args.fetch:
            cmd_fetch(con, config, metrics)

        if args.dedup:
            cmd_dedup(con, config, metrics)

        if args.summarize:
            cmd_summarize(con, config, metrics)

        if args.status:
            cmd_status(con, config, metrics)

        if args.validate_feeds:
            cmd_validate_feeds(con, config, metrics)

        if args.sources_action == "sources-list":
            args.active_only = getattr(args, "active_only", False)
            cmd_sources(con, args, config)
        elif args.sources_action in ("sources-enable", "sources-disable", "sources-import-yaml"):
            cmd_sources(con, args, config)

    except Exception as exc:
        logger.exception("Pipeline failed")
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        print(metrics.summary())
        con.close()


if __name__ == "__main__":
    main()
