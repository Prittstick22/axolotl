"""What one module declares and depends on, read from its parse tree - owner: Dev 1. #39.

Facts only: every field comes from `analyse_module` and `build_edges`, the same reading
an import does. Nothing here calls a model, and nothing is imported or executed.

Docstrings are copied as written. They are untrusted data (a docstring can say
anything, including text addressed to a model), so the renderer fences them and no
prompt ever sees them as instructions.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.analysis.astwalk import ModuleAnalysis
from app.models.contracts import GraphEdge, SymbolRecord

STATIC = "static_ast"
FUNCTION_KINDS = {"function", "async_function"}


def _name(symbol: SymbolRecord) -> str:
    return symbol.qualified_name.rsplit(".", 1)[-1]


def _public(name: str, *, method: bool = False) -> bool:
    return not name.startswith("_") or (method and name == "__init__")


def _first_line(text: str | None) -> str | None:
    lines = (text or "").strip().splitlines()
    return lines[0].strip() if lines else None


def _callable(symbol: SymbolRecord) -> dict[str, Any]:
    return {
        "name": _name(symbol),
        "signature": symbol.signature or "",
        "async": symbol.kind == "async_function",
        "decorators": list(symbol.decorators),
        "summary": _first_line(symbol.docstring),
        "line": symbol.start_line,
    }


def module_facts(analysis: ModuleAnalysis, edges: list[GraphEdge]) -> dict[str, Any]:
    """The public surface of one module, and its imports in both directions."""
    module = next((s for s in analysis.symbols if s.kind == "module"), None)
    top = module.symbol_id if module else None

    methods: dict[str, list[SymbolRecord]] = defaultdict(list)
    for symbol in analysis.symbols:
        if symbol.kind == "method" and symbol.parent_symbol_id:
            methods[symbol.parent_symbol_id].append(symbol)

    classes, functions = [], []
    for symbol in sorted(analysis.symbols, key=lambda s: s.start_line):
        if symbol.parent_symbol_id != top or not _public(_name(symbol)):
            continue
        if symbol.kind == "class":
            classes.append({
                "name": _name(symbol),
                "bases": list(symbol.bases),
                "summary": _first_line(symbol.docstring),
                "line": symbol.start_line,
                "methods": [
                    _callable(m) for m in sorted(methods[symbol.symbol_id],
                                                 key=lambda m: m.start_line)
                    if _public(_name(m), method=True)
                ],
            })
        elif symbol.kind in FUNCTION_KINDS:
            functions.append(_callable(symbol))

    outgoing = [e for e in edges if e.source == analysis.module_id]
    return {
        "module_id": analysis.module_id,
        "path": analysis.path,
        "dotted": analysis.dotted,
        "is_test": analysis.is_test,
        "docstring": module.docstring if module else None,
        "classes": classes,
        "functions": functions,
        "depends_on": sorted({e.target for e in outgoing if e.target.startswith("module_")}),
        "packages": sorted({e.target.removeprefix("ext_") for e in outgoing
                            if e.target.startswith("ext_")}),
        "used_by": sorted({e.source for e in edges if e.target == analysis.module_id}),
        "parsed": analysis.parsed,
        "python2_syntax": analysis.python2_syntax,
        "warnings": list(analysis.warnings),
        "basis": STATIC,
    }
