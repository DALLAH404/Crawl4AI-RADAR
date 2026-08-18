"""Tests for the DynamoDB storage layer."""

from __future__ import annotations

from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key

from radar_pipeline.db import (
    articles_by_kind,
    articles_for_company,
    articles_for_companies,
    failed_articles,
    find_by_article_hash,
    find_by_raw_link,
    find_by_title_hash_within,
    get_article,
    latest_articles,
    mark_article_failed,
    mark_article_irrelevant,
    pending_articles,
    put_article,
    retry_failed_articles,
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
        assert gsi_names == {"CompanyTimeIndex", "LatestIndex", "DedupIndex", "PendingIndex", "KindIndex"}


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

    def test_pending_articles_includes_tecnologia_category(self, store):
        put_article(store, _make_article(
            article_hash="tech1", link="https://example.com/tech1",
            category="tecnologia",
        ))
        put_article(store, _make_article(
            article_hash="auto1", link="https://example.com/auto1",
            category="auto",
        ))

        pending = pending_articles(store)
        assert {a["article_hash"] for a in pending} == {"tech1", "auto1"}

    def test_pending_articles_excludes_unknown_category(self, store):
        put_article(store, _make_article(
            article_hash="other1", link="https://example.com/other1",
            category="something-else",
        ))

        assert pending_articles(store) == []

    def test_pending_articles_includes_feed_type(self, store):
        put_article(store, _make_article(article_hash="with-feed-type", link="https://example.com/ft"))

        pending = pending_articles(store)
        assert pending[0]["feed_type"] == "google_news_query"

    def test_update_article_removes_from_pending_index(self, store):
        put_article(store, _make_article(article_hash="to-summarize", link="https://example.com/s"))
        assert len(pending_articles(store)) == 1

        update_article(store, "to-summarize", summary_status="ai_generated")
        assert len(pending_articles(store)) == 0

    def test_failed_article_leaves_pending_and_enters_failed_queue(self, store):
        put_article(store, _make_article(article_hash="will-fail", link="https://example.com/fail"))
        assert len(pending_articles(store)) == 1

        mark_article_failed(store, "will-fail", "LLM quota exceeded")

        assert len(pending_articles(store)) == 0
        failed = failed_articles(store)
        assert len(failed) == 1
        assert failed[0]["article_hash"] == "will-fail"
        assert failed[0]["extra"] == "LLM quota exceeded"

    def test_retry_failed_articles_moves_them_back_to_pending(self, store):
        put_article(store, _make_article(article_hash="retry-me", link="https://example.com/retry"))
        mark_article_failed(store, "retry-me", "transient error")
        assert len(pending_articles(store)) == 0
        assert len(failed_articles(store)) == 1

        reset_count = retry_failed_articles(store)

        assert reset_count == 1
        assert len(failed_articles(store)) == 0
        pending = pending_articles(store)
        assert len(pending) == 1
        assert pending[0]["article_hash"] == "retry-me"
        assert pending[0]["summary_status"] == "pending"

    def test_retry_failed_articles_is_a_noop_with_nothing_failed(self, store):
        put_article(store, _make_article(article_hash="fine", link="https://example.com/fine"))
        assert retry_failed_articles(store) == 0
        assert len(pending_articles(store)) == 1

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

    def test_articles_for_company_no_date_range_returns_everything(self, store):
        put_article(store, _make_article(
            article_hash="old", competitor_tag="Bosch",
            link="https://example.com/old", published_at="2020-01-01",
        ))
        put_article(store, _make_article(
            article_hash="new", competitor_tag="Bosch",
            link="https://example.com/new", published_at="2026-08-10",
        ))

        result = articles_for_company(store, "Bosch")
        assert [a["article_hash"] for a in result] == ["new", "old"]

    def test_articles_for_companies_no_date_range(self, store):
        put_article(store, _make_article(
            article_hash="b1", competitor_tag="Bosch",
            link="https://example.com/b1", published_at="2026-08-05",
        ))
        put_article(store, _make_article(
            article_hash="v1", competitor_tag="Valeo",
            link="https://example.com/v1", published_at="2026-08-06",
        ))

        result = articles_for_companies(store, ["Bosch", "Valeo"])
        assert [a["article_hash"] for a in result] == ["v1", "b1"]


class TestContentKind:
    def test_articles_by_kind_separates_news_and_social(self, store):
        put_article(store, _make_article(
            article_hash="news-1", content_kind="news", published_at="2026-08-05",
            link="https://example.com/news-1",
        ))
        put_article(store, _make_article(
            article_hash="social-1", content_kind="social", published_at="2026-08-06",
            link="https://example.com/social-1",
        ))

        news = articles_by_kind(store, "news")
        social = articles_by_kind(store, "social")
        assert [a["article_hash"] for a in news] == ["news-1"]
        assert [a["article_hash"] for a in social] == ["social-1"]

    def test_articles_for_company_kind_filter(self, store):
        put_article(store, _make_article(
            article_hash="bosch-news", competitor_tag="Bosch", content_kind="news",
            link="https://example.com/bosch-news", published_at="2026-08-05",
        ))
        put_article(store, _make_article(
            article_hash="bosch-social", competitor_tag="Bosch", content_kind="social",
            link="https://example.com/bosch-social", published_at="2026-08-06",
        ))

        news_only = articles_for_company(store, "Bosch", kind="news")
        social_only = articles_for_company(store, "Bosch", kind="social")
        both = articles_for_company(store, "Bosch")

        assert [a["article_hash"] for a in news_only] == ["bosch-news"]
        assert [a["article_hash"] for a in social_only] == ["bosch-social"]
        assert {a["article_hash"] for a in both} == {"bosch-news", "bosch-social"}

    def test_articles_for_company_kind_filter_honors_limit_past_dynamodb_prefilter_quirk(self, store):
        # 5 social articles interleaved with 5 news articles for the same
        # company. DynamoDB's Limit applies before the FilterExpression, so
        # a naive single-page Query for kind="social" with Limit=3 could
        # come back with 0-3 matches depending on interleaving, not
        # necessarily 3 — this is exactly the bug _query_company's loop
        # exists to prevent.
        for i in range(5):
            put_article(store, _make_article(
                article_hash=f"news-{i}", competitor_tag="Bosch", content_kind="news",
                link=f"https://example.com/news-{i}", published_at=f"2026-08-{10+i}",
            ))
            put_article(store, _make_article(
                article_hash=f"social-{i}", competitor_tag="Bosch", content_kind="social",
                link=f"https://example.com/social-{i}", published_at=f"2026-08-{10+i}",
            ))

        result = articles_for_company(store, "Bosch", kind="social", limit=3)
        assert len(result) == 3
        assert all(a["article_hash"].startswith("social-") for a in result)

    def test_articles_for_companies_kind_filter(self, store):
        put_article(store, _make_article(
            article_hash="bosch-social", competitor_tag="Bosch", content_kind="social",
            link="https://example.com/bosch-social", published_at="2026-08-05",
        ))
        put_article(store, _make_article(
            article_hash="valeo-news", competitor_tag="Valeo", content_kind="news",
            link="https://example.com/valeo-news", published_at="2026-08-06",
        ))

        result = articles_for_companies(store, ["Bosch", "Valeo"], kind="social")
        assert [a["article_hash"] for a in result] == ["bosch-social"]

    def test_content_kind_defaults_to_news(self, store):
        put_article(store, _make_article(article_hash="default-kind", link="https://example.com/dk"))
        assert get_article(store, "default-kind")["content_kind"] == "news"
        assert [a["article_hash"] for a in articles_by_kind(store, "news")] == ["default-kind"]
