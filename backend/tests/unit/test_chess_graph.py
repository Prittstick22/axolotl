"""The chess demo's own import graph, read from `demo/chess` on disk (#137, from #125).

`tests/unit/test_graph_relative_imports.py` pins the relative-import rule on a small
chess-shaped package. This pins it on the fixture the demo actually runs, so a
regression shows up in CI rather than on the Graph screen: before #124,
`from .game import Game` in `local_chess/__init__.py` resolved one level too high and
became two packages that don't exist, `ext_game` and `ext_game_state`.

No database: parsing and graph construction are pure.
"""

import pytest

from app.analysis.astwalk import analyse_module
from app.graph.builder import build_edges
from app.ingestion import fixture_path
from app.ingestion.safety import is_ignored


@pytest.fixture(scope="module")
def graph():
    root = fixture_path("chess")
    if not root.is_dir():
        pytest.skip(f"chess fixture not found at {root}")
    analyses = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if is_ignored(relative):
            continue
        analysis = analyse_module(source_path=path, relative_path=relative,
                                  repository_id="repo_chess", snapshot_hash="sha256:test")
        analyses[analysis.module_id] = analysis
    edges, externals, _ = build_edges(analyses)
    return edges, {node.id for node in externals}


def test_the_package_init_imports_its_own_modules(graph):
    edges, _ = graph
    from_init = {(e.target, e.resolution) for e in edges if e.source == "module_local_chess"}
    assert ("module_local_chess__game", "confirmed") in from_init
    assert ("module_local_chess__game_state", "confirmed") in from_init


def test_no_phantom_packages_from_the_init(graph):
    _, externals = graph
    assert "ext_game" not in externals
    assert "ext_game_state" not in externals
