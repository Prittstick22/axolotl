"""API behaviour with no database - owner: Dev 2. SHARED_CONTRACTS §9.

"If MongoDB is unreachable, endpoints return 503 DATABASE_UNAVAILABLE. No in-memory
fallback." These tests run with an empty .env, which is exactly that situation, and
pin two things:

  - nothing degrades into a 500 or, worse, a fabricated empty success
  - validation that needs no database still works, so T-08 holds even when Atlas is down

They need no external service and are safe to run anywhere.
"""

import pytest
from fastapi.testclient import TestClient

from app.errors import AppError
from app.persistence import db
from tests.conftest import API


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    """Force the no-database state, rather than relying on `.env` being empty.

    These tests used to pass only because no `MONGODB_URI` was configured. That
    made them green in CI and **red on any correctly configured machine** - every
    teammate who filled in `.env` would see twelve failures in a file describing
    behaviour that had not changed. A test whose premise depends on ambient config
    is testing the machine, not the code.

    Dropping the client is the real mechanism: `get_db()` then raises 503 and
    `ping()` reports unavailable, exactly as an Atlas outage would.
    """
    monkeypatch.setattr(db, "_client", None)

DB_BACKED = [
    ("get", f"{API}/plans/plan_000000000001", None),
    ("get", f"{API}/runs/run_000000000001", None),
    ("get", f"{API}/runs/run_000000000001/events", None),
    ("get", f"{API}/runs/run_000000000001/report", None),
    ("get", f"{API}/runs/run_000000000001/artefacts", None),
    ("get", f"{API}/runs/run_000000000001/units/unit_pricing", None),
    ("get", f"{API}/runs/run_000000000001/diff", None),
    ("get", f"{API}/runs/run_000000000001/export", None),
    ("post", f"{API}/plans/plan_000000000001/approve", {"approved": True}),
    ("post", f"{API}/plans/plan_000000000001/runs", None),
    ("post", f"{API}/runs/run_000000000001/cancel", None),
]


@pytest.mark.parametrize(("method", "path", "body"), DB_BACKED)
def test_database_backed_endpoints_return_503(client: TestClient, method, path, body):
    response = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)

    assert response.status_code == 503, f"{method} {path} gave {response.status_code}"
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


@pytest.mark.parametrize(("method", "path", "body"), DB_BACKED)
def test_no_endpoint_fakes_a_success_without_a_database(
    client: TestClient, method, path, body
):
    """A silent empty 200 would be the worst outcome: it reads as 'nothing to report'."""
    response = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
    assert response.status_code != 200
    assert response.status_code < 500 or response.status_code == 503


