"""Workspaces and patch application - owner: Dev 4. SHARED_CONTRACTS §8.

A workspace is a throwaway copy of the last **accepted** revision. The model's patch
is applied here and nowhere else, so three things hold by construction:

  - **The baseline is never written to.** Workspaces are copies; the snapshot is only
    ever read. T-09 asserts its hash is unchanged after a full run.
  - **A rejected attempt leaves nothing behind.** Every attempt gets a fresh workspace
    from the last accepted revision, so invalid partial edits cannot accumulate.
  - **A patch can only touch the files its unit owns.** `allowed_paths` comes from
    `MigrationUnit.file_paths`; everything else is refused with a reason the model
    can act on.

`apply_patch` is the single chokepoint where model output becomes files on disk, so
its validation is deliberately paranoid and ordered cheapest-first.
"""

from __future__ import annotations

import ast
import difflib
import logging
import secrets
import shutil
from pathlib import Path

from app.config import get_settings
from app.models.contracts import PatchAck, PatchProposal, Workspace

log = logging.getLogger(__name__)

BASELINE_REVISION = "rev_baseline"
REVISION_PREFIX = "rev_"
MAX_PATCH_BYTES = 64 * 1024          # §4: FilePatch.new_content, max 64 KB
PROTECTED_PREFIXES = ("tests/", "test/")


def new_revision_id() -> str:
    return REVISION_PREFIX + secrets.token_hex(6)


def workspace_root() -> Path:
    return Path(get_settings().workspace_root).expanduser().resolve()


def revision_path(run_id: str, revision: str) -> Path:
    """WORKSPACE_ROOT/runs/<run_id>/<revision>/ - one directory per revision."""
    return workspace_root() / "runs" / run_id / revision


async def workspace_path(run_id: str, revision: str) -> Path:
    """Where an accepted revision's tree lives, for the archive artefact."""
    return revision_path(run_id, revision)


# --------------------------------------------------------------------------
# creating
# --------------------------------------------------------------------------

async def create_workspace(
    run_id: str, repo_id: str, base_revision: str = BASELINE_REVISION
) -> Workspace:
    """A fresh copy of `base_revision`. Never reuses a previous attempt's tree."""
    from app.ingestion import snapshot_path

    source = (
        snapshot_path(repo_id)
        if base_revision == BASELINE_REVISION
        else revision_path(run_id, base_revision)
    )
    if not source.is_dir():
        raise FileNotFoundError(f"cannot copy from missing revision: {source}")

    revision = new_revision_id()
    destination = revision_path(run_id, revision)
    destination.parent.mkdir(parents=True, exist_ok=True)

    # The source may be read-only (an accepted revision or the snapshot), so the
    # copy is made writable again - the whole point of a candidate workspace.
    shutil.copytree(source, destination, dirs_exist_ok=True)
    _set_writable(destination)

    log.info("workspace %s for %s from %s", revision, run_id, base_revision)
    return Workspace(
        workspace_id=f"ws_{revision.removeprefix(REVISION_PREFIX)}",
        run_id=run_id,
        root=str(destination),
        base_revision=base_revision,
        revision_id=revision,
    )


# --------------------------------------------------------------------------
# applying
# --------------------------------------------------------------------------

async def apply_patch(
    workspace: Workspace, patch: PatchProposal, allowed_paths: list[str]
) -> PatchAck:
    """Validate and write a patch. Every rejection says why, for the model to act on.

    Validation is all-or-nothing: if any file fails, **none** are written. A
    half-applied patch would be verified as though it were what the model proposed.
    """
    root = Path(workspace.root).resolve()
    allowed = {p.replace("\\", "/") for p in allowed_paths}
    errors: list[str] = []
    staged: list[tuple[Path, str]] = []

    if not patch.files:
        return PatchAck(accepted=False, workspace_revision=None,
                        errors=["the patch contains no files"])

    for file_patch in patch.files:
        relative = file_patch.path.replace("\\", "/").removeprefix("./")

        if relative not in allowed:
            errors.append(
                f"{file_patch.path}: not in this unit's files. This unit may only "
                f"modify: {', '.join(sorted(allowed)) or '(none)'}"
            )
            continue

        if relative.startswith(PROTECTED_PREFIXES):
            # The oracle is not the model's to edit (§0.4).
            errors.append(f"{file_patch.path}: tests are protected and cannot be patched")
            continue

        target = (root / relative).resolve()
        if not _is_within(target, root):
            errors.append(f"{file_patch.path}: resolves outside the workspace")
            continue

        encoded = file_patch.new_content.encode("utf-8", errors="replace")
        if len(encoded) > MAX_PATCH_BYTES:
            errors.append(
                f"{file_patch.path}: {len(encoded)} bytes exceeds the "
                f"{MAX_PATCH_BYTES} byte limit"
            )
            continue

        if relative.endswith(".py"):
            try:
                ast.parse(file_patch.new_content, filename=relative)
            except SyntaxError as exc:
                # The model can fix this, so give it the real message.
                errors.append(
                    f"{file_patch.path}: syntax error at line {exc.lineno}: {exc.msg}"
                )
                continue

        staged.append((target, file_patch.new_content))

    if errors:
        log.info("patch rejected for %s: %s", workspace.run_id, errors)
        return PatchAck(accepted=False, workspace_revision=None, errors=errors)

    for target, content in staged:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")

    return PatchAck(
        accepted=True, workspace_revision=workspace.revision_id, errors=[]
    )


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------
# accepting
# --------------------------------------------------------------------------

