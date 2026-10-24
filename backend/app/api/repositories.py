"""Repositories API - owner: Dev 3. SHARED_CONTRACTS §6.

  GET  /demo-fixtures
  POST /repositories/import                         multipart `file` OR JSON body
  GET  /repositories
  GET  /repositories/{repo_id}
  GET  /repositories/{repo_id}/graph                ?level=module&run_id=
  GET  /repositories/{repo_id}/modules/{module_id}
  GET  /repositories/{repo_id}/symbols/{symbol_id}
  GET  /runs/{run_id}/dependency-changes             legacy vs migrated (#34)
  GET  /repositories/{repo_id}/security             legacy code alone (#42)
  GET  /runs/{run_id}/security                      legacy vs migrated (#42)

Uploaded archives are **static-analysis only**: they import with `executable=False`
and can never be migrated (§0.5). Only the allowlisted demo fixture is executable.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from pydantic import BaseModel

from app import graph as graph_service
from app import ingestion
from app.errors import VALIDATION_ERROR, AppError
from app.ingestion.safety import MAX_ARCHIVE_BYTES, MAX_EXTRACTED_BYTES
from app.models.contracts import GraphPayload, ModuleDetail, RepositoryRecord
from app.persistence.routing import DatabaseAwareRoute

log = logging.getLogger(__name__)
# Atlas going away mid-request is a 503 DATABASE_UNAVAILABLE, never a bare 500 (§9).
router = APIRouter(tags=["repositories"], route_class=DatabaseAwareRoute)

# Module-level singletons: calling File()/Form() inline in a default is a ruff B008.
# One per parameter: FastAPI binds a shared one to the first parameter's field name,
# which silently dropped `name` and read `paths` as a single value.
_FILE = File(None)
_FILES = File(None)
_PATHS = Form(None)
_DEMO_FIXTURE_ID = Form(None)
_NAME = Form(None)


class ImportFixtureRequest(BaseModel):
    demo_fixture_id: str
    name: str | None = None


@router.get("/demo-fixtures")
async def list_demo_fixtures() -> list[dict[str, str]]:
    return await ingestion.list_demo_fixtures()


@router.post("/repositories/import")
async def import_repository(
    request: Request,
    file: UploadFile | None = _FILE,
    files: list[UploadFile] | None = _FILES,
    paths: list[str] | None = _PATHS,
    demo_fixture_id: str | None = _DEMO_FIXTURE_ID,
    name: str | None = _NAME,
) -> RepositoryRecord:
    """Import a ZIP upload, a folder upload or an allowlisted demo fixture.

    Accepts either a multipart upload or a JSON body, because §6 specifies both and
    the frontend uses the JSON form for the fixture. A folder is multipart `files`
    with a matching `paths` entry for each, giving its path inside the folder.
    """
    if files:
        if file is not None or demo_fixture_id is not None:
            raise AppError(
                VALIDATION_ERROR,
                "Send a folder upload on its own, not with a ZIP or a demo_fixture_id",
                status_code=422,
            )
        folder = await _read_folder(files, paths or [])
        return await ingestion.import_repository(folder=folder, name=name)

    if file is None and demo_fixture_id is None:
        demo_fixture_id, name = await _fixture_from_json(request)

    if file is not None:
        # Spool to disk: the archive is untrusted and may be large, and the safety
        # checks work on a real file rather than a stream we have already buffered.
        with tempfile.TemporaryDirectory(prefix="axolotl-upload-") as tmp:
            upload = Path(tmp) / (Path(file.filename or "upload.zip").name)
            await _spool_zip(file, upload)
            return await ingestion.import_repository(
                zip_path=upload, name=name or upload.stem
            )

    return await ingestion.import_repository(
        demo_fixture_id=demo_fixture_id, name=name
    )


async def _spool_zip(file: UploadFile, destination: Path) -> None:
    """Copy an upload to disk a megabyte at a time, stopping at the archive limit.

    The same 422 as `_extract_zip` gives, but raised before the rest of an oversized
    body is copied into memory.
    """
    remaining = MAX_ARCHIVE_BYTES
    with destination.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            remaining -= len(chunk)
            if remaining < 0:
                raise AppError(
                    VALIDATION_ERROR,
                    f"Archive is larger than {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB",
                    status_code=422,
                )
            out.write(chunk)


async def _read_folder(
    files: list[UploadFile], paths: list[str]
) -> list[tuple[str, bytes]]:
    """Pair each uploaded file with its path, refusing more than the size limit.

    Each read is capped at what is left of the limit, so an oversized upload is
    refused without being held in memory.
    """
    if len(paths) != len(files):
        raise AppError(
            VALIDATION_ERROR,
            "A folder upload needs exactly one `paths` entry per file",
            status_code=422,
            details={"files": len(files), "paths": len(paths)},
        )

    folder: list[tuple[str, bytes]] = []
    remaining = MAX_EXTRACTED_BYTES
    for upload, path in zip(files, paths, strict=True):
        data = await upload.read(remaining + 1)
        remaining -= len(data)
        if remaining < 0:
            raise AppError(
                VALIDATION_ERROR,
                f"Upload is larger than {MAX_EXTRACTED_BYTES // (1024 * 1024)} MB",
                status_code=422,
            )
        folder.append((path, data))
    return folder


async def _fixture_from_json(request: Request) -> tuple[str, str | None]:
    try:
        body: Any = await request.json()
    except Exception as exc:  # noqa: BLE001 - a malformed body is a 422, not a 500
        raise AppError(
            VALIDATION_ERROR,
            "Send a multipart `file` upload or a JSON body with demo_fixture_id",
            status_code=422,
        ) from exc

    parsed = ImportFixtureRequest.model_validate(body)
    return parsed.demo_fixture_id, parsed.name


@router.get("/repositories")
async def list_repositories() -> list[RepositoryRecord]:
    return await ingestion.list_repositories()


@router.get("/repositories/{repo_id}")
async def get_repository(repo_id: str) -> RepositoryRecord:
    """One repository, with its indexing progress attached (SHARED_CONTRACTS §11).

    `search_index` is composed here and never stored: the record is written once at
    import and indexing finishes after it, so a persisted copy would be stale within
    seconds. `GET /repositories` deliberately leaves it null - `progress.get()` is a
    query per repository, and it writes when it finds indexing a restart interrupted,
    so filling a list would mean N reads and up to N writes for one page load.
    """
    record = await ingestion.get_repository(repo_id)

    try:
        from app.search import progress

        status = await progress.get(repo_id)
    except Exception as exc:  # noqa: BLE001 - a repository still reads without it
        log.warning("could not read indexing status for %s: %s", repo_id, exc)
        status = None

    if status:
        record.search_index = {
            "state": status.get("state"),
            "embedded": status.get("embedded"),
            "total": status.get("total"),
        }
    return record


@router.get("/repositories/{repo_id}/graph")
async def get_graph(
    repo_id: str,
    level: str = Query("module"),
    run_id: str | None = Query(None),
) -> GraphPayload:
    if level != "module":
        raise AppError(
            VALIDATION_ERROR,
            f"Only module-level graphs are supported, got {level!r}",
            status_code=422,
            details={"supported": ["module"]},
        )
    return await graph_service.get_graph(repo_id, run_id)


@router.get("/repositories/{repo_id}/modules/{module_id}")
async def get_module_detail(repo_id: str, module_id: str) -> ModuleDetail:
    return await graph_service.get_module_detail(repo_id, module_id)


@router.get("/repositories/{repo_id}/symbols/{symbol_id}")
async def get_symbol(repo_id: str, symbol_id: str) -> dict[str, Any]:
    """`SymbolRecord` plus the source it spans (§6).

    `warnings` is additive (§11): it says when the source cannot be shown, e.g. the
    snapshot is not on this machine, so an empty `source` is never left unexplained.
    """
    symbol, source, warnings = await graph_service.get_symbol_detail(repo_id, symbol_id)
    return {**symbol.model_dump(), "source": source, "warnings": warnings}


@router.get("/repositories/{repo_id}/runtime")
async def get_runtime(repo_id: str) -> dict:
    """Declared source requirements only; not a claim of runtime compatibility."""
    from app.ingestion.runtime import inspect_runtime

    await ingestion.get_repository(repo_id)
    return inspect_runtime(ingestion.snapshot_path(repo_id))


@router.get("/runs/{run_id}/dependency-changes")
async def get_dependency_changes(run_id: str) -> dict[str, Any]:
    """What the run did to the project's dependencies (#34).

    Imports added, removed and renamed per module, Python 2 builtins replaced, graph
    edges added and removed, third-party packages, and the declared Python version,
    legacy snapshot vs the run's last accepted revision. Every item has a `basis`:
    `static_ast` (read from the code), `py2_rename_table` (Python's documented
    Python 2 -> 3 renames) or `name_match` (a heuristic name match).
    """
    from app.graph.changes import dependency_changes

    return await dependency_changes(run_id)


@router.get("/repositories/{repo_id}/security")
async def get_repository_security(repo_id: str) -> dict[str, Any]:
    """Known-vulnerable declared packages and the Python 2 runtime note (#42).

    Only package names and versions are sent to OSV (osv.dev), never code.
    `packages.status` is `checked`, `no_packages`, `nothing_pinned` or `not_run`;
    `not_run` means OSV could not be reached and is never a pass.
    """
    from app.analysis.security import repository_security

    return await repository_security(repo_id)


@router.get("/runs/{run_id}/security")
async def get_run_security(run_id: str) -> dict[str, Any]:
    """Legacy vs the run's last accepted revision (#42): advisories the migration
    fixed, introduced or left in place, plus both sides' full reports."""
    from app.analysis.security import run_security

    return await run_security(run_id)
