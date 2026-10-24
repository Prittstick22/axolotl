"""#93: a restart only ends this host's runs. The team shares one cluster."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.contracts import RunSummary
from app.orchestration import store


class _Cursor:
    def __init__(self, documents):
        self._documents = documents

    def sort(self, *_args):
        return self

    def __aiter__(self):
        async def documents():
            for document in self._documents:
                yield dict(document)
        return documents()


class _Runs:
    """Stores what `save_run` writes. `find` returns every document: the host rule
    under test is applied by `list_unfinished_runs` itself, not by the query."""

    def __init__(self):
        self.documents: dict[str, dict] = {}

    async def replace_one(self, query, document, upsert=False):
        self.documents[query["run_id"]] = document

    def find(self, *_args):
        return _Cursor(list(self.documents.values()))


@pytest.fixture
def runs(monkeypatch) -> _Runs:
    collection = _Runs()

    class Database:
        def __getitem__(self, _name):
            return collection

    async def get_db():
        return Database()

    monkeypatch.setattr(store.db, "get_db", get_db)
    return collection


def a_run(run_id: str) -> RunSummary:
    now = datetime.now(UTC)
    return RunSummary(
        run_id=run_id, plan_id="plan_x", repository_id="repo_x", state="migrating",
        current_unit=None, units=[], units_total=0, units_verified=0, units_blocked=0,
        tests_passed=0, tests_failed=0, tests_inconclusive=0, last_event_sequence=0,
        model="m", runner_mode="trusted_docker", max_patch_attempts=3,
        created_at=now, updated_at=now,
    )


async def test_only_this_hosts_runs_count_as_unfinished_here(runs):
    await store.save_run(a_run("run_mine"))
    assert runs.documents["run_mine"]["host"] == store.HOST

    teammate = a_run("run_teammate").model_dump()
    runs.documents["run_teammate"] = {**teammate, "host": "someone-elses-laptop"}
    # Saved before #93, so it has no host: still ours, as it always was.
    runs.documents["run_old"] = a_run("run_old").model_dump()

    listed = {r.run_id for r in await store.list_unfinished_runs()}

    assert listed == {"run_mine", "run_old"}
