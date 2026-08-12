#!/usr/bin/env python3
"""
Scrape the last week of posts from one or more companies' public LinkedIn pages.

Uses Crawl4AI (see .claude/skills/crawl4ai/SKILL.md) against the logged-out
"About" page, e.g. https://www.linkedin.com/company/bosch/ — no login, no
credentials, no anti-bot evasion.

Why this URL and not /company/<slug>/posts/: LinkedIn authwalls the dedicated
/posts/ feed for logged-out visitors, but the public About page embeds an
"Updates" section with the company's most recent public posts (SEO content),
which is what this script parses.

Caveats (inherent to the logged-out page, not this script):
  - LinkedIn only exposes *relative* timestamps here ("3d", "1w", "2mo"), not
    exact dates, so the "last week" filter is an estimate, not exact.
  - Only the handful of posts LinkedIn renders on this page are available
    (no infinite scroll/pagination without login) — usually 5-10 posts.
  - Automated access to LinkedIn is against its User Agreement. Keep request
    volume low and don't run this on a schedule against accounts/pages you
    don't have permission to monitor.

After the date filter, a semantic layer classifies each remaining post with
an LLM (via crawl4ai's own LLM plumbing: LLMConfig + aperform_completion_with_backoff)
to keep only posts about aftermarket/parts/promotions/new-part-releases/selling
— catching posts that imply the topic without using the literal keywords.
Requires an API key in the environment (default: GEMINI_API_KEY, Google's
free-tier model; override with --llm-provider/--llm-api-key-env for
Anthropic, OpenAI, etc.).

For multiple companies (--companies / --companies-file, or the default list
below), each gets its own fresh browser instance — reusing one browser across
sequential companies was observed to trip LinkedIn's bot detection (HTTP 999)
on the second request. Results are written to output/<slug>.json (one file
per company, --output-dir to change the folder), and a delay is inserted
between page fetches (--delay-between-companies) so the run doesn't hit
LinkedIn as a burst of rapid requests.

If a company comes back blocked (HTTP 429/999, authwall, etc.), it's retried
after a cooldown (--block-retries/--block-retry-delay); if still blocked, that
company is skipped for this run (marked "blocked" in the summary, nothing
recorded as seen, so it's retried in full next run) and the *rest* of the
batch backs off harder (--block-cooldown) before continuing, since a block is
usually IP-wide rather than specific to one company page.

Meant to be run daily (cron, etc.): a per-company dedup ledger under
state/<slug>.json (--state-dir to change it) records every post URL already
processed, so each run only classifies posts new since the last run — posts
already known to be topic-irrelevant aren't re-sent to the LLM. output/<slug>.json
is a running log accumulated across runs (merged by URL, newest first), not
overwritten each time. Pass --no-dedup to disable the ledger (e.g. for a
one-off/backfill run).

Each post's photo URL(s) ("images") and video(s) ("videos", each a
{poster, src} pair — src is a direct .mp4 URL) are fetched by default (pass
--no-images to skip this and roughly halve the number of LinkedIn requests
per run). This costs a second full page fetch per company: LinkedIn doesn't
expose this media in the markdown/text rendering or its own structured SEO
data — checked, both omit it — photos only appear as <img> tags and videos
as <video poster= data-sources=...> tags after the page is scrolled, and
that same scroll was found to make the reaction-count widget disappear from
the page. So media is fetched from a separate page load with its own
full-page scroll, matched back to posts by DOM position (whatever's between
one post's link and the next); the primary fetch used for
text/reactions/comments/dates is never scrolled and stays unaffected. A
failed/blocked media fetch just means that run's posts have no images/videos
— not a reason to fail the company.
"""

import argparse
import asyncio
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
from crawl4ai.utils import aperform_completion_with_backoff

load_dotenv()

