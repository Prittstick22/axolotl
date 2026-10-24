"""Timing evidence cannot change verdicts or describe a rejected revision."""

import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.contracts import CaseOutcome
from app.reports import performance
from app.verification.comparison import compare, outcome_from_harness


@pytest.fixture
def harness(monkeypatch):
    # Only the trusted harness is loaded; candidate functions are fakes. No
    # imported project/fixture is executed on the host.
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[3]
                                   / "runner/harness/run_cases.py"))
    env = namespace["run_one"].__globals__
    monkeypatch.setattr(env["signal"], "signal", lambda *a: None)
    monkeypatch.setattr(env["signal"], "alarm", lambda *a: None, raising=False)
    monkeypatch.setattr(env["signal"], "SIGALRM", 14, raising=False)
    state = {"calls": 0, "resets": 0}

    def reset():
        state["resets"] += 1

    def invoke(values):
        state["calls"] += 1
        values.append(1)
        return values

    ticks = iter([0, 500, 1000, 1300, 2000, 2100, 3000, 3400, 4000, 4200])
    env.update(reset_state=reset, resolve=lambda _: invoke, cpu_time_ns=lambda: next(ticks))
    return env, state


def test_five_cpu_samples_exclude_warmup_and_reset_mutable_inputs(harness):
    env, state = harness
    result = env["run_one"]({"case_id": "a", "function": "fake.f", "args": [[]]}, 5)
    assert state == {"calls": 6, "resets": 6}
    assert result["value"] == [1]
    assert result["duration_samples_ns"] == [500, 300, 100, 400, 200]
    assert result["duration_ns"] == 300


@pytest.mark.parametrize("failure", ["different", "timeout", "raised"])
def test_unreliable_benchmark_does_not_replace_the_original_outcome(harness, failure):
    env, state = harness

    def invoke():
        state["calls"] += 1
        if state["calls"] > 1:
            if failure == "timeout":
                raise env["CaseTimeout"]()
            if failure == "raised":
                raise ValueError()
            return 2
        return 1

    env["resolve"] = lambda _: invoke
    result = env["run_one"]({"case_id": "a", "function": "fake.f"}, 5)
    assert result["status"] == "ok" and result["value"] == 1
    assert "duration_ns" not in result


def outcome(value=1, samples=None):
    samples = samples if samples is not None else [1000, 2000, 3000, 4000, 5000]
    return outcome_from_harness({
        "case_id": "a", "status": "ok", "value": value,
        "duration_samples_ns": samples, "duration_ns": sorted(samples)[2],
    }, "a")


def test_timing_survives_persistence_but_is_not_part_of_behaviour_equality():
    old, new = outcome(), outcome(samples=[5000] * 5)
    saved = CaseOutcome.model_validate_json(old.model_dump_json())
    assert saved.duration_ns == 3000 and len(saved.duration_samples_ns) == 5
    assert compare("a", "visible", saved, new).verdict == "match"
    assert compare("a", "visible", saved, outcome(value=2)).verdict == "mismatch"


@pytest.mark.parametrize("samples,duration", [
    (None, None), ([1], 1), ([True] * 5, 1), ([-1] * 5, -1), ([1] * 5, 99),
])
def test_legacy_or_invalid_timings_are_absent(samples, duration):
    result = outcome_from_harness({"status": "ok", "duration_ns": duration,
                                  "duration_samples_ns": samples}, "a")
    assert result.duration_ns is None and result.duration_samples_ns is None


@pytest.fixture
def evidence(monkeypatch):
    unit = SimpleNamespace(unit_id="unit_a", status="passed")
    run = SimpleNamespace(run_id="run_a", repository_id="repo_a", units=[unit],
                          runner_mode="trusted_docker", state="completed")
    final = SimpleNamespace(
        verification_id="final", run_id="run_a", unit_id=None,
        workspace_revision="rev_accepted", runner_mode="trusted_docker",
        all_required_gates_passed=True,
        comparisons=[compare("a", "held_out", outcome(), outcome(samples=[5000] * 5))],
    )

    async def results(_run):
        return [final]

    async def cases(_repo, **kwargs):
        assert kwargs["include_held_out"] is True
        return [SimpleNamespace(case_id="a", unit_id="unit_a")]

    monkeypatch.setattr(performance.ports, "list_verifications", results)
    monkeypatch.setattr(performance.ports, "load_cases", cases)
    return run, final


async def test_report_uses_paired_final_cases_and_sample_spread(evidence):
    run, final = evidence
    # Duplicate comparisons cannot inflate counts.
    final.comparisons *= 2
    summary = await performance.build_performance(run, "rev_accepted", "final")
    unit, = summary.units
    assert (unit.cases_timed, unit.before_median_us, unit.after_median_us) == (1, 3, 5)
    assert unit.within_noise is True  # delta 2us == baseline sample IQR 2us
    assert "Sandbox CPU timings on tested cases only" in summary.method


@pytest.mark.parametrize("field,value", [
    ("verification_id", "other"), ("workspace_revision", "rejected"),
    ("run_id", "another_run"), ("unit_id", "unit_a"),
    ("all_required_gates_passed", False), ("runner_mode", "disabled"),
])
async def test_report_rejects_unrelated_or_failed_evidence(evidence, field, value):
    run, final = evidence
    setattr(final, field, value)
    assert await performance.build_performance(run, "rev_accepted", "final") is None


@pytest.mark.parametrize("kind", ["mismatch", "untimed", "failed_run", "blocked_unit"])
async def test_report_omits_cases_without_usable_measurements(evidence, kind):
    run, final = evidence
    if kind == "mismatch":
        final.comparisons[0].verdict = "mismatch"
    elif kind == "untimed":
        final.comparisons[0].new.duration_samples_ns = None
    elif kind == "failed_run":
        run.state = "failed"
    else:
        run.units[0].status = "blocked"
    assert await performance.build_performance(run, "rev_accepted", "final") is None
