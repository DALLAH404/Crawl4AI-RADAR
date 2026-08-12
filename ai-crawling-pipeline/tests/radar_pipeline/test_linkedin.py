"""Tests for radar_pipeline.sources.linkedin.

Pure-function tests over small markdown/HTML fixtures — no network, no
Crawl4AI browser. Mirrors the style of test_feedparser.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from radar_pipeline.sources.linkedin import (
    _post_image_url,
    _post_title,
    _to_item,
    extract_post_media,
    extract_posts,
    is_blocked_result,
    parse_relative_age_days,
)


class TestParseRelativeAgeDays:
    @pytest.mark.parametrize(
        "rel_time,expected",
        [
            ("5m", 5 / 1440),
            ("21min", 21 / 1440),
            ("2h", 2 / 24),
            ("3d", 3.0),
            ("1w", 7.0),
            ("2mo", 60.0),
            ("1y", 365.0),
        ],
    )
    def test_known_units(self, rel_time, expected):
        assert parse_relative_age_days(rel_time) == pytest.approx(expected)

    def test_unknown_unit_returns_none(self):
        assert parse_relative_age_days("3xyz") is None

    def test_unparseable_returns_none(self):
        assert parse_relative_age_days("edited") is None


FETCHED_AT = datetime(2026, 8, 12, 12, 0, 0, tzinfo=timezone.utc)

MARKDOWN = """Some header content

##  Updates

* [ ](https://www.linkedin.com/posts/bosch_activity-12345)Bosch
3d

Report this post

Bosch is launching a new brake pad line for the aftermarket.

More details in the link below.
…more

* [1,234](https://www.linkedin.com/posts/bosch_activity-12345/social-actions-reactions)
* [56 Comments](https://www.linkedin.com/posts/bosch_activity-12345/social-actions-comments)

* [ ](https://www.linkedin.com/posts/bosch_activity-67890)Bosch
1w Edited

Report this post

Second post, no engagement yet.
…more

##  Something else
"""


class TestExtractPosts:
    def test_extracts_two_posts(self):
        posts = extract_posts(MARKDOWN, FETCHED_AT)
        assert len(posts) == 2

    def test_first_post_fields(self):
        posts = extract_posts(MARKDOWN, FETCHED_AT)
        p = posts[0]
        assert p["url"] == "https://www.linkedin.com/posts/bosch_activity-12345"
        assert "brake pad" in p["text"]
        assert p["relative_time"] == "3d"
        assert p["edited"] is False
        assert p["estimated_age_days"] == pytest.approx(3.0)
        assert p["estimated_date"] == "2026-08-09"
        assert p["reactions"] == 1234
        assert p["comments"] == 56

    def test_second_post_edited_flag_and_no_engagement(self):
        posts = extract_posts(MARKDOWN, FETCHED_AT)
        p = posts[1]
        assert p["url"] == "https://www.linkedin.com/posts/bosch_activity-67890"
        assert p["edited"] is True
        assert p["reactions"] == 0
        assert p["comments"] == 0

    def test_no_updates_section_returns_empty(self):
        assert extract_posts("no relevant section here", FETCHED_AT) == []


class TestExtractPostMedia:
    def test_json_ld_url_does_not_shift_position(self):
        url1 = "https://www.linkedin.com/posts/company_post-1"
        url2 = "https://www.linkedin.com/posts/company_post-2"
        page_html = (
            '<script type="application/ld+json">'
            f'{{"url": "{url1}"}}'
            "</script>"
            f'<div><a href="{url1}">post1</a></div>'
            '<img src="https://media.licdn.com/dms/image/abc/feedshare-shrink_800/xyz.jpg">'
            f'<div><a href="{url2}">post2</a></div>'
            '<video poster="https://media.licdn.com/dms/image/poster1.jpg" '
            'data-sources="[{&quot;src&quot;:&quot;https://example.com/v1.mp4&quot;,'
            '&quot;data-bitrate&quot;:500}]"></video>'
        )

        media = extract_post_media(page_html, [url1, url2])

        assert media[url1]["images"] == [
            "https://media.licdn.com/dms/image/abc/feedshare-shrink_800/xyz.jpg"
        ]
        assert media[url1]["videos"] == []
        assert media[url2]["images"] == []
        assert media[url2]["videos"] == [
            {"poster": "https://media.licdn.com/dms/image/poster1.jpg", "src": "https://example.com/v1.mp4"}
        ]

    def test_no_matching_href_leaves_empty_media(self):
        media = extract_post_media("<p>nothing here</p>", ["https://www.linkedin.com/posts/x_1"])
        assert media == {"https://www.linkedin.com/posts/x_1": {"images": [], "videos": []}}


class FakeResult:
    def __init__(self, success=True, status_code=200, url="https://www.linkedin.com/company/bosch/", markdown=""):
        self.success = success
        self.status_code = status_code
        self.url = url
        self.markdown = markdown


class TestIsBlockedResult:
    def test_ok_result_not_blocked(self):
        assert is_blocked_result(FakeResult()) is False

    def test_failed_result_blocked(self):
        assert is_blocked_result(FakeResult(success=False)) is True

    @pytest.mark.parametrize("status_code", [999, 429])
    def test_block_status_codes(self, status_code):
        assert is_blocked_result(FakeResult(status_code=status_code)) is True

    def test_authwall_url_blocked(self):
        result = FakeResult(url="https://www.linkedin.com/authwall?trk=x")
        assert is_blocked_result(result) is True

    def test_authwall_in_markdown_blocked(self):
        result = FakeResult(markdown="please sign in at linkedin.com/authwall")
        assert is_blocked_result(result) is True


class TestItemMapping:
    def test_title_from_first_line(self):
        assert _post_title("Bosch launches a new line.\nMore text here.") == "Bosch launches a new line."

    def test_title_truncates_long_first_line(self):
        long_line = "word " * 40
        title = _post_title(long_line.strip())
        assert len(title) <= 121
        assert title.endswith("…")

    def test_title_empty_for_media_only_post(self):
        assert _post_title("") == ""

    def test_image_url_prefers_first_image(self):
        post = {"images": ["https://img.example/1.jpg"], "videos": [{"poster": "https://img.example/poster.jpg"}]}
        assert _post_image_url(post) == "https://img.example/1.jpg"

    def test_image_url_falls_back_to_video_poster(self):
        post = {"images": [], "videos": [{"poster": "https://img.example/poster.jpg"}]}
        assert _post_image_url(post) == "https://img.example/poster.jpg"

    def test_image_url_empty_when_no_media(self):
        assert _post_image_url({"images": [], "videos": []}) == ""

    def test_to_item_shape(self):
        post = {
            "url": "https://www.linkedin.com/posts/bosch_activity-1",
            "text": "Bosch launches a new brake pad line.",
            "relative_time": "3d",
            "edited": False,
            "estimated_age_days": 3.0,
            "estimated_date": "2026-08-09",
            "reactions": 10,
            "comments": 2,
            "images": [],
            "videos": [],
        }
        item = _to_item("bosch", post)
        assert item["title"] == "Bosch launches a new brake pad line."
        assert item["link"] == post["url"]
        assert item["published_at"] == "2026-08-09"
        assert item["action_description"] == post["text"]
        assert item["summary_text"] == post["text"][:300]
        assert item["image_url"] == ""

        import json
        extra = json.loads(item["extra"])
        assert extra["linkedin_slug"] == "bosch"
        assert extra["reactions"] == 10
        assert extra["comments"] == 2
