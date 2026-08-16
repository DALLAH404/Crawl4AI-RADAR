#!/usr/bin/env python3
"""One-time backfill: add content_kind (and its KindIndex GSI entry) to
articles collected before that field existed.

Background: content_kind ("news" or "social") is denormalized onto every
article at collect time, same as feed_type. Articles already in the table
from before this feature was added have neither content_kind nor a gsi5pk
— db._as_full_article backfills the *field* to its dataclass default
("news") when reading them, so nothing crashes, but that default is wrong
for anything actually collected from a social source (LinkedIn today), and
more importantly nothing pre-existing is indexed in KindIndex at all until
it's rewritten.

This scans for base article items missing content_kind, infers the correct
value from the article's own feed_type (linkedin_company -> social,
everything else -> news — the same inference catalog.py uses for sources
that don't set content_kind explicitly), and calls update_article with it.
That single call is enough: update_article's read-merge-rewrite regenerates
the base item (now with a correct gsi5pk) *and* every one of that article's
company-link items (now carrying the correct content_kind attribute too) —
see db._write_article. No separate pass over company-link items needed.

Safe to run more than once — anything that already has content_kind is
left alone.

Usage:
    uv run python scripts/migrate_content_kind.py --table-name radar-articles --region <REGION>
"""

from __future__ import annotations

import argparse
import sys

from boto3.dynamodb.conditions import Attr

from radar_pipeline.db import connect, update_article

SOCIAL_FEED_TYPES = {"linkedin_company"}


def _infer_content_kind(feed_type: str) -> str:
    return "social" if feed_type in SOCIAL_FEED_TYPES else "news"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-name", default="radar-articles")
    parser.add_argument("--region")
    parser.add_argument("--endpoint-url", help="Local/dev only (dynamodb-local, moto)")
    args = parser.parse_args()

    store = connect(table_name=args.table_name, region_name=args.region, endpoint_url=args.endpoint_url)

    scan_kwargs = {
        "FilterExpression": Attr("sk").eq("METADATA") & Attr("content_kind").not_exists(),
    }
    items = []
    resp = store.table.scan(**scan_kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = store.table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **scan_kwargs)
        items.extend(resp.get("Items", []))

    print(f"Found {len(items)} article(s) missing content_kind.")

    fixed = 0
    for item in items:
        article_hash = item["article_hash"]
        feed_type = item.get("feed_type", "")
        kind = _infer_content_kind(feed_type)
        update_article(store, article_hash, content_kind=kind)
        fixed += 1
        print(f"  rewrote {article_hash} (feed_type={feed_type or '(none)'} -> content_kind={kind})")

    print(f"Done: {fixed} article(s) migrated (base item + its company-link items each).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
