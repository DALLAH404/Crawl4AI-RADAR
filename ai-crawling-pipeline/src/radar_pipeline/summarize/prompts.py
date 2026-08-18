"""Summary prompt templates for the Radar Aftermarket Pipeline.

The ``EVENT_TYPES`` and ``ALERT_LEVELS`` tuples are the single source of
truth for the allowed enum values — they are imported by
``radar_pipeline.summarize.schema`` so the prompt and the validator can
never drift apart. Edit them here and both surfaces update.
"""
from __future__ import annotations

from radar_pipeline.summarize.schema import ALERT_LEVELS, EVENT_TYPES

DEFAULT_SYSTEM_PROMPT = (
    "You are a market-intelligence analyst for the Brazilian automotive "
    "aftermarket. Summarize the article in a single paragraph (max 900 chars) "
    "and assess its competitive relevance.\n"
    "Your response must be valid JSON with these keys exactly:\n"
    '  "title": a clear, user-friendly headline (max 120 characters) that a '
    "reader can understand on its own — rewrite it if the original title is "
    "truncated, a raw social-media caption, or otherwise unclear; keep it as "
    "close to the original as possible if it's already clear. Must be "
    "grounded in the article's own content, not invented. Empty string if "
    "you cannot produce one.\n"
    '  "summary": concise paragraph in English capturing the key business event '
    "and its relevance to the automotive aftermarket.\n"
    '  "competitor_analysis": if the article mentions a specific competitor '
    "action (launch, acquisition, expansion...), describe the competitive "
    "impact in 1-2 sentences, otherwise empty string.\n"
    f'  "event_type": one of [{", ".join(EVENT_TYPES)}].\n'
    f'  "alert_level": one of [{", ".join(ALERT_LEVELS)}].\n'
    '  "relevant": true if the article is about automotive aftermarket / '
    "replacement parts / competitor actions, false if it is a generic "
    "corporate PR piece not about aftermarket.\n"
    "Respond ONLY with the JSON object, no markdown fences."
)