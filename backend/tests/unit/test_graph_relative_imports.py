"""Relative imports resolve from the module's own package - owner: Dev 3. Offline.

Found through #34 on demo/chess: `from .game import Game` in `local_chess/__init__.py`
became an external package `ext_game`, because level 1 was counted from the dotted name
as if `__init__.py` were a module inside the package rather than the package itself.
"""

from pathlib import Path

import pytest

from app.analysis.astwalk import analyse_module
from app.graph.builder import build_edges

FILES = {
    "pkg/__init__.py": "from .game import Game\nfrom . import board\n",
    "pkg/game.py": "from .board import Board\nfrom ..top import helper\n",
    "pkg/board.py": "",
    "pkg/sub/__init__.py": "from .. import game\nfrom ..board import Board\n",
    "top.py": "",
}


@pytest.fixture
def edges(tmp_path: Path):
    analyses = {}
    for relative, text in FILES.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        a = analyse_module(source_path=path, relative_path=relative,
                           repository_id="r", snapshot_hash="h")
        analyses[a.module_id] = a
    found, externals, _ = build_edges(analyses)
    return {(e.source, e.target) for e in found}, {n.id for n in externals}


def test_a_package_init_imports_from_itself(edges):
    found, externals = edges
    assert ("module_pkg", "module_pkg__game") in found
    assert ("module_pkg", "module_pkg__board") in found
    assert "ext_game" not in externals


def test_a_module_imports_from_its_parent_package(edges):
    found, _ = edges
    assert ("module_pkg__game", "module_pkg__board") in found
    assert ("module_pkg__game", "module_top") in found


def test_a_subpackage_init_goes_up_one_level_for_two_dots(edges):
    found, _ = edges
    assert ("module_pkg__sub", "module_pkg__game") in found
    assert ("module_pkg__sub", "module_pkg__board") in found
