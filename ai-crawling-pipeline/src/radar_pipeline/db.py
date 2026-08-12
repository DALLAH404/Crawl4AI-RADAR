"""SQLite + sqlite-vec database layer for the Radar Aftermarket Pipeline.

Schema: sources, articles, collection_runs, vec_articles (vec0),
articles_fts (fts5), and views for source health monitoring.

Public API:
    connect(db_path) -> sqlite3.Connection
    init_schema(con, dim) -> None
    run_migrations(con) -> None
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

import pysqlite3 as sqlite3
import sqlite_vec

SCHEMA_VERSION = 2


def connect(db_path: str | os.PathLike | None = None) -> sqlite3.Connection:
    p = Path(db_path) if db_path is not None else Path("outputs/radar/radar.db")
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("PRAGMA synchronous = NORMAL;")
    return con


def init_schema(con: sqlite3.Connection, dim: int = 768) -> None:
    con.executescript(f"""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );

        CREATE TABLE IF NOT EXISTS sources (
            source_id     TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            source_type   TEXT CHECK(source_type IN
                          ('portal','entidade','concorrente','tema','tecnologia','economia','lancamento')),
            tag           TEXT,
            product_line  TEXT,
            category      TEXT CHECK(category IN ('auto','economia','tecnologia')),
            feed_type     TEXT CHECK(feed_type IN ('rss_direct','google_news_query','linkedin_company')),
            rss_url       TEXT,
            query_text    TEXT,
            active        INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            created_at    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at    TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_sources_active_type ON sources(active, source_type);

        CREATE TABLE IF NOT EXISTS articles (
            id                  INTEGER PRIMARY KEY,
            article_hash        TEXT NOT NULL UNIQUE,
            title_hash          TEXT,
            published_at        TEXT,
            collected_at        TEXT NOT NULL,
            category            TEXT CHECK(category IN ('auto','economia','tecnologia')),
            competitor_tag      TEXT,
            product_line        TEXT,
            title               TEXT NOT NULL,
            action_description  TEXT,
            summary             TEXT,
            competitor_analysis TEXT,
            summary_status      TEXT NOT NULL DEFAULT 'pending'
                                 CHECK(summary_status IN
                                   ('pending','ai_generated','scraped_fallback','irrelevant','failed')),
            ai_model            TEXT,
            ai_processed_at     TEXT,
            event_type          TEXT,
            alert_level         TEXT CHECK(alert_level IN ('Alto','Medio','Baixo','')),
            is_launch           INTEGER NOT NULL DEFAULT 0 CHECK(is_launch IN (0,1)),
            image_url           TEXT,
            link                TEXT NOT NULL UNIQUE,
            raw_link            TEXT,
            ingestion_batch_id  TEXT,
            source_id           TEXT REFERENCES sources(source_id),
            source_name         TEXT NOT NULL,
            dedup_layer         INTEGER,
            dedup_decision      TEXT CHECK(dedup_decision IN ('new','duplicate','')),
            dedup_reason        TEXT,
            dedup_match_id      INTEGER REFERENCES articles(id),
            dedup_score         REAL,
            extra               TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_articles_category     ON articles(category);
        CREATE INDEX IF NOT EXISTS idx_articles_competitor   ON articles(competitor_tag);
        CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at);
        CREATE INDEX IF NOT EXISTS idx_articles_is_launch    ON articles(is_launch) WHERE is_launch = 1;
        CREATE INDEX IF NOT EXISTS idx_articles_pending_ai   ON articles(summary_status) WHERE summary_status = 'pending';
        CREATE INDEX IF NOT EXISTS idx_articles_title_hash   ON articles(title_hash);
        CREATE INDEX IF NOT EXISTS idx_articles_dedup_match  ON articles(dedup_match_id);
        CREATE INDEX IF NOT EXISTS idx_articles_raw_link     ON articles(raw_link) WHERE raw_link IS NOT NULL AND raw_link != '';

        CREATE TABLE IF NOT EXISTS collection_runs (
            id             INTEGER PRIMARY KEY,
            run_id         TEXT NOT NULL,
            source_id      TEXT REFERENCES sources(source_id),
            source_name    TEXT,
            executed_at    TEXT NOT NULL,
            mode           TEXT CHECK(mode IN ('normal','backfill')),
            status         TEXT CHECK(status IN ('ok','error')),
            items_found    INTEGER,
            items_new      INTEGER,
            duration_ms    INTEGER,
            error_message  TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_runs_executed_at   ON collection_runs(executed_at);
        CREATE INDEX IF NOT EXISTS idx_runs_source_status ON collection_runs(source_id, status);

        CREATE VIEW IF NOT EXISTS vw_source_health_daily AS
        SELECT
          date(executed_at)          AS run_date,
          source_id,
          source_name,
          SUM(status = 'error')      AS error_count,
          SUM(status = 'ok')         AS ok_count,
          SUM(items_new)             AS items_new_total
        FROM collection_runs
        GROUP BY 1, 2, 3;

        CREATE VIEW IF NOT EXISTS vw_stale_sources AS
        SELECT s.source_id, s.name, s.source_type, MAX(r.executed_at) AS last_success
        FROM sources s
        LEFT JOIN collection_runs r
          ON s.source_id = r.source_id AND r.status = 'ok'
        WHERE s.active = 1
        GROUP BY 1, 2, 3
        HAVING last_success IS NULL OR last_success < datetime('now', '-7 days');

        CREATE VIRTUAL TABLE IF NOT EXISTS vec_articles USING vec0(
            embedding float[{dim}] distance_metric=cosine
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
            title, summary, action_description,
            content='articles', content_rowid='id'
        );
    """)

    _ensure_fts_triggers(con)

    cur = con.execute("SELECT MAX(version) FROM schema_version")
    row = cur.fetchone()
    current = row[0] if row and row[0] is not None else 0
    # Only stamp on a genuinely fresh DB (no version row yet) — CREATE TABLE
    # IF NOT EXISTS above is a no-op for an existing DB, so an existing DB at
    # an older version must be left for run_migrations() to advance via its
    # own migration_NNNN.apply() steps, not fast-forwarded here.
    if current == 0:
        con.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
    con.commit()


def _ensure_fts_triggers(con: sqlite3.Connection) -> None:
    cur = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='articles_ai'"
    )
    if cur.fetchone() is not None:
        return

    con.executescript("""
        CREATE TRIGGER articles_ai AFTER INSERT ON articles BEGIN
            INSERT INTO articles_fts(rowid, title, summary, action_description)
            VALUES (new.id, new.title, new.summary, new.action_description);
        END;

        CREATE TRIGGER articles_ad AFTER DELETE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, title, summary, action_description)
            VALUES ('delete', old.id, old.title, old.summary, old.action_description);
        END;

        CREATE TRIGGER articles_au AFTER UPDATE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, title, summary, action_description)
            VALUES ('delete', old.id, old.title, old.summary, old.action_description);
            INSERT INTO articles_fts(rowid, title, summary, action_description)
            VALUES (new.id, new.title, new.summary, new.action_description);
        END;
    """)


def run_migrations(con: sqlite3.Connection) -> None:
    cur = con.execute("SELECT MAX(version) FROM schema_version")
    row = cur.fetchone()
    current = row[0] if row and row[0] is not None else 0

    if current < 1:
        from radar_pipeline.migrations import migration_0001

        migration_0001.apply(con)
        con.execute(
            "INSERT INTO schema_version (version) VALUES (1) "
            "ON CONFLICT(version) DO NOTHING"
        )
        con.commit()
        current = 1

    if current < 2:
        from radar_pipeline.migrations import migration_0002

        migration_0002.apply(con)
        con.execute(
            "INSERT INTO schema_version (version) VALUES (2) "
            "ON CONFLICT(version) DO NOTHING"
        )
        con.commit()


def has_sources_table(con: sqlite3.Connection) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone() is not None


# ----- sources -----

def insert_source(con: sqlite3.Connection, s) -> None:
    con.execute(
        """INSERT OR REPLACE INTO sources(
            source_id, name, source_type, tag, product_line, category,
            feed_type, rss_url, query_text, active, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))""",
        s.to_row(),
    )


def get_active_sources(con: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM sources WHERE active = 1 ORDER BY name"
    ).fetchall()]


def get_all_sources(con: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute("SELECT * FROM sources ORDER BY name").fetchall()]


# ----- articles -----

def insert_article(con: sqlite3.Connection, a, commit: bool = True) -> int | None:
    try:
        cur = con.execute(
            """INSERT INTO articles(
                article_hash, title_hash, published_at, collected_at, category,
                competitor_tag, product_line, title, action_description,
                summary, competitor_analysis, summary_status,
                event_type, alert_level, is_launch, image_url, link, raw_link,
                ingestion_batch_id, source_id, source_name,
                dedup_layer, dedup_decision, dedup_reason, dedup_match_id,
                dedup_score, extra
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                a.article_hash, a.title_hash, a.published_at, a.collected_at,
                a.category, a.competitor_tag, a.product_line, a.title,
                a.action_description, a.summary, a.competitor_analysis,
                a.summary_status, a.event_type, a.alert_level,
                int(a.is_launch), a.image_url, a.link, a.raw_link,
                a.ingestion_batch_id, a.source_id, a.source_name,
                a.dedup_layer, a.dedup_decision, a.dedup_reason,
                a.dedup_match_id, a.dedup_score, a.extra,
            ),
        )
        rowid = cur.lastrowid
        if commit:
            con.commit()
        return rowid
    except sqlite3.IntegrityError:
        return None


