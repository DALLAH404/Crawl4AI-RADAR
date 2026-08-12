# AI Crawling Pipeline

An asynchronous, configuration-driven pipeline for turning web pages into clean Markdown and concise AI-generated summaries.

The project uses [Crawl4AI](https://github.com/unclecode/crawl4ai) to render and extract web content, then sends the extracted content to an OpenAI-compatible chat-completions endpoint for summarization. Its central objective is to make web-content collection dependable across ordinary, JavaScript-heavy, authenticated, and occasionally bot-protected pages while keeping the workflow reproducible through YAML configuration.

## Objective

The pipeline is designed to solve a practical content-collection problem:

> Collect useful content from different kinds of websites, preserve it in a portable format, and produce compact summaries that are ready for downstream research, search, analysis, or AI workflows.

This objective has two parts:

1. **Reliable acquisition:** use a real browser to handle rendered pages, waiting conditions, JavaScript, sessions, and login flows. Detect likely blocks or transient failures instead of treating every HTTP-success response as valid content. Retry with exponential backoff and optional user-agent rotation when a target appears blocked.
2. **Useful normalization:** save each successful crawl as Markdown with its title and source URL, then summarize the saved Markdown asynchronously using a configurable LLM endpoint.

The package metadata also describes the project as extracting, formatting, summarizing, and categorizing web content. The current implementation fully covers extraction, Markdown formatting, and summarization. A separate categorization stage is not yet implemented, but the raw and processed Markdown outputs provide a suitable foundation for adding one.

## What It Does

- Crawls multiple URLs sequentially through one shared asynchronous browser session.
- Supports static pages, JavaScript-rendered pages, delayed content, custom JavaScript, and session-aware targets.
- Converts successful Crawl4AI results into Markdown files with title and URL metadata.
- Detects likely blocking through HTTP status codes, transient network errors, captcha or Cloudflare markers, and suspiciously short content.
- Retries blocked or transiently failed crawls with exponential backoff and jitter.
- Optionally rotates user agents between attempts and enables Crawl4AI `magic` and browser stealth settings.
- Allows global defaults and per-target overrides in YAML.
- Summarizes all raw Markdown files concurrently through any OpenAI-compatible API.
- Supports crawl-only, summarize-only, and full-pipeline execution.
- Keeps raw extraction separate from processed summaries so each stage can be rerun independently.

## Pipeline Flow

```text
YAML configuration
        |
        v
BrowserConfig + target settings
        |
        v
For each target: crawl -> detect block -> retry/backoff/rotate if needed
        |
        v
outputs/raw/<slug>.md
        |
        v
Dedup: classify each item against the local SQLite + sqlite-vec store
        |  (url hash -> title hash -> Jaccard -> Gemini embedding -> LLM judge)
        v
items table: status='new' (primary) or status='dupe' (skipped)
        |
        v
Summarize only status='new' AND summarized_at IS NULL items
        |  (set summarized_at on success; --resummarize clears it for all primaries)
        v
outputs/processed/<slug>.md
```

### Stage 1: Crawl

The crawler loads the browser settings, shared run defaults, anti-block policy, and target list from YAML. Each target can specify a CSS or JavaScript wait condition, custom JavaScript, a session ID, and any supported Crawl4AI `CrawlerRunConfig` option.

The targets are processed sequentially while sharing one `AsyncWebCrawler` instance. This keeps browser startup overhead low and allows session-aware flows to remain within the same browser lifecycle.

Successful results are written to the configured raw output directory. Failed targets are reported in the terminal and do not prevent the remaining targets from being attempted.

### Stage 2: Summarize

The summarizer reads every `*.md` file in the configured raw input directory. It extracts the title and source URL from the crawl header, limits the input to `max_input_chars`, and sends the content to an OpenAI-compatible chat-completions endpoint.

Summaries are processed concurrently with a bounded semaphore and written using the same filename in the processed output directory. This means the raw and processed artifacts can be matched by slug.

If a `dedup` section is present in the config, the summarizer is automatically driven by the dedup database: only items with `status='new'` are summarized, and duplicates are skipped (their raw `.md` files remain on disk for auditing).

### Stage 1.5: Dedup / filteration (optional)

Between crawl and summarize, the pipeline can run a five-layer dedup stage that filters out near-duplicate content before it reaches the LLM. This is a local re-implementation of the layered algorithm from `filteration.md`, with the original BigQuery store replaced by SQLite plus the `sqlite-vec` loadable extension, and the original Gemini Flash judge replaced by any OpenAI-compatible chat-completions endpoint. The Gemini embedding model is preserved.

For each raw `.md` file the dedup stage runs:

| Layer | What it does | Local implementation |
| --- | --- | --- |
| 0 | `md5(canonical_url)` lookup | `SELECT 1 FROM items WHERE id_hash = ? AND status='new'` |
| 1 | `md5(normalize(title))` lookup within a time window | `SELECT 1 FROM items WHERE title_hash = ? AND created_at >= ? AND status='new'` |
| 2 | Jaccard(title tokens) over a time window | Python `jaccard(set, set)` over SQLite-cached candidate titles |
| 3 | Embedding cosine over a time window | `sqlite-vec` KNN `MATCH ... ORDER BY distance LIMIT k` with `distance_metric=cosine` |
| 4 | LLM judge for ambiguous cases (`0.75 < cosine <= 0.85`) | OpenAI-compatible chat-completions (same shape as `summary.llm`) |

The dedup state lives in a single SQLite file (`dedup.db_path`) with one `items` table and a `vec0` virtual table for embedding KNN. Each call to the dedup stage inserts a row reflecting the decision: `status='new'` for primaries, `status='dupe'` with `dedup_layer` and `dupe_of_hash` for duplicates. Existing rows are never mutated, so the `new` set is stable across runs.

The summarizer is automatically gated by `status='new' AND summarized_at IS NULL` whenever a `dedup` section is configured. The `summarized_at` column is set on a successful summarize call, so re-running the pipeline without `--resummarize` is idempotent and does not call the LLM for items that have already been summarized. Raw `.md` files are not moved; only the DB decision is consulted.

## Anti-Block Strategy

Anti-block handling is the main reliability feature of this project. It is intended to identify responses that technically completed but do not contain the requested page, then retry them using a controlled policy.

### Block signals

By default, a crawl is considered blocked or transiently failed when one or more of these conditions is met:

- The response status is `403`, `429`, or `503`.
- An exception contains a transient marker such as `timeout`, `net::`, `ERR_`, `connection reset`, or `forbidden`.
- The response contains indicators such as `captcha`, `access denied`, `Cloudflare Ray`, `just a moment`, or bot-detection text.
- The crawl reports success but produces fewer than `min_content_chars` characters of Markdown.

Short-content detection is useful for cases where a challenge page returns HTTP `200` rather than an explicit error status.

### Retry behavior

When anti-block handling is enabled, each target receives up to `max_retries` attempts. The delay before retrying is calculated as:

```text
backoff_seconds * 2^(attempt - 1) + random_jitter
```

The user-agent pool can rotate between attempts. Crawl4AI's `magic` option and browser stealth mode can also be enabled globally or overridden for individual targets. These options improve resilience but cannot guarantee that every site will permit automated access.

Use this feature responsibly. Respect a site's terms of service, robots policy, authentication requirements, rate limits, and applicable law. Anti-block settings are not a substitute for permission to crawl a site.

## Project Structure

```text
.
├── configs/
│   ├── default.yaml       # Ready-to-run sample configuration and targets
│   ├── example.yaml       # Annotated configuration reference
│   └── radar.yaml         # radar-aftermarket-pipeline configuration (90 feeds)
├── outputs/
│   ├── raw/               # Markdown produced by the crawl stage
│   ├── processed/         # LLM summaries produced from raw Markdown
│   └── dedup.db           # Local SQLite + sqlite-vec store for the dedup stage
├── src/
│   ├── ai_crawling_pipeline/    # Original URL-crawl pipeline
│   │   ├── __init__.py        # CLI argument parsing and pipeline orchestration
│   │   ├── anti_block.py      # Block detection, retry, backoff, and UA rotation
│   │   ├── config.py          # YAML loading, validation, and settings models
│   │   ├── crawl.py           # Browser setup, target execution, and raw output
│   │   ├── db.py              # SQLite + sqlite-vec connection, schema, and queries
│   │   ├── dedup.py           # 5-layer dedup/filteration stage (local)
│   │   ├── embedding.py       # Async Gemini embedding client
│   │   └── summary.py         # Concurrent LLM summarization and processed output
│   └── radar_aftermarket_pipeline/   # Feed-aggregator pipeline (this repo)
│       ├── cli.py             # CLI: --collect/--classify/--fetch/--dedup/--summarize
│       ├── config.py          # RadarConfig + Feed + collect/classify/fetch settings
│       ├── feeds.py           # feedparser over RSS + Google-News search queries
│       ├── gnews.py           # 3-method Google-News URL resolver (httpx)
│       ├── classify.py        # PT-BR keyword matchers, English labels
│       └── fetch.py           # Shared Crawl4AI + crawl_with_retry + writer
├── tests/
│   ├── test_dedup_helpers.py
│   ├── test_dedup_layers.py
│   ├── test_pipeline_entry.py
│   ├── test_radar_classify.py
│   ├── test_radar_config.py
│   ├── test_radar_gnews.py
│   └── test_summarize_tracking.py
├── .env.example           # Environment variable template
├── pyproject.toml         # Package metadata and dependencies
└── uv.lock                # Locked dependency versions
```

### Two pipelines, one shared core

This repository hosts two CLI packages that share the same dedup, summary,
anti-block, and configuration machinery:

- **`ai-crawling-pipeline`** — the original pipeline: a list of static
  URLs is browser-crawled with Crawl4AI into Markdown, then summarized.
- **`radar-aftermarket-pipeline`** — a feed-aggregator for the Brazilian
  automotive aftermarket: ~90 RSS and Google-News feeds are polled,
  Google-News wrapper URLs are resolved, articles are fetched with the
  same Crawl4AI session, classified with PT-BR keyword rules, and
  passed to the same dedup and summary stages. See
  `src/radar_aftermarket_pipeline/README.md` for details (or read the
  `radar-aftermarket-pipeline` section below).

Both `ai-crawling-pipeline` and `radar-aftermarket-pipeline` are
installed by `uv sync` and exposed as console scripts.

## Requirements

- Python `3.12` or newer
- [uv](https://docs.astral.sh/uv/)
- A supported browser runtime for Crawl4AI
- An API key for the OpenAI-compatible endpoint used by the summary stage
- (Optional, for the dedup stage) a Gemini API key for embeddings
- (Optional, for the dedup stage's layer 4 judge) an OpenAI-compatible endpoint

## Installation

Install the locked dependencies from the project root:

```bash
uv sync
```

Create a local environment file from the included template:

```bash
cp .env.example .env
```

Set the keys in `.env` (one for the summary LLM, one for Gemini embeddings):

```dotenv
OPENAI_API_KEY=your-api-key
GEMINI_API_KEY=your-gemini-api-key
```

If Crawl4AI has not installed its browser dependencies in the environment, run its setup command:

```bash
uv run crawl4ai-setup
```

The repository ignores `.env` and generated `outputs/` files. Do not commit API keys or other credentials.

## Quick Start

### ai-crawling-pipeline (URL → Markdown → Summary)

Run the complete pipeline with the default configuration:

```bash
uv run ai-crawling-pipeline
```

This performs both stages in order:

```text
configs/default.yaml -> crawl -> outputs/raw/*.md -> summarize -> outputs/processed/*.md
```

The default configuration includes publicly available demonstration targets, including Books to Scrape, Quotes to Scrape, ScrapethisSite, the Oxylabs sandbox, Hugging Face pricing, and a Bosch article. Replace these targets with URLs you are authorized to crawl before using the project for real data collection.

### radar-aftermarket-pipeline (Feeds → Classify → Fetch → Dedup → Summary)

Validate every configured feed (no browser, no LLM):

```bash
uv run radar-aftermarket-pipeline --validate-feeds
```

Run the full chain against `configs/radar.yaml` (90 Brazilian automotive
aftermarket sources):

```bash
uv run radar-aftermarket-pipeline
```

Or a single stage:

```bash
uv run radar-aftermarket-pipeline --collect --classify
uv run radar-aftermarket-pipeline --fetch
uv run radar-aftermarket-pipeline --dedup
uv run radar-aftermarket-pipeline --summarize
```

## Command-Line Usage

### ai-crawling-pipeline

The installed console script is `ai-crawling-pipeline`.

#### Full pipeline

With no stage flag, the command crawls first and summarizes second:

```bash
uv run ai-crawling-pipeline
uv run ai-crawling-pipeline configs/example.yaml
uv run ai-crawling-pipeline --config configs/example.yaml
uv run ai-crawling-pipeline --config=configs/example.yaml
```

#### Crawl only

Use this when you want to refresh raw Markdown without calling the LLM:

```bash
uv run ai-crawling-pipeline --crawl --config configs/default.yaml
```

#### Summarize only

Use this to rerun summarization against existing raw Markdown:

```bash
uv run ai-crawling-pipeline --summarize --config configs/default.yaml
```

The summarize-only mode requires a `summary` section and at least one `*.md` file in its configured input directory. When a `dedup` section is configured, the summarizer reads from the dedup DB and only summarizes items with `status='new'`.

#### Dedup only

Use this to (re)classify existing raw Markdown without crawling or summarizing:

```bash
uv run ai-crawling-pipeline --dedup --config configs/default.yaml
```

The dedup-only mode requires a `dedup` section in the config and at least one `*.md` file in the configured `dedup.input_dir`. The DB at `dedup.db_path` is updated in place.

#### Resummarize existing items

By default, `--summarize` (and the full pipeline) is idempotent: each `items` row has a `summarized_at` timestamp, and only rows where that is `NULL` are sent to the LLM. Re-running the pipeline without this flag is a no-op for items that were already summarized in a previous run.

To force a regeneration of all primary items (e.g. after changing the model or the summary prompt in the YAML), pass `--resummarize` together with `--summarize`. The flag clears `summarized_at` for every primary and re-summarizes them:

```bash
uv run ai-crawling-pipeline --summarize --resummarize --config configs/default.yaml
```

`--resummarize` requires a `dedup` section in the config and is a no-op for the file-globbing `summarize_all` path (when `--summarize` is run without `--dedup`).

#### Configuration through the environment

When no configuration path is provided, the command uses `CONFIG_PATH` if it is set; otherwise it uses `configs/default.yaml`:

```bash
CONFIG_PATH=configs/example.yaml uv run ai-crawling-pipeline
```

The CLI rejects unknown flags and duplicate configuration-path arguments rather than silently falling back to the default configuration.

### radar-aftermarket-pipeline

The installed console script is `radar-aftermarket-pipeline`.

| Flag                 | Stage                                              |
|----------------------|----------------------------------------------------|
| `--collect`          | Poll all configured feeds                          |
| `--classify`         | Annotate `items.jsonl` with event_type/alert_level |
| `--fetch`            | Fetch articles that pass the classifier filters    |
| `--dedup`            | 5-layer dedup                                      |
| `--summarize`        | LLM summary                                        |
| `--resummarize`      | With `--summarize`: clear `summarized_at` and re-run |
| `--validate-feeds`   | Probe feed URLs, no fetching                       |
| `--config <path>`    | Custom config file                                 |

With no flag, the default chain runs: collect + classify + fetch + dedup +
summarize.

## Configuration

The configuration is YAML-based. `configs/example.yaml` documents the supported fields, while `configs/default.yaml` provides a working configuration.

### Browser settings

The `browser` mapping is passed to Crawl4AI's `BrowserConfig`:

```yaml
browser:
  headless: true
  viewport_width: 1280
  viewport_height: 800
  # user_agent: "Mozilla/5.0 ..."
  # proxy_config:
  #   server: http://proxy:8080
```

Browser settings apply to the shared browser session. Stealth mode is enabled automatically when the global anti-block policy has `enable_stealth: true`, unless the browser configuration already specifies the value.

### Global run defaults

The `defaults` mapping is passed to each Crawl4AI `CrawlerRunConfig`. Common options include:

```yaml
defaults:
  cache_mode: bypass
  remove_overlay_elements: true
  page_timeout: 60000
  screenshot: false
  # excluded_tags: ["nav", "footer"]
  # css_selector: ".main-content"
```

Supported cache mode names are `enabled`, `bypass`, `disabled`, `read_only`, and `write_only`. Any option supported by the installed Crawl4AI version can be supplied if it can be represented in YAML.

### Anti-block settings

The global `anti_block` mapping controls detection and retry behavior:

```yaml
anti_block:
  enabled: true
  max_retries: 3
  backoff_seconds: 2.0
  jitter_seconds: 0.5
  rotate_user_agent: true
  magic: true
  enable_stealth: true
  min_content_chars: 200
  block_status_codes: [403, 429, 503]
  block_indicators:
    - "access denied"
    - "captcha"
  user_agents:
    - "Mozilla/5.0 ..."
```

The settings are optional. If a target contains an `anti_block` mapping, its values override the global policy for that target only:

```yaml
targets:
  - slug: friendly_site
    url: https://example.com/friendly
    anti_block:
      enabled: false

  - slug: tough_target
    url: https://example.com/tough
    anti_block:
      max_retries: 5
      backoff_seconds: 5.0
      magic: false
```

### Output settings

The crawl stage writes raw Markdown to `output.dir`:

```yaml
output:
  dir: outputs/raw
```

The directory is created automatically. Each target is saved as `<slug>.md`; slugs should therefore be unique within a configuration.

### Targets

Every target requires a `slug` and `url`. Target-specific Crawl4AI options override values in `defaults`:

```yaml
targets:
  - slug: simple_page
    url: https://example.com/

  - slug: dynamic_page
    url: https://example.com/dynamic
    wait_for: "css:.loaded"
    js_code: |
      window.scrollTo(0, document.body.scrollHeight);

  - slug: authenticated_page
    url: https://example.com/dashboard
    session_id: example_session
    wait_for: "css:.dashboard"
```

The special target fields are:

| Field | Purpose |
| --- | --- |
| `slug` | Stable filename and identifier for the target. Required. |
| `url` | Page to crawl. Required. |
| `wait_for` | Wait condition such as `css:.product` or a Crawl4AI JavaScript condition. |
| `js_code` | JavaScript to execute during the crawl, useful for scrolling, clicking, or login flows. |
| `session_id` | Reuses a Crawl4AI browser session for session-aware requests. |
| `anti_block` | Partial per-target override of the global anti-block settings. |
| Any other field | Passed as a target-level `CrawlerRunConfig` option. |

For a login flow, use a session ID and JavaScript appropriate to the target's form:

```yaml
- slug: requires_login
  url: https://example.com/dashboard
  session_id: example_session
  wait_for: "css:.dashboard"
  js_code: |
    document.querySelector('#username').value = 'username';
    document.querySelector('#password').value = 'password';
    document.querySelector('form').submit();
```

Do not place real credentials in a committed YAML file. Prefer environment variables or an approved secret-management mechanism when a site requires authentication.

### Summary settings

The `summary` mapping controls the second stage:

```yaml
summary:
  input_dir: outputs/raw
  output_dir: outputs/processed
  max_input_chars: 20000
  llm:
    base_url: https://opencode.ai/zen/go/v1
    model: deepseek-v4-flash
    api_key_env: OPENAI_API_KEY
    temperature: 0.3
    max_tokens: 4096
  system_prompt: |
    Summarize the following web page content into a single concise paragraph.
  user_prompt_template: |
    {content}
```

The endpoint must expose an OpenAI-compatible chat-completions API. The sample `default.yaml` uses the OpenCode Zen endpoint and `deepseek-v4-flash`; replace `base_url`, `model`, and `api_key_env` for another provider. The API key is read from the environment variable named by `api_key_env`.

`{content}` in `user_prompt_template` is replaced with the raw Markdown. If the input exceeds `max_input_chars`, it is truncated before being sent and a truncation marker is appended.

The `radar-aftermarket-pipeline` config uses a specialized system prompt
that asks the LLM for `EXECUTIVE SUMMARY` + `COMPETITOR ANALYSIS` sections
in English.

### Dedup settings

The optional `dedup` mapping enables the local dedup/filteration stage between crawl and summarize. All fields are optional; defaults are shown:

```yaml
dedup:
  db_path: outputs/dedup.db             # local SQLite file (created if missing)
  input_dir: outputs/raw                # where to read raw .md files from
  title_window_hours: 72                # layer 1 lookback
  jaccard_window_hours: 24              # layer 2 lookback
  jaccard_threshold: 0.4                # layer 2 Jaccard cutoff
  embedding_window_hours: 72            # layer 3/4 lookback
  embedding_threshold: 0.85             # layer 3 cosine cutoff (1 - sqlite-vec distance)
  embedding_ambiguity_low: 0.75         # below this, item is treated as new
  embedding_ambiguity_high: 0.85        # upper edge of the LLM-judge band
  embedding_top_k: 10                   # KNN candidate count for layer 3
  embedding:
    provider: gemini
    model: models/gemini-embedding-001
    dim: 768                             # must match the model
    api_key_env: GEMINI_API_KEY
    task_type: RETRIEVAL_DOCUMENT
  llm:                                  # layer 4 judge (OpenAI-compatible)
    base_url: https://opencode.ai/zen/go/v1
    model: deepseek-v4-flash
    api_key_env: OPENAI_API_KEY
    temperature: 0.0
    max_tokens: 16
  judge_system_prompt: |
    You are a deduplication judge. ...
```

The `dedup.embedding` block configures the Gemini embedding model (`google-generativeai` SDK). The default model is `models/gemini-embedding-001` at 768 dimensions. The `dedup.llm` block has the same shape as `summary.llm` and configures the OpenAI-compatible endpoint used for the layer 4 judge.

To install the `sqlite-vec` extension at runtime the project depends on `pysqlite3-binary` (a SQLite build with extension loading enabled) and the `sqlite-vec` Python package, which bundles the loadable `vec0` extension.

## Output Format

For a successful target such as `books_toscrape`, the crawl stage creates:

```text
outputs/raw/books_toscrape.md
```

The file begins with provenance metadata:

```markdown
# Page title

URL: https://example.com/page

...extracted Markdown...
```

The summary stage creates a matching file:

```text
outputs/processed/books_toscrape.md
```

Its header preserves the source identity:

```markdown
# Summary: Page title
URL: https://example.com/page

...LLM-generated summary...
```

Raw files are intentionally retained. They allow summaries to be regenerated with a different model or prompt without paying the cost of crawling the source again.

For `radar-aftermarket-pipeline`, raw articles use a YAML front-matter
block (id_hash, source, cat, tag, line, event_type, alert_level, date,
image_url, url) followed by the same `# title\n\nURL: ...\n\n<markdown>`
body so the dedup and summary parsers still work unchanged.

## Customizing the Objective

The pipeline is intentionally organized around artifacts rather than a single opaque command. This makes it possible to adapt the project to different content workflows:

- Use `--crawl` to build a local content corpus.
- Use `--summarize` to regenerate summaries after changing the model or prompts.
- Adjust `system_prompt` to produce research notes, executive briefs, metadata, or another text format.
- Add a future classification or categorization stage that reads `outputs/raw` or `outputs/processed`.
- Add Crawl4AI run options such as CSS filtering or excluded tags to improve content quality for a target.

## Troubleshooting

### No browser or browser startup errors

Install the project dependencies and run the Crawl4AI browser setup:

```bash
uv sync
uv run crawl4ai-setup
```

### A target returns an empty or very short page

Try a target-specific wait condition, JavaScript action, longer timeout, or CSS selector:

```yaml
- slug: slow_dynamic_target
  url: https://example.com/heavy
  page_timeout: 120000
  wait_for: "css:.loaded-content"
  css_selector: ".main-content"
```

If the page is being blocked, review the anti-block log, increase the retry delay, provide an appropriate user-agent pool, or use an authorized proxy. Do not assume that increasing retries is always the correct solution.

### Missing API key

The summarizer stops before making requests when the configured environment variable is absent:

```bash
export OPENAI_API_KEY="your-api-key"
uv run ai-crawling-pipeline --summarize
```

Make sure the selected configuration's `summary.llm.api_key_env` matches the variable you set.

### Summarization has no input files

Run the crawl stage first or point `summary.input_dir` at a directory containing Markdown files:

```bash
uv run ai-crawling-pipeline --crawl
uv run ai-crawling-pipeline --summarize
```

## Development

Install development dependencies with:

```bash
uv sync --dev
```

Run the test suite when tests are present:

```bash
uv run pytest
```

The package requires Python `3.12+` and exposes the `ai-crawling-pipeline` console entry point defined in `pyproject.toml`.

## Author

Created by Ezzaldin Mamdouh.

No license file is currently included in the repository. Add and document a license before distributing the project publicly.
