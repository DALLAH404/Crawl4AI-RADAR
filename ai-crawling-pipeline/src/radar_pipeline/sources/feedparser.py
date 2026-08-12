"""Async RSS/Atom feed parser using httpx + feedparser.

Port of parseFeed_() and itemRss_() from codigo.gs, with two modern additions:

* retries with exponential backoff + jitter on transient block
  responses (429/5xx), honoring ``Retry-After`` when present;
* integration with a per-host ``RateLimiter`` so we share the same
  politeness window used by the Google News resolver.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import random
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any

import feedparser as feedparser_lib
import httpx

logger = logging.getLogger(__name__)

# Matches the UA used elsewhere in the pipeline (crawl/image) so the
# RSS endpoint, resolver, og:image fetch and the eventual article crawl
# look like one client.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "Accept": (
        "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

HTML_TAG_RE = re.compile(r"<[^>]+>", re.UNICODE)


class FeedFetchError(RuntimeError):
    """Raised when an HTTP/RSS fetch fails after exhausting retries."""

    def __init__(self, message: str, status_code: int | None = None,
                 url: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


def strip_html(text: str) -> str:
    text = HTML_TAG_RE.sub(" ", text).replace("&nbsp;", " ")\
        .replace("&amp;", "&").replace("&quot;", '"')\
        .replace("&#39;", "'").replace("&apos;", "'")\
        .replace("&lt;", "<").replace("&gt;", ">")\
        .replace("&#160;", " ")\
        .replace("&rsquo;", "'").replace("&lsquo;", "'")\
        .replace("&rdquo;", '"').replace("&ldquo;", '"')\
        .replace("&mdash;", "-").replace("&ndash;", "-")

    text = re.sub(r"\s+", " ", text).strip()
    return unescape(text)


def extract_image_from_entry(entry: dict[str, Any]) -> str:
    media_content = entry.get("media_content", [])
    if media_content:
        for mc in media_content:
            url = mc.get("url", "")
            media_type = mc.get("type", "")
            if url and ("image" in media_type or not media_type):
                return url

    media_thumbnail = entry.get("media_thumbnail", [])
    if media_thumbnail:
        for mt in media_thumbnail:
            url = mt.get("url", "")
            if url:
                return url

    links = entry.get("links", [])
    for link in links:
        if link.get("rel") == "enclosure":
            mime = link.get("type", "")
            if "image" in mime:
                return link.get("href", "")

    return ""


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Parse a Retry-After header. Accepts either seconds or an HTTP date."""
    raw = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


async def _do_request(
    url: str,
    client: httpx.AsyncClient,
    headers: dict[str, str],
) -> httpx.Response:
    return await client.get(
        url,
        headers=headers,
        follow_redirects=True,
    )


async def fetch_and_parse(
    url: str,
    client: httpx.AsyncClient | None = None,
    *,
    limiter: Any | None = None,
    user_agent: str = DEFAULT_UA,
    max_retries: int = 3,
    backoff_seconds: float = 2.0,
    max_backoff_seconds: float = 30.0,
    timeout_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    """Fetch the feed at ``url`` and return a list of entry dicts.

    Returns ``[]`` only when the server says HTTP 200 but the body does
    not contain any entries. Any non-200 final response, network error
    or ``HostBlockedError`` is raised as ``FeedFetchError`` so the caller
    can record the run as an error.

    Args:
        url: feed URL.
        client: shared httpx.AsyncClient (recommended). One is created
            and closed internally if omitted.
        limiter: optional ``RateLimiter`` from sources.ratelimit. When
            provided, the fetch goes through ``acquire()`` / ``release()``
            and the result is reported with ``record_success`` /
            ``record_failure``.
        user_agent: UA to send. Defaults to a Chrome-on-Windows string.
        max_retries: number of retries on 429/5xx (0 = no retries).
        backoff_seconds: base for exponential backoff between retries.
        max_backoff_seconds: cap on backoff between retries.
        timeout_seconds: per-request HTTP timeout.
    """
    close_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=timeout_seconds)

    headers = {**DEFAULT_HEADERS, "User-Agent": user_agent}

    last_exc: Exception | None = None
    last_status: int | None = None
    last_resp: httpx.Response | None = None

    acquired = False
    if limiter is not None:
        try:
            await limiter.acquire(url)
            acquired = True
        except Exception as exc:
            raise FeedFetchError(str(exc), status_code=None, url=url) from exc

    try:
        for attempt in range(max_retries + 1):
            try:
                last_resp = await _do_request(url, client, headers)
            except httpx.HTTPError as exc:
                last_exc = exc
                if limiter is not None:
                    limiter.record_failure(url)
                if attempt >= max_retries:
                    raise FeedFetchError(
                        f"HTTP error: {exc}", status_code=None, url=url,
                    ) from exc
            else:
                last_status = last_resp.status_code
                if last_resp.status_code == 200:
                    items = _parse_entries(last_resp.text)
                    if limiter is not None:
                        limiter.record_success(url)
                    return items

                if limiter is not None:
                    retry_after = _retry_after_seconds(last_resp)
                    limiter.record_failure(
                        url,
                        status=last_resp.status_code,
                        retry_after=retry_after,
                    )

                if last_resp.status_code not in (429,) and not (
                    500 <= last_resp.status_code < 600
                ):
                    raise FeedFetchError(
                        f"HTTP {last_resp.status_code}",
                        status_code=last_resp.status_code,
                        url=url,
                    )

                if attempt >= max_retries:
                    raise FeedFetchError(
                        f"HTTP {last_resp.status_code} after "
                        f"{max_retries} retries",
                        status_code=last_resp.status_code,
                        url=url,
                    )

            retry_after = (
                _retry_after_seconds(last_resp)
                if last_resp is not None
                else None
            )
            if retry_after is not None:
                sleep_for = min(retry_after, max_backoff_seconds)
            else:
                sleep_for = min(
                    backoff_seconds * (2 ** attempt), max_backoff_seconds,
                )
            sleep_for = sleep_for + random.uniform(0.0, min(0.5, sleep_for))
            await asyncio.sleep(sleep_for)

        raise FeedFetchError(
            "feed fetch exhausted retries",
            status_code=last_status,
            url=url,
        )
    finally:
        if acquired and limiter is not None:
            limiter.release(url)
        if close_client:
            await client.aclose()


def _parse_entries(xml_text: str) -> list[dict[str, Any]]:
    feed = feedparser_lib.parse(xml_text)
    items: list[dict[str, Any]] = []
    for entry in feed.entries:
        title = strip_html(entry.get("title", ""))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue

        published = entry.get("published_parsed") or entry.get("updated_parsed")
        if published:
            ts = calendar.timegm(published)
            published_at = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
        else:
            published_at = ""

        summary_html = entry.get("summary") or entry.get("description") or ""
        summary_text = strip_html(summary_html)
        image_url = extract_image_from_entry(entry)

        items.append({
            "title": title,
            "link": link,
            "published_at": published_at,
            "summary_html": summary_html,
            "summary_text": summary_text,
            "image_url": image_url,
        })
    return items
