"""Dependency changes between the legacy and the migrated code - owner: Dev 3. #34.

What a migration did to a project's dependencies, read statically from two trees:
the repository's snapshot (the legacy code) and a run's accepted revision (the
migrated code). Nothing is imported or executed; both trees are parsed the same way
an import parses them (`analyse_module`, `build_edges`, `inspect_runtime`).

Every item says how it is known:

  static_ast            a fact read from the parse trees: this import exists on one
                        side and not the other, this edge appeared, this name is used.
  py2_rename_table      a pairing from Python's own documented Python 2 -> 3 renames
                        (`ConfigParser` -> `configparser`, `raw_input` -> `input`).
                        The two names are facts; that one *replaced* the other is the
                        table's claim, so it is labelled.
  name_match            a Python 2 method name such as `.iteritems()`. It may be a
                        method of the project's own class, so it is labelled heuristic.

Computed on request, not stored. #33 (storing the migrated code's symbols in Atlas)
was closed on 24 Oct, so this reads both trees from disk, which is also why it needs
the snapshot and the run's workspace on this machine.
"""

from __future__ import annotations

import ast
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.analysis.astwalk import ModuleAnalysis, analyse_module
from app.analysis.python2 import python2_tree
from app.graph.builder import add_test_target_edges, build_edges
from app.ingestion.runtime import inspect_runtime
from app.ingestion.safety import is_ignored

log = logging.getLogger(__name__)

STATIC = "static_ast"
RENAME_TABLE = "py2_rename_table"
NAME_MATCH = "name_match"

# Python 2 standard-library modules and where they went in Python 3 (PEP 3108 and
# the `six.moves` table). Only the documented renames: nothing guessed.
PY2_MODULE_RENAMES: dict[str, str] = {
    "ConfigParser": "configparser",
    "Queue": "queue",
    "SocketServer": "socketserver",
    "cPickle": "pickle",
    "cStringIO": "io",
    "StringIO": "io",
    "copy_reg": "copyreg",
    "repr": "reprlib",
    "thread": "_thread",
    "dummy_thread": "_dummy_thread",
    "__builtin__": "builtins",
    "anydbm": "dbm",
    "dbhash": "dbm.bsd",
    "dumbdbm": "dbm.dumb",
    "gdbm": "dbm.gnu",
    "whichdb": "dbm",
    "commands": "subprocess",
    "httplib": "http.client",
    "HTMLParser": "html.parser",
    "htmlentitydefs": "html.entities",
    "BaseHTTPServer": "http.server",
    "SimpleHTTPServer": "http.server",
    "CGIHTTPServer": "http.server",
    "Cookie": "http.cookies",
    "cookielib": "http.cookiejar",
    "urllib2": "urllib.request",
    "urlparse": "urllib.parse",
    "robotparser": "urllib.robotparser",
    "xmlrpclib": "xmlrpc.client",
    "SimpleXMLRPCServer": "xmlrpc.server",
    "DocXMLRPCServer": "xmlrpc.server",
    "Tkinter": "tkinter",
    "tkMessageBox": "tkinter.messagebox",
    "UserDict": "collections",
    "UserList": "collections",
    "UserString": "collections",
}

# Python 2 builtins with a Python 3 replacement.
PY2_BUILTINS: dict[str, str] = {
    "raw_input": "input",
    "xrange": "range",
    "unicode": "str",
    "basestring": "str",
    "long": "int",
    "unichr": "chr",
    "reduce": "functools.reduce",
    "execfile": "exec",
    "file": "open",
    "intern": "sys.intern",
    "cmp": "(removed)",
    "apply": "(removed)",
    "buffer": "memoryview",
}

# Python 2 dict and iterator methods. A project may define its own, so heuristic.
PY2_METHODS: dict[str, str] = {
    "iteritems": "items",
    "itervalues": "values",
    "iterkeys": "keys",
    "has_key": "in",
    "viewitems": "items",
    "viewkeys": "keys",
    "viewvalues": "values",
}

# Python 2's standard library includes names Python 3 removed or renamed; they are
# still the standard library on the legacy side, not third-party packages.
_STDLIB = set(sys.stdlib_module_names) | {n.split(".")[0] for n in PY2_MODULE_RENAMES} | {
    "sets", "md5", "sha", "new", "popen2", "mimetools", "rfc822", "exceptions",
    "string", "types", "imp", "future_builtins", "__future__",
}