def find_by_article_hash(
    con: sqlite3.Connection,
    article_hash: str,
    exclude_id: int | None = None,
) -> sqlite3.Row | None:
    sql = (
        "SELECT * FROM articles WHERE article_hash = ? "
        "AND summary_status != 'irrelevant'"
    )
    params: list[Any] = [article_hash]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    sql += " LIMIT 1"
    return con.execute(sql, params).fetchone()


def find_by_raw_link(
    con: sqlite3.Connection,
    raw_link: str,
) -> sqlite3.Row | None:
    """Look up an article by its raw (unresolved) feed link.

    Used by the collect stage as a fast-path dedup BEFORE invoking the
    Google News resolver — see the comment in collector.py:_process_source
    for why this matters.
    """
    if not raw_link:
        return None
    return con.execute(
        "SELECT id, article_hash, raw_link, link FROM articles "
        "WHERE raw_link = ? LIMIT 1",
        (raw_link,),
    ).fetchone()


def find_by_title_hash_within(
    con: sqlite3.Connection,
    title_hash: str,
    since_ts: str,
    exclude_id: int | None = None,
) -> sqlite3.Row | None:
    sql = (
        "SELECT * FROM articles "
        "WHERE title_hash = ? AND published_at >= ? "
        "AND summary_status != 'irrelevant'"
    )
    params: list[Any] = [title_hash, since_ts]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    sql += " ORDER BY published_at DESC LIMIT 1"
    return con.execute(sql, params).fetchone()


