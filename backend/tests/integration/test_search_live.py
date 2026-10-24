"""Search against real Atlas and real Gemini - owner: Dev 3. Covers T-06 and T-07.

Marked `live`, so CI deselects it. Run it with a configured `.env`:

    uv run pytest -q -m live tests/integration/test_search_live.py

T-06 and T-07 are the two acceptance criteria for this layer, and neither can be
proven offline - they are claims about what a real Lucene analyser and a real
embedding model do:

  T-06  searching `finalise_amount` lands on `pricing.finalise_amount`
  T-07  asking "where does the final customer bill get rounded?" lands on the same
        function, whose name contains none of those words

The first version of this layer failed T-06 silently. `lucene.standard` keeps
`pricing.finalise_amount` as one token, so the query matched that field not at all -
and no offline test could have known, because the definition was perfectly valid.

**Isolation.** Everything happens in a throwaway `axolotl_test_<random>` database,
dropped at the end after asserting the prefix, and the imported snapshot is deleted
from disk. The database named in `.env` is never touched. An earlier version of this
file imported straight into it and cleaned up nothing, which left nine `legacy_orders`
repositories on the shared cluster in one afternoon (caught by Dev 3).

Two consequences of a fresh database worth knowing before you run this:

  - Atlas Search indexes are **per database**, so the throwaway one has to build its
    own. That takes twenty seconds or so, once per run, and the tests skip rather than
    fail if the cluster is slower than `INDEX_TIMEOUT_SECONDS`.
  - The import is shared by every test here, because it costs one embedding call per
    chunk and the Gemini quota is finite.
"""

import asyncio
import os
import secrets
import shutil
import stat
from pathlib import Path

import pytest
from pymongo import AsyncMongoClient

from app.config import get_settings
from app.ingestion import import_repository, snapshot_path
from app.search import count_chunks, search_repository
from app.search.indexes import ensure_search_indexes, search_status, supports_native_hybrid
from tests.integration.model_replay import (
    MemoryStore,
    new_stats,
    replaying_describe_chunks,
    replaying_embed_one,
)

# No `anyio` marker: pytest-asyncio (asyncio_mode = "auto") already runs these, and
# mixing the two puts the fixture and the test on different event loops.
pytestmark = [pytest.mark.live]

ROUNDING = "pricing.finalise_amount"
TEST_DB_PREFIX = "axolotl_test_"
INDEX_TIMEOUT_SECONDS = 180.0
INDEX_POLL_SECONDS = 3.0

# Set up once for the module. The client is deliberately *not* cached: it binds to the
# event loop it was created on and each test gets its own loop, so a cached one raises
# "Cannot use AsyncMongoClient in different event loop" - which `search_status()` then
# swallows and reports as "the keyword index does not exist yet", a thoroughly
# misleading way to find out. Only the plain strings are kept.
_STATE: dict[str, str] = {}


def _force_remove(func, path, _exc):
    """Snapshots are made read-only on purpose; deleting one needs the bit cleared.

    On the folder as well as the file: removing a file needs write permission on the
    folder that holds it, and snapshot folders are 0o555 too.
    """
    os.chmod(os.path.dirname(path), stat.S_IRWXU)
    os.chmod(path, stat.S_IRWXU)
    func(path)


@pytest.fixture(scope="module", autouse=True)
def replay_gemini(request):
    """Real Gemini answers, recorded once and replayed on later runs (model_replay.py).

    The first run costs ~48 embedding calls; later runs cost none, unless the model,
    the dimensions or the text change. `pytest --cache-clear` re-checks from scratch.
    """
    from app import search
    from app.search import descriptions, embeddings

    store = getattr(request.config, "cache", None) or MemoryStore()
    stats = new_stats()
    embed_one = replaying_embed_one(embeddings.embed_one, store, stats)

    mp = pytest.MonkeyPatch()
    mp.setattr(embeddings, "embed_one", embed_one)   # chunk embeddings
    mp.setattr(search, "embed_one", embed_one)       # query embeddings
    mp.setattr(descriptions, "describe_chunks",
               replaying_describe_chunks(descriptions.describe_chunks, store, stats))
    try:
        yield stats
    finally:
        mp.undo()
        print(f"\n  Gemini calls this run: {stats}")


