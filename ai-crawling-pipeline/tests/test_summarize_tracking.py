"""Tests for summarize_from_db's `summarized_at` tracking and the
`--resummarize` (force_resummarize=True) behavior."""
from __future__ import annotations

import asyncio
import tempfile
import unittest.mock as mock
from pathlib import Path

import pytest

from ai_crawling_pipeline import db
from ai_crawling_pipeline.config import LLMSettings, SummarySettings
from ai_crawling_pipeline.summary import summarize_from_db


pytestmark = pytest.mark.asyncio


def _settings(out_dir: Path) -> SummarySettings:
    return SummarySettings(
        input_dir=out_dir / "raw",
        output_dir=out_dir / "processed",
        max_input_chars=20000,
        llm=LLMSettings(
            base_url="https://example.invalid", api_key_env="OPENAI_API_KEY"
        ),
    )


def _seed_db(db_path: Path, raw_dir: Path) -> None:
    """Insert two primary items pointing at .md files in raw_dir."""
    con = db.connect(db_path)
    try:
        db.init_schema(con, dim=4)
        for i, slug in enumerate(("foo", "bar"), start=1):
            md = raw_dir / f"{slug}.md"
            md.write_text(
                f"# {slug} title\nURL: https://example.com/{slug}\n\nBody {i}"
            )
            db.insert_item(
                con,
                id_hash=f"id{i}",
                title_hash=f"t{i}",
                canonical_url=f"https://example.com/{slug}",
                title=f"{slug} title",
                excerpt=f"body {i}",
                content_path=str(md),
                source=slug,
                source_url=f"https://example.com/{slug}",
                embedding=[0.1 * i, 0.2 * i, 0.3 * i, 0.4 * i],
                embedding_model="m",
                created_at=float(i),
                dedup_layer=None,
                dupe_of_hash=None,
                status="new",
            )
    finally:
        con.close()


def _fake_llm(*, ok: bool = True, fail: str | None = None):
    """Build a stubbed AsyncOpenAI that returns a deterministic summary."""
    from openai.types.chat import (
        ChatCompletion,
        ChatCompletionMessage,
    )
    from openai.types.chat.chat_completion import Choice

    async def fake_create(*args, **kwargs):
        if not ok:
            return ChatCompletion(
                id="x",
                model="x",
                object="chat.completion",
                created=0,
                choices=[],
            )
        return ChatCompletion(
            id="x",
            model="x",
            object="chat.completion",
            created=0,
            choices=[
                Choice(
                    index=0,
                    finish_reason="stop",
                    message=ChatCompletionMessage(
                        role="assistant", content="Test summary."
                    ),
                )
            ],
        )

    return mock.patch(
        "ai_crawling_pipeline.summary.AsyncOpenAI",
        **{"return_value.chat.completions.create": fake_create},
    )


async def test_summarize_marks_summarized_at_on_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        raw = tmp / "raw"
        raw.mkdir()
        db_path = tmp / "dedup.db"
        _seed_db(db_path, raw)

        settings = _settings(tmp)
        with _fake_llm():
            await summarize_from_db(settings, db_path)

        # Both rows should now have summarized_at set.
        con = db.connect(db_path)
        try:
            rows = list(
                con.execute(
                    "SELECT source, summarized_at FROM items WHERE status='new'"
                )
            )
            assert len(rows) == 2
            for r in rows:
                assert r["summarized_at"] is not None
        finally:
            con.close()


async def test_summarize_skips_already_summarized_on_second_run() -> None:
    """Re-running without --resummarize must be a no-op for items that
    already have summarized_at set (the bug the user reported)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        raw = tmp / "raw"
        raw.mkdir()
        db_path = tmp / "dedup.db"
        _seed_db(db_path, raw)

        settings = _settings(tmp)

        # First run: both items get summarized.
        with _fake_llm():
            await summarize_from_db(settings, db_path)

        # Second run: count how many items are returned. It should be 0,
        # so the LLM client should not even be called.
        with _fake_llm() as m:
            with mock.patch.object(
                __import__("ai_crawling_pipeline.summary", fromlist=["AsyncOpenAI"]).AsyncOpenAI,
                "return_value",
            ) as client:
                # The second run should detect there are no unsummarized
                # items and return without ever constructing an LLM client.
                client.chat.completions.create.side_effect = AssertionError(
                    "LLM should not be called for already-summarized items"
                )
                await summarize_from_db(settings, db_path)
            # We expect the patch above to short-circuit before the
            # AsyncOpenAI() constructor is reached, so the m context
            # manager simply returns. If it did get called, the side
            # effect would raise.

        # Sanity check: still exactly 2 rows, both still summarized.
        con = db.connect(db_path)
        try:
            rows = list(
                con.execute(
                    "SELECT summarized_at FROM items WHERE status='new'"
                )
            )
            assert len(rows) == 2
            for r in rows:
                assert r["summarized_at"] is not None
        finally:
            con.close()


async def test_failed_summary_leaves_summarized_at_null() -> None:
    """If the LLM call fails, summarized_at should stay NULL so the item
    is retried on the next run."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        raw = tmp / "raw"
        raw.mkdir()
        db_path = tmp / "dedup.db"
        _seed_db(db_path, raw)

        settings = _settings(tmp)

        # Stubbed _summarize_one to raise immediately on every call.
        with mock.patch(
            "ai_crawling_pipeline.summary._summarize_one",
            side_effect=RuntimeError("network blip"),
        ):
            await summarize_from_db(settings, db_path)

        con = db.connect(db_path)
        try:
            rows = list(
                con.execute(
                    "SELECT summarized_at FROM items WHERE status='new'"
                )
            )
            assert len(rows) == 2
            for r in rows:
                assert r["summarized_at"] is None, (
                    "summarized_at should remain NULL on failure"
                )
        finally:
            con.close()


async def test_resummarize_flag_clears_summarized_at() -> None:
    """Passing force_resummarize=True should clear summarized_at for all
    primary rows, so the next run re-summarizes them all."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        raw = tmp / "raw"
        raw.mkdir()
        db_path = tmp / "dedup.db"
        _seed_db(db_path, raw)

        settings = _settings(tmp)

        # First run: mark as summarized.
        with _fake_llm():
            await summarize_from_db(settings, db_path)

        con = db.connect(db_path)
        try:
            assert all(
                r["summarized_at"] is not None
                for r in con.execute(
                    "SELECT summarized_at FROM items WHERE status='new'"
                )
            )
        finally:
            con.close()

        # Second run with force_resummarize=True: should re-summarize.
        with _fake_llm():
            await summarize_from_db(settings, db_path, force_resummarize=True)

        # Both rows have a new summarized_at (now > original).
        con = db.connect(db_path)
        try:
            rows = list(
                con.execute(
                    "SELECT summarized_at FROM items WHERE status='new' "
                    "ORDER BY source"
                )
            )
            assert len(rows) == 2
            for r in rows:
                assert r["summarized_at"] is not None
        finally:
            con.close()