def candidate_titles_within(
    con: sqlite3.Connection,
    since_ts: str,
    exclude_id: int | None = None,
) -> list[sqlite3.Row]:
    sql = (
        "SELECT id, article_hash, title FROM articles "
        "WHERE published_at >= ? AND summary_status != 'irrelevant'"
    )
    params: list[Any] = [since_ts]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return con.execute(sql, params).fetchall()


def knn_within(
    con: sqlite3.Connection,
    embedding: list[float],
    since_ts: str,
    top_k: int,
    exclude_id: int | None = None,
) -> list[sqlite3.Row]:
    sql = (
        "SELECT v.rowid, v.distance, a.article_hash, a.title, a.action_description "
        "FROM vec_articles v "
        "JOIN articles a ON a.id = v.rowid "
        "WHERE v.embedding MATCH ? AND k = ? "
        "AND a.published_at >= ? "
        "AND a.summary_status != 'irrelevant'"
    )
    params: list[Any] = [json.dumps(embedding), top_k, since_ts]
    if exclude_id is not None:
        sql += " AND a.id != ?"
        params.append(exclude_id)
    sql += " ORDER BY v.distance"
    return con.execute(sql, params).fetchall()


def fts5_search_within(
    con: sqlite3.Connection,
    query_text: str,
    since_ts: str,
    top_k: int,
    exclude_id: int | None = None,
) -> list[sqlite3.Row]:
    fts_query = " OR ".join(f'"{w}"' for w in query_text.split() if len(w) > 1)
    if not fts_query:
        return []
    sql = (
        "SELECT a.id, a.article_hash, a.title, fts.rank "
        "FROM articles_fts fts "
        "JOIN articles a ON a.id = fts.rowid "
        "WHERE articles_fts MATCH ? AND a.published_at >= ? "
        "AND a.summary_status != 'irrelevant'"
    )
    params: list[Any] = [fts_query, since_ts]
    if exclude_id is not None:
        sql += " AND a.id != ?"
        params.append(exclude_id)
    sql += " ORDER BY fts.rank LIMIT ?"
    params.append(top_k)
    return con.execute(sql, params).fetchall()


