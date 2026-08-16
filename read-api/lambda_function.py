"""Read API for RADAR — GET /articles, backed by DynamoDB.

Single Lambda, single route, zero external dependencies beyond boto3
(which every Lambda Python runtime ships with) — this file can be pasted
directly into the Lambda console's inline code editor, no zip upload, no
layer.

Query patterns (see ai-crawling-pipeline/src/radar_pipeline/README.md's
"Database layer — db.py" for the full schema):
  - latest N articles overall:        LatestIndex       (gsi2pk="ARTICLE")
  - one or more companies + a range:  CompanyTimeIndex  (gsi1pk="COMPANY#<tag>")

Deliberately does not import radar_pipeline.db — this Lambda is its own
independently-deployable unit, and duplicating the two query patterns here
(each ~15 lines, stable, already covered by radar_pipeline's own tests) is
a smaller ongoing cost than coupling this deployment's packaging to the
scraper's source tree. If the table schema in db.py ever changes, this
file needs the matching update by hand.

Route:
    GET /articles?limit=20&cursor=...
    GET /articles?company=Bosch,Valeo&from=2026-08-01&to=2026-08-31&limit=20&cursor=...

Response shape:
    {"items": [ {...article...}, ... ], "next_cursor": "<opaque>" | null}
"""

from __future__ import annotations

import base64
import json
import os
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

TABLE_NAME = os.environ.get("RADAR_TABLE_NAME", "radar-articles")
DEFAULT_LIMIT = 20
MAX_LIMIT = 100

_table = None


def _table_resource():
    global _table
    if _table is None:
        # Lambda's execution environment always sets AWS_REGION — but reads
        # elsewhere in this project found this botocore version doesn't
        # resolve AWS_REGION on its own the way it does AWS_DEFAULT_REGION,
        # so pass it explicitly rather than trust the default chain.
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        _table = boto3.resource("dynamodb", region_name=region).Table(TABLE_NAME)
    return _table


def _decimal_default(o: Any):
    if isinstance(o, Decimal):
        return int(o) if o % 1 == 0 else float(o)
    raise TypeError(f"not JSON serializable: {o!r}")


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            # Belt-and-suspenders: the HTTP API's own CORS config (set at
            # the API level in the console/CLI steps) is what actually
            # handles preflight; this header just means a response is
            # still correctly-CORS'd even if that config is ever missing.
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=_decimal_default),
    }


def _public_article(item: dict) -> dict:
    """Curated public shape — never leaks internal/operational fields
    (dedup_*, ingestion_batch_id, raw_link, extra, feed_type, ...).

    Company-link items (from CompanyTimeIndex) carry a smaller field set
    than base items (from LatestIndex) by design — they're a deliberately
    partial, denormalized view (see the README's "Multi-company reads").
    Fields absent on a given item just come through as their default here
    rather than raising — same reasoning as db._as_full_article, applied to
    a public API response instead of an internal one.
    """
    companies = item.get("companies")
    if companies is None:
        companies = [item["company"]] if item.get("company") else []
    return {
        "article_hash": item.get("article_hash", ""),
        "title": item.get("title", ""),
        "link": item.get("link", ""),
        "image_url": item.get("image_url", ""),
        "summary": item.get("summary", ""),
        "competitor_analysis": item.get("competitor_analysis", ""),
        "category": item.get("category", ""),
        "event_type": item.get("event_type", ""),
        "alert_level": item.get("alert_level", ""),
        "summary_status": item.get("summary_status", ""),
        "published_at": item.get("published_at", ""),
        "companies": companies,
        "source_name": item.get("source_name", ""),
    }


def _encode_cursor(value) -> str | None:
    if not value:
        return None
    return base64.urlsafe_b64encode(json.dumps(value, default=_decimal_default).encode()).decode()


def _decode_cursor(value: str | None):
    if not value:
        return None
    return json.loads(base64.urlsafe_b64decode(value.encode()).decode())


