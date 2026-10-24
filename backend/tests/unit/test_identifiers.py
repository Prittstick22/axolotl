"""Module names and ids (#140). Pure functions: no database, no files."""

import pytest

from app.analysis.identifiers import dotted_name_for, module_id_for

CASES = [
    # path, importable name, module id (as stored before #140: none may move)
    ("pkg/__main__.py", "pkg.__main__", "module_pkg____main__"),
    ("pkg/__version__.py", "pkg.__version__", "module_pkg____version__"),
    ("pkg/__init__.py", "pkg", "module_pkg"),
    ("pkg/sub/__init__.py", "pkg.sub", "module_pkg__sub"),
    ("a/b.py", "a.b", "module_a__b"),
    ("top.py", "top", "module_top"),
    ("local_chess/__main__.py", "local_chess.__main__", "module_local_chess____main__"),
]


@pytest.mark.parametrize(("path", "dotted", "_"), CASES)
def test_the_dotted_name_is_the_importable_name(path, dotted, _):
    assert dotted_name_for(path) == dotted
    assert dotted_name_for(path.replace("/", "\\")) == dotted  # a Windows path


@pytest.mark.parametrize(("path", "_", "module_id"), CASES)
def test_module_ids_are_unchanged(path, _, module_id):
    """Stored graphs, plans and runs refer to these ids, so the fix mustn't move one."""
    assert module_id_for(path) == module_id