def compare_trees(before: Path, after: Path) -> dict[str, Any]:
    """Dependency changes from the tree `before` (legacy) to `after` (migrated)."""
    old = _analyse_tree(before)
    new = _analyse_tree(after)
    warnings: list[str] = []
    for side, analyses in (("legacy", old), ("migrated", new)):
        for a in analyses.values():
            if not a.parsed:
                warnings.append(f"{side} {a.path}: could not be parsed, so its "
                                "dependencies are missing from this comparison")

    old_imports = _imports_by_module(old)
    new_imports = _imports_by_module(new)
    old_names = _py2_names_by_module(before, old)
    new_names = _py2_names_by_module(after, new)

    modules: list[dict[str, Any]] = []
    for module_id in sorted(set(old) | set(new)):
        entry = _module_changes(
            module_id, old.get(module_id), new.get(module_id),
            old_imports.get(module_id, set()), new_imports.get(module_id, set()),
            old_names.get(module_id, {}), new_names.get(module_id, {}),
        )
        if entry:
            modules.append(entry)

    old_edges = _edge_keys(old)
    new_edges = _edge_keys(new)
    old_packages = _third_party(old_edges)
    new_packages = _third_party(new_edges)
    before_runtime = inspect_runtime(before)
    after_runtime = inspect_runtime(after)

    return {
        "modules": modules,
        "edges": {
            "added": [_edge_dict(k) for k in sorted(new_edges - old_edges)],
            "removed": [_edge_dict(k) for k in sorted(old_edges - new_edges)],
            "basis": STATIC,
        },
        "packages": {
            "added": sorted(new_packages - old_packages),
            "removed": sorted(old_packages - new_packages),
            "kept": sorted(old_packages & new_packages),
            "basis": STATIC,
            "note": "third-party top-level packages imported by the code",
        },
        "python": {
            "before": {"declared": before_runtime.get("declared_python"),
                       "evidence": before_runtime.get("evidence", [])},
            "after": {"declared": after_runtime.get("declared_python"),
                      "evidence": after_runtime.get("evidence", [])},
            "basis": STATIC,
        },
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# one tree
# --------------------------------------------------------------------------

def _analyse_tree(root: Path) -> dict[str, ModuleAnalysis]:
    analyses: dict[str, ModuleAnalysis] = {}
    if not root.is_dir():
        return analyses
    for path in sorted(root.rglob("*.py")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if is_ignored(relative):
            continue
        analysis = analyse_module(source_path=path, relative_path=relative,
                                  repository_id="compare", snapshot_hash="compare")
        analyses[analysis.module_id] = analysis
    return analyses


def _imports_by_module(analyses: dict[str, ModuleAnalysis]) -> dict[str, set[str]]:
    """Each module's imports, as written: `configparser`, `local_chess.board`."""
    result: dict[str, set[str]] = defaultdict(set)
    for module_id, analysis in analyses.items():
        for ref in analysis.imports:
            if ref.edge_type == "unknown_dynamic" or not ref.target:
                continue
            if ref.level:
                result[module_id].add("." * ref.level + ref.target)
            else:
                result[module_id].add(ref.target)
    return result


def _py2_names_by_module(
    root: Path, analyses: dict[str, ModuleAnalysis]
) -> dict[str, dict[str, set[str]]]:
    """Python 2 builtins and methods each module uses: {'builtins': .., 'methods': ..}."""
    result: dict[str, dict[str, set[str]]] = {}
    for module_id, analysis in analyses.items():
        if not analysis.parsed:
            continue
        try:
            tree = ast.parse(analysis.source)
        except SyntaxError:
            tree = python2_tree(analysis.source, analysis.path)
        if tree is None:
            continue
        builtins_used = {
            n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            and n.id in PY2_BUILTINS
        }
        methods_used = {
            n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr in PY2_METHODS
        }
        result[module_id] = {"builtins": builtins_used, "methods": methods_used}
    return result


def _edge_keys(analyses: dict[str, ModuleAnalysis]) -> set[tuple[str, str, str]]:
    edges, _, _ = build_edges(analyses)
    edges = add_test_target_edges(analyses, edges)
    return {(e.source, e.target, e.type) for e in edges}


def _edge_dict(key: tuple[str, str, str]) -> dict[str, str]:
    source, target, edge_type = key
    return {"source": source, "target": target, "type": edge_type}


def _third_party(edges: set[tuple[str, str, str]]) -> set[str]:
    """Imports the graph resolved to an external package, minus the standard library.

    From resolved edges, not import names: chess's tests import `local_chess`, which
    is the project itself, not a package.
    """
    tops = {target.removeprefix("ext_") for _, target, _ in edges
            if target.startswith("ext_")}
    return {t for t in tops if t not in _STDLIB}


# --------------------------------------------------------------------------
# one module
# --------------------------------------------------------------------------

def _module_changes(
    module_id: str, old: ModuleAnalysis | None, new: ModuleAnalysis | None,
    old_imports: set[str], new_imports: set[str],
    old_names: dict[str, set[str]], new_names: dict[str, set[str]],
) -> dict[str, Any] | None:
    path = (new or old).path  # type: ignore[union-attr]
    if old is None:
        status = "added"
    elif new is None:
        status = "removed"
    else:
        status = "changed"

    added = new_imports - old_imports
    removed = old_imports - new_imports

    # A removed Python 2 module with its documented Python 3 name added: a rename.
    renamed: list[dict[str, str]] = []
    for name in sorted(removed):
        target = PY2_MODULE_RENAMES.get(name)
        if target and target in added:
            renamed.append({"before": name, "after": target, "basis": RENAME_TABLE})
    paired_before = {r["before"] for r in renamed}
    paired_after = {r["after"] for r in renamed}

    builtins = _replaced(old_names.get("builtins", set()),
                         new_names.get("builtins", set()), PY2_BUILTINS, RENAME_TABLE)
    methods = _replaced(old_names.get("methods", set()),
                        new_names.get("methods", set()), PY2_METHODS, NAME_MATCH)

    entry = {
        "module_id": module_id,
        "path": path,
        "status": status,
        "imports_added": [{"name": n, "basis": STATIC}
                          for n in sorted(added - paired_after)],
        "imports_removed": [{"name": n, "basis": STATIC}
                            for n in sorted(removed - paired_before)],
        "stdlib_renamed": renamed,
        "builtins_replaced": builtins,
        "methods_replaced": methods,
    }
    has_change = status != "changed" or any(
        entry[k] for k in ("imports_added", "imports_removed", "stdlib_renamed",
                           "builtins_replaced", "methods_replaced")
    )
    return entry if has_change else None


def _replaced(
    before: set[str], after: set[str], table: dict[str, str], basis: str
) -> list[dict[str, str]]:
    """Python 2 names used before and gone after, with their Python 3 counterpart."""
    return [
        {"before": name, "after": table[name], "basis": basis}
        for name in sorted(before - after)
    ]


# --------------------------------------------------------------------------
# a run
# --------------------------------------------------------------------------

BASELINE_REVISION = "rev_baseline"


async def dependency_changes(run_id: str) -> dict[str, Any]:
    """GET /runs/{run_id}/dependency-changes.

    Legacy side: the repository's snapshot. Migrated side: the run's **last accepted
    revision**, chosen exactly as `GET /runs/{run_id}/diff` chooses it (the whole
    journal, last `snapshot_accepted`), so the two screens describe the same code.
    """
    from app.errors import SNAPSHOT_UNAVAILABLE, AppError
    from app.ingestion import snapshot_path
    from app.orchestration import events, store
    from app.sandbox.workspaces import revision_path

    run = await store.get_run(run_id)  # 404 for an unknown run
    accepted = [e for e in await events.list_all_events(run_id)
                if e.event_type == "snapshot_accepted"]
    revision = accepted[-1].payload.get("revision") if accepted else None
    header = {"run_id": run_id, "repository_id": run.repository_id,
              "base_revision": BASELINE_REVISION, "revision": revision}

    if not revision:
        # Nothing accepted is not "nothing changed": say which it is.
        return {**header, "modules": [], "edges": {"added": [], "removed": []},
                "packages": {"added": [], "removed": [], "kept": []},
                "python": None,
                "warnings": ["no revision was accepted in this run, so there is no "
                             "migrated code to compare"]}

    before = snapshot_path(run.repository_id)
    after = revision_path(run_id, revision)
    missing = [name for name, root in (("legacy snapshot", before),
                                       ("migrated revision", after))
               if not root.is_dir()]
    if missing:
        # Both trees are on the disk of the machine that ran the import and the run.
        raise AppError(
            SNAPSHOT_UNAVAILABLE,
            f"The {' and the '.join(missing)} for this run is not on this machine, "
            "so its dependency changes cannot be read here. Ask on the machine that "
            "ran it.",
            status_code=409,
            details={"run_id": run_id, "missing": missing},
        )

    return {**header, **compare_trees(before, after)}
