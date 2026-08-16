"""Tests for og:image / fallback <img> extraction.

extract_og_image had a real bug: the original single regex only populated
its capture group for the content-before-property tag ordering, which is
the *rare* one — real pages overwhelmingly write property before content
(<meta property="og:image" content="...">). For that common ordering the
old code raised a caught IndexError internally and silently returned "",
with zero test coverage catching it. These tests pin both orderings.
"""

from __future__ import annotations

import httpx
import pytest

from radar_pipeline.fetch.image import (
    extract_img_from_html,
    extract_og_image,
    fetch_og_image,
    img_is_valid,
)


def test_extract_og_image_property_before_content():
    # The common real-world ordering — this is the case that was broken.
    html = '<meta property="og:image" content="https://example.com/a.jpg">'
    assert extract_og_image(html) == "https://example.com/a.jpg"


def test_extract_og_image_content_before_property():
    # The ordering the original regex actually handled.
    html = '<meta content="https://example.com/b.jpg" property="og:image">'
    assert extract_og_image(html) == "https://example.com/b.jpg"


def test_extract_og_image_no_tag_returns_empty():
    assert extract_og_image("<html><body>no og tags here</body></html>") == ""


def test_extract_og_image_ignores_other_meta_tags():
    html = (
        '<meta property="og:title" content="Some Title">'
        '<meta property="og:image" content="https://example.com/real.jpg">'
    )
    assert extract_og_image(html) == "https://example.com/real.jpg"


def test_extract_img_from_html_finds_first_img_src():
    html = '<div><img src="https://example.com/first.jpg"></div>'
    assert extract_img_from_html(html) == "https://example.com/first.jpg"


def test_extract_img_from_html_no_img_returns_empty():
    assert extract_img_from_html("<div>no images</div>") == ""


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/photo.jpg", True),
        ("https://www.gstatic.com/logo.png", False),
        ("https://news.google.com/favicon.ico", False),
        ("", False),
    ],
)
def test_img_is_valid_filters_bad_domains(url, expected):
    assert img_is_valid(url) is expected


@pytest.mark.asyncio
async def test_fetch_og_image_uses_og_tag_with_common_ordering():
    html = '<html><head><meta property="og:image" content="https://example.com/og.jpg"></head></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_og_image("https://example.com/article", client)

    assert result == "https://example.com/og.jpg"


@pytest.mark.asyncio
async def test_fetch_og_image_falls_back_to_plain_img_tag():
    html = '<html><body><img src="https://example.com/body-image.jpg"></body></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_og_image("https://example.com/article", client)

    assert result == "https://example.com/body-image.jpg"


@pytest.mark.asyncio
async def test_fetch_og_image_rejects_bad_domain_fallback():
    html = '<html><body><img src="https://www.gstatic.com/logo.png"></body></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_og_image("https://example.com/article", client)

    assert result == ""


@pytest.mark.asyncio
async def test_fetch_og_image_returns_empty_on_non_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_og_image("https://example.com/article", client)

    assert result == ""
