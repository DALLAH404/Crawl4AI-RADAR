"""Migration 0002 — Widen sources.feed_type to allow 'linkedin_company'.

SQLite cannot ALTER a CHECK constraint, so this rebuilds the `sources`
table with the new constraint via the standard SQLite recipe (create new
table, copy rows, drop old, rename). Idempotent: no-ops if the existing
table's DDL already contains 'linkedin_company'.
"""


def apply(con) -> None:
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()
    if row is None or row[0] is None or "linkedin_company" in row[0]:
        return

    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.executescript("""
            -- vw_stale_sources selects FROM sources; SQLite recompiles/
            -- validates every view's SQL as part of ALTER TABLE ... RENAME
            -- (it rewrites the table name inside dependent schema objects),
            -- and that validation runs against the mid-migration state where
            -- "sources" briefly doesn't exist. The view must be dropped
            -- before the rebuild and recreated after, identically to its
            -- definition in db.py:init_schema. vw_source_health_daily does
            -- not reference sources (only collection_runs) and is unaffected.
            DROP VIEW IF EXISTS vw_stale_sources;

            CREATE TABLE sources_new (
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

            INSERT INTO sources_new SELECT * FROM sources;

            DROP TABLE sources;

            ALTER TABLE sources_new RENAME TO sources;

            CREATE INDEX IF NOT EXISTS idx_sources_active_type ON sources(active, source_type);

            CREATE VIEW IF NOT EXISTS vw_stale_sources AS
            SELECT s.source_id, s.name, s.source_type, MAX(r.executed_at) AS last_success
            FROM sources s
            LEFT JOIN collection_runs r
              ON s.source_id = r.source_id AND r.status = 'ok'
            WHERE s.active = 1
            GROUP BY 1, 2, 3
            HAVING last_success IS NULL OR last_success < datetime('now', '-7 days');
        """)
        con.execute("PRAGMA foreign_key_check")
    finally:
        con.execute("PRAGMA foreign_keys = ON")

    con.commit()
