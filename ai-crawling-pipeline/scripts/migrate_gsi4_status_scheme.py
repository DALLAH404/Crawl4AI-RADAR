#!/usr/bin/env python3
"""One-time fix: rewrite articles stuck on the old PendingIndex GSI scheme.

Background: pending_articles()/failed_articles() find their work via a
sparse GSI attribute on each article, gsi4pk. It used to be a constant
"PENDING" string; the --retry-failed feature changed it to
"STATUS#pending" / "STATUS#failed" so the same index could also hold
retry candidates. That change is safe for anything written from then on —
db._base_item recomputes gsi4pk fresh on every write — but it does nothing
for articles that were already sitting in the table under the old scheme
and haven't been written to since. Their summary_status attribute is still
correctly 'pending'/'failed', but their gsi4pk is stuck at the literal old
"PENDING" string, which the current code never queries for — so they're
permanently invisible to pending_articles()/failed_articles() until
something rewrites them.

This scans the table directly (bypassing the stale index — that's the
whole problem with querying it here) for base article items whose
summary_status is 'pending' or 'failed', and calls update_article with that
same status (a no-op value change) purely to force db._write_article to
recompute gsi4pk/gsi4sk under the current scheme. Safe to run more than
once — anything already on the current scheme is skipped.

Usage:
    uv run python scripts/migrate_gsi4_status_scheme.py --table-name radar-articles --region <REGION>
"""

from __future__ import annotations

import argparse
import sys

from boto3.dynamodb.conditions import Attr

from radar_pipeline.db import connect, update_article


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-name", default="radar-articles")
    parser.add_argument("--region")
    parser.add_argument("--endpoint-url", help="Local/dev only (dynamodb-local, moto)")
    args = parser.parse_args()

    store = connect(table_name=args.table_name, region_name=args.region, endpoint_url=args.endpoint_url)

    scan_kwargs = {
        "FilterExpression": Attr("sk").eq("METADATA") & Attr("summary_status").is_in(["pending", "failed"]),
    }
    items = []
    resp = store.table.scan(**scan_kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = store.table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **scan_kwargs)
        items.extend(resp.get("Items", []))

    print(f"Found {len(items)} article(s) with summary_status pending/failed.")

    fixed = 0
    for item in items:
        article_hash = item["article_hash"]
        status = item["summary_status"]
        if item.get("gsi4pk") == f"STATUS#{status}":
            continue  # already on the current scheme
        update_article(store, article_hash, summary_status=status)
        fixed += 1
        print(f"  rewrote {article_hash} ({status})")

    print(f"Done: {fixed} article(s) migrated, {len(items) - fixed} already on the current scheme.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
