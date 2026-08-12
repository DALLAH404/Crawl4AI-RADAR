"""JSON writer for summarized articles.

Produces outputs/radar/processed/<source_id>/<article_id>_<hash>.json with
article metadata and LLM-generated summary fields.
"""
from __future__ import annotations

import json
from pathlib import Path


def make_summary_slug(source_id: str, article_id: int, article_hash: str) -> str:
    return f"{source_id}_{article_id}_{article_hash[:8]}"


def write_summary_json(
    output_dir: Path,
    article_id: int,
    article_hash: str,
    source_id: str,
    source_name: str,
    title: str,
    url: str,
    summary: str,
    competitor_analysis: str = "",
    category: str = "auto",
    tag: str = "",
    product_line: str = "Geral",
    event_type: str = "",
    alert_level: str = "",
    date: str = "",
    image_url: str = "",
    ai_model: str = "",
    ai_processed_at: str = "",
) -> Path:
    source = source_id or "unknown"
    source_dir = output_dir / source
    source_dir.mkdir(parents=True, exist_ok=True)

    slug = make_summary_slug(source, article_id, article_hash)
    out_path = source_dir / f"{slug}.json"

    payload = {
        "id": article_id,
        "id_hash": article_hash,
        "source": source,
        "source_name": source_name,
        "category": category,
        "tag": tag,
        "product_line": product_line,
        "event_type": event_type,
        "alert_level": alert_level,
        "date": date,
        "image_url": image_url,
        "url": url,
        "ai_model": ai_model,
        "ai_processed_at": ai_processed_at,
        "title": title,
        "summary": summary,
        "competitor_analysis": competitor_analysis,
    }

    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_path