# Automotive aftermarket / parts companies, verified against LinkedIn before
# inclusion (plain `nakata` and `controil` resolve to unrelated small
# companies, not the intended brands — use these confirmed slugs instead).
# ZF Aftermarket and Niterra/NGK have no distinct public LinkedIn page under
# any guessed slug, so they're omitted rather than guessed; Controil, Lonaflex
# and DRiV fall back to their parent companies (Randon/Fras-le, Tenneco),
# already covered below.
DEFAULT_COMPANIES = [
    "valeo",
    "bosch",  # Bosch (Mobility Aftermarket)
    "zf-group",  # ZF (Sachs, TRW, Lemförder, Wabco) — main page, no dedicated "ZF Aftermarket" page found
    "mahle",  # MAHLE Aftermarket
    "magneti-marelli-parts-and-services",  # Magneti Marelli (now Marelli)
    "schaeffler",  # Schaeffler (LuK, INA, FAG)
    "tenneco",  # Tenneco (Monroe, DRiV)
    "continental",  # Continental (ATE, VDO) — main page, no dedicated aftermarket page found
    "densopartseu",  # Denso — main page, no dedicated "Denso Aftermarket" page found
    "skf",  # SKF — main page, no dedicated "SKF Automotive" page found
    "nakata-uk",  # Nakata
    "frasleuk",  # Fras-le
    "randon",  # Randoncorp (Controil, Lonaflex)
]

DEFAULT_TOPICS = [
    "aftermarket parts",
    "spare/replacement parts",
    "new part or product releases",
    "promotions or discounts",
    "selling or purchasing parts",
]

CLASSIFY_PROMPT = """You are screening a company LinkedIn post for relevance.

Topics of interest: {topics}

Does this post relate to ANY of those topics — even if it doesn't use those \
exact words (e.g. a post about a reconditioned/replacement component counts \
as "aftermarket parts")? Judge the substance, not just keywords.

Post:
\"\"\"
{text}
\"\"\"

Respond with ONLY a JSON object, no markdown fences, no extra text:
{{"relevant": true or false, "reason": "one short sentence"}}"""

POST_URL_RE = re.compile(r'\n\s*\*\s*\[ \]\(https://www\.linkedin\.com/posts/')
TIME_RE = re.compile(r'^\s*(\d+)\s*([a-zA-Z]+)\s*(Edited)?\s*$', re.MULTILINE)
REACT_RE = re.compile(r'\[\s*([\d,]+)\s*\]\([^)]*social-actions-reactions\)')
COMMENT_RE = re.compile(r'\[\s*([\d,]+)\s*Comments?\s*\]\([^)]*social-actions-comments\)')
# Post photos use two different licdn.com URL patterns depending on the post
# type — single-image posts: feedshare-shrink_*/feedshare-image-high-res;
# multi-image carousel posts: image-shrink_* (confirmed: a 5-photo carousel
# post used image-shrink_800 for every photo, feedshare nowhere) — both are
# distinct from avatar/logo/cover images (company-logo_*,
# profile-displayphoto-*, image-scale_* for the cover photo).
POST_IMAGE_RE = re.compile(r'<img[^>]+src="(https://media\.licdn\.com/dms/image/[^"]*(?:feedshare|image-shrink)[^"]*)"')
# Video posts render as <video poster="..." data-sources="[...mp4 urls...]">
# rather than <img>, so they need a separate pattern entirely.
POST_VIDEO_RE = re.compile(r'<video\b[^>]*>')

# Approximate day-equivalents for LinkedIn's relative-time units. Minutes
# ("21m") were missing entirely, which silently dropped every post under an
# hour old — parse_relative_age_days returned None for them, and the date
# filter excludes anything with an unparseable/None age.
UNIT_DAYS = {
    "s": 1 / 86400,
    "sec": 1 / 86400,
    "m": 1 / 1440,
    "min": 1 / 1440,
    "mins": 1 / 1440,
    "h": 1 / 24,
    "hr": 1 / 24,
    "d": 1,
    "w": 7,
    "mo": 30,
    "y": 365,
}


def parse_relative_age_days(rel_time: str) -> float | None:
    m = re.match(r"(\d+)\s*([a-zA-Z]+)", rel_time)
    if not m:
        return None
    count, unit = int(m.group(1)), m.group(2).lower()
    per_unit = UNIT_DAYS.get(unit)
    if per_unit is None:
        return None
    return count * per_unit


def extract_updates_section(markdown: str) -> str:
    idx = markdown.lower().find("\n##  updates")
    if idx == -1:
        idx = markdown.lower().find("updates")
    if idx == -1:
        return ""
    next_heading = re.search(r"\n##[^#]", markdown[idx + 10 :])
    end = idx + 10 + next_heading.start() if next_heading else len(markdown)
    return markdown[idx:end]


