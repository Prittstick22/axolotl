"""Embeddings - owner: Dev 3. Spec §8.5.

Vectors for `code_chunks.embedding` and for query text, from the model named in
`GEMINI_EMBEDDING_MODEL` at `EMBEDDING_DIMENSIONS`.

The dimension count must match the Atlas vector index exactly. It is read from config
in both places, so changing it is one `.env` edit plus an index rebuild - never a
silent mismatch that returns nonsense.

Documents and queries are embedded with different task types, which is what the model
expects and measurably improves retrieval: a question and the code that answers it are
not the same kind of text.

Quota (#11): chunk embeddings go up to `MAX_BATCH` texts per request
(`batchEmbedContents`), so a 20-chunk batch is one request, not twenty. A query's
vector is kept for the life of the process, so rehearsing the same demo query costs
one call, not one per search. Both are the model's real answers for that exact text,
model and dimension count, never a stand-in. And a spent daily quota stops the work at
the first refusal instead of failing chunk by chunk.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict

from app.config import get_settings
from app.search.retry import describe, is_daily_quota_spent, is_retryable, with_retries

log = logging.getLogger(__name__)

MAX_CONCURRENCY = 5
DOCUMENT_TASK = "RETRIEVAL_DOCUMENT"
QUERY_TASK = "RETRIEVAL_QUERY"
QUERY_CACHE_SIZE = 256
# Gemini's cap on texts in one batchEmbedContents request.
MAX_BATCH = 100

# (model, dimensions, task type, text) -> vector, most recently used last.
_query_vectors: OrderedDict[tuple[str, int | None, str, str], list[float]] = OrderedDict()


class EmbeddingUnavailable(Exception):
    """No key, no model, or the API refused. Callers degrade rather than guess."""

    def __init__(
        self, message: str, *, quota_spent: bool = False, transient: bool = False
    ) -> None:
        super().__init__(message)
        self.quota_spent = quota_spent
        # Busy or rate limited even after retries, as opposed to a request the API
        # rejected: re-sending the same texts one by one would fail the same way.
        self.transient = transient


def _unavailable(exc: BaseException) -> EmbeddingUnavailable:
    return EmbeddingUnavailable(
        describe(exc), quota_spent=is_daily_quota_spent(exc),
        transient=is_retryable(exc),
    )


def _checked(vector: list[float], settings) -> list[float]:
    """A wrong length silently breaks `$vectorSearch`, which rejects the query rather
    than explaining itself, so check here where the cause is still visible."""
    expected = settings.embedding_dimensions
    if expected and len(vector) != expected:
        raise EmbeddingUnavailable(
            f"{settings.gemini_embedding_model} returned {len(vector)} dimensions, "
            f"but EMBEDDING_DIMENSIONS and the Atlas index expect {expected}"
        )
    return vector


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
        raise _unavailable(exc) from exc

    vector = _checked(list(response.embeddings[0].values), settings)
    if task_type == QUERY_TASK:
        _query_vectors[key] = list(vector)
        if len(_query_vectors) > QUERY_CACHE_SIZE:
            _query_vectors.popitem(last=False)
    return vector


async def embed_many(
    texts: list[str], *, task_type: str = DOCUMENT_TASK
) -> list[list[float]]:
    """Embed up to `MAX_BATCH` texts in **one** request. Raises EmbeddingUnavailable.

    All or nothing: Gemini answers a batch as a whole, so a vector per text in order,
    or an exception. The count is checked, because a short answer zipped against the
    chunks would put each vector on the wrong chunk.
    """
    if not texts:
        return []
    if len(texts) > MAX_BATCH:
        raise ValueError(f"at most {MAX_BATCH} texts per request, got {len(texts)}")
    client, settings = _client()
    from google.genai import types

    async def call():
        return await client.aio.models.embed_content(
            model=settings.gemini_embedding_model,
            contents=list(texts),
            config=types.EmbedContentConfig(
                output_dimensionality=settings.embedding_dimensions,
                task_type=task_type,
            ),
        )

    try:
        response = await with_retries(call, what=f"embedding {len(texts)} text(s)")
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc

    vectors = [list(e.values) for e in (response.embeddings or [])]
    if len(vectors) != len(texts):
        raise EmbeddingUnavailable(
            f"asked for {len(texts)} embeddings in one request, got {len(vectors)}"
        )
    return [_checked(v, settings) for v in vectors]


async def embed_documents(
    texts: list[str], *, failures: list[str] | None = None
) -> list[list[float] | None]:
    """Embed many chunk texts, `MAX_BATCH` per request. A failure yields None for
    that text, never a zero vector - a zero vector would sit at a fixed distance from
    every query and quietly pollute results.

    When the API rejects a batch outright (say, one text it will not take), that
    batch is retried one text at a time so the others are not lost. A busy model or a
    spent quota is not retried that way: it would refuse each text just the same.

    `failures`, when given, receives the first error's text, so a caller can say why.
    """
    results: list[list[float] | None] = [None] * len(texts)

    def note(exc: EmbeddingUnavailable) -> None:
        log.warning("embedding failed: %s", exc)
        if failures is not None and not failures:
            failures.append(str(exc))

    for start in range(0, len(texts), MAX_BATCH):
        part = texts[start:start + MAX_BATCH]
        try:
            results[start:start + len(part)] = await embed_many(
                part, task_type=DOCUMENT_TASK
            )
            continue
        except EmbeddingUnavailable as exc:
            note(exc)
            if exc.quota_spent:
                log.warning("daily embedding quota spent; skipping the rest")
                break
            if exc.transient or len(part) == 1:
                continue
        await _one_by_one(part, start, results, note)

    embedded = sum(1 for r in results if r is not None)
    log.info("embedded %d/%d chunk(s)", embedded, len(texts))
    return results


async def _one_by_one(part, offset, results, note) -> None:
    limit = asyncio.Semaphore(MAX_CONCURRENCY)
    spent: list[bool] = []

    async def run(index: int, text: str) -> None:
        async with limit:
            if spent:
                return      # every remaining call would be refused the same way
            try:
                results[offset + index] = await embed_one(text, task_type=DOCUMENT_TASK)
            except EmbeddingUnavailable as exc:
                note(exc)
                if exc.quota_spent:
                    spent.append(True)

    await asyncio.gather(*(run(i, t) for i, t in enumerate(part)))


def expected_dimensions() -> int:
    settings = get_settings()
    return int(settings.embedding_dimensions or 768)
