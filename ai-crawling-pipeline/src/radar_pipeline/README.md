# Radar Pipeline

Local-first news-aggregation pipeline for the Brazilian automotive aftermarket. Drives a
single SQLite database through four stages — **collect → fetch → dedup → summarize** —
persisting structured intelligence (summaries, event types, alert levels) per article.

```
configs/radar.yaml  ──┐
                      ▼
   ┌──── radar-pipeline (cli.py) ────────────────────────────────┐
   │                                                             │
   │  sources/catalog.py ──► seed 100+ sources from YAML         │
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
        outputs/radar/{radar.db, raw/, processed/}
```

## Pipeline stages

### 1. Collect — `sources/collector.py:collect_once`

Reads active sources from the `sources` table and concurrently polls RSS feeds,
Google News queries, and LinkedIn company pages. Key behaviours:

- **Source types** — `rss_direct` (uses `rss_url` verbatim), `google_news_query`
  (builds a Google News RSS URL from `query_text`, optionally with a `when:Nd` date
  filter), and `linkedin_company` (scrapes the logged-out LinkedIn "About" page for
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
- **Deduplicate at insert** — `find_by_article_hash(con, article_hash, exclude_id=...)`
  is checked; if a different row already has the same hash, the item is skipped
  (no DB write).
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

- **Pending source** — `pending_articles(con)` returns rows where
  `summary_status = 'pending' AND category IN ('auto','economia') AND
  (dedup_decision IS NULL OR dedup_decision != 'duplicate')`, left-joined against
  `sources` for `feed_type` (needed to route LinkedIn rows below). (See
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
  queries `find_by_article_hash`. If a *different* article (`existing["id"] !=
  article["id"]`) already points to the same canonical URL, the crawl is
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
  `UPDATE articles SET image_url = ? WHERE id = ?` (only on success).

### 3. Dedup — `dedup/layers.py`

Identifies duplicate articles so the summarize stage can skip them. The current
implementation keeps only **two layers**:

| Layer | Mechanism                              | Match scope                       | Reason         |
|-------|----------------------------------------|-----------------------------------|----------------|
| 0     | `article_hash` (= `md5(resolved_link)`) | entire `articles` table            | `same_url`     |
| 1     | `title_hash` (= `md5(normalize_title)`)  | published within `title_window_hours` | `same_title`   |

Each layer excludes the article itself via `exclude_id=article["id"]`
(see [Corner cases — self-match](#self-match-bug--exclude_id)) so an article
never deduplicates against its own row.

`classify_article` returns a `DedupResult` with `decision ∈ {duplicate, new}`,
the matching `layer`, `reason`, `match_id` of the older duplicate, and optional
`score`.

`run_dedup` then writes back:
- If `duplicate` — sets `dedup_decision='duplicate'`, `dedup_layer`,
  `dedup_reason`, `dedup_match_id`, `dedup_score`.
- If `new` — sets `dedup_decision='new'`.

`summary_status` is intentionally **not** modified here (the schema's CHECK
constraint restricts it to
`pending / ai_generated / scraped_fallback / irrelevant / failed`). Instead,
`pending_articles` excludes rows where `dedup_decision='duplicate'`, which is
how the dedup→summarize handshake works (see [Corner cases — summary_status](#summary_status-constraint--dedup_decision-independence)).

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

1. **Database update** — `summary`, `competitor_analysis`, `event_type`,
   `alert_level`, `summary_status='ai_generated'`, `ai_model`, `ai_processed_at`.
2. **JSON file** — `summarize/writer.write_summary_json` writes
   `outputs/radar/processed/<source_id>/<source_id>_<article_id>_<hash8>.json`
   containing a flat object with article metadata and the title, summary,
   competitor analysis, event_type, and alert_level.

Behaviour:

- **Input** — `pending_articles(con)` (same filter as fetch; dedup duplicates are
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
- **Failed LLM calls** — `summary_status='failed'` via `mark_article_failed`.

## Configuration

`configs/radar.yaml` is the canonical config and is loaded by
`config.load_radar_config`. Field defaults live in `config.py` dataclasses
(`DatabaseSettings`, `CollectSettings`, `FetchSettings`, `DedupSettings`,
`SummarizeSettings`, `SourcesSettings`, `GeminiEmbeddingSettings`,
`LLMSettings`).

### Paths (post-separation from `ai_crawling_pipeline`)

| Setting              | Default                          | Notes                                   |
|----------------------|----------------------------------|-----------------------------------------|
| `db.path`            | `outputs/radar/radar.db`        | SQLite + sqlite-vec (WAL)               |
| `fetch.output_dir`   | `outputs/radar/raw`             | Crawl4AI markdown output                |
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
| `--summarize`       | Summarize pending (non-duplicate) articles.                  |
| `--status`          | Print source and article statistics (read-only).             |
| `--validate-feeds`  | Probe every active source URL without inserting any rows. LinkedIn sources are printed `[SKIP]` — httpx probing is meaningless against a browser-rendered page; use `--collect` to actually validate them. |

Sub-commands: `sources-list [--active-only]`, `sources-import-yaml`,
`sources-enable <id>`, `sources-disable <id>`.

If **no stage flag is passed**, the default sequence runs:
`collect → fetch → dedup → summarize`. `--status` only prints stats and never
mutates data.

### Environment variables

| Variable        | Used by                                  |
|-----------------|------------------------------------------|
| `GEMINI_API_KEY`| `GeminiEmbedder` (Layer 3, currently unused). |
| `OPENAI_API_KEY`| `summarize/client.AsyncOpenAI` and `dedup/judge.judge` (Layer 4, currently unused). |

LinkedIn collection needs no API key or credentials — it scrapes public,
logged-out pages via Crawl4AI's browser automation, same as every other
Crawl4AI-based fetch in this pipeline.

## Database layer — `db.py`

SQLite is opened via `pysqlite3` (the system `sqlite3` lacks the
`sqlite-vec` extension). `connect()`:

1. Creates the parent directory of `db.path` if missing.
2. Loads `sqlite_vec` for the `vec0` virtual table.
3. Sets `row_factory = sqlite3.Row`.
4. Enables WAL mode and foreign keys.

### Schema (created by `init_schema`)

- **`sources`** — catalog with CHECK constraints on `source_type`,
  `category ∈ {auto, economia, tecnologia}`, `feed_type ∈ {rss_direct,
  google_news_query, linkedin_company}` (widened from just the first two by
  `migrations/migration_0002.py` — SQLite can't `ALTER` a CHECK, so it's a
  table rebuild; see [Migrations](#migrations--migrations) below). For
  `linkedin_company` sources, `query_text` holds the LinkedIn company slug
  instead of a search query.
- **`articles`** — core table. Note the CHECK constraints:
  - `summary_status ∈ {pending, ai_generated, scraped_fallback, irrelevant,
    failed}` (no `'duplicate'` value — see "summary_status" corner case below).
  - `alert_level ∈ {Alto, Medio, Baixo, ''}`.
  - `dedup_decision ∈ {new, duplicate, ''}`.
  - `is_launch` and `active` are stored as INTEGER 0/1.
  - UNIQUE on `article_hash` and `link`.
- **`collection_runs`** — per-source audit of each collect invocation.
- **`vec_articles`** — `vec0` virtual table keyed by `articles.id` with a
  `float[768]` embedding column using cosine distance. Populated only when
  Layer 3 was active; currently unused.
- **`articles_fts`** — FTS5 over `title, summary, action_description` with
  `content='articles'` (external-content table) and triggers `articles_ai` /
  `articles_ad` (and an `articles_au` update trigger created by
  `_ensure_fts_triggers`). Syncs FTS on insert and delete but not on update.
- **Views** — `vw_source_health_daily`, `vw_stale_sources` for quick observability.

### Returned row shapes — corner case

Several functions in `db.py` return `list[dict[str, Any]]` rather than
`list[sqlite3.Row]`:

- `get_active_sources`, `get_all_sources` — used by the collector and CLI; the
  collector calls `.get("category", ...)` on each row, which `sqlite3.Row`
  doesn't support.
- `pending_articles` — used by both fetch and summarize; the fetch stage does
  `article.get("image_url")` on each row.

These functions wrap each row with `dict(r)`. Callers that index rows (e.g.
`find_by_article_hash` returning `sqlite3.Row | None`) are unchanged — Row
supports `row["col"]` but not `.get`.

### Migrations — `migrations/`

`run_migrations(con)` in `db.py` records the schema version in
`schema_version`. `apply_migration(con, version)` imports
`radar_pipeline.migrations.migration_NNNN` dynamically. New migrations should
follow the same `apply(con) -> None` convention and increment `SCHEMA_VERSION`
in `db.py`.

- `migration_0001` — initial schema (delegates to `init_schema`).
- `migration_0002` — widens `sources.feed_type`'s CHECK constraint to allow
  `'linkedin_company'`. Rebuilds the table (`sources_new` → copy rows → drop →
  rename), since SQLite has no `ALTER TABLE ... ALTER CHECK`. Idempotent: a
  no-op if the table's DDL already mentions `linkedin_company` — which is the
  case on a fresh DB, since `init_schema` already creates `sources` with the
  widened CHECK baked in. `init_schema`'s own `schema_version` stamp is
  therefore guarded to fire only on a genuinely fresh DB (no version row at
  all); an existing v1 DB is left for `run_migrations` to advance a step at a
  time, or `migration_0002` would never run (it would see `current == 2`
  already and skip).

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

### Self-match bug — `exclude_id`

**Symptom**: every pending article was reported as `duplicate / layer=0 /
same_url` with `dedup_match_id == article.id` (an article matched itself).

**Cause**: the article is inserted during collect, then dedup runs later and
queries `find_by_article_hash(con, article_hash)` against the *whole* table —
including the article's own row. No exclusion was applied.

**Fix**: `exclude_id: int | None = None` was added to `find_by_article_hash`,
`find_by_title_hash_within`, `candidate_titles_within`, `fts5_search_within`,
`knn_within`, and threaded through `classify_article(article_id=...)`,
`run_dedup(article_id=article["id"])`, and `hybrid_search(exclude_id=...)`.
Existing callers in `collector.py` and `crawl.py` use the default `None` and
remain unchanged.

**Verification**: after the fix, `SELECT COUNT(*) FROM articles WHERE
dedup_decision='duplicate' AND dedup_match_id = id` returns `0`.

### `summary_status` constraint — `dedup_decision` independence

**Symptom**: an earlier attempt set `summary_status='duplicate'` during the
dedup UPDATE, which crashed with `CHECK constraint failed: summary_status IN
('pending','ai_generated','scraped_fallback','irrelevant','failed')`.

**Cause**: the schema CHECK constraint enumerates allowed `summary_status`
values and does not include `'duplicate'`.

**Fix**: dedup never touches `summary_status`. The dedup→summarize handshake
uses two independent columns:

- `summary_status` (set by collect / summarize).
- `dedup_decision` (set by dedup).

`pending_articles` filters on both:
```sql
WHERE summary_status = 'pending'
  AND category IN ('auto','economia')
  AND (dedup_decision IS NULL OR dedup_decision != 'duplicate')
```

Consequence: an article stays in the pending pool after being marked duplicate
only if a future code path resets `dedup_decision`. The CLI never does this.

### `INSERT OR REPLACE` on vec0

**Symptom**: `UNIQUE constraint failed on vec_articles primary key` when
re-running dedup on a DB that already contained vectors.

**Cause**: `sqlite-vec`'s `vec0` virtual table does not support
`INSERT OR REPLACE`. The OR REPLACE keyword causes SQLite to fall back to a
DELETE-then-INSERT path that `vec0` does not implement for the primary key.

**Fix**: `insert_vector` does `DELETE FROM vec_articles WHERE rowid = ?` then
`INSERT INTO vec_articles(rowid, embedding) VALUES (?, ?)`. This is idempotent
on re-runs. The function itself is currently unused (Layer 3 is disabled); the
fix is in place so a future re-enable does not regress.

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

### `pysqlite3.Row` lacks `.get`

**Symptom**: `'pysqlite3.dbapi2.Row' object has no attribute 'get'` during
collect (`source.get("category", ...)`) and fetch
(`article.get("image_url")`).

**Cause**: `connect()` sets `row_factory = sqlite3.Row`. Row supports
`row["col"]` and `row[0]` indexing but not the dict-only `.get` method.

**Fix**: `get_active_sources`, `get_all_sources`, and `pending_articles` return
`list[dict[str, Any]]` by wrapping each row with `dict(r)`. Other find
functions (`find_by_article_hash`, `find_by_title_hash_within`) still return
`sqlite3.Row | None` because their callers only use `row["col"]` indexing.

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
`GeminiEmbedder` class, `embed_many`, `hybrid_search`, `judge`, and
`insert_vector` are preserved as dead code for opt-in re-enablement.

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
| **dedup**  | **~0.1s**  | Two SQL lookups per article, no API calls   |
| summarize  | ~70s       | One LLM call per relevant article (conc=8)  |

dedup's ~3000× speedup vs. the previous embedding-based implementation is the
direct result of removing the Gemini `embed_many` round-trip and the
sequential per-article loop. If Layer 3 is re-enabled, prefer the existing
`embed_many` batch path (one API call for the whole corpus) over per-article
`embed` calls, and re-use the returned embedding for `insert_vector` to avoid
the redundant double-embed seen in the original code.

Future fetch speedups would require either higher concurrency, caching of
crawl results between runs, or a headless-browser pool rather than the current
per-article `AsyncWebCrawler` lifecycle in `crawl.py`.

## Module map

```
src/radar_pipeline/
├── __init__.py             re-exports `main`
├── __main__.py             `python -m radar_pipeline`
├── cli.py                  argparse + stage orchestration + status/sources subcommands
├── config.py               dataclasses + YAML loader (load_radar_config)
├── models.py               Source, Article, CollectionRun, DedupResult, SummarizeResult, ...
├── db.py                   connect(), init_schema(), run_migrations(), find_*,
│                           pending_articles, insert_article, insert_source, insert_vector,
│                           mark_article_irrelevant/failed, candidate/fts5/knn helpers
├── classify/
│   ├── rules.py            eh_fora_de_escopo, classify_article (keyword priority chain)
│   └── keywords.py         FORA_ESCOPO, KW_LANCAMENTO, KW_PECA_AUTOMOTIVA, TEMAS_KW
├── sources/
│   ├── catalog.py          seed_from_yaml, add/set_active/list sources
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
│   ├── hybrid.py           hybrid_search + reciprocal_rank_fusion (PRESERVED, unused)
│   └── judge.py            judge LLM disambiguator (PRESERVED, unused)
├── migrations/
│   ├── __init__.py          apply_migration dispatch
│   ├── migration_0001.py   initial migration
│   └── migration_0002.py   widen sources.feed_type CHECK for linkedin_company
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
```

Tests live in `tests/radar_pipeline/` and run via `uv run pytest
tests/radar_pipeline/ -q`. The suite covers classify, db, collector (RSS and
LinkedIn), and the LinkedIn extraction layer (127 tests).
