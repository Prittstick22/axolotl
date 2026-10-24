"""Reading Python 2-only syntax - owner: Dev 3. Issue #24.

The backend parses with Python 3's `ast`, which rejects `print "x"`, `except E, e`,
backticks, `<>`, `0777`, `10L`, `exec "code"`, `raise E, "msg"` and `ur""`. One such
line used to cost the whole file: no symbols, no imports, no chunks, so a Python 2
repository came out `partial` with holes in its graph.

When `ast.parse` fails, this rewrites **those constructs only**, in memory, with
lib2to3's own fixers for them (from `fissix`, below), and the analyser parses the
result. Three rules:

  - **The baseline is never rewritten.** The text produced here is used to build a
    parse tree and is then thrown away. The snapshot, the stored module source and every
    chunk are the original bytes.
  - **Lines stay where they were.** Each fixer here rewrites a construct in place, so
    line N of the result is line N of the original, and symbol spans, chunk slices and
    edge line numbers all point at the original file. Fixers that insert or remove lines
    (`fix_tuple_params`, for instance) are deliberately not used, and a result whose
    line count differs is refused rather than trusted.
  - **Syntax only, never meaning.** `xrange`, `dict.iteritems` or `unicode` parse fine
    in Python 3 and are left alone: changing them is the migration's job, not the
    analyser's.

The fixers come from `fissix`, the maintained standalone copy of the standard
library's `lib2to3`, not from `lib2to3` itself. `lib2to3` is deprecated, gone from
Python 3.13, and was missing from the Python 3.12 that GitHub CI installs with `uv`
(PR #59's first run), so code importing it silently did nothing there while passing
on a machine that had it. `fissix` is an ordinary dependency and behaves the same
everywhere.
"""

from __future__ import annotations

import ast
import logging

log = logging.getLogger(__name__)

# Each rewrites a construct within its own line(s); none adds or removes a line.
SYNTAX_FIXERS = tuple(
    f"fissix.fixes.fix_{name}"
    for name in (
        "print",        # print "x"           -> print("x")
        "except",       # except E, e:        -> except E as e:
        "exec",         # exec "code" in ns   -> exec("code", ns)
        "repr",         # `x`                 -> repr(x)
        "ne",           # a <> b              -> a != b
        "numliterals",  # 0777, 10L           -> 0o777, 10
        "long",         # long(x)             -> int(x), needed once 10L parses
        "raise",        # raise E, "msg"      -> raise E("msg")
        "unicode",      # ur"x", u"x"         -> r"x", "x"
    )
)


def python2_tree(source: str, filename: str) -> ast.Module | None:
    """A parse tree for Python 2 source whose lines match the original, or None.

    None when the text is not Python 2 either, or when the rewrite would have moved
    a line: a tree that points at the wrong lines is worse than no tree.
    """
    text = source if source.endswith("\n") else source + "\n"
    try:
        from fissix.refactor import RefactoringTool

        rewritten = str(RefactoringTool(list(SYNTAX_FIXERS))
                        .refactor_string(text, filename))
    except Exception as exc:  # noqa: BLE001 - the parser raises several unrelated types
        log.info("%s is not Python 2 either: %s", filename, exc)
        return None

    if rewritten.count("\n") != text.count("\n"):
        log.warning("%s: the Python 2 reading moved lines; not used", filename)
        return None

    try:
        return ast.parse(rewritten, filename=filename)
    except SyntaxError as exc:
        log.info("%s: Python 2 reading still does not parse: %s", filename, exc)
        return None


def uses_absolute_import(tree: ast.Module) -> bool:
    """`from __future__ import absolute_import`, which turns off Python 2's implicit
    relative imports for this module."""
    return any(
        isinstance(node, ast.ImportFrom) and node.module == "__future__"
        and any(alias.name == "absolute_import" for alias in node.names)
        for node in tree.body
    )
