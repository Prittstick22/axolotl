"""Workspaces and patch validation - owner: Dev 4.

`apply_patch` is the single point where model output becomes files on disk, so most
of this file is about what it **refuses**. Everything here is pure filesystem work -
no Docker, no database.
"""

from pathlib import Path

import pytest

from app.models.contracts import FilePatch, PatchProposal
from app.sandbox import workspaces
from app.sandbox.workspaces import (
    BASELINE_REVISION,
    MAX_PATCH_BYTES,
    accept_workspace,
    apply_patch,
    create_workspace,
    diff,
)

ALLOWED = ["pricing.py"]


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A fake snapshot plus a workspace root, both under tmp."""
    snapshot = tmp_path / "snapshot"
    (snapshot / "tests").mkdir(parents=True)
    (snapshot / "pricing.py").write_text("TAX = 1\n", encoding="utf-8")
    (snapshot / "orders.py").write_text("import pricing\n", encoding="utf-8")
    (snapshot / "tests" / "test_baseline.py").write_text("def test_x(): pass\n",
                                                         encoding="utf-8")

    root = tmp_path / "ws"
    monkeypatch.setattr(workspaces, "workspace_root", lambda: root)

    import app.ingestion as ingestion_module

    monkeypatch.setattr(ingestion_module, "snapshot_path", lambda repo_id: snapshot)
    return {"snapshot": snapshot, "root": root}


def patch_for(path: str, content: str) -> PatchProposal:
    return PatchProposal(
        unit_id="unit_pricing",
        files=[FilePatch(path=path, new_content=content)],
        summary="modernise", rationale="types",
    )


# --------------------------------------------------------------------------
# creating
# --------------------------------------------------------------------------

async def test_a_workspace_is_a_copy_of_the_baseline(world):
    ws = await create_workspace("run_1", "repo_1", BASELINE_REVISION)
    root = Path(ws.root)

    assert (root / "pricing.py").read_text() == "TAX = 1\n"
    assert root != world["snapshot"]
    assert ws.base_revision == BASELINE_REVISION
    assert ws.revision_id.startswith("rev_")


async def test_writing_to_a_workspace_never_touches_the_baseline(world):
    """The whole immutable-baseline guarantee in one assertion."""
    before = (world["snapshot"] / "pricing.py").read_text()

    ws = await create_workspace("run_1", "repo_1")
    await apply_patch(ws, patch_for("pricing.py", "TAX = 2\n"), ALLOWED)

    assert (world["snapshot"] / "pricing.py").read_text() == before
    assert (Path(ws.root) / "pricing.py").read_text() == "TAX = 2\n"


async def test_each_attempt_gets_a_fresh_workspace(world):
    """A rejected attempt must not leak into the next one."""
    first = await create_workspace("run_1", "repo_1")
    await apply_patch(first, patch_for("pricing.py", "TAX = 99\n"), ALLOWED)

    second = await create_workspace("run_1", "repo_1")

    assert second.revision_id != first.revision_id
    assert (Path(second.root) / "pricing.py").read_text() == "TAX = 1\n"


# --------------------------------------------------------------------------
# what apply_patch refuses
# --------------------------------------------------------------------------

async def test_a_file_outside_the_unit_is_refused(world):
    ws = await create_workspace("run_1", "repo_1")
    ack = await apply_patch(ws, patch_for("orders.py", "x = 1\n"), ALLOWED)

    assert ack.accepted is False
    assert any("not in this unit's files" in e for e in ack.errors)
    assert (Path(ws.root) / "orders.py").read_text() == "import pricing\n"


async def test_the_test_oracle_cannot_be_patched(world):
    """§0.4: nothing ever writes into the test oracle."""
    ws = await create_workspace("run_1", "repo_1")
    ack = await apply_patch(
        ws, patch_for("tests/test_baseline.py", "def test_x(): assert True\n"),
        ["tests/test_baseline.py"],   # even if somehow allowlisted
    )

    assert ack.accepted is False
    assert any("protected" in e for e in ack.errors)
    assert "pass" in (Path(ws.root) / "tests" / "test_baseline.py").read_text()


@pytest.mark.parametrize("path", ["../escape.py", "../../etc/passwd", "pricing/../../x.py"])
async def test_paths_escaping_the_workspace_are_refused(world, path):
    ws = await create_workspace("run_1", "repo_1")
    ack = await apply_patch(ws, patch_for(path, "x = 1\n"), [path])
    assert ack.accepted is False


async def test_a_syntax_error_is_refused_with_the_real_message(world):
    """The model can fix this, so it gets told exactly what broke."""
    ws = await create_workspace("run_1", "repo_1")
    ack = await apply_patch(ws, patch_for("pricing.py", "def broken(:\n    pass\n"), ALLOWED)

    assert ack.accepted is False
    assert any("syntax error" in e for e in ack.errors)
    assert any("line" in e for e in ack.errors)
    assert (Path(ws.root) / "pricing.py").read_text() == "TAX = 1\n"


async def test_an_oversized_patch_is_refused(world):
    ws = await create_workspace("run_1", "repo_1")
    huge = "# " + ("x" * (MAX_PATCH_BYTES + 100)) + "\n"
    ack = await apply_patch(ws, patch_for("pricing.py", huge), ALLOWED)

    assert ack.accepted is False
    assert any("exceeds" in e for e in ack.errors)


async def test_an_empty_patch_is_refused(world):
    ws = await create_workspace("run_1", "repo_1")
    ack = await apply_patch(
        ws, PatchProposal(unit_id="unit_pricing", files=[], summary="", rationale=""),
        ALLOWED,
    )
    assert ack.accepted is False


async def test_one_bad_file_rejects_the_whole_patch(world):
    """A half-applied patch would be verified as though it were what was proposed."""
    ws = await create_workspace("run_1", "repo_1")
    proposal = PatchProposal(
        unit_id="unit_pricing",
        files=[
            FilePatch(path="pricing.py", new_content="TAX = 2\n"),
            FilePatch(path="orders.py", new_content="x = 1\n"),   # not allowed
        ],
        summary="two files", rationale="",
    )
    ack = await apply_patch(ws, proposal, ALLOWED)

    assert ack.accepted is False
    assert (Path(ws.root) / "pricing.py").read_text() == "TAX = 1\n", (
        "the valid file was written despite the patch being rejected"
    )


async def test_a_valid_patch_is_applied(world):
    ws = await create_workspace("run_1", "repo_1")
    ack = await apply_patch(ws, patch_for("pricing.py", "TAX = 2\n"), ALLOWED)

    assert ack.accepted is True
    assert ack.workspace_revision == ws.revision_id
    assert ack.errors == []


async def test_a_dotted_directory_keeps_its_dot(world):
    """`.hidden/mod.py` is that path, not `hidden/mod.py` (lstrip("./") ate the dot)."""
    ws = await create_workspace("run_1", "repo_1")
    ack = await apply_patch(ws, patch_for(".hidden/mod.py", "x = 1\n"), [".hidden/mod.py"])

    assert ack.accepted is True, ack.errors
    assert (Path(ws.root) / ".hidden" / "mod.py").read_text() == "x = 1\n"
    assert not (Path(ws.root) / "hidden").exists()


async def test_a_leading_dot_slash_is_dropped(world):
    ws = await create_workspace("run_1", "repo_1")
    ack = await apply_patch(ws, patch_for("./pricing.py", "TAX = 2\n"), ALLOWED)

    assert ack.accepted is True, ack.errors
    assert (Path(ws.root) / "pricing.py").read_text() == "TAX = 2\n"


# --------------------------------------------------------------------------
# accepting and diffing
# --------------------------------------------------------------------------

async def test_accepting_returns_the_revision_id(world):
    ws = await create_workspace("run_1", "repo_1")
    await apply_patch(ws, patch_for("pricing.py", "TAX = 2\n"), ALLOWED)
    assert await accept_workspace(ws) == ws.revision_id


async def test_the_diff_shows_only_what_changed(world):
    base = await create_workspace("run_1", "repo_1")
    await accept_workspace(base)

    candidate = await create_workspace("run_1", "repo_1", base.revision_id)
    await apply_patch(candidate, patch_for("pricing.py", "TAX = 2\n"), ALLOWED)

    files = await diff("run_1", base.revision_id, candidate.revision_id)

    assert [f["path"] for f in files] == ["pricing.py"]
    assert "-TAX = 1" in files[0]["unified_diff"]
    assert "+TAX = 2" in files[0]["unified_diff"]


async def test_the_diff_from_rev_baseline_reads_the_snapshot(world, monkeypatch):
    """#44: with no baseline every file read as new."""
    import app.verification as verification_module

    async def repository_for_run(run_id):
        return "repo_1"

    monkeypatch.setattr(verification_module, "repository_for_run", repository_for_run)

    ws = await create_workspace("run_1", "repo_1")
    await apply_patch(ws, patch_for("pricing.py", "TAX = 2\n"), ALLOWED)

    files = await diff("run_1", BASELINE_REVISION, ws.revision_id)

    assert [f["path"] for f in files] == ["pricing.py"]
    assert files[0]["old_content"] == "TAX = 1\n"


async def test_an_unchanged_tree_produces_an_empty_diff(world):
    base = await create_workspace("run_1", "repo_1")
    await accept_workspace(base)
    candidate = await create_workspace("run_1", "repo_1", base.revision_id)

    assert await diff("run_1", base.revision_id, candidate.revision_id) == []


async def test_the_diff_can_be_limited_to_a_unit_s_files(world):
    base = await create_workspace("run_1", "repo_1")
    await accept_workspace(base)
    candidate = await create_workspace("run_1", "repo_1", base.revision_id)
    await apply_patch(candidate, patch_for("pricing.py", "TAX = 2\n"), ALLOWED)

    assert await diff("run_1", base.revision_id, candidate.revision_id,
                      ["orders.py"]) == []
