"""Tests for the main pipeline entry point.

These exercise the `_parse_args` -> stage orchestration path without making
real network or LLM calls.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from ai_crawling_pipeline import _parse_args
from ai_crawling_pipeline.config import (
    LLMSettings,
    SummarySettings,
)


def test_parse_args_default_runs_all_three_stages(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["prog"])
    config_path, run_crawl, run_summarize, run_dedup, run_resummarize = _parse_args([])
    assert run_crawl is True
    assert run_summarize is True
    assert run_dedup is True
    assert run_resummarize is False
    assert config_path.name == "default.yaml"


def test_parse_args_each_flag_individually(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["prog", "--crawl"])
    _, crawl, summarize, dedup, resummarize = _parse_args(["--crawl"])
    assert crawl and not summarize and not dedup
    assert not resummarize

    monkeypatch.setattr("sys.argv", ["prog", "--dedup"])
    _, crawl, summarize, dedup, resummarize = _parse_args(["--dedup"])
    assert dedup and not crawl and not summarize
    assert not resummarize

    monkeypatch.setattr("sys.argv", ["prog", "--summarize"])
    _, crawl, summarize, dedup, resummarize = _parse_args(["--summarize"])
    assert summarize and not crawl and not dedup
    assert not resummarize

    monkeypatch.setattr("sys.argv", ["prog", "--summarize", "--resummarize"])
    _, _, summarize, _, resummarize = _parse_args(["--summarize", "--resummarize"])
    assert summarize and resummarize


def test_parse_args_unknown_flag_errors() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--unknown"])


def test_summarize_from_db_called_with_two_positional_args() -> None:
    """Regression for the bug where the stage orchestrator called
    `summarize_from_db((settings, db_path))` (single tuple arg) instead
    of `summarize_from_db(settings, db_path)`. The correct call shape
    is two positional args.
    """
    from ai_crawling_pipeline.summary import summarize_from_db

    settings = SummarySettings(
        llm=LLMSettings(
            base_url="https://example.invalid", api_key_env="OPENAI_API_KEY"
        )
    )

    with tempfile.TemporaryDirectory() as tmp:
        bad_db = Path(tmp) / "not_a_db"
        bad_db.mkdir()  # a directory, not a file -> sqlite3 raises

        # Calling with the correct 2-arg shape should fail with a DB error,
        # NOT a TypeError about the signature.
        with pytest.raises(Exception) as exc_info:
            asyncio.run(summarize_from_db(settings, bad_db))
        assert not isinstance(exc_info.value, TypeError), (
            f"summarize_from_db called with wrong shape: {exc_info.value}"
        )


def test_summarize_from_db_rejects_missing_db() -> None:
    """summarize_from_db should fail fast when the DB file doesn't exist,
    instead of creating a dim-mismatched schema (see OCR review #4)."""
    from ai_crawling_pipeline.summary import summarize_from_db

    settings = SummarySettings(
        llm=LLMSettings(
            base_url="https://example.invalid", api_key_env="OPENAI_API_KEY"
        )
    )
    with tempfile.TemporaryDirectory() as tmp:
        nonexistent = Path(tmp) / "missing.db"
        with pytest.raises(SystemExit):
            asyncio.run(summarize_from_db(settings, nonexistent))
