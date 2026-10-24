"""Descriptions off at import, and their own model (#11) - owner: Dev 3. Offline.

A chunk description is one `generate_content` call per chunk on the agent's model:
122 for chess, from the same quota the migration run needs. Two settings:

  INDEX_DESCRIPTIONS        off by default: an import embeds chunks from their source,
                            identifiers and docstring, and makes no description call.
                            POST /repositories/{id}/index still adds descriptions.
  GEMINI_DESCRIPTION_MODEL  a separate (cheap) model for descriptions; unset means
                            GEMINI_MODEL, as before.

No Gemini: the client is a stand-in and the key is a placeholder.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from app import search
from app.config import get_settings
from app.search import pipeline
from app.search.settings import SearchSettings, description_model, search_settings
from tests.unit.test_background_indexing import (  # noqa: F401 - pytest fixtures
    REPO,
    finish_background,
    offline,
    some_chunks,
    statuses,
)


@pytest.fixture
def gemini(monkeypatch) -> dict[str, Any]:
    """Counting stand-ins for both Gemini stages; INDEX_DESCRIPTIONS at its default."""
    state: dict[str, Any] = {"describe": 0, "embed": 0, "stored": [], "earlier": {},
                             "need_description": None}

    async def describe(chunks):
        state["describe"] += 1
        for c in chunks:
            c["description"] = "new description"
        return []

    async def embed(chunks):
        state["embed"] += 1
        for c in chunks:
            c["embedding"] = [0.1] * 4
        return []

    async def store_enrichment(repo_id, chunks):
        state["stored"].extend(dict(c) for c in chunks)
        return len(chunks)

    async def find_enrichment(chunk_ids, *, need_description=True):
        state["need_description"] = need_description
        return {i: state["earlier"][i] for i in chunk_ids if i in state["earlier"]}

    async def searchable(repository_id, **_):
        return True

    monkeypatch.setattr(pipeline, "_describe", describe)
    monkeypatch.setattr(pipeline, "_embed", embed)
    monkeypatch.setattr(search, "store_enrichment", store_enrichment)
    monkeypatch.setattr(search, "find_enrichment", find_enrichment)
    monkeypatch.setattr(pipeline, "wait_until_vector_searchable", searchable)
    monkeypatch.setattr(search_settings(), "index_descriptions", False)
    monkeypatch.setattr(search_settings(), "gemini_description_model", "")
    return state


# --------------------------------------------------------------------------
# the settings
# --------------------------------------------------------------------------

def test_both_settings_default_to_the_cheap_and_unchanged_choices(monkeypatch):
    monkeypatch.delenv("INDEX_DESCRIPTIONS", raising=False)
    monkeypatch.delenv("GEMINI_DESCRIPTION_MODEL", raising=False)
    settings = SearchSettings(_env_file=None)
    assert settings.index_descriptions is False
    assert settings.gemini_description_model == ""


def test_both_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("INDEX_DESCRIPTIONS", "true")
    monkeypatch.setenv("GEMINI_DESCRIPTION_MODEL", "gemini-flash-lite")
    settings = SearchSettings(_env_file=None)
    assert settings.index_descriptions is True
    assert settings.gemini_description_model == "gemini-flash-lite"


def test_the_description_model_defaults_to_gemini_model(monkeypatch):
    monkeypatch.setattr(search_settings(), "gemini_description_model", "")
    assert description_model() == get_settings().gemini_model

    monkeypatch.setattr(search_settings(), "gemini_description_model", "gemini-flash")
    assert description_model() == "gemini-flash"


async def test_descriptions_are_requested_from_the_description_model(monkeypatch):
    """The point of the setting: the agent's model is not the one asked."""
    import google.genai

    from app.search.descriptions import describe_chunks

    asked: list[str] = []

    class Models:
        async def generate_content(self, model, contents, **_):
            asked.append(model)
            return SimpleNamespace(text="Rounds the final bill.")

    class Client:
        def __init__(self, *_, **__):
            self.aio = SimpleNamespace(models=Models())

    monkeypatch.setattr(google.genai, "Client", Client)
    monkeypatch.setattr(get_settings(), "gemini_api_key", "placeholder-not-a-key")
    monkeypatch.setattr(get_settings(), "gemini_model", "gemini-4-argon")
    monkeypatch.setattr(search_settings(), "gemini_description_model", "gemini-flash")

    chunks = [{"qualified_name": "pricing.finalise_amount", "path": "pricing.py",
               "source": "def finalise_amount(x): return round(x, 2)"}]
    assert await describe_chunks(chunks) == []
    assert asked == ["gemini-flash"]
    assert chunks[0]["description"] == "Rounds the final bill."


# --------------------------------------------------------------------------
# import: descriptions off by default
# --------------------------------------------------------------------------

async def test_by_default_an_import_makes_no_description_call(statuses, gemini):  # noqa: F811
    await pipeline.schedule_enrichment(REPO, some_chunks(45))
    await finish_background()

    assert gemini["describe"] == 0
    assert gemini["embed"] == 3, "45 chunks in batches of 20"
    status = statuses[REPO]
    assert status["state"] == "ready"
    assert (status["described"], status["embedded"]) == (0, 45)
    assert any("INDEX_DESCRIPTIONS" in w for w in status["warnings"])
    assert all(c["description"] is None for c in gemini["stored"])


