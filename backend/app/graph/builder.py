"""Dependency graph construction - owner: Dev 3. Spec §5.2, §8.1.

Turns the AST analyses into the module graph. Two rules decide whether this graph can
be trusted:

  - **A → B means A depends on B** (A imports B). Everything downstream - the
    scheduler, the eligibility rules, the impacted-tests gate - reads it that way.
  - An edge is `confirmed` only when the import resolves to a file that is actually
    in the snapshot. Anything else is `heuristic` or `unresolved` and is **excluded
    from the scheduling graph**, because migrating in an order derived from a guess
    is worse than refusing to guess.
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

import networkx as nx

from app.analysis.astwalk import ImportRef, ModuleAnalysis
from app.analysis.identifiers import edge_id_for, external_id_for
from app.models.contracts import GraphEdge, GraphNode

log = logging.getLogger(__name__)

# Edge types that express "A depends on B" for scheduling purposes.
DEPENDENCY_EDGE_TYPES = frozenset({"imports", "from_imports"})


def build_nodes(analyses: dict[str, ModuleAnalysis]) -> list[GraphNode]:
    """One node per analysed module, plus one per external package referenced."""
    nodes: list[GraphNode] = []

    for analysis in sorted(analyses.values(), key=lambda a: a.module_id):
        nodes.append(
            GraphNode(
                id=analysis.module_id,
                label=PurePosixPath(analysis.path).name,
                kind="test_module" if analysis.is_test else "local_module",
                path=analysis.path,
                function_count=analysis.function_count,
                class_count=analysis.class_count,
                warnings=list(analysis.warnings),
            )
        )
    return nodes


def build_edges(
    analyses: dict[str, ModuleAnalysis],
) -> tuple[list[GraphEdge], list[GraphNode], list[str]]:
    """Resolve every import into an edge.

    Returns (edges, external nodes, warnings). Edges are deduped per
    (source, target, type) keeping the **first** line, so a module importing another
    five times is one dependency, not five.
    """
    by_dotted = {a.dotted: a.module_id for a in analyses.values()}
    externals: dict[str, GraphNode] = {}
    warnings: list[str] = []
    seen: dict[tuple[str, str, str], GraphEdge] = {}

    for analysis in sorted(analyses.values(), key=lambda a: a.module_id):
        for reference in analysis.imports:
            edge = _resolve(analysis, reference, by_dotted, externals, warnings)
            if edge is None:
                continue
            key = (edge.source, edge.target, edge.type)
            # First occurrence wins, so source_line points at the first import.
            seen.setdefault(key, edge)

    edges = sorted(seen.values(), key=lambda e: (e.source, e.target, e.type))
    external_nodes = [externals[k] for k in sorted(externals)]
    return edges, external_nodes, warnings


def _resolve(
    analysis: ModuleAnalysis,
    reference: ImportRef,
    by_dotted: dict[str, str],
    externals: dict[str, GraphNode],
    warnings: list[str],
) -> GraphEdge | None:
    source = analysis.module_id

    if reference.edge_type == "unknown_dynamic":
        warnings.append(
            f"{analysis.path}:{reference.line}: dynamic import "
            f"({reference.detail or 'not statically resolvable'})"
        )
        target = (
            by_dotted.get(reference.target)
            or (external_id_for(reference.target) if reference.target != "<dynamic>"
                else "ext_unknown")
        )
        if target.startswith("ext_"):
            _ensure_external(externals, target)
        return _edge(
            source, target, "unknown_dynamic", "unresolved", analysis.path,
            reference.line,
        )

    candidates = _candidate_names(analysis, reference)
    for candidate in _python2_siblings(analysis, reference, candidates) + candidates:
        if candidate in by_dotted:
            return _edge(
                source, by_dotted[candidate], reference.edge_type, "confirmed",
                analysis.path, reference.line,
            )

    # Not in the snapshot, so it is a third-party or stdlib package.
    head = candidates[0] if candidates else reference.target
    if not head:
        return None
    external = external_id_for(head)
    _ensure_external(externals, external)
    return _edge(
        source, external, reference.edge_type, "confirmed", analysis.path,
        reference.line,
    )


def _candidate_names(analysis: ModuleAnalysis, reference: ImportRef) -> list[str]:
    """Dotted names this import could mean, most specific first."""
    if reference.level:
        base = _relative_base(analysis, reference.level)
        root = f"{base}.{reference.target}" if base and reference.target else (
            base or reference.target
        )
    else:
        root = reference.target

    if not root:
        return []

    candidates: list[str] = []
    # `from pkg import tax` may mean the submodule pkg.tax.
    for name in reference.names:
        candidates.append(f"{root}.{name}")
    candidates.append(root)

    # `import a.b.c` where only `a.b` is ours: walk the prefixes down.
    parts = root.split(".")
    for stop in range(len(parts) - 1, 0, -1):
        candidates.append(".".join(parts[:stop]))
    return candidates


def _python2_siblings(
    analysis: ModuleAnalysis, reference: ImportRef, candidates: list[str]
) -> list[str]:
    """Python 2 (#24): inside a package, `import board` means the sibling `pkg.board`
    before any top-level `board`.

    Only for files that are certainly Python 2 - they needed the Python 2 grammar -
    and have not opted out with `from __future__ import absolute_import`. Kept apart
    from `_candidate_names` so an unmatched import is still named for what was
    written (`ext_json`), never for the guessed sibling (`ext_pkg`).
    """
    package = _package_of(analysis)
    if reference.level or not analysis.python2_syntax or analysis.absolute_import \
            or not package:
        return []
    return [f"{package}.{c}" for c in candidates]


def _package_of(analysis: ModuleAnalysis) -> str:
    """`pkg.game` -> `pkg`; a package's own `__init__.py` -> the package itself."""
    if analysis.path.endswith("__init__.py"):
        return analysis.dotted
    return analysis.dotted.rpartition(".")[0]


def _relative_base(analysis: ModuleAnalysis, level: int) -> str:
    """`from ..pkg import x` inside a.b.c -> base `a` for level 2.

    Level 1 is the module's own package: the parent for `a/b/c.py`, but the package
    itself for `a/b/__init__.py`. Counting from the dotted name alone sent
    `from .game import Game` in `local_chess/__init__.py` one level too high, so it
    became an external package `ext_game` instead of an edge to `local_chess/game.py`.
    """
    package = _package_of(analysis)
    parts = package.split(".") if package else []
    trimmed = parts[: max(0, len(parts) - (level - 1))]
    return ".".join(trimmed)


def _ensure_external(externals: dict[str, GraphNode], node_id: str) -> None:
    externals.setdefault(
        node_id,
        GraphNode(
            id=node_id,
            label=node_id.removeprefix("ext_"),
            kind="external_package",
            path=None,
        ),
    )


def _edge(
    source: str, target: str, edge_type: str, resolution: str,
    source_path: str, line: int,
) -> GraphEdge:
    return GraphEdge(
        id=edge_id_for(source, target, edge_type, line),
        source=source,
        target=target,
        type=edge_type,
        resolution=resolution,
        basis="static_ast",
        source_path=source_path,
        source_line=line,
    )


def add_test_target_edges(
    analyses: dict[str, ModuleAnalysis], edges: list[GraphEdge]
) -> list[GraphEdge]:
    """Mark which modules each test file exercises.

    A test module importing `pricing` yields `test_targets`. These are reported but
    never used for scheduling - test files are not migrated.
    """
    by_dotted = {a.dotted: a.module_id for a in analyses.values()}
    extra: dict[tuple[str, str, str], GraphEdge] = {}

    for analysis in analyses.values():
        if not analysis.is_test:
            continue
        for reference in analysis.imports:
            if reference.edge_type == "unknown_dynamic":
                continue
            for candidate in _candidate_names(analysis, reference):
                target = by_dotted.get(candidate)
                if target and target != analysis.module_id:
                    key = (analysis.module_id, target, "test_targets")
                    extra.setdefault(key, _edge(
                        analysis.module_id, target, "test_targets", "confirmed",
                        analysis.path, reference.line,
                    ))
                    break

    return [*edges, *sorted(extra.values(), key=lambda e: (e.source, e.target))]


def to_networkx(nodes: list[GraphNode], edges: list[GraphEdge]) -> nx.DiGraph:
    """The scheduling graph: local modules and confirmed dependency edges only.

    Deliberately narrow. Test modules are excluded because they are never migrated;
    external packages because we cannot change them; heuristic and unresolved edges
    because an order derived from a guess is worse than refusing to guess.
    """
    graph = nx.DiGraph()

    local = {n.id: n for n in nodes if n.kind == "local_module"}
    for node_id, node in local.items():
        graph.add_node(node_id, path=node.path, label=node.label)

    for edge in edges:
        if edge.type not in DEPENDENCY_EDGE_TYPES:
            continue
        if edge.resolution != "confirmed":
            continue
        if edge.source not in local or edge.target not in local:
            continue
        graph.add_edge(
            edge.source, edge.target, type=edge.type, resolution=edge.resolution
        )

    return graph
