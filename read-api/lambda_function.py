"""Read API for RADAR — GET /articles, backed by DynamoDB.

Single Lambda, single route, zero external dependencies beyond boto3
(which every Lambda Python runtime ships with) — this file can be pasted
directly into the Lambda console's inline code editor, no zip upload, no
layer.

Query patterns (see ai-crawling-pipeline/src/radar_pipeline/README.md's
"Database layer — db.py" for the full schema):
  - latest N articles overall:            LatestIndex       (gsi2pk="ARTICLE")
  - one kind, no company:                 KindIndex         (gsi5pk="KIND#<kind>")
  - one or more companies, any/one kind:  CompanyTimeIndex  (gsi1pk="COMPANY#<tag>",
                                           optionally filtered by content_kind)

Deliberately does not import radar_pipeline.db — this Lambda is its own
independently-deployable unit, and duplicating these query patterns here
(stable, already covered by radar_pipeline's own tests) is a smaller
ongoing cost than coupling this deployment's packaging to the scraper's
source tree. If the table schema in db.py ever changes, this file needs
the matching update by hand.

Route:
    GET /articles?limit=20&cursor=...
    GET /articles?kind=news&limit=20&cursor=...
    GET /articles?company=Bosch,Valeo&from=2026-08-01&to=2026-08-31&kind=social&limit=20&cursor=...

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
from boto3.dynamodb.conditions import Attr, Key

TABLE_NAME = os.environ.get("RADAR_TABLE_NAME", "radar-articles")
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
VALID_KINDS = {"news", "social"}

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
    `action_description` is now denormalized onto company-link items too
    (see db.py's _company_item), but older items written before that change
    won't have it until the article is next updated — Fields absent on a
    given item just come through as their default here rather than raising,
    same reasoning as db._as_full_article, applied to a public API response
    instead of an internal one.
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
        # Full scraped post text — only meaningfully "full" for social
        # (LinkedIn) sources; for RSS/news sources this is just a 300-char
        # snippet (see linkedin.py vs collector.py's action_description).
        "action_description": item.get("action_description", ""),
        "competitor_analysis": item.get("competitor_analysis", ""),
        "category": item.get("category", ""),
        "content_kind": item.get("content_kind", "news"),
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


# The LLM's own relevance verdict (summarize/pipeline.py's mark_article_irrelevant)
# — never removes the article from any index, just flags it, so every read
# path has to filter it out itself. `not_exists()` keeps items that predate
# this field (there shouldn't be any, but see db.py's pending_articles for
# the same NOT EXISTS-or-not-equal idiom against a differently-meaning-missing
# field) from being silently dropped: DynamoDB treats a comparison against a
# missing attribute as non-matching, not as "unequal", so a bare `.ne()` alone
# would filter out anything that happens to lack summary_status entirely.
_NOT_IRRELEVANT = Attr("summary_status").not_exists() | Attr("summary_status").ne("irrelevant")

# dedup's own verdict (dedup/layers.py, via mark_article_duplicate) — same
# story as _NOT_IRRELEVANT, a different field entirely (summary_status and
# dedup_decision are deliberately independent, see radar_pipeline/README.md's
# "summary_status / dedup_decision independence"), so it needs its own
# exclusion here too. Two articles for the literal same story can land at
# different article_hashes (different URLs — e.g. a publisher exposing the
# same article at both /noticias/<slug> and /noticias/categoria/x/<slug>) and
# still get caught by dedup's title-hash layer; without this, that duplicate
# stayed fully visible on every index regardless.
_NOT_DUPLICATE = Attr("dedup_decision").not_exists() | Attr("dedup_decision").ne("duplicate")

_PUBLIC_ONLY = _NOT_IRRELEVANT & _NOT_DUPLICATE


def _query_paginated(
    table,
    index_name: str,
    key_condition,
    limit: int,
    start_key: dict | None,
) -> tuple[list[dict], dict | None]:
    """One page of a plain (no company fan-out) index query, filtered to
    non-irrelevant articles.

    DynamoDB's Limit applies to items *evaluated*, before FilterExpression
    runs — so a single Query can come back with fewer than `limit` matches
    even though more exist, whenever an irrelevant article falls inside that
    page's Limit window. Loops on LastEvaluatedKey until either `limit`
    matches are collected or the index is exhausted, same fix
    _query_one_company already applies for company-scoped queries.
    """
    items: list[dict] = []
    exclusive_start_key = start_key
    while True:
        kwargs: dict[str, Any] = {
            "IndexName": index_name,
            "KeyConditionExpression": key_condition,
            "FilterExpression": _PUBLIC_ONLY,
            "ScanIndexForward": False,
            "Limit": max(limit - len(items), 1),
        }
        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        exclusive_start_key = resp.get("LastEvaluatedKey")
        if len(items) >= limit or not exclusive_start_key:
            break
    return items[:limit], (exclusive_start_key if len(items) >= limit else None)


def _latest_articles(limit: int, cursor: str | None) -> dict:
    table = _table_resource()
    items, last_key = _query_paginated(
        table, "LatestIndex", Key("gsi2pk").eq("ARTICLE"), limit, _decode_cursor(cursor)
    )
    return {
        "items": [_public_article(i) for i in items],
        "next_cursor": _encode_cursor(last_key),
    }


def _articles_by_kind(kind: str, limit: int, cursor: str | None) -> dict:
    """The "one kind, no company" feed — same shape as _latest_articles,
    scoped to KindIndex's gsi5pk instead of LatestIndex's constant one."""
    table = _table_resource()
    items, last_key = _query_paginated(
        table, "KindIndex", Key("gsi5pk").eq(f"KIND#{kind}"), limit, _decode_cursor(cursor)
    )
    return {
        "items": [_public_article(i) for i in items],
        "next_cursor": _encode_cursor(last_key),
    }


def _query_one_company(
    table,
    company: str,
    since: str | None,
    until: str | None,
    kind: str | None,
    limit: int,
    start_key: dict | None,
) -> tuple[list[dict], dict | None]:
    """One company's items for one page, optionally kind-filtered, always
    excluding irrelevant/duplicate articles (_PUBLIC_ONLY).

    kind filtering uses a FilterExpression against content_kind
    (denormalized onto every company-link item), not a dedicated index —
    same reasoning as db.py's _query_company: a single company's result set
    is already narrow. DynamoDB's Limit applies before the FilterExpression
    runs, so a single Query can come back with fewer than `limit` matches
    even though more exist — this loops on LastEvaluatedKey until either
    `limit` matches are collected or this company's results (within the
    date range) are exhausted.
    """
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

    base_kwargs: dict[str, Any] = {
        "IndexName": "CompanyTimeIndex",
        "KeyConditionExpression": key_cond,
        "ScanIndexForward": False,
    }
    base_kwargs["FilterExpression"] = (
        _PUBLIC_ONLY & Attr("content_kind").eq(kind) if kind else _PUBLIC_ONLY
    )

    items: list[dict] = []
    exclusive_start_key = start_key
    while True:
        kwargs = dict(base_kwargs, Limit=max(limit - len(items), 1))
        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        exclusive_start_key = resp.get("LastEvaluatedKey")
        if len(items) >= limit or not exclusive_start_key:
            break
    return items[:limit], (exclusive_start_key if len(items) >= limit else None)


def _company_articles(
    companies: list[str],
    since: str | None,
    until: str | None,
    kind: str | None,
    limit: int,
    cursor: str | None,
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
        items, last_key = _query_one_company(
            table, company, since, until, kind, limit, prev_keys.get(company)
        )
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
    kind_param = params.get("kind")

    if kind_param and kind_param not in VALID_KINDS:
        return _response(400, {"error": f"kind must be one of {sorted(VALID_KINDS)}"})

    try:
        if company_param:
            companies = [c.strip() for c in company_param.split(",") if c.strip()]
            if not companies:
                return _response(400, {"error": "company must not be empty"})
            result = _company_articles(
                companies, params.get("from"), params.get("to"), kind_param, limit, cursor
            )
        elif kind_param:
            result = _articles_by_kind(kind_param, limit, cursor)
        else:
            result = _latest_articles(limit, cursor)
    except Exception as exc:  # noqa: BLE001 — a clean 500, not a stack trace to the client
        print(f"ERROR: {exc}")
        return _response(500, {"error": "internal error"})

    return _response(200, result)
