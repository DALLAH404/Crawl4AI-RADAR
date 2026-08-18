"""Google News RSS collection and link resolution.

Google News search has two separate operations:

1. Fetch and parse the RSS search feed.
2. Decode ``news.google.com`` article links into publisher URLs.

The decoder is blocking and maintained outside this project, so resolution
uses ``googlenewsdecoder`` in a worker thread.  ``GNewsCollector`` keeps the
two phases separate, caches successful resolutions, bounds concurrency, and
retries decoder failures with backoff.  The pipeline injects its shared
``httpx`` client and ``RateLimiter`` rather than creating another transport.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from time import mktime
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote

import feedparser
import httpx
from googlenewsdecoder import gnewsdecoder

from radar_pipeline.sources.feedparser import fetch_and_parse
from radar_pipeline.sources.ratelimit import HostBlockedError, RateLimiter

logger = logging.getLogger(__name__)


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class GNewsConfig:
    """Settings for Google News search and URL resolution."""

    hl: str = "pt-BR"
    gl: str = "BR"
    ceid: str = "BR:pt-419"
    max_items_per_query: int = 25
    request_timeout: float = 30.0
    resolve_concurrency: int = 5
    resolve_interval: float = 1.0
    resolve_max_retries: int = 3
    resolve_backoff_base: float = 2.0
    search_concurrency: int = 5
    search_delay_between: float = 0.0
    user_agent: str = DEFAULT_UA
    search_max_retries: int = 3
    search_backoff_seconds: float = 2.0


@dataclass
class GNewsItem:
    """A parsed Google News item with explicit resolution state."""

    title: str
    google_link: str
    published: Optional[datetime]
    source: str
    summary: str = ""
    image_url: str = ""
    summary_html: str = ""
    resolved_link: Optional[str] = None
    resolve_status: str = "pending"
    resolve_error: Optional[str] = None

    @property
    def link(self) -> str:
        return self.resolved_link or self.google_link

    @classmethod
    def from_feed_item(cls, item: dict[str, Any]) -> "GNewsItem":
        published = None
        raw_published = item.get("published_at")
        if raw_published:
            try:
                published = datetime.fromisoformat(raw_published.replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass

        return cls(
            title=item.get("title", ""),
            google_link=item.get("link", ""),
            published=published,
            source=item.get("source", ""),
            summary=item.get("summary_text", ""),
            image_url=item.get("image_url", ""),
            summary_html=item.get("summary_html", ""),
        )

    def to_feed_item(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "link": self.link,
            "published_at": self.published.isoformat() if self.published else "",
            "summary_html": self.summary_html,
            "summary_text": self.summary,
            "image_url": self.image_url,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "link": self.link,
            "google_link": self.google_link,
            "resolved_link": self.resolved_link,
            "resolve_status": self.resolve_status,
            "resolve_error": self.resolve_error,
            "published": self.published.isoformat() if self.published else None,
            "source": self.source,
            "summary": self.summary,
            "image_url": self.image_url,
        }


_TAG_RE = re.compile(r"<[^>]+>")


def _clean_html(value: str) -> str:
    if not value:
        return ""
    return unescape(_TAG_RE.sub(" ", value)).strip()


def _parse_entry_date(entry: Any) -> Optional[datetime]:
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if not struct:
        return None
    return datetime.fromtimestamp(mktime(struct), tz=timezone.utc)


def _extract_source(entry: Any) -> str:
    source = entry.get("source")
    if source and getattr(source, "title", None):
        return source.title
    if isinstance(source, dict) and source.get("title"):
        return source["title"]

    title = entry.get("title", "")
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    return ""


def _parse_feed_text(raw_xml: str) -> list[Any]:
    return feedparser.parse(raw_xml).entries


def _is_google_news_link(url: str) -> bool:
    return "news.google" in (url or "")


ResolveFunction = Callable[
    [str, Optional[httpx.AsyncClient], Optional[RateLimiter]], Awaitable[str]
]


class GNewsCollector:
    """Two-phase Google News collector adapted to the radar pipeline."""

    @staticmethod
    def _is_google_news_link(url: str) -> bool:
        return _is_google_news_link(url)

    def __init__(
        self,
        config: Optional[GNewsConfig] = None,
        *,
        client: Optional[httpx.AsyncClient] = None,
        limiter: Optional[RateLimiter] = None,
        resolve_function: Optional[ResolveFunction] = None,
    ):
        self.cfg = config or GNewsConfig()
        self.client = client
        self.limiter = limiter
        self._resolve_function = resolve_function
        self._resolve_cache: dict[str, str] = {}
        self._resolve_sema = asyncio.Semaphore(max(1, self.cfg.resolve_concurrency))
        self._owned_client = False

    async def __aenter__(self) -> "GNewsCollector":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def start(self) -> None:
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=self.cfg.request_timeout,
                follow_redirects=True,
                headers={"User-Agent": self.cfg.user_agent},
            )
            self._owned_client = True

    async def close(self) -> None:
        if self._owned_client and self.client is not None:
            await self.client.aclose()
            self.client = None
            self._owned_client = False

    def _require_client(self) -> httpx.AsyncClient:
        if self.client is None:
            raise RuntimeError(
                "No httpx client available. Use 'async with GNewsCollector()' "
                "or pass client= explicitly."
            )
        return self.client

    def build_search_url(self, query: str, when_days: Optional[int] = None) -> str:
        q = query if not (when_days and when_days > 0) else f"{query} when:{when_days}d"
        return (
            "https://news.google.com/rss/search?q="
            f"{quote(q)}&hl={self.cfg.hl}&gl={self.cfg.gl}&ceid={self.cfg.ceid}"
        )

    async def search(
        self,
        query: str,
        *,
        when_days: Optional[int] = None,
        max_items: Optional[int] = None,
        min_date: Optional[datetime] = None,
    ) -> list[GNewsItem]:
        """Fetch and parse RSS without resolving article links."""
        client = self._require_client()
        url = self.build_search_url(query, when_days)
        feed_items = await fetch_and_parse(
            url,
            client,
            limiter=self.limiter,
            user_agent=self.cfg.user_agent,
            max_retries=self.cfg.search_max_retries,
            backoff_seconds=self.cfg.search_backoff_seconds,
            timeout_seconds=self.cfg.request_timeout,
        )

        limit = max_items or self.cfg.max_items_per_query
        result = [GNewsItem.from_feed_item(item) for item in feed_items[:limit]]
        if min_date is not None:
            result = [
                item for item in result
                if item.published is None or item.published >= min_date
            ]
        return result

    async def search_many(
        self,
        queries: list[str],
        *,
        when_days: Optional[int] = None,
        max_items: Optional[int] = None,
        min_date: Optional[datetime] = None,
    ) -> dict[str, list[GNewsItem] | Exception]:
        """Search multiple queries concurrently, isolating per-query errors."""
        sema = asyncio.Semaphore(max(1, self.cfg.search_concurrency))
        results: dict[str, list[GNewsItem] | Exception] = {}

        async def _one(query: str) -> None:
            async with sema:
                try:
                    results[query] = await self.search(
                        query,
                        when_days=when_days,
                        max_items=max_items,
                        min_date=min_date,
                    )
                except Exception as exc:  # noqa: BLE001 - isolate sources
                    logger.warning("GNews search failed for %r: %s", query, exc)
                    results[query] = exc
                if self.cfg.search_delay_between:
                    await asyncio.sleep(self.cfg.search_delay_between)

        await asyncio.gather(*(_one(query) for query in queries))
        return results

    async def _resolve_once(self, google_link: str) -> str:
        acquired = False
        if self.limiter is not None:
            await self.limiter.acquire(google_link)
            acquired = True

        try:
            try:
                result = await asyncio.to_thread(
                    gnewsdecoder,
                    google_link,
                    interval=self.cfg.resolve_interval,
                )
            except Exception:
                if self.limiter is not None:
                    self.limiter.record_failure(google_link)
                raise

            if result.get("status") and result.get("decoded_url"):
                if self.limiter is not None:
                    self.limiter.record_success(google_link)
                return result["decoded_url"]

            message = result.get("message", "unknown decode failure")
            if self.limiter is not None:
                self.limiter.record_failure(google_link)
            raise RuntimeError(message)
        finally:
            if acquired and self.limiter is not None:
                self.limiter.release(google_link)

    async def resolve_link(self, google_link: str) -> str:
        """Resolve one link with cache, bounded concurrency, and retries."""
        if not _is_google_news_link(google_link):
            return google_link

        cached = self._resolve_cache.get(google_link)
        if cached is not None:
            return cached

        async with self._resolve_sema:
            cached = self._resolve_cache.get(google_link)
            if cached is not None:
                return cached

            last_error: Optional[str] = None
            for attempt in range(1, self.cfg.resolve_max_retries + 1):
                try:
                    if self._resolve_function is None:
                        real_url = await self._resolve_once(google_link)
                    else:
                        real_url = await self._resolve_function(
                            google_link, self.client, limiter=self.limiter,
                        )
                    if real_url and not _is_google_news_link(real_url):
                        self._resolve_cache[google_link] = real_url
                        return real_url
                    last_error = "decoder returned another Google News link"
                except HostBlockedError as exc:
                    last_error = str(exc)
                    break
                except Exception as exc:  # noqa: BLE001 - retry decoder failures
                    last_error = str(exc)

                if attempt < self.cfg.resolve_max_retries:
                    if self.cfg.resolve_backoff_base > 0:
                        sleep_for = (
                            self.cfg.resolve_backoff_base ** attempt
                            + random.uniform(0.0, 1.0)
                        )
                    else:
                        sleep_for = 0.0
                    logger.debug(
                        "GNews resolve attempt %d/%d failed for %s (%s); "
                        "retrying in %.1fs",
                        attempt,
                        self.cfg.resolve_max_retries,
                        google_link,
                        last_error,
                        sleep_for,
                    )
                    await asyncio.sleep(sleep_for)

            logger.warning("Giving up resolving %s: %s", google_link, last_error)
            raise RuntimeError(last_error or "failed to resolve Google News link")

    async def resolve_all(self, items: list[GNewsItem]) -> list[GNewsItem]:
        """Resolve items in place; failures remain marked for later retry."""
        async def _one(item: GNewsItem) -> None:
            if not _is_google_news_link(item.google_link):
                item.resolved_link = item.google_link
                item.resolve_status = "skipped"
                return
            try:
                item.resolved_link = await self.resolve_link(item.google_link)
                item.resolve_status = "ok"
            except Exception as exc:  # noqa: BLE001 - isolate each item
                item.resolve_status = "failed"
                item.resolve_error = str(exc)

        await asyncio.gather(*(_one(item) for item in items))
        return items

    async def resolve_pending(self, items: list[GNewsItem]) -> list[GNewsItem]:
        pending = [item for item in items if item.resolve_status == "failed"]
        if pending:
            await self.resolve_all(pending)
        return items

    # Kept as a small parsing seam for offline tests and callers that already
    # consume the standalone collector package's parsing API.
    def _items_from_feed_text(
        self,
        raw_xml: str,
        *,
        max_items: int,
        min_date: Optional[datetime],
    ) -> list[GNewsItem]:
        items: list[GNewsItem] = []
        for entry in _parse_feed_text(raw_xml)[:max_items]:
            published = _parse_entry_date(entry)
            if min_date and published and published < min_date:
                continue
            items.append(
                GNewsItem(
                    title=(entry.get("title") or "").strip(),
                    google_link=(entry.get("link") or "").strip(),
                    published=published,
                    source=_extract_source(entry),
                    summary=_clean_html(entry.get("summary", "")),
                )
            )
        return items


async def resolve_google_news_url(
    url: str,
    client: Optional[httpx.AsyncClient] = None,
    *,
    limiter: Optional[RateLimiter] = None,
) -> str:
    """Resolve a Google News URL using the reference decoder implementation.

    This compatibility function remains the fetch-stage entry point.  It
    raises when a Google News link cannot be decoded; callers that need batch
    isolation should use ``GNewsCollector.resolve_all``.
    """
    if not url or not _is_google_news_link(url):
        return url

    collector = GNewsCollector(client=client, limiter=limiter)
    try:
        return await collector.resolve_link(url)
    finally:
        await collector.close()


# Collector tests and downstream integrations historically patched the
# compatibility function.  The collection stage uses this marker to preserve
# that injection seam without making the production path create a resolver
# for every item.
DEFAULT_RESOLVE_FUNCTION = resolve_google_news_url
