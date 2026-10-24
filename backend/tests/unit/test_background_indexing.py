"""Background search indexing (#28) - owner: Dev 3. Offline, no Atlas, no Gemini.

An import used to hold the request until every chunk had been described and embedded:
more than two minutes for chess's 122 chunks. Now it stores the chunks, responds, and
describes and embeds them in the background, recording progress per repository.

What must stay true, and what these pin:

  - the import returns before any description or embedding is made;
  - vector and hybrid search answer 503 with the progress while that runs, never an
    empty result, and keyword search is not held up;
  - a restart cannot leave a repository "indexing" for ever, and a teammate's
    indexing on another machine is never failed from here;
  - a spent quota stops the job instead of spending a request per remaining chunk.

Gemini is replaced by stubs of `pipeline._describe` / `pipeline._embed`, and the key
is blanked so a missed stub fails closed rather than calling out.
"""

import asyncio
from typing import Any

import pytest

from app import search
from app.config import get_settings
from app.errors import AppError
from app.search import pipeline, progress
from tests.unit.test_import_pipeline import store  # noqa: F401 - pytest fixture

REPO = "repo_000000000028"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_api_key", "")
    progress._active.clear()
    yield
    progress._active.clear()


@pytest.fixture
def statuses(monkeypatch) -> dict[str, dict[str, Any]]:
    """In-memory `search_index_status`."""
    rows: dict[str, dict[str, Any]] = {}

    async def write(repo_id: str, fields: dict[str, Any]) -> None:
        rows.setdefault(repo_id, {"repository_id": repo_id}).update(fields)

    async def read(repo_id: str) -> dict[str, Any] | None:
        row = rows.get(repo_id)
        return dict(row) if row else None

    monkeypatch.setattr(progress, "_write", write)
    monkeypatch.setattr(progress, "_read", read)
    return rows


@pytest.fixture
def gemini(monkeypatch) -> dict[str, Any]:
    """Stand-ins for the two Gemini stages, with a gate to hold them mid-run."""
    state: dict[str, Any] = {
        "gate": asyncio.Event(), "describe_calls": 0, "embed_calls": 0,
        "describe": True, "embed": True, "stored": [],
    }
    state["gate"].set()

    async def describe(chunks):
        state["describe_calls"] += 1
        await state["gate"].wait()
        if not state["describe"]:
            return ["no GEMINI_API_KEY, so chunk descriptions were not generated"]
        for c in chunks:
            c["description"] = f"what {c['qualified_name']} does"
        return []

    async def embed(chunks):
        state["embed_calls"] += 1
        if not state["embed"]:
            return ["chunk embeddings failed (429 quota)"]
        for c in chunks:
            c["embedding"] = [0.1] * 4
        return []

    async def store_enrichment(repo_id, chunks):
        state["stored"].extend(dict(c) for c in chunks)
        return len(chunks)

    async def searchable(repository_id, **_):
        return True

    # Descriptions and embeddings already in Atlas for identical code (#11).
    state["earlier"] = {}

    async def find_enrichment(chunk_ids):
        return {i: state["earlier"][i] for i in chunk_ids if i in state["earlier"]}

    monkeypatch.setattr(search, "find_enrichment", find_enrichment)
    monkeypatch.setattr(pipeline, "_describe", describe)
    monkeypatch.setattr(pipeline, "_embed", embed)
    monkeypatch.setattr(search, "store_enrichment", store_enrichment)
    monkeypatch.setattr(pipeline, "wait_until_vector_searchable", searchable)
    return state


def some_chunks(count: int) -> list[dict[str, Any]]:
    return [{"chunk_id": f"chunk_{i}", "qualified_name": f"m.f{i}",
             "description": None, "embedding": None} for i in range(count)]


async def finish_background() -> None:
    await asyncio.gather(*list(pipeline._background))


# --------------------------------------------------------------------------
# the import
# --------------------------------------------------------------------------

