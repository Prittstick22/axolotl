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

    async def cases(_repo_id, _unit_id=None, include_held_out=False):
        return state["cases"]

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


async def test_a_mismatching_case_still_fails_the_unit(world):
    world["cases"] = [CASE]
    world["new_value"] = 2

    result = await verification.verify_unit("run_1", UNIT, WORKSPACE, [], 1)

    differential = next(g for g in result.gates if g.gate == "differential")
    assert differential.status == "failed"
    assert differential.required is True
    assert result.all_required_gates_passed is False
