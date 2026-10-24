"""AST extraction - owner: Dev 3. Spec §5.2.

Static analysis only. Nothing in this module imports, executes or evaluates the code
being analysed - it reads text and walks a parse tree, which is what makes it safe to
point at an uploaded archive.

A file that will not parse is a warning and a `partial` repository, never a crash:
one bad file must not cost you the other forty.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.analysis.identifiers import dotted_name_for, module_id_for, symbol_id_for
from app.analysis.python2 import python2_tree, uses_absolute_import
from app.models.contracts import SymbolRecord

log = logging.getLogger(__name__)

# Imports written through these are not statically resolvable.
DYNAMIC_IMPORT_CALLS = frozenset({"import_module", "__import__", "reload"})


@dataclass
class ImportRef:
    """One import statement's target, before resolution to a node."""

    target: str                  # dotted name exactly as written
    edge_type: str               # "imports" | "from_imports" | "unknown_dynamic"
    line: int
    level: int = 0               # relative-import dots; 0 for absolute
    detail: str | None = None    # why it is unresolved, for dynamic imports
    # `from pkg import tax` - `tax` may be a submodule rather than an attribute,
    # so resolution tries `pkg.tax` before falling back to `pkg`.
    names: list[str] = field(default_factory=list)


@dataclass
class ModuleAnalysis:
    module_id: str
    dotted: str
    path: str                    # repo-relative, POSIX
    source: str
    is_test: bool
    symbols: list[SymbolRecord] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    parsed: bool = True
    # Parsed through the line-preserving Python 2 reading (#24, analysis/python2.py).
    python2_syntax: bool = False
    # Python 2 resolves `import x` inside a package to a sibling module first, unless
    # the module opts out with `from __future__ import absolute_import`.
    absolute_import: bool = False

    @property
    def function_count(self) -> int:
        return sum(
            1 for s in self.symbols
            if s.kind in {"function", "async_function", "method", "nested_function"}
        )

    @property
    def class_count(self) -> int:
        return sum(1 for s in self.symbols if s.kind == "class")


def looks_like_test(relative_path: str) -> bool:
    path = PurePosixPath(str(relative_path).replace("\\", "/"))
    if "tests" in path.parts[:-1] or "test" in path.parts[:-1]:
        return True
    return path.name.startswith("test_") or path.name.endswith("_test.py")


def analyse_module(
    *, source_path: Path, relative_path: str, repository_id: str, snapshot_hash: str
) -> ModuleAnalysis:
    """Parse one file into symbols and import references."""
    relative_path = str(relative_path).replace("\\", "/")
    analysis = ModuleAnalysis(
        module_id=module_id_for(relative_path),
        dotted=dotted_name_for(relative_path),
        path=relative_path,
        source="",
        is_test=looks_like_test(relative_path),
    )

    try:
        analysis.source = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        analysis.parsed = False
        analysis.warnings.append(f"{relative_path}: could not read ({exc})")
        return analysis

    try:
        tree = ast.parse(analysis.source, filename=relative_path)
    except SyntaxError as exc:
        # Python 2-only syntax is not an error in a Python 2 repository (#24). The
        # reading keeps every line in place and never changes the file.
        python2 = python2_tree(analysis.source, relative_path)
        if python2 is None:
            # §5.2: a syntax error is a file-level warning and a `partial` repository.
            analysis.parsed = False
            analysis.warnings.append(
                f"{relative_path}: syntax error at line {exc.lineno}: {exc.msg}"
            )
            return analysis
        tree = python2
        analysis.python2_syntax = True
        analysis.warnings.append(
            f"{relative_path}: Python 2-only syntax (Python 3 stops at line "
            f"{exc.lineno}); analysed with the Python 2 grammar. The file itself is "
            "unchanged."
        )

    analysis.absolute_import = uses_absolute_import(tree)

    total_lines = analysis.source.count("\n") + 1
    analysis.symbols.append(
        _symbol(
            repository_id=repository_id, snapshot_hash=snapshot_hash,
            path=relative_path, kind="module", qualified_name=analysis.dotted,
            start_line=1, end_line=total_lines,
            docstring=ast.get_docstring(tree), parent_symbol_id=None,
        )
    )

    _Walker(analysis, repository_id, snapshot_hash).visit_body(
        tree.body, prefix=analysis.dotted, parent_symbol_id=analysis.symbols[0].symbol_id,
        inside_class=False, inside_function=False,
    )
    return analysis


def _symbol(
    *, repository_id: str, snapshot_hash: str, path: str, kind: str,
    qualified_name: str, start_line: int, end_line: int,
    docstring: str | None = None, signature: str | None = None,
    decorators: list[str] | None = None, bases: list[str] | None = None,
    parent_symbol_id: str | None = None,
) -> SymbolRecord:
    return SymbolRecord(
        repository_id=repository_id,
        snapshot_hash=snapshot_hash,
        symbol_id=symbol_id_for(snapshot_hash, path, qualified_name, start_line),
        kind=kind,
        qualified_name=qualified_name,
        module_id=module_id_for(path),
        path=path,
        start_line=start_line,
        end_line=end_line,
        signature=signature,
        decorators=decorators or [],
        bases=bases or [],
        # Untrusted data: a docstring can contain anything, including an attempt to
        # instruct a model. It is stored and displayed, never obeyed.
        docstring=docstring,
        parent_symbol_id=parent_symbol_id,
    )


