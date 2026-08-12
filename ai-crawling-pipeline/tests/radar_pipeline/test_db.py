"""Tests for the database schema and helpers."""

import pytest
import tempfile
from pathlib import Path

from radar_pipeline.db import (
    connect,
    find_by_article_hash,
    find_by_raw_link,
    get_active_sources,
    init_schema,
    insert_article,
    mark_article_irrelevant,
    pending_articles,
)
from radar_pipeline.models import Article, Source


@pytest.fixture
def db_conn():
    path = Path(tempfile.mktemp(suffix=".db"))
    con = connect(path)
    init_schema(con, dim=768)
    yield con
    con.close()
    path.unlink(missing_ok=True)


@pytest.fixture
def db_with_source(db_conn):
    from radar_pipeline.db import insert_source

    s = Source(
        source_id="test-source",
        name="Test Source",
        source_type="concorrente",
        tag="Test",
        product_line="Geral",
        category="auto",
        feed_type="google_news_query",
        query_text="test query",
    )
    insert_source(db_conn, s)
    db_conn.commit()
    return db_conn


def _make_article(source_id: str = "test-source", **kwargs) -> Article:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    defaults = {
        "article_hash": "abc123",
        "title_hash": "th1",
        "published_at": "2026-08-01",
        "collected_at": now,
        "category": "auto",
        "title": "Test article",
        "link": "https://example.com/test",
        "source_id": source_id,
        "source_name": "Test Source",
    }
    defaults.update(kwargs)
    return Article(**defaults)


class TestSchema:
    def test_tables_exist(self, db_conn):
        tables = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {r[0] for r in tables}
        assert "sources" in names
        assert "articles" in names
        assert "collection_runs" in names
        assert "schema_version" in names

    def test_views_exist(self, db_conn):
        views = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
        ).fetchall()
        names = {r[0] for r in views}
        assert "vw_source_health_daily" in names
        assert "vw_stale_sources" in names

    def test_vec_table_exists(self, db_conn):
        tables = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        assert "vec_articles" in {r[0] for r in tables}

    def test_fts_table_exists(self, db_conn):
        tables = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        assert "articles_fts" in {r[0] for r in tables}


class TestSources:
    def test_source_insert_and_query(self, db_conn):
        from radar_pipeline.db import insert_source

        s = Source(
            source_id="test-bosch",
            name="Bosch Test",
            source_type="concorrente",
            tag="Bosch",
            product_line="Eletrica",
            category="auto",
            feed_type="google_news_query",
            query_text="Bosch autopecas",
        )
        insert_source(db_conn, s)
        db_conn.commit()

        active = get_active_sources(db_conn)
        assert len(active) == 1
        assert active[0]["source_id"] == "test-bosch"


class TestArticles:
    def test_insert_and_dedup(self, db_with_source):
        a1 = _make_article(article_hash="abc123def456", link="https://example.com/article-1")
        rowid1 = insert_article(db_with_source, a1)
        assert rowid1 is not None

        a2 = _make_article(article_hash="abc123def456", link="https://example.com/dupe")
        rowid2 = insert_article(db_with_source, a2)
        assert rowid2 is None

    def test_find_by_hash(self, db_with_source):
        a = _make_article(article_hash="xyz789", link="https://example.com/findme")
        insert_article(db_with_source, a)

        found = find_by_article_hash(db_with_source, "xyz789")
        assert found is not None
        assert found["title"] == "Test article"

        not_found = find_by_article_hash(db_with_source, "nonexistent")
        assert not_found is None

    def test_find_by_raw_link(self, db_with_source):
        a = _make_article(
            article_hash="rawlinkhash",
            link="https://real.example.com/resolved",
            raw_link="https://news.google.com/articles/abc",
        )
        insert_article(db_with_source, a)

        found = find_by_raw_link(
            db_with_source, "https://news.google.com/articles/abc",
        )
        assert found is not None
        assert found["link"] == "https://real.example.com/resolved"

        not_found = find_by_raw_link(db_with_source, "https://nope.example/")
        assert not_found is None

    def test_find_by_raw_link_empty_returns_none(self, db_with_source):
        assert find_by_raw_link(db_with_source, "") is None

    def test_irrelevant_is_excluded_from_search(self, db_with_source):
        a = _make_article(article_hash="irrelevant-one", link="https://example.com/irrel")
        rowid = insert_article(db_with_source, a)
        assert rowid is not None

        found_before = find_by_article_hash(db_with_source, "irrelevant-one")
        assert found_before is not None

        mark_article_irrelevant(db_with_source, rowid)
        db_with_source.commit()

        found_after = find_by_article_hash(db_with_source, "irrelevant-one")
        assert found_after is None

    def test_pending_articles(self, db_with_source):
        for i in range(3):
            a = _make_article(
                article_hash=f"pending{i}",
                link=f"https://example.com/art{i}",
                summary_status="pending",
            )
            insert_article(db_with_source, a)

        a_gen = _make_article(
            article_hash="generated",
            link="https://example.com/done",
            summary_status="ai_generated",
        )
        insert_article(db_with_source, a_gen)

        pending = pending_articles(db_with_source)
        assert len(pending) == 3

    def test_pending_articles_includes_feed_type(self, db_with_source):
        a = _make_article(article_hash="with-feed-type", link="https://example.com/ft")
        insert_article(db_with_source, a)

        pending = pending_articles(db_with_source)
        assert pending[0]["feed_type"] == "google_news_query"


