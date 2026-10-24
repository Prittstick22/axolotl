"""`setup.py` is patchable, and never imported or executed - owner: Dev 2. #21.

Chess declares `python_requires='>=2.7,<3'` in `setup.py`, so a migration to 3.14 that
left that file alone would produce a package still claiming to be Python 2 only. The
file therefore has to be inside some unit's `file_paths`, which is the only allowlist a
patch may touch (`controller.submit_patch` -> `ports.apply_patch`).

It is, and by construction rather than by a special case: nothing imports `setup.py`,
so the condensation gives it a unit of its own. That is worth a test precisely because
it is incidental - a future change to the graph, the ignore rules or the unit builder
could drop it from the plan, and the only symptom would be a migrated package with the
wrong metadata, which no gate checks.

"Never imported or executed" is the other half. The analyser parses with `ast` and
imports nothing it reads, and the sandbox runs pytest and the case harness, neither of
which collects `setup.py`.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from app.analysis.astwalk import analyse_module
from app.graph.builder import build_edges
from app.ingestion import repo_root
from app.ingestion.safety import is_ignored
from app.orchestration.scheduler import build_units

SETUP = "setup.py"


def chess_units():
    root = repo_root() / "demo" / "chess"
    if not root.is_dir():
        pytest.skip("the chess source is not in this checkout")

    analyses = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if is_ignored(relative):
            continue
        analysis = analyse_module(
            source_path=path, relative_path=relative,
            repository_id="repo_chess", snapshot_hash="sha256:test",
        )
        analyses[analysis.module_id] = analysis

    edges, _external, _warnings = build_edges(analyses)
    graph = nx.DiGraph()
    graph.add_nodes_from(analyses)
    for edge in edges:
        if edge.source in analyses and edge.target in analyses:
            graph.add_edge(edge.source, edge.target)

    return build_units(graph, {m: a.path for m, a in analyses.items()}), analyses


def test_setup_py_is_in_a_unit_so_a_patch_may_touch_it():
    units, _ = chess_units()
    owning = [u for u in units if SETUP in u.file_paths]

    assert owning, (
        "setup.py is in no unit's file_paths, so no patch may touch it and a migrated "
        "chess would still declare python_requires='>=2.7,<3'"
    )
    assert len(owning) == 1, "two units claim setup.py; a patch allowlist must be clear"


def test_setup_py_is_its_own_unit_because_nothing_imports_it():
    """If something ever does import it, this fails rather than quietly bundling the
    packaging metadata into a code unit the agent is reasoning about."""
    units, _ = chess_units()
    unit = next(u for u in units if SETUP in u.file_paths)

    assert unit.file_paths == [SETUP], (
        f"setup.py shares a unit with {[f for f in unit.file_paths if f != SETUP]}"
    )
    assert not unit.is_cycle


def test_setup_py_declares_the_python_version_that_has_to_change():
    """The reason it must be patchable at all. If chess stops declaring this, the
    test above stops being load-bearing and should be revisited."""
    setup = repo_root() / "demo" / "chess" / SETUP
    if not setup.is_file():
        pytest.skip("the chess source is not in this checkout")

    body = setup.read_text(encoding="utf-8")
    assert "python_requires" in body
    assert "2.7" in body


def test_the_analyser_reads_setup_py_without_importing_it():
    """Static only: a `setup.py` that raised on import would still be analysed."""
    _units, analyses = chess_units()
    module = next(a for a in analyses.values() if a.path == SETUP)

    assert module.parsed
    # It was read as text and parsed, not executed: no module object exists for it.
    import sys

    assert not any(
        getattr(m, "__file__", "") and m.__file__.endswith("demo/chess/setup.py")
        for m in list(sys.modules.values())
        if m is not None
    )


def test_every_chess_file_a_patch_may_touch_is_a_python_file():
    """The allowlist is the unit's own files; nothing should let a patch reach
    README.md or a case file."""
    units, _ = chess_units()
    allowed = {path for unit in units for path in unit.file_paths}

    assert allowed, "no unit has any files"
    assert all(path.endswith(".py") for path in allowed), sorted(allowed)
    assert not any(Path(path).name == "cases.json" for path in allowed)
