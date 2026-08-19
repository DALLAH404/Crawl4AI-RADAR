"""Data models for the Radar Aftermarket Pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Source:
    source_id: str
    name: str
    source_type: str
    tag: str = ""
    product_line: str = ""
    category: str = "auto"
    feed_type: str = "google_news_query"
    rss_url: str = ""
    query_text: str = ""
    active: bool = True
    # "news" (RSS/Google News, the general-news feed) or "social" (LinkedIn
    # today, more platforms later — each new one is just this field in the
    # YAML catalog, not a code change). Independent of feed_type: feed_type
    # is the specific fetch mechanism, content_kind is the coarse frontend
    # routing category that stays a 2-value enum as social platforms grow.
    content_kind: str = "news"

    def to_row(self) -> tuple:
        return (
            self.source_id,
            self.name,
            self.source_type,
            self.tag,
            self.product_line,
            self.category,
            self.feed_type,
            self.rss_url,
            self.query_text,
            int(self.active),
        )


@dataclass
class Article:
    # Identity is the URL hash (article_hash), not an auto-increment ID —
    # DynamoDB has no equivalent of SQLite's ROWID, and keying on a value
    # derived from the article itself is what makes writes idempotent.
    article_hash: str = ""
    title_hash: str = ""
    published_at: str = ""
    collected_at: str = ""
    category: str = "auto"
    competitor_tag: str = ""
    product_line: str = ""
    title: str = ""
    action_description: str = ""
    summary: str = ""
    competitor_analysis: str = ""
    summary_status: str = "pending"
    ai_model: str = ""
    ai_processed_at: str = ""
    event_type: str = ""
    alert_level: str = "Baixo"
    is_launch: bool = False
    image_url: str = ""
    # Full ordered image list — only ever more than one item for a LinkedIn
    # carousel post (see sources/linkedin.py's _to_item); every other source
    # has at most one image and leaves this empty, with image_url as the
    # single source of truth.
    image_urls: list[str] = field(default_factory=list)
    link: str = ""
    raw_link: str = ""
    ingestion_batch_id: str = ""
    source_id: str = ""
    source_name: str = ""
    # Denormalized from the source's own feed_type at collect time — with
    # sources no longer living in a DB table (see catalog.py), this is how
    # the fetch stage still knows to route linkedin_company rows without a
    # join.
    feed_type: str = ""
    # Denormalized from the source the same way — "news" or "social", the
    # frontend's filter dimension alongside company. See db.py's KindIndex.
    content_kind: str = "news"
    dedup_layer: int | None = None
    dedup_decision: str = ""
    dedup_reason: str = ""
    dedup_match_hash: str = ""
    dedup_score: float | None = None
    extra: str = ""

    def companies(self) -> list[str]:
        """Companies this article should be indexed under for the per-company
        feed. Currently derived from the single `competitor_tag` a source
        carries; a source that should fan an article out to more than one
        company can pass a comma-separated tag (e.g. "Bosch,Mahle")."""
        return [c.strip() for c in self.competitor_tag.split(",") if c.strip()]


@dataclass
class CollectionRun:
    run_id: str = ""
    source_id: str = ""
    source_name: str = ""
    executed_at: str = ""
    mode: str = "normal"
    status: str = "ok"
    items_found: int = 0
    items_new: int = 0
    # Raw-link + article-hash dedup hits (collector.py's two continue paths)
    # — surfaced so CollectionStats.items_duplicate is actually populated
    # instead of items_found - items_new being the only, unattributed clue.
    items_duplicate: int = 0
    duration_ms: int = 0
    error_message: str = ""


@dataclass
class DedupCandidate:
    rowid: int
    distance: float
    id_hash: str
    title: str
    excerpt: str = ""
    dedup_layer: int | None = None


@dataclass
class DedupResult:
    decision: str
    layer: int | None = None
    reason: str = ""
    match_hash: str = ""
    score: float | None = None
    embedding: list[float] | None = None


@dataclass
class SummarizeResult:
    ok: bool
    summary: str = ""
    competitor_analysis: str = ""
    # LLM-rewritten headline, empty when the model didn't produce a usable
    # one — the caller (summarize/pipeline.py) falls back to the article's
    # original scraped title in that case, never overwriting with "".
    title: str = ""
    event_type: str = ""
    alert_level: str = ""
    relevant: bool = True
    error: str = ""


@dataclass
class CollectionStats:
    sources_total: int = 0
    sources_ok: int = 0
    sources_error: int = 0
    items_found: int = 0
    items_new: int = 0
    items_duplicate: int = 0
    duration_ms: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