def insert_vector(con: sqlite3.Connection, article_id: int, embedding: list[float]) -> None:
    con.execute("DELETE FROM vec_articles WHERE rowid = ?", (article_id,))
    con.execute(
        "INSERT INTO vec_articles(rowid, embedding) VALUES (?, ?)",
        (article_id, json.dumps(embedding)),
    )


# ----- collection runs -----

def insert_collection_run(con: sqlite3.Connection, run) -> None:
    con.execute(
        """INSERT INTO collection_runs(
            run_id, source_id, source_name, executed_at, mode,
            status, items_found, items_new, duration_ms, error_message
        ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            run.run_id, run.source_id, run.source_name, run.executed_at,
            run.mode, run.status, run.items_found, run.items_new,
            run.duration_ms, run.error_message,
        ),
    )


# ----- query helpers -----

def pending_articles(
    con: sqlite3.Connection, limit: int | None = None
) -> list[dict[str, Any]]:
    q = (
        "SELECT a.*, s.feed_type FROM articles a "
        "LEFT JOIN sources s ON s.source_id = a.source_id "
        "WHERE a.summary_status = 'pending' "
        "AND a.category IN ('auto','economia') "
        "AND (a.dedup_decision IS NULL OR a.dedup_decision != 'duplicate') "
        "ORDER BY a.collected_at"
    )
    if limit:
        q += f" LIMIT {limit}"
    return [dict(r) for r in con.execute(q).fetchall()]


def article_count(con: sqlite3.Connection) -> int:
    return con.execute(
        "SELECT COUNT(*) FROM articles WHERE summary_status != 'irrelevant'"
    ).fetchone()[0]


def article_count_by_status(con: sqlite3.Connection) -> dict[str, int]:
    rows = con.execute(
        "SELECT summary_status, COUNT(*) as cnt FROM articles GROUP BY summary_status"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def article_count_by_category(con: sqlite3.Connection) -> dict[str, int]:
    rows = con.execute(
        "SELECT category, COUNT(*) as cnt FROM articles "
        "WHERE summary_status != 'irrelevant' GROUP BY category"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def mark_article_irrelevant(con: sqlite3.Connection, article_id: int) -> None:
    con.execute(
        "UPDATE articles SET summary_status = 'irrelevant' WHERE id = ?",
        (article_id,),
    )


def mark_article_failed(con: sqlite3.Connection, article_id: int, error: str = "") -> None:
    con.execute(
        "UPDATE articles SET summary_status = 'failed', extra = ? WHERE id = ?",
        (error, article_id),
    )
