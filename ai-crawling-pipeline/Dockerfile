# RADAR scraper — built for ECS Fargate, not Lambda: a run takes ~12-15
# minutes (will grow once it covers more companies/sources), well past
# Lambda's 15-minute hard cap with no headroom, and it needs a real browser
# (Crawl4AI/Playwright) which Lambda's runtime doesn't provide out of the box.
#
# Base image ships Python + Playwright's browsers + all OS-level deps
# (fonts, libnss3, libatk, ...) already installed — pinned to the same
# Playwright version as requirements.txt so the browsers Crawl4AI drives
# match what's actually on disk. Building this by hand on python:3.12-slim
# means manually apt-get installing ~40 Chromium runtime packages; the
# playwright/python image is Microsoft's own maintained answer to that.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

WORKDIR /app

# Dependencies first (own layer, cached across source-only rebuilds).
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# crawl4ai's own post-install step — downloads the NLP models it uses for
# content extraction. Playwright's browsers are already present from the
# base image; this is a no-op for those, but running it keeps parity with
# the manual `crawl4ai-setup` step documented in README.md's Installation
# section, so the container isn't a special case to remember separately.
RUN crawl4ai-setup

# Now the project itself. README.md is copied too because pyproject.toml's
# uv_build backend reads it as the package's `readme` metadata field at
# install time — omitting it fails the build, not just the packaging.
COPY pyproject.toml ./pyproject.toml
COPY README.md ./README.md
COPY src ./src
COPY configs ./configs

RUN pip install --no-cache-dir --no-deps .

ENV PYTHONUNBUFFERED=1

# No default args: bare `radar-pipeline` runs the full collect -> fetch ->
# dedup -> summarize sequence, which is what the every-3-hours schedule
# (Phase 4) wants. The ECS task definition/EventBridge target can still
# override the container command for one-off `--backfill` or `--status` runs.
ENTRYPOINT ["radar-pipeline"]
