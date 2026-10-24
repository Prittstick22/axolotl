"""Collections and regular indexes - owner: Dev 3. SHARED_CONTRACTS §9.

`ensure_indexes()` runs at startup and makes the `axolotl` database match §9: all 12
collections exist, plus Dev 3's `search_index_status` (#28, pending a §9 row), and
every regular index in §9 exists, with an explicit name.

Rules it follows:
  - **Add only.** It never drops, renames or rebuilds an index. If an index already
    exists under a different shape, it is left exactly as it is and reported as a
    conflict, so a human decides. Startup never fails because of it.
  - **Idempotent.** It reads what exists first and creates only what is missing, so a
    second run creates nothing and changes nothing.
  - **Stable names.** Names never change once released: MongoDB refuses to create the
    same keys under a second name. Every name here is the one already live on Atlas,
    so this runs side by side with Dev 2's and Dev 4's own `ensure_indexes()`
    (`orchestration/store.py`, `orchestration/events.py`, `verification/store.py`).
  - **Honest failure.** If Atlas is unreachable it raises 503 DATABASE_UNAVAILABLE.

Atlas Search and Vector Search indexes are different: they are built asynchronously
by Atlas and managed with `create_search_index`. They live in
`ensure_search_indexes()` (Tasks 3/4).
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import CollectionInvalid, ConnectionFailure, OperationFailure

from app.errors import database_unavailable
from app.persistence import db

log = logging.getLogger(__name__)

# --- collections (§9) ---
REPOSITORIES = "repositories"
SYMBOLS = "symbols"
EDGES = "edges"
CODE_CHUNKS = "code_chunks"
MIGRATION_PLANS = "migration_plans"
MIGRATION_RUNS = "migration_runs"
RUN_EVENTS = "run_events"
COUNTERS = "counters"
BEHAVIOUR_CASES = "behaviour_cases"
VERIFICATION_RESULTS = "verification_results"
COUNTEREXAMPLES = "counterexamples"
ARTEFACTS = "artefacts"
# Not in §9 yet: per-repository search indexing progress (#28, app/search/progress.py).
# Additive and Dev 3-only; the §9 row is proposed to Dev 2.
SEARCH_INDEX_STATUS = "search_index_status"

COLLECTIONS: tuple[str, ...] = (
    REPOSITORIES,
    SYMBOLS,
    EDGES,
    CODE_CHUNKS,
    MIGRATION_PLANS,
    MIGRATION_RUNS,
    RUN_EVENTS,
    COUNTERS,
    BEHAVIOUR_CASES,
    VERIFICATION_RESULTS,
    COUNTEREXAMPLES,
    ARTEFACTS,
    SEARCH_INDEX_STATUS,
)

# MongoDB error codes for "an index with these keys or this name already exists
# with different options". Reported as conflicts, never raised.
_INDEX_CONFLICT_CODES = {85, 86}


@dataclass(frozen=True)
class IndexSpec:
    collection: str
    name: str
    keys: tuple[tuple[str, int], ...]
    unique: bool = False


# Every regular index in §9, under the names already live on Atlas. Several indexes
# were first created by Dev 2's code (orchestration/store.py, orchestration/events.py,
# verification/store.py, and an earlier version of this file); their names are kept
# exactly. `counters` has none: §9 lists `_id`, which MongoDB creates automatically
# on every collection and which cannot be redefined.
INDEX_SPECS: tuple[IndexSpec, ...] = (
    # Dev 3
    IndexSpec(REPOSITORIES, "repository_id_unique", (("repository_id", 1),), unique=True),
    IndexSpec(REPOSITORIES, "repository_created", (("created_at", 1),)),
    IndexSpec(SYMBOLS, "symbol_unique", (("repository_id", 1), ("symbol_id", 1)), unique=True),
    IndexSpec(SYMBOLS, "symbol_qualified_name", (("repository_id", 1), ("qualified_name", 1))),
    IndexSpec(SYMBOLS, "symbol_module", (("repository_id", 1), ("module_id", 1))),
    # DEVIATION from §9: stored edges are GraphEdge documents, so the fields are
    # `source` / `target`, not `source_symbol_id` / `target_symbol_id`. Real data
    # already uses these names. See docs/mongodb-atlas.md.
    IndexSpec(EDGES, "edge_source", (("repository_id", 1), ("source", 1))),
    IndexSpec(EDGES, "edge_target", (("repository_id", 1), ("target", 1))),
    IndexSpec(CODE_CHUNKS, "chunk_unique", (("repository_id", 1), ("chunk_id", 1)), unique=True),
    IndexSpec(SEARCH_INDEX_STATUS, "index_status_repository_unique",
              (("repository_id", 1),), unique=True),
    # Dev 2
    IndexSpec(MIGRATION_PLANS, "plan_id_unique", (("plan_id", 1),), unique=True),
    IndexSpec(MIGRATION_PLANS, "plan_repository", (("repository_id", 1),)),
    IndexSpec(MIGRATION_RUNS, "run_id_unique", (("run_id", 1),), unique=True),
    IndexSpec(MIGRATION_RUNS, "run_state_created", (("state", 1), ("created_at", -1))),
    IndexSpec(RUN_EVENTS, "run_sequence_unique",
              (("run_id", 1), ("sequence", 1)), unique=True),
    IndexSpec(ARTEFACTS, "artefact_id_unique", (("artefact_id", 1),), unique=True),
    IndexSpec(ARTEFACTS, "artefact_run", (("run_id", 1),)),
    # Dev 4 - "case_id unique per repo" read as unique (repository_id, case_id)
    IndexSpec(BEHAVIOUR_CASES, "case_repo_unit", (("repository_id", 1), ("unit_id", 1))),
    IndexSpec(BEHAVIOUR_CASES, "case_repo_case_unique",
              (("repository_id", 1), ("case_id", 1)), unique=True),
    IndexSpec(VERIFICATION_RESULTS, "verification_id_unique",
              (("verification_id", 1),), unique=True),
    IndexSpec(VERIFICATION_RESULTS, "verification_run_unit", (("run_id", 1), ("unit_id", 1))),
    IndexSpec(COUNTEREXAMPLES, "counterexample_id_unique",
              (("counterexample_id", 1),), unique=True),
    IndexSpec(COUNTEREXAMPLES, "counterexample_run_unit",
              (("run_id", 1), ("unit_id", 1), ("status", 1))),
)


@dataclass
class IndexReport:
    """What one `ensure_indexes()` call did. A second run should show nothing created."""

    collections_created: list[str] = field(default_factory=list)
    indexes_created: list[str] = field(default_factory=list)  # "collection.name"
    indexes_present: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)  # left untouched, needs a human


async def ensure_indexes() -> IndexReport:
    """Create missing §9 collections and indexes. Never drops or modifies anything."""
    report = IndexReport()
    try:
        database = await db.get_db()

        existing = set(await database.list_collection_names())
        for name in COLLECTIONS:
            if name in existing:
                continue
            try:
                await database.create_collection(name)
                report.collections_created.append(name)
            except CollectionInvalid:
                pass  # created concurrently by another process - that's fine

        for collection in COLLECTIONS:
            specs = [s for s in INDEX_SPECS if s.collection == collection]
            if specs:
                await _ensure_collection_indexes(database, specs, report)
    except ConnectionFailure as exc:
        raise database_unavailable(
            f"MongoDB is unreachable ({type(exc).__name__})"
        ) from exc

    log.info(
        "ensure_indexes: %d collection(s) created, %d index(es) created, "
        "%d already present, %d conflict(s)",
        len(report.collections_created), len(report.indexes_created),
        len(report.indexes_present), len(report.conflicts),
    )
    for conflict in report.conflicts:
        log.warning("index conflict left untouched: %s", conflict)
    return report


async def _ensure_collection_indexes(
    database: AsyncDatabase[dict[str, Any]], specs: list[IndexSpec], report: IndexReport
) -> None:
    existing = await database[specs[0].collection].index_information()

    for spec in specs:
        label = f"{spec.collection}.{spec.name}"
        wanted_keys = [(f, int(d)) for f, d in spec.keys]

        if spec.name in existing:
            info = existing[spec.name]
            if _keys(info) == wanted_keys and bool(info.get("unique")) == spec.unique:
                report.indexes_present.append(label)
            else:
                report.conflicts.append(
                    f"{label} exists with keys={_keys(info)} unique={bool(info.get('unique'))}"
                    f", expected keys={wanted_keys} unique={spec.unique}"
                )
            continue

        other = next((n for n, i in existing.items() if _keys(i) == wanted_keys), None)
        if other is not None:
            report.conflicts.append(f"{label}: same keys already indexed as {other!r}")
            continue

        try:
            await database[spec.collection].create_index(
                list(spec.keys), name=spec.name, unique=spec.unique
            )
            report.indexes_created.append(label)
        except OperationFailure as exc:
            if exc.code not in _INDEX_CONFLICT_CODES:
                raise
            report.conflicts.append(f"{label}: {exc.code_name or exc.code}")


def _keys(info: dict[str, Any]) -> list[tuple[str, int]]:
    return [(f, int(d)) for f, d in info["key"]]


async def ensure_search_indexes() -> None:
    """PLACEHOLDER - Tasks 3/4. Not called anywhere yet.

    Will create `code_chunks_text` (Atlas Search) and `code_chunks_vector` (Vector
    Search, `repository_id` filter field) with `create_search_index`. Until then
    `search_status()` truthfully reports both as "unavailable".
    """
    raise NotImplementedError("Atlas Search / Vector indexes arrive in Dev 3 Tasks 3/4")
