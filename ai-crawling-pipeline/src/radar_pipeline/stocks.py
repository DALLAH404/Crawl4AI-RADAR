"""European stock price scraper.

Twelve Data's free-tier API (used by the frontend's stock ticker) only
serves US-exchange symbols — every Euronext Paris / XETR / Stockholm symbol
tried came back a 403 "not available with your plan" (confirmed via a live
call, not assumed; see frontend/src/lib/stockTickers.ts). This module scrapes
finanzen.net's public stock pages instead for the companies Twelve Data can't
reach, and writes the result to S3 as one small JSON snapshot the frontend
reads directly.

finanzen.net's own robots.txt (checked before writing this) does not
disallow the /aktien/<slug>-aktie pages used here — only admin, search, and
export paths are blocked. Deliberately NOT scraping Google/Yahoo Finance:
both serve a mandatory GDPR consent wall before the real page, and clicking
through that programmatically is a different, worse thing than routine
scraping friction.

Each company's page can list more than one exchange (e.g. SKF's shows a
German EUR cross-listing before its primary Stockholm SEK one) — `currency`
picks which price block to use.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from crawl4ai import AsyncWebCrawler

# <price> <currency> <change> <currency> <percent>% <date> — confirmed against
# live pages for all four companies below before this was written.
PRICE_PATTERN = re.compile(
    r"([\d]{1,4}[.,]\d{2})\s*(EUR|SEK|USD)\s*[+-][\d]{1,4}[.,]\d{2}\s*(?:EUR|SEK|USD)\s*([+-][\d]{1,3}[.,]\d{2})\s*%"
)


@dataclass
class StockQuote:
    company: str
    price: float
    change_percent: float
    currency: str
    scraped_at: str


COMPANIES: list[dict[str, str]] = [
    {"company": "Valeo", "url": "https://www.finanzen.net/aktien/valeo-aktie", "currency": "EUR"},
    {"company": "Continental", "url": "https://www.finanzen.net/aktien/continental-aktie", "currency": "EUR"},
    {"company": "Schaeffler", "url": "https://www.finanzen.net/aktien/schaeffler-aktie", "currency": "EUR"},
    {"company": "SKF", "url": "https://www.finanzen.net/aktien/skf_group-aktie", "currency": "SEK"},
]


def _visible_text(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def parse_quote(html: str, currency: str) -> tuple[float, float] | None:
    """First price block matching `currency`, as (price, change_percent)."""
    text = _visible_text(html)
    for price_str, cur, percent_str in PRICE_PATTERN.findall(text):
        if cur != currency:
            continue
        return float(price_str.replace(",", ".")), float(percent_str.replace(",", "."))
    return None


async def scrape_stock(
    crawler: AsyncWebCrawler, company: str, url: str, currency: str
) -> StockQuote | None:
    result = await crawler.arun(url=url)
    if not result.success or not result.html:
        return None
    parsed = parse_quote(result.html, currency)
    if parsed is None:
        return None
    price, change_percent = parsed
    return StockQuote(
        company=company,
        price=price,
        change_percent=change_percent,
        currency=currency,
        scraped_at=datetime.now(timezone.utc).isoformat(),
    )


async def scrape_all_stocks(
    companies: list[dict[str, str]] = COMPANIES,
) -> list[StockQuote]:
    quotes: list[StockQuote] = []
    async with AsyncWebCrawler() as crawler:
        for entry in companies:
            quote = await scrape_stock(crawler, entry["company"], entry["url"], entry["currency"])
            if quote is not None:
                quotes.append(quote)
    return quotes


def quotes_to_json(quotes: list[StockQuote]) -> str:
    return json.dumps({"items": [asdict(q) for q in quotes]})


def upload_stocks_json(s3_client, bucket: str, key: str, quotes: list[StockQuote]) -> None:
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=quotes_to_json(quotes).encode("utf-8"),
        ContentType="application/json",
        CacheControl="max-age=3600",
    )
