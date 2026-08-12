"""Tests for the dedup helpers and DB layer."""
from __future__ import annotations

import tempfile
from pathlib import Path

from ai_crawling_pipeline import db
from ai_crawling_pipeline.dedup import (
    canonicalize_url,
    jaccard,
    md5_hex,
    normalize_title,
    tokenize,
)


def test_canonicalize_url_strips_case_trailing_slash_and_fragment() -> None:
    assert canonicalize_url("https://Example.com/Path/") == "https://example.com/path"
    assert canonicalize_url("HTTPS://X.com/P#frag") == "https://x.com/p"
    assert canonicalize_url("  https://x.com  ") == "https://x.com"


def test_normalize_title_lowercases_strips_prefix_and_collapses_ws() -> None:
    assert normalize_title("  Hello World  ") == "hello world"
    assert normalize_title("Breaking: Foo Bar") == "foo bar"
    assert normalize_title("Update:\n\nFoo\tBar") == "foo bar"


def test_md5_is_stable() -> None:
    assert md5_hex("hi") == "49f68a5c8493ec2c0bf489821c21fc3b"


def test_tokenize_filters_short_tokens() -> None:
    toks = tokenize("The AI is a is of the AI")
    assert "the" in toks
    assert "ai" in toks
    # single-char tokens dropped
    assert "a" not in toks
    assert "i" not in toks


def test_jaccard_edge_cases() -> None:
    assert jaccard(set(), set()) == 1.0
    assert jaccard(set(), {"a"}) == 0.0
    assert jaccard({"a"}, set()) == 0.0
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert 0.0 < jaccard({"a", "b", "c"}, {"b", "c", "d"}) < 1.0


def test_db_schema_and_knn_round_trip() -> None:
    """Smoke test for schema init, insertion, and vec0 KNN search."""
    with tempfile.TemporaryDirectory() as tmp:
        con = db.connect(Path(tmp) / "t.db")
        try:
            db.init_schema(con, dim=4)
            db.insert_item(
                con,
                id_hash="id1",
                title_hash="t1",
                canonical_url="https://x.com/1",
                title="title one",
                content_path="",
                source="s",
                source_url="https://x.com/1",
                embedding=[1.0, 0.0, 0.0, 0.0],
                embedding_model="m",
                created_at=100.0,
                dedup_layer=None,
                dupe_of_hash=None,
                status="new",
            )
            db.insert_item(
                con,
                id_hash="id2",
                title_hash="t2",
                canonical_url="https://x.com/2",
                title="title two",
                content_path="",
                source="s",
                source_url="https://x.com/2",
                embedding=[0.0, 1.0, 0.0, 0.0],
                embedding_model="m",
                created_at=200.0,
                dedup_layer=None,
                dupe_of_hash=None,
                status="new",
            )
            # KNN with a vector close to id1
            hits = db.knn_within(
                con, [0.99, 0.01, 0.0, 0.0], since_ts=0.0, top_k=2
            )
            assert len(hits) == 2
            assert hits[0]["id_hash"] == "id1"
            assert hits[0]["distance"] < hits[1]["distance"]
            # Time-window filter excludes id2 if its created_at is too old
            hits2 = db.knn_within(
                con, [0.0, 0.99, 0.01, 0.0], since_ts=150.0, top_k=2
            )
            assert {h["id_hash"] for h in hits2} == {"id2"}
        finally:
            con.close()


def test_parse_raw_markdown_strips_both_header_lines() -> None:
    """The excerpt should exclude both the `# title` line and the `URL:`
    line so they don't leak into embeddings or the LLM judge input."""
    from ai_crawling_pipeline.dedup import parse_raw_markdown

    text = (
        "# Hello World\n"
        "\n"
        "URL: https://example.com/hello\n"
        "\n"
        "This is the body of the article."
    )
    title, url, excerpt = parse_raw_markdown(text)
    assert title == "Hello World"
    assert url == "https://example.com/hello"
    assert "URL:" not in excerpt, f"URL header leaked into excerpt: {excerpt!r}"
    assert "# Hello World" not in excerpt
    assert "This is the body" in excerpt

    # Order of header lines doesn't matter.
    text2 = "URL: https://x.com\n\n# Title\n\nBody text."
    title, url, excerpt = parse_raw_markdown(text2)
    assert title == "Title"
    assert url == "https://x.com"
    assert "Body text." in excerpt
    assert "URL:" not in excerpt
    assert "# Title" not in excerpt

    # Edge cases.
    title, url, excerpt = parse_raw_markdown("Just body, no headers.")
    assert title == "" and url == "" and "Just body" in excerpt
    title, url, excerpt = parse_raw_markdown("")
    assert title == "" and url == "" and excerpt == ""


