"""End-to-end import of the demo fixture - owner: Dev 3. Covers T-01 and T-09.

Runs the real `import_repository` against the real fixture, with Atlas replaced by an
in-memory store and the snapshot written to a tmp directory. Everything else - the
copy, the hashing, the AST walk, the graph build - is the production path.

T-09 is the one worth having here: the snapshot hash must be reproducible, because
the whole immutable-baseline guarantee is built on it.
"""

import os
import shutil

import pytest

from app import ingestion
from app.analysis.identifiers import snapshot_hash_for
from app.errors import NOT_FOUND, AppError
from app.persistence import graphstore

# The `store` fixture replaces snapshot_path with a plain tmp directory; the rebuild
# tests below need the real one.
REAL_SNAPSHOT_PATH = ingestion.snapshot_path


@pytest.fixture
def store(monkeypatch, tmp_path):
    """In-memory stand-in for the three Dev 3 collections."""
    saved: dict = {"repositories": {}, "symbols": [], "edges": []}

    async def save_repository(record):
        saved["repositories"][record.repository_id] = record
        return record

    async def get_repository(repo_id):
        if repo_id not in saved["repositories"]:  # as graphstore does
            raise AppError(NOT_FOUND, f"No repository {repo_id!r}", status_code=404)
        return saved["repositories"][repo_id]

    async def replace_symbols(repo_id, symbols):
        saved["symbols"] = list(symbols)
        return len(symbols)

    async def replace_edges(repo_id, edges):
        saved["edges"] = list(edges)
        return len(edges)

    for name, fn in [
        ("save_repository", save_repository), ("get_repository", get_repository),
        ("replace_symbols", replace_symbols), ("replace_edges", replace_edges),
    ]:
        monkeypatch.setattr(graphstore, name, fn)
        monkeypatch.setattr(ingestion.graphstore, name, fn)

    # Import also persists behaviour cases (Dev 4 brief §2), which needs Atlas.
    async def persist_cases(cases):
        saved["cases"] = list(cases)
        return len(cases)

    from app.verification import cases as cases_module

    monkeypatch.setattr(cases_module, "persist_cases", persist_cases)
    saved["cases"] = []

    # Import also persists search chunks (Dev 3 brief §3). Without this stub the
    # "in-memory" fixture writes to the real `code_chunks` collection on any machine
    # with a configured .env - eleven imports' worth of pollution per run.
    async def persist_chunks(repo_id, chunks):
        saved["chunks"] = list(chunks)
        return len(chunks)

    import app.search as search_module

    monkeypatch.setattr(search_module, "persist_chunks", persist_chunks)
    saved["chunks"] = []

    # ...and then points each symbol at its chunk, also an Atlas write.
    async def set_source_chunk_ids(repo_id, chunk_by_symbol):
        saved["source_chunk_ids"] = dict(chunk_by_symbol)
        return len(chunk_by_symbol)

    monkeypatch.setattr(graphstore, "set_source_chunk_ids", set_source_chunk_ids)
    saved["source_chunk_ids"] = {}

    monkeypatch.setattr(ingestion, "snapshot_path", lambda repo_id: tmp_path / repo_id)

    # The in-memory store is always "reachable".
    async def database_reachable():
        return None

    monkeypatch.setattr(ingestion, "_require_database", database_reachable)
    return saved


async def test_importing_the_fixture_produces_a_real_repository_record(store):
    if not ingestion.fixture_path("legacy_orders").is_dir():
        pytest.skip("demo fixture not present")

    record = await ingestion.import_repository(demo_fixture_id="legacy_orders")

    assert record.repository_id.startswith("repo_")
    assert record.origin == "demo_fixture"
    assert record.analysis_status == "ok"
    assert record.snapshot_hash.startswith("sha256:")
    assert record.module_count == 7      # six modules + the test module
    assert record.test_file_count == 1
    assert record.symbol_count > 20

    # The analysis itself must be clean. The one warning allowed here is the
    # indexing note: the test suite imports with INDEX_ON_IMPORT off, so import
    # says so rather than leaving semantic search silently empty.
    assert [w for w in record.warnings if "INDEX_ON_IMPORT" not in w] == []

    # Chunks are built even with enrichment off, so keyword search works at once.
    assert len(store["chunks"]) > 20
    assert all(c["description"] is None for c in store["chunks"])
    assert all(c["embedding"] is None for c in store["chunks"])


