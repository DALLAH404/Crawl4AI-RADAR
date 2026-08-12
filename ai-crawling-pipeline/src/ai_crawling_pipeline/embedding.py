"""Async wrapper around the Gemini embedding API used by the dedup stage.

The dedup stage only needs embedding (not generation) from Gemini. We call
`google.generativeai.embed_content` from a thread to keep the dedup loop
non-blocking. Batching is done per-layer (all candidate titles in one call
when possible) to amortize the round-trip.
"""

from __future__ import annotations

import asyncio
import logging
import os
import warnings

# The `google.generativeai` package is officially deprecated in favor of
# `google.genai`, but it still works. Scope the suppression narrowly to
# that module so legitimate FutureWarnings from other libraries still
# surface.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning, append=False)
    import google.generativeai as genai  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "models/gemini-embedding-001"
DEFAULT_DIM = 768


class GeminiEmbedder:
    """Thin async wrapper around the Gemini embed_content API."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        dim: int = DEFAULT_DIM,
        api_key: str | None = None,
        api_key_env: str = "GEMINI_API_KEY",
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> None:
        if not api_key:
            api_key = os.environ.get(api_key_env)
        if not api_key:
            # ValueError (not SystemExit) so the orchestrator's
            # `except Exception` guard can handle the missing-config case.
            raise ValueError(
                f"Missing API key: set the {api_key_env} environment variable "
                f"(e.g. in .env or via `export {api_key_env}=...`)."
            )
        genai.configure(api_key=api_key)
        self._model = model
        self._dim = dim
        self._task_type = task_type

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, text: str, task_type: str | None = None) -> list[float]:
        """Embed a single string."""
        vectors = await self.embed_many([text], task_type=task_type)
        return vectors[0]

    async def embed_many(
        self,
        texts: list[str],
        task_type: str | None = None,
    ) -> list[list[float]]:
        """Embed a list of strings. Empty input returns [].

        The SDK's batch form returns a single `embedding` key containing a
        list parallel to the inputs.
        """
        if not texts:
            return []
        tt = task_type or self._task_type
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: genai.embed_content(
                model=self._model,
                content=texts,
                task_type=tt,
                output_dimensionality=self._dim,
            ),
        )
        vecs = result.get("embedding") or []
        if len(vecs) != len(texts):
            raise RuntimeError(
                f"Gemini returned {len(vecs)} embeddings for {len(texts)} inputs"
            )
        return [list(v) for v in vecs]

