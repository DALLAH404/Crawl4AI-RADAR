"""2-layer dedup pipeline — URL hash + normalized title hash.

Layers:
    0  canonical_url        -> md5 -> article_hash (exact match across whole table)
    1  normalized title     -> md5 -> title_hash (within title_window)

Embedding-based layers (3 and 4) and the Jaccard/FTS5 layer (2) were removed to
keep dedup fast and quota-light. The Gemini embedder code (radar_pipeline/dedup/
embedding.py) is preserved for future use.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from radar_pipeline.config import DedupSettings
from radar_pipeline.db import (
    find_by_article_hash,
    find_by_title_hash_within,
)
from radar_pipeline.models import DedupResult

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"\s+", re.UNICODE)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _normalize_title(title: str) -> str:
    t = title.strip().lower()
    t = TOKEN_RE.sub(" ", t)
    for prefix in ("breaking:", "update:", "news:", "just in:"):
        if t.startswith(prefix):
            t = t[len(prefix):].lstrip()
    return t


def _window_ts(hours: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


class Deduper:
    def __init__(self, con: sqlite3.Connection, settings: DedupSettings) -> None:
        self.con = con
        self.settings = settings

    async def classify_article(
        self,
        article_hash: str,
        link: str,
        title: str,
        action_description: str = "",
        article_id: int | None = None,
    ) -> DedupResult:
        title_norm = _normalize_title(title)
        title_hash = _md5(title_norm)

        # Layer 0: URL hash
        if not article_hash:
            logger.warning(
                "Article %s has no article_hash; skipping Layer 0 (same_url) check",
                article_id,
            )
        else:
            existing = find_by_article_hash(
                self.con, article_hash, exclude_id=article_id
            )
            if existing is not None:
                return DedupResult(
                    decision="duplicate",
                    layer=0,
                    reason="same_url",
                    match_id=existing["id"],
                )

        # Layer 1: Normalized title hash within window
        title_window = _window_ts(self.settings.title_window_hours)
        existing_t = find_by_title_hash_within(
            self.con, title_hash, title_window, exclude_id=article_id
        )
        if existing_t is not None:
            return DedupResult(
                decision="duplicate",
                layer=1,
                reason="same_title",
                match_id=existing_t["id"],
            )

        return DedupResult(decision="new")


async def run_dedup(con: sqlite3.Connection, settings: DedupSettings) -> dict:
    from radar_pipeline.db import pending_articles

    articles = pending_articles(con)
    if not articles:
        return {"total": 0, "new": 0, "duplicates": 0}

    deduper = Deduper(con, settings)
    results = {"total": len(articles), "new": 0, "duplicates": 0}

    for article in articles:
        result = await deduper.classify_article(
            article_hash=article["article_hash"],
            link=article["link"],
            title=article["title"],
            action_description=article["action_description"],
            article_id=article["id"],
        )

        if result.decision == "duplicate":
            con.execute(
                """UPDATE articles SET
                    dedup_decision = 'duplicate',
                    dedup_layer = ?,
                    dedup_reason = ?,
                    dedup_match_id = ?,
                    dedup_score = ?
                WHERE id = ?""",
                (result.layer, result.reason, result.match_id, result.score, article["id"]),
            )
            results["duplicates"] += 1
        else:
            con.execute(
                """UPDATE articles SET
                    dedup_decision = 'new'
                WHERE id = ?""",
                (article["id"],),
            )
            results["new"] += 1

    con.commit()
    return results
