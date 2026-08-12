"""Summarize raw markdown files via an OpenAI-compatible LLM."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from openai import AsyncOpenAI
from openai import APIError, APIConnectionError, AuthenticationError

from ai_crawling_pipeline.config import SummarySettings

logger = logging.getLogger(__name__)

DEFAULT_SUMMARIZE_CONCURRENCY = 5


def _resolve_api_key(api_key_env: str) -> str:
    key = os.environ.get(api_key_env)
    if not key:
        raise SystemExit(
            f"Missing API key: set the {api_key_env} environment variable "
            f"(e.g. in .env or via `export {api_key_env}=...`)."
        )
    return key


def _read_input(path: Path, max_chars: int) -> tuple[str, bool]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[...truncated...]", True
    return text, False


def _extract_title(raw_text: str) -> str:
    for line in raw_text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _build_summary_header(slug: str, title: str, source_url: str) -> str:
    parts = [f"# Summary: {title}" if title else f"# Summary: {slug}"]
    if source_url:
        parts.append(f"URL: {source_url}")
    return "\n".join(parts) + "\n\n"


async def _summarize_one(
    client: AsyncOpenAI,
    settings: SummarySettings,
    raw_path: Path,
) -> tuple[bool, str, int | None, str | None]:
    text, _ = _read_input(raw_path, settings.max_input_chars)
    title = _extract_title(text)
    source_url = ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("URL: "):
            source_url = s[5:].strip()
            break

    user_content = settings.user_prompt_template.format(content=text)
    messages = [
        {"role": "system", "content": settings.system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        resp = await client.chat.completions.create(
            model=settings.llm.model,
            messages=messages,
            temperature=settings.llm.temperature,
            max_tokens=settings.llm.max_tokens,
        )
    except (AuthenticationError, APIError, APIConnectionError) as exc:
        return False, title, None, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error from LLM API")
        return False, title, None, f"{type(exc).__name__}: {exc}"

    if not resp.choices:
        return False, title, None, "empty choices in LLM response"
    choice = resp.choices[0]
    summary_text = (choice.message.content or "").strip()
    if not summary_text:
        finish_reason = getattr(choice, "finish_reason", None) or "unknown"
        return False, title, None, f"empty response from LLM (finish_reason={finish_reason})"

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = settings.output_dir / raw_path.name
    header = _build_summary_header(raw_path.stem, title, source_url)
    out_path.write_text(header + summary_text + "\n", encoding="utf-8")
    return True, title, len(summary_text), str(out_path)


async def summarize_all(settings: SummarySettings, concurrency: int = DEFAULT_SUMMARIZE_CONCURRENCY) -> None:
    if not settings.input_dir.exists():
        raise SystemExit(f"Input directory does not exist: {settings.input_dir}")

    files = sorted(settings.input_dir.glob("*.md"))
    if not files:
        print(f"No .md files found in {settings.input_dir}")
        return

    api_key = _resolve_api_key(settings.llm.api_key_env)
    client = AsyncOpenAI(base_url=settings.llm.base_url, api_key=api_key)

    print(
        f"Summarize: input={settings.input_dir} output={settings.output_dir} "
        f"model={settings.llm.model} base_url={settings.llm.base_url} "
        f"files={len(files)} concurrency={concurrency}"
    )

    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded_summarize(f: Path) -> tuple[bool, str, int | None, str | None]:
        async with semaphore:
            print(f"-> {f.stem}")
            return await _summarize_one(client, settings, f)

    results = await asyncio.gather(
        *(_bounded_summarize(f) for f in files), return_exceptions=True
    )

    summary: list[dict] = []
    for f, result in zip(files, results):
        if isinstance(result, Exception):
            logger.exception("Summarize failed for %s", f.stem)
            print(f"   FAILED: {type(result).__name__}: {result}")
            summary.append({"slug": f.stem, "ok": False, "error": f"{type(result).__name__}: {result}"})
            continue
        ok, title, chars, info = result
        if ok:
            print(f"   OK ({chars} chars) -> {info}")
            summary.append({"slug": f.stem, "ok": True, "title": title, "chars": chars, "path": info})
        else:
            print(f"   FAILED: {info}")
            summary.append({"slug": f.stem, "ok": False, "error": info})

    print("\nSummary:")
    for row in summary:
        if row["ok"]:
            print(f"  [ok]   {row['slug']}: {row['chars']} chars -> {row['path']}")
        else:
            print(f"  [fail] {row['slug']}: {row['error']}")


async def summarize_from_db(
    settings: SummarySettings,
    db_path: str | os.PathLike,
    concurrency: int = DEFAULT_SUMMARIZE_CONCURRENCY,
    force_resummarize: bool = False,
) -> None:
    """Summarize items marked `new` in the dedup SQLite database.

    Used when the pipeline runs the dedup stage between crawl and summarize:
    the DB knows which items are primary (new) and which are duplicates
    (skipped) so we don't waste LLM calls on near-identical content.

    By default, items that have already been summarized in a prior run
    (have a non-NULL `summarized_at`) are skipped so the pipeline is
    idempotent and does not re-summarize unchanged content. Pass
    `force_resummarize=True` (the `--resummarize` CLI flag) to clear
    `summarized_at` for every primary and re-summarize them all; this is
    the right knob to use when the LLM model or the summary prompt
    changes.
    """
    # Local import to avoid a circular dep at module load.
    from ai_crawling_pipeline import db as dbmod

    if not Path(db_path).exists():
        raise SystemExit(
            f"Dedup DB does not exist at {db_path}; run --dedup first."
        )

    con = dbmod.connect(db_path)
    try:
        # The dedup stage owns schema creation. We only verify the table
        # is present so we don't accidentally create a dim-mismatched
        # items_vec table by calling init_schema here.
        if not dbmod.has_items_table(con):
            raise SystemExit(
                f"Dedup DB at {db_path} is empty; run --dedup first."
            )
        if force_resummarize:
            reset = dbmod.clear_summarized(con)
            print(f"Resummarize: cleared summarized_at for {reset} primary item(s).")
        rows = list(dbmod.unsummarized_items(con))
    finally:
        con.close()

    if not rows:
        print(f"No unsummarized items in DB {db_path}; nothing to do.")
        return

    api_key = _resolve_api_key(settings.llm.api_key_env)
    client = AsyncOpenAI(base_url=settings.llm.base_url, api_key=api_key)
    print(
        f"Summarize (from DB): db={db_path} output={settings.output_dir} "
        f"model={settings.llm.model} new_items={len(rows)} concurrency={concurrency}"
    )

    # We need the same connection later to write summarized_at per row.
    # Reopen in RW mode (connect() returns a RW connection by default).
    con = dbmod.connect(db_path)
    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded_summarize(row):
        async with semaphore:
            p = Path(row["content_path"])
            print(f"-> {p.stem}")
            return await _summarize_one(client, settings, p)

    # return_exceptions=True so a single failure (e.g. missing file)
    # doesn't abort the rest of the batch.
    results = await asyncio.gather(
        *(_bounded_summarize(r) for r in rows), return_exceptions=True
    )

    summary: list[dict] = []
    successes = 0
    try:
        for row, result in zip(rows, results):
            slug = Path(row["content_path"]).stem
            if isinstance(result, Exception):
                logger.exception("Summarize failed for %s", slug)
                print(f"   FAILED: {type(result).__name__}: {result}")
                summary.append(
                    {
                        "slug": slug,
                        "ok": False,
                        "error": f"{type(result).__name__}: {result}",
                    }
                )
                # Leave summarized_at NULL so the item is retried next run.
                continue
            ok, title, chars, info = result
            if ok:
                dbmod.mark_summarized(con, int(row["rowid"]), ts=time.time())
                successes += 1
                print(f"   OK ({chars} chars) -> {info}")
                summary.append(
                    {
                        "slug": slug,
                        "ok": True,
                        "title": title,
                        "chars": chars,
                        "path": info,
                    }
                )
            else:
                print(f"   FAILED: {info}")
                summary.append({"slug": slug, "ok": False, "error": info})
                # Leave summarized_at NULL so the item is retried.
    finally:
        con.close()

    print("\nSummary:")
    for r in summary:
        if r["ok"]:
            print(f"  [ok]   {r['slug']}: {r['chars']} chars -> {r['path']}")
        else:
            print(f"  [fail] {r['slug']}: {r['error']}")
    print(f"  summarized: {successes}/{len(rows)}")


if __name__ == "__main__":
    import sys

    from ai_crawling_pipeline.config import load_config

    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    cfg = load_config(config_path) if config_path is not None else load_config()
    if cfg.summary is None:
        raise SystemExit("No 'summary' section found in config.")
    asyncio.run(summarize_all(cfg.summary))
