#!/usr/bin/env python3
"""Manual idempotency check against a real DynamoDB endpoint.

The pytest suite (tests/radar_pipeline/test_db.py::test_idempotent_insert_does_not_duplicate)
already proves this against moto's in-memory mock on every test run. This
script is for the times you want to see it against something more real —
dynamodb-local in Docker, or an actual AWS dev table — per Phase 1 step 5.

Usage:

    # against dynamodb-local (docker run -p 8000:8000 amazon/dynamodb-local)
    uv run python scripts/check_dynamodb_idempotency.py \\
        --endpoint-url http://localhost:8000 --region us-east-1

    # against a real AWS dev table (you created it via DEPLOYMENT.md;
    # requires AWS credentials in the environment this script runs in —
    # NOT this Codespace)
    uv run python scripts/check_dynamodb_idempotency.py \\
        --table-name radar-articles-dev --region us-east-1

Creates its own scratch article (a fixed, clearly-marked hash) so it's safe
to run against a table that already has real data — it never touches
anything else. Exits non-zero on failure.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key

from radar_pipeline.db import connect, ensure_table, get_article, put_article
from radar_pipeline.models import Article

SCRATCH_HASH = "idempotency-check-scratch-item-do-not-use"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-name", default="radar-articles")
    parser.add_argument("--region")
    parser.add_argument("--endpoint-url", help="e.g. http://localhost:8000 for dynamodb-local")
    args = parser.parse_args()

    store = connect(table_name=args.table_name, region_name=args.region, endpoint_url=args.endpoint_url)
    if args.endpoint_url:
        # Local endpoint only — never attempt CreateTable against real AWS
        # from here; the production table is created via DEPLOYMENT.md.
        ensure_table(store)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    article = Article(
        article_hash=SCRATCH_HASH,
        title_hash="idempotency-check-title-hash",
        published_at=now,
        collected_at=now,
        category="auto",
        competitor_tag="IdempotencyCheck",
        title="Idempotency check scratch article",
        link="https://example.invalid/idempotency-check",
        source_id="idempotency-check",
        source_name="Idempotency Check",
        feed_type="google_news_query",
    )

    print(f"Table: {args.table_name}  Endpoint: {args.endpoint_url or '(default AWS)'}")

    first = put_article(store, article)
    print(f"First put_article(): inserted={first}")
    second = put_article(store, article)
    print(f"Second put_article() (same article_hash): inserted={second}")

    resp = store.table.query(KeyConditionExpression=Key("pk").eq(f"ARTICLE#{SCRATCH_HASH}"))
    base_items = [i for i in resp["Items"] if i["sk"] == "METADATA"]
    print(f"Base items under this hash after two writes: {len(base_items)}")

    ok = first is True and second is False and len(base_items) == 1
    print("PASS — writes are idempotent, no duplicate item was created." if ok
          else "FAIL — see counts above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