async def accept_workspace(workspace: Workspace) -> str:
    """Freeze this revision and return its id. Nothing writes to it again."""
    root = Path(workspace.root)
    if not root.is_dir():
        raise FileNotFoundError(f"workspace is gone: {root}")
    _set_read_only(root)
    log.info("accepted revision %s for %s", workspace.revision_id, workspace.run_id)
    return workspace.revision_id


# --------------------------------------------------------------------------
# diffing
# --------------------------------------------------------------------------

async def diff(
    run_id: str, base_revision: str, revision: str, paths: list[str] | None = None
) -> list[dict]:
    """Per-file unified diff between two revisions.

    `rev_baseline` has no run directory: it is the repository's snapshot, the same
    tree `create_workspace` copies the first workspace from.
    """
    if base_revision == BASELINE_REVISION:
        from app.ingestion import snapshot_path
        from app.verification import repository_for_run

        base_root = snapshot_path(await repository_for_run(run_id))
    else:
        base_root = revision_path(run_id, base_revision)
    new_root = revision_path(run_id, revision)

    if not new_root.is_dir():
        return []

    wanted = {p.replace("\\", "/") for p in paths} if paths else None
    results: list[dict] = []

    for new_file in sorted(new_root.rglob("*")):
        if not new_file.is_file() or "__pycache__" in new_file.parts:
            continue
        relative = new_file.relative_to(new_root).as_posix()
        if wanted is not None and relative not in wanted:
            continue

        old_file = base_root / relative
        old_content = (
            old_file.read_text(encoding="utf-8", errors="replace")
            if old_file.is_file() else ""
        )
        new_content = new_file.read_text(encoding="utf-8", errors="replace")
        if old_content == new_content:
            continue

        unified = "".join(
            difflib.unified_diff(
                old_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
        results.append({
            "path": relative,
            "old_content": old_content,
            "new_content": new_content,
            "unified_diff": unified,
        })

    return results


# --------------------------------------------------------------------------
# permissions
# --------------------------------------------------------------------------

# The sandbox container runs as uid 65534 (nobody), which does not own these files.
# Owner-only modes (0o600 / 0o400) make the tree unreadable inside the container, so
# every case fails to import and pytest cannot even collect - and on Windows, where
# chmod only toggles a read-only attribute, that failure is invisible. World-readable
# is required for the runner to work at all; the mounts are `:ro`, so the container
# still cannot write.
FILE_READ_ONLY = 0o444
FILE_WRITABLE = 0o644
DIR_READ_ONLY = 0o555      # dirs need +x to be traversed
DIR_WRITABLE = 0o755


def _set_read_only(root: Path) -> None:
    """Best effort. The real protection is that no code path writes to an accepted
    revision; this makes an accident loud rather than silent."""
    _chmod_tree(root, FILE_READ_ONLY, DIR_READ_ONLY, "freeze")


def _set_writable(root: Path) -> None:
    _chmod_tree(root, FILE_WRITABLE, DIR_WRITABLE, "make writable")


def _chmod_tree(root: Path, file_mode: int, dir_mode: int, what: str) -> None:
    try:
        for path in sorted(root.rglob("*"), reverse=True):
            path.chmod(file_mode if path.is_file() else dir_mode)
        root.chmod(dir_mode)
    except OSError as exc:  # noqa: BLE001
        log.warning("could not %s %s: %s", what, root, exc)
