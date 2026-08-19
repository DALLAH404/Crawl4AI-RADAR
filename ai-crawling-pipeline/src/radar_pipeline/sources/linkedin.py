"""LinkedIn company-page post scraping — logged-out "Updates" section.

Ported from ``scripts/linkedin_radar.py`` (see that file's module docstring
for the full rationale this module inherits: why the public About page is
used instead of the authwalled ``/posts/`` feed, why LinkedIn's relative
timestamps make the date filter approximate, why a fresh browser is used
per company, and why media needs a second, separately-scrolled page fetch).

What's *not* ported from the script: the LLM topic filter, the
``state/<slug>.json`` dedup ledger, and the ``output/<slug>.json`` writer —
those responsibilities now belong to the DB (``articles.article_hash``
dedup) and the summarize stage (LLM relevance verdict), matching every
other source type in the pipeline.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

from radar_pipeline.config import LinkedInSettings
from radar_pipeline.sources.ratelimit import HostBlockedError, RateLimiter

logger = logging.getLogger(__name__)

POST_URL_RE = re.compile(r'\n\s*\*\s*\[ \]\(https://www\.linkedin\.com/posts/')
TIME_RE = re.compile(r'^\s*(\d+)\s*([a-zA-Z]+)\s*(Edited)?\s*$', re.MULTILINE)
REACT_RE = re.compile(r'\[\s*([\d,]+)\s*\]\([^)]*social-actions-reactions\)')
COMMENT_RE = re.compile(r'\[\s*([\d,]+)\s*Comments?\s*\]\([^)]*social-actions-comments\)')
# Post photos use two different licdn.com URL patterns depending on the post
# type — single-image posts: feedshare-shrink_*/feedshare-image-high-res;
# multi-image carousel posts: image-shrink_* (confirmed: a 5-photo carousel
# post used image-shrink_800 for every photo, feedshare nowhere) — both are
# distinct from avatar/logo/cover images (company-logo_*,
# profile-displayphoto-*, image-scale_* for the cover photo).
POST_IMAGE_RE = re.compile(r'<img[^>]+src="(https://media\.licdn\.com/dms/image/[^"]*(?:feedshare|image-shrink)[^"]*)"')
# Video posts render as <video poster="..." data-sources="[...mp4 urls...]">
# rather than <img>, so they need a separate pattern entirely.
POST_VIDEO_RE = re.compile(r'<video\b[^>]*>')
# The stable numeric ID embedded in every post URL — used to locate a post's
# href in the media fetch's HTML even when the surrounding URL differs (see
# _href_pattern below).
ACTIVITY_ID_RE = re.compile(r'-activity-(\d+)')

# Approximate day-equivalents for LinkedIn's relative-time units. Minutes
# ("21m") were missing entirely in an earlier version, which silently
# dropped every post under an hour old — parse_relative_age_days returned
# None for them, and the date filter excludes anything with an
# unparseable/None age.
UNIT_DAYS = {
    "s": 1 / 86400,
    "sec": 1 / 86400,
    "m": 1 / 1440,
    "min": 1 / 1440,
    "mins": 1 / 1440,
    "h": 1 / 24,
    "hr": 1 / 24,
    "d": 1,
    "w": 7,
    "mo": 30,
    "y": 365,
}

# Bounds only pathological single-line posts (no line breaks at all) —
# not meant to shorten an ordinary headline-length first line. Raised from
# 120 after that cap turned out to cut off completely ordinary post
# openers mid-sentence with no way for a reader to see the rest, since the
# frontend only ever receives this already-truncated title, never the
# full post text.
_TITLE_MAX_CHARS = 280


class LinkedInBlockedError(RuntimeError):
    """Raised when a company page stays blocked after all retry attempts."""

    def __init__(self, slug: str, reason: str):
        super().__init__(f"LinkedIn blocked company page {slug!r}: {reason}")
        self.slug = slug
        self.reason = reason


def parse_relative_age_days(rel_time: str) -> float | None:
    m = re.match(r"(\d+)\s*([a-zA-Z]+)", rel_time)
    if not m:
        return None
    count, unit = int(m.group(1)), m.group(2).lower()
    per_unit = UNIT_DAYS.get(unit)
    if per_unit is None:
        return None
    return count * per_unit


def extract_updates_section(markdown: str) -> str:
    idx = markdown.lower().find("\n##  updates")
    if idx == -1:
        idx = markdown.lower().find("updates")
    if idx == -1:
        return ""
    next_heading = re.search(r"\n##[^#]", markdown[idx + 10 :])
    end = idx + 10 + next_heading.start() if next_heading else len(markdown)
    return markdown[idx:end]


def extract_posts(markdown: str, fetched_at: datetime) -> list[dict]:
    section = extract_updates_section(markdown)
    if not section:
        return []

    chunks = POST_URL_RE.split(section)[1:]
    posts = []
    for chunk in chunks:
        slug, _, rest = chunk.partition(")")
        url = "https://www.linkedin.com/posts/" + slug

        time_match = TIME_RE.search(chunk)
        rel_time = f"{time_match.group(1)}{time_match.group(2)}" if time_match else None
        edited = bool(time_match.group(3)) if time_match else False

        text_start = chunk.find("Report this post")
        text_start = chunk.find("\n", text_start) + 1 if text_start != -1 else 0
        end_candidates = [x for x in (chunk.find("…more", text_start), chunk.find("`` ``", text_start)) if x != -1]
        text_end = min(end_candidates) if end_candidates else len(chunk)
        text = re.sub(r"\n\s*\*\s*$", "", chunk[text_start:text_end]).strip()

        reactions_match = REACT_RE.search(chunk)
        comments_match = COMMENT_RE.search(chunk)

        age_days = parse_relative_age_days(rel_time) if rel_time else None
        estimated_date = (fetched_at - timedelta(days=age_days)) if age_days is not None else None

        posts.append(
            {
                "url": url,
                "text": text,
                "relative_time": rel_time,
                "edited": edited,
                "estimated_age_days": age_days,
                "estimated_date": estimated_date.date().isoformat() if estimated_date else None,
                "reactions": int((reactions_match.group(1) if reactions_match else "0").replace(",", "")),
                "comments": int((comments_match.group(1) if comments_match else "0").replace(",", "")),
            }
        )
    return posts


def extract_video_sources(video_tag: str) -> dict | None:
    poster_match = re.search(r'poster="([^"]*)"', video_tag)
    sources_match = re.search(r'data-sources="([^"]*)"', video_tag)
    if not sources_match:
        return None
    try:
        sources = json.loads(html.unescape(sources_match.group(1)))
    except json.JSONDecodeError:
        return None
    if not sources:
        return None
    best = max(sources, key=lambda s: s.get("data-bitrate", 0))
    return {
        "poster": html.unescape(poster_match.group(1)) if poster_match else None,
        "src": best.get("src"),
    }


def _href_pattern(url: str) -> re.Pattern[str]:
    # The text fetch and the media fetch (_fetch_company_page vs
    # _fetch_page_images) are two independent browser sessions loading the
    # same feed at different moments — a post's href can pick up different
    # tracking query params between the two, so an exact-URL match can miss
    # a post entirely even though it's right there in the HTML (silently
    # zeroing out its images/videos, see extract_post_media's caller).
    # Anchor on the stable numeric activity ID instead, still scoped to an
    # href attribute (not just anywhere in the page — see extract_post_media
    # below for why that scoping matters). Falls back to the old exact-URL
    # match for the rare post URL with no activity ID in it.
    m = ACTIVITY_ID_RE.search(url)
    if not m:
        return re.compile(re.escape(f'href="{url}"'))
    return re.compile(r'href="[^"]*-activity-' + re.escape(m.group(1)) + r'[^"]*"')


def extract_post_media(page_html: str, post_urls: list[str]) -> dict[str, dict]:
    # Match each post to the photo(s)/video(s) between its link and the next
    # post's link in the raw HTML (DOM order == feed order). Must search for
    # href="<url>" specifically, not just the URL string — the page also
    # embeds every post URL in a JSON-LD <script> block as a plain string,
    # which would give a false/earlier position if matched loosely.
    positions = []
    for url in post_urls:
        m = _href_pattern(url).search(page_html)
        if m:
            positions.append((m.start(), url))
        else:
            logger.warning("LinkedIn: post href not found in media fetch, no images/videos: %s", url)
    positions.sort()

    img_positions = [(m.start(), html.unescape(m.group(1))) for m in POST_IMAGE_RE.finditer(page_html)]
    video_positions = []
    for m in POST_VIDEO_RE.finditer(page_html):
        video = extract_video_sources(m.group(0))
        if video:
            video_positions.append((m.start(), video))

    media_by_url: dict[str, dict] = {url: {"images": [], "videos": []} for url in post_urls}
    for i, (pos, url) in enumerate(positions):
        next_pos = positions[i + 1][0] if i + 1 < len(positions) else len(page_html)
        media_by_url[url] = {
            "images": [u for p, u in img_positions if pos <= p < next_pos],
            "videos": [v for p, v in video_positions if pos <= p < next_pos],
        }
    return media_by_url


def is_blocked_result(result) -> bool:
    if not result.success:
        return True
    markdown = str(result.markdown)
    return (
        result.status_code == 999
        or result.status_code == 429
        or "authwall" in result.url
        or "/login" in result.url
        or "linkedin.com/authwall" in markdown
    )


def company_page_url(company_slug: str) -> str:
    # Some brands (e.g. MAHLE Aftermarket) are LinkedIn "Showcase" pages, not
    # top-level Company pages, and live at /showcase/<slug>/ instead of
    # /company/<slug>/. Prefix a slug with "showcase/" to target one.
    path = company_slug if company_slug.startswith("showcase/") else f"company/{company_slug}"
    return f"https://www.linkedin.com/{path}/"


async def _fetch_company_page(company_slug: str, settings: LinkedInSettings):
    # A fresh browser (not a shared/reused one) per company: reusing one
    # browser session across sequential company fetches was observed to trip
    # LinkedIn's bot detection (HTTP 999) on the second request even with a
    # multi-second delay between calls, whereas a clean browser per company
    # did not.
    browser_config = BrowserConfig(headless=settings.headless)
    run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=settings.page_timeout_ms)
    async with AsyncWebCrawler(config=browser_config) as crawler:
        return await crawler.arun(url=company_page_url(company_slug), config=run_config)


async def _fetch_page_images(company_slug: str, settings: LinkedInSettings) -> str | None:
    # Post photos are lazy-loaded — absent from the HTML entirely without
    # scrolling — but a full-page scroll was found to also make the
    # reaction-count widget disappear from the page. So this is a
    # deliberately separate, best-effort fetch: the primary fetch stays
    # untouched and always has correct reactions/comments, and this one is
    # only ever used for its raw HTML <img>/<video> tags, matched back to
    # posts by URL via extract_post_media.
    browser_config = BrowserConfig(headless=settings.headless)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=settings.page_timeout_ms,
        scan_full_page=True,
        wait_for_images=True,
    )
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=company_page_url(company_slug), config=run_config)
    if is_blocked_result(result):
        return None
    return result.html


async def _fetch_company_page_with_retry(
    company_slug: str, settings: LinkedInSettings, limiter: RateLimiter
):
    url = company_page_url(company_slug)
    result = None
    for attempt in range(settings.block_retries + 1):
        try:
            await limiter.acquire(url)
        except HostBlockedError as exc:
            raise LinkedInBlockedError(company_slug, f"host blocked: {exc}") from exc
        try:
            result = await _fetch_company_page(company_slug, settings)
        finally:
            limiter.release(url)

        if not is_blocked_result(result):
            limiter.record_success(url)
            return result, False

        limiter.record_failure(url, status=getattr(result, "status_code", None))
        if attempt < settings.block_retries:
            logger.warning(
                "LinkedIn: %s blocked (attempt %d/%d) — waiting %.0fs before retrying",
                company_slug, attempt + 1, settings.block_retries + 1, settings.block_retry_delay,
            )
            await asyncio.sleep(settings.block_retry_delay)
    return result, True


async def _fetch_page_images_with_limiter(
    company_slug: str, settings: LinkedInSettings, limiter: RateLimiter
) -> str | None:
    # Best-effort: a failed/blocked media fetch just means this run's posts
    # have no images/videos — never a reason to fail the company.
    url = company_page_url(company_slug)
    try:
        await limiter.acquire(url)
    except HostBlockedError:
        return None
    try:
        images_html = await _fetch_page_images(company_slug, settings)
    except Exception:
        logger.warning("LinkedIn: media fetch failed for %s", company_slug, exc_info=True)
        images_html = None
    finally:
        limiter.release(url)
    if images_html is None:
        limiter.record_failure(url)
        return None
    limiter.record_success(url)
    return images_html


def _post_title(text: str) -> str:
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not first_line:
        return ""
    if len(first_line) <= _TITLE_MAX_CHARS:
        return first_line
    cut = first_line[:_TITLE_MAX_CHARS].rsplit(" ", 1)[0] or first_line[:_TITLE_MAX_CHARS]
    return cut + "…"


def _post_image_url(post: dict) -> str:
    if post.get("images"):
        return post["images"][0]
    if post.get("videos"):
        return post["videos"][0].get("poster") or ""
    return ""


def _to_item(slug: str, post: dict) -> dict[str, Any]:
    text = post["text"]
    extra = {
        "linkedin_slug": slug,
        "relative_time": post["relative_time"],
        "edited": post["edited"],
        "estimated_age_days": post["estimated_age_days"],
        "reactions": post["reactions"],
        "comments": post["comments"],
        "images": post.get("images", []),
        "videos": post.get("videos", []),
    }
    return {
        # Empty when the post has no text (media-only post) — the caller
        # (collector) fills in a source-name-based fallback, since only it
        # knows the source's display name.
        "title": _post_title(text),
        "link": post["url"],
        "published_at": post["estimated_date"] or "",
        "summary_text": text[:300],
        "image_url": _post_image_url(post),
        # Full ordered image list (card/feed still only ever show image_url,
        # the first one) — the article detail page uses this to show every
        # photo in a multi-image carousel post.
        "image_urls": post.get("images", []),
        "action_description": text,
        "extra": json.dumps(extra, ensure_ascii=False),
    }


async def fetch_company_posts(
    slug: str,
    *,
    limiter: RateLimiter,
    settings: LinkedInSettings,
) -> list[dict[str, Any]]:
    """Fetch and parse recent posts for one LinkedIn company page.

    Returns items in the same dict shape as
    ``radar_pipeline.sources.feedparser._parse_entries`` (title, link,
    published_at, summary_text, image_url), plus ``action_description`` and
    ``extra`` which the collector passes straight through to ``Article``.

    Raises ``LinkedInBlockedError`` if the company page is still blocked
    after ``settings.block_retries`` retries — the collector maps this to a
    ``FeedFetchError`` the same way it maps ``HostBlockedError`` for RSS/
    Google-News sources, so nothing is inserted and the company is retried
    in full next run.
    """
    result, blocked = await _fetch_company_page_with_retry(slug, settings, limiter)
    fetched_at = datetime.now(timezone.utc)

    if blocked:
        reason = (
            result.error_message if result is not None and not result.success
            else f"status {getattr(result, 'status_code', '?')}"
        )
        raise LinkedInBlockedError(slug, reason)

    markdown = str(result.markdown)
    posts = extract_posts(markdown, fetched_at)
    posts = [
        p for p in posts
        if p["estimated_age_days"] is not None and p["estimated_age_days"] <= settings.days
    ]
    if not posts:
        return []

    posts = posts[: settings.max_posts_per_company]

    if settings.include_media:
        images_html = await _fetch_page_images_with_limiter(slug, settings, limiter)
        media_by_url = (
            extract_post_media(images_html, [p["url"] for p in posts])
            if images_html is not None
            else {}
        )
        for p in posts:
            media = media_by_url.get(p["url"], {"images": [], "videos": []})
            p["images"] = media["images"]
            p["videos"] = media["videos"]
    else:
        for p in posts:
            p["images"] = []
            p["videos"] = []

    return [_to_item(slug, p) for p in posts]
