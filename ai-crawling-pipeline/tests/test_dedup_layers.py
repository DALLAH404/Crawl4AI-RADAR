"""Tests for the five-layer dedup pipeline."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from ai_crawling_pipeline.config import (
    DedupSettings,
    GeminiEmbeddingSettings,
    LLMSettings,
)
from ai_crawling_pipeline.dedup import Deduper, parse_raw_markdown


def _settings(tmp: Path) -> DedupSettings:
    return DedupSettings(
        db_path=tmp / "dedup.db",
        input_dir=tmp,
        title_window_hours=72,
        jaccard_window_hours=24,
        jaccard_threshold=0.4,
        embedding_window_hours=72,
        embedding_threshold=0.85,
        embedding_ambiguity_low=0.75,
        embedding_ambiguity_high=0.85,
        embedding_top_k=10,
        embedding=GeminiEmbeddingSettings(
            model="models/gemini-embedding-001", dim=4
        ),
        llm=LLMSettings(
            base_url="https://example.invalid",
            api_key_env="OPENAI_API_KEY",
        ),
    )


def _stub_embedder(deduper: Deduper) -> None:
    """Deterministic, content-sensitive fake embedder.

    Hashes the input so that:
      - similar text (same input) gets a near-identical vector
      - very different text gets a near-orthogonal vector
    """

    async def embed(text: str, task_type: str | None = None) -> list[float]:
        h = hashlib.md5(text.encode("utf-8")).digest()
        return [(h[i] - 128) / 128.0 for i in range(4)]

    deduper.embedder.embed = embed  # type: ignore[assignment]


def _stub_judge(deduper: Deduper, verdict: str = "same") -> None:
    async def judge(
        title_a: str, title_b: str, excerpt_a: str, excerpt_b: str
    ) -> str:
        return verdict

    deduper._judge_llm = judge  # type: ignore[assignment]


async def test_layer0_id_hash_match() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        settings = _settings(tmp)
        async with _DeduperCtx(settings) as d:
            _stub_embedder(d)
            d1 = await d.classify(
                slug="a",
                source_url="https://x.com/a",
                canonical_url="https://x.com/a",
                title="Apple launches new iPhone",
                excerpt="Apple announced a new iPhone.",
            )
            assert d1.status == "new" and d1.layer is None
            assert d1.rowid is not None
            d2 = await d.classify(
                slug="a2",
                source_url="https://x.com/a",
                canonical_url="https://x.com/a",
                title="Apple launches new iPhone (again)",
                excerpt="Apple announced a new iPhone model.",
            )
            assert d2.status == "dupe" and d2.layer == 0
            assert d2.dupe_of_hash == d1.id_hash
            assert d2.rowid is not None
            assert d2.rowid != d1.rowid, "Each classify() call must produce a fresh rowid"


async def test_layer0_duplicate_does_not_overwrite_primary_content_path() -> None:
    """Regression: when a layer-0 dupe is inserted, the original primary
    row's content_path must not be overwritten by the dupe's UPDATE."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        settings = _settings(tmp)
        async with _DeduperCtx(settings) as d:
            _stub_embedder(d)
            d1 = await d.classify(
                slug="a",
                source_url="https://x.com/a",
                canonical_url="https://x.com/a",
                title="Apple launches new iPhone",
                excerpt="Apple announced a new iPhone.",
            )
            # Simulate the orchestrator's content_path UPDATE (dedup_all).
            d.con.execute(
                "UPDATE items SET content_path = ? WHERE rowid = ?",
                (str(tmp / "primary.md"), d1.rowid),
            )
            d.con.commit()
            d2 = await d.classify(
                slug="a2",
                source_url="https://x.com/a",
                canonical_url="https://x.com/a",
                title="Apple launches new iPhone (again)",
                excerpt="Apple announced a new iPhone model.",
            )
            d.con.execute(
                "UPDATE items SET content_path = ? WHERE rowid = ?",
                (str(tmp / "dupe.md"), d2.rowid),
            )
            d.con.commit()
            # The primary's content_path must still point to primary.md.
            row = d.con.execute(
                "SELECT content_path FROM items WHERE rowid = ?", (d1.rowid,)
            ).fetchone()
            assert row["content_path"].endswith("primary.md"), row["content_path"]
            row2 = d.con.execute(
                "SELECT content_path FROM items WHERE rowid = ?", (d2.rowid,)
            ).fetchone()
            assert row2["content_path"].endswith("dupe.md"), row2["content_path"]





