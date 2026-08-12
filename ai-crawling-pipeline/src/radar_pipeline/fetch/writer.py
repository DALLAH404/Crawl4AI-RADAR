"""Markdown writer for fetched articles.

Produces outputs/radar/raw/<slug>.md with YAML front-matter compatible
with the dedup and summary stages.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


YAML_FRONT_MATTER = """---
id_hash: {id_hash}
source: {source}
category: {category}
tag: {tag}
product_line: {product_line}
event_type: {event_type}
alert_level: {alert_level}
date: {date}
image_url: {image_url}
url: {url}
---

"""


def make_slug(source_id: str, article_hash: str, title: str) -> str:
    slug = f"{source_id}_{article_hash[:8]}"
    slug = slug.lower().replace(" ", "-").replace("/", "-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:100]


def write_article_md(
    output_dir: Path,
    source_id: str,
    article_hash: str,
    title: str,
    url: str,
    markdown: str,
    category: str = "auto",
    tag: str = "",
    product_line: str = "Geral",
    event_type: str = "",
    alert_level: str = "",
    date: str = "",
    image_url: str = "",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = make_slug(source_id, article_hash, title)
    out_path = output_dir / f"{slug}.md"

    front = YAML_FRONT_MATTER.format(
        id_hash=article_hash,
        source=source_id,
        category=category,
        tag=tag,
        product_line=product_line,
        event_type=event_type,
        alert_level=alert_level,
        date=date,
        image_url=image_url,
        url=url,
    )

    body = f"# {title}\n\nURL: {url}\n\n{markdown}"
    out_path.write_text(front + body, encoding="utf-8")
    return out_path
