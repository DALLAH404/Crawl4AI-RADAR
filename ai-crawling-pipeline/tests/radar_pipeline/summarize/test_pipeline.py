"""Regression test for run_summarize against a freshly-collected article.

A pending article has never been summarized — summary="" (the Article
dataclass default), which DynamoDB stores by omitting the attribute
entirely (db._base_item strips empty-string fields). run_summarize reads
`article["summary"]` via bracket access; if db.py ever stopped backfilling
Article's field defaults on read (db._as_full_article), this would raise
KeyError('summary') and abort the whole run before touching any article —
exactly what happened in production the first time this ran for real. See
db.py's _as_full_article and the corner-case note in README.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from radar_pipeline.config import LLMSettings, SummarizeSettings
from radar_pipeline.db import get_article, put_article
from radar_pipeline.models import Article, SummarizeResult
from radar_pipeline.summarize import pipeline as pipeline_mod


def _fresh_pending_article(**overrides) -> Article:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    defaults = dict(
        article_hash="fresh-hash-1",
        title_hash="fresh-title-hash-1",
        published_at="2026-08-13",
        collected_at=now,
        category="auto",
        competitor_tag="Bosch",
        title="Bosch launches a new brake pad line",
        action_description="Bosch launches a new brake pad line for aftermarket.",
        summary_status="pending",
        link="https://example.com/bosch-brake-pads",
        source_id="bosch",
        source_name="Bosch",
        feed_type="google_news_query",
        # summary, competitor_analysis, event_type, alert_level, image_url all
        # left at their Article defaults ("" / "Baixo") — never set, exactly
        # like a real freshly-collected, never-summarized article.
    )
    defaults.update(overrides)
    return Article(**defaults)


@pytest.mark.asyncio
async def test_run_summarize_does_not_crash_on_never_summarized_article(store, tmp_path: Path, monkeypatch):
    put_article(store, _fresh_pending_article())
    assert "summary" not in store.table.get_item(
        Key={"pk": "ARTICLE#fresh-hash-1", "sk": "METADATA"}
    )["Item"], "test setup assumption broken: summary should be absent, not empty, on a fresh item"

    async def fake_summarize_one(*, title, content, llm_settings, system_prompt, max_chars):
        return SummarizeResult(
            ok=True, relevant=True, summary="A concise summary.",
            competitor_analysis="Bosch is expanding aftermarket brake coverage.",
            event_type="Lancamento", alert_level="Alto",
        )

    monkeypatch.setattr(pipeline_mod, "summarize_one", fake_summarize_one)

    settings = SummarizeSettings(
        output_dir=tmp_path,
        llm=LLMSettings(base_url="https://example.invalid/v1", model="test-model"),
    )

    result = await pipeline_mod.run_summarize(store, settings)

    assert result == {"total": 1, "summarized": 1, "irrelevant": 0, "failed": 0}
    updated = get_article(store, "fresh-hash-1")
    assert updated["summary"] == "A concise summary."
    assert updated["summary_status"] == "ai_generated"


@pytest.mark.asyncio
async def test_run_summarize_marks_irrelevant_without_crashing(store, tmp_path: Path, monkeypatch):
    put_article(store, _fresh_pending_article(article_hash="fresh-hash-2", link="https://example.com/2"))

    async def fake_summarize_one(*, title, content, llm_settings, system_prompt, max_chars):
        return SummarizeResult(ok=True, relevant=False)

    monkeypatch.setattr(pipeline_mod, "summarize_one", fake_summarize_one)

    settings = SummarizeSettings(
        output_dir=tmp_path,
        llm=LLMSettings(base_url="https://example.invalid/v1", model="test-model"),
    )

    result = await pipeline_mod.run_summarize(store, settings)

    assert result == {"total": 1, "summarized": 0, "irrelevant": 1, "failed": 0}
    assert get_article(store, "fresh-hash-2")["summary_status"] == "irrelevant"
