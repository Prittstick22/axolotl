"""Unit gates - owner: Dev 4.

`verify_unit` driven offline: the runner and the stores are faked, so only the gate
logic and the pass rule run. The container side is covered in test_sandbox_live.py.
"""

from pathlib import Path

import pytest

import app.verification as verification
from app.models.contracts import BehaviourCase, CaseOutcome, MigrationUnit, Workspace
from app.sandbox.runner import RunResult

UNIT = MigrationUnit(
    unit_id="unit_storage", module_ids=["storage"], file_paths=["storage.py"],
    depends_on_unit_ids=[], dependant_unit_ids=[],
)
WORKSPACE = Workspace(
    workspace_id="ws_1", run_id="run_1", root=".", base_revision="rev_baseline",
    revision_id="rev_1",
)
CASE = BehaviourCase(
    case_id="case_1", repository_id="repo_1", unit_id="unit_storage",
    function="storage.load", args=[], visibility="visible",
    provenance="test", case_hash="h",
)


@pytest.fixture
def world(monkeypatch):
    """A clean runner, no stores. `world["cases"]` and `world["new_value"]` steer it."""
    state = {"cases": [], "new_value": 1}
    ok = RunResult(exit_code=0, stdout="1 passed", stderr="")

    async def status():
        return {"available": True, "mode": "trusted_docker"}

    async def clean(_candidate: Path):
        return ok

    async def behaviour(_candidate, _cases_file):
        return [{"case_id": c.case_id, "status": "ok", "value": state["new_value"]}
                for c in state["cases"]], ok

    async def cases(_repo_id, unit_id=None, include_held_out=False):
        return [
            case for case in state["cases"]
            if (unit_id is None or case.unit_id == unit_id)
            and (include_held_out or case.visibility == "visible")
        ]

    async def baseline(_run_id):
        return {"case_1": CaseOutcome(case_id="case_1", status="ok", value=1)}

    async def nothing(*_args, **_kwargs):
        return None

    async def repo(_run_id):
        return "repo_1"

    monkeypatch.setattr(verification.runner, "runner_status", status)
    monkeypatch.setattr(verification.runner, "run_compileall", clean)
    monkeypatch.setattr(verification.runner, "run_pytest", clean)
    monkeypatch.setattr(verification.runner, "run_behaviour_cases", behaviour)
    monkeypatch.setattr(verification, "load_cases", cases)
    monkeypatch.setattr(verification, "repository_for_run", repo)
    monkeypatch.setattr(verification, "write_cases_file", lambda *_a: Path("cases.json"))
    monkeypatch.setattr(verification.store, "get_baseline_outcomes", baseline)
    monkeypatch.setattr(verification.store, "save_verification", nothing)
    monkeypatch.setattr(verification.store, "save_counterexamples", nothing)
    monkeypatch.setattr(verification.store, "resolve_counterexamples", nothing)
    return state


async def test_a_unit_with_no_cases_passes_on_its_other_gates(world):
    """#2: storage.py passed every gate and was still blocked."""
    result = await verification.verify_unit("run_1", UNIT, WORKSPACE, [], 1)

    differential = next(g for g in result.gates if g.gate == "differential")
    assert differential.status == "skipped"
    assert differential.required is False
    assert result.all_required_gates_passed is True


@pytest.mark.parametrize("visibility", ["visible", "held_out"])
async def test_a_mismatching_case_still_fails_the_unit(world, visibility):
    world["cases"] = [CASE.model_copy(update={"visibility": visibility})]
    world["new_value"] = 2

    result = await verification.verify_unit("run_1", UNIT, WORKSPACE, [], 1)

    differential = next(g for g in result.gates if g.gate == "differential")
    assert differential.status == "failed"
    assert differential.required is True
    assert result.all_required_gates_passed is False


