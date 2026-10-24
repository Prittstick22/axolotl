"""Live Atlas integration tests - owner: Dev 2.

These talk to the **real** cluster, so they are behind the `live` marker and skip
themselves when `MONGODB_URI` is unset.

    uv run pytest -m live                 # just these
    uv run pytest -m "not live"           # everything else (the default in CI)

**Isolation.** Each run creates its own database named `axolotl_test_<random>` and
drops only that, after asserting the name carries the test prefix. Your real
`axolotl` database is never written to or dropped. The global client is swapped
through monkeypatch, so it is restored even if a test fails.

What these are for: proving the things unit tests cannot - that the atomic `$inc`
really is atomic under concurrency, that the unique index really rejects duplicates,
and that timezone-aware datetimes survive the round trip.
"""

import asyncio
import secrets

import pytest
from pymongo.errors import DuplicateKeyError

from app.config import get_settings
from app.models.contracts import MigrationPlan, MigrationUnit, RunSummary, UnitRunState
from app.orchestration import events as events_module
from app.orchestration import store as store_module
from app.persistence import db

pytestmark = pytest.mark.live

TEST_DB_PREFIX = "axolotl_test_"


@pytest.fixture
async def live_db(monkeypatch):
    """A throwaway database on the real cluster, dropped afterwards."""
    settings = get_settings()
    if not settings.mongodb_uri:
        pytest.skip("MONGODB_URI is not set - fill .env to run live tests")

    from pymongo import AsyncMongoClient

    name = TEST_DB_PREFIX + secrets.token_hex(4)
    client = AsyncMongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=10000,
        appname="axolotl-tests",
        tz_aware=True,
    )

    # Swap the module globals so store/events use this database. monkeypatch puts
    # them back afterwards, even on failure.
    monkeypatch.setattr(db, "_client", client)
    monkeypatch.setattr(db, "_database_name", name)

    try:
        yield name
    finally:
        # Guard the drop: only ever a database this fixture created.
        assert name.startswith(TEST_DB_PREFIX), f"refusing to drop {name!r}"
        try:
            await client.drop_database(name)
        finally:
            await client.close()


def make_plan(plan_id: str = "plan_live00001") -> MigrationPlan:
    return MigrationPlan(
        plan_id=plan_id,
        repository_id="repo_live00001",
        snapshot_hash="sha256:live",
        target_id="modern_python3",
        units=[
            MigrationUnit(
                unit_id="unit_pricing", module_ids=["module_pricing"],
                file_paths=["pricing.py"], depends_on_unit_ids=[],
                dependant_unit_ids=["unit_orders"],
            )
        ],
        order=["unit_pricing"],
        rationale="deterministic",
        risks=[],
        coverage_gaps=[],
        model="gemini-4-argon",
        created_at=store_module.utcnow(),
    )


def make_run(run_id: str = "run_live00001", state="migrating") -> RunSummary:
    now = store_module.utcnow()
    return RunSummary(
        run_id=run_id, plan_id="plan_live00001", repository_id="repo_live00001",
        state=state, current_unit=None,
        units=[UnitRunState(unit_id="unit_pricing", module_ids=["module_pricing"],
                            status="queued")],
        units_total=1, units_verified=0, units_blocked=0,
        tests_passed=0, tests_failed=0, tests_inconclusive=0,
        last_event_sequence=0, model="gemini-4-argon",
        runner_mode="trusted_docker", created_at=now, updated_at=now,
    )


# --------------------------------------------------------------------------
# cluster capability - the outstanding Phase 0 question
# --------------------------------------------------------------------------

async def test_cluster_is_reachable_and_reports_its_version(live_db):
    ok, error = await db.ping()
    assert ok, f"ping failed: {error}"

    version = await db.server_version()
    assert version, "cluster did not report a version"

    major, minor = (int(p) for p in version.split(".")[:2])
    print(f"\n  Atlas cluster version: {version}")
    print(f"  native $rankFusion (needs 8.0+):  {'YES' if (major, minor) >= (8, 0) else 'NO'}")
    print(f"  native $scoreFusion (needs 8.2+): {'YES' if (major, minor) >= (8, 2) else 'NO'}")

    if (major, minor) < (8, 0):
        pytest.skip(
            f"cluster is {version}; hybrid search must fall back to manual reciprocal "
            "rank fusion and report native_hybrid: false (tell Dev 3)"
        )


async def test_indexes_can_be_created_idempotently(live_db):
    await store_module.ensure_indexes()
    await events_module.ensure_indexes()
    await store_module.ensure_indexes()  # twice must be fine

    database = await db.get_db()
    names = await database[events_module.EVENTS_COLLECTION].index_information()
    assert "run_sequence_unique" in names
    assert names["run_sequence_unique"].get("unique") is True


# --------------------------------------------------------------------------
# round trips
# --------------------------------------------------------------------------

async def test_a_plan_survives_the_round_trip(live_db):
    original = make_plan()
    await store_module.save_plan(original)

    loaded = await store_module.get_plan(original.plan_id)
    assert loaded.plan_id == original.plan_id
    assert loaded.units[0].unit_id == "unit_pricing"
    assert loaded.order == ["unit_pricing"]
    assert loaded.status == "draft"


