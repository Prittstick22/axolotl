"""Embeddings - owner: Dev 3. Spec §8.5.

Vectors for `code_chunks.embedding` and for query text, from the model named in
`GEMINI_EMBEDDING_MODEL` at `EMBEDDING_DIMENSIONS`.

The dimension count must match the Atlas vector index exactly. It is read from config
in both places, so changing it is one `.env` edit plus an index rebuild - never a
silent mismatch that returns nonsense.

Documents and queries are embedded with different task types, which is what the model
expects and measurably improves retrieval: a question and the code that answers it are
not the same kind of text.

Quota (#11): a query's vector is kept for the life of the process, so rehearsing the
same demo query costs one call, not one per search. It is the model's real answer for
that exact text, model and dimension count, never a stand-in. And a spent daily quota
stops a batch of chunk embeddings at the first failure instead of failing each one.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict

from app.config import get_settings
from app.search.retry import describe, is_daily_quota_spent, with_retries

log = logging.getLogger(__name__)

MAX_CONCURRENCY = 5
DOCUMENT_TASK = "RETRIEVAL_DOCUMENT"
QUERY_TASK = "RETRIEVAL_QUERY"
QUERY_CACHE_SIZE = 256

# (model, dimensions, task type, text) -> vector, most recently used last.
_query_vectors: OrderedDict[tuple[str, int | None, str, str], list[float]] = OrderedDict()


class EmbeddingUnavailable(Exception):
    """No key, no model, or the API refused. Callers degrade rather than guess."""

    def __init__(self, message: str, *, quota_spent: bool = False) -> None:
        super().__init__(message)
        self.quota_spent = quota_spent


def _client():
    settings = get_settings()
    if not settings.gemini_api_key:
        raise EmbeddingUnavailable("GEMINI_API_KEY is not set")
    if not settings.gemini_embedding_model:
        raise EmbeddingUnavailable("GEMINI_EMBEDDING_MODEL is not set")

    from google import genai

    return genai.Client(api_key=settings.gemini_api_key), settings


async def embed_one(text: str, *, task_type: str = QUERY_TASK) -> list[float]:
    """Embed a single string. Used for the query side of vector search."""
    client, settings = _client()
    key = (settings.gemini_embedding_model, settings.embedding_dimensions, task_type, text)
    if task_type == QUERY_TASK and key in _query_vectors:
        _query_vectors.move_to_end(key)
        return list(_query_vectors[key])

    from google.genai import types

    async def call():
        return await client.aio.models.embed_content(
            model=settings.gemini_embedding_model,
            contents=text,
            config=types.EmbedContentConfig(
                output_dimensionality=settings.embedding_dimensions,
                task_type=task_type,
            ),
        )

    try:
        response = await with_retries(call, what="embedding")
    except Exception as exc:  # noqa: BLE001
        raise EmbeddingUnavailable(
            describe(exc), quota_spent=is_daily_quota_spent(exc)
        ) from exc

    vector = list(response.embeddings[0].values)
    # A wrong length silently breaks `$vectorSearch`, which rejects the query rather
    # than explaining itself, so check here where the cause is still visible.
    expected = settings.embedding_dimensions
    if expected and len(vector) != expected:
        raise EmbeddingUnavailable(
            f"{settings.gemini_embedding_model} returned {len(vector)} dimensions, "
            f"but EMBEDDING_DIMENSIONS and the Atlas index expect {expected}"
        )
    if task_type == QUERY_TASK:
        _query_vectors[key] = list(vector)
        if len(_query_vectors) > QUERY_CACHE_SIZE:
            _query_vectors.popitem(last=False)
    return vector


async def embed_documents(
    texts: list[str], *, failures: list[str] | None = None
) -> list[list[float] | None]:
    """Embed many chunk texts. A failure yields None for that one, never a zero
    vector - a zero vector would sit at a fixed distance from every query and quietly
    pollute results.

    `failures`, when given, receives the first error's text, so a caller can say why.
    """
    if not texts:
        return []

    limit = asyncio.Semaphore(MAX_CONCURRENCY)
    results: list[list[float] | None] = [None] * len(texts)
    spent: list[str] = []

    async def run(index: int, text: str) -> None:
        async with limit:
            if spent:
                return      # every remaining call would be refused the same way
            try:
                results[index] = await embed_one(text, task_type=DOCUMENT_TASK)
            except EmbeddingUnavailable as exc:
                log.warning("embedding %d failed: %s", index, exc)
                if failures is not None and not failures:
                    failures.append(str(exc))
                if exc.quota_spent and not spent:
                    spent.append(str(exc))
                    log.warning("daily embedding quota spent; skipping the rest")

    await asyncio.gather(*(run(i, t) for i, t in enumerate(texts)))

    embedded = sum(1 for r in results if r is not None)
    log.info("embedded %d/%d chunk(s)", embedded, len(texts))
    return results


def expected_dimensions() -> int:
    settings = get_settings()
    return int(settings.embedding_dimensions or 768)
