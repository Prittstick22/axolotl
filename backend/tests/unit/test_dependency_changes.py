"""Dependency changes, legacy vs migrated (#34) - owner: Dev 3. Offline.

A small Python 2 project and a hand-written Python 3 migration of it, compared the way
`GET /runs/{run_id}/dependency-changes` compares a snapshot with a run's accepted
revision. Nothing is imported or executed; both trees are only parsed.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.errors import AppError
from app.graph import changes
from app.graph.changes import compare_trees

LEGACY = {
    "setup.py": 'setup(name="x", python_requires=">=2.7,<3")\n',
    "pkg/__init__.py": "",
    "pkg/config.py": (
        "import ConfigParser\n"
        "import requests\n"
        "from pkg import util\n"
        "\n"
        "def load(d):\n"
        "    for k, v in d.iteritems():\n"
        "        print k\n"
        '    return raw_input("x")\n'
    ),
    "pkg/util.py": "import cPickle\n",
    "pkg/old.py": "import os\n",
}

MIGRATED = {
    "setup.py": 'setup(name="x", python_requires=">=3.14")\n',
    "pkg/__init__.py": "",
    "pkg/config.py": (
        "import configparser\n"
        "import httpx\n"
        "from pkg import util\n"
        "\n"
        "def load(d):\n"
        "    for k, v in d.items():\n"
        "        print(k)\n"
        '    return input("x")\n'
    ),
    "pkg/util.py": "import pickle\n",
    "pkg/new.py": "import json\n",
}


def write(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def result(tmp_path) -> dict[str, Any]:
    before = write(tmp_path / "legacy", LEGACY)
    after = write(tmp_path / "migrated", MIGRATED)
    return compare_trees(before, after)


def module(result, path: str) -> dict[str, Any]:
    return next(m for m in result["modules"] if m["path"] == path)


# --------------------------------------------------------------------------
# per module
# --------------------------------------------------------------------------

def test_a_python2_module_renamed_to_its_python3_name(result):
    config = module(result, "pkg/config.py")
    assert config["status"] == "changed"
    assert config["stdlib_renamed"] == [
        {"before": "ConfigParser", "after": "configparser", "basis": "py2_rename_table"}
    ]
    # A pairing is reported once, as a rename, not also as a removal and an addition.
    assert "ConfigParser" not in [i["name"] for i in config["imports_removed"]]
    assert "configparser" not in [i["name"] for i in config["imports_added"]]
    assert module(result, "pkg/util.py")["stdlib_renamed"] == [
        {"before": "cPickle", "after": "pickle", "basis": "py2_rename_table"}
    ]


def test_imports_added_and_removed_are_static_facts(result):
    config = module(result, "pkg/config.py")
    assert config["imports_added"] == [{"name": "httpx", "basis": "static_ast"}]
    assert config["imports_removed"] == [{"name": "requests", "basis": "static_ast"}]


def test_python2_builtins_replaced(result):
    assert module(result, "pkg/config.py")["builtins_replaced"] == [
        {"before": "raw_input", "after": "input", "basis": "py2_rename_table"}
    ]


def test_python2_methods_are_labelled_heuristic(result):
    """`.iteritems()` could be a method of the project's own class."""
    assert module(result, "pkg/config.py")["methods_replaced"] == [
        {"before": "iteritems", "after": "items", "basis": "name_match"}
    ]


def test_added_and_removed_modules(result):
    assert module(result, "pkg/old.py")["status"] == "removed"
    assert module(result, "pkg/new.py")["status"] == "added"


def test_an_unchanged_module_is_not_listed(result):
    assert "pkg/__init__.py" not in [m["path"] for m in result["modules"]]


# --------------------------------------------------------------------------
# whole project
# --------------------------------------------------------------------------

def test_graph_edges_added_and_removed(result):
    added = {(e["source"], e["target"]) for e in result["edges"]["added"]}
    removed = {(e["source"], e["target"]) for e in result["edges"]["removed"]}
    assert ("module_pkg__new", "ext_json") in added
    assert ("module_pkg__config", "ext_httpx") in added
    assert ("module_pkg__old", "ext_os") in removed
    assert ("module_pkg__config", "ext_requests") in removed
    # The local edge both sides share is neither.
    assert ("module_pkg__config", "module_pkg__util") not in added | removed