def extract_posts(markdown: str, fetched_at: datetime) -> list[dict]:
    section = extract_updates_section(markdown)
    if not section:
        return []

    chunks = POST_URL_RE.split(section)[1:]
    posts = []
    for chunk in chunks:
        slug, _, rest = chunk.partition(")")
        url = "https://www.linkedin.com/posts/" + slug

        time_match = TIME_RE.search(chunk)
        rel_time = f"{time_match.group(1)}{time_match.group(2)}" if time_match else None
        edited = bool(time_match.group(3)) if time_match else False

        text_start = chunk.find("Report this post")
        text_start = chunk.find("\n", text_start) + 1 if text_start != -1 else 0
        end_candidates = [x for x in (chunk.find("…more", text_start), chunk.find("`` ``", text_start)) if x != -1]
        text_end = min(end_candidates) if end_candidates else len(chunk)
        text = re.sub(r"\n\s*\*\s*$", "", chunk[text_start:text_end]).strip()

        reactions_match = REACT_RE.search(chunk)
        comments_match = COMMENT_RE.search(chunk)

        age_days = parse_relative_age_days(rel_time) if rel_time else None
        estimated_date = (fetched_at - timedelta(days=age_days)) if age_days is not None else None

        posts.append(
            {
                "url": url,
                "text": text,
                "relative_time": rel_time,
                "edited": edited,
                "estimated_age_days": age_days,
                "estimated_date": estimated_date.date().isoformat() if estimated_date else None,
                "reactions": int((reactions_match.group(1) if reactions_match else "0").replace(",", "")),
                "comments": int((comments_match.group(1) if comments_match else "0").replace(",", "")),
            }
        )
    return posts


def extract_video_sources(video_tag: str) -> dict | None:
    poster_match = re.search(r'poster="([^"]*)"', video_tag)
    sources_match = re.search(r'data-sources="([^"]*)"', video_tag)
    if not sources_match:
        return None
    try:
        sources = json.loads(html.unescape(sources_match.group(1)))
    except json.JSONDecodeError:
        return None
    if not sources:
        return None
    best = max(sources, key=lambda s: s.get("data-bitrate", 0))
    return {
        "poster": html.unescape(poster_match.group(1)) if poster_match else None,
        "src": best.get("src"),
    }


def extract_post_media(page_html: str, post_urls: list[str]) -> dict[str, dict]:
    # Match each post to the photo(s)/video(s) between its link and the next
    # post's link in the raw HTML (DOM order == feed order). Must search for
    # href="<url>" specifically, not just the URL string — the page also
    # embeds every post URL in a JSON-LD <script> block as a plain string,
    # which would give a false/earlier position if matched loosely.
    positions = []
    for url in post_urls:
        m = re.search(re.escape(f'href="{url}"'), page_html)
        if m:
            positions.append((m.start(), url))
    positions.sort()

    img_positions = [(m.start(), html.unescape(m.group(1))) for m in POST_IMAGE_RE.finditer(page_html)]
    video_positions = []
    for m in POST_VIDEO_RE.finditer(page_html):
        video = extract_video_sources(m.group(0))
        if video:
            video_positions.append((m.start(), video))

    media_by_url: dict[str, dict] = {url: {"images": [], "videos": []} for url in post_urls}
    for i, (pos, url) in enumerate(positions):
        next_pos = positions[i + 1][0] if i + 1 < len(positions) else len(page_html)
        media_by_url[url] = {
            "images": [u for p, u in img_positions if pos <= p < next_pos],
            "videos": [v for p, v in video_positions if pos <= p < next_pos],
        }
    return media_by_url


def extract_json_object(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def classify_post(post: dict, llm_config: LLMConfig, topics: list[str], semaphore: asyncio.Semaphore) -> dict:
    prompt = CLASSIFY_PROMPT.format(topics=", ".join(topics), text=post["text"])
    async with semaphore:
        try:
            response = await aperform_completion_with_backoff(
                provider=llm_config.provider,
                prompt_with_variables=prompt,
                api_token=llm_config.api_token,
                base_url=llm_config.base_url,
            )
        except Exception as e:
            print(f"  LLM classification failed for {post['url']}: {e}", file=sys.stderr)
            return {**post, "topic_relevant": None, "topic_reason": f"classification error: {e}"}

    content = response.choices[0].message.content
    parsed = extract_json_object(content) or {}
    return {
        **post,
        "topic_relevant": parsed.get("relevant"),
        "topic_reason": parsed.get("reason", "").strip(),
    }


async def filter_by_topic(posts: list[dict], llm_config: LLMConfig, topics: list[str]) -> list[dict]:
    semaphore = asyncio.Semaphore(5)
    classified = await asyncio.gather(*(classify_post(p, llm_config, topics, semaphore) for p in posts))
    return list(classified)


def load_seen(state_path: Path) -> dict[str, str]:
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text())


