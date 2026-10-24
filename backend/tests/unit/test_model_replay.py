"""Record-and-replay for live-test Gemini calls - owner: Dev 3. No network.

The "real" model here is a counting stand-in: these tests check the replay bookkeeping,
not the model. The live search test is what uses the actual model through it.
"""

import pytest

from app.config import get_settings
from tests.integration.model_replay import (
    MemoryStore,
    new_stats,
    replaying_describe_chunks,
    replaying_embed_one,
)


@pytest.fixture
def model(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_embedding_model", "gemini-embedding-001")
    monkeypatch.setattr(settings, "embedding_dimensions", 768)
    monkeypatch.setattr(settings, "gemini_model", "gemini-3.8-flash")
    return settings


def counting_embedder():
    calls = []

    async def real(text, *, task_type="RETRIEVAL_QUERY"):
        calls.append((text, task_type))
        return [float(len(text)), float(len(calls))]

    return real, calls


async def test_the_first_call_is_real_and_the_second_is_replayed(model):
    real, calls = counting_embedder()
    stats = new_stats()
    embed = replaying_embed_one(real, MemoryStore(), stats)

    first = await embed("customer payment rounding")
    second = await embed("customer payment rounding")

    assert len(calls) == 1
    assert first == second
    assert stats["embeddings_called"] == 1 and stats["embeddings_replayed"] == 1


async def test_anything_that_changes_what_would_be_sent_is_a_real_call(model):
    real, calls = counting_embedder()
    embed = replaying_embed_one(real, MemoryStore(), new_stats())

    await embed("rounding")
    await embed("rounding", task_type="RETRIEVAL_DOCUMENT")  # different task
    await embed("rounding!")                                  # different text
    model.embedding_dimensions = 1536
    await embed("rounding")                                   # different dimensions
    model.gemini_embedding_model = "another-embedding-model"
    await embed("rounding")                                   # different model

    assert len(calls) == 5


async def test_a_failed_call_is_not_recorded(model):
    attempts = []

    async def flaky(text, *, task_type="RETRIEVAL_QUERY"):
        attempts.append(text)
        if len(attempts) == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return [1.0]

    store, stats = MemoryStore(), new_stats()
    embed = replaying_embed_one(flaky, store, stats)

    with pytest.raises(RuntimeError):
        await embed("rounding")
    assert await embed("rounding") == [1.0]
    assert len(attempts) == 2  # retried for real, not replayed as a failure


async def test_the_store_survives_across_wrappers_like_runs_do(model):
    """Two wrappers on one store stand in for two test runs on one pytest cache."""
    real, calls = counting_embedder()
    store = MemoryStore()

    await replaying_embed_one(real, store, new_stats())("rounding")
    stats = new_stats()
    await replaying_embed_one(real, store, stats)("rounding")

    assert len(calls) == 1
    assert stats == {**new_stats(), "embeddings_replayed": 1}


def chunk(name, source="def f():\n    pass"):
    return {"path": "pricing.py", "qualified_name": name, "source": source,
            "description": None}


async def test_descriptions_are_recorded_and_only_new_chunks_are_described(model):
    described = []

    async def real(chunks):
        for c in chunks:
            described.append(c["qualified_name"])
            c["description"] = f"Describes {c['qualified_name']}."
        return []

    store = MemoryStore()
    await replaying_describe_chunks(real, store, new_stats())([chunk("a"), chunk("b")])

    stats = new_stats()
    batch = [chunk("a"), chunk("b"), chunk("c")]
    await replaying_describe_chunks(real, store, stats)(batch)

    assert described == ["a", "b", "c"]  # a and b only once
    assert [c["description"] for c in batch] == [
        "Describes a.", "Describes b.", "Describes c.",
    ]
    assert stats["descriptions_replayed"] == 2 and stats["descriptions_called"] == 1


async def test_a_changed_source_is_described_again(model):
    described = []

    async def real(chunks):
        for c in chunks:
            described.append(c["source"])
            c["description"] = "x"
        return []

    store = MemoryStore()
    await replaying_describe_chunks(real, store, new_stats())([chunk("a", "v1")])
    await replaying_describe_chunks(real, store, new_stats())([chunk("a", "v2")])
    assert described == ["v1", "v2"]


async def test_failed_descriptions_are_not_recorded_and_warnings_pass_through(model):
    async def quota_gone(chunks):
        return ["the Gemini quota ran out after 0 of 1 chunk description(s)"]

    store, stats = MemoryStore(), new_stats()
    batch = [chunk("a")]
    warnings = await replaying_describe_chunks(quota_gone, store, stats)(batch)

    assert warnings == ["the Gemini quota ran out after 0 of 1 chunk description(s)"]
    assert batch[0]["description"] is None
    assert stats["descriptions_called"] == 0


async def test_nothing_reaches_the_model_when_everything_is_recorded(model):
    async def must_not_be_called(chunks):
        raise AssertionError("the real model was called")

    async def real(chunks):
        for c in chunks:
            c["description"] = "recorded"
        return []

    store = MemoryStore()
    await replaying_describe_chunks(real, store, new_stats())([chunk("a")])
    batch = [chunk("a")]
    await replaying_describe_chunks(must_not_be_called, store, new_stats())(batch)
    assert batch[0]["description"] == "recorded"


# --------------------------------------------------------------------------
# the hook points are the ones the real code goes through
# --------------------------------------------------------------------------
# If any of these broke, the live test would silently bypass the replay and spend
# quota again. Each patches the exact attribute test_search_live.py patches.

async def test_chunk_embeddings_go_through_embeddings_embed_many(monkeypatch):
    from app.search import embeddings

    seen = []

    async def fake(texts, *, task_type="RETRIEVAL_DOCUMENT"):
        seen.append((list(texts), task_type))
        return [[0.0] for _ in texts]

    monkeypatch.setattr(embeddings, "embed_many", fake)
    assert await embeddings.embed_documents(["a", "b"]) == [[0.0], [0.0]]
    assert seen == [(["a", "b"], embeddings.DOCUMENT_TASK)], "one batched request"


async def test_batched_embeddings_replay_and_only_ask_for_what_is_missing():
    from tests.integration.model_replay import replaying_embed_many

    asked: list[list[str]] = []

    async def real(texts, *, task_type="RETRIEVAL_DOCUMENT"):
        asked.append(list(texts))
        return [[float(len(t))] for t in texts]

    store, stats = MemoryStore(), new_stats()
    embed_many = replaying_embed_many(real, store, stats)

    assert await embed_many(["a", "bb"]) == [[1.0], [2.0]]
    assert await embed_many(["bb", "ccc", "a"]) == [[2.0], [3.0], [1.0]]
    assert asked == [["a", "bb"], ["ccc"]]
    assert stats["embeddings_called"] == 3 and stats["embeddings_replayed"] == 2


async def test_a_text_recorded_one_by_one_replays_in_a_batch():
    """The fallback path and the batched path share keys."""
    from tests.integration.model_replay import replaying_embed_many

    async def one(text, *, task_type="RETRIEVAL_QUERY"):
        return [7.0]

    async def many(texts, *, task_type="RETRIEVAL_DOCUMENT"):
        raise AssertionError("should have replayed")

    store = MemoryStore()
    from app.search.embeddings import DOCUMENT_TASK

    await replaying_embed_one(one, store, new_stats())("a", task_type=DOCUMENT_TASK)
    assert await replaying_embed_many(many, store, new_stats())(["a"]) == [[7.0]]


async def test_query_embeddings_go_through_search_embed_one(monkeypatch):
    import app.search as search

    async def fake(text, *, task_type="RETRIEVAL_QUERY"):
        return [42.0]

    monkeypatch.setattr(search, "embed_one", fake)
    assert await search._embed_query("customer payment rounding") == [42.0]


async def test_descriptions_go_through_descriptions_describe_chunks(monkeypatch):
    from app.search import descriptions, pipeline

    async def fake(chunks):
        return ["from the fake"]

    monkeypatch.setattr(descriptions, "describe_chunks", fake)
    assert await pipeline._describe([{"qualified_name": "a"}]) == ["from the fake"]
