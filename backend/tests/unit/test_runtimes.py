"""#22: each side of a migration runs on its own interpreter, and says which.

`python2_to_3` (the chess demo) records the baseline on Python 2.7 and verifies the
candidate on Python 3.14; `modern_python3` keeps both on the original 3.12 image.
The real containers are exercised in `tests/integration/test_sandbox_runtimes_live.py`.
"""

from pathlib import Path

import pytest

import app.verification as verification
from app.config import get_settings
from app.models.contracts import BehaviourCase, MigrationUnit, Workspace
from app.sandbox import runner, runtimes
from app.sandbox.runner import RunResult

UNIT = MigrationUnit(
    unit_id="unit_board", module_ids=["local_chess.board"],
    file_paths=["local_chess/board.py"], depends_on_unit_ids=[], dependant_unit_ids=[],
)
WORKSPACE = Workspace(
    workspace_id="ws_1", run_id="run_1", root=".", base_revision="rev_baseline",
    revision_id="rev_1",
)
CASE = BehaviourCase(
    case_id="case_1", repository_id="repo_1", unit_id=UNIT.unit_id,
    function="local_chess.common.parse_square", args=["e4"],
    visibility="visible", provenance="test", case_hash="h",
)


# --------------------------------------------------------------------------
# which interpreter for which target
# --------------------------------------------------------------------------

def test_python2_to_3_baselines_on_27_and_verifies_on_314():
    pair = runtimes.for_target("python2_to_3")
    settings = get_settings()

    assert (pair.baseline.python, pair.baseline.image) == ("2.7", settings.runner_image_py27)
    assert (pair.candidate.python, pair.candidate.image) == ("3.14", settings.runner_image_py314)


@pytest.mark.parametrize("target_id", ["modern_python3", None])
def test_modern_python3_and_no_target_keep_the_original_image(target_id):
    pair = runtimes.for_target(target_id)

    assert pair.baseline == pair.candidate
    assert pair.candidate.python == "3.12"
    assert pair.candidate.image == get_settings().runner_image


def test_an_unknown_target_is_an_error_not_a_quiet_312():
    with pytest.raises(ValueError, match="fastapi"):
        runtimes.for_target("fastapi")


def test_python27_compiles_in_memory_and_python3_uses_compileall():
    """2.7 has no PYTHONPYCACHEPREFIX, so compileall would write into read-only /code."""
    assert runtimes.runtime("2.7").compile_command == runtimes.COMPILE_TREE
    assert runtimes.runtime("2.7").compile_needs_harness is True
    for python in ("3.12", "3.14"):
        assert runtimes.runtime(python).compile_command == runtimes.COMPILEALL
        assert runtimes.runtime(python).compile_needs_harness is False


# --------------------------------------------------------------------------
# the runner runs the runtime's image, under the same profile
# --------------------------------------------------------------------------