class TestLinkedInFeedType:
    def test_insert_source_with_linkedin_feed_type(self, db_conn):
        from radar_pipeline.db import insert_source

        s = Source(
            source_id="test-linkedin",
            name="Bosch LinkedIn",
            source_type="concorrente",
            tag="Bosch",
            product_line="Geral",
            category="auto",
            feed_type="linkedin_company",
            query_text="bosch",
        )
        insert_source(db_conn, s)
        db_conn.commit()

        active = get_active_sources(db_conn)
        assert len(active) == 1
        assert active[0]["feed_type"] == "linkedin_company"


class TestMigration0002:
    def test_widens_feed_type_check_and_preserves_rows(self):
        import pysqlite3 as sqlite3

        from radar_pipeline.migrations import migration_0002

        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript("""
            CREATE TABLE schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT
            );
            INSERT INTO schema_version (version) VALUES (1);

            CREATE TABLE sources (
                source_id     TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                source_type   TEXT CHECK(source_type IN
                              ('portal','entidade','concorrente','tema','tecnologia','economia','lancamento')),
                tag           TEXT,
                product_line  TEXT,
                category      TEXT CHECK(category IN ('auto','economia','tecnologia')),
                feed_type     TEXT CHECK(feed_type IN ('rss_direct','google_news_query')),
                rss_url       TEXT,
                query_text    TEXT,
                active        INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                created_at    TEXT,
                updated_at    TEXT
            );

            INSERT INTO sources (
                source_id, name, source_type, tag, product_line, category,
                feed_type, rss_url, query_text, active, created_at
            ) VALUES (
                'existing-source', 'Existing', 'concorrente', 'X', 'Geral', 'auto',
                'google_news_query', '', 'query', 1, '2026-01-01T00:00:00Z'
            );
        """)
        con.commit()

        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO sources (source_id, name, source_type, category, feed_type, active) "
                "VALUES ('li', 'LI', 'concorrente', 'auto', 'linkedin_company', 1)"
            )

        migration_0002.apply(con)

        row = con.execute(
            "SELECT * FROM sources WHERE source_id = 'existing-source'"
        ).fetchone()
        assert row is not None
        assert row["feed_type"] == "google_news_query"

        con.execute(
            "INSERT INTO sources (source_id, name, source_type, category, feed_type, active) "
            "VALUES ('li', 'LI', 'concorrente', 'auto', 'linkedin_company', 1)"
        )
        con.commit()
        row2 = con.execute(
            "SELECT feed_type FROM sources WHERE source_id = 'li'"
        ).fetchone()
        assert row2["feed_type"] == "linkedin_company"

        con.close()

    def test_idempotent_on_already_migrated_table(self, db_conn):
        from radar_pipeline.migrations import migration_0002

        count_before = db_conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        migration_0002.apply(db_conn)
        count_after = db_conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        assert count_before == count_after