async def test_datetimes_come_back_timezone_aware(live_db):
    """SHARED_CONTRACTS §0.8. UK clocks go back mid-hackathon; naive datetimes bite."""
    await store_module.save_plan(make_plan())
    loaded = await store_module.get_plan("plan_live00001")
    assert loaded.created_at.tzinfo is not None


async def test_a_run_survives_the_round_trip(live_db):
    await store_module.save_run(make_run())
    loaded = await store_module.get_run("run_live00001")
    assert loaded.state == "migrating"
    assert loaded.units[0].status == "queued"


async def test_an_artefact_survives_the_round_trip(live_db):
    await store_module.save_artefact({
        "artefact_id": "art_live00001", "run_id": "run_live00001",
        "kind": "patch", "path": "/tmp/x.patch", "sha256": "a" * 64,
    })
    loaded = await store_module.get_artefact("art_live00001")
    assert loaded["kind"] == "patch"
    assert (await store_module.list_artefacts("run_live00001"))[0]["sha256"] == "a" * 64


# --------------------------------------------------------------------------
# the event journal - what unit tests cannot prove
# --------------------------------------------------------------------------

async def test_sequences_start_at_one_and_increase(live_db):
    first = await events_module.append_event("run_seq1", "run_created", payload={})
    second = await events_module.append_event("run_seq1", "run_state_changed", payload={})
    assert first.sequence == 1
    assert second.sequence == 2


async def test_sequences_are_unique_under_concurrency(live_db):
    """The real reason the counter uses an atomic $inc.

    Fifty concurrent writers must produce fifty distinct sequence numbers. A
    read-then-write counter would collide here.
    """
    await events_module.ensure_indexes()

    results = await asyncio.gather(*[
        events_module.append_event("run_race", "agent_turn", payload={"i": i})
        for i in range(50)
    ])

    sequences = sorted(e.sequence for e in results)
    assert len(set(sequences)) == 50, "duplicate sequence numbers issued"
    assert sequences == list(range(1, 51))


async def test_the_unique_index_rejects_a_duplicate_sequence(live_db):
    """Belt and braces: even a bug in the counter cannot corrupt the journal."""
    await events_module.ensure_indexes()
    await events_module.append_event("run_dup", "run_created", payload={})

    database = await db.get_db()
    with pytest.raises(DuplicateKeyError):
        await database[events_module.EVENTS_COLLECTION].insert_one({
            "run_id": "run_dup", "sequence": 1,
            "timestamp": store_module.utcnow(), "event_type": "forged", "payload": {},
        })


async def test_events_page_forward_without_gaps_or_repeats(live_db):
    for i in range(5):
        await events_module.append_event("run_page", "agent_turn", payload={"i": i})

    first_page = await events_module.list_events("run_page", after_sequence=0, limit=2)
    assert [e.sequence for e in first_page] == [1, 2]

    second_page = await events_module.list_events("run_page", after_sequence=2, limit=2)
    assert [e.sequence for e in second_page] == [3, 4]

    tail = await events_module.list_events("run_page", after_sequence=4, limit=200)
    assert [e.sequence for e in tail] == [5]


async def test_secrets_are_redacted_before_they_reach_the_database(live_db):
    """The important one: check the stored document, not the returned object."""
    uri = get_settings().mongodb_uri
    await events_module.append_event(
        "run_secret", "error", payload={"message": f"failed connecting to {uri}"}
    )

    database = await db.get_db()
    stored = await database[events_module.EVENTS_COLLECTION].find_one(
        {"run_id": "run_secret"}
    )
    assert uri not in str(stored)
    assert events_module.REDACTED in stored["payload"]["message"]


# --------------------------------------------------------------------------
# restart handling
# --------------------------------------------------------------------------

async def test_interrupted_runs_are_marked_failed_on_startup(live_db):
    """Now through the worker, which also releases the units and journals why (#17).

    The marking used to be a bulk update in `store`, which left the units at
    `migrating` and `queued` inside a failed run.
    """
    from app.orchestration import events as events_module
    from app.orchestration import worker as worker_module

    await store_module.save_run(make_run("run_live_alive", state="migrating"))
    await store_module.save_run(make_run("run_live_done", state="completed"))

    marked = await worker_module.mark_interrupted_runs()

    assert "run_live_alive" in marked
    assert "run_live_done" not in marked

    interrupted = await store_module.get_run("run_live_alive")
    assert interrupted.state == "failed"
    assert interrupted.error == store_module.INTERRUPTED
    # No unit is left mid-flight inside a terminal run.
    assert [u.status for u in interrupted.units] == ["skipped"]
    assert interrupted.current_unit is None

    journal = await events_module.list_all_events("run_live_alive")
    kinds = [e.event_type for e in journal]
    assert "unit_blocked" in kinds, "the event log just stopped"
    assert any(e.event_type == "error"
               and e.payload.get("code") == "RUN_INTERRUPTED" for e in journal)
    # The closing event precedes the terminal transition (#16).
    assert kinds.index("error") < kinds.index("run_state_changed")

    finished = await store_module.get_run("run_live_done")
    assert finished.state == "completed"
    assert finished.error is None