def test_unsupported_target_is_rejected_before_the_database_is_touched(
    client: TestClient,
):
    """T-08 must hold even with Atlas down - validation comes first."""
    response = client.post(
        f"{API}/repositories/repo_000000000001/plans", json={"target_id": "async_python"}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "UNSUPPORTED_TARGET"
    assert body["error"]["details"]["reason"]


def test_unknown_target_is_rejected_before_the_database_is_touched(client: TestClient):
    response = client.post(
        f"{API}/repositories/repo_000000000001/plans", json={"target_id": "cobol_to_rust"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_TARGET"


def test_a_supported_target_fails_on_the_missing_graph_not_silently(client: TestClient):
    """Dev 3's graph is not wired yet, so this is 503 with a reason - never a fake plan."""
    response = client.post(
        f"{API}/repositories/repo_000000000001/plans",
        json={"target_id": "modern_python3"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] in {
        "INTEGRATION_UNAVAILABLE", "DATABASE_UNAVAILABLE",
    }


def test_malformed_plan_request_is_a_422_envelope(client: TestClient):
    response = client.post(f"{API}/repositories/repo_1/plans", json={"wrong": "field"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_endpoints_needing_no_database_still_work(client: TestClient):
    assert client.get(f"{API}/health").status_code == 200
    assert client.get(f"{API}/migration-targets").status_code == 200
    assert client.get(f"{API}/system/status").status_code == 200


def test_system_status_reports_the_database_as_unavailable(client: TestClient):
    """The status endpoint is the one place that must answer while everything is down."""
    body = client.get(f"{API}/system/status").json()
    assert body["mongodb"] == "unavailable"


def test_the_runner_is_unavailable_while_any_target_image_is_missing(
    client: TestClient, monkeypatch
):
    """#95: only the 3.12 image used to be checked, so the chip read "ok" with the
    chess run's 2.7 and 3.14 images missing."""
    from app.api import system

    async def runner_status(runtime):
        if runtime.python == "3.14":
            return {"mode": "disabled", "available": False,
                    "detail": f"image {runtime.image} (Python 3.14) not found"}
        return {"mode": "trusted_docker", "available": True}

    monkeypatch.setattr(system, "runner_status", runner_status)
    body = client.get(f"{API}/system/status").json()

    assert body["runner"] == {"mode": "trusted_docker", "available": False}
    assert "Python 3.14" in body["details"]["runner"]


def test_an_unexpected_error_still_gets_the_contract_envelope(monkeypatch):
    """A reachable-but-failing Atlas (auth error, timeout mid-query) used to return a
    bare FastAPI 500 that clients cannot parse, having been promised one error shape.

    `raise_server_exceptions=False` makes TestClient return the response the server
    would actually send instead of re-raising.
    """
    from app.main import create_app
    from app.orchestration import store as store_module

    async def explode(run_id):
        raise RuntimeError("simulated Atlas failure with secret in the text")

    monkeypatch.setattr(store_module, "get_run", explode)

    with TestClient(create_app(), raise_server_exceptions=False) as raw:
        response = raw.get(f"{API}/runs/run_000000000001")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert set(body["error"]) == {"code", "message", "details"}
    # The exception's text can carry a connection string, so only its type is shown.
    assert "secret" not in body["error"]["message"]
    assert body["error"]["message"] == "Unexpected RuntimeError"


def test_held_out_counterexamples_are_redacted_for_the_human_view():
    """The unit endpoint is a read path like any other: a sealed held-out case must
    not leak its inputs there."""
    from app.api.plans_runs import _redact_held_out

    sealed = _redact_held_out({
        "case_id": "hcase_001", "visibility": "held_out", "exposed_to_model": False,
        "function": "pricing.finalise_amount", "args": [[{"unit_price": "2.45"}]],
        "kwargs": {}, "old": {"value": "2.65"}, "new": {"value": "2.64"},
    })
    assert "2.45" not in str(sealed)
    assert "2.65" not in str(sealed)
    assert sealed["redacted"] is True
    assert sealed["case_id"] == "hcase_001"   # the id itself is fine to show


def test_an_exposed_held_out_case_is_shown_in_full():
    from app.api.plans_runs import _redact_held_out

    exposed = _redact_held_out({
        "case_id": "hcase_001", "visibility": "held_out", "exposed_to_model": True,
        "args": [[{"unit_price": "2.45"}]], "old": {"value": "2.65"},
    })
    assert "2.45" in str(exposed)


def test_visible_counterexamples_are_never_redacted():
    from app.api.plans_runs import _redact_held_out

    visible = _redact_held_out({
        "case_id": "case_001", "visibility": "visible", "exposed_to_model": False,
        "args": [[{"unit_price": "1.25"}]],
    })
    assert "1.25" in str(visible)


# --------------------------------------------------------------------------
# #45: the unit endpoint returns its verifications
# --------------------------------------------------------------------------

def a_comparison(case_id: str, visibility: str, old: str, new: str) -> dict:
    return {
        "case_id": case_id,
        "visibility": visibility,
        "verdict": "mismatch",
        "old": {"case_id": case_id, "status": "ok", "value": old,
                "exception_message": None},
        "new": {"case_id": case_id, "status": "ok", "value": new,
                "exception_message": None},
    }


def a_verification(comparisons: list[dict]) -> dict:
    return {
        "verification_id": "ver_dd3750652173",
        "run_id": "run_bb0da2e79534",
        "unit_id": "unit_pricing",
        "attempt": 1,
        "workspace_revision": "rev_9db26cbee19c",
        "gates": [],
        "comparisons": comparisons,
        "counterexample_ids": [],
        "all_required_gates_passed": True,
        "runner_mode": "trusted_docker",
        "started_at": "2026-10-24T10:00:00Z",
        "finished_at": "2026-10-24T10:00:09Z",
    }


def test_a_sealed_held_out_comparison_does_not_leak_what_the_case_expected():
    """The Diff screen needs the per-case outputs; a sealed case must not give up its
    expected value to get them.

    This is the whole point of the held-out suite: if the values reach a read path,
    the independence is gone whether or not a model was the reader.
    """
    from app.api.plans_runs import _redact_comparisons

    sealed = _redact_comparisons(
        a_verification([a_comparison("hcase_001", "held_out", "2.65", "2.64")]),
        exposed=set(),
    )
    comparison = sealed["comparisons"][0]

    assert "2.65" not in str(comparison)
    assert "2.64" not in str(comparison)
    assert comparison["redacted"] is True
    # The reviewer still learns that a held-out case mismatched, and which one.
    assert comparison["verdict"] == "mismatch"
    assert comparison["case_id"] == "hcase_001"
    assert comparison["old"]["status"] == "ok"


def test_an_exposed_held_out_comparison_is_shown_in_full():
    """Repair already showed this case to the model, so a reviewer may see it too."""
    from app.api.plans_runs import _redact_comparisons

    shown = _redact_comparisons(
        a_verification([a_comparison("hcase_001", "held_out", "2.65", "2.64")]),
        exposed={"hcase_001"},
    )
    assert "2.65" in str(shown["comparisons"][0])
    assert "redacted" not in shown["comparisons"][0]


def test_visible_comparisons_are_never_redacted():
    from app.api.plans_runs import _redact_comparisons

    shown = _redact_comparisons(
        a_verification([a_comparison("case_001", "visible", "1.25", "1.26")]),
        exposed=set(),
    )
    assert "1.25" in str(shown["comparisons"][0])


def test_a_held_out_exception_message_is_redacted_but_its_presence_is_not():
    """An exception message can carry the expected value in its text."""
    from app.api.plans_runs import _redact_comparisons

    comparison = a_comparison("hcase_002", "held_out", "x", "y")
    comparison["new"]["status"] = "raised"
    comparison["new"]["exception_message"] = "expected Decimal('2.65'), got 2.64"

    sealed = _redact_comparisons(a_verification([comparison]), exposed=set())
    new = sealed["comparisons"][0]["new"]

    assert "2.65" not in str(new)
    assert new["status"] == "raised"          # that it raised is not a secret
    assert new["exception_message"] is not None


def test_a_verification_with_no_comparisons_is_passed_through_unchanged():
    from app.api.plans_runs import _redact_comparisons

    verification = a_verification([])
    assert _redact_comparisons(verification, exposed=set()) == verification
    del verification["comparisons"]
    assert _redact_comparisons(verification, exposed=set()) == verification


def _a_run(run_id: str, state: str, unit_status: str):
    from datetime import UTC, datetime

    from app.models.contracts import RunSummary, UnitRunState

    now = datetime.now(UTC)
    return RunSummary(
        run_id=run_id, plan_id="plan_000000000001",
        repository_id="repo_000000000001", state=state, current_unit=None,
        units=[UnitRunState(unit_id="unit_pricing", module_ids=["module_pricing"],
                            status=unit_status)],
        units_total=1, units_verified=1 if unit_status == "passed" else 0,
        units_blocked=0, tests_passed=0, tests_failed=0, tests_inconclusive=0,
        last_event_sequence=0, model="gemini-4-argon", runner_mode="trusted_docker",
        created_at=now, updated_at=now,
    )


async def test_the_unit_endpoint_returns_the_run_s_verifications(monkeypatch):
    """It returned a hardcoded `[]` with the comment "read when it lands". It had
    landed: `list_verifications` was already in app/verification/store.py.

    The Diff screen's gate table, attempt history and per-case outputs all come from
    here, and only the per-case outputs exist nowhere else.
    """
    from app.api import plans_runs

    run = _a_run("run_bb0da2e79534", "completed", "passed")

    async def get_run(run_id):
        return run

    async def list_verifications(run_id, unit_id=None):
        assert (run_id, unit_id) == ("run_bb0da2e79534", "unit_pricing")
        return [a_verification([a_comparison("case_001", "visible", "1.25", "1.26")])]

    async def no_counterexamples(run_id, unit_id, *, for_model):
        return []

    monkeypatch.setattr(plans_runs.store, "get_run", get_run)
    monkeypatch.setattr(plans_runs.ports, "list_verifications", list_verifications)
    monkeypatch.setattr(plans_runs.ports, "get_counterexamples", no_counterexamples)

    body = await plans_runs.get_run_unit("run_bb0da2e79534", "unit_pricing")

    assert len(body["verifications"]) == 1
    assert body["verifications"][0]["verification_id"] == "ver_dd3750652173"
    assert body["verifications"][0]["comparisons"][0]["old"]["value"] == "1.25"


async def test_the_unit_endpoint_survives_dev4_being_unavailable(monkeypatch):
    """An unwired verifier must not take the whole unit view down with it."""
    from app.api import plans_runs
    async def get_run(run_id):
        return _a_run("run_1", "migrating", "migrating")

    async def unavailable(*args, **kwargs):
        raise plans_runs.ports.IntegrationUnavailable("Dev 4", "app.verification", "x")

    monkeypatch.setattr(plans_runs.store, "get_run", get_run)
    monkeypatch.setattr(plans_runs.ports, "list_verifications", unavailable)
    monkeypatch.setattr(plans_runs.ports, "get_counterexamples", unavailable)

    body = await plans_runs.get_run_unit("run_1", "unit_pricing")
    assert body["verifications"] == []
    assert body["counterexamples"] == []
    assert body["unit"]["status"] == "migrating"

def test_the_harness_logs_do_not_carry_what_the_comparisons_redact():
    """The differential and impacted-tests gates store the harness's raw output, one
    JSON outcome per case with held-out ones included. Redacting the comparisons alone
    left `hcase_001`'s expected value in the same response, in the gate's log.
    """
    from app.api.plans_runs import WITHHELD_LOG, _withhold_harness_logs

    verification = a_verification([])
    verification["gates"] = [
        {"gate": "differential", "status": "passed", "summary": "",
         "log_excerpt": '{"case_id": "hcase_001", "status": "ok", "value": "2.65"}'},
        {"gate": "impacted_tests", "status": "skipped", "summary": "", "log_excerpt": ""},
        {"gate": "existing_tests", "status": "passed", "summary": "",
         "log_excerpt": "12 passed in 0.14s"},
    ]
    gates = {g["gate"]: g for g in _withhold_harness_logs(verification)["gates"]}

    assert "2.65" not in str(gates)
    assert gates["differential"]["log_excerpt"] == WITHHELD_LOG
    assert gates["impacted_tests"]["log_excerpt"] == ""        # nothing to withhold
    assert gates["existing_tests"]["log_excerpt"] == "12 passed in 0.14s"


# --------------------------------------------------------------------------
# #45 follow-up: nothing held out reaches the response, whatever carries it
# --------------------------------------------------------------------------

HELD_OUT_VALUE = "2.65"
HARNESS_LOG = (
    '{"case_id": "case_001", "status": "ok", "value": "1.25"}\n'
    '{"case_id": "hcase_001", "status": "ok", "value": "' + HELD_OUT_VALUE + '"}\n'
)


def a_gate(name: str, log: str = "") -> dict:
    return {
        "gate": name, "status": "passed", "required": True,
        "summary": "2 case(s) agree with the baseline, 0 differ, 0 inconclusive",
        "passed": 2, "failed": 0, "inconclusive": 0, "log_excerpt": log,
    }


def test_no_held_out_value_survives_anywhere_in_the_unit_response():
    """My first fix at this (`e4fad35`) redacted the comparisons and left the gate
    logs alone, so `hcase_001`'s expected value went out in the same response that
    carefully refused to show it. Dev 5 caught it live on Atlas Local.

    The reason it was invisible: my test fixtures all had `"gates": []`. Asserting on
    the whole serialised response, rather than on one field at a time, is what makes
    that class of gap hard to repeat.
    """
    from app.api.plans_runs import _redact_comparisons, _withhold_harness_logs

    verification = a_verification([
        a_comparison("case_001", "visible", "1.25", "1.26"),
        a_comparison("hcase_001", "held_out", HELD_OUT_VALUE, "2.64"),
    ])
    verification["gates"] = [
        a_gate("compile"),
        a_gate("existing_tests", "2 passed in 0.4s"),
        a_gate("differential", HARNESS_LOG),
        a_gate("impacted_tests", HARNESS_LOG),
    ]

    body = _withhold_harness_logs(_redact_comparisons(verification, exposed=set()))

    assert HELD_OUT_VALUE not in str(body), "a held-out value reached the response"
    # The visible case is unaffected: redaction is per case, not wholesale.
    assert any(c["case_id"] == "case_001" and c["old"]["value"] == "1.25"
               for c in body["comparisons"])
    # A log with nothing held out in it is left alone.
    assert body["gates"][1]["log_excerpt"] == "2 passed in 0.4s"


def test_a_gate_nobody_remembered_to_list_is_still_withheld():
    """`HARNESS_GATES` is a list of names, and only `differential` and
    `impacted_tests` are built by `_comparison_gate` today. A third one added to that
    builder later would leak until someone remembered to add it here, so a gate log
    that mentions a sealed case is withheld whatever the gate is called.
    """
    from app.api.plans_runs import _redact_comparisons, _withhold_harness_logs

    verification = a_verification([
        a_comparison("hcase_001", "held_out", HELD_OUT_VALUE, "2.64"),
    ])
    verification["gates"] = [a_gate("final_integration", HARNESS_LOG)]

    body = _withhold_harness_logs(_redact_comparisons(verification, exposed=set()))

    assert HELD_OUT_VALUE not in str(body)
    assert "mentions a held-out case" in body["gates"][0]["log_excerpt"]


def test_an_exposed_case_does_not_trigger_withholding():
    """Repair already showed this case to the model, so its log may be read too -
    otherwise the gate log disappears exactly when a reviewer needs it most."""
    from app.api.plans_runs import _redact_comparisons, _withhold_harness_logs

    verification = a_verification([
        a_comparison("hcase_001", "held_out", HELD_OUT_VALUE, "2.64"),
    ])
    verification["gates"] = [a_gate("final_integration", HARNESS_LOG)]

    body = _withhold_harness_logs(
        _redact_comparisons(verification, exposed={"hcase_001"})
    )
    assert body["gates"][0]["log_excerpt"] == HARNESS_LOG


def test_harness_gates_are_withheld_even_with_nothing_sealed():
    """So the response shape does not change depending on the data."""
    from app.api.plans_runs import WITHHELD_LOG, _redact_comparisons, _withhold_harness_logs

    verification = a_verification([a_comparison("case_001", "visible", "1.25", "1.26")])
    verification["gates"] = [a_gate("differential", HARNESS_LOG)]

    body = _withhold_harness_logs(_redact_comparisons(verification, exposed=set()))
    assert body["gates"][0]["log_excerpt"] == WITHHELD_LOG


def test_a_verification_with_no_gates_is_passed_through():
    from app.api.plans_runs import _withhold_harness_logs

    verification = a_verification([])
    assert _withhold_harness_logs(verification) == verification
    del verification["gates"]
    assert _withhold_harness_logs(verification) == verification


async def test_the_endpoint_itself_lets_no_held_out_value_through(monkeypatch):
    """Through `get_run_unit`, not through the two helpers it happens to call.

    The previous test asserts the helpers redact. This asserts the *endpoint* does,
    which is what ships: if it stopped calling one of them - or a third field were
    added to the response - the helper-level test would still pass. That is the same
    mistake as fixtures with `"gates": []`, one level up.
    """
    from app.api import plans_runs

    verification = a_verification([
        a_comparison("case_001", "visible", "1.25", "1.26"),
        a_comparison("hcase_001", "held_out", HELD_OUT_VALUE, "2.64"),
    ])
    verification["gates"] = [
        a_gate("compile"),
        a_gate("differential", HARNESS_LOG),
        a_gate("final_integration", HARNESS_LOG),
    ]

    async def get_run(run_id):
        return _a_run("run_bb0da2e79534", "completed", "passed")

    async def list_verifications(run_id, unit_id=None):
        return [verification]

    async def get_counterexamples(run_id, unit_id, *, for_model):
        # The full record, as Dev 4 returns it for the human view.
        return [{
            "counterexample_id": "cex_001", "run_id": run_id, "unit_id": unit_id,
            "attempt": 1, "case_id": "hcase_001", "visibility": "held_out",
            "exposed_to_model": False, "function": "pricing.finalise_amount",
            "args": [[{"unit_price": "2.45"}]], "kwargs": {},
            "old": {"value": HELD_OUT_VALUE}, "new": {"value": "2.64"},
        }]

    monkeypatch.setattr(plans_runs.store, "get_run", get_run)
    monkeypatch.setattr(plans_runs.ports, "list_verifications", list_verifications)
    monkeypatch.setattr(plans_runs.ports, "get_counterexamples", get_counterexamples)

    body = await plans_runs.get_run_unit("run_bb0da2e79534", "unit_pricing")

    serialised = str(body)
    assert HELD_OUT_VALUE not in serialised, "a held-out value reached the endpoint"
    assert "2.45" not in serialised, "a held-out input reached the endpoint"
    # Still useful: the reviewer sees that the sealed case mismatched, and the
    # visible case in full.
    assert any(c["case_id"] == "hcase_001" and c["verdict"] == "mismatch"
               for c in body["verifications"][0]["comparisons"])
    assert any(c["case_id"] == "case_001" and c["old"]["value"] == "1.25"
               for c in body["verifications"][0]["comparisons"])


# --------------------------------------------------------------------------
# SHARED_CONTRACTS §11: search_index is composed on read, never stored
# --------------------------------------------------------------------------

async def test_one_repository_carries_its_indexing_progress(monkeypatch):
    """So the Overview needs no second call (Dev 3's ask, acked 24 Oct)."""
    from app.api import repositories
    from app.search import progress

    async def get_repository(repo_id):
        return _a_repository(repo_id)

    async def get(repo_id):
        return {"repository_id": repo_id, "state": "indexing", "embedded": 40,
                "total": 122, "reason": None, "described": 12}

    monkeypatch.setattr(repositories.ingestion, "get_repository", get_repository)
    monkeypatch.setattr(progress, "get", get)

    record = await repositories.get_repository("repo_000000000001")

    assert record.search_index == {"state": "indexing", "embedded": 40, "total": 122}
    # Only the three agreed keys: the rest is the indexing endpoint's business.
    assert set(record.search_index) == {"state", "embedded", "total"}


async def test_a_repository_never_indexed_reports_nothing_rather_than_zero(monkeypatch):
    """`null` and "0 of 0 embedded" are different claims."""
    from app.api import repositories
    from app.search import progress

    async def get_repository(repo_id):
        return _a_repository(repo_id)

    async def get(repo_id):
        return None

    monkeypatch.setattr(repositories.ingestion, "get_repository", get_repository)
    monkeypatch.setattr(progress, "get", get)

    record = await repositories.get_repository("repo_000000000001")
    assert record.search_index is None


async def test_an_unreadable_status_does_not_take_the_repository_down(monkeypatch):
    from app.api import repositories
    from app.search import progress

    async def get_repository(repo_id):
        return _a_repository(repo_id)

    async def explode(repo_id):
        raise RuntimeError("search_index_status is unreachable")

    monkeypatch.setattr(repositories.ingestion, "get_repository", get_repository)
    monkeypatch.setattr(progress, "get", explode)

    record = await repositories.get_repository("repo_000000000001")
    assert record.search_index is None
    assert record.repository_id == "repo_000000000001"


async def test_the_repository_list_does_not_read_progress_at_all(monkeypatch):
    """`progress.get()` is a query per repository and writes when it finds indexing a
    restart interrupted, so filling a list would be N reads and up to N writes."""
    from app.api import repositories
    from app.search import progress

    called: list[str] = []

    async def list_repositories():
        return [_a_repository("repo_000000000001"), _a_repository("repo_000000000002")]

    async def get(repo_id):
        called.append(repo_id)
        return {"state": "ready", "embedded": 40, "total": 40}

    monkeypatch.setattr(repositories.ingestion, "list_repositories", list_repositories)
    monkeypatch.setattr(progress, "get", get)

    records = await repositories.list_repositories()

    assert called == [], "the list endpoint read indexing status per repository"
    assert all(r.search_index is None for r in records)


def test_search_index_is_not_persisted_on_the_record():
    """A record is written once at import and indexing finishes after it, so a stored
    copy would be stale within seconds and stale for good."""
    import inspect

    from app.ingestion import _persist

    source = inspect.getsource(_persist)
    assert "search_index" not in source, (
        "import is writing search_index into the repositories document"
    )


def _a_repository(repo_id: str):
    from datetime import UTC, datetime

    from app.models.contracts import RepositoryRecord

    return RepositoryRecord(
        repository_id=repo_id, name="chess", snapshot_hash="sha256:abc",
        origin="folder_upload", executable=True, demo_fixture_id="chess",
        file_count=16, module_count=13, symbol_count=120, test_file_count=1,
        analysis_status="ok", created_at=datetime.now(UTC),
    )


# --------------------------------------------------------------------------
# #55: a unit's diff is of the revision its verdict is on
# --------------------------------------------------------------------------

def an_event(sequence: int, unit_id: str | None, revision: str):
    from datetime import UTC, datetime

    from app.models.contracts import RunEvent

    return RunEvent(
        run_id="run_e5380c4d180c", sequence=sequence,
        timestamp=datetime.now(UTC), event_type="snapshot_accepted",
        unit_id=unit_id, payload={"revision": revision},
    )


def a_plan_with_units(*unit_ids: str):
    from datetime import UTC, datetime

    from app.models.contracts import MigrationPlan, MigrationUnit

    return MigrationPlan(
        plan_id="plan_000000000001", repository_id="repo_000000000001",
        snapshot_hash="sha256:abc", target_id="modern_python3",
        units=[
            MigrationUnit(
                unit_id=u, module_ids=[u.replace("unit_", "module_")],
                file_paths=[u.replace("unit_", "") + ".py"],
                depends_on_unit_ids=[], dependant_unit_ids=[],
            )
            for u in unit_ids
        ],
        order=list(unit_ids), rationale="", risks=[], coverage_gaps=[],
        status="approved", model="gemini-4-argon", created_at=datetime.now(UTC),
    )


@pytest.fixture
def diff_world(monkeypatch):
    """A run that accepted two units on two different revisions."""
    from app.api import plans_runs

    state: dict = {
        "events": [
            an_event(10, "unit_customers", "rev_76b17cde768c"),
            an_event(20, "unit_pricing", "rev_18c737308c94"),
        ],
        "asked": [],
    }

    async def get_run(run_id):
        return _a_run(run_id, "completed", "passed")

    async def get_plan(plan_id):
        return a_plan_with_units("unit_customers", "unit_pricing", "unit_refunds")

    async def list_all_events(run_id):
        return state["events"]

    async def diff(run_id, base, revision, paths=None):
        state["asked"].append({"base": base, "revision": revision, "paths": paths})
        return [{"path": (paths[0] if paths else "pricing.py"), "status": "modified"}]

    monkeypatch.setattr(plans_runs.store, "get_run", get_run)
    monkeypatch.setattr(plans_runs.store, "get_plan", get_plan)
    monkeypatch.setattr(plans_runs.events, "list_all_events", list_all_events)
    monkeypatch.setattr(plans_runs.ports, "diff", diff)
    return state


async def test_a_unit_is_diffed_against_its_own_accepted_revision(diff_world):
    """The case from the issue: two accepted units, asking for the first.

    It used to serve the run's last accepted revision for both, so the Diff screen's
    header showed a revision the unit's gates were not verified on.
    """
    from app.api import plans_runs

    body = await plans_runs.get_run_diff("run_e5380c4d180c", unit_id="unit_customers")

    assert body["revision"] == "rev_76b17cde768c"
    assert diff_world["asked"][-1]["revision"] == "rev_76b17cde768c"
    assert diff_world["asked"][-1]["paths"] == ["customers.py"]


async def test_the_last_accepted_unit_is_unaffected(diff_world):
    from app.api import plans_runs

    body = await plans_runs.get_run_diff("run_e5380c4d180c", unit_id="unit_pricing")
    assert body["revision"] == "rev_18c737308c94"


async def test_without_a_unit_the_run_s_last_accepted_revision_is_used(diff_world):
    """Unchanged behaviour: the whole-run diff is of where the run ended up."""
    from app.api import plans_runs

    body = await plans_runs.get_run_diff("run_e5380c4d180c")

    assert body["revision"] == "rev_18c737308c94"
    assert diff_world["asked"][-1]["paths"] is None


async def test_a_unit_that_accepted_nothing_says_so(diff_world):
    """`unit_refunds` was blocked. Serving the last revision filtered to its files
    returned an empty list too, which reads as "accepted, nothing changed"."""
    from app.api import plans_runs

    body = await plans_runs.get_run_diff("run_e5380c4d180c", unit_id="unit_refunds")

    assert body["revision"] is None
    assert body["files"] == []
    assert diff_world["asked"] == [], "a diff was computed for an unaccepted unit"


async def test_a_later_acceptance_of_the_same_unit_wins(diff_world):
    """A repaired unit is accepted more than once; its verdict is on the last one."""
    from app.api import plans_runs

    diff_world["events"].append(an_event(30, "unit_customers", "rev_later0000"))
    body = await plans_runs.get_run_diff("run_e5380c4d180c", unit_id="unit_customers")

    assert body["revision"] == "rev_later0000"


async def test_an_unknown_unit_is_a_404_even_with_nothing_accepted(diff_world):
    """The unit check used to sit after the early return, so this answered 200."""
    from app.api import plans_runs

    diff_world["events"] = []

    with pytest.raises(AppError) as caught:
        await plans_runs.get_run_diff("run_e5380c4d180c", unit_id="unit_nope")

    assert caught.value.status_code == 404
    assert caught.value.code == "UNIT_NOT_FOUND"


async def test_a_run_that_accepted_nothing_has_no_diff(diff_world):
    from app.api import plans_runs

    diff_world["events"] = []
    body = await plans_runs.get_run_diff("run_e5380c4d180c")

    assert body == {"base_revision": "rev_baseline", "revision": None, "files": []}
