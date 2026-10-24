"""Run artefacts - owner: Dev 2. Dev 2 brief "Reports", T-16.

Three kinds, per SHARED_CONTRACTS §6 (`kind: "patch" | "report" | "archive"`):

  patch    unified diff of the baseline against the final **accepted** revision
  archive  zip of the final accepted tree
  report   the RunReport as JSON

The project export (#40) is the migrated project itself, plus a MIGRATION.md and the
report under `axolotl/`. Not the patch: a download labelled "migrated project" that
delivered a diff was the bug. It is built on request and not recorded, so §6 keeps
its three kinds.

Files live on disk under `WORKSPACE_ROOT/artefacts/<run_id>/`. Only metadata, the path
and a sha256 go in Mongo - never blobs (§9).

**T-16**: the diff a user downloads must be the diff of the revision that was actually
verified. `verify_patch_matches_revision` recomputes it and compares hashes, so a
mismatch is caught rather than displayed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.ingestion.safety import is_ignored
from app.models.contracts import MigrationPlan, RunReport, RunSummary
from app.orchestration import ports, store
from app.orchestration.ids import new_artefact_id

log = logging.getLogger(__name__)

BASELINE_REVISION = "rev_baseline"


def artefact_root(run_id: str) -> Path:
    root = Path(get_settings().workspace_root).expanduser() / "artefacts" / run_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_unified_diff(files: list[dict[str, Any]]) -> str:
    """One patch file from Dev 4's per-file diffs, in a stable order.

    Sorted by path so the same revision always renders byte-identical - otherwise the
    T-16 hash comparison would fail on ordering alone.
    """
    chunks: list[str] = []
    for entry in sorted(files, key=lambda f: f.get("path", "")):
        unified = entry.get("unified_diff")
        if unified:
            chunks.append(unified if unified.endswith("\n") else unified + "\n")
    return "".join(chunks)


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

async def write_patch_artefact(run: RunSummary, revision: str) -> dict[str, Any] | None:
    """Unified diff of baseline -> `revision`. None when Dev 4's diff is unavailable."""
    try:
        files = await ports.diff(run.run_id, BASELINE_REVISION, revision)
    except ports.IntegrationUnavailable as exc:
        log.info("no patch artefact for %s: %s", run.run_id, exc)
        return None

    content = render_unified_diff(files)
    path = artefact_root(run.run_id) / "migration.patch"
    # newline="\n" disables Windows CRLF translation. Without it the file on disk
    # differs from the bytes we hashed, so the T-16 comparison fails on Windows and
    # the .patch gains CRLF line endings that can break `git apply`.
    path.write_text(content, encoding="utf-8", newline="\n")

    return await _record(
        run.run_id, "patch", path,
        extra={"base_revision": BASELINE_REVISION, "revision": revision,
               "changed_files": sorted(f.get("path", "") for f in files)},
    )


async def write_archive_artefact(
    run: RunSummary, tree: Path | None
) -> dict[str, Any] | None:
    """Zip of the final accepted tree. None when there is no tree to archive."""
    if tree is None or not Path(tree).is_dir():
        log.info("no archive artefact for %s: no accepted tree on disk", run.run_id)
        return None

    tree = Path(tree)
    path = artefact_root(run.run_id) / "final_tree.zip"

    # Deterministic: sorted entries and a fixed timestamp, so the same tree always
    # produces the same archive hash.
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(tree.rglob("*")):
            if item.is_file() and "__pycache__" not in item.parts:
                _add(archive, item.relative_to(tree).as_posix(), item.read_bytes())

    return await _record(run.run_id, "archive", path)


