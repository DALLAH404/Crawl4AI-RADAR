"""Local dedup/filteration stage.

Re-implements the layered filteration algorithm from filteration.md using
SQLite (via db.py), Gemini embeddings (via embedding.py), and an
OpenAI-compatible LLM (via the same AsyncOpenAI client used by summary.py).

The five layers, in order:
    0  canonical_url        -> md5 -> items.id_hash
    1  normalized title     -> md5 -> items.title_hash (within title_window)
    2  Jaccard(title) > th  -> in-memory compare vs SQLite candidates
    3  Gemini embedding     -> sqlite-vec KNN cosine vs items (within window)
    4  OpenAI-compat LLM judge when 0.75 < cosine <= 0.85

The competitor/story_group/enrichments_queue concepts from the original
news algorithm are intentionally not modeled: this pipeline only needs
new vs duplicate per item, and downstream stages (the summarizer) use
`status='new'` as the gate.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from openai import AsyncOpenAI

from ai_crawling_pipeline.config import DedupSettings
from ai_crawling_pipeline.db import (
    candidate_titles_within,
    connect,
    find_primary_by_id_hash,
    find_by_title_hash_within,
    init_schema,
    insert_item,
    knn_within,
)
from ai_crawling_pipeline.embedding import GeminiEmbedder

logger = logging.getLogger(__name__)

MAX_EMBED_CHARS = 2000  # body excerpt fed to Gemini embedding
MAX_JUDGE_EXCERPT_CHARS = 400  # per-side excerpt fed to the LLM judge
JACCARD_TITLE_CAP = 5000  # max titles considered per layer-2 scan


# ---------------------------------------------------------------------------
# Pure helpers (no IO)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


def canonicalize_url(url: str) -> str:
    """Lowercase scheme+host, drop trailing slash and fragments. Kept simple
    because the upstream crawler does not always give a clean URL."""
    u = url.strip()
    if "#" in u:
        u = u.split("#", 1)[0]
    if u.endswith("/"):
        u = u[:-1]
    return u.lower()


def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def normalize_title(title: str) -> str:
    """Lowercase, collapse whitespace, strip common boilerplate prefixes."""
    t = title.strip().lower()
    t = re.sub(r"\s+", " ", t)
    for prefix in ("breaking:", "update:", "news:", "just in:"):
        if t.startswith(prefix):
            t = t[len(prefix):].lstrip()
    return t


def title_id_hash(canonical_url: str) -> str:
    return md5_hex(canonicalize_url(canonical_url))


def title_text_hash(title: str) -> str:
    return md5_hex(normalize_title(title))


def tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    layer: int | None  # 0..4 if duplicate, None if new
    id_hash: str
    title: str
    status: str  # 'dupe' or 'new'
    dupe_of_hash: str | None = None
    cluster_score: float | None = None
    rowid: int | None = None  # rowid of the row inserted for this item


class Deduper:
    def __init__(self, settings: DedupSettings) -> None:
        self.settings = settings
        self.con = connect(settings.db_path)
        self.embedder = GeminiEmbedder(
            model=settings.embedding.model,
            dim=settings.embedding.dim,
            api_key_env=settings.embedding.api_key_env,
            task_type=settings.embedding.task_type,
        )
        init_schema(self.con, self.embedder.dim)
        self.llm_client = AsyncOpenAI(
            base_url=settings.llm.base_url,
            api_key=os.environ.get(settings.llm.api_key_env) or "",
        )

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass

    # -------------------- Layer helpers --------------------

    async def _judge_llm(
        self, title_a: str, title_b: str, excerpt_a: str, excerpt_b: str
    ) -> str:
        """Call the OpenAI-compatible LLM and return one of:
        'same', 'different', 'related' (lowercased, stripped)."""
        prompt_user = (
            f"Item A title: {title_a}\nItem A excerpt: {excerpt_a[:400]}\n\n"
            f"Item B title: {title_b}\nItem B excerpt: {excerpt_b[:400]}\n\n"
            "Respond with exactly one of: same, different, related."
        )
        try:
            resp = await self.llm_client.chat.completions.create(
                model=self.settings.llm.model,
                messages=[
                    {"role": "system", "content": self.settings.judge_system_prompt},
                    {"role": "user", "content": prompt_user},
                ],
                temperature=self.settings.llm.temperature,
                max_tokens=self.settings.llm.max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM judge failed, treating as different: %s", exc)
            return "different"
        if not resp.choices:
            return "different"
        # Use a token-level match to avoid substring collisions like
        # "they are different, not the same" matching "same" first.
        text = (resp.choices[0].message.content or "").strip().lower()
        cleaned = re.sub(r"[^a-z]+", " ", text).strip()
        valid = {"same", "different", "related"}
        for word in cleaned.split():
            if word in valid:
                return word
        return "different"

    async def _check_layers_3_4(
        self, embedding: list[float], title: str, excerpt: str
    ) -> tuple[Decision | None, list[sqlite3.Row] | None]:
        """Run layer 3 (cosine) and, when ambiguous, layer 4 (LLM judge).

        Returns (decision, candidates) where decision is None when the item
        is new, or a Decision with layer=3/4 when a duplicate is found.
        Candidates (rows with their cosine distance) is returned only when
        the result is ambiguous and the caller might want to log them.
        """
        candidates = knn_within(
            self.con,
            embedding=embedding,
            since_ts=time.time() - self.settings.embedding_window_hours * 3600,
            top_k=self.settings.embedding_top_k,
        )
        if not candidates:
            return None, None

        best = candidates[0]
        best_distance = float(best["distance"])
        best_cosine = 1.0 - best_distance  # sqlite-vec cosine distance
        primary_hash = best["id_hash"]

        if best_cosine > self.settings.embedding_threshold:
            return (
                Decision(
                    layer=3,
                    id_hash="",
                    title=title,
                    status="dupe",
                    dupe_of_hash=primary_hash,
                    cluster_score=best_cosine,
                ),
                candidates,
            )

        if (
            self.settings.embedding_ambiguity_low
            < best_cosine
            <= self.settings.embedding_ambiguity_high
        ):
            # Load the primary's real excerpt (and title) from SQLite so the
            # LLM judge has actual content to compare against.
            other = find_primary_by_id_hash(self.con, primary_hash)
            other_title = (other["title"] or "") if other else ""
            other_excerpt = (other["excerpt"] or other_title) if other else ""
            verdict = await self._judge_llm(
                title_a=title,
                title_b=other_title,
                excerpt_a=excerpt,
                excerpt_b=other_excerpt,
            )
            if verdict == "same":
                return (
                    Decision(
                        layer=4,
                        id_hash="",
                        title=title,
                        status="dupe",
                        dupe_of_hash=primary_hash,
                        cluster_score=best_cosine,
                    ),
                    candidates,
                )
            return None, candidates

        return None, candidates

    async def classify(
        self,
        *,
        slug: str,
        source_url: str,
        canonical_url: str,
        title: str,
        excerpt: str,
    ) -> Decision:
        """Run the five layers for a single new item. Always returns a
        Decision. Each call inserts a fresh items row reflecting the
        decision (status='new' or status='dupe'); existing rows are never
        mutated, so the 'new' set stays stable across runs.

        `excerpt` is stored in the items table so the layer-4 LLM judge
        can compare real content (not just titles) when cosine falls in
        the ambiguous band.
        """
        id_hash = title_id_hash(canonical_url)
        title_hash = title_text_hash(title)
        now = time.time()
        stored_excerpt = (excerpt or "")[:MAX_EMBED_CHARS]

        def _insert_dupe(
            layer: int,
            primary_hash: str,
            cluster_score: float | None,
            embedding: list[float] | None,
        ) -> int:
            return insert_item(
                self.con,
                id_hash=id_hash,
                title_hash=title_hash,
                canonical_url=canonical_url,
                title=title,
                excerpt=stored_excerpt,
                content_path="",  # filled in by caller after persistence
                source=slug,
                source_url=source_url,
                embedding=embedding,
                embedding_model=self.embedder.model if embedding else None,
                created_at=now,
                dedup_layer=layer,
                dupe_of_hash=primary_hash,
                status="dupe",
                cluster_score=cluster_score,
            )

        # Layer 0: id_hash already exists?
        existing = find_primary_by_id_hash(self.con, id_hash)
        if existing is not None:
            rowid = _insert_dupe(0, existing["id_hash"], None, embedding=None)
            return Decision(
                layer=0,
                id_hash=id_hash,
                title=title,
                status="dupe",
                dupe_of_hash=existing["id_hash"],
                rowid=rowid,
            )

        # Layer 1: title_hash within the title window?
        existing_t = find_by_title_hash_within(
            self.con,
            title_hash=title_hash,
            since_ts=now - self.settings.title_window_hours * 3600,
        )
        if existing_t is not None:
            rowid = _insert_dupe(1, existing_t["id_hash"], None, embedding=None)
            return Decision(
                layer=1,
                id_hash=id_hash,
                title=title,
                status="dupe",
                dupe_of_hash=existing_t["id_hash"],
                rowid=rowid,
            )

        # Layer 2: Jaccard(title) over the Jaccard window.
        threshold = self.settings.jaccard_threshold
        if threshold > 0:
            since = now - self.settings.jaccard_window_hours * 3600
            cands = candidate_titles_within(self.con, since)
            if len(cands) > JACCARD_TITLE_CAP:
                # Cap the candidates to avoid quadratic blowup; the earliest
                # matches are still reachable via layer 1.
                cands = cands[:JACCARD_TITLE_CAP]
            new_tokens = tokenize(title)
            for cand in cands:
                score = jaccard(new_tokens, tokenize(cand["title"]))
                if score > threshold:
                    rowid = _insert_dupe(2, cand["id_hash"], score, embedding=None)
                    return Decision(
                        layer=2,
                        id_hash=id_hash,
                        title=title,
                        status="dupe",
                        dupe_of_hash=cand["id_hash"],
                        cluster_score=score,
                        rowid=rowid,
                    )

        # Layer 3/4: embedding + (optional) LLM judge.
        embedding = await self.embedder.embed(
            f"{title}\n\n{stored_excerpt}",
            task_type="RETRIEVAL_DOCUMENT",
        )
        decision, _candidates = await self._check_layers_3_4(
            embedding, title, stored_excerpt
        )
        if decision is not None:
            rowid = _insert_dupe(
                decision.layer,
                decision.dupe_of_hash or "",
                decision.cluster_score,
                embedding=embedding,
            )
            return Decision(
                layer=decision.layer,
                id_hash=id_hash,
                title=title,
                status="dupe",
                dupe_of_hash=decision.dupe_of_hash,
                cluster_score=decision.cluster_score,
                rowid=rowid,
            )

        # New item.
        rowid = insert_item(
            self.con,
            id_hash=id_hash,
            title_hash=title_hash,
            canonical_url=canonical_url,
            title=title,
            excerpt=stored_excerpt,
            content_path="",
            source=slug,
            source_url=source_url,
            embedding=embedding,
            embedding_model=self.embedder.model,
            created_at=now,
            dedup_layer=None,
            dupe_of_hash=None,
            status="new",
            cluster_score=None,
        )
        return Decision(
            layer=None,
            id_hash=id_hash,
            title=title,
            status="new",
            rowid=rowid,
        )


# ---------------------------------------------------------------------------
# Raw markdown -> decision
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"^URL:\s*(.+)$", re.MULTILINE)
_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def parse_raw_markdown(text: str) -> tuple[str, str, str]:
    """Extract (title, source_url, excerpt) from a raw crawl .md file.

    Files are written by crawl.py with a header of:
        # <title>
        URL: <url>
        <body...>
    """
    m_title = _TITLE_RE.search(text)
    m_url = _URL_RE.search(text)
    title = m_title.group(1).strip() if m_title else ""
    source_url = m_url.group(1).strip() if m_url else ""
    # The body starts after whichever of the two header lines comes last.
    body_end = 0
    if m_title is not None:
        body_end = max(body_end, m_title.end())
    if m_url is not None:
        body_end = max(body_end, m_url.end())
    body = text[body_end:]
    excerpt = body.strip()[:2000]
    return title, source_url, excerpt


async def dedup_all(settings: DedupSettings) -> None:
    """Run the dedup stage over every `*.md` file in `settings.input_dir`."""
    input_dir = Path(settings.input_dir)
    if not input_dir.exists():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    files = sorted(input_dir.glob("*.md"))
    if not files:
        print(f"No .md files found in {input_dir}")
        return

    print(
        f"Dedup: input={input_dir} db={settings.db_path} "
        f"embedding={settings.embedding.model} dim={settings.embedding.dim} "
        f"files={len(files)}"
    )

    deduper = Deduper(settings)
    try:
        results: list[Decision] = []
        for f in files:
            text = f.read_text(encoding="utf-8", errors="replace")
            title, source_url, excerpt = parse_raw_markdown(text)
            slug = f.stem
            canonical = source_url or f"local://{slug}"
            print(f"-> {slug}: {title[:80]}")
            try:
                decision = await deduper.classify(
                    slug=slug,
                    source_url=source_url,
                    canonical_url=canonical,
                    title=title or slug,
                    excerpt=excerpt,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Dedup failed for %s", slug)
                print(f"   ERROR: {type(exc).__name__}: {exc}")
                continue
            # Persist the content_path back to the row we just inserted.
            # Updating by rowid (not id_hash) is critical: layer 0-2 inserts
            # can share an id_hash with the primary row, and updating by
            # id_hash would corrupt the primary's content_path.
            if decision.rowid is not None:
                deduper.con.execute(
                    "UPDATE items SET content_path = ? WHERE rowid = ?",
                    (str(f), decision.rowid),
                )
                deduper.con.commit()
            label = (
                f"DUPE layer={decision.layer} of={decision.dupe_of_hash[:8]}"
                if decision.status == "dupe"
                else "NEW"
            )
            score = (
                f" score={decision.cluster_score:.3f}"
                if decision.cluster_score is not None
                else ""
            )
            print(f"   {label}{score}")
            results.append(decision)
    finally:
        deduper.close()

    new = sum(1 for d in results if d.status == "new")
    dupes = [d for d in results if d.status == "dupe"]
    print("\nDedup summary:")
    print(f"  total={len(results)} new={new} duplicates={len(dupes)}")
    by_layer: dict[int | None, int] = {}
    for d in dupes:
        by_layer[d.layer] = by_layer.get(d.layer, 0) + 1
    for layer in sorted(by_layer):
        print(f"  layer {layer}: {by_layer[layer]}")
