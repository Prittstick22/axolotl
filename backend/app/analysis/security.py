"""Security report: known-vulnerable packages and the Python 2 runtime - owner: Dev 3. #42.

Two checks, both on facts read statically from a tree. Nothing is installed, imported
or executed.

  packages  The third-party packages a project *declares* (setup.py `install_requires`,
            pyproject.toml, requirements*.txt), checked against OSV (osv.dev), the
            open database `pip-audit` also uses. **Only package names and versions
            leave the machine, never code.**
  runtime   A legacy tree that declares Python 2.7 runs on an interpreter with no
            security fixes since 2020.

Honest wording is the point of the shape:

  - A package without an exact `==` version cannot be checked, and is reported as
    "version not pinned, can't be checked", never as clean.
  - No declared packages is "no third-party packages to check", not an empty list a
    reader would take for "no vulnerabilities".
  - If OSV cannot be reached, the check **did not run**. It is never a pass.

These find known vulnerabilities in declared packages. They are not a security audit.
"""

from __future__ import annotations

import ast
import logging
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.analysis.python2 import python2_tree
from app.ingestion.runtime import inspect_runtime

log = logging.getLogger(__name__)

OSV_QUERY_URL = "https://api.osv.dev/v1/query"
OSV_ADVISORY_URL = "https://osv.dev/vulnerability/{id}"
OSV_TIMEOUT_SECONDS = 15.0
MAX_MANIFEST_BYTES = 256 * 1024

DISCLAIMER = ("These checks find known vulnerabilities in declared, pinned packages "
              "(OSV database). They are not a security audit.")
UNPINNED_NOTE = "version not pinned, can't be checked"
NO_PACKAGES = "no third-party packages to check"
PYTHON2_NOTE = ("Python 2.7 has had no security fixes since 2020; migrating to "
                "Python 3 removes that risk.")

# PEP 508, enough of it: a name, optional [extras], the specifier, `; markers`.
_REQUIREMENT = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*"
    r"(?P<spec>[^;#]*?)\s*(;.*)?$"
)
_EXACT_PIN = re.compile(r"^===?\s*(?P<version>[A-Za-z0-9][A-Za-z0-9.+!_-]*)$")


# --------------------------------------------------------------------------
# declared packages
# --------------------------------------------------------------------------

def declared_packages(root: Path) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """-> (packages, manifests read, warnings). Static: nothing is executed."""
    packages: list[dict[str, Any]] = []
    sources: list[str] = []
    warnings: list[str] = []
    if not root.is_dir():
        return packages, sources, warnings

    candidates = [root / "setup.py", root / "pyproject.toml",
                  *sorted(root.glob("requirements*.txt")),
                  *sorted((root / "requirements").glob("*.txt"))]
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            warnings.append(f"{relative}: larger than {MAX_MANIFEST_BYTES} bytes, not read")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            warnings.append(f"{relative}: could not be read ({type(exc).__name__})")
            continue

        if path.name == "setup.py":
            found, problems = _from_setup_py(text, relative)
        elif path.name == "pyproject.toml":
            found, problems = _from_pyproject(text, relative)
        else:
            found, problems = _from_requirements(text, relative)
        sources.append(relative)
        packages.extend(found)
        warnings.extend(problems)

    return _dedupe(packages), sources, warnings


def _from_setup_py(text: str, source: str) -> tuple[list[dict], list[str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = python2_tree(text, source)
    if tree is None:
        return [], [f"{source}: could not be parsed, so its packages were not read"]

    found: list[dict] = []
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "install_requires":
                continue
            value = keyword.value
            if isinstance(value, ast.List | ast.Tuple) and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str)
                for e in value.elts
            ):
                for element in value.elts:
                    found.extend(_parse_requirement(element.value, source))
            else:
                # Built at runtime (read from a file, computed): reading it would
                # mean executing setup.py, which this never does.
                problems.append(f"{source}: install_requires is not a literal list, "
                                "so it was not read")
    return found, problems


def _from_pyproject(text: str, source: str) -> tuple[list[dict], list[str]]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return [], [f"{source}: not valid TOML, so its packages were not read"]
    found: list[dict] = []
    for requirement in data.get("project", {}).get("dependencies", []) or []:
        if isinstance(requirement, str):
            found.extend(_parse_requirement(requirement, source))
    poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {}) or {}
    for name, spec in poetry.items():
        if name.lower() == "python":
            continue
        version = spec if isinstance(spec, str) else (spec or {}).get("version", "")
        # Poetry writes an exact pin as a bare version: "2.19.0".
        spec_text = f"=={version}" if re.fullmatch(r"[0-9][0-9A-Za-z.+!_-]*",
                                                   version or "") else version
        found.extend(_parse_requirement(f"{name}{spec_text or ''}", source))
    return found, []