async def test_the_import_responds_before_any_description_or_embedding(
    store, statuses, gemini, monkeypatch  # noqa: F811
):
    from app import ingestion

    if not ingestion.fixture_path("legacy_orders").is_dir():
        pytest.skip("demo fixture not present")
    monkeypatch.setattr(get_settings(), "index_on_import", True)
    gemini["gate"].clear()      # descriptions block until released

    record = await ingestion.import_repository(demo_fixture_id="legacy_orders")

    total = len(store["chunks"])
    assert total > 20, "chunks are stored before the import responds"
    assert gemini["stored"] == [], "nothing is enriched yet"
    status = statuses[record.repository_id]
    assert status["state"] == "indexing"
    assert (status["total"], status["embedded"]) == (total, 0)
    # The record does not claim enrichment was skipped: it was deferred.
    assert not [w for w in record.warnings if "INDEX_ON_IMPORT" in w]

    gemini["gate"].set()
    await finish_background()

    status = statuses[record.repository_id]
    assert status["state"] == "ready"
    assert status["embedded"] == status["described"] == total
    assert len(gemini["stored"]) == total
    assert all(c["embedding"] and c["description"] for c in gemini["stored"])


async def test_with_index_on_import_off_nothing_runs_in_the_background(
    store, statuses, gemini  # noqa: F811
):
    from app import ingestion

    if not ingestion.fixture_path("legacy_orders").is_dir():
        pytest.skip("demo fixture not present")
    # conftest turns INDEX_ON_IMPORT off for every offline test.
    record = await ingestion.import_repository(demo_fixture_id="legacy_orders")

    assert pipeline._background == set()
    assert statuses == {}
    assert gemini["describe_calls"] == gemini["embed_calls"] == 0
    assert [w for w in record.warnings if "INDEX_ON_IMPORT" in w]


# --------------------------------------------------------------------------
# the background job
# --------------------------------------------------------------------------

async def test_progress_is_recorded_batch_by_batch(statuses, gemini, monkeypatch):
    monkeypatch.setattr(pipeline, "ENRICH_BATCH_SIZE", 2)
    seen: list[int] = []
    original = progress.advance

    async def advance(repo_id, *, described, embedded):
        seen.append(embedded)
        await original(repo_id, described=described, embedded=embedded)

    monkeypatch.setattr(progress, "advance", advance)

    assert await pipeline.schedule_enrichment(REPO, some_chunks(5)) == []
    await finish_background()

    assert seen == [2, 4, 5]
    assert statuses[REPO]["state"] == "ready"


async def test_a_spent_quota_stops_the_job_instead_of_trying_every_chunk(
    statuses, gemini, monkeypatch
):
    """#11: each failed request still counts against the daily quota."""
    monkeypatch.setattr(pipeline, "ENRICH_BATCH_SIZE", 2)
    gemini["describe"] = False
    gemini["embed"] = False

    await pipeline.schedule_enrichment(REPO, some_chunks(10))
    await finish_background()

    assert gemini["embed_calls"] == 1, "stopped after the first batch"
    assert gemini["describe_calls"] == 1, "descriptions stopped after the first batch"
    status = statuses[REPO]
    assert status["state"] == "failed"
    assert "embedding stopped after 0 of 10" in status["reason"]
    assert not progress.is_active(REPO), "a failed job can be run again"


async def test_descriptions_failing_does_not_stop_the_embeddings(statuses, gemini,
                                                                 monkeypatch):
    monkeypatch.setattr(pipeline, "ENRICH_BATCH_SIZE", 2)
    gemini["describe"] = False

    await pipeline.schedule_enrichment(REPO, some_chunks(5))
    await finish_background()

    assert gemini["describe_calls"] == 1
    assert statuses[REPO]["state"] == "ready"
    assert (statuses[REPO]["described"], statuses[REPO]["embedded"]) == (0, 5)