async def test_only_the_allowlisted_fixture_is_executable(store):
    """Spec §3.4 / §0.5: executable migration for trusted fixtures only."""
    if not ingestion.fixture_path("legacy_orders").is_dir():
        pytest.skip("demo fixture not present")

    record = await ingestion.import_repository(demo_fixture_id="legacy_orders")
    assert record.executable is True
    assert record.demo_fixture_id == "legacy_orders"


async def test_a_zip_upload_is_never_executable(store, tmp_path):
    """An upload may be analysed but must never be migrated."""
    import zipfile

    archive = tmp_path / "upload.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("mod.py", "def f():\n    return 1\n")

    record = await ingestion.import_repository(zip_path=archive, name="upload")

    assert record.executable is False
    assert record.origin == "zip_upload"
    assert record.demo_fixture_id is None


# One extra file of the uploader's own, which is the attack the trust gate exists for.
PAYLOAD = b"import os\nos.system('id')\n"


def chess_folder():
    chess = ingestion.repo_root() / "demo" / "chess"
    if not chess.is_dir():
        pytest.skip("chess source not present")
    return [
        (path.relative_to(chess).as_posix(), path.read_bytes())
        for path in sorted(chess.rglob("*"))
        if path.is_file()
    ]


async def test_a_folder_upload_of_the_trusted_chess_source_is_executable(
    store, tmp_path
):
    """Changed by #21, deliberately.

    Until 24 Oct every upload was static-analysis only. The chess demo is uploaded as
    a folder, so that rule made the MVP demo impossible. An upload is now executable
    when it is an **exact content copy** of a trusted tree in this repository - see
    `app/ingestion/trust.py`. Nothing about its name or origin earns that.
    """
    from app.ingestion.runtime import inspect_runtime

    record = await ingestion.import_repository(folder=chess_folder(), name="chess")

    assert record.origin == "folder_upload"
    assert record.executable is True
    assert record.demo_fixture_id == "chess"
    assert any("exact copy of the trusted chess source" in w
               for w in record.warnings), (
        "the record does not say why this upload may be executed"
    )
    assert record.name == "chess"
    assert record.module_count > 10
    assert inspect_runtime(tmp_path / record.repository_id)["declared_python"] == "2.7"


async def test_a_folder_upload_that_is_not_a_trusted_source_is_never_executable(store):
    """The rule that still holds for every other upload."""
    folder = chess_folder()
    folder.append(("local_chess/payload.py", PAYLOAD))

    record = await ingestion.import_repository(folder=folder, name="chess-plus")

    assert record.origin == "folder_upload"
    assert record.executable is False
    assert record.demo_fixture_id is None


async def test_an_ordinary_folder_upload_is_never_executable(store):
    record = await ingestion.import_repository(
        folder=[("app.py", b"print('hello')\n")], name="mine"
    )

    assert record.executable is False
    assert record.demo_fixture_id is None
    assert store["cases"] == []  # #29: no orders cases on an unrelated repository.


