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

Two distinct query patterns, matching the DynamoDB schema in
`ai-crawling-pipeline/src/radar_pipeline/README.md`'s "Database layer":

- **No `company`** — latest N articles overall, via `LatestIndex`. This is the
  homepage feed — chronological, not grouped by company (see the design
  discussion that motivated the schema).
- **`company` present** — one or more companies' articles within `from`/`to`,
  via `CompanyTimeIndex`. More than one company fans out into one `Query` per
  company (DynamoDB can't do an "IN" query against a partition key) and merges
  the results by `published_at`, deduping any article linked to more than one
  of the selected companies. Pagination in this case is *approximate*, not
  exact — each page re-queries every selected company and re-merges, carrying a
  cursor per company rather than one global one. Fine at this project's scale;
  not something to build a heavier merged read-model for yet.

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
100 rather than erroring if it's just *too high*) or an empty `company` param.
`405` — anything other than `GET`/`OPTIONS`. `500` — anything unexpected talking
to DynamoDB; the actual exception is logged (CloudWatch), never returned to the
caller.

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
