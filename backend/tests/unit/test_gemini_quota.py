"""Protecting the Gemini quota (#11) - owner: Dev 3. Offline, no Gemini.

The free tier allows 1000 embedding requests a day; one chess import is 122. These pin
the places a request used to be spent for nothing:

  - a spent *daily* quota was retried four times per chunk, as if it were a momentary
    rate limit;
  - chunk embeddings carried on failing one by one after the quota was gone;
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
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.5] * 8)])

    settings = get_settings()
    monkeypatch.setattr(settings, "embedding_dimensions", 8)
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
    # The stub fails at once, so the first failure is seen before any other call
    # starts; against the real API, up to MAX_CONCURRENCY may already be in flight.
    assert len(client["calls"]) == 1, "one request, not one per chunk times retries"
    assert "RESOURCE_EXHAUSTED" in failures[0]


async def test_chunk_embedding_carries_on_past_a_one_off_failure(client, monkeypatch):
    """Only a spent quota stops the batch; one bad chunk does not cost the rest."""
    failed = {"chunk 0"}

    class Models:
        async def embed_content(self, model, contents, config=None):
            client["calls"].append(contents)
            if contents in failed:
                raise RuntimeError("400 INVALID_ARGUMENT")
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.5] * 8)])

    settings = get_settings()
    monkeypatch.setattr(embeddings, "_client", lambda: (
        SimpleNamespace(aio=SimpleNamespace(models=Models())), settings))

    vectors = await embeddings.embed_documents(["chunk 0", "chunk 1", "chunk 2"])
    assert vectors[0] is None and vectors[1] and vectors[2]


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
    assert query["enriched_with"].endswith("|gemini-embedding-1.0:768")
    assert query["enriched_with"].startswith(settings.gemini_model + "|")
    assert query["embedding"] == {"$type": "array"}
