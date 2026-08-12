"""ai-crawling-pipeline package."""

import asyncio
import os
import sys
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

from ai_crawling_pipeline.config import DEFAULT_CONFIG_PATH, load_config
from ai_crawling_pipeline.crawl import crawl_from_config
from ai_crawling_pipeline.dedup import dedup_all
from ai_crawling_pipeline.summary import summarize_all, summarize_from_db


def _parse_config_arg(arg: str, argv: list[str], idx: int) -> Path:
    """Resolve a --config <path> or --config=<path> form; advance the index for the spaced form."""
    if arg == "--config":
        if idx + 1 >= len(argv):
            raise SystemExit("--config requires a path argument")
        return Path(argv[idx + 1])
    if arg.startswith("--config="):
        return Path(arg.split("=", 1)[1])
    raise AssertionError(f"not a --config form: {arg}")


def _parse_args(argv: list[str]) -> tuple[Path, bool, bool, bool, bool]:
    """Parse argv into (config_path, run_crawl, run_summarize, run_dedup, run_resummarize).

    Recognized flags: --crawl, --summarize, --dedup, --resummarize, --config <path>,
    --config=<path>. Any other argument starting with '-' is a hard error to prevent
    silent fall-through to the default config (e.g. a typo like --craw1).
    """
    run_crawl = False
    run_summarize = False
    run_dedup = False
    run_resummarize = False
    config_path: Path | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--crawl":
            run_crawl = True
        elif arg == "--summarize":
            run_summarize = True
        elif arg == "--dedup":
            run_dedup = True
        elif arg == "--resummarize":
            run_resummarize = True
        elif arg == "--config" or arg.startswith("--config="):
            if config_path is not None:
                raise SystemExit("--config specified more than once")
            config_path = _parse_config_arg(arg, argv, i)
            if arg == "--config":
                i += 1  # consume the value
        elif arg.startswith("-"):
            raise SystemExit(f"Unknown flag(s): {arg}")
        else:
            if config_path is None:
                config_path = Path(arg)
            else:
                raise SystemExit(f"Unexpected positional argument: {arg}")
        i += 1

    if not run_crawl and not run_summarize and not run_dedup:
        run_crawl = True
        run_dedup = True
        run_summarize = True

    if config_path is None:
        env = os.environ.get("CONFIG_PATH")
        config_path = Path(env) if env else DEFAULT_CONFIG_PATH

    return config_path, run_crawl, run_summarize, run_dedup, run_resummarize


def main() -> None:
    """Pipeline entry point.

    Modes (combinable):
      - --crawl        -> crawl only
      - --dedup        -> dedup/filteration only (reads outputs/raw/*.md)
      - --summarize    -> summarize only (DB-driven when dedup is configured)
      - --resummarize  -> with --summarize, force re-summarize all primary items
                         (clears `summarized_at` in the dedup DB)
      - no flag        -> run full pipeline: crawl then dedup then summarize
    """
    load_dotenv()
    config_path, run_crawl, run_summarize, run_dedup, run_resummarize = _parse_args(
        sys.argv[1:]
    )

    cfg = load_config(config_path)

    if run_summarize and cfg.summary is None:
        raise SystemExit("No 'summary' section found in config; cannot run summarization.")
    if run_dedup and cfg.dedup is None:
        raise SystemExit("No 'dedup' section found in config; cannot run dedup.")
    if run_resummarize and not run_summarize:
        raise SystemExit("--resummarize only makes sense with --summarize.")
    if run_resummarize and (cfg.dedup is None or not run_dedup):
        # The --resummarize flag operates on the dedup DB. The DB only
        # exists / is up-to-date when dedup is configured and either ran
        # in this invocation or ran in a previous one. Without dedup the
        # flag has no effect and would silently no-op, which is confusing.
        raise SystemExit(
            "--resummarize requires a 'dedup' section in the config. "
            "Run the full pipeline (no --resummarize) at least once first."
        )

    # `summarize_from_db` should only be used when dedup actually ran in
    # this invocation. Otherwise we summarize every .md in input_dir, even
    # if a stale dedup DB exists on disk from a previous run.
    summarize_fn: Callable = summarize_all
    summarize_kwargs: dict = {}
    if run_dedup and cfg.dedup is not None and cfg.summary is not None:
        summarize_fn = summarize_from_db
        summarize_kwargs = {"force_resummarize": run_resummarize}
        summarize_args: tuple = (cfg.summary, cfg.dedup.db_path)
    else:
        # summarize_all doesn't take force_resummarize; ignore the flag in
        # the file-globbing path (it always re-summarizes anyway, which
        # matches the README regeneration use case).
        summarize_args = (cfg.summary,)

    stages: list[tuple[str, Callable, tuple, dict]] = []
    if run_crawl:
        stages.append(("crawl", crawl_from_config, (cfg,), {}))
    if run_dedup:
        stages.append(("dedup", dedup_all, (cfg.dedup,), {}))
    if run_summarize:
        stages.append(("summarize", summarize_fn, summarize_args, summarize_kwargs))

    for name, fn, args, kwargs in stages:
        print(f"\n=== {name} ===")
        try:
            asyncio.run(fn(*args, **kwargs))
        except Exception:
            print(f"Stage '{name}' failed:", file=sys.stderr)
            raise


if __name__ == "__main__":
    main()

