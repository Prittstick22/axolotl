"""Security report (#42) - owner: Dev 3. Offline: OSV is an httpx mock transport.

What must hold:
  - only package names and versions are sent to OSV, never code;
  - a vulnerable pinned package is reported with its advisory link and fixed version;
  - an unpinned package is "version not pinned, can't be checked", never clean;
  - no packages is "no third-party packages to check", not an empty "all clear";
  - OSV unreachable is "the check didn't run", never a pass;
  - a Python 2.7 legacy tree carries the runtime note.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.analysis import security
from app.errors import AppError

ADVISORY = {
    "id": "GHSA-x84v-xcm2-53pg",
    "aliases": ["CVE-2018-18074"],
    "summary": "Insufficiently Protected Credentials in Requests",
    "affected": [{
        "package": {"name": "requests", "ecosystem": "PyPI"},
        "ranges": [{"type": "ECOSYSTEM",
                    "events": [{"introduced": "0"}, {"fixed": "2.20.0"}]}],
    }],
}


def osv(vulnerable: dict[tuple[str, str], list[dict]] | None = None, *,
        fail: str | None = None, fail_from: int = 1
        ) -> tuple[httpx.AsyncClient, list[dict]]:
    """A client whose OSV answers from `vulnerable`; records every request body.

    With `fail`, requests from number `fail_from` on (1-based) fail that way.
    """
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if len(sent) < fail_from:
            fail_now = None
        else:
            fail_now = fail
        if fail_now == "connect":
            raise httpx.ConnectError("no route to host", request=request)
        if fail_now == "500":
            return httpx.Response(500, json={"error": "boom"})
        key = (body["package"]["name"], body["version"])
        return httpx.Response(200, json={"vulns": (vulnerable or {}).get(key, [])})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), sent


def tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# the four cases the issue names
# --------------------------------------------------------------------------

async def test_a_vulnerable_pinned_package(tmp_path):
    root = tree(tmp_path, {"requirements.txt": "requests==2.19.0\n"})
    client, sent = osv({("requests", "2.19.0"): [ADVISORY]})

    section = await security.check_packages(root, client=client)

    assert section["status"] == "checked"
    (checked,) = section["checked"]
    assert (checked["name"], checked["version"]) == ("requests", "2.19.0")
    (vuln,) = checked["vulnerabilities"]
    assert vuln["id"] == "GHSA-x84v-xcm2-53pg"
    assert vuln["aliases"] == ["CVE-2018-18074"]
    assert vuln["fixed_versions"] == ["2.20.0"]
    assert vuln["url"] == "https://osv.dev/vulnerability/GHSA-x84v-xcm2-53pg"
    assert "1 with known vulnerabilities" in section["message"]


PYSEC = {
    "id": "PYSEC-2018-28",
    "aliases": ["CVE-2018-18074", "GHSA-x84v-xcm2-53pg"],
    "affected": [{
        "package": {"name": "requests", "ecosystem": "PyPI"},
        "ranges": [{"type": "ECOSYSTEM", "events": [{"fixed": "2.20.0"}]},
                   {"type": "GIT", "events": [{"fixed": "c45d7c49ea75"}]}],
    }],
}


async def test_one_issue_under_two_ids_is_reported_once(tmp_path):
    """Seen live: OSV lists CVE-2018-18074 as both GHSA-x84v-… and PYSEC-2018-28."""
    root = tree(tmp_path, {"requirements.txt": "requests==2.19.0\n"})
    client, _ = osv({("requests", "2.19.0"): [PYSEC, ADVISORY]})

    section = await security.check_packages(root, client=client)

    (vuln,) = section["checked"][0]["vulnerabilities"]
    assert vuln["ids"] == ["GHSA-x84v-xcm2-53pg", "PYSEC-2018-28"]
    assert vuln["aliases"] == ["CVE-2018-18074"]
    # The GIT range's commit is not a version anyone can install.
    assert vuln["fixed_versions"] == ["2.20.0"]


async def test_only_names_and_versions_leave_the_machine(tmp_path):
    root = tree(tmp_path, {"requirements.txt": "requests==2.19.0\n",
                           "app.py": "SECRET = 'do not send'\n"})
    client, sent = osv()
    await security.check_packages(root, client=client)
    assert sent == [{"package": {"name": "requests", "ecosystem": "PyPI"},
                     "version": "2.19.0"}]


async def test_an_unpinned_package_is_never_reported_clean(tmp_path):
    root = tree(tmp_path, {"requirements.txt": "flask>=2.0\nrequests\n"})
    client, sent = osv()

    section = await security.check_packages(root, client=client)

    assert sent == [], "nothing to ask OSV about"
    assert section["status"] == "nothing_pinned"
    assert section["checked"] == []
    assert [(u["name"], u["specifier"], u["note"]) for u in section["unpinned"]] == [
        ("flask", ">=2.0", "version not pinned, can't be checked"),
        ("requests", None, "version not pinned, can't be checked"),
    ]


async def test_no_packages_says_so(tmp_path):
    root = tree(tmp_path, {"setup.py": "setup(name='x', python_requires='>=2.7,<3')\n",
                           "pkg/m.py": "import os\n"})
    client, sent = osv()
    section = await security.check_packages(root, client=client)
    assert section["status"] == "no_packages"
    assert section["message"] == "no third-party packages to check"
    assert sent == []


@pytest.mark.parametrize("failure", ["connect", "500"])
async def test_osv_unreachable_is_not_a_pass(tmp_path, failure):
    root = tree(tmp_path, {"requirements.txt": "requests==2.19.0\nflask==3.0.0\n"})
    # The first package (flask) is checked, then OSV goes away.
    client, sent = osv(fail=failure, fail_from=2)

    section = await security.check_packages(root, client=client)

    assert section["status"] == "not_run"
    assert section["message"].startswith("the vulnerability check didn't run")
    assert len(sent) == 2, "one package was checked before OSV went away"
    assert section["checked"] == [], "no half-checked list that reads as checked"


# --------------------------------------------------------------------------
# reading manifests
# --------------------------------------------------------------------------

def test_setup_py_install_requires_read_statically(tmp_path):
    root = tree(tmp_path, {"setup.py": (
        "raise SystemExit('never executed')\n"
        "setup(install_requires=['Jinja2==2.10', 'six>=1.0'])\n")})
    packages, sources, warnings = security.declared_packages(root)
    assert sources == ["setup.py"] and warnings == []
    assert [(p["name"], p["version"]) for p in packages] == [("jinja2", "2.10"),
                                                             ("six", None)]


def test_a_python2_setup_py_is_read_too(tmp_path):
    root = tree(tmp_path, {"setup.py": (
        "print 'installing'\nsetup(install_requires=['requests==2.19.0'])\n")})
    packages, _, _ = security.declared_packages(root)
    assert [(p["name"], p["version"]) for p in packages] == [("requests", "2.19.0")]


def test_a_computed_install_requires_is_a_warning_not_a_guess(tmp_path):
    root = tree(tmp_path, {"setup.py": (
        "reqs = open('requirements.txt').read().split()\n"
        "setup(install_requires=reqs)\n")})
    packages, _, warnings = security.declared_packages(root)
    assert packages == []
    assert "install_requires is not a literal list" in warnings[0]


def test_pyproject_and_poetry(tmp_path):
    root = tree(tmp_path, {"pyproject.toml": (
        '[project]\ndependencies = ["httpx==0.27.0", "rich"]\n'
        '[tool.poetry.dependencies]\npython = "^3.12"\nDjango = "1.11.0"\n')})
    packages, _, _ = security.declared_packages(root)
    assert [(p["name"], p["version"]) for p in packages] == [
        ("django", "1.11.0"), ("httpx", "0.27.0"), ("rich", None)]


def test_requirements_options_and_urls_are_skipped_with_a_note(tmp_path):
    root = tree(tmp_path, {"requirements.txt": (
        "# comment\n-r base.txt\nrequests==2.19.0  # pinned\n"
        "git+https://example.com/x.git\nurllib3==1.24.1 ; python_version < '3'\n")})
    packages, _, warnings = security.declared_packages(root)
    assert [(p["name"], p["version"]) for p in packages] == [("requests", "2.19.0"),
                                                             ("urllib3", "1.24.1")]
    assert len(warnings) == 2


def test_a_wildcard_is_not_a_pin(tmp_path):
    root = tree(tmp_path, {"requirements.txt": "requests==2.*\n"})
    packages, _, _ = security.declared_packages(root)
    assert packages[0]["version"] is None


# --------------------------------------------------------------------------
# runtime, and legacy vs migrated
# --------------------------------------------------------------------------

def test_the_python27_note(tmp_path):
    legacy = tree(tmp_path / "a", {"setup.py": "setup(python_requires='>=2.7,<3')\n"})
    modern = tree(tmp_path / "b", {"setup.py": "setup(python_requires='>=3.14')\n"})
    assert security.runtime_section(legacy)["note"] == security.PYTHON2_NOTE
    assert security.runtime_section(modern)["note"] is None


def a_report(status: str, checked: list[dict]) -> dict[str, Any]:
    return {"packages": {"status": status, "checked": checked}, "runtime": {}}


def checked(name, version, *ids):
    return {"name": name, "version": version,
            "vulnerabilities": [{"id": i, "url": f"https://osv.dev/vulnerability/{i}",
                                 "fixed_versions": []} for i in ids]}


def test_what_the_migration_fixed_introduced_and_kept():
    legacy = a_report("checked", [checked("requests", "2.19.0", "A", "B")])
    migrated = a_report("checked", [checked("requests", "2.31.0", "B"),
                                    checked("flask", "0.12", "C")])
    changes = security.compare_reports(legacy, migrated)
    assert [c["id"] for c in changes["fixed"]] == ["A"]
    assert [c["id"] for c in changes["introduced"]] == ["C"]
    assert [c["id"] for c in changes["still_present"]] == ["B"]


def test_no_comparison_when_a_side_did_not_run():
    changes = security.compare_reports(a_report("not_run", []),
                                       a_report("checked", [checked("x", "1", "A")]))
    assert changes["fixed"] == changes["introduced"] == []
    assert "didn't run on both sides" in changes["note"]


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------

@pytest.fixture
def run(monkeypatch, tmp_path):
    tree(tmp_path / "snapshot", {"requirements.txt": "requests==2.19.0\n",
                                 "setup.py": "setup(python_requires='>=2.7,<3')\n"})
    tree(tmp_path / "runs" / "rev_last", {"requirements.txt": "requests==2.31.0\n",
                                          "setup.py": "setup(python_requires='>=3.14')\n"})
    state: dict[str, Any] = {"accepted": ["rev_last"]}

    async def get_run(run_id):
        return SimpleNamespace(run_id=run_id, repository_id="repo_x")

    async def list_all_events(run_id):
        return [SimpleNamespace(event_type="snapshot_accepted",
                                payload={"revision": r}) for r in state["accepted"]]

    async def query_osv(name, version, client):
        return [security._advisory(ADVISORY, name)] if version == "2.19.0" else []

    from app import ingestion
    from app.orchestration import events, store
    from app.sandbox import workspaces

    monkeypatch.setattr(store, "get_run", get_run)
    monkeypatch.setattr(events, "list_all_events", list_all_events)
    monkeypatch.setattr(ingestion, "snapshot_path", lambda repo: tmp_path / "snapshot")
    monkeypatch.setattr(workspaces, "revision_path",
                        lambda run_id, rev: tmp_path / "runs" / rev)
    monkeypatch.setattr(security, "query_osv", query_osv)
    return state


async def test_a_run_shows_what_the_migration_fixed(run):
    result = await security.run_security("run_1")
    assert result["revision"] == "rev_last"
    assert [c["id"] for c in result["changes"]["fixed"]] == ["GHSA-x84v-xcm2-53pg"]
    assert result["changes"]["introduced"] == []
    assert result["legacy"]["runtime"]["note"] == security.PYTHON2_NOTE
    assert result["migrated"]["runtime"]["note"] is None
    assert result["disclaimer"] == security.DISCLAIMER


async def test_a_run_that_accepted_nothing_reports_the_legacy_side_only(run):
    run["accepted"] = []
    result = await security.run_security("run_1")
    assert result["migrated"] is None and result["changes"] is None
    assert "no revision was accepted" in result["warnings"][0]
    assert result["legacy"]["packages"]["status"] == "checked"


async def test_a_tree_missing_from_this_machine_is_a_409(run):
    run["accepted"] = ["rev_elsewhere"]
    with pytest.raises(AppError) as caught:
        await security.run_security("run_1")
    assert (caught.value.status_code, caught.value.code) == (409, "SNAPSHOT_UNAVAILABLE")


def test_both_endpoints_are_registered():
    from app.main import create_app

    paths = create_app().openapi()["paths"]
    assert "/api/v1/repositories/{repo_id}/security" in paths
    assert "/api/v1/runs/{run_id}/security" in paths
