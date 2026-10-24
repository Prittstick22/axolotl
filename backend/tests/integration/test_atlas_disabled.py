"""Disabling Atlas breaks the journeys honestly - owner: Dev 3. Spec §11.2, SHARED_CONTRACTS §9.

The MongoDB claim is only true if taking Atlas away *breaks* the graph, search and
repository journeys - with a clear 503 DATABASE_UNAVAILABLE, never a bare 500 and never
an empty 200 that reads as "nothing here".

Three ways Atlas goes away, each with a **real** driver client (no fake database):

  no_uri             MONGODB_URI unset
  nothing_listening  a well-formed URI with nothing on the other end
  unknown_host       an Atlas SRV address that does not resolve

None of them reaches the real cluster, so these run anywhere.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pymongo import AsyncMongoClient

from app import ingestion
from app.persistence import db
from tests.conftest import API

SCENARIOS = {
    "no_uri": None,
    "nothing_listening": "mongodb://127.0.0.1:1/",
    "unknown_host": "mongodb+srv://no-such-cluster.invalid/",
}
HOSTNAMES = ("127.0.0.1", "no-such-cluster")

DEV3_ENDPOINTS = [
    ("get", f"{API}/repositories"),
    ("get", f"{API}/repositories/repo_000000000001"),
    ("get", f"{API}/repositories/repo_000000000001/graph"),
    ("get", f"{API}/repositories/repo_000000000001/modules/module_pricing"),
    ("get", f"{API}/repositories/repo_000000000001/symbols/sym_0000000000000000"),
    ("get", f"{API}/repositories/repo_000000000001/search?q=finalise_amount"),
    ("get", f"{API}/repositories/repo_000000000001/search?q=rounding&mode=vector"),
    ("get", f"{API}/repositories/repo_000000000001/search?q=rounding&mode=hybrid"),
    ("post", f"{API}/repositories/repo_000000000001/index"),
]

# Dev 2's journeys, added after Dev 3 found every one of these returning a bare 500
# with a URI set and the cluster down. The fix is `route_class=DatabaseAwareRoute` on
# the plans/runs router; these keep it from being dropped in a later refactor.
DEV2_ENDPOINTS = [
    ("get", f"{API}/plans/plan_000000000001"),
    ("get", f"{API}/runs/run_000000000001"),
    ("get", f"{API}/runs/run_000000000001/events"),
    ("get", f"{API}/runs/run_000000000001/report"),
    ("get", f"{API}/runs/run_000000000001/artefacts"),
    ("get", f"{API}/runs/run_000000000001/diff"),
    ("get", f"{API}/runs/run_000000000001/units/unit_1"),
    ("post", f"{API}/runs/run_000000000001/cancel"),
]


@pytest.fixture(params=sorted(SCENARIOS))
def atlas_gone(request, client: TestClient, monkeypatch):
    """Swap the app's Mongo client for one that cannot reach anything."""
    uri = SCENARIOS[request.param]
    mongo = None
    if uri:
        mongo = AsyncMongoClient(
            uri, serverSelectionTimeoutMS=300, connectTimeoutMS=300, connect=False
        )
    monkeypatch.setattr(db, "_client", mongo)
    yield request.param
    if mongo is not None:
        client.portal.call(mongo.close)


@pytest.mark.parametrize(("method", "path"), DEV2_ENDPOINTS)
def test_every_dev2_journey_is_a_503(client, atlas_gone, method, path):
    """Orchestration endpoints, which used to be bare 500s.

    A 500 tells the user nothing and reads as a bug in the app; a 503
    DATABASE_UNAVAILABLE says which dependency is missing. The MongoDB claim rests on
    taking Atlas away breaking these journeys *honestly*.
    """
    response = getattr(client, method)(path)

    assert response.status_code == 503, (
        f"{atlas_gone}: {method} {path} -> {response.status_code} {response.text}"
    )
    error = response.json()["error"]
    assert error["code"] == "DATABASE_UNAVAILABLE"
    assert not any(host in error["message"] for host in HOSTNAMES)


@pytest.mark.parametrize(("method", "path"), DEV3_ENDPOINTS)
def test_every_dev3_journey_is_a_503(client, atlas_gone, method, path):
    response = getattr(client, method)(path)

    assert response.status_code == 503, f"{atlas_gone}: {method} {path} -> {response.text}"
    error = response.json()["error"]
    assert error["code"] == "DATABASE_UNAVAILABLE"
    # The type of failure, never the driver's text: it lists cluster hostnames.
    assert not any(host in error["message"] for host in HOSTNAMES)


def test_status_says_unavailable_rather_than_guessing(client, atlas_gone):
    body = client.get(f"{API}/system/status").json()
    assert body["mongodb"] == "unavailable"
    assert body["search_indexes"] == {"keyword": "unavailable", "vector": "unavailable"}


def test_an_import_fails_cleanly_and_leaves_nothing_on_disk(
    client, atlas_gone, monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(ingestion, "snapshot_path", lambda repo_id: tmp_path / repo_id)

    response = client.post(
        f"{API}/repositories/import", json={"demo_fixture_id": "legacy_orders"}
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert list(tmp_path.iterdir()) == [], "a snapshot outlived the failed import"


def test_input_mistakes_are_still_reported_with_atlas_down(client, atlas_gone):
    """Validation needs no database, so a bad request is a 422, not a 503."""
    response = client.post(
        f"{API}/repositories/import", json={"demo_fixture_id": "no_such_fixture"}
    )
    assert response.status_code == 422


def test_the_fixture_list_does_not_need_atlas(client, atlas_gone):
    """It is read from disk, and says only what can be imported - nothing from Atlas."""
    response = client.get(f"{API}/demo-fixtures")
    assert response.status_code == 200
    assert [f["demo_fixture_id"] for f in response.json()] == ["chess", "legacy_orders"]


def test_a_failed_import_removes_its_half_written_snapshot(monkeypatch, tmp_path: Path):
    """Atlas dropping *during* an import: the snapshot must not outlive the failure."""
    import asyncio

    from app.errors import database_unavailable

    async def reachable():
        return None

    async def atlas_drops(*args, **kwargs):
        assert any(tmp_path.iterdir()), "the snapshot should exist mid-import"
        raise database_unavailable("MongoDB is unreachable (AutoReconnect)")

    async def not_imported_yet(repo_id):
        return None

    monkeypatch.setattr(ingestion, "_require_database", reachable)
    monkeypatch.setattr(ingestion, "_existing_repository", not_imported_yet)
    monkeypatch.setattr(ingestion, "snapshot_path", lambda repo_id: tmp_path / repo_id)
    monkeypatch.setattr(ingestion, "_persist", atlas_drops)

    with pytest.raises(Exception, match="unreachable"):
        asyncio.run(ingestion.import_repository(demo_fixture_id="legacy_orders"))
    assert list(tmp_path.iterdir()) == []
