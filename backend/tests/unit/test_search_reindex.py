"""Re-indexing a repository - owner: Dev 3. Offline.

`POST /repositories/{id}/index` re-chunks from the **on-disk snapshot**, and that
snapshot is local to whoever imported the repository. Atlas holds the symbols; the
sources live on one laptop.

That caught me out for real. Dev 3 imported `legacy_orders` into `axolotl` on her
machine and asked me to run the index, since I have the Gemini key. On my machine the
snapshot does not exist, every source read failed, `build_chunks` saw no sources, and
the endpoint returned **200 with `chunks=0` and "no indexable symbols"** - wrong twice
over, because all forty symbols were present and the repository was perfectly healthy.

It now refuses with 409 `SNAPSHOT_UNAVAILABLE` and says what to do instead.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.errors import AppError
from app.models.contracts import RepositoryRecord, SymbolRecord
from app.search import pipeline

pytestmark = pytest.mark.anyio

REPO = "repo_000000000001"
SOURCE = """def f0():
    return 1
"""


def a_symbol(index: int = 0) -> SymbolRecord:
    return SymbolRecord(
        symbol_id=f"sym_{index:016d}",
        repository_id=REPO,
        snapshot_hash="sha256:abc",
        module_id="module_pricing",
        path="pricing.py",
        kind="function",
        qualified_name=f"pricing.f{index}",
        start_line=1,
        end_line=3,
        signature="()",
        docstring=None,
        decorators=[],
        bases=[],
        parent_symbol_id=None,
    )


@pytest.fixture
def repository(monkeypatch, tmp_path):
    """Atlas has the repository and its symbols; the snapshot is configurable."""
    state: dict[str, Any] = {"symbols": [a_symbol()], "root": tmp_path / "snapshots"}

    async def get_repository(repo_id: str) -> RepositoryRecord:
        return RepositoryRecord(
            repository_id=repo_id, name="legacy_orders", snapshot_hash="sha256:abc",
            origin="demo_fixture", executable=True, demo_fixture_id="legacy_orders",
            file_count=1, module_count=1, symbol_count=len(state["symbols"]),
            test_file_count=0, analysis_status="ok",
            created_at=datetime.now(UTC),
        )

    async def list_symbols(repo_id: str, module_id: str | None = None):
        return state["symbols"]

    from app.persistence import graphstore

    monkeypatch.setattr(graphstore, "get_repository", get_repository)
    monkeypatch.setattr(graphstore, "list_symbols", list_symbols)

    import app.ingestion as ingestion

    monkeypatch.setattr(ingestion, "snapshot_path", lambda repo_id: state["root"])

    # A re-index records its progress in `search_index_status` (#28).
    from app.search import progress

    statuses: dict[str, dict[str, Any]] = {}
    state["statuses"] = statuses

    async def write(repo_id: str, fields: dict[str, Any]) -> None:
        statuses.setdefault(repo_id, {"repository_id": repo_id}).update(fields)

    async def read(repo_id: str) -> dict[str, Any] | None:
        return statuses.get(repo_id)

    monkeypatch.setattr(progress, "_write", write)
    monkeypatch.setattr(progress, "_read", read)
    return state


async def test_a_repository_imported_elsewhere_is_a_409_not_a_quiet_success(repository):
    """The snapshot directory does not exist on this machine."""
    assert not repository["root"].exists()

    with pytest.raises(AppError) as caught:
        await pipeline.reindex_repository(REPO)

    assert caught.value.status_code == 409
    assert caught.value.code == "SNAPSHOT_UNAVAILABLE"
    # Actionable: the reader has to know that re-importing is the way out.
    assert "import it again" in caught.value.message
    assert caught.value.details["symbols"] == 1


async def test_the_error_is_not_confused_with_an_unknown_repository(repository):
    """404 would say the repository does not exist. It does - in Atlas."""
    with pytest.raises(AppError) as caught:
        await pipeline.reindex_repository(REPO)
    assert caught.value.code != "NOT_FOUND"
    assert caught.value.status_code != 404


async def test_a_repository_with_no_symbols_at_all_is_not_a_snapshot_error(repository):
    """Nothing to chunk is a different thing from nowhere to chunk from."""
    repository["symbols"] = []
    repository["root"].mkdir(parents=True)

    result = await pipeline.reindex_repository(REPO)
    assert result["chunks"] == 0
    assert "no indexable symbols" in result["warnings"][0]


async def test_a_present_snapshot_is_chunked(repository, monkeypatch):
    root: Path = repository["root"]
    root.mkdir(parents=True)
    (root / "pricing.py").write_text("def f0():\n    return 1\n", encoding="utf-8")

    persisted: dict[str, Any] = {}

    async def fake_persist(repo_id, chunks):
        persisted["chunks"] = chunks
        return len(chunks)

    async def fake_link(repo_id, mapping):
        persisted["links"] = mapping
        return len(mapping)

    from app import search
    from app.persistence import graphstore

    monkeypatch.setattr(search, "persist_chunks", fake_persist)
    # Dev 3's source_chunk_id write, a second database call in the same function.
    monkeypatch.setattr(graphstore, "set_source_chunk_ids", fake_link)
    monkeypatch.setattr(pipeline, "wait_until_searchable", _already_searchable)

    result = await pipeline.reindex_repository(REPO, enrich=False)

    assert result["chunks"] == 1
    assert persisted["chunks"][0]["qualified_name"] == "pricing.f0"
    # The symbol is linked back to the chunk that was actually stored.
    assert persisted["links"] == {"sym_0000000000000000":
                                 persisted["chunks"][0]["chunk_id"]}


async def test_nothing_is_persisted_when_there_is_nothing_to_chunk(repository,
                                                                   monkeypatch):
    """The guard that stops a re-index wiping a repository's existing chunks.

    `persist_chunks` deletes by `repository_id` before inserting, so reaching it with
    an empty list would replace a working index with nothing. When this first
    happened the forty chunks survived only because of the early return - luck, since
    that return was written for "no symbols", not for "no sources".
    """
    repository["symbols"] = []
    repository["root"].mkdir(parents=True)

    called = {"persist": False}

    async def fake_persist(repo_id, chunks):
        called["persist"] = True
        return 0

    from app import search

    monkeypatch.setattr(search, "persist_chunks", fake_persist)

    await pipeline.reindex_repository(REPO, enrich=False)
    assert called["persist"] is False


async def test_a_failed_symbol_link_does_not_report_the_chunks_as_unstored(
    repository, monkeypatch
):
    """Two writes, two failure reports.

    `persist_chunks` and Dev 3's `set_source_chunk_ids` are separate database calls in
    one function. They shared a try/except, so a failure in the second reported
    "chunks were not persisted" with `chunks: 0` - and the caller would re-run an
    index that had in fact succeeded. Search works fine in this state; only the
    `source_chunk_id` back-reference is stale.
    """
    root: Path = repository["root"]
    root.mkdir(parents=True)
    (root / "pricing.py").write_text(SOURCE, encoding="utf-8")

    async def fake_persist(repo_id, chunks):
        return len(chunks)

    async def failing_link(repo_id, mapping):
        raise RuntimeError("write conflict")

    from app import search
    from app.persistence import graphstore

    monkeypatch.setattr(search, "persist_chunks", fake_persist)
    monkeypatch.setattr(graphstore, "set_source_chunk_ids", failing_link)
    monkeypatch.setattr(pipeline, "wait_until_searchable", _already_searchable)

    result = await pipeline.reindex_repository(REPO, enrich=False)

    assert result["chunks"] == 1, "the chunks were stored; do not report zero"
    linking = [w for w in result["warnings"] if "not linked back" in w]
    assert len(linking) == 1
    assert "write conflict" in linking[0]
    assert not any("were not persisted" in w for w in result["warnings"])


async def _already_searchable(repository_id: str, **_: object) -> bool:
    return True
