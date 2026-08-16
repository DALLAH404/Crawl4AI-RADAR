"""Tests for the read API Lambda, against a moto-mocked DynamoDB table.

Seeds items by hand (raw put_item), not via radar_pipeline.db — this
Lambda is deliberately standalone (see lambda_function.py's module
docstring), so its tests shouldn't depend on the scraper package either.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ["RADAR_TABLE_NAME"] = "radar-articles"

import lambda_function as lf  # noqa: E402


@pytest.fixture
def table():
    with mock_aws():
        lf._table = None  # force a fresh Table() bound to this mock context
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="radar-articles",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "gsi1pk", "AttributeType": "S"},
                {"AttributeName": "gsi1sk", "AttributeType": "S"},
                {"AttributeName": "gsi2pk", "AttributeType": "S"},
                {"AttributeName": "gsi2sk", "AttributeType": "S"},
                {"AttributeName": "gsi5pk", "AttributeType": "S"},
                {"AttributeName": "gsi5sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "LatestIndex",
                    "KeySchema": [
                        {"AttributeName": "gsi2pk", "KeyType": "HASH"},
                        {"AttributeName": "gsi2sk", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "CompanyTimeIndex",
                    "KeySchema": [
                        {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                        {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "KindIndex",
                    "KeySchema": [
                        {"AttributeName": "gsi5pk", "KeyType": "HASH"},
                        {"AttributeName": "gsi5sk", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
        )
        yield boto3.resource("dynamodb", region_name="us-east-1").Table("radar-articles")


def _put_base_item(table, *, article_hash, published_at, title="A title", **extra):
    content_kind = extra.pop("content_kind", "news")
    item = {
        "pk": f"ARTICLE#{article_hash}",
        "sk": "METADATA",
        "article_hash": article_hash,
        "title": title,
        "link": f"https://example.com/{article_hash}",
        "published_at": published_at,
        "category": "auto",
        "content_kind": content_kind,
        "summary": "A summary.",
        "summary_status": "ai_generated",
        "source_name": "Test Source",
        "companies": extra.pop("companies", ["Bosch"]),
        "gsi2pk": "ARTICLE",
        "gsi2sk": f"{published_at}#{article_hash}",
        "gsi5pk": f"KIND#{content_kind}",
        "gsi5sk": f"{published_at}#{article_hash}",
        "dedup_reason": "internal-only-should-never-leak",
        "raw_link": "https://news.google.com/internal-only-should-never-leak",
        **extra,
    }
    table.put_item(Item=item)
    return item


def _put_company_item(table, *, article_hash, company, published_at, title="A title", **extra):
    item = {
        "pk": f"ARTICLE#{article_hash}",
        "sk": f"COMPANY#{company}",
        "article_hash": article_hash,
        "company": company,
        "title": title,
        "link": f"https://example.com/{article_hash}",
        "published_at": published_at,
        "category": "auto",
        "gsi1pk": f"COMPANY#{company}",
        "gsi1sk": f"{published_at}#{article_hash}",
        **extra,
    }
    table.put_item(Item=item)
    return item


def _event(query: dict | None = None, method: str = "GET") -> dict:
    return {
        "requestContext": {"http": {"method": method}},
        "queryStringParameters": query or {},
    }


class TestLatestArticles:
    def test_returns_items_newest_first(self, table):
        _put_base_item(table, article_hash="old", published_at="2026-08-01")
        _put_base_item(table, article_hash="new", published_at="2026-08-10")

        resp = lf.handler(_event({"limit": "10"}), None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert [a["article_hash"] for a in body["items"]] == ["new", "old"]
        assert body["next_cursor"] is None

    def test_never_leaks_internal_fields(self, table):
        _put_base_item(table, article_hash="a1", published_at="2026-08-01")
        body = json.loads(lf.handler(_event({}), None)["body"])
        article = body["items"][0]
        assert "dedup_reason" not in article
        assert "raw_link" not in article
        assert set(article) == {
            "article_hash", "title", "link", "image_url", "summary",
            "competitor_analysis", "category", "content_kind", "event_type",
            "alert_level", "summary_status", "published_at", "companies", "source_name",
        }

    def test_pagination_cursor_advances(self, table):
        for i in range(3):
            _put_base_item(table, article_hash=f"a{i}", published_at=f"2026-08-0{i+1}")

        page1 = json.loads(lf.handler(_event({"limit": "2"}), None)["body"])
        assert len(page1["items"]) == 2
        assert page1["next_cursor"] is not None

        page2 = json.loads(lf.handler(_event({"limit": "2", "cursor": page1["next_cursor"]}), None)["body"])
        assert len(page2["items"]) == 1
        assert page2["next_cursor"] is None

        seen = {a["article_hash"] for a in page1["items"]} | {a["article_hash"] for a in page2["items"]}
        assert seen == {"a0", "a1", "a2"}


class TestCompanyArticles:
    def test_single_company_time_range(self, table):
        _put_company_item(table, article_hash="b1", company="Bosch", published_at="2026-08-05")
        _put_company_item(table, article_hash="v1", company="Valeo", published_at="2026-08-05")

        body = json.loads(lf.handler(_event({"company": "Bosch", "from": "2026-08-01"}), None)["body"])
        assert [a["article_hash"] for a in body["items"]] == ["b1"]

    def test_date_range_from_and_to(self, table):
        _put_company_item(table, article_hash="in-range", company="Bosch", published_at="2026-08-15")
        _put_company_item(table, article_hash="too-late", company="Bosch", published_at="2026-09-15")

        body = json.loads(
            lf.handler(_event({"company": "Bosch", "from": "2026-08-01", "to": "2026-08-31"}), None)["body"]
        )
        assert [a["article_hash"] for a in body["items"]] == ["in-range"]

    def test_multi_company_merges_and_dedupes(self, table):
        _put_company_item(table, article_hash="shared", company="Bosch", published_at="2026-08-05")
        _put_company_item(table, article_hash="shared", company="Valeo", published_at="2026-08-05")
        _put_company_item(table, article_hash="bosch-only", company="Bosch", published_at="2026-08-06")

        body = json.loads(lf.handler(_event({"company": "Bosch,Valeo", "from": "2026-08-01"}), None)["body"])
        hashes = [a["article_hash"] for a in body["items"]]
        assert sorted(hashes) == ["bosch-only", "shared"]
        assert len(hashes) == len(set(hashes))  # no duplicate despite being linked to both companies

    def test_company_item_field_subset_still_valid_shape(self, table):
        _put_company_item(table, article_hash="b1", company="Bosch", published_at="2026-08-05")
        body = json.loads(lf.handler(_event({"company": "Bosch", "from": "2026-08-01"}), None)["body"])
        article = body["items"][0]
        assert article["companies"] == ["Bosch"]
        assert article["summary"] == ""  # not denormalized onto company-link items


class TestKindFiltering:
    def test_kind_alone_no_company(self, table):
        _put_base_item(table, article_hash="news-1", published_at="2026-08-05", content_kind="news")
        _put_base_item(table, article_hash="social-1", published_at="2026-08-06", content_kind="social")

        news = json.loads(lf.handler(_event({"kind": "news"}), None)["body"])
        social = json.loads(lf.handler(_event({"kind": "social"}), None)["body"])
        assert [a["article_hash"] for a in news["items"]] == ["news-1"]
        assert [a["article_hash"] for a in social["items"]] == ["social-1"]

    def test_no_kind_no_company_is_unfiltered_latest(self, table):
        _put_base_item(table, article_hash="news-1", published_at="2026-08-05", content_kind="news")
        _put_base_item(table, article_hash="social-1", published_at="2026-08-06", content_kind="social")

        body = json.loads(lf.handler(_event({}), None)["body"])
        assert {a["article_hash"] for a in body["items"]} == {"news-1", "social-1"}

    def test_company_and_kind_combined(self, table):
        _put_company_item(table, article_hash="bosch-news", company="Bosch", published_at="2026-08-05", content_kind="news")
        _put_company_item(table, article_hash="bosch-social", company="Bosch", published_at="2026-08-06", content_kind="social")

        news_only = json.loads(
            lf.handler(_event({"company": "Bosch", "from": "2026-08-01", "kind": "news"}), None)["body"]
        )
        social_only = json.loads(
            lf.handler(_event({"company": "Bosch", "from": "2026-08-01", "kind": "social"}), None)["body"]
        )
        both = json.loads(lf.handler(_event({"company": "Bosch", "from": "2026-08-01"}), None)["body"])

        assert [a["article_hash"] for a in news_only["items"]] == ["bosch-news"]
        assert [a["article_hash"] for a in social_only["items"]] == ["bosch-social"]
        assert {a["article_hash"] for a in both["items"]} == {"bosch-news", "bosch-social"}

    def test_company_and_kind_honors_limit_past_dynamodb_prefilter_quirk(self, table):
        # Interleaved news/social for the same company — Limit applies
        # before the FilterExpression, so a naive single Query for
        # kind=social with limit=3 could come back with fewer than 3 even
        # though 5 exist. This is exactly what _query_one_company's loop
        # in lambda_function.py exists to prevent.
        for i in range(5):
            _put_company_item(
                table, article_hash=f"news-{i}", company="Bosch",
                published_at=f"2026-08-{10+i}", content_kind="news",
            )
            _put_company_item(
                table, article_hash=f"social-{i}", company="Bosch",
                published_at=f"2026-08-{10+i}", content_kind="social",
            )

        body = json.loads(
            lf.handler(_event({"company": "Bosch", "from": "2026-08-01", "kind": "social", "limit": "3"}), None)["body"]
        )
        assert len(body["items"]) == 3
        assert all(a["article_hash"].startswith("social-") for a in body["items"])

    def test_multi_company_with_kind(self, table):
        _put_company_item(table, article_hash="bosch-social", company="Bosch", published_at="2026-08-05", content_kind="social")
        _put_company_item(table, article_hash="valeo-news", company="Valeo", published_at="2026-08-06", content_kind="news")

        body = json.loads(lf.handler(_event({"company": "Bosch,Valeo", "kind": "social"}), None)["body"])
        assert [a["article_hash"] for a in body["items"]] == ["bosch-social"]

    def test_invalid_kind_rejected(self, table):
        resp = lf.handler(_event({"kind": "not-a-real-kind"}), None)
        assert resp["statusCode"] == 400


class TestHandlerValidation:
    def test_options_request_is_ok_with_no_body_work(self, table):
        resp = lf.handler(_event({}, method="OPTIONS"), None)
        assert resp["statusCode"] == 200

    def test_unsupported_method_rejected(self, table):
        resp = lf.handler(_event({}, method="POST"), None)
        assert resp["statusCode"] == 405

    def test_invalid_limit_rejected(self, table):
        resp = lf.handler(_event({"limit": "not-a-number"}), None)
        assert resp["statusCode"] == 400

    def test_limit_is_clamped_to_max(self, table):
        for i in range(5):
            _put_base_item(table, article_hash=f"a{i}", published_at=f"2026-08-0{i+1}")
        resp = lf.handler(_event({"limit": str(lf.MAX_LIMIT + 1000)}), None)
        assert resp["statusCode"] == 200

    def test_empty_company_param_rejected(self, table):
        resp = lf.handler(_event({"company": "  ,  "}), None)
        assert resp["statusCode"] == 400

    def test_cors_header_present(self, table):
        resp = lf.handler(_event({}), None)
        assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