async def test_identical_code_reuses_what_atlas_already_holds(statuses, gemini,
                                                                monkeypatch):
    """#11: re-importing chess must not pay 244 Gemini calls for answers in Atlas."""
    monkeypatch.setattr(pipeline, "ENRICH_BATCH_SIZE", 2)
    gemini["earlier"] = {f"chunk_{i}": {"description": f"earlier {i}",
                                        "embedding": [0.2] * 4} for i in range(3)}

    await pipeline.schedule_enrichment(REPO, some_chunks(4))
    await finish_background()

    assert (gemini["describe_calls"], gemini["embed_calls"]) == (1, 1), \
        "only chunk_3 went to Gemini"
    by_id = {c["chunk_id"]: c for c in gemini["stored"]}
    assert by_id["chunk_0"]["description"] == "earlier 0"
    assert by_id["chunk_3"]["description"] == "what m.f3 does"
    assert statuses[REPO]["state"] == "ready"
    assert statuses[REPO]["embedded"] == 4


async def test_everything_reused_means_no_gemini_call_at_all(statuses, gemini):
    gemini["earlier"] = {f"chunk_{i}": {"description": "d", "embedding": [0.2] * 4}
                         for i in range(3)}

    await pipeline.schedule_enrichment(REPO, some_chunks(3))
    await finish_background()

    assert gemini["describe_calls"] == gemini["embed_calls"] == 0
    assert statuses[REPO]["state"] == "ready"


async def test_a_crash_mid_job_is_recorded_as_failed(statuses, gemini, monkeypatch):
    async def broken(repo_id, chunks):
        raise RuntimeError("write conflict")

    monkeypatch.setattr(search, "store_enrichment", broken)

    await pipeline.schedule_enrichment(REPO, some_chunks(3))
    await finish_background()

    assert statuses[REPO]["state"] == "failed"
    assert "write conflict" in statuses[REPO]["reason"]
    assert not progress.is_active(REPO)


async def test_a_repository_is_never_indexed_twice_at_once(statuses, gemini):
    gemini["gate"].clear()
    assert await pipeline.schedule_enrichment(REPO, some_chunks(3)) == []

    warnings = await pipeline.schedule_enrichment(REPO, some_chunks(3))
    assert warnings == ["this repository is already being indexed"]
    assert len(pipeline._background) == 1

    with pytest.raises(AppError) as caught:
        await pipeline._refuse_if_indexing(REPO)
    assert (caught.value.status_code, caught.value.code) == (409, "INDEXING_IN_PROGRESS")

    gemini["gate"].set()
    await finish_background()
    await pipeline._refuse_if_indexing(REPO)    # finished: may run again


async def test_no_status_store_means_no_job_and_says_so(gemini, monkeypatch):
    async def unreachable(repo_id, fields):
        raise RuntimeError("Atlas went away")

    monkeypatch.setattr(progress, "_write", unreachable)

    warnings = await pipeline.schedule_enrichment(REPO, some_chunks(3))

    assert "were not started" in warnings[0]
    assert pipeline._background == set()
    assert not progress.is_active(REPO)


# --------------------------------------------------------------------------
# restarts
# --------------------------------------------------------------------------

def a_row(**fields: Any) -> dict[str, Any]:
    return {"repository_id": REPO, "state": "indexing", "embedded": 40, "total": 122,
            "host": progress.HOST, "process_id": progress.PROCESS_ID, **fields}


async def test_indexing_left_by_a_previous_server_process_reads_as_failed(statuses):
    statuses[REPO] = a_row(process_id="a-process-that-has-since-stopped")

    status = await progress.get(REPO)

    assert status["state"] == "failed"
    assert "interrupted" in status["reason"]
    assert statuses[REPO]["state"] == "failed", "the stored row is corrected too"
    assert "host" not in status and "process_id" not in status


