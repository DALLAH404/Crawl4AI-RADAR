"""Tests for the DynamoDB storage layer."""

from __future__ import annotations

from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key

from radar_pipeline.db import (
    find_by_article_hash,
    find_by_raw_link,
    find_by_title_hash_within,
    get_article,
    latest_articles,
    mark_article_irrelevant,
    articles_for_company,
    pending_articles,
    put_article,
    update_article,
)
from radar_pipeline.models import Article


def _make_article(**kwargs) -> Article:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    defaults = {
        "article_hash": "abc123",
        "title_hash": "th1",
        "published_at": "2026-08-01",
        "collected_at": now,
        "category": "auto",
        "competitor_tag": "Test",
        "title": "Test article",
        "link": "https://example.com/test",
        "source_id": "test-source",
        "source_name": "Test Source",
        "feed_type": "google_news_query",
    }
    defaults.update(kwargs)
    return Article(**defaults)


class TestTable:
    def test_gsis_exist(self, store):
        desc = store.table.meta.client.describe_table(TableName=store.table.table_name)
        gsi_names = {g["IndexName"] for g in desc["Table"].get("GlobalSecondaryIndexes", [])}
        assert gsi_names == {"CompanyTimeIndex", "LatestIndex", "DedupIndex", "PendingIndex"}


class TestArticles:
    def test_insert_and_get(self, store):
        a = _make_article(article_hash="xyz789", link="https://example.com/findme")
        assert put_article(store, a) is True

        found = get_article(store, "xyz789")
        assert found is not None
        assert found["title"] == "Test article"

    def test_idempotent_insert_does_not_duplicate(self, store):
        """Phase 1 requirement: running the same insert twice must not create
        a duplicate item — the second call is a no-op, not a second row."""
        a1 = _make_article(article_hash="dupe-hash", link="https://example.com/article-1")
        a2 = _make_article(article_hash="dupe-hash", link="https://example.com/a-different-url-but-same-hash")

        assert put_article(store, a1) is True
        assert put_article(store, a2) is False  # rejected: article_hash already exists

        # Exactly one item under this hash, and it's the FIRST write's data
        # (a conditional put never overwrites — a re-run doesn't clobber
        # fields another process may have already updated, e.g. a summary).
        found = get_article(store, "dupe-hash")
        assert found["link"] == "https://example.com/article-1"

        resp = store.table.query(
            KeyConditionExpression=Key("pk").eq("ARTICLE#dupe-hash")
        )
        base_items = [i for i in resp["Items"] if i["sk"] == "METADATA"]
        assert len(base_items) == 1

    def test_find_by_hash(self, store):
        put_article(store, _make_article(article_hash="xyz789", link="https://example.com/findme"))

        found = find_by_article_hash(store, "xyz789")
        assert found is not None
        assert found["title"] == "Test article"

        assert find_by_article_hash(store, "nonexistent") is None

    def test_find_by_raw_link(self, store):
        put_article(store, _make_article(
            article_hash="rawlinkhash",
            link="https://real.example.com/resolved",
            raw_link="https://news.google.com/articles/abc",
        ))

        found = find_by_raw_link(store, "https://news.google.com/articles/abc")
        assert found is not None
        assert found["link"] == "https://real.example.com/resolved"

        assert find_by_raw_link(store, "https://nope.example/") is None

    def test_find_by_raw_link_empty_returns_none(self, store):
        assert find_by_raw_link(store, "") is None

    def test_irrelevant_is_excluded_from_dedup_lookups(self, store):
        put_article(store, _make_article(article_hash="irrelevant-one", link="https://example.com/irrel"))

        assert find_by_article_hash(store, "irrelevant-one") is not None

        mark_article_irrelevant(store, "irrelevant-one")

        assert find_by_article_hash(store, "irrelevant-one") is None
        # ...but the article itself is still readable directly.
        assert get_article(store, "irrelevant-one")["summary_status"] == "irrelevant"

    def test_find_by_title_hash_within_window(self, store):
        put_article(store, _make_article(
            article_hash="title-a", title_hash="shared-title-hash",
            published_at="2026-08-10T12:00:00Z",
        ))

        found = find_by_title_hash_within(store, "shared-title-hash", "2026-08-01T00:00:00Z")
        assert found is not None
        assert found["article_hash"] == "title-a"

        assert find_by_title_hash_within(store, "shared-title-hash", "2026-08-15T00:00:00Z") is None

    def test_pending_articles(self, store):
        for i in range(3):
            put_article(store, _make_article(
                article_hash=f"pending{i}", link=f"https://example.com/art{i}",
                summary_status="pending",
            ))
        put_article(store, _make_article(
            article_hash="generated", link="https://example.com/done",
            summary_status="ai_generated",
        ))

        pending = pending_articles(store)
        assert len(pending) == 3

    def test_pending_articles_includes_feed_type(self, store):
        put_article(store, _make_article(article_hash="with-feed-type", link="https://example.com/ft"))

        pending = pending_articles(store)
        assert pending[0]["feed_type"] == "google_news_query"

    def test_update_article_removes_from_pending_index(self, store):
        put_article(store, _make_article(article_hash="to-summarize", link="https://example.com/s"))
        assert len(pending_articles(store)) == 1

        update_article(store, "to-summarize", summary_status="ai_generated")
        assert len(pending_articles(store)) == 0

    def test_latest_articles_ordering(self, store):
        put_article(store, _make_article(article_hash="older", link="https://example.com/1", published_at="2026-08-01"))
        put_article(store, _make_article(article_hash="newer", link="https://example.com/2", published_at="2026-08-10"))

        latest = latest_articles(store, limit=10)
        assert [a["article_hash"] for a in latest] == ["newer", "older"]

    def test_articles_for_company_time_range(self, store):
        put_article(store, _make_article(
            article_hash="bosch-1", competitor_tag="Bosch",
            link="https://example.com/b1", published_at="2026-08-05",
        ))
        put_article(store, _make_article(
            article_hash="valeo-1", competitor_tag="Valeo",
            link="https://example.com/v1", published_at="2026-08-05",
        ))

        bosch = articles_for_company(store, "Bosch", since_ts="2026-08-01")
        assert [a["article_hash"] for a in bosch] == ["bosch-1"]

    def test_multi_company_article_appears_in_both_feeds(self, store):
        put_article(store, _make_article(
            article_hash="shared", competitor_tag="Bosch,Valeo",
            link="https://example.com/shared", published_at="2026-08-05",
        ))

        assert [a["article_hash"] for a in articles_for_company(store, "Bosch", "2026-08-01")] == ["shared"]
        assert [a["article_hash"] for a in articles_for_company(store, "Valeo", "2026-08-01")] == ["shared"]