def _from_requirements(text: str, source: str) -> tuple[list[dict], list[str]]:
    found: list[dict] = []
    problems: list[str] = []
    for line in text.splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-", "git+", "http:", "https:", "file:")) or " @ " in line:
            problems.append(f"{source}: '{line[:60]}' is an option, include or URL, "
                            "not a package version, so it was not checked")
            continue
        found.extend(_parse_requirement(line, source))
    return found, problems


def _parse_requirement(text: str, source: str) -> list[dict[str, Any]]:
    match = _REQUIREMENT.match(text)
    if not match:
        return []
    spec = (match.group("spec") or "").strip()
    pinned = _EXACT_PIN.match(spec)
    # `==2.*` is a range, not a pin: the pattern does not accept `*`.
    version = pinned.group("version") if pinned else None
    return [{"name": normalise(match.group("name")), "version": version,
             "specifier": spec or None, "source": source}]


def normalise(name: str) -> str:
    """PEP 503: `Jinja2`, `jinja_2` and `jinja.2` are the same package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _dedupe(packages: list[dict]) -> list[dict]:
    seen: dict[tuple[str, str | None], dict] = {}
    for p in packages:
        seen.setdefault((p["name"], p["version"]), p)
    return sorted(seen.values(), key=lambda p: (p["name"], p["version"] or ""))


# --------------------------------------------------------------------------
# OSV
# --------------------------------------------------------------------------

class OsvUnavailable(Exception):
    """OSV could not answer, so nothing may be reported as checked."""


async def query_osv(name: str, version: str, client: httpx.AsyncClient) -> list[dict]:
    """Known vulnerabilities of one exact PyPI package version. One request.

    Sends only `{"package": {"name", "ecosystem": "PyPI"}, "version"}`.
    """
    body = {"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
    try:
        response = await client.post(OSV_QUERY_URL, json=body)
    except httpx.HTTPError as exc:
        raise OsvUnavailable(f"{type(exc).__name__}") from exc
    if response.status_code != 200:
        raise OsvUnavailable(f"HTTP {response.status_code}")
    try:
        vulns = response.json().get("vulns", []) or []
    except ValueError as exc:
        raise OsvUnavailable("the answer was not JSON") from exc
    return _group_aliases([_advisory(v, name) for v in vulns])


def _group_aliases(advisories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per issue. OSV lists one issue under several ids that name each
    other as aliases (`GHSA-x84v-…` and `PYSEC-2018-28` for CVE-2018-18074), so
    `requests==2.19.0` showed 10 advisories for 5 issues."""
    groups: list[dict[str, Any]] = []
    for advisory in sorted(advisories, key=lambda a: a["id"] or ""):
        names = {advisory["id"], *advisory["aliases"]}
        group = next((g for g in groups if g["_names"] & names), None)
        if group is None:
            groups.append({**advisory, "ids": [advisory["id"]], "_names": names})
            continue
        group["ids"].append(advisory["id"])
        group["_names"] |= names
        group["fixed_versions"] = sorted(set(group["fixed_versions"])
                                         | set(advisory["fixed_versions"]))
        group["summary"] = group["summary"] or advisory["summary"]
    for group in groups:
        names = group.pop("_names")
        group["aliases"] = sorted(names - set(group["ids"]))
    return groups


def _advisory(vuln: dict, package: str) -> dict[str, Any]:
    fixed: set[str] = set()
    for affected in vuln.get("affected", []) or []:
        pkg = affected.get("package", {}) or {}
        if pkg.get("ecosystem") != "PyPI" or normalise(pkg.get("name", "")) != package:
            continue
        for rng in affected.get("ranges", []) or []:
            # GIT ranges name a commit, not a version anyone can install.
            if rng.get("type") not in ("ECOSYSTEM", "SEMVER"):
                continue
            for event in rng.get("events", []) or []:
                if event.get("fixed"):
                    fixed.add(event["fixed"])
    return {
        "id": vuln.get("id"),
        "aliases": vuln.get("aliases", []) or [],
        "summary": vuln.get("summary") or (vuln.get("details") or "")[:200] or None,
        "fixed_versions": sorted(fixed),
        "url": OSV_ADVISORY_URL.format(id=vuln.get("id")),
    }


