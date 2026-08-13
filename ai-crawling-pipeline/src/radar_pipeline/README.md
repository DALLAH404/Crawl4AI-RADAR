# Radar Pipeline

News-aggregation pipeline for the Brazilian automotive aftermarket. Drives a DynamoDB
table through four stages — **collect → fetch → dedup → summarize** — persisting
structured intelligence (summaries, event types, alert levels) per article. Runs as a
scheduled ECS Fargate task; storage moved from a local SQLite file to DynamoDB so the
pipeline has no local disk state to lose between runs (see [Database layer](#database-layer--dbpy)).

```
configs/radar.yaml  ──┐
                      ▼
   ┌──── radar-pipeline (cli.py) ────────────────────────────────┐
   │                                                             │
   │  sources/catalog.py ──► load 100+ sources straight from YAML│
   │                          (no DB — see Sources below)        │
   │                                                             │
   │  collect  sources/collector.py        RSS / Google News /   │
   │     │                                 LinkedIn companies    │
   │     │     sources/feedparser.py       async feed parse      │
   │     │     sources/gnews.py            Google News resolver  │
   │     │     sources/linkedin.py         LinkedIn post scrape  │
   │     │     classify/rules.py           keyword classifier    │
   │     ▼                                                       │
   │  fetch    fetch/crawl.py              Crawl4AI              │
   │     │                                 (LinkedIn: archives    │
   │     │                                  collected text, no    │
   │     │                                  extra request)        │
   │     │     fetch/image.py              og:image extractor     │
   │     │     fetch/writer.py             outputs/radar/raw/*.md│
   │     ▼                                                       │
   │  dedup    dedup/layers.py             Layer 0 + Layer 1     │
   │     │     db.py find_*                  exclude self         │
   │     ▼                                                       │
   │  summarize  summarize/pipeline.py    LLM (OpenAI-compat)   │
   │             summarize/client.py                              │
   │             summarize/writer.py      outputs/radar/processed│
   │             *.md                                             │
   │                                                             │
   └─────────────────────────────────────────────────────────────┘
                     │
                     ▼
   DynamoDB table (db.table_name)  +  outputs/radar/{raw/, processed/}
```

## Pipeline stages

### 1. Collect — `sources/collector.py:collect_once`

Takes the list of active sources loaded fresh from `configs/radar_sources.yaml`
(`sources/catalog.py:list_sources`, `active_only=True` — see [Sources](#sources) below)
and concurrently polls RSS feeds, Google News queries, and LinkedIn company pages. Key
behaviours:

- **Config-driven company + date-range scope** — `collect.companies` (a list of
  source `tag` values) restricts a run to those companies only; empty means every
  active source. `collect.hours_back` sets a fine-grained normal-mode cutoff (e.g. `3`
  for the every-3-hours schedule) instead of the coarser `days_back`; `--backfill`
  switches to `backfill_days` for a manual wide-range catch-up run. All three are
  overridable per-run via `--companies`, `--hours-back`, `--backfill` without editing
  the YAML.
- **Source types** — `rss_direct` (uses `rss_url` verbatim), `google_news_query`
  (builds a Google News RSS URL from `query_text`, optionally with a `when:Nd`/`when:Nh`
  date filter), and `linkedin_company` (scrapes the logged-out LinkedIn "About" page for
  `query_text`, treated as a company slug — see [LinkedIn sources](#linkedin-sources)
  below).
- **Two collection passes** — RSS/Google-News sources are gathered together, bounded
  by `CollectSettings.concurrency` via an `asyncio.Semaphore` (default 8), each with
  its own `httpx.AsyncClient`. LinkedIn sources run in a **separate** pass afterward,
  bounded by `CollectSettings.linkedin.concurrency` (default 1) — a LinkedIn source
  holds its slot for tens of seconds (browser launch + inter-company delay) and would
  otherwise starve the RSS semaphore.
- **Date window** — items older than `days_back` (normal mode) or `backfill_days`
  (backfill mode) are dropped before insertion (RSS/Google-News path only — LinkedIn
  applies its own `CollectSettings.linkedin.days` filter upstream in
  `fetch_company_posts`; see [Corner cases](#naive-vs-aware-datetime-comparison-in-the-date-window-check)).
- **Out-of-scope filter** — `classify/rules.eh_fora_de_escopo` discards items whose
  `title + summary` matches any `FORA_ESCOPO` keyword (e.g. classified ads, jobs).
  Applies to LinkedIn posts too.
- **URL resolution** — Google News URLs are resolved to their canonical publisher
  URL via `resolve_google_news_url` (`gnews.py`) *before* hashing, so that
  `article_hash = md5(resolved_link)` is stable across re-runs and feeds. LinkedIn
  post URLs are already canonical and are used as-is — the resolver is never called
  for `linkedin_company` sources.
- **Deduplicate at insert** — `find_by_article_hash(store, article_hash)` is checked
  first; then `put_article(store, article)` is itself a conditional write
  (`attribute_not_exists(pk)`) keyed on `article_hash`, so even a race between two
  concurrent collect passes can't create two items for the same hash — the loser's
  write is silently rejected rather than skipped by a pre-check that could race.
- **Keyword classify** — `classify_article(title, summary)` assigns
  `event_type / alert_level / is_launch` synchronously, with no LLM call. Values
  are stored at insert time and re-derivable later via `--classify`.
- **Inserted rows** always start with `summary_status = 'pending'` so downstream
  stages pick them up.

Schema columns written here: `article_hash`, `title_hash`, `published_at`,
`collected_at`, `category`, `competitor_tag`, `product_line`, `title`,
`action_description` (first 300 chars of `summary_text`, except LinkedIn — see
below), `event_type`, `alert_level`, `is_launch`, `image_url`, `link`, `raw_link`,
`ingestion_batch_id`, `source_id`, `source_name`, `extra` (LinkedIn only).

#### LinkedIn sources

`sources/linkedin.py` scrapes a company's public, logged-out LinkedIn "About" page
(`https://www.linkedin.com/company/<slug>/`, or `showcase/<slug>` for a Showcase
page) — no login, no credentials. LinkedIn only authwalls the dedicated `/posts/`
feed for logged-out visitors; the About page embeds an "Updates" section with the
company's most recent public posts, which is what gets parsed. This inherits the
extraction logic (and its documented caveats — relative-timestamp date estimates,
5-10 posts max, no pagination) from the original standalone
`scripts/linkedin_radar.py`, which remains in the repo as a no-DB debugging tool;
only the DB integration is new here.

- **No LLM filter at collect time** — unlike the standalone script, posts are not
  pre-filtered for topic relevance here. Every post within `linkedin.days` is
  inserted as `pending`; relevance is decided once, downstream, by the summarize
  stage's `relevant` verdict (same as every other source), so off-topic posts stay
  auditable in the DB as `summary_status='irrelevant'` instead of leaving no trace.
- **Politeness/circuit-breaking** reuses `sources/ratelimit.RateLimiter` rather than
  the script's own sleep bookkeeping: a limiter is built with
  `per_host_concurrency=1` and `request_delay_seconds=linkedin.delay_seconds`.
  Because every company shares the `www.linkedin.com` host, this both serializes
  companies and enforces the inter-company delay, and its circuit breaker (with
  `999`, LinkedIn's bot-detection status, added to the immediate-block set in
  `ratelimit.py`) gives the "a block is IP-wide, back the whole batch off" behaviour
  the script implemented by hand.
- **Blocked companies** raise `LinkedInBlockedError`, mapped to `FeedFetchError` the
  same way `HostBlockedError` is for RSS sources — the `collection_runs` row is
  recorded as `status='error'`, nothing is inserted, and the company is retried in
  full next run. The DB replaces the script's `state/<slug>.json` dedup ledger.
- **`action_description` holds the full post text**, not `[:300]` like RSS sources —
  there is no fetchable article body behind a LinkedIn post (see the fetch stage
  below), and `summarize` reads `action_description`, so truncating here would throw
  away the entire input.
- **`title`** is the first non-empty line of the post text (truncated to ~120 chars);
  for a media-only post with no caption, the collector falls back to
  `"<source name> LinkedIn post"` since the column is `NOT NULL`.
- **`image_url`** is the first post photo, or the first video's poster frame if there
  are no photos. Fetching media costs a second, separately-scrolled page load per
  company (`linkedin.include_media`, on by default) — LinkedIn lazy-loads post
  photos and the full-page scroll needed to trigger that also makes the
  reaction-count widget disappear, so it's a deliberately separate best-effort fetch
  from the primary one used for text/reactions/comments/dates. A failed media fetch
  just means that run's posts have no images/videos, never a reason to fail the
  company.
- **`extra`** is a JSON object: `linkedin_slug`, `relative_time`, `edited`,
  `estimated_age_days`, `reactions`, `comments`, `images[]`, `videos[]` (each
  `{poster, src}`).
- **Config** — `CollectSettings.linkedin` (`configs/radar.yaml` → `collect.linkedin`):
  `enabled`, `days`, `concurrency`, `delay_seconds`, `jitter_seconds`,
  `block_retries`, `block_retry_delay`, `block_cooldown_seconds`, `page_timeout_ms`,
  `include_media`, `max_posts_per_company`, `headless`.

### 2. Fetch — `fetch/crawl.py:fetch_articles`

Crawls the pending articles' resolved URLs with **Crawl4AI** and writes the page
content as Markdown to `outputs/radar/raw/<source_id>/<source_id>_<hash8>.md`.

- **Pending source** — `pending_articles(store)` queries the `PendingIndex` GSI
  (sparse: only items with `summary_status = 'pending'`) and filters to
  `category IN ('auto','economia') AND (dedup_decision NOT EXISTS OR dedup_decision
  != 'duplicate')`. `feed_type` (needed to route LinkedIn rows below) is denormalized
  onto the article itself at collect time — see [Sources](#sources) — rather than
  joined from a sources table, since DynamoDB has no join. (See
  [Dedup below](#3-dedup--deduplayerspy) for why duplicates are excluded.)
- **LinkedIn short-circuit** — `linkedin_company` rows skip Crawl4AI entirely and
  the og:image fetch below. LinkedIn authwalls individual `/posts/` URLs for
  logged-out crawlers, so there's nothing fetchable there, and the full post text
  was already captured at collect time (`Article.action_description`). The
  collected text is archived straight to `outputs/radar/raw/<source_id>/*.md` via
  the same `write_article_md`, counted as `fetched`, at zero network cost.
- **Browser config** — `BrowserConfig(**config.browser)` with `headless`
  defaulting to `True` via `setdefault`. The YAML `browser:` block may override
  every option. *Corner case*: a previous implementation passed `headless=True`
  *and* `**(config.browser if config else {})`. When the YAML block already
  contained `headless: true`, Python raised `got multiple values for keyword
  argument 'headless'`. The present code avoids this by `setdefault` on a copy.
- **URL pre-resolution** — every `link` containing `news.google` is resolved
  *again* via `resolve_google_news_url` (Google News links expire / change
  signature, so a fresh resolution is more reliable than reusing the value from
  collect).
- **Cross-article duplicate URL check** — re-computes `md5(target_url)` and
  queries `find_by_article_hash`. If a *different* article (`existing["article_hash"]
  != article["article_hash"]`) already points to the same canonical URL, the crawl is
  skipped (no markdown written, no failed counter incremented).
- **Image enrichment** — uses `image_url` from the feed if non-empty; otherwise
  fetches the page with `httpx` and parses `og:image` via
  `fetch/image.fetch_og_image`. Branding / logo domains (gstatic, google logos,
  favicons, default placeholders) are filtered out (`BAD_IMG_DOMAINS`).
- **Anti-block** — when `fetch.anti_block.enabled` is true, the Crawl4AI call is
  wrapped by `ai_crawling_pipeline.anti_block.crawl_with_retry`, which retries
  with backoff, rotated user-agents, and Crawl4AI `magic` / stealth flags based
  on `AntiBlockSettings`.
- **Output** — `fetch/writer.write_article_md` writes Markdown with YAML
  front-matter (`id_hash, source, category, tag, product_line, event_type,
  alert_level, date, image_url, url`).
- **Failure handling** — any exception in `_fetch_one` is caught and counted as
  `failed`; the pipeline does not abort on per-article failures. Empty markdown
  also counts as `failed`. The article's `image_url` is persisted via
  `update_article(store, article_hash, image_url=...)` (only on success).

### 3. Dedup — `dedup/layers.py`

Identifies duplicate articles so the summarize stage can skip them. The current
implementation keeps only **two layers**:

| Layer | Mechanism                              | Match scope                       | Reason         |
|-------|----------------------------------------|-----------------------------------|----------------|
| 0     | `article_hash` (= `md5(resolved_link)`) | whole table, via `find_by_article_hash` | `same_url`     |
| 1     | `title_hash` (= `md5(normalize_title)`)  | published within `title_window_hours`, via the `DedupIndex` GSI | `same_title`   |

Layer 0 is a structural no-op — `article_hash` is the table's own partition key, so
a lookup by an article's own hash can never return anything but itself, exclusion or
not. It's kept for behavioral parity with the pre-DynamoDB version rather than
special-cased away (see [Corner cases](#corner-cases--known-limitations)); the write
path (`put_article`'s conditional `attribute_not_exists(pk)`) is what actually
prevents a same-URL duplicate from ever reaching the table in the first place. Layer
1's `exclude_hash=article_hash` does matter, since a title-hash match is a *different*
item.

`classify_article` returns a `DedupResult` with `decision ∈ {duplicate, new}`,
the matching `layer`, `reason`, `match_hash` of the older duplicate, and optional
`score`.

`run_dedup` then writes back via `update_article`:
- If `duplicate` — sets `dedup_decision='duplicate'`, `dedup_layer`,
  `dedup_reason`, `dedup_match_hash`, `dedup_score`.
- If `new` — sets `dedup_decision='new'`.

`summary_status` is intentionally **not** modified here — it stays independent of
`dedup_decision` (DynamoDB has no CHECK constraint to enforce this, but the
application code never conflates the two). Instead, `pending_articles` excludes items
where `dedup_decision='duplicate'`, which is how the dedup→summarize handshake works.

#### Removed layers (preserved implementation)

Layers 2 (Jaccard + FTS5 hybrid) and 3/4 (Gemini embedding cosine + LLM judge)
were removed to make dedup both fast and quota-light. The implementation files
are intentionally **kept** for future use:

- `dedup/embedding.py` — `GeminiEmbedder.embed`, `.embed_many` (batch)
- `dedup/hybrid.py` — `hybrid_search`, `reciprocal_rank_fusion`
- `dedup/judge.py` — `judge` (LLM disambiguator)
- `db.fts5_search_within`, `db.knn_within`, `db.insert_vector`
- `DedupSettings.{use_hybrid_fts5, jaccard_*, embedding_*, gemini_embedding,
  judge_llm, ambiguity_*, top_k}` in `config.py`

These are dead code today; restoring Layer 3 is a one-line change in
`classify_article`. See [Performance notes](#performance-notes) for why they
were removed.

### 4. Summarize — `summarize/pipeline.py:run_summarize`

Calls an OpenAI-compatible LLM (default `deepseek-v4-flash` via a custom
`base_url`) for every *new* (non-duplicate) pending article and writes two
artefacts:

1. **DynamoDB update** — `update_article(store, article_hash, ...)` sets `summary`,
   `competitor_analysis`, `event_type`, `alert_level`,
   `summary_status='ai_generated'`, `ai_model`, `ai_processed_at` on the base item
   (and its company-link items — see [Database layer](#database-layer--dbpy)).
2. **JSON file** — `summarize/writer.write_summary_json` writes
   `outputs/radar/processed/<source_id>/<source_id>_<hash8>.json`
   containing a flat object with article metadata and the title, summary,
   competitor analysis, event_type, and alert_level.

Behaviour:

- **Input** — `pending_articles(store)` (same filter as fetch; dedup duplicates are
  excluded by construction).
- **Content assembled** — `action_description` is the base; if the article has
  a non-empty `summary` (e.g. set by a previous run), it is prepended.
- **Truncation** — content is truncated to `SummarizeSettings.max_input_chars`
  (default 20000) with an appended `[...truncated...]` marker.
- **Concurrency** — bounded by `SummarizeSettings.concurrency` (default 8) via
  `asyncio.Semaphore`.
- **Retries** — `summarize/client.summarize_one` retries up to `MAX_RETRIES=3`
  with exponential backoff (`1s, 2s, 4s`) on `APIError` / `APIConnectionError`.
  Other exceptions return `SummarizeResult(ok=False)` immediately.
- **JSON parsing** — the LLM is expected to return a JSON object with keys
  `summary, competitor_analysis, event_type, alert_level, relevant`. If parsing
  fails, the code falls back to a `re.search(r"\{.*\}")` to extract a JSON
  fragment; if that also fails, the raw text (first 900 chars) is used as the
  summary with `relevant=True` (graceful degradation).
- **Relevance** — articles where `result.relevant is False` are marked
  `summary_status='irrelevant'` via `mark_article_irrelevant` and skipped from
  the JSON writer. Non-relevant articles therefore never appear under
  `outputs/radar/processed/`.
- **Failed LLM calls** — `summary_status='failed'` via `mark_article_failed`. This is
  permanent until acted on: a failed article drops out of the pending queue and is
  never automatically retried on a later run. Run `radar-pipeline --retry-failed`
  (typically followed by `--summarize` in the same invocation) to reset every failed
  article back to `pending` and give it another attempt — see
  [Database layer](#database-layer--dbpy) for how the retry queue is found without a
  table Scan.

## Configuration

`configs/radar.yaml` is the canonical config and is loaded by
`config.load_radar_config`. Field defaults live in `config.py` dataclasses
(`DatabaseSettings`, `CollectSettings`, `FetchSettings`, `DedupSettings`,
`SummarizeSettings`, `SourcesSettings`, `GeminiEmbeddingSettings`,
`LLMSettings`).

### Paths (post-separation from `ai_crawling_pipeline`)

| Setting              | Default                          | Notes                                   |
|----------------------|----------------------------------|-----------------------------------------|
| `db.table_name`      | `radar-articles`                 | DynamoDB table (see below)              |
| `db.endpoint_url`    | unset                            | Local/dev only — dynamodb-local or moto; unset means real AWS |
| `fetch.output_dir`   | `outputs/radar/raw`              | Crawl4AI markdown output                |
| `summarize.output_dir` | `outputs/radar/processed`       | LLM-summarized JSON output              |
| `sources.yaml_path`  | `configs/radar_sources.yaml`    | Source catalog (100+ seeds, incl. 13 LinkedIn) |

Output paths were deliberately moved from the shared `outputs/` tree to
`outputs/radar/...` so the two pipelines (`ai_crawling_pipeline` and
`radar_pipeline`) no longer collide on `raw/` and `processed/`.

### CLI

`uv run radar-pipeline` exposes the following flags (see `cli._build_parser`):

| Flag                | Effect                                                        |
|---------------------|---------------------------------------------------------------|
| `--config PATH`     | Override the YAML config path.                                |
| `--collect`         | Poll all active sources.                                      |
| `--classify`        | Re-run the keyword classifier over pending articles.         |
| `--fetch`           | Crawl pending articles' URLs.                                |
| `--dedup`           | Run the 2-layer dedup pass over pending articles.            |
| `--retry-failed`    | Reset articles whose summarize call failed back to `pending`, so the next `--summarize` retries them. Not automatic — never runs as part of the default sequence. |
| `--summarize`       | Summarize pending (non-duplicate) articles.                  |
| `--status`          | Print source and article statistics (read-only).             |
| `--validate-feeds`  | Probe every active source URL without inserting any rows. LinkedIn sources are printed `[SKIP]` — httpx probing is meaningless against a browser-rendered page; use `--collect` to actually validate them. |
| `--backfill`        | Scope `--collect` to `collect.backfill_days` (wide manual catch-up) instead of the normal schedule window. |
| `--companies TAGS`  | Comma-separated source `tag`s to scope `--collect` to this run, overriding `collect.companies`. |
| `--hours-back N`    | Override `collect.hours_back` for this run (normal mode only). |

Sub-command: `sources-list [--active-only]` — reads straight from the YAML catalog.
There's no `sources-enable`/`sources-disable`/`sources-import-yaml` anymore: since
sources aren't stored in a database (see [Sources](#sources) below), there's nothing
left to seed or toggle at runtime — edit `active:` in `configs/radar_sources.yaml`
directly.

If **no stage flag is passed**, the default sequence runs:
`collect → fetch → dedup → summarize`. `--status` only prints stats and never
mutates data.

### Sources

Sources (RSS feeds, Google News queries, LinkedIn company slugs) live entirely in
`configs/radar_sources.yaml` — there is no DynamoDB table for them.
`sources/catalog.py:list_sources` parses the YAML fresh on every run; `active_only=True`
filters to `active: true`. This is a deliberate simplification from the SQLite version,
which seeded the YAML into a `sources` table and offered `sources-enable`/`sources-disable`
to flip `active` at runtime — that mutable state added a table and a migration
(`migration_0002`, widening the `feed_type` CHECK) for data that's small, static, and
hand-edited anyway. Toggling a source now means editing the YAML; there was nothing else
in that table worth keeping a database for.

### Environment variables

| Variable        | Used by                                  |
|-----------------|------------------------------------------|
| `GEMINI_API_KEY`| `GeminiEmbedder` (Layer 3, currently unused). |
| `OPENAI_API_KEY`| `summarize/client.AsyncOpenAI` and `dedup/judge.judge` (Layer 4, currently unused). |

LinkedIn collection needs no API key or credentials — it scrapes public,
logged-out pages via Crawl4AI's browser automation, same as every other
Crawl4AI-based fetch in this pipeline.

## Database layer — `db.py`

A single DynamoDB table (`db.table_name`, default `radar-articles`) accessed via
boto3's resource API. `connect()` returns a `RadarStore` wrapping the `Table`
resource; `ensure_table()` creates the table and its 4 GSIs if missing — **local/dev
use only** (moto in tests, optionally `dynamodb-local`). The production table is
created once via the console/CLI steps in `DEPLOYMENT.md`, not by the app on every
boot, so the task's IAM role can stay read/write-only (no `CreateTable`).

Identity is `article_hash` (`md5` of the resolved article URL) everywhere — there is
no auto-increment ID. DynamoDB has no ROWID equivalent, and keying writes off a value
derived from the article itself is what makes `put_article` idempotent: a re-scraped
article overwrites the same item instead of creating a duplicate. This replaced every
`article["id"]` reference across the fetch, dedup, summarize, and CLI stages with
`article["article_hash"]`.

### Schema — item types and GSIs

One table, three item types, sharing four GSIs (all `ProjectionType: ALL` — item
sizes here are small, so the extra storage cost buys skipping an N+1 `GetItem` on
every read):

| Item | Key | GSI entry | Purpose |
|---|---|---|---|
| **Base article** | `pk=ARTICLE#<hash>` `sk=METADATA` | `LatestIndex`: `gsi2pk=ARTICLE, gsi2sk=<ts>#<hash>` | Every article field; "latest N overall" |
| | | `DedupIndex`: `gsi3pk=TITLEHASH#<title_hash>, gsi3sk=<ts>#<hash>` | Layer 1 title-hash dedup within a window |
| | | `PendingIndex`: `gsi4pk=STATUS#<status>, gsi4sk=<ts>#<hash>` (only while `summary_status` is `pending` or `failed`) | `pending_articles()` / `failed_articles()` without scanning the whole table |
| **Raw-link pointer** | `pk=ARTICLE#<hash>` `sk=RAWLINK#<rawhash>` | `DedupIndex`: `gsi3pk=RAWLINK#<rawhash>, gsi3sk=METADATA` | `find_by_raw_link` — the fast pre-resolve dedup check |
| **Company link** | `pk=ARTICLE#<hash>` `sk=COMPANY#<tag>` | `CompanyTimeIndex`: `gsi1pk=COMPANY#<tag>, gsi1sk=<ts>#<hash>` | Per-company timeline (denormalized display fields, one item per company an article is relevant to) |

`Article.companies()` derives the company list from `competitor_tag` (comma-separated
if a source should fan an article out to more than one company — today every source
carries exactly one tag, so this is normally a single-element list). One article
produces one base item + one raw-link pointer + N company-link items, written as
separate `put_item` calls rather than a `TransactWriteItems` — see the comment on
`_write_article` for why that tradeoff was made at this project's scale.

The old SQLite `sources` and `collection_runs` tables have no DynamoDB equivalent —
sources moved to pure YAML config (see [Sources](#sources)), and per-run audit history
isn't persisted at all (CloudWatch Logs from the Fargate task covers it; see
`_reduce_results` in `collector.py`). `vec_articles` (sqlite-vec KNN) and `articles_fts`
(FTS5) were dropped too: both were already unused dead weight (Layers 2–4 were removed
from dedup before this migration — see [Removed layers](#removed-layers-preserved-implementation)),
and DynamoDB has no equivalent of either (would need OpenSearch to bring them back,
which is out of scope here).

### Retrying failed summarize attempts

`PendingIndex` is sparse on `summary_status`, but not sparse to just one value —
both `pending` and `failed` articles carry a `gsi4pk`, as `STATUS#pending` /
`STATUS#failed` respectively (`ai_generated`/`irrelevant` are terminal and carry
neither, so they're never indexed here at all). `pending_articles()` and
`failed_articles()` are the same query against the same GSI, just a different
`gsi4pk` value. This is what makes `retry_failed_articles()` — reset every failed
article's `summary_status` back to `pending` — a `Query` plus one `update_article`
per result, not a table `Scan`: the failed set is exactly as cheap to find as the
pending set, regardless of how large the table has grown. `radar-pipeline
--retry-failed` is the CLI entry point; nothing calls it automatically, since an
article that fails for a persistent reason (bad content, not a transient API
hiccup) would otherwise retry forever on every scheduled run.

### Multi-company reads

DynamoDB Query takes one partition-key value at a time, so filtering the frontend's
feed by more than one company (`articles_for_companies`) fans out into one
`CompanyTimeIndex` Query per company and merges the results in Python by
`published_at`, deduping on `article_hash` in case an article is linked to more than
one of the selected companies. The single-company case (`articles_for_company`) is
just one Query.

### Irrelevant articles are invisible to dedup lookups

`find_by_article_hash` and `find_by_title_hash_within` both skip an item whose
`summary_status == 'irrelevant'` — carried over from the old SQLite
`AND summary_status != 'irrelevant'` clause on the same lookups. `get_article` (used
by `update_article`, `--status`, etc.) does **not** filter this — an irrelevant
article is still a real, readable row; it just doesn't count as "already seen" for
dedup purposes.

## Observability — `observability/`

- **`logging.py`** — `setup_logging()` configures the `radar_pipeline` logger as
  a structured JSON logger (`{"ts": ..., "level": ..., "logger": ..., "msg":
  ...}`). Used by every module.
- **`metrics.py`** — `MetricsCollector` accumulates one `PipelineRun` per stage
  and prints a per-stage summary at the end of every run.

```
=== Pipeline Summary ===
  [OK] collect (46.0s)
         sources_ok: 90
         items_new: 46
  [OK] dedup (0.1s)
         total: 40
         new: 40
         duplicates: 0
```

## Corner cases & known limitations

This section documents every previously-broken behaviour and the resolution
applied, so that the next maintainer does not rediscover them.

### Self-match — structurally impossible now, was a bug pre-DynamoDB

**Original symptom (SQLite version)**: every pending article was reported as
`duplicate / layer=0 / same_url` with `dedup_match_id == article.id` (an article
matched itself), because `find_by_article_hash` queried the whole table with no
self-exclusion.

**Under DynamoDB**: `article_hash` is the table's own partition key. A lookup by an
article's own hash cannot return anything but that same item — `find_by_article_hash`
and `find_by_title_hash_within` both take `exclude_hash`, and Layer 0 in
`dedup/layers.py` is a documented no-op preserved for parity (see
[Database layer](#database-layer--dbpy)) rather than special-cased away. The old
class of bug — a lookup silently including the row you're checking — can't recur here
because there's no separate row-vs-key identity to forget to exclude.

### `summary_status` / `dedup_decision` independence

**Symptom (original)**: an earlier attempt set `summary_status='duplicate'` during
the dedup update, which crashed against SQLite's CHECK constraint on that column.

**Fix, preserved under DynamoDB**: dedup never touches `summary_status`. The
dedup→summarize handshake still uses two independent fields:

- `summary_status` (set by collect / summarize).
- `dedup_decision` (set by dedup).

`pending_articles` filters on both — via the sparse `PendingIndex` GSI (only items
with `summary_status='pending'` are in it at all) plus a `FilterExpression` for
`category IN ('auto','economia') AND (dedup_decision NOT EXISTS OR dedup_decision !=
'duplicate')`. DynamoDB has no CHECK constraint to enforce the separation the way
SQLite did, so this now relies entirely on the application code never conflating the
two — worth remembering before adding a third status-like field.

### `BrowserConfig` duplicate `headless` keyword

**Symptom**: `crawl4ai.async_configs.BrowserConfig() got multiple values for
keyword argument 'headless'` whenever the YAML `fetch.browser` block contained
its own `headless: true`.

**Cause**: `BrowserConfig(headless=True, **(config.browser))` unpacked a dict
that itself contained `headless`.

**Fix**: `crawl.py` now does `browser_kwargs = dict(config.browser or {});
browser_kwargs.setdefault("headless", True); BrowserConfig(**browser_kwargs)`.
The YAML can still override `headless` if it wants; the explicit default only
fires when the YAML omits the key.

### Empty `article_hash`

**Symptom**: an article whose URL resolution returned `""` would have
`article_hash = md5("")` — a well-known constant — and would falsely deduplicate
against every other empty-URL article.

**Fix**: `classify_article` logs a warning and skips Layer 0 when
`article_hash` is empty/falsy. The article then falls through to Layer 1
(title hash) and any later restored layer.

### Removed Layer 2 (Jaccard + FTS5) — false positives

**Symptom**: 38 of 40 fetched articles were flagged `duplicate / layer=2 /
fts5_match` even though they were distinct news stories.

**Cause**: the FTS5 keyword search returned a top hit for every title (FTS5
will always return *something* when there is any text overlap), and Layer 2
accepted the first hit unconditionally. Combined with no self-exclusion at the
time, even self-matches were possible.

**Fix**: Layer 2 is removed entirely from `classify_article`. The `use_hybrid_fts5`
flag still exists in config (it would otherwise need a migration); it only
gates the now-removed code path.

### Removed Layer 3 (Gemini embedding) — quota & latency

**Symptom**: dedup of 40 articles took ~299s and hit Gemini quotas on rapid
re-runs.

**Cause**: per-article `embedder.embed(text)` calls (sequential loop), plus a
redundant second embedding for every "new" article inside `run_dedup` for
`insert_vector`. Batching via `embed_many` mitigates latency (1.4s for 40
texts) but quota pressure remains.

**Fix**: Layer 3 (and Layer 4 judge) are removed from `classify_article`. The
`GeminiEmbedder` class, `embed_many`, `hybrid_search`, and `judge` are preserved as
dead code for opt-in re-enablement — though re-enabling now means more than flipping
a flag: `dedup/hybrid.py` and `db.fts5_search_within`/`db.knn_within`/`insert_vector`
were built against SQLite's `sqlite-vec`/FTS5 extensions, which have no DynamoDB
equivalent (`db.py` no longer defines them at all — those imports would now fail).
Reviving Layer 3/4 on DynamoDB would mean standing up something like OpenSearch for
the vector/full-text search, not just restoring the old function calls.

### `httpx.sleep` does not exist

In `sources/gnews.py:_resolve_via_batchexecute`, the retry branch was calling
`await httpx.sleep(0.5)`. `httpx` does not expose `sleep`. The correct call is
`await asyncio.sleep(0.5)`. This branch only fires when the first
`batchexecute` attempt fails, so the bug was latent — it would raise
`AttributeError` at runtime if triggered.

**Fix**: `asyncio` is now imported at the top of `gnews.py` and the retry call
uses `await asyncio.sleep(0.5)`.

### `feedparser.strip_html` unreachable statement

`strip_html` had an early `return` on the HTML-tag-strip + entity-replace line,
making the subsequent `re.sub(r"\s+", " ", text).strip()` and `unescape(text)`
unreachable. Whitespace collapse and final HTML-entity unescape were therefore
dead code. Feed items with multiple consecutive spaces or numeric character
references (e.g. `&#231;` for `ç`) would pass through un-collapsed and
un-unescaped.

**Fix**: the early `return` was changed to `text =` so the whitespace collapse
and `unescape` now execute on every call.

### `published_at` uses local time, not UTC

`feedparser.py` was computing
`published_at = datetime.fromtimestamp(time.mktime(published))`.
`time.mktime` interprets `feedparser`'s `published_parsed` struct_time as
*local* time, while feedparser actually returns UTC-normalized struct_times.
On hosts whose local timezone differs from UTC, `published_at` would be
offset by the timezone delta, which affects Layer 1's window computation
(`published_at >= since_ts`).

**Fix**: replaced `time.mktime` with `calendar.timegm` (which interprets the
struct_time as UTC) and `datetime.fromtimestamp` with
`datetime.fromtimestamp(ts, tz=timezone.utc)`. Timestamps are now consistently
UTC across the pipeline (matching `collector.py`'s
`datetime.now(timezone.utc)` convention). The inline `from datetime` / `import
time` were removed in favor of module-level `import calendar` and
`from datetime import datetime, timezone`.

### Naive vs. aware datetime comparison in the date-window check

`_process_source`'s date-window check
(`if pub_dt < cutoff: continue`) compares a **naive**
`datetime.fromisoformat(item["published_at"])` — RSS/Google-News
`published_at` values are stored without a `Z`/offset suffix — against a
**tz-aware** `cutoff` (`datetime.now(timezone.utc) - timedelta(...)`).
Comparing naive and aware datetimes raises `TypeError`, which the
surrounding `except (ValueError, TypeError): pass` silently swallows. The
`days_back`/`backfill_days` cutoff is therefore currently a no-op for
*every* RSS/Google-News source, not something introduced by LinkedIn
support.

**Not fixed here**: fixing it (parsing `published_at` as UTC-aware before
comparison) would retroactively change what a normal `--collect` run keeps
for every existing source, which is out of scope for adding LinkedIn as a
new source type. The LinkedIn path (`sources/linkedin.py:fetch_company_posts`)
does not depend on this check — it applies its own
`CollectSettings.linkedin.days` filter upstream, using LinkedIn's relative
timestamps, before items ever reach `_process_source`.

## Performance notes

Current stage timings on a ~40-article batch (90 sources polled):

| Stage      | Wall time  | Bottleneck                                  |
|------------|------------|---------------------------------------------|
| collect    | ~46s       | 90 RSS polls (many Google News 503s)        |
| fetch      | ~160s      | Crawl4AI per-article with Playwright        |
| **dedup**  | **~0.1s**  | Two point lookups per article (GetItem + GSI Query), no API calls |
| summarize  | ~70s       | One LLM call per relevant article (conc=8)  |

These timings predate the DynamoDB migration (they were measured against SQLite) but
the dedup stage's relative cheapness should carry over or improve — a `GetItem` by
`article_hash` and a `Query` against the sparse `DedupIndex` GSI are both O(1)-ish
regardless of table size, unlike a full-table SQL scan would become as the table
grows across months of 3-hourly runs. If Layer 3 is ever re-enabled (see
[Removed Layer 3](#removed-layer-3-gemini-embedding--quota--latency)), re-measure
here — it would need its own storage (OpenSearch or similar), not a DynamoDB GSI.

Future fetch speedups would require either higher concurrency, caching of
crawl results between runs, or a headless-browser pool rather than the current
per-article `AsyncWebCrawler` lifecycle in `crawl.py`.

## Module map

```
src/radar_pipeline/
├── __init__.py             re-exports `main`
├── __main__.py             `python -m radar_pipeline`
├── cli.py                  argparse + stage orchestration + status/sources-list
├── config.py               dataclasses + YAML loader (load_radar_config)
├── models.py               Source, Article, CollectionRun, DedupResult, SummarizeResult, ...
├── db.py                   DynamoDB layer: connect(), ensure_table() (local/dev),
│                           put_article, update_article, get_article, find_*,
│                           pending_articles, latest_articles, articles_for_company(ies),
│                           mark_article_irrelevant/failed, article_count*
├── classify/
│   ├── rules.py            eh_fora_de_escopo, classify_article (keyword priority chain)
│   └── keywords.py         FORA_ESCOPO, KW_LANCAMENTO, KW_PECA_AUTOMOTIVA, TEMAS_KW
├── sources/
│   ├── catalog.py          load_sources/list_sources — parses configs/radar_sources.yaml, no DB
│   ├── collector.py        collect_once (async, two passes: RSS/GNews + LinkedIn)
│   ├── feedparser.py       fetch_and_parse (httpx + feedparser)
│   ├── gnews.py            resolve_google_news_url (batchexecute → redirect → base64)
│   ├── linkedin.py         fetch_company_posts (Crawl4AI); extract_posts/extract_post_media
│   └── ratelimit.py        RateLimiter + circuit breaker (shared by GNews and LinkedIn)
├── fetch/
│   ├── crawl.py            fetch_articles (Crawl4AI + anti_block retry + og:image)
│   ├── image.py            fetch_og_image, BAD_IMG_DOMAINS filter
│   └── writer.py           write_article_md (Markdown + YAML front-matter)
├── dedup/
│   ├── layers.py           Deduper.classify_article (Layer 0 + 1), run_dedup
│   ├── embedding.py        GeminiEmbedder (PRESERVED, unused)
│   ├── hybrid.py           hybrid_search + reciprocal_rank_fusion (PRESERVED, unused —
│   │                       imports from db.py that no longer exist; see Removed Layer 3)
│   └── judge.py            judge LLM disambiguator (PRESERVED, unused)
├── summarize/
│   ├── pipeline.py         run_summarize (LLM concurrency + DB/JSON write)
│   ├── client.py           summarize_one (AsyncOpenAI + retries + JSON fallback)
│   ├── writer.py           write_summary_json (JSON)
│   └── prompts.py          DEFAULT_SYSTEM_PROMPT (aftermarket analyst persona)
└── observability/
    ├── logging.py          setup_logging (structured JSON logger)
    └── metrics.py          MetricsCollector + PipelineRun.summary()
```

## Running

```bash
# default sequence: collect -> fetch -> dedup -> summarize
uv run radar-pipeline

# isolated stages
uv run radar-pipeline --collect
uv run radar-pipeline --fetch
uv run radar-pipeline --dedup
uv run radar-pipeline --summarize

# inspection
uv run radar-pipeline --status
uv run radar-pipeline sources-list

# re-classify already-collected articles without re-pollling feeds
uv run radar-pipeline --classify

# probe source URLs without writing
uv run radar-pipeline --validate-feeds

# scope a run to specific companies and/or a tighter time window, without
# editing configs/radar.yaml
uv run radar-pipeline --collect --companies bosch,valeo --hours-back 3

# wide manual catch-up instead of the normal schedule window
uv run radar-pipeline --collect --backfill

# give up-front-failed summarize attempts another shot (e.g. after an LLM
# quota/outage clears), then actually retry them in the same invocation
uv run radar-pipeline --retry-failed --summarize
```

Point `db.endpoint_url` (in the config, or `--endpoint-url` on
`scripts/check_dynamodb_idempotency.py`) at `dynamodb-local` for a fully offline dev
loop:

```bash
docker run -p 8000:8000 amazon/dynamodb-local
uv run python scripts/check_dynamodb_idempotency.py --endpoint-url http://localhost:8000
```

Tests live in `tests/radar_pipeline/` and run via `uv run pytest
tests/radar_pipeline/ -q`. The suite covers classify, db (against a moto-mocked
DynamoDB table — see `conftest.py`), collector (RSS and LinkedIn), and the LinkedIn
extraction layer (126 tests).