async def test_indexing_this_process_is_not_running_reads_as_failed(statuses):
    statuses[REPO] = a_row()        # this process, but nothing active
    assert (await progress.get(REPO))["state"] == "failed"


async def test_indexing_this_process_is_running_stays_indexing(statuses):
    statuses[REPO] = a_row()
    progress.claim(REPO)
    assert (await progress.get(REPO))["state"] == "indexing"


async def test_a_teammates_indexing_on_another_machine_is_left_alone(statuses):
    """Shared cluster, machine-local snapshots: only the importer can index."""
    statuses[REPO] = a_row(host="someone-elses-laptop", process_id="theirs")

    assert (await progress.get(REPO))["state"] == "indexing"
    assert statuses[REPO]["state"] == "indexing"


async def test_an_interrupted_repository_can_be_indexed_again(statuses):
    statuses[REPO] = a_row(process_id="a-process-that-has-since-stopped")
    await pipeline._refuse_if_indexing(REPO)    # does not raise


# --------------------------------------------------------------------------
# search while indexing
# --------------------------------------------------------------------------

@pytest.fixture
def searchable(monkeypatch) -> dict[str, Any]:
    """Indexes ready; the engines answer with one hit each, so an empty list could
    only come from the gate - which must never produce one."""
    calls: dict[str, Any] = {"keyword": 0, "vector": 0}

    class Database:
        def __getitem__(self, _name: str) -> None:
            return None

    async def get_db():
        return Database()

    async def search_status():
        return {"keyword": "ok", "vector": "ok"}

    async def keyword(collection, repo_id, query, limit):
        calls["keyword"] += 1
        return ["hit"]

    async def vector(collection, repo_id, query, limit):
        calls["vector"] += 1
        return ["hit"]

    from app.persistence import db

    monkeypatch.setattr(db, "get_db", get_db)
    monkeypatch.setattr(search, "search_status", search_status)
    monkeypatch.setattr(search, "_keyword", keyword)
    monkeypatch.setattr(search, "_vector", vector)
    return calls


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
async def test_vector_and_hybrid_are_a_503_with_progress_while_indexing(
    statuses, searchable, mode
):
    statuses[REPO] = a_row()
    progress.claim(REPO)

    with pytest.raises(AppError) as caught:
        await search.search_repository(REPO, "customer payment rounding", mode=mode)

    error = caught.value
    assert (error.status_code, error.code) == (503, "SEARCH_INDEX_NOT_READY")
    assert error.details["state"] == "indexing"
    assert (error.details["embedded"], error.details["total"]) == (40, 122)
    assert "40 of 122" in error.message
    assert searchable["vector"] == 0


async def test_keyword_search_works_while_indexing(statuses, searchable):
    statuses[REPO] = a_row()
    progress.claim(REPO)

    result = await search.search_repository(REPO, "finalise_amount", mode="keyword")
    assert result["hits"] == ["hit"]


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
async def test_failed_indexing_is_a_503_with_the_reason(statuses, searchable, mode):
    statuses[REPO] = a_row(state="failed", reason="embedding stopped after 0 of 122")

    with pytest.raises(AppError) as caught:
        await search.search_repository(REPO, "rounding", mode=mode)

    assert caught.value.status_code == 503
    assert caught.value.details["reason"] == "embedding stopped after 0 of 122"
    assert "POST /repositories/{id}/index" in caught.value.message


async def test_a_ready_repository_searches_normally(statuses, searchable):
    statuses[REPO] = a_row(state="ready", embedded=122)

    result = await search.search_repository(REPO, "rounding", mode="vector")
    assert result["hits"] == ["hit"]


async def test_a_repository_never_indexed_in_the_background_searches_as_before(
    statuses, searchable
):
    """Imported before #28, or with INDEX_ON_IMPORT off: no status document."""
    result = await search.search_repository(REPO, "rounding", mode="vector")
    assert result["hits"] == ["hit"]