def test_db_has_items_table_guard() -> None:
    """summarize_from_db uses has_items_table to refuse uninitialized DBs."""
    with tempfile.TemporaryDirectory() as tmp:
        # Fresh DB without schema -> no items table.
        con = db.connect(Path(tmp) / "empty.db")
        try:
            assert db.has_items_table(con) is False
            db.init_schema(con, dim=4)
            assert db.has_items_table(con) is True
        finally:
            con.close()


def test_db_init_schema_migrates_excerpt_column() -> None:
    """An older DB lacking the `excerpt` column should be upgraded in place."""
    with tempfile.TemporaryDirectory() as tmp:
        con = db.connect(Path(tmp) / "old.db")
        try:
            # Create a schema without `excerpt` (simulates the previous version).
            con.executescript(
                """
                CREATE TABLE items (
                    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_hash TEXT,
                    title_hash TEXT,
                    canonical_url TEXT,
                    title TEXT,
                    content_path TEXT,
                    source TEXT,
                    source_url TEXT,
                    embedding_model TEXT,
                    created_at REAL,
                    dedup_layer INTEGER,
                    dupe_of_hash TEXT,
                    status TEXT CHECK(status IN ('new','dupe')) DEFAULT 'new',
                    cluster_score REAL
                );
                """
            )
            con.commit()
            # Migrate via init_schema.
            db.init_schema(con, dim=4)
            cols = {row[1] for row in con.execute("PRAGMA table_info(items)").fetchall()}
            assert "excerpt" in cols
            assert "summarized_at" in cols
        finally:
            con.close()


def test_db_unsummarized_items_filters_by_status_and_timestamp() -> None:
    """unsummarized_items returns only `status='new'` AND `summarized_at IS NULL`."""
    with tempfile.TemporaryDirectory() as tmp:
        con = db.connect(Path(tmp) / "d.db")
        try:
            db.init_schema(con, dim=4)
            # Two new items + one dupe + one already-summarized new item.
            db.insert_item(
                con, id_hash="a", title_hash="ta", canonical_url="u/a", title="A",
                content_path="", source="a", source_url="u/a",
                embedding=None, embedding_model=None, created_at=1.0,
                dedup_layer=None, dupe_of_hash=None, status="new",
            )
            db.insert_item(
                con, id_hash="b", title_hash="tb", canonical_url="u/b", title="B",
                content_path="", source="b", source_url="u/b",
                embedding=None, embedding_model=None, created_at=2.0,
                dedup_layer=None, dupe_of_hash=None, status="new",
            )
            db.insert_item(
                con, id_hash="c", title_hash="tc", canonical_url="u/c", title="C",
                content_path="", source="c", source_url="u/c",
                embedding=None, embedding_model=None, created_at=3.0,
                dedup_layer=None, dupe_of_hash="a", status="dupe",
            )
            db.insert_item(
                con, id_hash="d", title_hash="td", canonical_url="u/d", title="D",
                content_path="", source="d", source_url="u/d",
                embedding=None, embedding_model=None, created_at=4.0,
                dedup_layer=None, dupe_of_hash=None, status="new",
            )
            # Mark D as already summarized.
            db.mark_summarized(con, 4, ts=10.0)
            rows = list(db.unsummarized_items(con))
            sources = {r["source"] for r in rows}
            assert sources == {"a", "b"}, sources
        finally:
            con.close()


def test_db_clear_summarized_resets_all_primaries() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        con = db.connect(Path(tmp) / "d.db")
        try:
            db.init_schema(con, dim=4)
            for i, src in enumerate(("a", "b"), start=1):
                db.insert_item(
                    con, id_hash=f"id{i}", title_hash=f"t{i}",
                    canonical_url=f"u/{src}", title=src, content_path="",
                    source=src, source_url=f"u/{src}",
                    embedding=None, embedding_model=None, created_at=float(i),
                    dedup_layer=None, dupe_of_hash=None, status="new",
                )
            db.mark_summarized(con, 1, ts=1.0)
            db.mark_summarized(con, 2, ts=2.0)
            n_reset = db.clear_summarized(con)
            assert n_reset == 2
            rows = list(db.unsummarized_items(con))
            assert len(rows) == 2
        finally:
            con.close()
