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
    id: int | None = None
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
    link: str = ""
    raw_link: str = ""
    ingestion_batch_id: str = ""
    source_id: str = ""
    source_name: str = ""
    dedup_layer: int | None = None
    dedup_decision: str = ""
    dedup_reason: str = ""
    dedup_match_id: int | None = None
    dedup_score: float | None = None
    extra: str = ""


@dataclass
class CollectionRun:
    id: int | None = None
    run_id: str = ""
    source_id: str = ""
    source_name: str = ""
    executed_at: str = ""
    mode: str = "normal"
    status: str = "ok"
    items_found: int = 0
    items_new: int = 0
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
    match_id: int | None = None
    score: float | None = None
    embedding: list[float] | None = None


@dataclass
class SummarizeResult:
    ok: bool
    summary: str = ""
    competitor_analysis: str = ""
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
