"""Crawl targets loaded from a YAML config and save their markdown."""

from __future__ import annotations

import asyncio
from pathlib import Path

from crawl4ai import AsyncWebCrawler, BrowserConfig

from ai_crawling_pipeline.anti_block import crawl_with_retry
from ai_crawling_pipeline.config import AntiBlockSettings, CrawlConfig, Target, load_config


def _build_browser_config(cfg: CrawlConfig) -> BrowserConfig:
    kwargs = dict(cfg.browser.kwargs)
    if cfg.anti_block and cfg.anti_block.enabled and cfg.anti_block.enable_stealth:
        kwargs.setdefault("enable_stealth", True)
    return BrowserConfig(**kwargs) if kwargs else BrowserConfig(headless=True)


def _resolve_anti_block(cfg: CrawlConfig, target: Target) -> AntiBlockSettings | None:
    if cfg.anti_block is None and target.anti_block is None:
        return None
    base = cfg.anti_block or AntiBlockSettings()
    return base.merged_with(target.anti_block)


def _save_markdown(target: Target, markdown: str, title: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{target.slug}.md"
    header = f"# {title or target.url}\n\nURL: {target.url}\n\n"
    out_path.write_text(header + markdown, encoding="utf-8")
    return out_path


async def crawl_from_config(cfg: CrawlConfig) -> None:
    """Run all targets in `cfg` sequentially, sharing one browser session."""
    browser_config = _build_browser_config(cfg)
    print(f"Config: {cfg.to_summary()}")
    if cfg.anti_block and cfg.anti_block.enabled:
        ab = cfg.anti_block
        print(
            f"Anti-block: enabled max_retries={ab.max_retries} "
            f"backoff={ab.backoff_seconds}s magic={ab.magic} "
            f"stealth={ab.enable_stealth} rotate_ua={ab.rotate_user_agent} "
            f"ua_pool={len(ab.user_agents)} min_chars={ab.min_content_chars}"
        )

    summary: list[dict] = []

    async with AsyncWebCrawler(config=browser_config) as crawler:
        for target in cfg.targets:
            ab = _resolve_anti_block(cfg, target)
            print(f"-> {target.slug}: {target.url}")
            result, exc, attempts, log = await crawl_with_retry(
                crawler=crawler,
                target=target,
                base_run_config=cfg.defaults,
                settings=ab,
            )

            if exc is not None or result is None or not result.success:
                err = exc or (result.error_message if result else "no result")
                print(f"   FAILED after {attempts} attempt(s): {err}")
                if log:
                    for line in log:
                        print(f"     - {line}")
                summary.append({"slug": target.slug, "ok": False, "error": str(err), "attempts": attempts})
                continue

            markdown = result.markdown or ""
            if not markdown.strip():
                err = "empty markdown (blocked?)"
                print(f"   FAILED after {attempts} attempt(s): {err}")
                if log:
                    for line in log:
                        print(f"     - {line}")
                summary.append({"slug": target.slug, "ok": False, "error": err, "attempts": attempts})
                continue

            title = result.metadata.get("title", target.url) if result.metadata else target.url
            out_path = _save_markdown(target, markdown, title, cfg.output.dir)
            chars = len(markdown)
            print(f"   OK ({chars} chars) -> {out_path}")
            summary.append({
                "slug": target.slug,
                "ok": True,
                "title": title,
                "chars": chars,
                "path": str(out_path),
                "attempts": attempts,
            })

    print("\nSummary:")
    for row in summary:
        if row["ok"]:
            print(f"  [ok]   {row['slug']}: {row['chars']} chars (attempts={row['attempts']}) -> {row['path']}")
        else:
            print(f"  [fail] {row['slug']}: {row['error']} (attempts={row['attempts']})")


async def crawl_all(config_path: str | Path | None = None) -> None:
    """Load config (default if path is None) and run it."""
    cfg = load_config(config_path) if config_path is not None else load_config()
    await crawl_from_config(cfg)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(crawl_all(path))