def save_seen(state_path: Path, seen: dict[str, str]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(seen, indent=2, sort_keys=True))


def company_page_url(company_slug: str) -> str:
    # Some brands (e.g. MAHLE Aftermarket) are LinkedIn "Showcase" pages, not
    # top-level Company pages, and live at /showcase/<slug>/ instead of
    # /company/<slug>/. Prefix a slug with "showcase/" to target one.
    path = company_slug if company_slug.startswith("showcase/") else f"company/{company_slug}"
    return f"https://www.linkedin.com/{path}/"


async def fetch_company_page(company_slug: str):
    # A fresh browser (not a shared/reused one) per company: reusing one
    # browser session across sequential company fetches was observed to trip
    # LinkedIn's bot detection (HTTP 999) on the second request even with a
    # multi-second delay between calls, whereas a clean browser per company
    # did not.
    browser_config = BrowserConfig(headless=True)
    run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=30000)
    async with AsyncWebCrawler(config=browser_config) as crawler:
        return await crawler.arun(url=company_page_url(company_slug), config=run_config)


async def fetch_page_images(company_slug: str) -> str | None:
    # Post photos are lazy-loaded — absent from the HTML entirely without
    # scrolling (confirmed: only the cover photo shows up otherwise) — but a
    # full-page scroll was found to also make the reaction-count widget
    # disappear from the page (LinkedIn swaps in different content once
    # you've scrolled that far). So this runs as a deliberately separate,
    # best-effort fetch: the primary fetch (fetch_company_page) stays
    # untouched and always has correct reactions/comments, and this one is
    # only ever used for its raw HTML <img> tags, matched back to posts by
    # URL via extract_post_images. A failure/block here just means posts
    # come back with no images this run — not a reason to fail the company.
    browser_config = BrowserConfig(headless=True)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS, page_timeout=30000, scan_full_page=True, wait_for_images=True
    )
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=company_page_url(company_slug), config=run_config)
    if is_blocked_result(result):
        return None
    return result.html


def is_blocked_result(result) -> bool:
    if not result.success:
        return True
    markdown = str(result.markdown)
    return (
        result.status_code == 999
        or result.status_code == 429
        or "authwall" in result.url
        or "/login" in result.url
        or "linkedin.com/authwall" in markdown
    )


async def fetch_company_page_with_retry(company_slug: str, retries: int, retry_delay: float):
    result = None
    for attempt in range(retries + 1):
        result = await fetch_company_page(company_slug)
        if not is_blocked_result(result):
            return result, False
        if attempt < retries:
            print(
                f"  Blocked (attempt {attempt + 1}/{retries + 1}) — "
                f"waiting {retry_delay:.0f}s before retrying...",
                file=sys.stderr,
            )
            await asyncio.sleep(retry_delay)
    return result, True