async def check_packages(
    root: Path, *, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """The `packages` section for one tree."""
    packages, sources, warnings = declared_packages(root)
    section: dict[str, Any] = {
        "database": "OSV (osv.dev)", "sources": sources,
        "checked": [], "unpinned": [], "warnings": warnings,
        "checked_at": datetime.now(UTC).isoformat(),
    }
    if not packages:
        return {**section, "status": "no_packages", "message": NO_PACKAGES}

    pinned = [p for p in packages if p["version"]]
    section["unpinned"] = [
        {"name": p["name"], "specifier": p["specifier"], "source": p["source"],
         "note": UNPINNED_NOTE}
        for p in packages if not p["version"]
    ]
    if not pinned:
        return {**section, "status": "nothing_pinned",
                "message": "every declared package is unpinned, so none could be "
                           "checked"}

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=OSV_TIMEOUT_SECONDS)
    try:
        for p in pinned:
            vulns = await query_osv(p["name"], p["version"], client)
            section["checked"].append({**p, "vulnerabilities": vulns})
    except OsvUnavailable as exc:
        log.warning("OSV unavailable: %s", exc)
        # Partial answers are dropped: a half-checked list reads as a checked one.
        return {**section, "checked": [], "status": "not_run",
                "message": f"the vulnerability check didn't run: OSV could not be "
                           f"reached ({exc})"}
    finally:
        if owns_client:
            await client.aclose()

    vulnerable = sum(1 for c in section["checked"] if c["vulnerabilities"])
    return {**section, "status": "checked",
            "message": f"{len(pinned)} pinned package(s) checked, {vulnerable} with "
                       f"known vulnerabilities"}


def runtime_section(root: Path) -> dict[str, Any]:
    runtime = inspect_runtime(root)
    declared = runtime.get("declared_python")
    return {
        "declared_python": declared,
        "evidence": runtime.get("evidence", []),
        "note": PYTHON2_NOTE if declared == "2.7" else None,
    }


async def tree_report(root: Path, *, client: httpx.AsyncClient | None = None) -> dict:
    return {"packages": await check_packages(root, client=client),
            "runtime": runtime_section(root)}


# --------------------------------------------------------------------------
# legacy vs migrated
# --------------------------------------------------------------------------

def compare_reports(legacy: dict, migrated: dict) -> dict[str, Any]:
    """Advisories the migration fixed, introduced, or left in place.

    Only when both sides were checked: a side that did not run cannot show that
    something was fixed.
    """
    statuses = (legacy["packages"]["status"], migrated["packages"]["status"])
    if "not_run" in statuses:
        return {"fixed": [], "introduced": [], "still_present": [],
                "note": "not compared: the vulnerability check didn't run on both sides"}

    def keyed(report: dict) -> dict[tuple[str, str], dict]:
        return {(c["name"], v["id"]): {"package": c["name"], "version": c["version"],
                                       "id": v["id"], "url": v["url"],
                                       "fixed_versions": v["fixed_versions"]}
                for c in report["packages"]["checked"] for v in c["vulnerabilities"]}

    before, after = keyed(legacy), keyed(migrated)
    return {
        "fixed": [before[k] for k in sorted(before.keys() - after.keys())],
        "introduced": [after[k] for k in sorted(after.keys() - before.keys())],
        "still_present": [after[k] for k in sorted(before.keys() & after.keys())],
        "note": None,
    }


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------

async def repository_security(repo_id: str) -> dict[str, Any]:
    """GET /repositories/{repo_id}/security - the legacy code alone."""
    from app.errors import SNAPSHOT_UNAVAILABLE, AppError
    from app.ingestion import get_repository, snapshot_path

    await get_repository(repo_id)  # 404 for an unknown repository
    root = snapshot_path(repo_id)
    if not root.is_dir():
        raise AppError(
            SNAPSHOT_UNAVAILABLE,
            "This repository's snapshot is not on this machine, so its packages "
            "cannot be read here.",
            status_code=409, details={"repository_id": repo_id},
        )
    return {"repository_id": repo_id, "legacy": await tree_report(root),
            "disclaimer": DISCLAIMER}


async def run_security(run_id: str) -> dict[str, Any]:
    """GET /runs/{run_id}/security - legacy vs the run's last accepted revision.

    The revision is chosen as GET /runs/{run_id}/diff chooses it: the whole journal,
    last `snapshot_accepted`.
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
              "base_revision": "rev_baseline", "revision": revision,
              "disclaimer": DISCLAIMER}

    before = snapshot_path(run.repository_id)
    trees = [("legacy snapshot", before)]
    if revision:
        trees.append(("migrated revision", revision_path(run_id, revision)))
    missing = [name for name, root in trees if not root.is_dir()]
    if missing:
        raise AppError(
            SNAPSHOT_UNAVAILABLE,
            f"The {' and the '.join(missing)} for this run is not on this machine, "
            "so its security report cannot be read here.",
            status_code=409, details={"run_id": run_id, "missing": missing},
        )

    async with httpx.AsyncClient(timeout=OSV_TIMEOUT_SECONDS) as client:
        legacy = await tree_report(before, client=client)
        if not revision:
            return {**header, "legacy": legacy, "migrated": None, "changes": None,
                    "warnings": ["no revision was accepted in this run, so there is "
                                 "no migrated code to compare"]}
        migrated = await tree_report(revision_path(run_id, revision), client=client)

    return {**header, "legacy": legacy, "migrated": migrated,
            "changes": compare_reports(legacy, migrated), "warnings": []}
