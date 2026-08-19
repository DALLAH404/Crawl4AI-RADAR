# RADAR read API

A single AWS Lambda (`lambda_function.py`) fronted by an API Gateway HTTP API,
serving one route — `GET /articles` — read-only against the same DynamoDB table
`ai-crawling-pipeline`'s scraper writes to. Built for the frontend (Phase 7): the
homepage feed and per-company timeline are both this one endpoint, called with
different query parameters.

No dependencies beyond `boto3` (bundled in every Lambda Python runtime) — this
file can be pasted directly into the Lambda console's inline code editor. No zip
upload, no layer, no build step. See `../DEPLOYMENT.md`'s Phase 6 for the AWS-side
setup steps.

## Route

```
GET /articles
```

| Query param | Meaning | Default |
|---|---|---|
| `limit` | Max items to return (1–100) | 20 |
| `cursor` | Opaque pagination token from a previous response's `next_cursor` | none (first page) |
| `company` | One or more source `tag` values, comma-separated (e.g. `Bosch,Valeo`) — switches from the latest-overall feed to a per-company one | none (latest-overall) |
| `from` | Start of the date range (`YYYY-MM-DD` or full ISO timestamp), only used with `company` | none |
| `to` | End of the date range, inclusive, only used with `company` | none |
| `kind` | `news` or `social` — the News/Social filter. Combinable with `company` | none (both kinds) |

Three query patterns depending on which of `company`/`kind` are present, matching
the DynamoDB schema in `ai-crawling-pipeline/src/radar_pipeline/README.md`'s
"Database layer":

- **Neither `company` nor `kind`** — latest N articles overall, via `LatestIndex`.
  This is the homepage feed — chronological, not grouped by company or kind (see
  the design discussion that motivated the schema).
- **`kind`, no `company`** — the un-companied News or Social feed, via `KindIndex`.
  Gets its own index (rather than filtering `LatestIndex`) because it can span the
  whole table, same reasoning as `LatestIndex` itself.
- **`company` present (with or without `kind`)** — one or more companies' articles
  within `from`/`to`, via `CompanyTimeIndex`. More than one company fans out into
  one `Query` per company (DynamoDB can't do an "IN" query against a partition key)
  and merges the results by `published_at`, deduping any article linked to more
  than one of the selected companies. When `kind` is also given, each company's
  Query is filtered by `content_kind` — a `FilterExpression`, not a second
  dedicated index, since one company's results are already narrow.

All three query patterns also filter out `summary_status='irrelevant'` and
`dedup_decision='duplicate'` articles (`_PUBLIC_ONLY` in `lambda_function.py`,
combining `_NOT_IRRELEVANT` and `_NOT_DUPLICATE`) — neither the LLM's relevance
verdict (`summarize/pipeline.py`'s `mark_article_irrelevant`) nor dedup's own
verdict (`dedup/layers.py`) ever removes an article from any index, they just
flag it (on two deliberately independent fields — see
`radar_pipeline/README.md`'s "summary_status / dedup_decision independence"),
so every read path here has to exclude both itself. `dedup_decision` isn't
denormalized onto company-link items the same way `action_description` is —
see `db.py`'s `_company_item` — so a company-filtered read only excludes
duplicates written after that field was added; older company-link items
predate it and won't be excluded until the article is next rewritten. These
filters — like the `content_kind` one above — interact with
DynamoDB's `Limit` in a way worth knowing about: `Limit` applies to items
*evaluated*, before any `FilterExpression` runs, so a naive single query can
come back with fewer than `limit` matches even when more exist. `_query_paginated`
(the plain latest/kind-only paths) and `_query_one_company` (the company path)
both loop on the pagination cursor until they actually collect `limit` matches
or run out of results, rather than under-returning — so pagination stays exact,
not approximate, in all three cases. Only the multi-company fan-out has the
"carries a cursor per company" approximation described next.

  Pagination across multiple companies is *approximate*, not exact — each page
  re-queries every selected company and re-merges, carrying a cursor per company
  rather than one global one. Fine at this project's scale; not something to build
  a heavier merged read-model for yet.

### Response

```json
{
  "items": [
    {
      "article_hash": "…",
      "title": "…",
      "link": "…",
      "image_url": "…",
      "summary": "…",
      "competitor_analysis": "…",
      "category": "auto",
      "content_kind": "news",
      "event_type": "Lancamento",
      "alert_level": "Alto",
      "summary_status": "ai_generated",
      "published_at": "2026-08-13",
      "companies": ["Bosch"],
      "source_name": "Bosch"
    }
  ],
  "next_cursor": "eyJwayI6...  or null"
}
```

Only ever this curated field set — internal/operational fields
(`dedup_reason`, `raw_link`, `extra`, `ingestion_batch_id`, `feed_type`, ...)
never leave the Lambda. Company-scoped results (via `CompanyTimeIndex`) carry a
smaller subset of real data than latest-overall results (via `LatestIndex`) —
`competitor_analysis` and `source_name` come back empty, because company-link
items are a deliberately partial, denormalized view (see the schema doc's
"Multi-company reads"), not because something's broken. Fields absent on a given
item come back as their empty default rather than being omitted from the JSON,
so the frontend never needs to guard against a missing key.

### Errors

`400` — bad `limit` (not a positive integer, though it's silently clamped to
100 rather than erroring if it's just *too high*), an empty `company` param, or a
`kind` value other than `news`/`social`. `405` — anything other than
`GET`/`OPTIONS`. `500` — anything unexpected talking to DynamoDB; the actual
exception is logged (CloudWatch), never returned to the caller.

## IAM

`iam/read-api-role-policy.json` — `dynamodb:Query` only, scoped to the
`radar-articles` table and its GSIs. No `GetItem` (nothing here does a
single-item lookup by key), no writes, no `Scan`. A separate role from the
scraper's task role (`ai-crawling-pipeline/iam/task-role-policy.json`) — this
Lambda should never be able to write to the table it reads from.

## Testing locally

```bash
pip install boto3 moto pytest
cd read-api
pytest tests/ -q
```

Tests run against a moto-mocked table (real DynamoDB API, no network, no AWS
account needed) — seeded by hand with raw `put_item` calls rather than importing
`radar_pipeline.db`, to keep this deployable unit's tests as standalone as the
Lambda code itself.