async def run(
    company_slug: str,
    days: int,
    topics: list[str] | None,
    llm_config: LLMConfig | None,
    state_path: Path | None = None,
    block_retries: int = 1,
    block_retry_delay: float = 30.0,
    include_images: bool = True,
) -> list[dict] | None:
    result, blocked = await fetch_company_page_with_retry(company_slug, block_retries, block_retry_delay)
    fetched_at = datetime.now(timezone.utc)

    if blocked:
        reason = result.error_message if result and not result.success else f"status {result.status_code if result else '?'}"
        print(
            f"Still blocked after {block_retries + 1} attempt(s) — {reason}. "
            "Nothing marked as seen; this company will be retried in full next run.",
            file=sys.stderr,
        )
        return None

    markdown = str(result.markdown)
    posts = extract_posts(markdown, fetched_at)
    if include_images and posts:
        images_html = await fetch_page_images(company_slug)
        if images_html is not None:
            media_by_url = extract_post_media(images_html, [p["url"] for p in posts])
            for p in posts:
                media = media_by_url.get(p["url"], {"images": [], "videos": []})
                p["images"] = media["images"]
                p["videos"] = media["videos"]
        else:
            print("  Image/video fetch was blocked/failed — posts will have no media this run.", file=sys.stderr)
            for p in posts:
                p["images"] = []
                p["videos"] = []
    if not posts:
        print(
            "No posts parsed. LinkedIn's page markup may have changed, or this "
            "company page doesn't expose a public 'Updates' section.",
            file=sys.stderr,
        )
        return []

    posts = [p for p in posts if p["estimated_age_days"] is not None and p["estimated_age_days"] <= days]
    if not posts:
        return posts

    seen: dict[str, str] = {}
    if state_path is not None:
        seen = load_seen(state_path)
        new_posts = [p for p in posts if p["url"] not in seen]
        already_seen = len(posts) - len(new_posts)
        if already_seen:
            print(f"Skipping {already_seen} post(s) already seen in a previous run.", file=sys.stderr)
        posts = new_posts

    if not posts:
        return posts

    if topics:
        classified = await filter_by_topic(posts, llm_config, topics)
        errored = [p for p in classified if p["topic_relevant"] is None]
        dropped = [p for p in classified if p["topic_relevant"] is False]
        if dropped:
            print(f"Dropped {len(dropped)}/{len(classified)} post(s) as not topic-relevant.", file=sys.stderr)
        if errored:
            print(
                f"{len(errored)} post(s) failed classification and will be retried next run "
                "(not marked as seen).",
                file=sys.stderr,
            )
        result_posts = [p for p in classified if p["topic_relevant"] is True]
        # Only posts with a definite true/false verdict are "processed" — a
        # classification error (e.g. no API credit) must not be recorded as
        # seen, or that post would be silently skipped forever.
        to_mark_seen = [p for p in classified if p["topic_relevant"] is not None]
    else:
        result_posts = posts
        to_mark_seen = posts

    if state_path is not None:
        today = fetched_at.date().isoformat()
        for p in to_mark_seen:
            seen[p["url"]] = today
        save_seen(state_path, seen)

    return result_posts


