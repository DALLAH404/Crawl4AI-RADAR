#!/usr/bin/env python3
"""Wipe every item from the radar table without touching its schema.

DynamoDB has no bulk "delete everything" API — this scans the whole table
for every item's key (pk, sk; the only attributes a delete needs), then
deletes them via Table.batch_writer(), which handles the 25-item-per-request
BatchWriteItem chunking and retries on throttling automatically. The table
itself and all of its GSIs (CompanyTimeIndex, LatestIndex, DedupIndex,
PendingIndex, KindIndex) are untouched — only data goes away, so the
read-API keeps working (just returning empty results) the whole time,
instead of the downtime a delete-and-recreate would cause.

Refuses to do anything without --yes — this deletes every article, every
company-link item, and every raw-link dedup pointer in the table,
irreversibly.

Standalone on purpose — only needs boto3 (already on CloudShell), not the
radar_pipeline package installed. connect()-and-scan is all radar_pipeline.db
would have given us here anyway.

Usage:
    python3 clear_table.py --table-name radar-articles --region <REGION> --yes
"""

from __future__ import annotations

import argparse
import sys

import boto3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-name", default="radar-articles")
    parser.add_argument("--region")
    parser.add_argument("--endpoint-url", help="Local/dev only (dynamodb-local, moto)")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete. Without this, only counts and prints what would be deleted.",
    )
    args = parser.parse_args()

    kwargs: dict[str, str] = {}
    if args.region:
        kwargs["region_name"] = args.region
    if args.endpoint_url:
        kwargs["endpoint_url"] = args.endpoint_url
    table = boto3.resource("dynamodb", **kwargs).Table(args.table_name)

    keys: list[dict] = []
    resp = table.scan(ProjectionExpression="pk, sk")
    keys.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ProjectionExpression="pk, sk", ExclusiveStartKey=resp["LastEvaluatedKey"])
        keys.extend(resp.get("Items", []))

    print(f"Found {len(keys)} item(s) in table {args.table_name!r}.")

    if not args.yes:
        print("Dry run — nothing deleted. Re-run with --yes to actually delete.")
        return 0

    with table.batch_writer() as batch:
        for key in keys:
            batch.delete_item(Key={"pk": key["pk"], "sk": key["sk"]})

    print(f"Deleted {len(keys)} item(s) from {args.table_name!r}. Table and GSIs left intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