async def test_a_unit_with_only_held_out_cases_is_still_compared(world):
    world["cases"] = [CASE.model_copy(update={"visibility": "held_out"})]

    result = await verification.verify_unit("run_1", UNIT, WORKSPACE, [], 1)

    differential = next(g for g in result.gates if g.gate == "differential")
    assert differential.status == "passed"
    assert differential.required is True
    assert differential.passed == 1
    assert result.all_required_gates_passed is True


@pytest.mark.parametrize(
    ("method", "runner_result", "gate_name", "gate_status"),
    [
        ("run_compileall", RunResult(1, "", "syntax error"), "compile", "failed"),
        ("run_pytest", RunResult(1, "1 failed", ""), "existing_tests", "failed"),
        ("run_pytest", RunResult(-1, "", "", timed_out=True),
         "existing_tests", "inconclusive"),
        ("run_pytest", RunResult(0, "1 passed", "", killed=True),
         "sandbox_policy", "failed"),
        ("run_pytest", RunResult(0, "1 passed", "", oversized_output=True),
         "sandbox_policy", "failed"),
        ("run_pytest", RunResult(-1, "", "", docker_error="daemon unavailable"),
         "existing_tests", "inconclusive"),
    ],
    ids=["compile", "tests", "timeout", "killed", "output-limit", "docker-error"],
)
async def test_no_cases_does_not_bypass_other_gates(
    world, monkeypatch, method, runner_result, gate_name, gate_status,
):
    async def failed(_candidate):
        return runner_result

    monkeypatch.setattr(verification.runner, method, failed)

    result = await verification.verify_unit("run_1", UNIT, WORKSPACE, [], 1)

    gate = next(g for g in result.gates if g.gate == gate_name)
    assert gate.status == gate_status
    assert gate.required is True
    assert result.all_required_gates_passed is False


async def test_no_cases_does_not_bypass_an_unavailable_runner(world, monkeypatch):
    async def unavailable():
        return {"available": False, "mode": "disabled"}

    monkeypatch.setattr(verification.runner, "runner_status", unavailable)

    result = await verification.verify_unit("run_1", UNIT, WORKSPACE, [], 1)

    assert all(g.status == "inconclusive" for g in result.gates)
    assert result.all_required_gates_passed is False


@pytest.mark.parametrize("missing_side", ["baseline", "candidate"])
async def test_missing_case_outcomes_are_inconclusive_not_skipped(
    world, monkeypatch, missing_side,
):
    world["cases"] = [CASE]

    async def missing_baseline(_run_id):
        return {}

    async def missing_candidate(_candidate, _cases_file):
        return [], RunResult(0, "", "")

    if missing_side == "baseline":
        monkeypatch.setattr(verification.store, "get_baseline_outcomes", missing_baseline)
    else:
        monkeypatch.setattr(verification.runner, "run_behaviour_cases", missing_candidate)

    result = await verification.verify_unit("run_1", UNIT, WORKSPACE, [], 1)

    differential = next(g for g in result.gates if g.gate == "differential")
    assert differential.status == "inconclusive"
    assert differential.required is True
    assert result.all_required_gates_passed is False


@pytest.mark.parametrize(("new_value", "passed"), [(1, True), (2, False)])
async def test_a_unit_with_no_cases_still_checks_its_dependants(world, new_value, passed):
    dependant = MigrationUnit(
        unit_id="unit_orders", module_ids=["orders"], file_paths=["orders.py"],
        depends_on_unit_ids=[UNIT.unit_id], dependant_unit_ids=[],
    )
    world["cases"] = [CASE.model_copy(update={
        "unit_id": dependant.unit_id, "function": "orders.load", "visibility": "held_out",
    })]
    world["new_value"] = new_value

    result = await verification.verify_unit("run_1", UNIT, WORKSPACE, [dependant], 1)

    differential = next(g for g in result.gates if g.gate == "differential")
    impacted = next(g for g in result.gates if g.gate == "impacted_tests")
    assert differential.status == "skipped"
    assert differential.required is False
    assert impacted.status == ("passed" if passed else "failed")
    assert impacted.required is True
    assert result.all_required_gates_passed is passed