@pytest.fixture(scope="module", autouse=True)
def throwaway_database(replay_gemini):
    """Name the database up front and drop it, and the snapshot, at the end.

    Deliberately a *sync* fixture: teardown runs its own `asyncio.run`, with a client
    created and closed inside it, so nothing outlives the loop it belongs to.
    """
    settings = get_settings()
    if not settings.mongodb_uri:
        pytest.skip("MONGODB_URI is not set - fill .env to run live tests")

    name = TEST_DB_PREFIX + secrets.token_hex(4)
    _STATE["database"] = name
    try:
        yield name
    finally:
        assert name.startswith(TEST_DB_PREFIX), f"refusing to drop {name!r}"
        snapshot = _STATE.get("snapshot")

        async def drop() -> None:
            client = AsyncMongoClient(
                settings.mongodb_uri, serverSelectionTimeoutMS=10000,
                appname="axolotl-tests", tz_aware=True,
            )
            try:
                # Dropping the database takes its search indexes with it.
                await client.drop_database(name)
            finally:
                await client.close()

        try:
            asyncio.run(drop())
        finally:
            if snapshot:
                shutil.rmtree(Path(snapshot), onerror=_force_remove)


@pytest.fixture
async def repo_id(throwaway_database, monkeypatch):
    """A client on this test's loop, pointed at the throwaway database."""
    settings = get_settings()
    if not settings.gemini_api_key:
        pytest.skip("GEMINI_API_KEY is not set")

    from app.persistence import db

    client = AsyncMongoClient(
        settings.mongodb_uri, serverSelectionTimeoutMS=10000,
        appname="axolotl-tests", tz_aware=True,
    )
    monkeypatch.setattr(db, "_client", client)
    monkeypatch.setattr(db, "_database_name", throwaway_database)

    try:
        await _wait_for_indexes()
        if "repository" not in _STATE:
            record = await import_repository(demo_fixture_id="legacy_orders")
            _STATE["repository"] = record.repository_id
            _STATE["snapshot"] = str(snapshot_path(record.repository_id))
            # Descriptions and embeddings follow the import in the background (#28).
            # Wait here, on the loop that started them, so every test below searches
            # a fully indexed repository - and the job is not abandoned when this
            # test's loop closes.
            from app.search import pipeline

            await asyncio.gather(*list(pipeline._background))
        yield _STATE["repository"]
    finally:
        await client.close()


async def _wait_for_indexes() -> None:
    """Build the throwaway database's own search indexes, once per run."""
    if _STATE.get("indexes") == "ready":
        return
    if _STATE.get("indexes") == "failed":
        pytest.skip(_STATE["reason"])

    await ensure_search_indexes()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + INDEX_TIMEOUT_SECONDS
    status = {}
    while loop.time() < deadline:
        status = await search_status()
        if status["keyword"] == "ok" and status["vector"] == "ok":
            _STATE["indexes"] = "ready"
            return
        await asyncio.sleep(INDEX_POLL_SECONDS)

    # A slow cluster is not a code defect, so skip loudly rather than fail. Cached,
    # because otherwise every test in the file pays the full timeout again - eleven
    # tests at three minutes each is over half an hour of waiting to be told the same
    # thing once.
    reason = (f"search indexes on {_STATE['database']} were not ready within "
              f"{INDEX_TIMEOUT_SECONDS:.0f}s: {status}")
    _STATE["indexes"] = "failed"
    _STATE["reason"] = reason
    pytest.skip(reason)


def names(result) -> list[str]:
    return [hit.qualified_name for hit in result["hits"]]


async def vector_search(repo_id: str, query: str, mode: str, limit: int = 5):
    """Search, but skip rather than fail when the embedding quota is spent.

    The free tier allows 1000 `embed_content` requests a day and one import costs
    forty. When it runs out, the layer behaves exactly as designed - hybrid degrades
    to keyword and says `mode_used: "keyword"`, vector mode returns 503 - so these
    tests would fail asserting the full-capability path while nothing is broken.

    "I could not run this" and "I ran it and it was wrong" must not look the same,
    which is the same rule the search endpoints follow for a building index.
    """
    from app.errors import AppError

    try:
        result = await search_repository(repo_id, query, mode=mode, limit=limit)
    except AppError as exc:
        if "quota" in str(exc).lower() or "RESOURCE_EXHAUSTED" in str(exc):
            pytest.skip(f"embedding quota is exhausted: {exc}")
        raise

    # Hybrid degrades silently by design, so catch it by what it reports.
    if mode in {"vector", "hybrid"} and result["mode_used"] == "keyword":
        pytest.skip(
            "the embedding side is unavailable (hybrid fell back to keyword), most "
            "likely the daily embedding quota"
        )
    return result


