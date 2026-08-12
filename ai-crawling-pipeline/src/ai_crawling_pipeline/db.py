"""SQLite storage for the dedup stage with sqlite-vec vector search.

Uses pysqlite3 (extension-loading enabled) and the sqlite-vec loadable
extension for KNN cosine search over item embeddings.

Public API:
    connect(db_path) -> sqlite3.Connection
    init_schema(con, dim) -> None
    insert_item(...) -> int (returns the inserted rowid)
    find_primary_by_id_hash(con, id_hash) -> sqlite3.Row | None
    find_by_title_hash_within(con, title_hash, since_ts) -> sqlite3.Row | None
    candidate_titles_within(con, since_ts) -> list[sqlite3.Row]
    knn_within(con, embedding, since_ts, top_k) -> list[sqlite3.Row]
    new_items(con) -> list[sqlite3.Row]
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable

import pysqlite3 as sqlite3
import sqlite_vec


def _default_db_path() -> Path:
    return Path("outputs/dedup.db")


def connect(db_path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """Open (creating parents as needed) a SQLite connection with extension
    loading enabled and the sqlite-vec extension loaded.
    """
    p = Path(db_path) if db_path is not None else _default_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    con.row_factory = sqlite3.Row
    return con


def init_schema(con: sqlite3.Connection, dim: int) -> None:
    """Create the `items` table, indexes, and the `items_vec` virtual table
    sized to `dim` (the Gemini embedding dimensionality). Idempotent: safe
    to call from both the dedup and summarize stages.
    """
    con.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS items (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            id_hash TEXT,
            title_hash TEXT,
            canonical_url TEXT,
            title TEXT,
            excerpt TEXT,
            content_path TEXT,
            source TEXT,
            source_url TEXT,
            embedding_model TEXT,
            created_at REAL,
            dedup_layer INTEGER,
            dupe_of_hash TEXT,
            status TEXT CHECK(status IN ('new','dupe')) DEFAULT 'new',
            cluster_score REAL,
            summarized_at REAL
        );

        CREATE INDEX IF NOT EXISTS idx_items_id_hash ON items(id_hash);
        CREATE INDEX IF NOT EXISTS idx_items_title_hash ON items(title_hash);
        CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
        CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at);
        """
    )
    # Add columns for older DBs that pre-date them. Each ALTER is a no-op
    # when the column already exists; PRAGMA check keeps it idempotent.
    cur = con.execute("PRAGMA table_info(items)")
    cols = {row[1] for row in cur.fetchall()}
    if "excerpt" not in cols:
        con.execute("ALTER TABLE items ADD COLUMN excerpt TEXT")
    if "summarized_at" not in cols:
        con.execute("ALTER TABLE items ADD COLUMN summarized_at REAL")
    # Indexes that depend on migrated columns are created AFTER the
    # column migration, otherwise the CREATE INDEX would fail on an
    # older DB that doesn't have the column yet.
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_items_summarized "
        "ON items(summarized_at)"
    )
    # The vec0 virtual table uses the items.rowid as its primary key so
    # that KNN queries can be joined to the items table on rowid.
    con.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS items_vec USING vec0("
        f"embedding float[{dim}] distance_metric=cosine, "
        f"id INTEGER PRIMARY KEY)"
    )
    con.commit()


def has_items_table(con: sqlite3.Connection) -> bool:
    """Whether the `items` table already exists in the DB. Used by the
    summarizer to refuse reading from an uninitialized dedup DB."""
    cur = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='items' LIMIT 1"
    )
    return cur.fetchone() is not None