@pytest.mark.parametrize("source", ["folder", "zip"])
async def test_chess_import_routes_cases_without_exposing_hidden_inputs(store, tmp_path, source):
    import runpy

    from app.graph import _nodes_from_symbols
    from app.graph.builder import to_networkx
    from app.orchestration.scheduler import build_units, impacted_units

    folder = chess_folder()
    if source == "folder":
        record = await ingestion.import_repository(folder=folder)
    else:
        packager = runpy.run_path(str(ingestion.repo_root() / "demo/package_chess.py"))
        archive = packager["build_archive"](
            ingestion.repo_root() / "demo/chess", tmp_path / "chess.zip",
        )
        record = await ingestion.import_repository(zip_path=archive)

    cases = store["cases"]
    assert record.demo_fixture_id == "chess"
    assert len(cases) == 24
    assert sum(c.visibility == "visible" for c in cases) == 12
    assert sum(c.visibility == "held_out" for c in cases) == 12
    assert all(c.case_id.startswith("chess_") for c in cases)
    snapshot = tmp_path / record.repository_id
    assert not (snapshot / "challenge_cases").exists()
    assert not (snapshot / "chess_cases.py").exists()
    assert "chess_hidden_" not in str(store["chunks"])

    graph = to_networkx(_nodes_from_symbols(store["symbols"]), store["edges"])
    units = build_units(graph, {
        s.module_id: s.path for s in store["symbols"] if s.kind == "module"
    })
    cli = next(u for u in units if "local_chess/cli.py" in u.file_paths)
    assert {c.unit_id for c in cases} == {cli.unit_id}
    # Lower-level changes still run the session cases as impacted checks.
    for path in ["local_chess/game.py", "local_chess/rules.py", "local_chess/game_state.py",
                 "local_chess/renderer.py", "local_chess/pieces.py"]:
        unit = next(u for u in units if path in u.file_paths)
        assert cli.unit_id in impacted_units(unit.unit_id, units)


async def test_modified_chess_upload_gets_no_builtin_hidden_cases(store):
    folder = chess_folder() + [("extra.py", b"x = 1\n")]
    record = await ingestion.import_repository(folder=folder, name="chess")
    assert record.executable is False
    assert record.demo_fixture_id is None
    assert len(store["cases"]) == 12
    assert all(c.visibility == "visible" for c in store["cases"])


async def test_importing_both_a_folder_and_a_fixture_is_refused(store):
    from app.errors import AppError

    with pytest.raises(AppError) as caught:
        await ingestion.import_repository(
            folder=[("mod.py", b"x = 1\n")], demo_fixture_id="legacy_orders"
        )
    assert caught.value.status_code == 422


async def test_the_snapshot_hash_is_reproducible(store):
    """T-09 rests on this: the same tree must always hash the same, or 'unchanged
    baseline' means nothing."""
    if not ingestion.fixture_path("legacy_orders").is_dir():
        pytest.skip("demo fixture not present")

    first = await ingestion.import_repository(demo_fixture_id="legacy_orders")
    store["repositories"].clear()  # as if into a fresh database
    second = await ingestion.import_repository(demo_fixture_id="legacy_orders")

    assert first.snapshot_hash == second.snapshot_hash
    # #82: one repository per fixture content, so the id is reproducible too.
    assert first.repository_id == second.repository_id


def test_the_snapshot_hash_depends_on_content_and_layout():
    base = {"a.py": "hash-a", "b.py": "hash-b"}
    assert snapshot_hash_for(base) == snapshot_hash_for({"b.py": "hash-b", "a.py": "hash-a"})
    assert snapshot_hash_for(base) != snapshot_hash_for({"a.py": "hash-a", "b.py": "CHANGED"})
    assert snapshot_hash_for(base) != snapshot_hash_for({"a.py": "hash-a", "c.py": "hash-b"})


async def test_the_snapshot_is_written_and_readable(store, tmp_path):
    if not ingestion.fixture_path("legacy_orders").is_dir():
        pytest.skip("demo fixture not present")

    record = await ingestion.import_repository(demo_fixture_id="legacy_orders")
    snapshot = tmp_path / record.repository_id

    assert (snapshot / "pricing.py").is_file()
    assert "finalise_amount" in (snapshot / "pricing.py").read_text(encoding="utf-8")


async def test_held_out_cases_never_reach_the_snapshot(store, tmp_path):
    """They live outside the fixture tree on purpose. If they ever moved inside,
    they would be snapshot, indexed and visible to the model."""
    if not ingestion.fixture_path("legacy_orders").is_dir():
        pytest.skip("demo fixture not present")

    record = await ingestion.import_repository(demo_fixture_id="legacy_orders")
    snapshot = tmp_path / record.repository_id

    assert not list(snapshot.rglob("held_out*"))
    assert not list(snapshot.rglob("*challenge_cases*"))


