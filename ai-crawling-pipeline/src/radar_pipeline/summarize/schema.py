"""Strict schema validation for the LLM summarization response.

Single source of truth for the field constraints shared by the prompt
(`radar_pipeline.summarize.prompts`) and the client
(`radar_pipeline.summarize.client`). Pure stdlib — no extra dependencies.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

EVENT_TYPES: tuple[str, ...] = (
    "Lancamento",
    "Evento",
    "Institucional",
    "M&A",
    "Preco",
    "Investimento",
    "Distribuicao",
    "Executivos",
    "Indicador",
    "Atualizacao",
)

ALERT_LEVELS: tuple[str, ...] = ("Alto", "Medio", "Baixo")

DEFAULT_EVENT_TYPE = "Atualizacao"
DEFAULT_ALERT_LEVEL = "Baixo"

MAX_SUMMARY_LEN = 900
MAX_COMPETITOR_ANALYSIS_LEN = 500
MAX_TITLE_LEN = 120

_TRUE_STRINGS = {"true", "yes", "1", "y", "t"}
_FALSE_STRINGS = {"false", "no", "0", "n", "f"}


def parse_llm_json(raw: str) -> dict[str, Any] | None:
    """Parse the LLM response as a JSON object.

    Returns the dict on success. Returns ``None`` when the response is not
    valid JSON (with or without markdown fences) — the caller is responsible
    for surfacing this as a hard failure rather than silently storing text.
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def coerce_relevant(value: Any) -> bool:
    """Coerce a value coming from JSON into a strict boolean.

    Accepts genuine booleans and common string variants ("true"/"false",
    "yes"/"no", "1"/"0"). Anything else falls back to ``True`` with a
    warning so that a malformed model output is not silently treated as
    irrelevant (false) — this matches the historical default behavior
    while still closing the bug where the string ``"false"`` was truthy.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUE_STRINGS:
            return True
        if v in _FALSE_STRINGS:
            return False
    logger.warning("Unexpected 'relevant' value %r; defaulting to True", value)
    return True


def validate_event_type(value: Any) -> str:
    if isinstance(value, str) and value in EVENT_TYPES:
        return value
    return DEFAULT_EVENT_TYPE


def validate_alert_level(value: Any) -> str:
    if isinstance(value, str) and value in ALERT_LEVELS:
        return value
    return DEFAULT_ALERT_LEVEL


class SchemaError(ValueError):
    """Raised when the LLM payload is structurally unusable."""


def normalize_summary_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and coerce a parsed LLM dict into a SummaryResult-shaped dict.

    Returns a dict with keys: summary, competitor_analysis, event_type,
    alert_level, relevant — all coerced to the expected types and lengths.
    Raises ``SchemaError`` if the payload lacks a usable ``summary`` field.
    """
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise SchemaError("missing or empty 'summary' field in LLM response")
    summary = summary.strip()[:MAX_SUMMARY_LEN]

    competitor_analysis = data.get("competitor_analysis") or ""
    if not isinstance(competitor_analysis, str):
        competitor_analysis = ""
    competitor_analysis = competitor_analysis.strip()[:MAX_COMPETITOR_ANALYSIS_LEN]

    # Optional, unlike summary — an empty string here (not raising) is what
    # tells the caller (summarize/pipeline.py) to fall back to the article's
    # original scraped title instead of overwriting it with nothing.
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        title = ""
    else:
        title = title.strip()[:MAX_TITLE_LEN]

    return {
        "summary": summary,
        "competitor_analysis": competitor_analysis,
        "title": title,
        "event_type": validate_event_type(data.get("event_type")),
        "alert_level": validate_alert_level(data.get("alert_level")),
        "relevant": coerce_relevant(data.get("relevant", True)),
    }