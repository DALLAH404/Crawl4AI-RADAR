"""_feed_url no longer appends a `when:` recency operator to Google News
query URLs — confirmed live that several of our real configured queries
(e.g. Bosch's) return zero results with `when:` at any window size, and
plenty without it. LinkedIn and direct-RSS sources are unaffected either
way, since neither ever went through the `when:` branch.
"""

from __future__ import annotations

from radar_pipeline.sources.collector import _feed_url


def test_feed_url_google_news_query_has_no_when_operator():
    source = {
        "feed_type": "google_news_query",
        "query_text": "Bosch autopeças OR aftermarket Brasil",
        "tag": "Bosch",
        "name": "Bosch",
    }
    url = _feed_url(source)
    assert "when%3A" not in url
    assert "when:" not in url
    assert "Bosch" in url


def test_feed_url_falls_back_to_tag_when_no_query_text():
    source = {"feed_type": "google_news_query", "query_text": "", "tag": "Valeo", "name": "Valeo"}
    url = _feed_url(source)
    assert "Valeo" in url
    assert "when:" not in url


def test_feed_url_linkedin_returns_company_page_url():
    source = {"feed_type": "linkedin_company", "query_text": "bosch"}
    url = _feed_url(source)
    assert "linkedin.com" in url
    assert "bosch" in url


def test_feed_url_rss_direct_returns_the_configured_url_unchanged():
    source = {
        "feed_type": "rss_direct",
        "rss_url": "https://example.com/feed.xml",
        "query_text": "",
    }
    assert _feed_url(source) == "https://example.com/feed.xml"