async def test_the_fixture_is_chunked_and_stored(repo_id):
    assert await count_chunks(repo_id) > 20


async def test_t06_an_exact_identifier_is_found_by_keyword_search(repo_id):
    """The query people actually type: a bare function name, no module prefix."""
    result = await search_repository(
        repo_id, "finalise_amount", mode="keyword", limit=5
    )

    assert result["mode_used"] == "keyword"
    assert names(result)[0] == ROUNDING
    # And by a wide margin, not by a hair: the exact-name boost should put it far
    # clear of a module that merely calls it.
    assert result["hits"][0].score > 5 * result["hits"][1].score


async def test_a_partial_identifier_is_found_too(repo_id):
    """`finalise` alone matched nothing at all before the identifier analyser."""
    result = await search_repository(
        repo_id, "finalise", mode="keyword", limit=5
    )
    assert ROUNDING in names(result)


async def test_a_fully_qualified_name_is_found_exactly(repo_id):
    result = await search_repository(
        repo_id, ROUNDING, mode="keyword", limit=5
    )
    assert names(result)[0] == ROUNDING


async def test_t07_a_natural_language_question_finds_the_rounding_function(repo_id):
    """The acceptance query from the brief, verbatim.

    `finalise_amount` contains none of "final customer bill rounded" as a word a
    keyword index would match on its name.
    """
    result = await vector_search(
        repo_id, "where does the final customer bill get rounded?", "hybrid"
    )

    assert result["mode_used"] == "hybrid"
    assert ROUNDING in names(result)[:3]


async def test_a_question_sharing_no_word_with_the_code_still_finds_it(repo_id):
    """Stricter than T-07: not one content word here appears in `pricing.py`.

    "shopper", "owe" and "receipt" are absent from the module, so a keyword index
    cannot answer this at all. Only the embedding can.
    """
    result = await vector_search(
        repo_id, "what does a shopper owe on their receipt?", "vector"
    )

    assert result["mode_used"] == "vector"
    assert ROUNDING in names(result)[:3]


async def test_hybrid_uses_native_rank_fusion_on_this_cluster(repo_id):
    """Our Atlas is 8.0.34, so `$rankFusion` must be what actually ran."""
    assert await supports_native_hybrid() is True

    result = await vector_search(repo_id, "rounding the total", "hybrid")
    assert result["native_hybrid"] is True
    assert result["mode_used"] == "hybrid"


async def test_fusion_reports_how_each_engine_contributed(repo_id):
    result = await vector_search(
        repo_id, "where does the final customer bill get rounded?", "hybrid"
    )
    hit = next(h for h in result["hits"] if h.qualified_name == ROUNDING)

    # The breakdown is what lets the UI say *why* something ranked where it did.
    assert hit.keyword_rank is not None or hit.vector_rank is not None
    assert hit.match_method == "hybrid"


async def test_fusion_can_beat_keyword_search_on_its_own(repo_id):
    """The reason hybrid exists, measured rather than asserted.

    On the T-07 query the keyword index ranks the rounding function several places
    down; the vector index puts it first; fusion puts it first. If this ever stops
    being true, hybrid is costing a stage for nothing and we should know.
    """
    query = "where does the final customer bill get rounded?"
    keyword = await search_repository(
        repo_id, query, mode="keyword", limit=10
    )
    hybrid = await vector_search(repo_id, query, "hybrid", limit=10)

    keyword_names, hybrid_names = names(keyword), names(hybrid)
    assert ROUNDING in hybrid_names
    hybrid_position = hybrid_names.index(ROUNDING)
    keyword_position = (
        keyword_names.index(ROUNDING) if ROUNDING in keyword_names else 99
    )
    assert hybrid_position <= keyword_position


async def test_results_never_carry_an_embedding(repo_id):
    """768 floats per hit would dwarf the payload and help nobody."""
    result = await vector_search(repo_id, "rounding", "hybrid", limit=3)
    for hit in result["hits"]:
        assert not hasattr(hit, "embedding")
        assert hit.snippet
        assert hit.path.endswith(".py")


async def test_one_repository_cannot_see_another(repo_id):
    """Every stage filters by repository; a stale id must return nothing, not noise."""
    result = await search_repository(
        "repo_does_not_exist", "finalise_amount", mode="keyword", limit=5
    )
    assert result["hits"] == []