@pytest.fixture
def docker_calls(monkeypatch):
    calls: list[tuple[str, ...]] = []

    async def fake_docker(*args, timeout):
        calls.append(args)
        return RunResult(exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_docker", fake_docker)
    return calls


def _image_and_command(args: tuple[str, ...]) -> tuple[str, list[str]]:
    """Split a `docker run` argument list at the image."""
    images = {runtimes.runtime(p).image for p in ("2.7", "3.12", "3.14")}
    index = next(i for i, a in enumerate(args) if a in images)
    return args[index], list(args[index + 1:])


async def test_the_27_compile_gate_mounts_the_harness_and_writes_nothing(docker_calls):
    await runner.run_compileall(Path("/tmp/tree"), runtime=runtimes.runtime("2.7"))

    args = docker_calls[0]
    image, command = _image_and_command(args)
    assert image == get_settings().runner_image_py27
    assert command == list(runtimes.COMPILE_TREE)
    assert any(a.endswith(":/harness:ro") for a in args)
    assert all(flag in args for flag in runner.SECURITY_PROFILE)


async def test_without_a_runtime_the_gates_run_exactly_as_before(docker_calls):
    await runner.run_compileall(Path("/tmp/tree"))
    await runner.run_pytest(Path("/tmp/tree"))

    for args in docker_calls:
        image, _ = _image_and_command(args)
        assert image == get_settings().runner_image
        assert not any(a.endswith(":/harness:ro") for a in args)
    assert _image_and_command(docker_calls[0])[1] == list(runtimes.COMPILEALL)


async def test_cases_and_tests_run_on_the_runtime_given(docker_calls):
    py314 = runtimes.runtime("3.14")
    await runner.run_behaviour_cases(Path("/tmp/tree"), Path("/tmp/cases.json"), runtime=py314)
    await runner.run_pytest(Path("/tmp/tree"), runtime=py314)

    assert [_image_and_command(a)[0] for a in docker_calls] == [py314.image, py314.image]
    for args in docker_calls:
        assert all(flag in args for flag in runner.SECURITY_PROFILE)


async def test_a_missing_runtime_image_says_how_to_build_it(monkeypatch):
    async def fake_docker(*args, timeout):
        if args[0] == "version":
            return RunResult(exit_code=0, stdout="27.0", stderr="")
        return RunResult(exit_code=1, stdout="", stderr="No such image")

    monkeypatch.setattr(runner, "_docker", fake_docker)

    status = await runner.runner_status(runtimes.runtime("2.7"))

    assert status["available"] is False
    assert "Python 2.7" in status["detail"]
    assert "--build-arg PYTHON_VERSION=2.7" in status["detail"]


async def test_the_probe_reports_what_ran_and_is_cached_per_image(monkeypatch):
    runner._PROBED.clear()
    calls: list[tuple[str, ...]] = []

    async def fake_docker(*args, timeout):
        calls.append(args)
        return RunResult(exit_code=0, stdout="2.7.18\n", stderr="")

    monkeypatch.setattr(runner, "_docker", fake_docker)
    py27 = runtimes.runtime("2.7")

    assert await runner.probe_python(py27, "sha256:abc") == "2.7.18"
    assert await runner.probe_python(py27, "sha256:abc") == "2.7.18"
    assert len(calls) == 1
    assert _image_and_command(calls[0])[0] == py27.image


async def test_a_failed_probe_is_none_never_a_guess(monkeypatch):
    runner._PROBED.clear()

    async def fake_docker(*args, timeout):
        return RunResult(exit_code=125, stdout="", stderr="cannot start")

    monkeypatch.setattr(runner, "_docker", fake_docker)

    assert await runner.probe_python(runtimes.runtime("3.14"), "sha256:def") is None


# --------------------------------------------------------------------------
# verification picks the side's runtime and records what ran
# --------------------------------------------------------------------------

@pytest.fixture
def chess_run(monkeypatch):
    """A python2_to_3 run with every runner call recorded by runtime."""
    used: dict[str, list[str]] = {"status": [], "compile": [], "cases": [], "pytest": []}
    ok = RunResult(exit_code=0, stdout="1 passed", stderr="")

    async def pair(_run_id):
        return runtimes.for_target("python2_to_3")

    async def status(runtime=None):
        used["status"].append(runtime.python)
        return {
            "available": True, "mode": "trusted_docker",
            "image_digest": f"sha-{runtime.python}",
        }

    async def probed(runtime, _digest=None):
        return {"2.7": "2.7.18", "3.14": "3.14.9"}[runtime.python]

    async def compiled(_code, *, runtime=None, **_kwargs):
        used["compile"].append(runtime.python)
        return ok

    async def behaviour(_code, _cases_file, *, runtime=None, **_kwargs):
        used["cases"].append(runtime.python)
        return [{"case_id": CASE.case_id, "status": "ok", "value": {"__tuple__": [4, 4]}}], ok

    async def tests(_code, *, runtime=None, **_kwargs):
        used["pytest"].append(runtime.python)
        return ok

    async def cases(_repo_id, unit_id=None, include_held_out=False):
        return [CASE]

    async def baseline(_run_id):
        from app.models.contracts import CaseOutcome
        return {CASE.case_id: CaseOutcome(case_id=CASE.case_id, status="ok",
                                          value={"__tuple__": [4, 4]})}

    async def nothing(*_args, **_kwargs):
        return None

    async def repo(_run_id):
        return "repo_1"

    async def snapshot_hash(_repo_id):
        return "sha256:snapshot"

    import app.ingestion as ingestion

    monkeypatch.setattr(verification, "runtimes_for_run", pair)
    monkeypatch.setattr(verification.runner, "runner_status", status)
    monkeypatch.setattr(verification.runner, "probe_python", probed)
    monkeypatch.setattr(verification.runner, "run_compileall", compiled)
    monkeypatch.setattr(verification.runner, "run_behaviour_cases", behaviour)
    monkeypatch.setattr(verification.runner, "run_pytest", tests)
    monkeypatch.setattr(verification, "load_cases", cases)
    monkeypatch.setattr(verification, "repository_for_run", repo)
    monkeypatch.setattr(verification, "write_cases_file", lambda *_a: Path("cases.json"))
    monkeypatch.setattr(verification, "_snapshot_hash", snapshot_hash)
    monkeypatch.setattr(ingestion, "snapshot_path", lambda _repo_id: Path("/tmp/snapshot"))
    monkeypatch.setattr(verification.store, "get_baseline_outcomes", baseline)
    monkeypatch.setattr(verification.store, "save_baseline_outcomes", nothing)
    monkeypatch.setattr(verification.store, "save_verification", nothing)
    monkeypatch.setattr(verification.store, "save_counterexamples", nothing)
    monkeypatch.setattr(verification.store, "resolve_counterexamples", nothing)
    return used


async def test_the_baseline_runs_on_python_27_and_records_it(chess_run):
    summary = await verification.capture_baseline("run_1", "repo_1", [UNIT])

    assert chess_run["status"] == ["2.7"]
    assert chess_run["cases"] == ["2.7"]
    assert chess_run["pytest"] == ["2.7"]
    assert summary.environment["python"] == "2.7.18"
    assert summary.environment["image"] == get_settings().runner_image_py27
    assert summary.environment["image_digest"] == "sha-2.7"
    assert summary.environment["candidate_python"] == "3.14"


async def test_every_unit_gate_runs_the_candidate_on_python_314(chess_run):
    result = await verification.verify_unit("run_1", UNIT, WORKSPACE, [], 1)

    assert chess_run["status"] == ["3.14"]
    assert chess_run["compile"] == ["3.14"]
    assert chess_run["cases"] == ["3.14"]
    assert chess_run["pytest"] == ["3.14"]
    assert result.all_required_gates_passed is True
    assert result.environment == {
        "python": "3.14.9",
        "image": get_settings().runner_image_py314,
        "image_digest": "sha-3.14",
        "runner_mode": "trusted_docker",
    }


async def test_final_verification_runs_on_python_314(chess_run):
    result = await verification.verify_final("run_1", WORKSPACE)

    assert chess_run["cases"] == ["3.14"]
    assert chess_run["pytest"] == ["3.14"]
    assert result.environment["python"] == "3.14.9"


async def test_a_missing_314_image_is_inconclusive_not_a_312_fallback(chess_run, monkeypatch):
    async def missing(runtime=None):
        return {"available": False, "mode": "disabled",
                "detail": f"image {runtime.image} (Python {runtime.python}) not found"}

    monkeypatch.setattr(verification.runner, "runner_status", missing)

    result = await verification.verify_unit("run_1", UNIT, WORKSPACE, [], 1)

    assert all(g.status == "inconclusive" for g in result.gates)
    assert result.all_required_gates_passed is False
    assert chess_run["compile"] == chess_run["cases"] == chess_run["pytest"] == []