async def test_layer1_title_hash_match() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        settings = _settings(tmp)
        async with _DeduperCtx(settings) as d:
            _stub_embedder(d)
            d1 = await d.classify(
                slug="a",
                source_url="https://x.com/a",
                canonical_url="https://x.com/a",
                title="Apple launches new iPhone",
                excerpt="Apple announced a new iPhone.",
            )
            assert d1.status == "new"
            d2 = await d.classify(
                slug="b",
                source_url="https://y.com/b",
                canonical_url="https://y.com/b",
                title="Apple launches new iPhone",
                excerpt="Apple announced a new iPhone model.",
            )
            assert d2.status == "dupe" and d2.layer == 1
            assert d2.dupe_of_hash == d1.id_hash


async def test_layer2_jaccard_match() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        settings = _settings(tmp)
        async with _DeduperCtx(settings) as d:
            _stub_embedder(d)
            d1 = await d.classify(
                slug="a",
                source_url="https://x.com/a",
                canonical_url="https://x.com/a",
                title="Apple launches new iPhone",
                excerpt="Apple announced a new iPhone.",
            )
            assert d1.status == "new"
            d2 = await d.classify(
                slug="b",
                source_url="https://y.com/b",
                canonical_url="https://y.com/b",
                title="Apple launches its new iPhone line",
                excerpt="Apple announced a new iPhone model with great cameras.",
            )
            assert d2.status == "dupe" and d2.layer == 2
            assert d2.cluster_score is not None and d2.cluster_score > 0.4


async def test_unrelated_content_is_new() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        settings = _settings(tmp)
        async with _DeduperCtx(settings) as d:
            _stub_embedder(d)
            d1 = await d.classify(
                slug="a",
                source_url="https://x.com/a",
                canonical_url="https://x.com/a",
                title="Apple launches new iPhone",
                excerpt="Apple announced a new iPhone.",
            )
            d2 = await d.classify(
                slug="b",
                source_url="https://y.com/b",
                canonical_url="https://y.com/b",
                title="Microsoft releases Windows update",
                excerpt="Microsoft released a Windows update with security patches.",
            )
            assert d1.status == "new"
            assert d2.status == "new"