async def test_with_descriptions_on_the_import_describes(statuses, gemini,  # noqa: F811
                                                         monkeypatch):
    monkeypatch.setattr(search_settings(), "index_descriptions", True)

    await pipeline.schedule_enrichment(REPO, some_chunks(5))
    await finish_background()

    assert gemini["describe"] == 1
    assert statuses[REPO]["described"] == 5
    assert gemini["need_description"] is True


async def test_off_still_reuses_a_stored_description(statuses, gemini):  # noqa: F811
    """Already paid for, so it is free to keep - and it searches better."""
    gemini["earlier"] = {"chunk_0": {"description": "earlier", "embedding": [0.2] * 4}}

    await pipeline.schedule_enrichment(REPO, some_chunks(2))
    await finish_background()

    assert gemini["need_description"] is False
    by_id = {c["chunk_id"]: c for c in gemini["stored"]}
    assert by_id["chunk_0"]["description"] == "earlier"
    assert statuses[REPO]["described"] == 1
    assert statuses[REPO]["embedded"] == 2


# --------------------------------------------------------------------------
# POST /repositories/{id}/index adds descriptions whatever the setting
# --------------------------------------------------------------------------

async def test_reindex_describes_even_with_descriptions_off(gemini, monkeypatch):
    from app.models.contracts import SymbolRecord

    symbol = SymbolRecord(
        symbol_id="sym_0000000000000001", repository_id=REPO,
        snapshot_hash="sha256:abc", module_id="module_pricing", path="pricing.py",
        kind="function", qualified_name="pricing.finalise_amount", start_line=1,
        end_line=2, signature="(x)", docstring=None, decorators=[], bases=[],
        parent_symbol_id=None,
    )
    persisted: dict[str, Any] = {}

    async def persist(repo_id, chunks):
        persisted["chunks"] = chunks
        return len(chunks)

    async def link(repo_id, mapping):
        return len(mapping)

    async def searchable(repository_id, **_):
        return True

    from app.persistence import graphstore

    monkeypatch.setattr(search, "persist_chunks", persist)
    monkeypatch.setattr(graphstore, "set_source_chunk_ids", link)
    monkeypatch.setattr(pipeline, "wait_until_searchable", searchable)

    result = await pipeline.index_repository(
        REPO, [symbol], {"module_pricing": "def finalise_amount(x):\n    return x\n"},
        enrich=True,
    )

    assert gemini["describe"] == 1
    assert result["described"] == 1
    (chunk,) = persisted["chunks"]
    # Tagged, so a later import of the same code reuses it instead of paying again.
    assert chunk["enriched_with"] == search.enrichment_tag(described=True)


# --------------------------------------------------------------------------
# tags
# --------------------------------------------------------------------------

def test_the_tag_says_whether_a_description_was_embedded(monkeypatch):
    monkeypatch.setattr(search_settings(), "gemini_description_model", "gemini-flash")
    monkeypatch.setattr(get_settings(), "gemini_embedding_model", "gemini-embedding-1.0")
    monkeypatch.setattr(get_settings(), "embedding_dimensions", 768)

    assert search.chunk_tag({"embedding": [0.1], "description": "d"}) == \
        "gemini-flash|gemini-embedding-1.0:768"
    assert search.chunk_tag({"embedding": [0.1], "description": None}) == \
        "none|gemini-embedding-1.0:768"
    assert search.chunk_tag({"embedding": None, "description": "d"}) is None


async def test_reuse_without_descriptions_prefers_a_described_chunk(monkeypatch):
    from app.persistence import db

    rows = [
        {"chunk_id": "c", "description": None, "embedding": [0.1]},
        {"chunk_id": "c", "description": "paid for already", "embedding": [0.2]},
    ]
    seen: dict[str, Any] = {}

    class Cursor:
        def __init__(self):
            self.rows = iter(rows)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.rows)
            except StopIteration:
                raise StopAsyncIteration from None

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

    found = await search.find_enrichment(["c"], need_description=False)
    assert "description" not in seen["query"]
    assert found["c"]["description"] == "paid for already"


# --------------------------------------------------------------------------
# pacing (GEMINI_DESCRIPTIONS_PER_MINUTE)
# --------------------------------------------------------------------------

async def test_unpaced_by_default(monkeypatch):
    from app.search import descriptions

    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(descriptions.asyncio, "sleep", sleep)
    pacer = descriptions._Pacer()
    for _ in range(10):
        await pacer.wait(0)
    assert slept == []


