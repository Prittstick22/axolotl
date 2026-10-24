"""Protecting the Gemini quota (#11) - owner: Dev 3. Offline, no Gemini.

The free tier allows 1000 embedding requests a day; one chess import is 122. These pin
the places a request used to be spent for nothing:

  - a spent *daily* quota was retried four times per chunk, as if it were a momentary
    rate limit;
  - chunk embeddings carried on failing one by one after the quota was gone;
  - each chunk embedding was its own request (122 for chess) where one request
    carries up to 100;
  - every search re-embedded its query, so rehearsing one demo query cost a call each
    time.

The Gemini client is replaced by a counter; no request can leave the machine.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from app.config import get_settings
from app.search import embeddings
from app.search.retry import is_daily_quota_spent, is_retryable

# Verbatim from the 23 Oct 429 (BUILD_LOG.md).
DAILY = ("429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
         "embed_content_free_tier_requests, limit: 1000, model: gemini-embedding-1.0.  "
         "Please retry in 12h14m51s")
PER_MINUTE = ("429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
              "embed_content_free_tier_requests_per_minute, limit: 100. "
              "Please retry in 41.2s")


# --------------------------------------------------------------------------
# retries
# --------------------------------------------------------------------------

def test_a_spent_daily_quota_is_not_retried():
    assert is_daily_quota_spent(RuntimeError(DAILY))
    assert not is_retryable(RuntimeError(DAILY))


def test_a_per_minute_limit_is_still_retried():
    """It clears in seconds; giving up on it would lose descriptions for nothing."""
    assert not is_daily_quota_spent(RuntimeError(PER_MINUTE))
    assert is_retryable(RuntimeError(PER_MINUTE))


@pytest.mark.parametrize("text", [
    "503 UNAVAILABLE. This model is currently experiencing high demand",
    "429 RESOURCE_EXHAUSTED. Rate limit reached, please slow down",
])
def test_transient_failures_are_unaffected(text):
    assert not is_daily_quota_spent(RuntimeError(text))
    assert is_retryable(RuntimeError(text))


# --------------------------------------------------------------------------
# the client, counted
# --------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch) -> dict[str, Any]:
    state: dict[str, Any] = {"calls": [], "fail_with": None}

    class Models:
        async def embed_content(self, model, contents, config=None):
            state["calls"].append(contents)
            if state["fail_with"]:
                raise RuntimeError(state["fail_with"])
            texts = contents if isinstance(contents, list) else [contents]
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.5] * 8) for _ in texts])

    settings = get_settings()
    monkeypatch.setattr(settings, "embedding_dimensions", 8)
    # Retries back off for real; no test here should wait for them.
    from app.search import retry

    monkeypatch.setattr(retry, "BASE_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(embeddings, "_client", lambda: (
        SimpleNamespace(aio=SimpleNamespace(models=Models())), settings))
    monkeypatch.setattr(embeddings, "_query_vectors", type(embeddings._query_vectors)())
    return state


async def test_a_repeated_query_is_embedded_once(client):
    first = await embeddings.embed_one("customer payment rounding")
    again = await embeddings.embed_one("customer payment rounding")

    assert first == again == [0.5] * 8
    assert client["calls"] == ["customer payment rounding"]


async def test_a_different_query_is_a_real_call(client):
    await embeddings.embed_one("rounding")
    await embeddings.embed_one("refunds")
    assert client["calls"] == ["rounding", "refunds"]


async def test_chunk_embeddings_are_not_cached_per_process(client):
    """Chunks are reused through Atlas (find_enrichment), with the model checked."""
    await embeddings.embed_one("def f(): ...", task_type=embeddings.DOCUMENT_TASK)
    await embeddings.embed_one("def f(): ...", task_type=embeddings.DOCUMENT_TASK)
    assert len(client["calls"]) == 2


async def test_a_failed_query_is_not_cached(client):
    client["fail_with"] = "400 INVALID_ARGUMENT"
    with pytest.raises(embeddings.EmbeddingUnavailable):
        await embeddings.embed_one("rounding")
    client["fail_with"] = None
    assert await embeddings.embed_one("rounding") == [0.5] * 8
    assert len(client["calls"]) == 2


async def test_the_cache_is_bounded(client, monkeypatch):
    monkeypatch.setattr(embeddings, "QUERY_CACHE_SIZE", 2)
    for query in ("a", "b", "c"):
        await embeddings.embed_one(query)
    await embeddings.embed_one("a")     # evicted, so asked again
    assert client["calls"] == ["a", "b", "c", "a"]


async def test_chunk_embedding_stops_at_a_spent_daily_quota(client):
    client["fail_with"] = DAILY
    failures: list[str] = []

    vectors = await embeddings.embed_documents([f"chunk {i}" for i in range(40)],
                                               failures=failures)

    assert vectors == [None] * 40
    assert len(client["calls"]) == 1, "one request, not one per chunk times retries"
    assert len(client["calls"][0]) == 40, "the whole batch in that one request"
    assert "RESOURCE_EXHAUSTED" in failures[0]


def rejecting(client, monkeypatch, bad: str, error: str = "400 INVALID_ARGUMENT"):
    """A client that refuses any request containing `bad`, as Gemini refuses a whole
    batch when it will not take one of its texts."""

    class Models:
        async def embed_content(self, model, contents, config=None):
            client["calls"].append(contents)
            texts = contents if isinstance(contents, list) else [contents]
            if bad in texts:
                raise RuntimeError(error)
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.5] * 8) for _ in texts])

    settings = get_settings()
    monkeypatch.setattr(embeddings, "_client", lambda: (
        SimpleNamespace(aio=SimpleNamespace(models=Models())), settings))


async def test_chunk_embedding_carries_on_past_a_one_off_failure(client, monkeypatch):
    """A rejected batch is retried text by text, so one bad chunk costs only itself."""
    rejecting(client, monkeypatch, "chunk 0")

    vectors = await embeddings.embed_documents(["chunk 0", "chunk 1", "chunk 2"])
    assert vectors[0] is None and vectors[1] and vectors[2]
    assert client["calls"][0] == ["chunk 0", "chunk 1", "chunk 2"], "batch first"


async def test_a_busy_model_is_not_retried_text_by_text(client, monkeypatch):
    """After its retries, a 503 batch would fail the same way for every single text."""
    rejecting(client, monkeypatch, "chunk 0", "503 UNAVAILABLE. high demand")

    vectors = await embeddings.embed_documents(["chunk 0", "chunk 1", "chunk 2"])
    assert vectors == [None, None, None]
    assert all(len(c) == 3 for c in client["calls"]), "only the batch, retried"


# --------------------------------------------------------------------------
# batching
# --------------------------------------------------------------------------

async def test_chess_sized_batches_are_one_request_each(client):
    vectors = await embeddings.embed_documents([f"chunk {i}" for i in range(20)])
    assert len(client["calls"]) == 1
    assert vectors == [[0.5] * 8] * 20


async def test_more_than_the_api_cap_is_split(client):
    await embeddings.embed_documents([f"chunk {i}" for i in range(250)])
    assert [len(c) for c in client["calls"]] == [100, 100, 50]


async def test_a_short_answer_is_never_zipped_onto_the_wrong_chunks(client, monkeypatch):
    """Two vectors for three texts would shift every vector onto the wrong chunk."""

    class Models:
        async def embed_content(self, model, contents, config=None):
            client["calls"].append(contents)
            texts = contents if isinstance(contents, list) else [contents]
            count = len(texts) - 1 if len(texts) > 1 else 1
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.5] * 8) for _ in range(count)])

    settings = get_settings()
    monkeypatch.setattr(embeddings, "_client", lambda: (
        SimpleNamespace(aio=SimpleNamespace(models=Models())), settings))

    with pytest.raises(embeddings.EmbeddingUnavailable, match="got 2"):
        await embeddings.embed_many(["a", "b", "c"])
    # embed_documents treats it as a rejected batch and goes text by text.
    assert await embeddings.embed_documents(["a", "b", "c"]) == [[0.5] * 8] * 3


# --------------------------------------------------------------------------
# reusing stored enrichment
# --------------------------------------------------------------------------

async def test_reuse_requires_the_same_models_and_dimensions(monkeypatch):
    """A model or dimension change must never be served the old vectors."""
    from app import search
    from app.persistence import db

    seen: dict[str, Any] = {}

    class Cursor:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class Collection:
        def find(self, query, projection):
            seen["query"] = query
            return Cursor()

    class Database:
        def __getitem__(self, _name):
            return Collection()

    async def get_db():
        return Database()

    monkeypatch.setattr(db, "get_db", get_db)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_embedding_model", "gemini-embedding-1.0")
    monkeypatch.setattr(settings, "embedding_dimensions", 768)

    assert await search.find_enrichment(["chunk_a"]) == {}
    query = seen["query"]
    assert query["chunk_id"] == {"$in": ["chunk_a"]}
    pattern = query["enriched_with"]["$regex"]
    import re

    assert re.search(pattern, "gemini-4-argon|gemini-embedding-1.0:768")
    assert re.search(pattern, "none|gemini-embedding-1.0:768")
    assert not re.search(pattern, "gemini-4-argon|gemini-embedding-1.0:1536")
    assert not re.search(pattern, "gemini-4-argon|text-embedding-005:768")
    assert query["embedding"] == {"$type": "array"}
    assert query["description"] == {"$type": "string"}
