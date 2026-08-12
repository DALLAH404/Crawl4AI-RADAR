"""Image extraction helpers — port of og:image and media:thumbnail extraction."""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property=["\']og:image["\']|content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\'])',
    re.IGNORECASE,
)

IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)

BAD_IMG_DOMAINS = [
    "gstatic.com", "news.google", "googleusercontent",
    "branding", "logo", "favicon", "default", "placeholder",
]


def extract_img_from_html(html: str) -> str:
    m = IMG_SRC_RE.search(html)
    return m.group(1) if m else ""


def img_is_valid(url: str) -> bool:
    if not url:
        return False
    lu = url.lower()
    return not any(b in lu for b in BAD_IMG_DOMAINS)


async def fetch_og_image(url: str, client: httpx.AsyncClient | None = None) -> str:
    close_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return ""
        html = resp.text[:100000]
        m = OG_IMAGE_RE.search(html)
        if m:
            img = m.group(1) or m.group(2)
            if img and img_is_valid(img):
                return img
        return ""
    except Exception:
        return ""
    finally:
        if close_client:
            await client.aclose()