def slugify_filename(company_slug: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", company_slug).strip("_") or "company"


def load_existing_output(out_path: Path) -> list[dict]:
    if not out_path.exists():
        return []
    try:
        return json.loads(out_path.read_text())
    except json.JSONDecodeError:
        return []


def merge_posts(existing: list[dict], new: list[dict]) -> list[dict]:
    # output/<slug>.json is a running log across daily runs, not a snapshot of
    # a single run — overwriting it with just that run's delta destroyed
    # everything found on previous days. Merge by URL instead (new data wins,
    # e.g. refreshed reaction/comment counts), newest first.
    by_url = {p["url"]: p for p in existing}
    for p in new:
        by_url[p["url"]] = p
    return sorted(by_url.values(), key=lambda p: p.get("estimated_date") or "", reverse=True)


async def run_companies(
    company_slugs: list[str],
    days: int,
    topics: list[str] | None,
    llm_config: LLMConfig | None,
    output_dir: Path,
    delay_between_companies: float,
    state_dir: Path | None,
    block_retries: int = 1,
    block_retry_delay: float = 30.0,
    block_cooldown: float = 60.0,
    include_images: bool = True,
) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, int] = {}
    was_blocked = False

    for i, company_slug in enumerate(company_slugs):
        print(f"\n=== {company_slug} ===")
        state_path = state_dir / f"{slugify_filename(company_slug)}.json" if state_dir is not None else None
        was_blocked = False
        try:
            posts = await run(company_slug, days, topics, llm_config, state_path, block_retries, block_retry_delay, include_images)
            if posts is None:
                was_blocked = True
                summary[company_slug] = -2
        except Exception as e:
            print(f"  Failed: {e}", file=sys.stderr)
            summary[company_slug] = -1
            posts = None

        if posts is not None:
            for p in posts:
                print(f"[{p['relative_time']}] (~{p['estimated_date']}) {p['reactions']} reactions, {p['comments']} comments")
                if "topic_reason" in p:
                    print(f"  relevant because: {p['topic_reason']}")
                print(f"  {p['text'][:200]}{'…' if len(p['text']) > 200 else ''}")
                if p.get("images"):
                    print(f"  {len(p['images'])} image(s): {p['images'][0]}{' ...' if len(p['images']) > 1 else ''}")
                if p.get("videos"):
                    print(f"  {len(p['videos'])} video(s): {p['videos'][0]['src']}")
                print(f"  {p['url']}")

            out_path = output_dir / f"{slugify_filename(company_slug)}.json"
            merged = merge_posts(load_existing_output(out_path), posts)
            with open(out_path, "w") as f:
                json.dump(merged, f, indent=2)
            print(f"  {len(posts)} new post(s) ({len(merged)} total) in {out_path}")
            summary[company_slug] = len(posts)

        if i < len(company_slugs) - 1:
            wait = block_cooldown if was_blocked else delay_between_companies
            if was_blocked:
                print(f"  Backing off {wait:.0f}s before the next company (block signal seen).", file=sys.stderr)
            if wait > 0:
                await asyncio.sleep(wait)

    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", help="Single LinkedIn company slug; overrides the default company list")
    parser.add_argument("--companies", help="Comma-separated LinkedIn company slugs, e.g. bosch-aftermarket,zf-group,schaeffler")
    parser.add_argument(
        "--companies-file",
        help="Path to a text file with one company slug per line (# comments allowed); overrides the default company list",
    )
    parser.add_argument("--days", type=int, default=7, help="Only keep posts estimated within this many days (default: 7)")
    parser.add_argument("--output-dir", default="output", help="Folder to write one JSON file per company into (default: output); each run's file holds only posts new since the last run")
    parser.add_argument("--delay-between-companies", type=float, default=8.0, help="Seconds to wait between company page fetches (default: 8.0)")
    parser.add_argument("--state-dir", default="state", help="Folder holding per-company dedup state, i.e. which post URLs have already been seen (default: state)")
    parser.add_argument("--no-dedup", action="store_true", help="Disable dedup: re-fetch and re-output every matching post every run, ignoring/not updating state")
    parser.add_argument("--block-retries", type=int, default=1, help="Extra attempts for a company blocked by LinkedIn's anti-bot protection (default: 1)")
    parser.add_argument("--block-retry-delay", type=float, default=90.0, help="Seconds to wait between retry attempts for a blocked company (default: 90.0)")
    parser.add_argument("--block-cooldown", type=float, default=60.0, help="Extra seconds to wait before the next company after a block was seen, on top of the normal delay (default: 60.0)")
    parser.add_argument("--no-images", action="store_true", help="Skip fetching post photo URLs (saves one extra page fetch per company)")
    parser.add_argument(
        "--topics",
        default=",".join(DEFAULT_TOPICS),
        help=f"Comma-separated topics for the semantic filter (default: {', '.join(DEFAULT_TOPICS)})",
    )
    parser.add_argument("--no-topic-filter", action="store_true", help="Skip the LLM semantic filter, keep only the date filter")
    parser.add_argument(
        "--llm-provider",
        default="gemini/gemini-2.0-flash",
        help="litellm-style provider/model string for the semantic filter (default: gemini/gemini-2.0-flash, "
        "Google's free-tier model; get a key with no credit card at aistudio.google.com/app/apikey)",
    )
    parser.add_argument(
        "--llm-api-key-env",
        default="GEMINI_API_KEY",
        help="Env var holding the API key for --llm-provider (default: GEMINI_API_KEY)",
    )
    args = parser.parse_args()

    if args.companies_file:
        lines = Path(args.companies_file).read_text().splitlines()
        company_slugs = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    elif args.companies:
        company_slugs = [c.strip() for c in args.companies.split(",") if c.strip()]
    elif args.company:
        company_slugs = [args.company]
    else:
        company_slugs = DEFAULT_COMPANIES

    if not company_slugs:
        print("No company slugs given.", file=sys.stderr)
        sys.exit(1)

    topics = None if args.no_topic_filter else [t.strip() for t in args.topics.split(",") if t.strip()]
    llm_config = None
    if topics:
        api_key = os.getenv(args.llm_api_key_env)
        if not api_key:
            print(
                f"--llm-api-key-env points at {args.llm_api_key_env}, which is not set. "
                "Set it, or pass --no-topic-filter to skip the semantic layer.",
                file=sys.stderr,
            )
            sys.exit(1)
        llm_config = LLMConfig(provider=args.llm_provider, api_token=api_key)

    summary = asyncio.run(
        run_companies(
            company_slugs,
            args.days,
            topics,
            llm_config,
            Path(args.output_dir),
            args.delay_between_companies,
            None if args.no_dedup else Path(args.state_dir),
            args.block_retries,
            args.block_retry_delay,
            args.block_cooldown,
            not args.no_images,
        )
    )

    print("\n=== Summary ===")
    for company_slug, count in summary.items():
        if count == -2:
            status = "blocked by LinkedIn (will retry next run)"
        elif count < 0:
            status = "failed"
        else:
            status = f"{count} post(s)"
        print(f"  {company_slug}: {status}")


if __name__ == "__main__":
    main()
