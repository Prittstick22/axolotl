"""Python 2-only syntax (#24) - owner: Dev 3. Offline.

One Python 2-only line used to cost a whole file: `ast.parse` refused it, and the file
contributed no symbols, no imports and no chunks. These pin the replacement:

  - symbols, imports and chunks come out of Python 2 files, at their original lines;
  - the file on disk and the stored source are never rewritten;
  - inside a package, a Python 2 `import board` is the sibling module, as Python 2
    resolves it, unless the module asked for `absolute_import`;
  - Python 3 files, and files that are not Python at all, behave exactly as before.
"""

from pathlib import Path

import pytest

from app.analysis import python2
from app.analysis.astwalk import analyse_module
from app.graph.builder import build_edges
from app.search.chunking import build_chunks

GAME = '''import board
from helpers import fmt
import json


def show(b):
    print "board:", fmt(b)
    try:
        x = 0777 + 10L
    except ValueError, e:
        print >>sys.stderr, `e`
    if x <> 1:
        raise ValueError, "bad"
    exec "y = 1" in {}


class Game(object):
    def title(self):
        return ur"chess"
'''

BOARD = '''class Board(object):
    def show(self):
        print "8x8"
'''

HELPERS = '''def fmt(x):
    return `x`
'''

ABSOLUTE = '''from __future__ import absolute_import
import board

print "absolute"
'''

PYTHON3 = '''import board


def f():
    print("fine in both")
'''

FILES = {
    "pkg/__init__.py": "",
    "pkg/game.py": GAME,
    "pkg/board.py": BOARD,
    "pkg/helpers.py": HELPERS,
    "pkg/absolute.py": ABSOLUTE,
    "pkg/modern.py": PYTHON3,
    "broken.py": "def (:\n",
}


@pytest.fixture
def analyses(tmp_path: Path):
    for relative, text in FILES.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    before = {r: (tmp_path / r).read_bytes() for r in FILES}
    result = {}
    for relative in FILES:
        a = analyse_module(source_path=tmp_path / relative, relative_path=relative,
                           repository_id="repo_py2", snapshot_hash="sha256:py2")
        result[relative] = a
    after = {r: (tmp_path / r).read_bytes() for r in FILES}
    assert before == after, "analysis must never write to the snapshot"
    return result


def by_name(analysis):
    return {s.qualified_name: s for s in analysis.symbols}


def test_a_python2_file_yields_its_symbols(analyses):
    game = analyses["pkg/game.py"]
    assert game.parsed and game.python2_syntax
    assert {"pkg.game", "pkg.game.show", "pkg.game.Game", "pkg.game.Game.title"} \
        <= set(by_name(game))


def test_symbols_keep_their_original_line_numbers(analyses):
    symbols = by_name(analyses["pkg/game.py"])
    lines = GAME.splitlines()
    show, title = symbols["pkg.game.show"], symbols["pkg.game.Game.title"]
    assert lines[show.start_line - 1] == "def show(b):"
    assert lines[show.end_line - 1] == '    exec "y = 1" in {}'
    assert lines[title.start_line - 1] == "    def title(self):"


def test_the_stored_source_is_the_original_text(analyses):
    assert analyses["pkg/game.py"].source == GAME


def test_chunks_are_cut_from_the_original_text(analyses):
    game = analyses["pkg/game.py"]
    chunks = build_chunks(game.symbols, {game.module_id: game.source})
    show = next(c for c in chunks if c["qualified_name"] == "pkg.game.show")
    assert 'print "board:", fmt(b)' in show["source"]
    assert "except ValueError, e:" in show["source"]


def test_the_warning_says_what_happened(analyses):
    (warning,) = analyses["pkg/game.py"].warnings
    # Python 3's tokenizer rejects `0777` (line 9) before the parser reaches the
    # print statement on line 7, so the line is where Python 3 stopped.
    assert "Python 2-only syntax (Python 3 stops at line 9)" in warning
    assert "unchanged" in warning


def test_python2_imports_resolve_to_sibling_modules(analyses):
    edges, _, _ = build_edges({a.module_id: a for a in analyses.values()})
    from_game = {(e.target, e.resolution) for e in edges
                 if e.source == "module_pkg__game"}
    assert ("module_pkg__board", "confirmed") in from_game
    assert ("module_pkg__helpers", "confirmed") in from_game
    assert ("ext_json", "confirmed") in from_game, "no sibling json: still external"
    assert not any(t == "ext_board" for t, _ in from_game)


def test_absolute_import_turns_the_sibling_rule_off(analyses):
    absolute = analyses["pkg/absolute.py"]
    assert absolute.python2_syntax and absolute.absolute_import
    edges, _, _ = build_edges({a.module_id: a for a in analyses.values()})
    targets = {e.target for e in edges if e.source == "module_pkg__absolute"}
    assert "ext_board" in targets
    assert "module_pkg__board" not in targets


def test_python3_files_are_untouched(analyses):
    """A file Python 3 parses is not Python 2 evidence; its imports mean what they
    mean in Python 3."""
    modern = analyses["pkg/modern.py"]
    assert modern.parsed and not modern.python2_syntax and modern.warnings == []
    edges, _, _ = build_edges({a.module_id: a for a in analyses.values()})
    targets = {e.target for e in edges if e.source == "module_pkg__modern"}
    assert targets == {"ext_board"}


def test_a_file_that_is_not_python_is_still_a_syntax_error(analyses):
    broken = analyses["broken.py"]
    assert not broken.parsed and broken.symbols == []
    assert "syntax error at line 1" in broken.warnings[0]


def test_a_reading_that_would_move_lines_is_refused(monkeypatch):
    """Spans and chunks would point at the wrong lines; no tree is better."""
    from fissix import refactor

    real = refactor.RefactoringTool.refactor_string

    def adds_a_line(self, data, name):
        return str(real(self, data, name)) + "\n# extra\n"

    monkeypatch.setattr(refactor.RefactoringTool, "refactor_string", adds_a_line)
    assert python2.python2_tree('print "x"\n', "x.py") is None


def test_only_syntax_is_rewritten_not_meaning():
    """`xrange` and `iteritems` parse in Python 3; changing them is the migration's."""
    assert python2.SYNTAX_FIXERS
    assert not any(f.endswith(("xrange", "dict", "unicode_literals", "tuple_params",
                               "imports", "basestring"))
                   for f in python2.SYNTAX_FIXERS)


def test_a_python2_setup_py_still_declares_its_python(tmp_path):
    """The declaration lives in exactly the file most likely to be Python 2."""
    from app.ingestion.runtime import inspect_runtime

    (tmp_path / "setup.py").write_text(
        'print "installing"\nsetup(name="old", python_requires=">=2.7,<3")\n',
        encoding="utf-8",
    )
    assert inspect_runtime(tmp_path)["declared_python"] == "2.7"