async def test_layer4_llm_judge_says_same() -> None:
    """When the KNN cosine falls in the ambiguous band and the LLM judge
    says 'same', the item is recorded as a layer-4 dupe."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        settings = _settings(tmp)
        # Disable earlier layers so the test reaches layer 3/4.
        settings.jaccard_threshold = 0.0
        settings.title_window_hours = 0
        async with _DeduperCtx(settings) as d:
            _stub_embedder(d)
            d1 = await d.classify(
                slug="a",
                source_url="https://x.com/a",
                canonical_url="https://x.com/a",
                title="Apple launches new iPhone",
                excerpt="Apple announced a new iPhone.",
            )
            assert d1.status == "new"

            from ai_crawling_pipeline.dedup import Decision

            # Pretend KNN returned a candidate with cosine 0.80 (in the
            # ambiguous band) and let the LLM judge make the final call.
            async def fake_check(embedding, title, excerpt):
                from ai_crawling_pipeline.db import find_primary_by_id_hash

                other = find_primary_by_id_hash(d.con, d1.id_hash)
                verdict = await d._judge_llm(
                    title_a=title,
                    title_b=other["title"] if other else "",
                    excerpt_a=excerpt,
                    excerpt_b=other["title"] if other else "",
                )
                if verdict == "same":
                    return (
                        Decision(
                            layer=4,
                            id_hash="",
                            title=title,
                            status="dupe",
                            dupe_of_hash=d1.id_hash,
                            cluster_score=0.80,
                        ),
                        None,
                    )
                return None, None

            d._check_layers_3_4 = fake_check  # type: ignore[assignment]
            _stub_judge(d, verdict="same")
            d2 = await d.classify(
                slug="b",
                source_url="https://y.com/b",
                canonical_url="https://y.com/b",
                title="Apple launches a new iPhone variant",
                excerpt="Apple announced a new iPhone variant.",
            )
            assert d2.status == "dupe", d2
            assert d2.layer == 4, d2
            assert d2.cluster_score == 0.80


async def test_layer4_llm_judge_says_different_is_new() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        settings = _settings(tmp)
        settings.jaccard_threshold = 0.0
        settings.title_window_hours = 0
        async with _DeduperCtx(settings) as d:
            _stub_embedder(d)
            d1 = await d.classify(
                slug="a",
                source_url="https://x.com/a",
                canonical_url="https://x.com/a",
                title="Apple launches new iPhone",
                excerpt="Apple announced a new iPhone.",
            )
            assert d1.status == "new"

            from ai_crawling_pipeline.dedup import Decision

            async def fake_check(embedding, title, excerpt):
                from ai_crawling_pipeline.db import find_primary_by_id_hash

                other = find_primary_by_id_hash(d.con, d1.id_hash)
                verdict = await d._judge_llm(
                    title_a=title,
                    title_b=other["title"] if other else "",
                    excerpt_a=excerpt,
                    excerpt_b=other["title"] if other else "",
                )
                if verdict == "same":
                    return (
                        Decision(
                            layer=4,
                            id_hash="",
                            title=title,
                            status="dupe",
                            dupe_of_hash=d1.id_hash,
                            cluster_score=0.80,
                        ),
                        None,
                    )
                return None, None

            d._check_layers_3_4 = fake_check  # type: ignore[assignment]
            _stub_judge(d, verdict="different")
            d2 = await d.classify(
                slug="b",
                source_url="https://y.com/b",
                canonical_url="https://y.com/b",
                title="Apple launches a new iPhone variant",
                excerpt="Apple announced a new iPhone variant.",
            )
            assert d2.status == "new", d2
            assert d2.layer is None, d2


async def test_judge_llm_token_extraction_handles_negation() -> None:
    """Regression for the substring-matching bug: 'different, not the same'
    must not match 'same' first."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        settings = _settings(tmp)
        async with _DeduperCtx(settings) as d:
            # Stub the LLM client to return a controlled response.
            from openai.types.chat import (
                ChatCompletion,
                ChatCompletionMessage,
            )
            from openai.types.chat.chat_completion import Choice

            class _Resp:
                choices: list

            def make_response(text: str) -> ChatCompletion:
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
                                role="assistant", content=text
                            ),
                        )
                    ],
                )

            class _Completions:
                def __init__(self, text: str) -> None:
                    self._text = text

                async def create(self, *args, **kwargs):
                    return make_response(self._text)

            class _Chat:
                def __init__(self, text: str) -> None:
                    self.completions = _Completions(text)

            # Override llm_client.chat
            class _StubClient:
                def __init__(self, text: str) -> None:
                    self.chat = _Chat(text)

            cases = [
                ("they are different, not the same", "different"),
                ("SAME.", "same"),
                ("related", "related"),
                ("  Same event reported", "same"),
                ("garbage output", "different"),  # defaults
                ("", "different"),  # empty
            ]
            for response_text, expected in cases:
                d.llm_client = _StubClient(response_text)  # type: ignore[assignment]
                verdict = await d._judge_llm("A", "B", "exA", "exB")
                assert verdict == expected, (
                    f"input={response_text!r} -> {verdict!r}, expected {expected!r}"
                )


def test_parse_raw_markdown_extracts_header_fields() -> None:
    body = "# Hello World\n\nURL: https://example.com/hello\n\nThis is the body."
    title, url, excerpt = parse_raw_markdown(body)
    assert title == "Hello World"
    assert url == "https://example.com/hello"
    assert "This is the body" in excerpt


# ---------------------------------------------------------------------------
# context manager helper
# ---------------------------------------------------------------------------

class _DeduperCtx:
    def __init__(self, settings: DedupSettings) -> None:
        self.settings = settings
        self.deduper: Deduper | None = None

    async def __aenter__(self) -> Deduper:
        os.environ.setdefault("GEMINI_API_KEY", "test-key")
        os.environ.setdefault("OPENAI_API_KEY", "test-key")
        self.deduper = Deduper(self.settings)
        return self.deduper

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.deduper is not None:
            self.deduper.close()
