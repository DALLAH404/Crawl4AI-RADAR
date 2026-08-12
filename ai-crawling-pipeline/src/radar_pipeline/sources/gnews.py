"""Google News URL resolver — port of the 3-method resolver from codigo.gs.

Methods (tried in order):
    1. batchexecute API (Fbv4je/garturlreq payload)
    2. HTTP redirect following (Location header + body canonical link)
    3. Base64 decoding of articles/<id>

All outbound calls to ``news.google.com`` are routed through an
optional ``RateLimiter`` shared with the feed fetcher. When the limiter
reports a transient block (429 / 5xx) the resolver short-circuits and
returns the original URL — the legacy chain tried all three methods
against a blocked host, multiplying the Google News traffic by 3x per
item, which is what the radar pipeline used to do and exactly the
pattern that triggered the 503 block.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any
from urllib.parse import quote

import httpx

from radar_pipeline.sources.ratelimit import HostBlockedError, RateLimiter

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _decode_base64_url(article_id: str) -> str | None:
    try:
        padding = 4 - len(article_id) % 4
        if padding != 4:
            article_id += "=" * padding
        article_id = article_id.replace("-", "+").replace("_", "/")
        raw = base64.b64decode(article_id)
        text = "".join(
            chr(b) if 32 <= b < 127 else " "
            for b in raw
        )
        m = re.search(r"https?://[^\s\"']+", text)
        if m and "news.google" not in m.group(0):
            return m.group(0).rstrip(
                "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0e\x0f"
                "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b"
                "\x1c\x1d\x1e\x1f"
            )
    except Exception:
        pass
    return None


async def _gnews_get(
    limiter: RateLimiter | None,
    url: str,
    client: httpx.AsyncClient,
) -> httpx.Response | None:
    """GET ``url``, honoring the limiter. None = blocked / network error."""
    if limiter is not None:
        if await limiter.is_blocked(url):
            return None
        try:
            await limiter.acquire(url)
        except HostBlockedError:
            return None
        try:
            resp = await client.get(url, headers={"User-Agent": UA})
        finally:
            limiter.release(url)
    else:
        resp = await client.get(url, headers={"User-Agent": UA})

    if limiter is not None:
        _record(limiter, url, resp)
    if resp.status_code in (429,) or 500 <= resp.status_code < 600:
        return None
    return resp


async def _gnews_post(
    limiter: RateLimiter | None,
    url: str,
    client: httpx.AsyncClient,
    content: str,
) -> httpx.Response | None:
    if limiter is not None:
        if await limiter.is_blocked(url):
            return None
        try:
            await limiter.acquire(url)
        except HostBlockedError:
            return None
        try:
            resp = await client.post(
                url,
                content=content,
                headers={
                    "User-Agent": UA,
                    "Content-Type":
                        "application/x-www-form-urlencoded;charset=UTF-8",
                },
            )
        finally:
            limiter.release(url)
    else:
        resp = await client.post(
            url,
            content=content,
            headers={
                "User-Agent": UA,
                "Content-Type":
                    "application/x-www-form-urlencoded;charset=UTF-8",
            },
        )

    if limiter is not None:
        _record(limiter, url, resp)
    if resp.status_code in (429,) or 500 <= resp.status_code < 600:
        return None
    return resp


def _record(limiter: RateLimiter, url: str, resp: httpx.Response) -> None:
    retry_after = None
    ra = resp.headers.get("retry-after")
    if ra:
        try:
            retry_after = float(ra)
        except ValueError:
            retry_after = None
    if resp.status_code == 200:
        limiter.record_success(url)
    else:
        limiter.record_failure(url, status=resp.status_code, retry_after=retry_after)


async def _resolve_via_batchexecute(
    client: httpx.AsyncClient,
    url: str,
    article_id: str,
    limiter: RateLimiter | None = None,
) -> str | None:
    try:
        resp = await _gnews_get(limiter, url, client)
        if resp is None or resp.status_code != 200:
            return None
        html = resp.text

        m_sig = re.search(r'data-n-a-sg="([^"]+)"', html)
        m_ts = re.search(r'data-n-a-ts="([^"]+)"', html)
        if not m_sig or not m_ts:
            return None

        sig = m_sig.group(1)
        ts = m_ts.group(1)

        inner = json.dumps([
            "garturlreq",
            [
                ["X", "X", ["X", "X"], None, None, 1, 1, "US:en",
                 None, 1, None, None, None, None, None, 0, 1],
                "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
            ],
            article_id, ts, sig,
        ])

        payload_data = [[["Fbv4je", inner, None, "generic"]]]
        payload = "f.req=" + quote(json.dumps(payload_data))

        endpoint = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
        resp2 = await _gnews_post(limiter, endpoint, client, payload)
        if resp2 is None:
            return None

        text = resp2.text
        m_real = re.search(r"https?://(?!news\.google)[^\\\"\s]+", text)
        if m_real:
            return m_real.group(0).replace("\\u003d", "=").replace("\\u0026", "&")
    except Exception:
        pass
    return None


async def _resolve_via_redirect(
    client: httpx.AsyncClient,
    url: str,
    limiter: RateLimiter | None = None,
) -> str | None:
    try:
        resp = await _gnews_get(limiter, url, client)
        if resp is None:
            return None
        final_url = str(resp.url)
        if "news.google" not in final_url and final_url.startswith("http"):
            return final_url

        body = resp.text
        m_body = re.search(
            r'<link[^>]+rel="canonical"[^>]+href="(https?://(?:news\.google)?[^"]+)"',
            body, re.IGNORECASE,
        )
        if m_body and "news.google" not in m_body.group(1):
            return m_body.group(1)

        m_au = re.search(r'data-n-au="(https?://[^"]+)"', body)
        if m_au and "news.google" not in m_au.group(1):
            return m_au.group(1)
    except Exception:
        pass
    return None


async def resolve_google_news_url(
    url: str,
    client: httpx.AsyncClient | None = None,
    *,
    limiter: RateLimiter | None = None,
) -> str:
    """Resolve a Google News redirect URL to its underlying article URL.

    Returns ``url`` unchanged when it isn't a Google News link.
    """
    if not url or "news.google" not in url:
        return url

    m = re.search(r"/articles/([^?/]+)", url)
    if not m:
        return url

    article_id = m.group(1)
    close_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=30.0, follow_redirects=True,
        )

    try:
        # 1) batchexecute
        real = await _resolve_via_batchexecute(
            client, url, article_id, limiter=limiter,
        )
        if real:
            return real

        # If the limiter tripped during the batchexecute step,
        # stop: the remaining methods would only burn more requests
        # against the same blocked Google host.
        if limiter is not None and await limiter.is_blocked(url):
            return url

        # 2) redirect chain
        real = await _resolve_via_redirect(client, url, limiter=limiter)
        if real:
            return real

        if limiter is not None and await limiter.is_blocked(url):
            return url

        # 3) base64 decode — purely local, but only useful if the host
        # is responsive: if Google is currently blocking us there is no
        # cached underlying URL to find in the page body anyway.
        if limiter is not None and await limiter.is_blocked(url):
            return url

        real = _decode_base64_url(article_id)
        if real:
            return real
    finally:
        if close_client:
            await client.aclose()

    return url