def _latest_articles(limit: int, cursor: str | None) -> dict:
    table = _table_resource()
    kwargs: dict[str, Any] = {
        "IndexName": "LatestIndex",
        "KeyConditionExpression": Key("gsi2pk").eq("ARTICLE"),
        "ScanIndexForward": False,
        "Limit": limit,
    }
    start_key = _decode_cursor(cursor)
    if start_key:
        kwargs["ExclusiveStartKey"] = start_key
    resp = table.query(**kwargs)
    return {
        "items": [_public_article(i) for i in resp.get("Items", [])],
        "next_cursor": _encode_cursor(resp.get("LastEvaluatedKey")),
    }


def _query_one_company(
    table, company: str, since: str | None, until: str | None, limit: int, start_key: dict | None
) -> tuple[list[dict], dict | None]:
    key_cond = Key("gsi1pk").eq(f"COMPANY#{company}")
    if since and until:
        # Trailing char sorts after anything the hash suffix on gsi1sk can
        # contain, so this includes everything published *on* `until`, not
        # just strictly before it — same trick as the CLI spot-check
        # commands in DEPLOYMENT.md.
        key_cond &= Key("gsi1sk").between(since, f"{until}￿")
    elif since:
        key_cond &= Key("gsi1sk").gte(since)
    elif until:
        key_cond &= Key("gsi1sk").lte(f"{until}￿")

    kwargs: dict[str, Any] = {
        "IndexName": "CompanyTimeIndex",
        "KeyConditionExpression": key_cond,
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if start_key:
        kwargs["ExclusiveStartKey"] = start_key
    resp = table.query(**kwargs)
    return resp.get("Items", []), resp.get("LastEvaluatedKey")


def _company_articles(
    companies: list[str], since: str | None, until: str | None, limit: int, cursor: str | None
) -> dict:
    """Fans out one Query per company — DynamoDB Query takes one partition
    key value at a time — and merges by published_at, deduping on
    article_hash in case an article belongs to more than one selected
    company. Pagination here is approximate, not exact: each page re-queries
    every company for up to `limit` items and re-merges, carrying a cursor
    per company rather than one global cursor. Fine for this project's
    scale; documented as a known tradeoff in the README rather than solved
    with something heavier (e.g. a single merged secondary read model).
    """
    table = _table_resource()
    prev_keys: dict[str, dict] = _decode_cursor(cursor) or {}
    next_keys: dict[str, dict] = {}
    seen: set[str] = set()
    merged: list[dict] = []

    for company in companies:
        items, last_key = _query_one_company(table, company, since, until, limit, prev_keys.get(company))
        if last_key:
            next_keys[company] = last_key
        for item in items:
            article_hash = item.get("article_hash")
            if article_hash in seen:
                continue
            seen.add(article_hash)
            merged.append(item)

    merged.sort(key=lambda i: i.get("published_at", ""), reverse=True)
    page = merged[:limit]
    has_more = len(merged) > limit or bool(next_keys)
    return {
        "items": [_public_article(i) for i in page],
        "next_cursor": _encode_cursor(next_keys) if has_more else None,
    }


def handler(event, context):
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "GET")
    if method == "OPTIONS":
        return _response(200, {})
    if method != "GET":
        return _response(405, {"error": "method not allowed"})

    params = event.get("queryStringParameters") or {}

    try:
        limit = int(params.get("limit", DEFAULT_LIMIT))
        if limit < 1:
            raise ValueError
        limit = min(limit, MAX_LIMIT)
    except (TypeError, ValueError):
        return _response(400, {"error": "limit must be a positive integer"})

    cursor = params.get("cursor")
    company_param = params.get("company")

    try:
        if company_param:
            companies = [c.strip() for c in company_param.split(",") if c.strip()]
            if not companies:
                return _response(400, {"error": "company must not be empty"})
            result = _company_articles(companies, params.get("from"), params.get("to"), limit, cursor)
        else:
            result = _latest_articles(limit, cursor)
    except Exception as exc:  # noqa: BLE001 — a clean 500, not a stack trace to the client
        print(f"ERROR: {exc}")
        return _response(500, {"error": "internal error"})

    return _response(200, result)
