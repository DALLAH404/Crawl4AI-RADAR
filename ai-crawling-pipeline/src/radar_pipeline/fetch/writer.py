"""Writer for fetched articles — local disk or S3, same Markdown shape.

Produces `<slug>.md` with YAML front-matter compatible with the dedup and
summary stages. `write_article_md` (local disk) is used when no S3 bucket
is configured (`fetch.s3` in config); `upload_article_md` (S3) is used when
it is, organized one folder per fetch run:

    <prefix>/<run_timestamp>/<source_id>/<slug>.md

Local disk on Fargate is ephemeral — gone the moment the task stops — so
S3 is what makes a fetch run's actual scraped content durable across runs.
"""

from __future__ import annotations

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


def render_article_markdown(
    article_hash: str,
    source_id: str,
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
) -> str:
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
    return front + body


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

    content = render_article_markdown(
        article_hash=article_hash, source_id=source_id, title=title, url=url,
        markdown=markdown, category=category, tag=tag, product_line=product_line,
        event_type=event_type, alert_level=alert_level, date=date, image_url=image_url,
    )
    out_path.write_text(content, encoding="utf-8")
    return out_path


def s3_key_for_run(
    prefix: str, run_timestamp: str, source_id: str, article_hash: str, title: str
) -> str:
    slug = make_slug(source_id, article_hash, title)
    return f"{prefix}/{run_timestamp}/{source_id}/{slug}.md"


def upload_article_md(
    s3_client,
    bucket: str,
    prefix: str,
    run_timestamp: str,
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
) -> str:
    """Upload one article's rendered Markdown to S3. Returns the object key."""
    key = s3_key_for_run(prefix, run_timestamp, source_id, article_hash, title)
    content = render_article_markdown(
        article_hash=article_hash, source_id=source_id, title=title, url=url,
        markdown=markdown, category=category, tag=tag, product_line=product_line,
        event_type=event_type, alert_level=alert_level, date=date, image_url=image_url,
    )
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=content.encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
    )
    return key