async def test_importing_both_a_zip_and_a_fixture_is_refused(store, tmp_path):
    from app.errors import AppError

    archive = tmp_path / "x.zip"
    archive.write_bytes(b"")
    with pytest.raises(AppError):
        await ingestion.import_repository(
            zip_path=archive, demo_fixture_id="legacy_orders"
        )


async def test_an_unknown_fixture_id_is_refused(store):
    from app.errors import AppError

    with pytest.raises(AppError) as caught:
        await ingestion.import_repository(demo_fixture_id="not_a_fixture")
    assert caught.value.status_code == 422


async def test_the_scheduling_graph_rebuilt_from_the_store_is_the_t03_set(store):
    """T-03 end to end: what `load_networkx` derives from persisted data.

    The stored edges deliberately hold more than this - the test module's own
    imports are kept so `get_graph` can show them. `to_networkx` is what narrows
    to local modules and confirmed dependency edges.
    """
    if not ingestion.fixture_path("legacy_orders").is_dir():
        pytest.skip("demo fixture not present")

    from app.graph import _nodes_from_symbols
    from app.graph.builder import to_networkx

    await ingestion.import_repository(demo_fixture_id="legacy_orders")

    nodes = _nodes_from_symbols(store["symbols"])
    scheduling = to_networkx(nodes, store["edges"])

    assert set(scheduling.edges()) == {
        ("module_main", "module_orders"),
        ("module_main", "module_refunds"),
        ("module_orders", "module_pricing"),
        ("module_orders", "module_customers"),
        ("module_orders", "module_storage"),
        ("module_refunds", "module_pricing"),
        ("module_refunds", "module_storage"),
    }


async def test_the_test_module_s_imports_are_stored_but_not_scheduled(store):
    """They are real information for the graph view, and noise for the scheduler."""
    if not ingestion.fixture_path("legacy_orders").is_dir():
        pytest.skip("demo fixture not present")

    from app.analysis.identifiers import module_id_for
    from app.graph import _nodes_from_symbols
    from app.graph.builder import to_networkx

    await ingestion.import_repository(demo_fixture_id="legacy_orders")
    test_module = module_id_for("tests/test_baseline.py")

    stored_from_tests = [e for e in store["edges"] if e.source == test_module]
    assert stored_from_tests, "the test module's imports were not persisted"

    scheduling = to_networkx(_nodes_from_symbols(store["symbols"]), store["edges"])
    assert test_module not in scheduling.nodes()


# --------------------------------------------------------------------------
# built-in fixtures on a shared database (#81, #82, #83)
# --------------------------------------------------------------------------

async def test_chess_is_the_first_built_in_fixture():
    """New Migration preselects the first entry, and chess is the demo."""
    assert [f["demo_fixture_id"] for f in await ingestion.list_demo_fixtures()] == [
        "chess", "legacy_orders",
    ]


async def test_the_chess_fixture_imports_executable_with_its_own_cases(store):
    chess_folder()  # skips when the source is absent

    record = await ingestion.import_repository(demo_fixture_id="chess")

    assert record.origin == "demo_fixture"
    assert record.executable is True
    assert record.demo_fixture_id == "chess"
    assert record.repository_id == ingestion.fixture_repository_id("chess")
    cases = store["cases"]
    assert sum(c.visibility == "visible" for c in cases) == 12
    assert sum(c.visibility == "held_out" for c in cases) == 12
    assert all(c.case_id.startswith("chess_") for c in cases)
    assert not any(c.provenance == "demo/challenge_cases/held_out.json" for c in cases)


