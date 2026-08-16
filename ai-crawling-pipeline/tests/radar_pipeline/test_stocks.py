"""Unit tests for the European stock scraper's parsing and S3 upload.

parse_quote is tested against small fixture HTML snippets shaped like the
real finanzen.net pages (verified live before this was written — see
stocks.py's docstring), not checked-in full page dumps.
"""

from __future__ import annotations

import json

import boto3
from moto import mock_aws

from radar_pipeline.stocks import StockQuote, parse_quote, upload_stocks_json

VALEO_SNIPPET = """
<div>Aktien-Sparplan anlegen 14,65 EUR -0,07 EUR -0,48 % 14.08.2026</div>
"""

# SKF's real page lists a German EUR cross-listing before its primary
# Stockholm SEK one — this fixture reproduces that ordering on purpose.
SKF_SNIPPET = """
<div>Sparplan anlegen 23,76 EUR -0,04 EUR -0,17 % 14.08.2026</div>
<div>OTC Stockholm 262,00 SEK +0,70 SEK +0,27 % 14.08.2026</div>
"""


def test_parse_quote_extracts_price_and_percent():
    assert parse_quote(VALEO_SNIPPET, "EUR") == (14.65, -0.48)


def test_parse_quote_picks_the_matching_currency_block():
    # Asking for SEK must skip the earlier EUR block, not just take the
    # first price-shaped match on the page.
    assert parse_quote(SKF_SNIPPET, "SEK") == (262.0, 0.27)


def test_parse_quote_returns_none_when_currency_not_present():
    assert parse_quote(VALEO_SNIPPET, "SEK") is None


def test_upload_stocks_json_writes_expected_shape():
    quotes = [
        StockQuote(
            company="Valeo",
            price=14.65,
            change_percent=-0.48,
            currency="EUR",
            scraped_at="2026-08-16T00:00:00+00:00",
        ),
    ]
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-bucket")
        upload_stocks_json(s3, "test-bucket", "stocks/latest.json", quotes)

        body = s3.get_object(Bucket="test-bucket", Key="stocks/latest.json")["Body"].read()
        assert json.loads(body) == {
            "items": [
                {
                    "company": "Valeo",
                    "price": 14.65,
                    "change_percent": -0.48,
                    "currency": "EUR",
                    "scraped_at": "2026-08-16T00:00:00+00:00",
                }
            ]
        }
