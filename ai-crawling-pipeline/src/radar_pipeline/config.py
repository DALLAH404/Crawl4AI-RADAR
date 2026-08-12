"""Configuration models for the Radar Aftermarket Pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LLMSettings:
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    api_key_env: str = "OPENAI_API_KEY"
    temperature: float = 0.3
    max_tokens: int = 2048


@dataclass
class GeminiEmbeddingSettings:
    model: str = "models/gemini-embedding-001"
    dim: int = 768
    api_key_env: str = "GEMINI_API_KEY"
    task_type: str = "RETRIEVAL_DOCUMENT"


@dataclass
class DatabaseSettings:
    path: Path = field(default_factory=lambda: Path("outputs/radar/radar.db"))
    embedding_dim: int = 768
    wal_mode: bool = True
    foreign_keys: bool = True


@dataclass
class LinkedInSettings:
    enabled: bool = True
    days: int = 7
    concurrency: int = 1
    delay_seconds: float = 8.0
    jitter_seconds: float = 2.0
    block_retries: int = 1
    block_retry_delay: float = 90.0
    block_cooldown_seconds: float = 60.0
    page_timeout_ms: int = 30000
    include_media: bool = True
    max_posts_per_company: int = 25
    headless: bool = True


@dataclass
class CollectSettings:
    concurrency: int = 8
    max_items_per_feed: int = 25
    backfill_max_items: int = 100
    days_back: int = 30
    backfill_days: int = 90
    # Politeness & resilience for the collect stage. See
    # radar_pipeline.sources.ratelimit.RateLimiter for semantics.
    gnews_concurrency: int = 2
    request_delay_seconds: float = 0.25
    request_jitter_seconds: float = 0.25
    block_cooldown_seconds: float = 60.0
    fetch_max_retries: int = 3
    fetch_backoff_seconds: float = 2.0
    linkedin: LinkedInSettings = field(default_factory=LinkedInSettings)


@dataclass
class FetchSettings:
    output_dir: Path = field(default_factory=lambda: Path("outputs/radar/raw"))
    browser: dict[str, Any] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    anti_block: dict[str, Any] | None = None


@dataclass
class DedupSettings:
    title_window_hours: int = 72
    jaccard_window_hours: int = 24
    jaccard_threshold: float = 0.4
    embedding_window_hours: int = 72
    embedding_threshold: float = 0.85
    ambiguity_low: float = 0.75
    ambiguity_high: float = 0.85
    top_k: int = 10
    use_hybrid_fts5: bool = True
    gemini_embedding: GeminiEmbeddingSettings = field(default_factory=GeminiEmbeddingSettings)
    judge_llm: LLMSettings = field(default_factory=LLMSettings)
    judge_system_prompt: str = (
        "You are a deduplication judge. Given two page titles and short content "
        "excerpts, decide if they describe the same specific event, different "
        "events, or are merely related. Respond with exactly one of: same, "
        "different, related."
    )


from radar_pipeline.summarize.prompts import DEFAULT_SYSTEM_PROMPT as DEFAULT_SUMMARIZE_SYSTEM_PROMPT


@dataclass
class SummarizeSettings:
    output_dir: Path = field(default_factory=lambda: Path("outputs/radar/processed"))
    concurrency: int = 8
    max_input_chars: int = 20000
    llm: LLMSettings = field(default_factory=LLMSettings)
    system_prompt: str = DEFAULT_SUMMARIZE_SYSTEM_PROMPT


@dataclass
class SourcesSettings:
    yaml_path: Path = field(default_factory=lambda: Path("configs/radar_sources.yaml"))
    auto_seed: bool = True


@dataclass
class RadarConfig:
    db: DatabaseSettings = field(default_factory=DatabaseSettings)
    collect: CollectSettings = field(default_factory=CollectSettings)
    fetch: FetchSettings | None = None
    dedup: DedupSettings | None = None
    summarize: SummarizeSettings | None = None
    sources: SourcesSettings = field(default_factory=SourcesSettings)


def _parse_llm(raw: dict[str, Any] | None) -> LLMSettings:
    raw = raw or {}
    return LLMSettings(
        base_url=raw.get("base_url", LLMSettings.base_url),
        model=raw.get("model", LLMSettings.model),
        api_key_env=raw.get("api_key_env", LLMSettings.api_key_env),
        temperature=float(raw.get("temperature", LLMSettings.temperature)),
        max_tokens=int(raw.get("max_tokens", LLMSettings.max_tokens)),
    )


def _parse_linkedin(raw: dict[str, Any] | None) -> LinkedInSettings:
    raw = raw or {}
    return LinkedInSettings(
        enabled=bool(raw.get("enabled", LinkedInSettings.enabled)),
        days=int(raw.get("days", LinkedInSettings.days)),
        concurrency=int(raw.get("concurrency", LinkedInSettings.concurrency)),
        delay_seconds=float(raw.get("delay_seconds", LinkedInSettings.delay_seconds)),
        jitter_seconds=float(raw.get("jitter_seconds", LinkedInSettings.jitter_seconds)),
        block_retries=int(raw.get("block_retries", LinkedInSettings.block_retries)),
        block_retry_delay=float(raw.get("block_retry_delay", LinkedInSettings.block_retry_delay)),
        block_cooldown_seconds=float(raw.get("block_cooldown_seconds", LinkedInSettings.block_cooldown_seconds)),
        page_timeout_ms=int(raw.get("page_timeout_ms", LinkedInSettings.page_timeout_ms)),
        include_media=bool(raw.get("include_media", LinkedInSettings.include_media)),
        max_posts_per_company=int(raw.get("max_posts_per_company", LinkedInSettings.max_posts_per_company)),
        headless=bool(raw.get("headless", LinkedInSettings.headless)),
    )


def _parse_gemini(raw: dict[str, Any] | None) -> GeminiEmbeddingSettings:
    raw = raw or {}
    return GeminiEmbeddingSettings(
        model=raw.get("model", GeminiEmbeddingSettings.model),
        dim=int(raw.get("dim", GeminiEmbeddingSettings.dim)),
        api_key_env=raw.get("api_key_env", GeminiEmbeddingSettings.api_key_env),
        task_type=raw.get("task_type", GeminiEmbeddingSettings.task_type),
    )


def load_radar_config(yaml_path: str | Path) -> RadarConfig:
    import yaml

    p = Path(yaml_path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")

    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    db_r = data.get("db") or {}
    collect_r = data.get("collect") or {}
    fetch_r = data.get("fetch")
    dedup_r = data.get("dedup")
    summarize_r = data.get("summarize")
    sources_r = data.get("sources") or {}

    db = DatabaseSettings(
        path=Path(db_r.get("path", "outputs/radar/radar.db")),
        embedding_dim=int(db_r.get("embedding_dim", DatabaseSettings.embedding_dim)),
    )

    collect = CollectSettings(
        concurrency=int(collect_r.get("concurrency", CollectSettings.concurrency)),
        max_items_per_feed=int(collect_r.get("max_items_per_feed", CollectSettings.max_items_per_feed)),
        backfill_max_items=int(collect_r.get("backfill_max_items", CollectSettings.backfill_max_items)),
        days_back=int(collect_r.get("days_back", CollectSettings.days_back)),
        gnews_concurrency=int(collect_r.get("gnews_concurrency", CollectSettings.gnews_concurrency)),
        request_delay_seconds=float(collect_r.get("request_delay_seconds", CollectSettings.request_delay_seconds)),
        request_jitter_seconds=float(collect_r.get("request_jitter_seconds", CollectSettings.request_jitter_seconds)),
        block_cooldown_seconds=float(collect_r.get("block_cooldown_seconds", CollectSettings.block_cooldown_seconds)),
        fetch_max_retries=int(collect_r.get("fetch_max_retries", CollectSettings.fetch_max_retries)),
        fetch_backoff_seconds=float(collect_r.get("fetch_backoff_seconds", CollectSettings.fetch_backoff_seconds)),
        linkedin=_parse_linkedin(collect_r.get("linkedin")),
    )

    fetch = None
    if fetch_r is not None:
        fetch = FetchSettings(
            output_dir=Path(fetch_r.get("output_dir", "outputs/radar/raw")),
            browser=fetch_r.get("browser") or {},
            defaults=fetch_r.get("defaults") or {},
            anti_block=fetch_r.get("anti_block"),
        )

    dedup = None
    if dedup_r is not None:
        dedup = DedupSettings(
            title_window_hours=int(dedup_r.get("title_window_hours", DedupSettings.title_window_hours)),
            jaccard_window_hours=int(dedup_r.get("jaccard_window_hours", DedupSettings.jaccard_window_hours)),
            jaccard_threshold=float(dedup_r.get("jaccard_threshold", DedupSettings.jaccard_threshold)),
            embedding_window_hours=int(dedup_r.get("embedding_window_hours", DedupSettings.embedding_window_hours)),
            embedding_threshold=float(dedup_r.get("embedding_threshold", DedupSettings.embedding_threshold)),
            ambiguity_low=float(dedup_r.get("ambiguity_low", DedupSettings.ambiguity_low)),
            ambiguity_high=float(dedup_r.get("ambiguity_high", DedupSettings.ambiguity_high)),
            top_k=int(dedup_r.get("top_k", DedupSettings.top_k)),
            use_hybrid_fts5=bool(dedup_r.get("use_hybrid_fts5", DedupSettings.use_hybrid_fts5)),
            gemini_embedding=_parse_gemini(dedup_r.get("gemini_embedding")),
            judge_llm=_parse_llm(dedup_r.get("judge_llm")),
            judge_system_prompt=dedup_r.get("judge_system_prompt", DedupSettings.judge_system_prompt),
        )

    summarize = None
    if summarize_r is not None:
        summarize = SummarizeSettings(
            output_dir=Path(summarize_r.get("output_dir", "outputs/radar/processed")),
            concurrency=int(summarize_r.get("concurrency", SummarizeSettings.concurrency)),
            max_input_chars=int(summarize_r.get("max_input_chars", SummarizeSettings.max_input_chars)),
            llm=_parse_llm(summarize_r.get("llm")),
            system_prompt=summarize_r.get("system_prompt", DEFAULT_SUMMARIZE_SYSTEM_PROMPT),
        )

    sources = SourcesSettings(
        yaml_path=Path(sources_r.get("yaml_path", "configs/radar_sources.yaml")),
        auto_seed=bool(sources_r.get("auto_seed", SourcesSettings.auto_seed)),
    )

    return RadarConfig(db=db, collect=collect, fetch=fetch, dedup=dedup, summarize=summarize, sources=sources)