async def write_export(
    report: RunReport, plan: MigrationPlan, revision: str, tree: Path,
    docs: dict[str, str] | None = None, summary: str | None = None,
) -> Path:
    """#40: the migrated project at `revision`, with the run's files under `axolotl/`.

    Only the project. The tree is a copy of the snapshot, which ingestion already
    filtered and which cases.py checks for held-out cases; the same filter runs again
    here, so a cache or a `.env` written into the tree since cannot ship.

    The diff is deliberately **not** in here. People clicked a download expecting the
    migrated codebase and got `migration.patch`, so the zip is now the project and the
    patch stays its own artefact (`kind: "patch"`), downloaded on its own.

    `docs` (#39), when given, is the run's module documentation as Markdown files,
    written under `axolotl/docs/`. It holds only what is already generated: building
    an export never calls a model. `summary` (#35), when given, is the run's migration
    summary, written as `axolotl/SUMMARY.md`.
    """
    path = artefact_root(report.run_id) / "migrated_project.zip"

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(tree.rglob("*")):
            name = item.relative_to(tree).as_posix()
            if item.is_file() and not is_ignored(name):
                _add(archive, name, item.read_bytes())
        _add(archive, "axolotl/MIGRATION.md",
             migration_notes(report, plan, revision).encode())
        _add(archive, "axolotl/report.json", json.dumps(
            report.model_dump(mode="json"), indent=2, sort_keys=True).encode())
        if summary:
            _add(archive, "axolotl/SUMMARY.md", summary.encode())
        for name, text in sorted((docs or {}).items()):
            _add(archive, f"axolotl/docs/{name}", text.encode())
    return path


def migration_notes(
    report: RunReport, plan: MigrationPlan, revision: str
) -> str:
    """Which files were migrated and verified, and which still hold the legacy code.

    A unit that was not accepted never reached `revision`: every attempt starts from a
    fresh copy of the last accepted tree, so its files are exactly as imported.
    """
    files = {u.unit_id: u.file_paths for u in plan.units}
    units = sorted(report.units, key=lambda u: plan.order.index(u.unit_id)
                   if u.unit_id in plan.order else len(plan.order))
    migrated = [f"- {p}" for u in units if u.status == "passed"
                for p in files.get(u.unit_id, [])]
    legacy = [f"- {p} ({u.status})" for u in units if u.status != "passed"
              for p in files.get(u.unit_id, [])]
    return "\n".join([
        f"# Migrated to `{plan.target_id}` by Axolotl",
        "",
        f"Run `{report.run_id}`, verdict `{report.verdict}`, revision `{revision}`.",
        "",
        "## Migrated and verified",
        "",
        *(migrated or ["- None"]),
        "",
        "## Still legacy",
        "",
        *(legacy or ["- None. Every unit was migrated."]),
        "",
        "## What the verification means",
        "",
        *[f"- {c}" for c in report.coverage_caveats],
        "",
        "`report.json` is the full report.",
        "",
    ])


async def write_report_artefact(report: RunReport) -> dict[str, Any]:
    """The report itself, as downloadable JSON."""
    path = artefact_root(report.run_id) / "report.json"
    path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    return await _record(report.run_id, "report", path)


def _add(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data)


async def _record(
    run_id: str, kind: str, path: Path, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    artefact = {
        "artefact_id": new_artefact_id(),
        "run_id": run_id,
        "kind": kind,
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "created_at": store.utcnow(),
        **(extra or {}),
    }
    await store.save_artefact(artefact)
    log.info("artefact %s %s sha256=%s", kind, artefact["artefact_id"],
             artefact["sha256"][:12])
    return artefact


# --------------------------------------------------------------------------
# T-16
# --------------------------------------------------------------------------

async def verify_patch_matches_revision(run_id: str, revision: str) -> dict[str, Any]:
    """T-16: the stored patch must be the diff of the verified accepted revision.

    Recomputes the diff for `revision` and compares its sha256 with the stored patch
    artefact's. Returns a verdict rather than raising, so the caller can record a
    mismatch honestly instead of showing a diff that does not match what was verified.
    """
    stored = [a for a in await store.list_artefacts(run_id) if a["kind"] == "patch"]
    if not stored:
        return {"checked": False, "reason": "no patch artefact recorded"}

    artefact = stored[-1]
    if artefact.get("revision") != revision:
        return {
            "checked": True,
            "matches": False,
            "reason": (
                f"patch artefact is for revision {artefact.get('revision')!r}, "
                f"but the verified revision is {revision!r}"
            ),
        }

    try:
        files = await ports.diff(run_id, BASELINE_REVISION, revision)
    except ports.IntegrationUnavailable as exc:
        return {"checked": False, "reason": str(exc)}

    recomputed = sha256_bytes(render_unified_diff(files).encode("utf-8"))
    matches = recomputed == artefact["sha256"]
    return {
        "checked": True,
        "matches": matches,
        "revision": revision,
        "stored_sha256": artefact["sha256"],
        "recomputed_sha256": recomputed,
        "reason": None if matches else "stored patch does not match the verified revision",
    }
