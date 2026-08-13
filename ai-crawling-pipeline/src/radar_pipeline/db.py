"""DynamoDB storage layer for the Radar Aftermarket Pipeline.

Single table, four GSIs. Identity is `article_hash` (md5 of the resolved
article URL) everywhere — there is no auto-increment ID; DynamoDB has no
equivalent of SQLite's ROWID, and keying writes off a value derived from
the article itself is what makes them idempotent (a re-scraped article
overwrites the same item instead of creating a new one).

Item types (all in the one `radar-articles` table):

    Base article    pk=ARTICLE#<hash>          sk=METADATA
                     gsi2pk=ARTICLE             gsi2sk=<ts>#<hash>   (LatestIndex)
                     gsi3pk=TITLEHASH#<hash>    gsi3sk=<ts>#<hash>   (DedupIndex)
                     gsi4pk=STATUS#<status>     gsi4sk=<ts>#<hash>   (PendingIndex,
                                                                      only while status
                                                                      is 'pending' or
                                                                      'failed' — see
                                                                      retry_failed_articles)

    Raw-link pointer pk=ARTICLE#<hash>          sk=RAWLINK#<rawhash>
                     gsi3pk=RAWLINK#<rawhash>   gsi3sk=METADATA      (DedupIndex)

    Company link     pk=ARTICLE#<hash>          sk=COMPANY#<tag>
                     gsi1pk=COMPANY#<tag>       gsi1sk=<ts>#<hash>   (CompanyTimeIndex)
                     (denormalized display fields, so the per-company feed
                     reads in one Query with no follow-up GetItem)

All four GSIs project ALL attributes — item sizes here are tiny (a news
article's text, not a blob) so the extra storage/WCU cost buys skipping an
N+1 GetItem on every read path.

`ensure_table()` is for local/dev use only (moto, dynamodb-local) — the
production table is created once via the AWS console/CLI steps in
DEPLOYMENT.md, not by the application on every boot.

Public API:
    connect(table_name, region_name, endpoint_url) -> RadarStore
    ensure_table(store) -> None                          (local/dev only)
    put_article(store, article) -> bool                  (idempotent insert)
    update_article(store, article_hash, **changes) -> dict | None
    get_article(store, article_hash) -> dict | None
    find_by_article_hash(store, article_hash, exclude_hash=None) -> dict | None
    find_by_raw_link(store, raw_link) -> dict | None
    find_by_title_hash_within(store, title_hash, since_ts, exclude_hash=None) -> dict | None
    pending_articles(store, limit=None) -> list[dict]
    failed_articles(store, limit=None) -> list[dict]
    retry_failed_articles(store) -> int
    latest_articles(store, limit=20) -> list[dict]
    articles_for_company(store, company, since_ts, until_ts=None, limit=None) -> list[dict]
    articles_for_companies(store, companies, since_ts, until_ts=None, limit=None) -> list[dict]
    mark_article_irrelevant(store, article_hash) -> None
    mark_article_failed(store, article_hash, error="") -> None
    article_count(store) -> int
    article_count_by_status(store) -> dict[str, int]
    article_count_by_category(store) -> dict[str, int]
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from radar_pipeline.models import Article

DEFAULT_TABLE_NAME = "radar-articles"

_INTERNAL_KEYS = {
    "pk", "sk",
    "gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk", "gsi3pk", "gsi3sk", "gsi4pk", "gsi4sk",
}


@dataclass
class RadarStore:
    table: Any  # boto3 DynamoDB Table resource


def connect(
    table_name: str | None = None,
    region_name: str | None = None,
    endpoint_url: str | None = None,
) -> RadarStore:
    table_name = table_name or os.environ.get("RADAR_TABLE_NAME", DEFAULT_TABLE_NAME)
    kwargs: dict[str, Any] = {}
    if region_name:
        kwargs["region_name"] = region_name
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    resource = boto3.resource("dynamodb", **kwargs)
    return RadarStore(table=resource.Table(table_name))


def ensure_table(store: RadarStore) -> None:
    """Create the table (on-demand billing) with its 4 GSIs if it doesn't
    already exist. Local/dev only — see the module docstring."""
    client = store.table.meta.client
    table_name = store.table.table_name
    if table_name in client.list_tables().get("TableNames", []):
        return

    client.create_table(
        TableName=table_name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "gsi1pk", "AttributeType": "S"},
            {"AttributeName": "gsi1sk", "AttributeType": "S"},
            {"AttributeName": "gsi2pk", "AttributeType": "S"},
            {"AttributeName": "gsi2sk", "AttributeType": "S"},
            {"AttributeName": "gsi3pk", "AttributeType": "S"},
            {"AttributeName": "gsi3sk", "AttributeType": "S"},
            {"AttributeName": "gsi4pk", "AttributeType": "S"},
            {"AttributeName": "gsi4sk", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "CompanyTimeIndex",
                "KeySchema": [
                    {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "LatestIndex",
                "KeySchema": [
                    {"AttributeName": "gsi2pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi2sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "DedupIndex",
                "KeySchema": [
                    {"AttributeName": "gsi3pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi3sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "PendingIndex",
                "KeySchema": [
                    {"AttributeName": "gsi4pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi4sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    )
    client.get_waiter("table_exists").wait(TableName=table_name)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _clean(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in _INTERNAL_KEYS}


# Article() with every field at its dataclass default — computed once, reused
# by _as_full_article for every "full article" read. `companies()` is a
# method, not a field, so it's correctly absent here.
_ARTICLE_DEFAULTS = asdict(Article())


def _as_full_article(item: dict) -> dict:
    """Fill in Article's field defaults for anything DynamoDB doesn't have —
    empty-string attributes aren't stored (see _base_item), so a pending
    article that's never been summarized has no 'summary' key at all, not an
    empty one. Bracket access (article["summary"]) on that raises KeyError;
    every caller across fetch/dedup/summarize/cli assumed the SQLite-era
    guarantee that every column always exists, empty or not. Restoring that
    guarantee here — once, for every full-article read — is more robust than
    auditing every call site for .get() usage (which already missed two
    files' worth of call sites once). Company-link items are a deliberately
    partial view of an article and do NOT go through this — see
    articles_for_company(ies)."""
    return {**_ARTICLE_DEFAULTS, **_clean(item)}


def _sort_ts(article: Article) -> str:
    # published_at is sometimes just a date ("2026-08-01"), sometimes a full
    # timestamp; either way it's a fixed-width ISO8601 prefix, so plain
    # string comparison against a same-shaped `since_ts` sorts correctly.
    # collected_at (always set at construction) is the fallback so this is
    # never empty — DynamoDB rejects an empty string as a key attribute.
    return article.published_at or article.collected_at


def _base_item(article: Article) -> dict:
    ts = _sort_ts(article)
    item: dict[str, Any] = {
        "pk": f"ARTICLE#{article.article_hash}",
        "sk": "METADATA",
        "article_hash": article.article_hash,
        "title_hash": article.title_hash,
        "published_at": article.published_at,
        "collected_at": article.collected_at,
        "category": article.category,
        "competitor_tag": article.competitor_tag,
        "companies": article.companies(),
        "product_line": article.product_line,
        "title": article.title,
        "action_description": article.action_description,
        "summary": article.summary,
        "competitor_analysis": article.competitor_analysis,
        "summary_status": article.summary_status,
        "ai_model": article.ai_model,
        "ai_processed_at": article.ai_processed_at,
        "event_type": article.event_type,
        "alert_level": article.alert_level,
        "is_launch": bool(article.is_launch),
        "image_url": article.image_url,
        "link": article.link,
        "raw_link": article.raw_link,
        "ingestion_batch_id": article.ingestion_batch_id,
        "source_id": article.source_id,
        "source_name": article.source_name,
        "feed_type": article.feed_type,
        "dedup_layer": article.dedup_layer,
        "dedup_decision": article.dedup_decision,
        "dedup_reason": article.dedup_reason,
        "dedup_match_hash": article.dedup_match_hash,
        "dedup_score": Decimal(str(article.dedup_score)) if article.dedup_score is not None else None,
        "extra": article.extra,
        "gsi2pk": "ARTICLE",
        "gsi2sk": f"{ts}#{article.article_hash}",
    }
    if article.title_hash:
        item["gsi3pk"] = f"TITLEHASH#{article.title_hash}"
        item["gsi3sk"] = f"{ts}#{article.article_hash}"
    if article.summary_status in ("pending", "failed"):
        # Sparse on purpose, same as the other GSIs — only articles that
        # might still need work sit in this index. 'failed' is included
        # (not just 'pending') so retry_failed_articles() has something to
        # query without a full-table Scan; 'ai_generated'/'irrelevant' are
        # terminal and never revisited via this GSI.
        item["gsi4pk"] = f"STATUS#{article.summary_status}"
        item["gsi4sk"] = f"{article.collected_at}#{article.article_hash}"
    return {k: v for k, v in item.items() if v not in (None, "")}


def _rawlink_item(article: Article) -> dict:
    raw_hash = _md5(article.raw_link)
    return {
        "pk": f"ARTICLE#{article.article_hash}",
        "sk": f"RAWLINK#{raw_hash}",
        "gsi3pk": f"RAWLINK#{raw_hash}",
        "gsi3sk": "METADATA",
        "article_hash": article.article_hash,
        "link": article.link,
    }


def _company_item(article: Article, tag: str) -> dict:
    ts = _sort_ts(article)
    item = {
        "pk": f"ARTICLE#{article.article_hash}",
        "sk": f"COMPANY#{tag}",
        "gsi1pk": f"COMPANY#{tag}",
        "gsi1sk": f"{ts}#{article.article_hash}",
        "article_hash": article.article_hash,
        "company": tag,
        "title": article.title,
        "link": article.link,
        "image_url": article.image_url,
        "category": article.category,
        "event_type": article.event_type,
        "alert_level": article.alert_level,
        "summary": article.summary,
        "summary_status": article.summary_status,
        "published_at": ts,
    }
    return {k: v for k, v in item.items() if v not in (None, "")}


def _write_article(store: RadarStore, article: Article, *, condition_new: bool) -> bool:
    # Not wrapped in a DynamoDB transaction: the base item, raw-link pointer,
    # and company links are three-to-a-few separate put_items rather than one
    # atomic TransactWriteItems call. All three are keyed deterministically
    # off article_hash, so a crash between them just leaves a gap that the
    # next idempotent write (a re-run of the same source) fills in — an
    # acceptable trade for a lot less complexity at this project's scale.
    base_item = _base_item(article)
    try:
        if condition_new:
            store.table.put_item(Item=base_item, ConditionExpression="attribute_not_exists(pk)")
        else:
            store.table.put_item(Item=base_item)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise

    if article.raw_link:
        store.table.put_item(Item=_rawlink_item(article))

    for tag in article.companies():
        store.table.put_item(Item=_company_item(article, tag))

    return True


def put_article(store: RadarStore, article: Article) -> bool:
    """Idempotent insert. Returns False (no-op) if article_hash already
    exists — the caller does not need to check-then-insert separately."""
    return _write_article(store, article, condition_new=True)


def _dict_to_article(d: dict) -> Article:
    fields = {k: v for k, v in d.items() if k in Article.__dataclass_fields__}
    if isinstance(fields.get("dedup_score"), Decimal):
        fields["dedup_score"] = float(fields["dedup_score"])
    if "is_launch" in fields:
        fields["is_launch"] = bool(fields["is_launch"])
    return Article(**fields)


def update_article(store: RadarStore, article_hash: str, **changes: Any) -> dict | None:
    """Read-merge-rewrite. Rewrites the base item plus every company-link
    item for this article, so denormalized display fields never drift."""
    existing = get_article(store, article_hash)
    if existing is None:
        return None
    article = _dict_to_article({**existing, **changes})
    _write_article(store, article, condition_new=False)
    return get_article(store, article_hash)


def get_article(store: RadarStore, article_hash: str) -> dict | None:
    resp = store.table.get_item(Key={"pk": f"ARTICLE#{article_hash}", "sk": "METADATA"})
    item = resp.get("Item")
    return _as_full_article(item) if item else None


def find_by_article_hash(store: RadarStore, article_hash: str, exclude_hash: str | None = None) -> dict | None:
    # article_hash is the table's own partition key, so a lookup can only
    # ever return the row whose hash equals the query — if that's also the
    # excluded hash, there's nothing else it could match. (This mirrors the
    # old SQLite behavior, where the same exclude-self check against a
    # UNIQUE column was likewise always a no-op — preserved for parity.)
    if exclude_hash is not None and article_hash == exclude_hash:
        return None
    item = get_article(store, article_hash)
    # An article already marked irrelevant doesn't count as "already seen"
    # for dedup purposes — matches the old `AND summary_status != 'irrelevant'`
    # clause on this lookup in the SQLite version.
    if item is not None and item.get("summary_status") == "irrelevant":
        return None
    return item


def find_by_raw_link(store: RadarStore, raw_link: str) -> dict | None:
    if not raw_link:
        return None
    raw_hash = _md5(raw_link)
    resp = store.table.query(
        IndexName="DedupIndex",
        KeyConditionExpression=Key("gsi3pk").eq(f"RAWLINK#{raw_hash}"),
        Limit=1,
    )
    items = resp.get("Items", [])
    if not items:
        return None
    return get_article(store, items[0]["article_hash"])


def find_by_title_hash_within(
    store: RadarStore,
    title_hash: str,
    since_ts: str,
    exclude_hash: str | None = None,
) -> dict | None:
    resp = store.table.query(
        IndexName="DedupIndex",
        KeyConditionExpression=(
            Key("gsi3pk").eq(f"TITLEHASH#{title_hash}") & Key("gsi3sk").gte(since_ts)
        ),
        ScanIndexForward=False,
    )
    for item in resp.get("Items", []):
        if exclude_hash and item.get("article_hash") == exclude_hash:
            continue
        if item.get("summary_status") == "irrelevant":
            continue
        return _as_full_article(item)
    return None


def _status_queue(store: RadarStore, status: str, limit: int | None = None) -> list[dict]:
    kwargs: dict[str, Any] = {
        "IndexName": "PendingIndex",
        "KeyConditionExpression": Key("gsi4pk").eq(f"STATUS#{status}"),
        "FilterExpression": (
            Attr("category").is_in(["auto", "economia"])
            & (Attr("dedup_decision").not_exists() | Attr("dedup_decision").ne("duplicate"))
        ),
    }
    items: list[dict] = []
    resp = store.table.query(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = store.table.query(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        items.extend(resp.get("Items", []))
    items.sort(key=lambda i: i.get("collected_at", ""))
    if limit:
        items = items[:limit]
    return [_as_full_article(i) for i in items]


def pending_articles(store: RadarStore, limit: int | None = None) -> list[dict]:
    return _status_queue(store, "pending", limit)


def failed_articles(store: RadarStore, limit: int | None = None) -> list[dict]:
    """Articles whose last summarize attempt failed (LLM error, quota, etc.) —
    retry candidates. These sit in the same sparse GSI as pending_articles
    (STATUS#failed instead of STATUS#pending) so finding them costs a Query,
    not a table Scan."""
    return _status_queue(store, "failed", limit)


def retry_failed_articles(store: RadarStore) -> int:
    """Reset every failed article back to 'pending' so the next --summarize
    picks it up again. Without this, a failed summarize call is permanent —
    mark_article_failed drops the article out of the pending queue for good,
    with no automatic retry. Returns the number of articles reset."""
    articles = failed_articles(store)
    for article in articles:
        update_article(store, article["article_hash"], summary_status="pending")
    return len(articles)


def latest_articles(store: RadarStore, limit: int = 20) -> list[dict]:
    resp = store.table.query(
        IndexName="LatestIndex",
        KeyConditionExpression=Key("gsi2pk").eq("ARTICLE"),
        ScanIndexForward=False,
        Limit=limit,
    )
    return [_as_full_article(i) for i in resp.get("Items", [])]


def articles_for_company(
    store: RadarStore,
    company: str,
    since_ts: str,
    until_ts: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    key_cond = Key("gsi1pk").eq(f"COMPANY#{company}")
    if until_ts:
        key_cond &= Key("gsi1sk").between(since_ts, f"{until_ts}￿")
    else:
        key_cond &= Key("gsi1sk").gte(since_ts)
    kwargs: dict[str, Any] = {
        "IndexName": "CompanyTimeIndex",
        "KeyConditionExpression": key_cond,
        "ScanIndexForward": False,
    }
    if limit:
        kwargs["Limit"] = limit
    resp = store.table.query(**kwargs)
    return [_clean(i) for i in resp.get("Items", [])]


def articles_for_companies(
    store: RadarStore,
    companies: list[str],
    since_ts: str,
    until_ts: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    # DynamoDB Query takes one partition-key value at a time, so an OR
    # filter across companies fans out into one Query per company and merges
    # the (already time-sorted) results in Python, deduping on article_hash
    # in case an article is linked to more than one of the selected companies.
    seen: set[str] = set()
    merged: list[dict] = []
    for company in companies:
        for item in articles_for_company(store, company, since_ts, until_ts, limit):
            if item["article_hash"] in seen:
                continue
            seen.add(item["article_hash"])
            merged.append(item)
    merged.sort(key=lambda i: i.get("published_at", ""), reverse=True)
    return merged[:limit] if limit else merged


def mark_article_irrelevant(store: RadarStore, article_hash: str) -> None:
    update_article(store, article_hash, summary_status="irrelevant")


def mark_article_failed(store: RadarStore, article_hash: str, error: str = "") -> None:
    update_article(store, article_hash, summary_status="failed", extra=error)


def _scan_base_items(store: RadarStore) -> list[dict]:
    kwargs: dict[str, Any] = {"FilterExpression": Attr("sk").eq("METADATA")}
    items: list[dict] = []
    resp = store.table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = store.table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        items.extend(resp.get("Items", []))
    return items


def article_count(store: RadarStore) -> int:
    return sum(1 for i in _scan_base_items(store) if i.get("summary_status") != "irrelevant")


def article_count_by_status(store: RadarStore) -> dict[str, int]:
    counts: dict[str, int] = {}
    for i in _scan_base_items(store):
        status = i.get("summary_status", "")
        counts[status] = counts.get(status, 0) + 1
    return counts


def article_count_by_category(store: RadarStore) -> dict[str, int]:
    counts: dict[str, int] = {}
    for i in _scan_base_items(store):
        if i.get("summary_status") == "irrelevant":
            continue
        category = i.get("category", "")
        counts[category] = counts.get(category, 0) + 1
    return counts