async def test_calls_are_spaced_evenly_across_concurrent_callers(monkeypatch):
    """Five workers asking at once get five slots twelve seconds apart, not a burst."""
    import asyncio

    from app.search import descriptions

    clock = {"now": 1000.0}
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(round(seconds, 6))

    monkeypatch.setattr(descriptions.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(descriptions.asyncio, "sleep", sleep)
    pacer = descriptions._Pacer()

    await asyncio.gather(*(pacer.wait(5) for _ in range(5)))
    assert sorted(slept) == [12.0, 24.0, 36.0, 48.0], "first goes at once"


async def test_a_quiet_spell_does_not_bank_a_burst(monkeypatch):
    from app.search import descriptions

    clock = {"now": 1000.0}
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(descriptions.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(descriptions.asyncio, "sleep", sleep)
    pacer = descriptions._Pacer()

    await pacer.wait(5)
    clock["now"] += 600          # ten quiet minutes
    await pacer.wait(5)
    await pacer.wait(5)
    assert slept == [12.0], "after a pause, one goes at once and the next waits"


async def test_every_description_request_takes_a_slot(monkeypatch):
    import google.genai

    from app.search import descriptions

    waits: list[int] = []

    async def wait(self, per_minute):
        waits.append(per_minute)

    class Models:
        async def generate_content(self, model, contents, **_):
            return SimpleNamespace(text="d")

    class Client:
        def __init__(self, *_, **__):
            self.aio = SimpleNamespace(models=Models())

    monkeypatch.setattr(descriptions._Pacer, "wait", wait)
    monkeypatch.setattr(google.genai, "Client", Client)
    monkeypatch.setattr(get_settings(), "gemini_api_key", "placeholder-not-a-key")
    monkeypatch.setattr(search_settings(), "gemini_descriptions_per_minute", 5)

    chunks = [{"qualified_name": f"m.f{i}", "path": "m.py", "source": "x"}
              for i in range(3)]
    await descriptions.describe_chunks(chunks)
    assert waits == [5, 5, 5]


def test_the_rate_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_DESCRIPTIONS_PER_MINUTE", "5")
    assert SearchSettings(_env_file=None).gemini_descriptions_per_minute == 5


# --------------------------------------------------------------------------
# hangs and dropped connections
# --------------------------------------------------------------------------

def flaky_flash(monkeypatch, behaviours: list[str]):
    """A stand-in client whose successive calls 'hang', 'drop' or answer 'ok'."""
    import asyncio

    import google.genai
    import httpx

    from app.search import descriptions, retry

    calls: list[str] = []

    class Models:
        async def generate_content(self, model, contents, **_):
            behaviour = behaviours[min(len(calls), len(behaviours) - 1)]
            calls.append(behaviour)
            if behaviour == "hang":
                await asyncio.sleep(3600)
            if behaviour == "drop":
                raise httpx.ReadError("connection dropped")
            return SimpleNamespace(text="Rounds the final bill.")

    class Client:
        def __init__(self, *_, **__):
            self.aio = SimpleNamespace(models=Models())

    monkeypatch.setattr(google.genai, "Client", Client)
    monkeypatch.setattr(get_settings(), "gemini_api_key", "placeholder-not-a-key")
    monkeypatch.setattr(search_settings(), "gemini_descriptions_per_minute", 0)
    monkeypatch.setattr(descriptions, "DESCRIPTION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(retry, "BASE_DELAY_SECONDS", 0.0)
    return calls


def a_chunk():
    return [{"qualified_name": "pricing.finalise_amount", "path": "pricing.py",
             "source": "def finalise_amount(x): return round(x, 2)"}]


async def test_a_dropped_connection_is_retried_once(monkeypatch):
    from app.search.descriptions import describe_chunks

    calls = flaky_flash(monkeypatch, ["drop", "ok"])
    chunks = a_chunk()
    assert await describe_chunks(chunks) == []
    assert calls == ["drop", "ok"]
    assert chunks[0]["description"] == "Rounds the final bill."


async def test_a_connection_that_keeps_dropping_is_given_up_after_one_retry(monkeypatch):
    from app.search.descriptions import describe_chunks

    calls = flaky_flash(monkeypatch, ["drop"])
    chunks = a_chunk()
    warnings = await describe_chunks(chunks)
    assert calls == ["drop", "drop"], "exactly one retry, two requests"
    assert "description" not in chunks[0]
    assert any("ReadError" in w for w in warnings)


async def test_a_hanging_call_times_out_instead_of_holding_a_slot(monkeypatch):
    from app.search.descriptions import describe_chunks

    calls = flaky_flash(monkeypatch, ["hang", "ok"])
    chunks = a_chunk()
    assert await describe_chunks(chunks) == []
    assert calls == ["hang", "ok"], "timed out, then retried as transient"
    assert chunks[0]["description"] == "Rounds the final bill."


async def test_each_attempt_takes_its_own_paced_slot(monkeypatch):
    from app.search import descriptions

    calls = flaky_flash(monkeypatch, ["drop", "ok"])
    waits: list[int] = []

    async def wait(self, per_minute):
        waits.append(per_minute)

    monkeypatch.setattr(descriptions._Pacer, "wait", wait)
    monkeypatch.setattr(search_settings(), "gemini_descriptions_per_minute", 5)
    await descriptions.describe_chunks(a_chunk())
    assert len(calls) == 2 and waits == [5, 5]
