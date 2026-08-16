"""Source catalog — loads configs/radar_sources.yaml into Source objects.

Sources are small, static, hand-edited config (feed URLs, LinkedIn slugs) —
there is no DynamoDB table for them. Toggling a source on/off means editing
`active:` in the YAML directly; there is no live enable/disable command
anymore (there was nothing left to mutate once sources stopped living in a
database row).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from radar_pipeline.models import Source

logger = logging.getLogger(__name__)


def _make_slug(name: str) -> str:
    slug = name.lower()
    slug = slug.replace(" ", "-").replace("/", "-").replace("·", "-")
    slug = slug.replace(".", "").replace(",", "").replace("&", "and")
    slug = slug.replace("(", "").replace(")", "").replace(":", "")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def _to_source(item: dict[str, Any]) -> Source:
    feed_type = item.get("feed_type", "google_news_query")
    if item.get("rss_url"):
        feed_type = "rss_direct"

    # Explicit content_kind wins; otherwise infer from feed_type as a safety
    # net (a new linkedin_company source added without remembering to tag it
    # "social" still lands in the right place). Every other feed_type
    # defaults to "news". New social platforms later are one YAML field, no
    # code change — this inference only exists for today's one social
    # feed_type.
    content_kind = item.get("content_kind") or (
        "social" if feed_type == "linkedin_company" else "news"
    )

    return Source(
        source_id=item.get("source_id") or _make_slug(item["name"]),
        name=item["name"],
        source_type=item.get("source_type", "concorrente"),
        tag=item.get("tag", ""),
        product_line=item.get("product_line", "Geral"),
        category=item.get("category", "auto"),
        feed_type=feed_type,
        rss_url=item.get("rss_url", ""),
        query_text=item.get("query_text", ""),
        active=item.get("active", True),
        content_kind=content_kind,
    )


def load_sources(yaml_path: str | Path) -> list[Source]:
    """Parse every source in the YAML catalog (active and inactive)."""
    p = Path(yaml_path)
    if not p.exists():
        logger.warning("Source YAML not found: %s", p)
        return []

    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    raw_sources = data.get("sources", [])
    if not raw_sources:
        logger.warning("No sources in %s", p)
        return []

    return [_to_source(item) for item in raw_sources]


def list_sources(yaml_path: str | Path, active_only: bool = False) -> list[Source]:
    sources = load_sources(yaml_path)
    if active_only:
        sources = [s for s in sources if s.active]
    return sources
