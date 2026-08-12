"""Source catalog — YAML seeder and CRUD for the sources table.

Seeds the sources table from configs/radar_sources.yaml on first run
or via `radar-pipeline sources import-yaml`. Add/disable/enable/list
sub-commands operate directly on the SQLite table.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from radar_pipeline.db import (
    get_active_sources,
    get_all_sources,
    insert_source,
)
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


def seed_from_yaml(con, yaml_path: str | Path) -> int:
    p = Path(yaml_path)
    if not p.exists():
        logger.warning("Source YAML not found: %s", p)
        return 0

    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    sources = data.get("sources", [])
    if not sources:
        logger.warning("No sources in %s", p)
        return 0

    count = 0
    for item in sources:
        feed_type = item.get("feed_type", "google_news_query")
        if item.get("rss_url"):
            feed_type = "rss_direct"

        source = Source(
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
        )
        insert_source(con, source)
        count += 1

    con.commit()
    logger.info("Seeded %d sources from %s", count, p)
    return count


def add_source(con, **kwargs: Any) -> None:
    source = Source(
        source_id=kwargs.get("source_id") or _make_slug(kwargs["name"]),
        **{k: v for k, v in kwargs.items() if k in Source.__dataclass_fields__},
    )
    insert_source(con, source)
    con.commit()


def set_active(con, source_id: str, active: bool) -> bool:
    cur = con.execute(
        "UPDATE sources SET active = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE source_id = ?",
        (int(active), source_id),
    )
    con.commit()
    return cur.rowcount > 0


def list_sources(con, active_only: bool = False) -> list:
    if active_only:
        return [dict(r) for r in get_active_sources(con)]
    return [dict(r) for r in get_all_sources(con)]
