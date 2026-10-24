"""Index specification - owner: Dev 3. SHARED_CONTRACTS §9. No network needed.

The table below is written out by hand from §9, so a change to `INDEX_SPECS` that drifts
from the contract fails here first, before it reaches Atlas.
"""

import pytest
from pymongo import AsyncMongoClient

from app.errors import DATABASE_UNAVAILABLE, AppError
from app.persistence import db
from app.persistence.indexes import COLLECTIONS, INDEX_SPECS, ensure_indexes

# (collection, name, keys, unique) - §9 under the names already live on Atlas, plus
# the extras other owners' code already created there.
EXPECTED = {
    ("repositories", "repository_id_unique", (("repository_id", 1),), True),
    ("repositories", "repository_created", (("created_at", 1),), False),
    ("symbols", "symbol_unique", (("repository_id", 1), ("symbol_id", 1)), True),
    ("symbols", "symbol_qualified_name", (("repository_id", 1), ("qualified_name", 1)), False),
    ("symbols", "symbol_module", (("repository_id", 1), ("module_id", 1)), False),
    # Deviation from §9: stored edges use `source` / `target` (docs/mongodb-atlas.md).
    ("edges", "edge_source", (("repository_id", 1), ("source", 1)), False),
    ("edges", "edge_target", (("repository_id", 1), ("target", 1)), False),
    ("code_chunks", "chunk_unique", (("repository_id", 1), ("chunk_id", 1)), True),
    # Not in §9 yet: Dev 3's indexing progress (#28), proposed to Dev 2 as a §9 row.
    ("search_index_status", "index_status_repository_unique", (("repository_id", 1),), True),
    ("migration_plans", "plan_id_unique", (("plan_id", 1),), True),
    ("migration_plans", "plan_repository", (("repository_id", 1),), False),
    ("migration_runs", "run_id_unique", (("run_id", 1),), True),
    ("migration_runs", "run_state_created", (("state", 1), ("created_at", -1)), False),
    ("run_events", "run_sequence_unique", (("run_id", 1), ("sequence", 1)), True),
    ("behaviour_cases", "case_repo_unit", (("repository_id", 1), ("unit_id", 1)), False),
    ("behaviour_cases", "case_repo_case_unique",
     (("repository_id", 1), ("case_id", 1)), True),
    ("verification_results", "verification_id_unique", (("verification_id", 1),), True),
    ("verification_results", "verification_run_unit", (("run_id", 1), ("unit_id", 1)), False),
    ("counterexamples", "counterexample_id_unique", (("counterexample_id", 1),), True),
    ("counterexamples", "counterexample_run_unit",
     (("run_id", 1), ("unit_id", 1), ("status", 1)), False),
    ("artefacts", "artefact_id_unique", (("artefact_id", 1),), True),
    ("artefacts", "artefact_run", (("run_id", 1),), False),
}

# Names that other code on main also creates. Renaming any of these would make MongoDB
# reject one side at startup (same keys, different name).
CREATED_ELSEWHERE = {
    ("migration_plans", "plan_id_unique"),
    ("migration_plans", "plan_repository"),
    ("migration_runs", "run_id_unique"),
    ("migration_runs", "run_state_created"),
    ("run_events", "run_sequence_unique"),
    ("artefacts", "artefact_id_unique"),
    ("artefacts", "artefact_run"),
    ("verification_results", "verification_id_unique"),
    ("verification_results", "verification_run_unit"),
    ("counterexamples", "counterexample_id_unique"),
    ("counterexamples", "counterexample_run_unit"),
}


def test_the_twelve_section_9_collections_plus_indexing_status():
    assert set(COLLECTIONS) == {
        "repositories", "symbols", "edges", "code_chunks", "migration_plans",
        "migration_runs", "run_events", "counters", "behaviour_cases",
        "verification_results", "counterexamples", "artefacts",
        "search_index_status",
    }
    assert len(COLLECTIONS) == 13


def test_index_specs_match_section_9_exactly():
    actual = {(s.collection, s.name, s.keys, s.unique) for s in INDEX_SPECS}
    assert actual == EXPECTED
    assert len(INDEX_SPECS) == len(EXPECTED), "duplicate spec"


def test_every_index_targets_a_known_collection():
    assert {s.collection for s in INDEX_SPECS} <= set(COLLECTIONS)


def test_counters_relies_on_the_builtin_id_index():
    """§9 lists `_id` for counters; MongoDB creates it and it cannot be redefined."""
    assert not [s for s in INDEX_SPECS if s.collection == "counters"]


def test_names_are_explicit_and_unique_per_collection():
    seen = set()
    for spec in INDEX_SPECS:
        assert spec.name and spec.name != "_id_"
        assert (spec.collection, spec.name) not in seen
        seen.add((spec.collection, spec.name))


def test_no_two_specs_index_the_same_keys():
    keys = [(s.collection, s.keys) for s in INDEX_SPECS]
    assert len(keys) == len(set(keys))


def test_names_shared_with_other_owners_are_preserved():
    assert CREATED_ELSEWHERE <= {(s.collection, s.name) for s in INDEX_SPECS}


async def test_get_db_without_a_uri_is_a_503(monkeypatch):
    monkeypatch.setattr(db, "_client", None)
    with pytest.raises(AppError) as caught:
        await db.get_db()
    assert caught.value.status_code == 503
    assert caught.value.code == DATABASE_UNAVAILABLE


async def test_unreachable_database_is_a_503_not_a_crash(monkeypatch):
    """A real client pointed at a closed local port - no fake, no network."""
    client = AsyncMongoClient(
        "mongodb://127.0.0.1:1/", serverSelectionTimeoutMS=300, connectTimeoutMS=300
    )
    monkeypatch.setattr(db, "_client", client)
    try:
        with pytest.raises(AppError) as caught:
            await ensure_indexes()
        assert caught.value.status_code == 503
        assert caught.value.code == DATABASE_UNAVAILABLE

        ok, message = await db.ping()
        assert not ok
        assert message == "MongoDB ping failed (ServerSelectionTimeoutError)"
    finally:
        await client.close()
