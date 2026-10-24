"""Which uploads may be executed - owner: Dev 2. Spec §3.4, §0.5, #21. Offline.

The gate that stops arbitrary uploaded code from running. Since 24 Oct the demo needs
an *upload* of chess to be executable, so "uploads are never executable" became "an
upload that is an exact copy of a trusted tree in this repository is executable".

These tests are the gate's spec: a faithful copy passes, anything else does not.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.ingestion import repo_root, trust

CHESS = "demo/chess"


def chess_tree() -> Path:
    tree = repo_root() / CHESS
    if not tree.is_dir():
        pytest.skip("the chess source is not in this checkout")
    return tree


def copy_upload(destination: Path, *, crlf: bool = False) -> list[str]:
    """Stand in for an upload: the same bytes, optionally with Windows newlines."""
    source = chess_tree()
    copied: list[str] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source).as_posix()
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if crlf:
            body = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            target.write_bytes(body)
        else:
            shutil.copy2(path, target)
        copied.append(relative)
    return copied


def test_an_exact_copy_of_the_chess_source_is_recognised(tmp_path):
    copied = copy_upload(tmp_path)
    assert trust.trusted_fixture_for(tmp_path, copied) == "chess"


def test_the_same_source_with_windows_line_endings_is_still_recognised(tmp_path):
    """The one difference the same logical source legitimately has across machines.

    `docs/chess-source-manifest.json` was generated on a CRLF checkout, so 13 of its
    16 hashes disagree with the identical source here. A gate that did not normalise
    would accept chess on the uploader's machine and reject it everywhere else.
    """
    copied = copy_upload(tmp_path, crlf=True)
    assert trust.trusted_fixture_for(tmp_path, copied) == "chess"


def test_one_changed_byte_is_not_the_trusted_source(tmp_path):
    copied = copy_upload(tmp_path)
    board = tmp_path / "local_chess" / "board.py"
    board.write_text(board.read_text(encoding="utf-8") + "\nx = 1\n", encoding="utf-8")

    assert trust.trusted_fixture_for(tmp_path, copied) is None


def test_an_added_file_is_not_the_trusted_source(tmp_path):
    """The attack this gate exists for: trusted source plus one file of your own."""
    copied = copy_upload(tmp_path)
    (tmp_path / "local_chess" / "payload.py").write_text(
        "import os\nos.system('id')\n", encoding="utf-8"
    )
    copied.append("local_chess/payload.py")

    assert trust.trusted_fixture_for(tmp_path, copied) is None


def test_a_missing_file_is_not_the_trusted_source(tmp_path):
    copied = copy_upload(tmp_path)
    (tmp_path / "local_chess" / "rules.py").unlink()
    copied.remove("local_chess/rules.py")

    assert trust.trusted_fixture_for(tmp_path, copied) is None


def test_a_file_moved_to_another_path_is_not_the_trusted_source(tmp_path):
    """Content alone is not enough; the layout is part of the identity."""
    copied = copy_upload(tmp_path)
    source = tmp_path / "local_chess" / "rules.py"
    source.rename(tmp_path / "local_chess" / "rules_renamed.py")
    copied[copied.index("local_chess/rules.py")] = "local_chess/rules_renamed.py"

    assert trust.trusted_fixture_for(tmp_path, copied) is None


def test_an_unrelated_upload_matches_nothing(tmp_path):
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    assert trust.trusted_fixture_for(tmp_path, ["app.py"]) is None


def test_an_empty_upload_matches_nothing(tmp_path):
    assert trust.trusted_fixture_for(tmp_path, []) is None


def test_the_legacy_orders_fixture_is_also_recognised_by_content(tmp_path):
    """The backup demo, so the rule is not chess-shaped by accident."""
    source = repo_root() / "demo" / "legacy_orders"
    if not source.is_dir():
        pytest.skip("the legacy_orders fixture is not in this checkout")

    copied = []
    for path in sorted(source.rglob("*")):
        if path.is_file():
            relative = path.relative_to(source).as_posix()
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied.append(relative)

    assert trust.trusted_fixture_for(tmp_path, copied) == "legacy_orders"


def test_a_missing_trusted_tree_is_a_warning_not_a_crash(tmp_path, monkeypatch):
    """A checkout without the fixtures must still import uploads, just not trust them."""
    monkeypatch.setattr(trust, "TRUSTED_TREES", {"ghost": "demo/not-here"})
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    assert trust.trusted_fixture_for(tmp_path, ["app.py"]) is None


def test_normalising_does_not_depend_on_where_the_newline_falls(tmp_path):
    """A CRLF split across read buffers would hash differently if streamed."""
    big = tmp_path / "big.py"
    body = ("x = 1" + "\r\n") * 20000
    big.write_bytes(body.encode("utf-8"))

    plain = tmp_path / "plain.py"
    plain.write_bytes(body.replace("\r\n", "\n").encode("utf-8"))

    assert trust.normalised_hash(big) == trust.normalised_hash(plain)
