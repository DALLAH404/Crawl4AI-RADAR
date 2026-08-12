"""Load crawl configuration from a YAML file."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from crawl4ai import CacheMode

DEFAULT_CONFIG_PATH = Path("configs/default.yaml")

CACHE_MODES = {m.value: m for m in CacheMode}

DEFAULT_SUMMARY_SYSTEM_PROMPT = (
    "Summarize the following web page content into a single concise paragraph "
    "(~150 words). Capture the main topic, key entities, and notable facts. "
    "Do not use bullet points."
)
DEFAULT_SUMMARY_USER_TEMPLATE = "{content}"

DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
]

DEFAULT_BLOCK_INDICATORS = [
    "access denied",
    "captcha",
    "cf-ray id",
    "cloudflare ray",
    "just a moment",
    "please verify you are a human",
    "checking your browser before accessing",
    "attention required! please complete the verification",
    "bot detection",
]

DEFAULT_BLOCK_STATUS_CODES = [403, 429, 503]


@dataclass
class BrowserSettings:
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutputSettings:
    dir: Path = Path("outputs/raw")


@dataclass
class LLMSettings:
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"
    temperature: float = 0.3
    max_tokens: int = 400


@dataclass
class SummarySettings:
    input_dir: Path = Path("outputs/raw")
    output_dir: Path = Path("outputs/processed")
    max_input_chars: int = 20000
    llm: LLMSettings = field(default_factory=LLMSettings)
    system_prompt: str = DEFAULT_SUMMARY_SYSTEM_PROMPT
    user_prompt_template: str = DEFAULT_SUMMARY_USER_TEMPLATE


@dataclass
class GeminiEmbeddingSettings:
    provider: str = "gemini"
    model: str = "models/gemini-embedding-001"
    dim: int = 768
    api_key_env: str = "GEMINI_API_KEY"
    task_type: str = "RETRIEVAL_DOCUMENT"


DEFAULT_DEDUP_JUDGE_SYSTEM_PROMPT = (
    "You are a deduplication judge. Given two page titles and short content "
    "excerpts, decide if they describe the same specific event, different "
    "events, or are merely related. Respond with exactly one of: same, "
    "different, related."
)


@dataclass
class DedupSettings:
    db_path: Path = Path("outputs/dedup.db")
    input_dir: Path = Path("outputs/raw")
    title_window_hours: int = 72
    jaccard_window_hours: int = 24
    jaccard_threshold: float = 0.4
    embedding_window_hours: int = 72
    embedding_threshold: float = 0.85
    embedding_ambiguity_low: float = 0.75
    embedding_ambiguity_high: float = 0.85
    embedding_top_k: int = 10
    embedding: GeminiEmbeddingSettings = field(default_factory=GeminiEmbeddingSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    judge_system_prompt: str = DEFAULT_DEDUP_JUDGE_SYSTEM_PROMPT


@dataclass
class AntiBlockSettings:
    enabled: bool = True
    max_retries: int = 3
    backoff_seconds: float = 2.0
    jitter_seconds: float = 0.5
    rotate_user_agent: bool = True
    user_agents: list[str] = field(default_factory=lambda: list(DEFAULT_USER_AGENTS))
    magic: bool = True
    enable_stealth: bool = True
    block_indicators: list[str] = field(default_factory=lambda: list(DEFAULT_BLOCK_INDICATORS))
    block_status_codes: list[int] = field(default_factory=lambda: list(DEFAULT_BLOCK_STATUS_CODES))
    min_content_chars: int = 200

    def merged_with(self, override: dict[str, Any] | None) -> "AntiBlockSettings":
        """Return a copy with fields from `override` (partial dict) applied on top."""
        out_kwargs: dict[str, Any] = {
            "enabled": self.enabled,
            "max_retries": self.max_retries,
            "backoff_seconds": self.backoff_seconds,
            "jitter_seconds": self.jitter_seconds,
            "rotate_user_agent": self.rotate_user_agent,
            "user_agents": list(self.user_agents),
            "magic": self.magic,
            "enable_stealth": self.enable_stealth,
            "block_indicators": list(self.block_indicators),
            "block_status_codes": list(self.block_status_codes),
            "min_content_chars": self.min_content_chars,
        }
        for k, v in (override or {}).items():
            if k in out_kwargs:
                out_kwargs[k] = v
        return AntiBlockSettings(**out_kwargs)


@dataclass
class Target:
    slug: str
    url: str
    wait_for: str | None = None
    js_code: str | None = None
    session_id: str | None = None
    run_kwargs: dict[str, Any] = field(default_factory=dict)
    anti_block: dict[str, Any] | None = None


@dataclass
class CrawlConfig:
    browser: BrowserSettings
    defaults: dict[str, Any]
    output: OutputSettings
    targets: list[Target]
    summary: SummarySettings | None = None
    anti_block: AntiBlockSettings | None = None
    dedup: DedupSettings | None = None

    def to_summary(self) -> str:
        return (
            f"browser={self.browser.kwargs} "
            f"defaults_keys={sorted(self.defaults)} "
            f"output_dir={self.output.dir} "
            f"targets=[{', '.join(t.slug for t in self.targets)}]"
        )


def _coerce_cache_mode(value: Any) -> Any:
    if isinstance(value, str) and value in CACHE_MODES:
        return CACHE_MODES[value]
    return value


def _coerce_run_kwargs(d: dict[str, Any]) -> dict[str, Any]:
    out = dict(d)
    if "cache_mode" in out:
        out["cache_mode"] = _coerce_cache_mode(out["cache_mode"])
    return out


def _parse_anti_block(raw: dict[str, Any] | None) -> AntiBlockSettings:
    raw = raw or {}
    return AntiBlockSettings(
        enabled=bool(raw.get("enabled", AntiBlockSettings.enabled)),
        max_retries=int(raw.get("max_retries", AntiBlockSettings.max_retries)),
        backoff_seconds=float(raw.get("backoff_seconds", AntiBlockSettings.backoff_seconds)),
        jitter_seconds=float(raw.get("jitter_seconds", AntiBlockSettings.jitter_seconds)),
        rotate_user_agent=bool(raw.get("rotate_user_agent", AntiBlockSettings.rotate_user_agent)),
        user_agents=list(raw.get("user_agents", list(DEFAULT_USER_AGENTS))),
        magic=bool(raw.get("magic", AntiBlockSettings.magic)),
        enable_stealth=bool(raw.get("enable_stealth", AntiBlockSettings.enable_stealth)),
        block_indicators=list(raw.get("block_indicators", list(DEFAULT_BLOCK_INDICATORS))),
        block_status_codes=list(raw.get("block_status_codes", list(DEFAULT_BLOCK_STATUS_CODES))),
        min_content_chars=int(raw.get("min_content_chars", AntiBlockSettings.min_content_chars)),
    )


def _parse_target(raw: dict[str, Any]) -> Target:
    if not isinstance(raw, dict):
        raise ValueError(f"Target entry must be a mapping, got {type(raw).__name__}: {raw}")
    if "slug" not in raw or "url" not in raw:
        raise ValueError(f"Target missing required 'slug' or 'url': {raw}")
    excluded = {"slug", "url", "wait_for", "js_code", "session_id", "anti_block"}
    run_kwargs = {k: v for k, v in raw.items() if k not in excluded}
    anti_block = raw.get("anti_block")
    return Target(
        slug=raw["slug"],
        url=raw["url"],
        wait_for=raw.get("wait_for"),
        js_code=raw.get("js_code"),
        session_id=raw.get("session_id"),
        run_kwargs=_coerce_run_kwargs(run_kwargs),
        anti_block=anti_block if isinstance(anti_block, dict) else None,
    )


def _parse_llm(raw_llm: dict[str, Any] | None) -> LLMSettings:
    """Parse an `llm:` YAML mapping into an LLMSettings dataclass."""
    raw_llm = raw_llm or {}
    return LLMSettings(
        base_url=raw_llm.get("base_url", LLMSettings.base_url),
        model=raw_llm.get("model", LLMSettings.model),
        api_key_env=raw_llm.get("api_key_env", LLMSettings.api_key_env),
        temperature=float(raw_llm.get("temperature", LLMSettings.temperature)),
        max_tokens=int(raw_llm.get("max_tokens", LLMSettings.max_tokens)),
    )


def _parse_summary(raw: dict[str, Any] | None) -> SummarySettings:
    raw = raw or {}
    return SummarySettings(
        input_dir=Path(raw.get("input_dir", SummarySettings.input_dir)),
        output_dir=Path(raw.get("output_dir", SummarySettings.output_dir)),
        max_input_chars=int(raw.get("max_input_chars", SummarySettings.max_input_chars)),
        llm=_parse_llm(raw.get("llm")),
        system_prompt=raw.get("system_prompt", DEFAULT_SUMMARY_SYSTEM_PROMPT),
        user_prompt_template=raw.get("user_prompt_template", DEFAULT_SUMMARY_USER_TEMPLATE),
    )


def _parse_embedding(raw: dict[str, Any] | None) -> GeminiEmbeddingSettings:
    raw = raw or {}
    return GeminiEmbeddingSettings(
        provider=str(raw.get("provider", GeminiEmbeddingSettings.provider)),
        model=str(raw.get("model", GeminiEmbeddingSettings.model)),
        dim=int(raw.get("dim", GeminiEmbeddingSettings.dim)),
        api_key_env=str(raw.get("api_key_env", GeminiEmbeddingSettings.api_key_env)),
        task_type=str(raw.get("task_type", GeminiEmbeddingSettings.task_type)),
    )


def _parse_dedup(raw: dict[str, Any] | None) -> DedupSettings:
    raw = raw or {}
    return DedupSettings(
        db_path=Path(raw.get("db_path", DedupSettings.db_path)),
        input_dir=Path(raw.get("input_dir", DedupSettings.input_dir)),
        title_window_hours=int(raw.get("title_window_hours", DedupSettings.title_window_hours)),
        jaccard_window_hours=int(raw.get("jaccard_window_hours", DedupSettings.jaccard_window_hours)),
        jaccard_threshold=float(raw.get("jaccard_threshold", DedupSettings.jaccard_threshold)),
        embedding_window_hours=int(raw.get("embedding_window_hours", DedupSettings.embedding_window_hours)),
        embedding_threshold=float(raw.get("embedding_threshold", DedupSettings.embedding_threshold)),
        embedding_ambiguity_low=float(raw.get("embedding_ambiguity_low", DedupSettings.embedding_ambiguity_low)),
        embedding_ambiguity_high=float(raw.get("embedding_ambiguity_high", DedupSettings.embedding_ambiguity_high)),
        embedding_top_k=int(raw.get("embedding_top_k", DedupSettings.embedding_top_k)),
        embedding=_parse_embedding(raw.get("embedding")),
        llm=_parse_llm(raw.get("llm")),
        judge_system_prompt=str(raw.get("judge_system_prompt", DEFAULT_DEDUP_JUDGE_SYSTEM_PROMPT)),
    )


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> CrawlConfig:
    """Load a YAML config file and return a CrawlConfig."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")

    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping, got {type(data).__name__}")

    browser_raw = data.get("browser") or {}
    defaults_raw = data.get("defaults") or {}
    output_raw = data.get("output") or {}
    targets_raw = data.get("targets") or []
    summary_raw = data.get("summary")
    anti_block_raw = data.get("anti_block")
    dedup_raw = data.get("dedup")

    if not isinstance(targets_raw, list):
        raise ValueError("'targets' must be a list")

    defaults = _coerce_run_kwargs(dict(defaults_raw))
    targets = [_parse_target(t) for t in targets_raw]
    summary = _parse_summary(summary_raw) if summary_raw is not None else None
    anti_block = _parse_anti_block(anti_block_raw) if anti_block_raw is not None else None
    dedup = _parse_dedup(dedup_raw) if dedup_raw is not None else None

    return CrawlConfig(
        browser=BrowserSettings(kwargs=dict(browser_raw)),
        defaults=defaults,
        output=OutputSettings(dir=Path(output_raw.get("dir", "outputs/raw"))),
        targets=targets,
        summary=summary,
        anti_block=anti_block,
        dedup=dedup,
    )


def merge_configs(paths: list[str | Path]) -> CrawlConfig:
    """Load multiple YAML files and merge them. Later files win on top-level keys
    and override targets with the same slug."""
    if not paths:
        return load_config(DEFAULT_CONFIG_PATH)
    configs = [load_config(p) for p in paths]
    last = configs[-1]
    browser = BrowserSettings(kwargs=dict(last.browser.kwargs))
    defaults = dict(last.defaults)
    output = OutputSettings(dir=last.output.dir)
    targets_map: dict[str, Target] = {}
    for c in configs:
        for t in c.targets:
            targets_map[t.slug] = t
    targets = list(targets_map.values())
    summary = copy.deepcopy(last.summary) if last.summary is not None else None
    anti_block = copy.deepcopy(last.anti_block) if last.anti_block is not None else None
    dedup = copy.deepcopy(last.dedup) if last.dedup is not None else None
    return CrawlConfig(
        browser=browser,
        defaults=defaults,
        output=output,
        targets=targets,
        summary=summary,
        anti_block=anti_block,
        dedup=dedup,
    )