def test_third_party_packages_but_not_the_python2_stdlib(result):
    packages = result["packages"]
    assert packages["added"] == ["httpx"]
    assert packages["removed"] == ["requests"]
    # ConfigParser and cPickle are Python 2's standard library, not packages.
    assert not {"ConfigParser", "cPickle"} & set(packages["removed"])


def test_the_declared_python_version_before_and_after(result):
    python = result["python"]
    assert python["before"]["declared"] == "2.7"
    assert python["before"]["evidence"] == ["setup.py: >=2.7,<3"]
    assert python["after"]["evidence"] == ["setup.py: >=3.14"]


def test_the_project_s_own_package_is_not_a_third_party_package(tmp_path):
    """Found on demo/chess: tests import `local_chess`, the project itself."""
    files = {**MIGRATED, "tests/test_pkg.py": "import pkg.config\nimport pytest\n"}
    result = compare_trees(write(tmp_path / "a", LEGACY), write(tmp_path / "b", files))
    assert "pkg" not in result["packages"]["added"] + result["packages"]["kept"]
    assert "pytest" in result["packages"]["added"]


def test_identical_trees_have_no_changes(tmp_path):
    before = write(tmp_path / "a", MIGRATED)
    after = write(tmp_path / "b", MIGRATED)
    result = compare_trees(before, after)
    assert result["modules"] == []
    assert result["edges"]["added"] == result["edges"]["removed"] == []
    assert result["packages"]["added"] == result["packages"]["removed"] == []


def test_a_file_that_does_not_parse_is_a_warning_not_a_crash(tmp_path):
    before = write(tmp_path / "a", MIGRATED)
    after = write(tmp_path / "b", {**MIGRATED, "pkg/util.py": "def (:\n"})
    result = compare_trees(before, after)
    assert any("migrated pkg/util.py" in w for w in result["warnings"])


# --------------------------------------------------------------------------
# a run
# --------------------------------------------------------------------------

@pytest.fixture
def run(monkeypatch, tmp_path) -> dict[str, Any]:
    """A run whose journal accepted two revisions; trees in tmp_path."""
    state: dict[str, Any] = {"accepted": ["rev_first", "rev_last"]}
    write(tmp_path / "snapshot", LEGACY)
    write(tmp_path / "runs" / "rev_last", MIGRATED)
    write(tmp_path / "runs" / "rev_first", LEGACY)

    async def get_run(run_id):
        return SimpleNamespace(run_id=run_id, repository_id="repo_x")

    async def list_all_events(run_id):
        return [SimpleNamespace(event_type="snapshot_accepted",
                                payload={"revision": r}) for r in state["accepted"]]

    from app import ingestion
    from app.orchestration import events, store
    from app.sandbox import workspaces

    monkeypatch.setattr(store, "get_run", get_run)
    monkeypatch.setattr(events, "list_all_events", list_all_events)
    monkeypatch.setattr(ingestion, "snapshot_path", lambda repo: tmp_path / "snapshot")
    monkeypatch.setattr(workspaces, "revision_path",
                        lambda run_id, rev: tmp_path / "runs" / rev)
    return state


async def test_a_run_compares_the_snapshot_with_its_last_accepted_revision(run):
    result = await changes.dependency_changes("run_1")
    assert (result["base_revision"], result["revision"]) == ("rev_baseline", "rev_last")
    assert result["repository_id"] == "repo_x"
    assert module(result, "pkg/config.py")["stdlib_renamed"]


async def test_nothing_accepted_says_so_rather_than_no_changes(run):
    run["accepted"] = []
    result = await changes.dependency_changes("run_1")
    assert result["revision"] is None and result["modules"] == []
    assert "no revision was accepted" in result["warnings"][0]


async def test_a_tree_missing_from_this_machine_is_a_409(run, tmp_path):
    run["accepted"] = ["rev_elsewhere"]
    with pytest.raises(AppError) as caught:
        await changes.dependency_changes("run_1")
    assert (caught.value.status_code, caught.value.code) == (409, "SNAPSHOT_UNAVAILABLE")
    assert caught.value.details["missing"] == ["migrated revision"]


def test_the_endpoint_is_registered():
    from app.main import create_app

    assert "/api/v1/runs/{run_id}/dependency-changes" in create_app().openapi()["paths"]
