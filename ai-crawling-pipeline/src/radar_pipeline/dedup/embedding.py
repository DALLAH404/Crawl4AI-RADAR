"""Gemini embedding client for the dedup stage.

Async wrapper around google.generativeai.embed_content.
Uses the models/gemini-embedding-001 model at 768 dimensions.
"""

from __future__ import annotations

import asyncio
import logging
import os
import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning, append=False)
    import google.generativeai as genai

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "models/gemini-embedding-001"
DEFAULT_DIM = 768


class GeminiEmbedder:
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
            raise ValueError(
                f"Missing API key: set the {api_key_env} environment variable"
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
        vectors = await self.embed_many([text], task_type=task_type)
        return vectors[0]

    async def embed_many(
        self,
        texts: list[str],
        task_type: str | None = None,
    ) -> list[list[float]]:
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
