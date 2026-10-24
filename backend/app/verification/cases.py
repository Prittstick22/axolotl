"""Behaviour cases - owner: Dev 4. SHARED_CONTRACTS §4, §9.

Cases carry **inputs only**. The expected values come from running the immutable
baseline, never from a checked-in expectation, so there is nothing to quietly edit
when a migration fails.

Held-out suites live under `demo/challenge_cases/`, **outside the fixture trees**,
so they are never snapshot, never indexed and never searchable. Each suite
is selected by the trusted fixture identity recorded at import, never a filename. It
reaches the sandbox (mounted read-only) but never the model: every model-facing read
goes through `include_held_out=False` or redaction.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from app.models.contracts import BehaviourCase
from app.persistence import db

log = logging.getLogger(__name__)

CASES_COLLECTION = "behaviour_cases"

VISIBLE_CASES_RELATIVE = "tests/cases.json"
HELD_OUT_RELATIVE = "demo/challenge_cases/held_out.json"
HELD_OUT_BY_FIXTURE = {
    "legacy_orders": HELD_OUT_RELATIVE,
    "chess": "demo/challenge_cases/chess/held_out.json",
}


def case_hash(function: str, args: list[Any], kwargs: dict[str, Any]) -> str:
    """Stable hash of a case's inputs. Deterministic across processes."""
    payload = json.dumps(
        {"function": function, "args": args, "kwargs": kwargs},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def unit_id_for_function(function: str) -> str:
    """`pricing.finalise_amount` -> `unit_pricing`.

    Fallback only: the fixture's case files carry `unit_id` explicitly. A cyclic
    unit covers several modules, and this cannot know that - which is why the
    explicit value wins.
    """
    module = function.split(".", 1)[0] if "." in function else function
    return f"unit_{module}"


def _read_case_file(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "cases" in data:
        return list(data["cases"])
    if isinstance(data, list):
        return data
    raise ValueError(f"{path}: expected a JSON array or an object with 'cases'")


def _to_case(
    raw: dict[str, Any], repository_id: str, visibility: str, provenance: str
) -> BehaviourCase:
    function = raw.get("function", "")
    args = raw.get("args", [])
    kwargs = raw.get("kwargs", {})
    return BehaviourCase(
        case_id=raw["case_id"],
        repository_id=repository_id,
        unit_id=raw.get("unit_id") or unit_id_for_function(function),
        function=function,
        args=args,
        kwargs=kwargs,
        visibility=visibility,  # type: ignore[arg-type]
        exposed_to_model=False,
        description=raw.get("description"),
        provenance=provenance,
        case_hash=case_hash(function, args, kwargs),
    )


def collect_cases(
    repository_id: str, snapshot_root: Path, project_root: Path,
    *, fixture_id: str | None = None,
) -> list[BehaviourCase]:
    """Visible inputs come from the snapshot; hidden inputs require a trusted fixture.

    `fixture_id` is supplied by ingestion after allowlist/content matching, not by
    an upload. Unknown repositories have no built-in held-out suite (#29).
    """
    cases: list[BehaviourCase] = []

    for raw in _read_case_file(snapshot_root / VISIBLE_CASES_RELATIVE):
        cases.append(_to_case(raw, repository_id, "visible", VISIBLE_CASES_RELATIVE))

    held_out_relative = HELD_OUT_BY_FIXTURE.get(fixture_id)
    if held_out_relative:
        for raw in _read_case_file(project_root / held_out_relative):
            cases.append(_to_case(raw, repository_id, "held_out", held_out_relative))

    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate behaviour case IDs across visible and held-out suites")

    # The whole design rests on held-out cases not being in the snapshot.
    if (snapshot_root / "challenge_cases").exists():
        raise ValueError(
            "held-out cases were found inside the snapshot; they would be visible "
            "to the model and would stop being an independent check"
        )
    return cases


async def persist_cases(cases: list[BehaviourCase]) -> int:
    """Replace this repository's cases. Called once at import."""
    if not cases:
        return 0
    database = await db.get_db()
    await database[CASES_COLLECTION].delete_many(
        {"repository_id": cases[0].repository_id}
    )
    await database[CASES_COLLECTION].insert_many([c.model_dump() for c in cases])
    return len(cases)


async def load_cases(
    repo_id: str, unit_id: str | None = None, *, include_held_out: bool = False
) -> list[BehaviourCase]:
    """SHARED_CONTRACTS §8.

    `include_held_out` defaults to **False** on purpose: the safe answer is the
    default, so forgetting the argument cannot leak the held-out suite.
    """
    database = await db.get_db()
    query: dict[str, Any] = {"repository_id": repo_id}
    if unit_id:
        query["unit_id"] = unit_id
    if not include_held_out:
        query["visibility"] = "visible"

    cursor = database[CASES_COLLECTION].find(query, {"_id": 0}).sort("case_id", 1)
    return [BehaviourCase(**document) async for document in cursor]


async def count_visible_cases(repo_id: str) -> dict[str, int]:
    """unit_id -> visible case count, for MigrationUnit.visible_case_count."""
    counts: dict[str, int] = {}
    for case in await load_cases(repo_id, include_held_out=False):
        counts[case.unit_id] = counts.get(case.unit_id, 0) + 1
    return counts


async def mark_exposed(repo_id: str, case_id: str) -> None:
    """Flip `exposed_to_model`. Recorded so the report can declare it."""
    database = await db.get_db()
    await database[CASES_COLLECTION].update_one(
        {"repository_id": repo_id, "case_id": case_id},
        {"$set": {"exposed_to_model": True}},
    )
    log.info("held-out case %s exposed for %s", case_id, repo_id)


def write_cases_file(
    case_ids: list[str], snapshot_root: Path, project_root: Path, destination: Path,
    *, fixture_id: str | None = None,
) -> Path:
    """Build the `/cases.json` the harness will be given, for `case_ids`.

    Deliberately read back from the **source files** rather than rebuilt from
    `BehaviourCase`. Four of the fixture's cases use a `steps` sequence, and `steps`
    is not a field on `BehaviourCase` in SHARED_CONTRACTS §4 - a known gap that needs
    an additive change under §11. Rebuilding from the model would silently drop those
    cases' bodies and they would execute as single calls against nothing, which is
    worse than not running them.

    So: the contract stays untouched, and the stateful cases still run.
    """
    wanted = set(case_ids)
    payload: list[dict[str, Any]] = []

    sources = [snapshot_root / VISIBLE_CASES_RELATIVE]
    held_out_relative = HELD_OUT_BY_FIXTURE.get(fixture_id)
    if held_out_relative:
        sources.append(project_root / held_out_relative)
    found: set[str] = set()
    for source in sources:
        for raw in _read_case_file(source):
            if raw.get("case_id") not in wanted:
                continue
            if raw["case_id"] in found:
                raise ValueError(f"duplicate behaviour case ID: {raw['case_id']}")
            found.add(raw["case_id"])
            entry = {
                "case_id": raw["case_id"],
                "function": raw.get("function", ""),
                "args": raw.get("args", []),
                "kwargs": raw.get("kwargs", {}),
            }
            if raw.get("steps"):
                entry["steps"] = raw["steps"]
            payload.append(entry)

    missing = wanted - found
    if missing:
        raise ValueError(f"behaviour cases missing from this fixture: {sorted(missing)}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2), encoding="utf-8", newline="\n"
    )
    return destination