async def test_importing_a_fixture_again_returns_the_existing_repository(
    store, monkeypatch
):
    """From any laptop: no second analysis, no second index, no model calls."""
    chess_folder()
    first = await ingestion.import_repository(demo_fixture_id="chess")

    def must_not_run(*args, **kwargs):
        raise AssertionError("a re-import must not analyse or index again")

    monkeypatch.setattr(ingestion, "_analyse", must_not_run)
    second = await ingestion.import_repository(demo_fixture_id="chess")

    assert second.repository_id == first.repository_id
    assert list(store["repositories"]) == [first.repository_id]


async def test_a_folder_upload_of_chess_still_gets_its_own_id(store):
    """Only a fixture chosen by id is shared; an upload is an import event."""
    record = await ingestion.import_repository(folder=chess_folder())
    assert record.demo_fixture_id == "chess"
    assert record.repository_id != ingestion.fixture_repository_id("chess")


def copy_of_chess(destination, *, crlf=False):
    source = ingestion.repo_root() / "demo" / "chess"
    if not source.is_dir():
        pytest.skip("chess source not present")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__"))
    if crlf:
        for path in destination.rglob("*.py"):
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    return destination


def use_fixture_tree(monkeypatch, tree):
    real = ingestion.fixture_path
    monkeypatch.setattr(
        ingestion, "fixture_path",
        lambda fixture_id: tree if fixture_id == "chess" else real(fixture_id),
    )


def test_the_fixture_id_ignores_line_endings_but_not_content(tmp_path, monkeypatch):
    lf = copy_of_chess(tmp_path / "lf")
    crlf = copy_of_chess(tmp_path / "crlf", crlf=True)

    use_fixture_tree(monkeypatch, lf)
    from_lf = ingestion.fixture_repository_id("chess")
    use_fixture_tree(monkeypatch, crlf)
    assert ingestion.fixture_repository_id("chess") == from_lf

    (crlf / "local_chess" / "board.py").write_bytes(b"# changed\n")
    changed = ingestion.fixture_repository_id("chess")
    assert changed != from_lf
    assert changed.startswith("repo_") and len(changed) == len("repo_") + 12


def snapshot_hash_of(root):
    from app.analysis.identifiers import hash_file

    return snapshot_hash_for({
        path.relative_to(root).as_posix(): hash_file(path)
        for path in root.rglob("*") if path.is_file()
    })


async def test_a_laptop_without_the_snapshot_rebuilds_it_from_its_checkout(
    store, tmp_path, monkeypatch
):
    """#83: the import was made elsewhere; this machine's copy is the same bytes."""
    from app.config import get_settings

    chess_folder()
    record = await ingestion.import_repository(demo_fixture_id="chess")

    monkeypatch.setattr(get_settings(), "workspace_root", str(tmp_path / "other_laptop"))
    rebuilt = REAL_SNAPSHOT_PATH(record.repository_id)

    assert rebuilt.is_dir()
    assert snapshot_hash_of(rebuilt) == record.snapshot_hash
    assert not os.access(rebuilt / "local_chess" / "board.py", os.W_OK)
    assert [p.name for p in rebuilt.parent.iterdir()] == [record.repository_id]
    assert ingestion.snapshot_available(record.repository_id)


def test_a_fixture_that_changed_since_its_import_is_not_rebuilt(tmp_path, monkeypatch):
    from app.config import get_settings

    tree = copy_of_chess(tmp_path / "checkout")
    use_fixture_tree(monkeypatch, tree)
    imported_as = ingestion.fixture_repository_id("chess")
    (tree / "local_chess" / "board.py").write_bytes(b"# someone edited chess\n")

    monkeypatch.setattr(get_settings(), "workspace_root", str(tmp_path / "workspaces"))

    assert not REAL_SNAPSHOT_PATH(imported_as).exists()
    assert not ingestion.snapshot_available(imported_as)


def test_an_upload_made_elsewhere_is_never_rebuilt(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "workspace_root", str(tmp_path))

    assert not REAL_SNAPSHOT_PATH("repo_000000000abc").exists()
    assert not (tmp_path / "snapshots").exists() or not any(
        (tmp_path / "snapshots").iterdir()
    )