class _Walker:
    """Walks a module body, recording symbols and imports with proper nesting."""

    def __init__(
        self, analysis: ModuleAnalysis, repository_id: str, snapshot_hash: str
    ) -> None:
        self.analysis = analysis
        self.repository_id = repository_id
        self.snapshot_hash = snapshot_hash

    def visit_body(
        self, body: list[ast.stmt], *, prefix: str, parent_symbol_id: str | None,
        inside_class: bool, inside_function: bool,
    ) -> None:
        for node in body:
            if isinstance(node, ast.Import):
                self._record_import(node)
            elif isinstance(node, ast.ImportFrom):
                self._record_import_from(node)
            elif isinstance(node, ast.ClassDef):
                self._record_class(node, prefix, parent_symbol_id)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                self._record_function(
                    node, prefix, parent_symbol_id, inside_class, inside_function
                )
            else:
                # Dynamic imports can appear anywhere, including inside an `if`.
                self._scan_for_dynamic_imports(node)
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.stmt):
                        self.visit_body(
                            [child], prefix=prefix, parent_symbol_id=parent_symbol_id,
                            inside_class=inside_class, inside_function=inside_function,
                        )

    # -- imports ---------------------------------------------------------

    def _record_import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.analysis.imports.append(
                ImportRef(target=alias.name, edge_type="imports", line=node.lineno)
            )

    def _record_import_from(self, node: ast.ImportFrom) -> None:
        base = node.module or ""
        if node.level and not base:
            # `from . import tax` - each imported name is a candidate submodule.
            for alias in node.names:
                self.analysis.imports.append(
                    ImportRef(
                        target=alias.name, edge_type="from_imports",
                        line=node.lineno, level=node.level,
                    )
                )
            return
        self.analysis.imports.append(
            ImportRef(
                target=base, edge_type="from_imports",
                line=node.lineno, level=node.level or 0,
                names=[alias.name for alias in node.names if alias.name != "*"],
            )
        )

    def _scan_for_dynamic_imports(self, node: ast.AST) -> None:
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            name = None
            if isinstance(child.func, ast.Attribute):
                name = child.func.attr
            elif isinstance(child.func, ast.Name):
                name = child.func.id
            if name not in DYNAMIC_IMPORT_CALLS:
                continue

            literal = None
            if child.args and isinstance(child.args[0], ast.Constant):
                if isinstance(child.args[0].value, str):
                    literal = child.args[0].value

            self.analysis.imports.append(
                ImportRef(
                    target=literal or "<dynamic>",
                    edge_type="unknown_dynamic",
                    line=getattr(child, "lineno", 0),
                    detail=f"{name}() cannot be resolved statically",
                )
            )

    # -- definitions -----------------------------------------------------

    def _record_class(
        self, node: ast.ClassDef, prefix: str, parent_symbol_id: str | None
    ) -> None:
        qualified = f"{prefix}.{node.name}" if prefix else node.name
        symbol = _symbol(
            repository_id=self.repository_id, snapshot_hash=self.snapshot_hash,
            path=self.analysis.path, kind="class", qualified_name=qualified,
            start_line=node.lineno, end_line=_end_line(node),
            docstring=ast.get_docstring(node),
            decorators=[_unparse(d) for d in node.decorator_list],
            bases=[_unparse(b) for b in node.bases],
            parent_symbol_id=parent_symbol_id,
        )
        self.analysis.symbols.append(symbol)
        self.visit_body(
            node.body, prefix=qualified, parent_symbol_id=symbol.symbol_id,
            inside_class=True, inside_function=False,
        )

    def _record_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, prefix: str,
        parent_symbol_id: str | None, inside_class: bool, inside_function: bool,
    ) -> None:
        if inside_function:
            kind = "nested_function"
        elif inside_class:
            kind = "method"
        elif isinstance(node, ast.AsyncFunctionDef):
            kind = "async_function"
        else:
            kind = "function"

        qualified = f"{prefix}.{node.name}" if prefix else node.name
        symbol = _symbol(
            repository_id=self.repository_id, snapshot_hash=self.snapshot_hash,
            path=self.analysis.path, kind=kind, qualified_name=qualified,
            start_line=node.lineno, end_line=_end_line(node),
            docstring=ast.get_docstring(node),
            signature=_unparse(node.args),
            decorators=[_unparse(d) for d in node.decorator_list],
            parent_symbol_id=parent_symbol_id,
        )
        self.analysis.symbols.append(symbol)
        self.visit_body(
            node.body, prefix=qualified, parent_symbol_id=symbol.symbol_id,
            inside_class=False, inside_function=True,
        )


def _end_line(node: ast.AST) -> int:
    return int(getattr(node, "end_lineno", None) or getattr(node, "lineno", 1))


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 - a label, never worth failing an import for
        return "<unparseable>"