def insert_item(
    con: sqlite3.Connection,
    *,
    id_hash: str,
    title_hash: str,
    canonical_url: str,
    title: str,
    content_path: str,
    source: str,
    source_url: str,
    embedding: list[float] | None,
    embedding_model: str | None,
    created_at: float,
    dedup_layer: int | None,
    dupe_of_hash: str | None,
    status: str,
    excerpt: str | None = None,
    cluster_score: float | None = None,
    commit: bool = True,
) -> int:
    """Insert a new item row and, if `embedding` is provided, the matching
    vector row. Returns the inserted rowid.

    `commit=True` (default) commits immediately. Pass `commit=False` when
    batching many inserts in one transaction (the caller is responsible
    for committing).
    """
    cur = con.execute(
        """
        INSERT INTO items(
            id_hash, title_hash, canonical_url, title, excerpt, content_path,
            source, source_url, embedding_model, created_at, dedup_layer,
            dupe_of_hash, status, cluster_score
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            id_hash, title_hash, canonical_url, title, excerpt, content_path,
            source, source_url, embedding_model, created_at, dedup_layer,
            dupe_of_hash, status, cluster_score,
        ),
    )
    rowid = cur.lastrowid
    if embedding is not None:
        con.execute(
            "INSERT INTO items_vec(id, embedding) VALUES (?, ?)",
            (rowid, json.dumps(embedding)),
        )
    if commit:
        con.commit()
    return rowid


def find_primary_by_id_hash(
    con: sqlite3.Connection, id_hash: str
) -> sqlite3.Row | None:
    """Return the primary (earliest) row matching this id_hash, if any.
    The primary is the first row inserted for that id_hash; any later
    row with the same id_hash is itself marked as a dupe of this one."""
    return con.execute(
        """
        SELECT * FROM items
         WHERE id_hash = ? AND status = 'new'
         ORDER BY created_at ASC, rowid ASC
         LIMIT 1
        """,
        (id_hash,),
    ).fetchone()


def find_by_title_hash_within(
    con: sqlite3.Connection, title_hash: str, since_ts: float
) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT * FROM items
         WHERE title_hash = ? AND created_at >= ?
           AND status = 'new'
         ORDER BY created_at DESC
         LIMIT 1
        """,
        (title_hash, since_ts),
    ).fetchone()


def candidate_titles_within(
    con: sqlite3.Connection, since_ts: float
) -> list[sqlite3.Row]:
    """Return rows for Jaccard comparison (new items in the time window)."""
    return con.execute(
        """
        SELECT id_hash, title FROM items
         WHERE created_at >= ? AND status = 'new'
        """,
        (since_ts,),
    ).fetchall()


def knn_within(
    con: sqlite3.Connection,
    embedding: list[float],
    since_ts: float,
    top_k: int,
) -> list[sqlite3.Row]:
    """KNN cosine search over items whose created_at >= since_ts.

    Returns rows joined from items_vec + items, with a `distance` column.

    NOTE: sqlite-vec applies the `k` limit during the KNN scan over the
    entire `items_vec` table *before* the joined-table `WHERE` filters on
    `created_at` and `status` are evaluated. Callers should set `top_k`
    large enough to survive the post-filter, or rely on the caller in
    `dedup.py` to over-fetch and rerun the filter in Python if needed.
    """
    return con.execute(
        """
        SELECT v.id AS rowid, v.distance AS distance,
               i.id_hash, i.title, i.excerpt, i.dedup_layer
          FROM items_vec v
          JOIN items i ON i.rowid = v.id
         WHERE v.embedding MATCH ? AND k = ?
           AND i.created_at >= ?
           AND i.status = 'new'
         ORDER BY v.distance
        """,
        (json.dumps(embedding), top_k, since_ts),
    ).fetchall()


def new_items(con: sqlite3.Connection) -> Iterable[sqlite3.Row]:
    """All rows marked new (regardless of whether they've been summarized).
    Prefer `unsummarized_items` for the summarizer's normal flow."""
    return con.execute(
        "SELECT * FROM items WHERE status = 'new' ORDER BY created_at, rowid"
    ).fetchall()


def unsummarized_items(con: sqlite3.Connection) -> Iterable[sqlite3.Row]:
    """Rows marked new that have NOT been summarized yet.

    `rowid` is included explicitly so callers can write back
    `summarized_at` updates by rowid without an extra lookup.
    """
    return con.execute(
        "SELECT rowid, * FROM items "
        "WHERE status = 'new' AND summarized_at IS NULL "
        "ORDER BY created_at, rowid"
    ).fetchall()


def mark_summarized(
    con: sqlite3.Connection, rowid: int, ts: float | None = None
) -> None:
    """Stamp `summarized_at` on a row after a successful summary write.

    Pass `ts=None` to use the current time. The caller is responsible for
    calling this only on success; failures should leave the column NULL so
    the item is retried on the next run.
    """
    if ts is None:
        ts = time.time()
    con.execute(
        "UPDATE items SET summarized_at = ? WHERE rowid = ?", (ts, rowid)
    )
    con.commit()


def clear_summarized(con: sqlite3.Connection) -> int:
    """Reset `summarized_at` to NULL for every primary (`status='new'`)
    row, so the next summarize_from_db run re-summarizes them all. Returns
    the number of rows reset. Used by the --resummarize CLI flag."""
    cur = con.execute(
        "UPDATE items SET summarized_at = NULL WHERE status = 'new'"
    )
    con.commit()
    return cur.rowcount
