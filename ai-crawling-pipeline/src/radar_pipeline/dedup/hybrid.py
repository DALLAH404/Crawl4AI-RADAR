"""Hybrid search helpers — FTS5 + vec0 reciprocal-rank fusion.

The *enhanced* part of the dedup stage. Combines vector KNN results
with keyword-based FTS5 matches using weighted reciprocal-rank fusion.
"""

from __future__ import annotations

import sqlite3

from radar_pipeline.db import fts5_search_within, knn_within


def reciprocal_rank_fusion(
    knn_results: list[sqlite3.Row],
    fts_results: list[sqlite3.Row],
    knn_weight: float = 0.6,
    fts_weight: float = 0.4,
    top_k: int = 10,
) -> list[dict]:
    scores: dict[int, dict] = {}

    for rank, row in enumerate(knn_results):
        rid = row["rowid"]
        cosine = 1.0 - float(row["distance"])
        scores[rid] = {
            "rowid": rid,
            "article_hash": row["article_hash"],
            "title": row["title"],
            "cosine": cosine,
            "score": knn_weight / max(rank + 1, 1),
        }

    for rank, row in enumerate(fts_results):
        rid = row["id"]
        fts_score = fts_weight / max(rank + 1, 1)
        if rid in scores:
            scores[rid]["score"] += fts_score
        else:
            scores[rid] = {
                "rowid": rid,
                "article_hash": row["article_hash"],
                "title": row["title"],
                "cosine": 0.0,
                "score": fts_score,
            }

    ranked = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
    return ranked[:top_k]


async def hybrid_search(
    con: sqlite3.Connection,
    query_embedding: list[float],
    query_text: str,
    since_ts: str,
    top_k: int = 10,
    exclude_id: int | None = None,
) -> list[dict]:
    knn_results = knn_within(con, query_embedding, since_ts, top_k * 2, exclude_id=exclude_id)
    fts_results = fts5_search_within(con, query_text, since_ts, top_k, exclude_id=exclude_id)

    return reciprocal_rank_fusion(knn_results, fts_results, top_k=top_k)
