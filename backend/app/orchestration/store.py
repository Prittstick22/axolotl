"""Persistence for Dev 2's collections - owner: Dev 2. SHARED_CONTRACTS §9.

Collections: `migration_plans`, `migration_runs`, `counters`, `artefacts`.
(`run_events` is written only through events.append_event.)

There is no in-memory fallback: with Atlas unreachable, `get_db()` raises 503
DATABASE_UNAVAILABLE and every caller fails honestly (§9).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.errors import (
    ARTEFACT_NOT_FOUND,
    PLAN_NOT_FOUND,
    RUN_NOT_FOUND,
    AppError,
)
from app.models.contracts import MigrationPlan, RunSummary
from app.orchestration.states import TERMINAL_RUN_STATES
from app.persistence import db

log = logging.getLogger(__name__)

PLANS = "migration_plans"
RUNS = "migration_runs"
ARTEFACTS = "artefacts"

INTERRUPTED = "interrupted by server restart"


def utcnow() -> datetime:
    """Timezone-aware UTC. Never use naive datetimes (§0.8)."""
    return datetime.now(UTC)


# --------------------------------------------------------------------------
# plans
# --------------------------------------------------------------------------

async def save_plan(plan: MigrationPlan) -> MigrationPlan:
    database = await db.get_db()
    await database[PLANS].replace_one(
        {"plan_id": plan.plan_id}, plan.model_dump(), upsert=True
    )
    return plan


async def get_plan(plan_id: str) -> MigrationPlan:
    database = await db.get_db()
    document = await database[PLANS].find_one({"plan_id": plan_id}, {"_id": 0})
    if document is None:
        raise AppError(
            PLAN_NOT_FOUND, f"No plan {plan_id!r}", status_code=404,
            details={"plan_id": plan_id},
        )
    return MigrationPlan(**document)


async def list_plans_for_repository(repo_id: str) -> list[MigrationPlan]:
    database = await db.get_db()
    cursor = (
        database[PLANS]
        .find({"repository_id": repo_id}, {"_id": 0})
        .sort("created_at", -1)
    )
    return [MigrationPlan(**document) async for document in cursor]


async def supersede_open_plans(repo_id: str, except_plan_id: str) -> int:
    """A repository has at most one live plan; older drafts become `superseded`."""
    database = await db.get_db()
    result = await database[PLANS].update_many(
        {
            "repository_id": repo_id,
            "plan_id": {"$ne": except_plan_id},
            "status": {"$in": ["draft", "approved"]},
        },
        {"$set": {"status": "superseded"}},
    )
    return int(result.modified_count)


# --------------------------------------------------------------------------
# runs
# --------------------------------------------------------------------------

async def save_run(run: RunSummary) -> RunSummary:
    database = await db.get_db()
    run.updated_at = utcnow()
    await database[RUNS].replace_one(
        {"run_id": run.run_id}, run.model_dump(), upsert=True
    )
    return run


async def get_run(run_id: str) -> RunSummary:
    database = await db.get_db()
    document = await database[RUNS].find_one({"run_id": run_id}, {"_id": 0})
    if document is None:
        raise AppError(
            RUN_NOT_FOUND, f"No run {run_id!r}", status_code=404,
            details={"run_id": run_id},
        )
    return RunSummary(**document)


async def list_runs(limit: int = 50) -> list[RunSummary]:
    database = await db.get_db()
    cursor = database[RUNS].find({}, {"_id": 0}).sort("created_at", -1).limit(limit)
    return [RunSummary(**document) async for document in cursor]


async def list_unfinished_runs() -> list[RunSummary]:
    """Runs that are not in a terminal state. On startup, these did not survive.

    Only the query lives here. `worker.mark_interrupted_runs` ends them, because
    ending a run means moving its units and journalling why, and the worker is where
    both of those already happen - once, for cancellation and interruption alike
    (#17). A bulk `update_many` here would be a second way to end a run, free to
    disagree with the first.
    """
    database = await db.get_db()
    cursor = database[RUNS].find(
        {"state": {"$nin": sorted(TERMINAL_RUN_STATES)}}, {"_id": 0}
    ).sort("created_at", 1)
    return [RunSummary(**document) async for document in cursor]


# --------------------------------------------------------------------------
# artefacts  (metadata + path + sha256 only, never blobs - §9)
# --------------------------------------------------------------------------

async def save_artefact(artefact: dict[str, Any]) -> dict[str, Any]:
    database = await db.get_db()
    await database[ARTEFACTS].replace_one(
        {"artefact_id": artefact["artefact_id"]}, artefact, upsert=True
    )
    return artefact


async def list_artefacts(run_id: str) -> list[dict[str, Any]]:
    database = await db.get_db()
    cursor = database[ARTEFACTS].find({"run_id": run_id}, {"_id": 0}).sort("kind", 1)
    return [document async for document in cursor]


async def get_artefact(artefact_id: str) -> dict[str, Any]:
    database = await db.get_db()
    document = await database[ARTEFACTS].find_one(
        {"artefact_id": artefact_id}, {"_id": 0}
    )
    if document is None:
        raise AppError(
            ARTEFACT_NOT_FOUND, f"No artefact {artefact_id!r}", status_code=404,
            details={"artefact_id": artefact_id},
        )
    return document


# --------------------------------------------------------------------------
# indexes
# --------------------------------------------------------------------------

async def ensure_indexes() -> None:
    """Idempotent, safe to run at every startup (§9)."""
    database = await db.get_db()
    await database[PLANS].create_index("plan_id", unique=True, name="plan_id_unique")
    await database[PLANS].create_index("repository_id", name="plan_repository")
    await database[RUNS].create_index("run_id", unique=True, name="run_id_unique")
    await database[RUNS].create_index(
        [("state", 1), ("created_at", -1)], name="run_state_created"
    )
    await database[ARTEFACTS].create_index(
        "artefact_id", unique=True, name="artefact_id_unique"
    )
    await database[ARTEFACTS].create_index("run_id", name="artefact_run")
