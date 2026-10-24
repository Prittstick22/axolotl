"""Ingestion - owner: Dev 3. Spec §5.1, §5.2. See SHARED_CONTRACTS §2, §8.

Imports a repository into an immutable snapshot, analyses it, and persists the
resulting graph to Atlas.

Two rules that are not negotiable:

  - **Only allowlisted fixtures are executable.** A ZIP or folder upload gets
    `executable=False` and can never be migrated (§0.5); asking for a plan on one is
    a 409 `REPOSITORY_NOT_EXECUTABLE`.
  - **The snapshot is written once and never again.** Dev 4 copies from it; nobody
    writes into it. T-09 asserts its hash is unchanged after a full run.

Nothing here imports or executes the analysed code.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.analysis.astwalk import ModuleAnalysis, analyse_module
from app.analysis.identifiers import (
    REPOSITORY_PREFIX,
    SHORT_ID_BYTES,
    hash_file,
    new_repository_id,
    snapshot_hash_for,
)
from app.config import get_settings
from app.errors import NOT_FOUND, VALIDATION_ERROR, AppError, database_unavailable
from app.graph.builder import add_test_target_edges, build_edges, build_nodes
from app.ingestion import trust
from app.ingestion.safety import (
    MAX_ARCHIVE_BYTES,
    MAX_FILES,
    UnsafeArchive,
    is_ignored,
    plan_extraction,
    plan_folder,
)
from app.models.contracts import RepositoryRecord
from app.persistence import graphstore

log = logging.getLogger(__name__)

# The ONLY executable source. Spec §3.4: executable migration is enabled for
# allowlisted trusted fixtures and nothing else.
# Chess is listed first: it is the demo, and New Migration preselects the first entry.
DEMO_FIXTURES: dict[str, dict[str, str]] = {
    "chess": {
        "demo_fixture_id": "chess",
        "name": "chess",
        "description": (
            "William's terminal chess game, written for Python 2.7, with its rules and "
            "terminal-session tests. The demo: migrate it to Python 3.14."
        ),
        "relative_path": "demo/chess",
    },
    "legacy_orders": {
        "demo_fixture_id": "legacy_orders",
        "name": "legacy_orders",
        "description": (
            "The test fixture: a small Python 3 order service with a deliberately subtle "
            "discount/tax/rounding rule. Six modules, no external dependencies."
        ),
        "relative_path": "demo/legacy_orders",
    },
}


def repo_root() -> Path:
    """The checkout root: .../axolotl, from .../axolotl/backend/app/ingestion."""
    return Path(__file__).resolve().parents[3]


def fixture_path(demo_fixture_id: str) -> Path:
    fixture = DEMO_FIXTURES.get(demo_fixture_id)
    if fixture is None:
        raise AppError(
            VALIDATION_ERROR,
            f"Unknown demo fixture {demo_fixture_id!r}",
            status_code=422,
            details={"available": sorted(DEMO_FIXTURES)},
        )
    return repo_root() / fixture["relative_path"]


async def list_demo_fixtures() -> list[dict[str, str]]:
    """GET /demo-fixtures."""
    return [
        {
            "demo_fixture_id": f["demo_fixture_id"],
            "name": f["name"],
            "description": f["description"],
        }
        for f in DEMO_FIXTURES.values()
    ]


def fixture_repository_id(demo_fixture_id: str) -> str:
    """The one repository id for this fixture's content in this checkout (#82).

    Records are shared through Atlas, so a random id per import gave every laptop its
    own copy of the same fixture. Derived from the normalised tree hash: line endings
    don't change it, any real change to the fixture does, and the id keeps the usual
    `repo_` + 12 hex shape.
    """
    source = fixture_path(demo_fixture_id)
    tree_hash = trust.normalised_tree_hash(source)
    if tree_hash is None:
        raise AppError(
            VALIDATION_ERROR, f"Fixture directory not found: {source}", status_code=500
        )
    return _fixture_id_for(demo_fixture_id, tree_hash)


def _fixture_id_for(demo_fixture_id: str, tree_hash: str) -> str:
    digest = hashlib.sha256(f"fixture:{demo_fixture_id}:{tree_hash}".encode()).hexdigest()
    return REPOSITORY_PREFIX + digest[: SHORT_ID_BYTES * 2]


def snapshot_path(repo_id: str) -> Path:
    """Read-only baseline tree for this repository (SHARED_CONTRACTS §8, sync).

    Snapshots stay on the machine that imported them. A built-in fixture's is the one
    exception: every checkout has the source, so when this machine has no copy it is
    rebuilt from the checkout (#83), and a fixture imported on one laptop runs on all.
    """
    root = Path(get_settings().workspace_root).expanduser()
    path = root / "snapshots" / repo_id
    if not path.is_dir():
        _rebuild_fixture_snapshot(repo_id, path)
    return path


def snapshot_available(repo_id: str) -> bool:
    """True when this machine can read, run or re-index the repository's code (#84)."""
    return snapshot_path(repo_id).is_dir()


def _rebuild_fixture_snapshot(repo_id: str, path: Path) -> None:
    """Rebuild `path` if `repo_id` is a built-in fixture's id for this checkout.

    The id is derived from the content, so a match means the checkout holds exactly
    what was imported. A fixture changed since the import gives a different id, nothing
    is rebuilt, and the repository reads as "not on this machine" like any upload.
    """
    for demo_fixture_id in DEMO_FIXTURES:
        source = fixture_path(demo_fixture_id)
        tree_hash = trust.normalised_tree_hash(source)
        if tree_hash is None or _fixture_id_for(demo_fixture_id, tree_hash) != repo_id:
            continue

        # Built beside the destination and renamed into place, so a reader never sees
        # half a snapshot. If two requests race, the second rename fails and its copy
        # is thrown away.
        staging = path.with_name(f".{repo_id}.{secrets.token_hex(4)}")
        try:
            staging.mkdir(parents=True)
            _write_fixture_snapshot(source, staging)
            if trust.normalised_tree_hash(staging) != tree_hash:
                log.warning("%s changed while it was being copied; not rebuilt", source)
                return
            staging.rename(path)
        except OSError as exc:
            if not path.is_dir():
                log.warning("could not rebuild the %s snapshot: %s", demo_fixture_id, exc)
            return
        finally:
            _remove_tree(staging)
        _make_read_only(path)
        log.info("rebuilt the %s snapshot for %s from this checkout", demo_fixture_id,
                 repo_id)
        return


async def get_repository(repo_id: str) -> RepositoryRecord:
    return await graphstore.get_repository(repo_id)


async def list_repositories() -> list[RepositoryRecord]:
    return await graphstore.list_repositories()


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------

async def import_repository(
    *,
    zip_path: Path | None = None,
    demo_fixture_id: str | None = None,
    folder: list[tuple[str, bytes]] | None = None,
    name: str | None = None,
) -> RepositoryRecord:
    """SHARED_CONTRACTS §8. Exactly one of `zip_path`, `demo_fixture_id` or `folder`.

    `folder` is an uploaded directory as (path inside the folder, content) pairs.
    """
    if sum(bool(source) for source in (zip_path, demo_fixture_id, folder)) != 1:
        raise AppError(
            VALIDATION_ERROR,
            "Provide exactly one of a ZIP upload, a folder upload or a demo_fixture_id",
            status_code=422,
        )

    if demo_fixture_id:
        fixture_path(demo_fixture_id)  # 422 for an unknown id, even with Atlas down

    # Fail before touching the disk: without this, an import with Atlas down copies
    # the whole snapshot, then 503s, and leaves a folder for a repository that does
    # not exist anywhere (§9: 503 DATABASE_UNAVAILABLE, no half-done work).
    await _require_database()

    if demo_fixture_id:
        # One repository per fixture content (#82). Importing it again, from any
        # laptop, returns the existing one: no re-analysis, re-indexing or model calls.
        repository_id = fixture_repository_id(demo_fixture_id)
        existing = await _existing_repository(repository_id)
        if existing is not None:
            snapshot_path(repository_id)  # rebuilds this machine's copy if missing
            log.info("%s is already imported as %s", demo_fixture_id, repository_id)
            return existing
    else:
        repository_id = new_repository_id()
    destination = snapshot_path(repository_id)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        return await _import_into(
            destination, repository_id,
            zip_path=zip_path, demo_fixture_id=demo_fixture_id, folder=folder,
            name=name,
        )
    except BaseException:
        # A rejected archive, nothing importable, or Atlas dropping mid-import: the
        # snapshot was never accepted, so it must not outlive the failure.
        shutil.rmtree(destination, ignore_errors=True)
        raise


async def _existing_repository(repo_id: str) -> RepositoryRecord | None:
    try:
        return await graphstore.get_repository(repo_id)
    except AppError as exc:
        if exc.code == NOT_FOUND:
            return None
        raise


async def _require_database() -> None:
    from app.persistence import db

    await db.get_db()  # 503 when no URI is configured
    ok, reason = await db.ping()
    if not ok:
        raise database_unavailable(reason or "MongoDB is unreachable")


async def _import_into(
    destination: Path, repository_id: str, *,
    zip_path: Path | None, demo_fixture_id: str | None,
    folder: list[tuple[str, bytes]] | None, name: str | None,
) -> RepositoryRecord:

    if demo_fixture_id:
        source = fixture_path(demo_fixture_id)
        _assert_held_out_cases_are_outside(source)
        copied = _fixture_snapshot(source, destination)
        origin, executable = "demo_fixture", True
        display_name = name or DEMO_FIXTURES[demo_fixture_id]["name"]
    elif folder:
        copied = _write_folder(folder, destination)
        origin, executable = "folder_upload", False
        display_name = name or "uploaded-folder"
    else:
        copied = _extract_zip(zip_path, destination)
        origin, executable = "zip_upload", False
        display_name = name or Path(zip_path).stem

    if not copied:
        raise AppError(
            VALIDATION_ERROR, "Nothing importable was found", status_code=422
        )

    file_hashes = {rel: hash_file(destination / rel) for rel in copied}
    snapshot_hash = snapshot_hash_for(file_hashes)

    # An upload earns execution only by being an exact copy of a trusted tree in this
    # repository (#21). Nothing about its name or origin counts; see ingestion/trust.py
    # for why the comparison normalises line endings and why the recorded
    # `snapshot_hash` above is not normalised.
    trusted_warnings: list[str] = []
    if not executable:
        matched = trust.trusted_fixture_for(destination, copied)
        if matched:
            executable = True
            demo_fixture_id = demo_fixture_id or matched
            log.info("%s is an exact copy of the trusted %s source; executable",
                     repository_id, matched)
            trusted_warnings.append(
                f"this upload is an exact copy of the trusted {matched} source, so "
                f"executable migration is enabled for it"
            )

    analyses = _analyse(destination, copied, repository_id, snapshot_hash)
    record = await _persist(
        repository_id=repository_id,
        display_name=display_name,
        snapshot_hash=snapshot_hash,
        origin=origin,
        executable=executable,
        demo_fixture_id=demo_fixture_id,
        file_count=len(copied),
        analyses=analyses,
    )

    # Behaviour cases are persisted at import (Dev 4 brief §2). Held-out cases are
    # read from outside the snapshot and stored, but every model-facing read goes
    # through include_held_out=False.
    try:
        from app.verification.cases import collect_cases, persist_cases

        cases = collect_cases(
            repository_id, destination, repo_root(), fixture_id=demo_fixture_id,
        )
        if cases:
            await persist_cases(cases)
            log.info("persisted %d behaviour case(s) for %s", len(cases), repository_id)
    except Exception as exc:  # noqa: BLE001 - a repository is still usable without cases
        log.warning("could not persist behaviour cases: %s", exc)
        record.warnings.append(f"behaviour cases were not loaded: {exc}")

    # Chunking, descriptions and embeddings (Dev 3 brief §3). Chunks are built and
    # persisted before the import responds, so keyword search works at once and with
    # no API key at all. Descriptions and embeddings, which `INDEX_ON_IMPORT` gates,
    # are two Gemini calls per chunk - minutes for chess - so they run in the
    # background (#28); GET /repositories/{id}/index shows their progress.
    try:
        from app.search.pipeline import index_repository

        enrich = get_settings().index_on_import
        indexed = await index_repository(
            repository_id,
            symbols=[s for a in analyses.values() for s in a.symbols],
            sources_by_module={m: a.source for m, a in analyses.items()},
            enrich=enrich,
            in_background=enrich,
        )
        record.warnings.extend(indexed["warnings"])
        log.info(
            "search index for %s: %d chunk(s), %d described, %d embedded",
            repository_id, indexed["chunks"], indexed["described"],
            indexed["embedded"],
        )
    except Exception as exc:  # noqa: BLE001 - a repository is still usable unsearchable
        log.warning("could not index %s for search: %s", repository_id, exc)
        record.warnings.append(f"search indexing failed: {exc}")

    # Behaviour cases and search indexing both run after `_persist` has already
    # written the record, so their warnings would otherwise live only on the object
    # returned to this one caller and never reach GET /repositories/{id}.
    record.warnings.extend(trusted_warnings)
    record.warnings = record.warnings[:50]
    await graphstore.save_repository(record)

    _make_read_only(destination)
    log.info(
        "imported %s (%s) %d file(s), %d module(s), %s",
        repository_id, origin, record.file_count, record.module_count,
        record.analysis_status,
    )
    return record


def _assert_held_out_cases_are_outside(fixture_dir: Path) -> None:
    """Held-out cases must never be snapshot, indexed or searchable (Dev 3 brief).

    They live in `demo/challenge_cases/`, outside the fixture tree, on purpose.
    This asserts that is still true rather than trusting the layout.
    """
    offenders = [p for p in fixture_dir.rglob("*") if "challenge_cases" in p.parts]
    if offenders:
        raise AppError(
            VALIDATION_ERROR,
            "Held-out challenge cases are inside the fixture tree; they would be "
            "snapshot and become visible to the model.",
            status_code=500,
            details={"paths": [str(p) for p in offenders[:5]]},
        )


def _fixture_snapshot(source: Path, destination: Path) -> list[str]:
    """Make `destination` an exact copy of a trusted fixture. Returns relative paths.

    `snapshot_path` may already have rebuilt it from this checkout, and a copy left by
    an earlier import against another database may be there too. Either is kept only
    if it is the same content; anything else is replaced.
    """
    if not source.is_dir():
        raise AppError(
            VALIDATION_ERROR, f"Fixture directory not found: {source}", status_code=500
        )
    if trust.normalised_tree_hash(destination) != trust.normalised_tree_hash(source):
        _remove_tree(destination)
        destination.mkdir(parents=True)
        _write_fixture_snapshot(source, destination)
    return _snapshot_files(destination)


def _write_fixture_snapshot(source: Path, destination: Path) -> list[str]:
    """Copy a trusted fixture, skipping ignored paths. Returns relative paths.

    Line endings are written as LF. A fixture's snapshot is then the same bytes on
    every laptop, whatever each checkout did to its newlines, so its recorded
    `snapshot_hash` holds wherever it is rebuilt (T-09). `.gitattributes` already
    checks these files out as LF; this covers a stray CRLF save. See trust.py for
    why the two line endings cannot differ in what Python does.
    """
    copied: list[str] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(source).as_posix()
        if is_ignored(relative):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
        copied.append(relative)
        if len(copied) > MAX_FILES:
            raise AppError(
                VALIDATION_ERROR,
                f"Fixture holds more than {MAX_FILES} files", status_code=422,
            )
    return copied


def _snapshot_files(root: Path) -> list[str]:
    return [
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]


def _remove_tree(root: Path) -> None:
    """Delete a snapshot folder, including one `_make_read_only` has locked."""
    if not root.exists():
        return
    for path in [root, *root.rglob("*")]:
        try:
            path.chmod(0o755 if path.is_dir() else 0o644)
        except OSError:
            pass
    shutil.rmtree(root, ignore_errors=True)


def _extract_zip(zip_path: Path | None, destination: Path) -> list[str]:
    """Extract an untrusted archive. Every rejection is an explicit 422 (T-02)."""
    path = Path(zip_path)  # type: ignore[arg-type]
    if not path.is_file():
        raise AppError(VALIDATION_ERROR, "Upload not found", status_code=422)
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise AppError(
            VALIDATION_ERROR,
            f"Archive is larger than {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB",
            status_code=422,
        )

    copied: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for info, relative in plan_extraction(archive):
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                # Stream member-by-member; never extractall, which trusts names.
                with archive.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                copied.append(relative)
    except UnsafeArchive as exc:
        raise AppError(
            VALIDATION_ERROR, f"Archive rejected: {exc}", status_code=422,
            details={"reason": str(exc)},
        ) from exc
    except zipfile.BadZipFile as exc:
        raise AppError(
            VALIDATION_ERROR, "Upload is not a valid ZIP archive", status_code=422
        ) from exc
    return copied


def _write_folder(folder: list[tuple[str, bytes]], destination: Path) -> list[str]:
    """Write an untrusted folder upload. Every rejection is an explicit 422 (T-02)."""
    try:
        planned = plan_folder([(path, len(data)) for path, data in folder])
    except UnsafeArchive as exc:
        raise AppError(
            VALIDATION_ERROR, f"Upload rejected: {exc}", status_code=422,
            details={"reason": str(exc)},
        ) from exc

    copied: list[str] = []
    for index, relative in planned:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(folder[index][1])
        copied.append(relative)
    return copied


def _analyse(
    root: Path, relative_paths: list[str], repository_id: str, snapshot_hash: str
) -> dict[str, ModuleAnalysis]:
    analyses: dict[str, ModuleAnalysis] = {}
    for relative in relative_paths:
        if not relative.endswith(".py"):
            continue
        analysis = analyse_module(
            source_path=root / relative,
            relative_path=relative,
            repository_id=repository_id,
            snapshot_hash=snapshot_hash,
        )
        analyses[analysis.module_id] = analysis
    return analyses


async def _persist(
    *, repository_id: str, display_name: str, snapshot_hash: str, origin: str,
    executable: bool, demo_fixture_id: str | None, file_count: int,
    analyses: dict[str, ModuleAnalysis],
) -> RepositoryRecord:
    nodes = build_nodes(analyses)
    edges, external_nodes, edge_warnings = build_edges(analyses)
    edges = add_test_target_edges(analyses, edges)

    symbols = [s for a in analyses.values() for s in a.symbols]
    warnings = [w for a in analyses.values() for w in a.warnings] + edge_warnings

    parsed = [a for a in analyses.values() if a.parsed]
    if not analyses:
        status: Any = "failed"
    elif len(parsed) == len(analyses):
        status = "ok"
    elif parsed:
        status = "partial"
    else:
        status = "failed"

    record = RepositoryRecord(
        repository_id=repository_id,
        name=display_name,
        snapshot_hash=snapshot_hash,
        origin=origin,  # type: ignore[arg-type]
        executable=executable,
        demo_fixture_id=demo_fixture_id,
        file_count=file_count,
        module_count=len(analyses),
        symbol_count=len(symbols),
        test_file_count=sum(1 for a in analyses.values() if a.is_test),
        analysis_status=status,
        warnings=warnings[:50],
        created_at=datetime.now(UTC),
    )

    await graphstore.save_repository(record)
    await graphstore.replace_symbols(repository_id, symbols)
    await graphstore.replace_edges(repository_id, edges)
    # External nodes are derived, not stored: they are rebuilt from edges on read.
    del nodes, external_nodes
    return record


def _make_read_only(root: Path) -> None:
    """Best effort. The snapshot is the baseline and nothing may write to it again.

    **World-readable, not owner-only.** The sandbox container runs as uid 65534 and
    does not own these files, so `0o400` would make the baseline unreadable inside
    the container and baseline capture would fail on every case. The mount is `:ro`,
    so the container still cannot write to it.

    On Windows chmod only toggles a read-only attribute, so this is a guard rail
    rather than a guarantee. The real protection is that no code path opens a
    snapshot for writing.
    """
    try:
        for path in sorted(root.rglob("*"), reverse=True):
            path.chmod(0o444 if path.is_file() else 0o555)
    except OSError as exc:  # noqa: BLE001
        log.warning("could not make snapshot read-only: %s", exc)
