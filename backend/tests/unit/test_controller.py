"""UnitControllerImpl tests - owner: Dev 2.

This is the model's entire surface, so these tests are about what it *cannot* do:
exceed its attempt budget, write events it does not own, patch outside its unit, or
be considered verified without a recorded VerificationResult.

The event journal and Dev 4's sandbox are replaced with recorders, so the controller's
own logic is under test rather than Mongo or Docker.
"""

from datetime import UTC, datetime

import pytest

from app.models.contracts import (
    CaseComparison,
    CaseOutcome,
    FilePatch,
    GateResult,
    MigrationUnit,
    PatchAck,
    PatchProposal,
    VerificationResult,
)
from app.orchestration import controller as controller_module
from app.orchestration import events as events_module
from app.orchestration import ports as ports_module
from app.orchestration.controller import NO_ATTEMPTS_LEFT, UnitControllerImpl

UNIT = MigrationUnit(
    unit_id="unit_pricing",
    module_ids=["module_pricing"],
    file_paths=["pricing.py"],
    depends_on_unit_ids=[],
    dependant_unit_ids=["unit_orders"],
    visible_case_count=4,
)


class Recorder:
    """Collects events instead of writing them to Atlas."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def append_event(self, run_id, event_type, unit_id=None, attempt=None, payload=None):
        self.events.append((event_type, payload or {}))

        class _Event:
            sequence = len(self.events)

        return _Event()

    def types(self) -> list[str]:
        return [t for t, _ in self.events]


@pytest.fixture
def recorder(monkeypatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(events_module, "append_event", rec.append_event)
    monkeypatch.setattr(controller_module.events, "append_event", rec.append_event)
    return rec


@pytest.fixture
def sandbox(monkeypatch):
    """A sandbox that accepts any patch and records the allowlist it was given."""
    calls: dict = {"allowed_paths": None, "exposed": []}

    class _Workspace:
        revision_id = "rev_000000000001"

    async def create_workspace(run_id, repo_id, base_revision):
        return _Workspace()

    async def apply_patch(workspace, patch, allowed_paths):
        calls["allowed_paths"] = allowed_paths
        return PatchAck(accepted=True, workspace_revision="rev_000000000001")

    async def expose_case(run_id, case_id):
        calls["exposed"].append(case_id)

    monkeypatch.setattr(ports_module, "create_workspace", create_workspace)
    monkeypatch.setattr(ports_module, "apply_patch", apply_patch)
    monkeypatch.setattr(ports_module, "expose_case", expose_case)
    return calls


def make_patch() -> PatchProposal:
    return PatchProposal(
        unit_id="unit_pricing",
        files=[FilePatch(path="pricing.py", new_content="x = 1\n")],
        summary="modernise pricing",
        rationale="type hints",
    )


def make_verification(*, passed: bool, comparisons=None) -> VerificationResult:
    return VerificationResult(
        verification_id="ver_000000000001",
        run_id="run_000000000001",
        unit_id="unit_pricing",
        attempt=1,
        workspace_revision="rev_000000000001",
        gates=[GateResult(gate="differential", status="passed" if passed else "failed",
                          summary="differential")],
        comparisons=comparisons or [],
        counterexample_ids=[],
        all_required_gates_passed=passed,
        runner_mode="trusted_docker",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )


def comparison(case_id: str, visibility: str, verdict: str) -> CaseComparison:
    outcome = CaseOutcome(case_id=case_id, status="ok")
    return CaseComparison(
        case_id=case_id, visibility=visibility, verdict=verdict,
        old=outcome, new=outcome,
    )


# --------------------------------------------------------------------------
# attempt budget
# --------------------------------------------------------------------------

async def test_budget_starts_at_the_configured_maximum(recorder):
    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    assert ctrl.patch_attempts_remaining() == 3


async def test_each_patch_spends_one_attempt(recorder, sandbox):
    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())
    assert ctrl.patch_attempts_remaining() == 2
    await ctrl.submit_patch(make_patch())
    assert ctrl.patch_attempts_remaining() == 1


async def test_a_fourth_patch_is_refused_and_never_reaches_the_sandbox(recorder, sandbox):
    """The model cannot talk its way past the budget."""
    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    for _ in range(3):
        await ctrl.submit_patch(make_patch())

    sandbox["allowed_paths"] = None
    ack = await ctrl.submit_patch(make_patch())

    assert ack.accepted is False
    assert NO_ATTEMPTS_LEFT in ack.errors
    assert ctrl.patch_attempts_remaining() == 0
    assert sandbox["allowed_paths"] is None  # sandbox was not called


async def test_a_patch_may_only_touch_the_unit_s_own_files(recorder, sandbox):
    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())
    assert sandbox["allowed_paths"] == ["pricing.py"]


async def test_a_second_patch_without_a_verification_is_not_a_repair(recorder, sandbox):
    """A repair follows a failed check. Two patches in a row is just two patches."""
    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())
    await ctrl.submit_patch(make_patch())
    assert "repair_started" not in recorder.types()


async def test_a_patch_after_a_failed_verification_is_announced_as_a_repair(
    recorder, sandbox, monkeypatch
):
    async def verify_unit(*args, **kwargs):
        return make_verification(passed=False)

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)

    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())
    await ctrl.run_verification()
    assert "repair_started" not in recorder.types()

    await ctrl.submit_patch(make_patch())
    assert "repair_started" in recorder.types()


async def test_the_unit_walks_verifying_to_repairing_to_migrating(
    recorder, sandbox, monkeypatch
):
    """Spec §6.2's repair path, in order."""
    async def verify_unit(*args, **kwargs):
        return make_verification(passed=False)

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)

    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())
    await ctrl.run_verification()
    assert ctrl.status == "repairing"

    await ctrl.submit_patch(make_patch())
    assert ctrl.status == "migrating"


async def test_a_unit_out_of_budget_stays_verifying_for_the_worker_to_block(
    recorder, sandbox, monkeypatch
):
    """The controller never blocks a unit; one place decides that."""
    async def verify_unit(*args, **kwargs):
        return make_verification(passed=False)

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)

    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=1)
    await ctrl.submit_patch(make_patch())
    await ctrl.run_verification()

    assert ctrl.patch_attempts_remaining() == 0
    assert ctrl.status == "verifying"
    assert ctrl.is_verified() is False


async def test_applying_a_patch_emits_proposed_then_applied(recorder, sandbox):
    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())
    types = recorder.types()
    assert types.index("patch_proposed") < types.index("patch_applied")


# --------------------------------------------------------------------------
# is_verified
# --------------------------------------------------------------------------

async def test_a_unit_is_not_verified_before_anything_has_been_checked(recorder):
    ctrl = UnitControllerImpl("run_1", UNIT, [])
    assert ctrl.is_verified() is False


async def test_a_unit_is_not_verified_just_because_a_patch_applied(recorder, sandbox):
    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    ack = await ctrl.submit_patch(make_patch())
    assert ack.accepted is True
    assert ctrl.is_verified() is False


async def test_only_a_passing_verification_makes_a_unit_verified(recorder, sandbox, monkeypatch):
    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())

    async def verify_unit(*args, **kwargs):
        return make_verification(passed=False)

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)
    await ctrl.run_verification()
    assert ctrl.is_verified() is False

    async def verify_passing(*args, **kwargs):
        return make_verification(passed=True)

    monkeypatch.setattr(ports_module, "verify_unit", verify_passing)
    # A fresh patch is required before verifying again: one verification per
    # applied patch, which is what the real repair loop does anyway.
    await ctrl.submit_patch(make_patch())
    await ctrl.run_verification()
    assert ctrl.is_verified() is True


async def test_verification_cannot_be_re_rolled_without_a_new_patch(
    recorder, sandbox, monkeypatch
):
    """Otherwise the model could keep calling run_verification until a flaky gate
    happened to pass, and the latest result wins."""
    calls = {"n": 0}

    async def verify_unit(*args, **kwargs):
        calls["n"] += 1
        return make_verification(passed=False)

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)

    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())
    await ctrl.run_verification()
    assert calls["n"] == 1

    second = await ctrl.run_verification()
    assert second["ok"] is False
    assert "not applied" in second["error"]
    assert calls["n"] == 1, "the verifier ran again without a new patch"

    await ctrl.submit_patch(make_patch())
    await ctrl.run_verification()
    assert calls["n"] == 2


async def test_only_one_held_out_case_is_ever_exposed_for_a_unit(
    recorder, sandbox, monkeypatch
):
    """The cap is per unit. Per case, a 3-attempt unit could release three.

    Each attempt fails a different held-out case, so a per-case cap would expose a
    new one every time.
    """
    attempt = {"n": 0}

    async def verify_unit(*args, **kwargs):
        attempt["n"] += 1
        return make_verification(passed=False, comparisons=[
            comparison(f"hcase_{attempt['n']:03d}", "held_out", "mismatch"),
        ])

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)

    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    while ctrl.patch_attempts_remaining() > 0:
        await ctrl.submit_patch(make_patch())
        await ctrl.run_verification()

    assert attempt["n"] == 3, "the loop did not actually run three attempts"
    assert len(sandbox["exposed"]) == 1, (
        f"more than one held-out case leaked: {sandbox['exposed']}"
    )


async def test_a_sandbox_outage_does_not_cost_the_model_an_attempt(
    recorder, monkeypatch
):
    """Blocking a unit because infrastructure failed blames the model for someone
    else's problem."""
    async def unavailable(*args, **kwargs):
        raise ports_module.IntegrationUnavailable("Dev 4", "app.sandbox",
                                                  "create_workspace")

    monkeypatch.setattr(ports_module, "create_workspace", unavailable)

    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    before = ctrl.patch_attempts_remaining()
    ack = await ctrl.submit_patch(make_patch())

    assert ack.accepted is False
    assert ctrl.patch_attempts_remaining() == before, "an attempt was spent on an outage"


async def test_an_unavailable_verifier_never_counts_as_verified(recorder, sandbox, monkeypatch):
    """Inconclusive is not a pass."""
    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())

    async def unavailable(*args, **kwargs):
        raise ports_module.IntegrationUnavailable("Dev 4", "app.verification", "verify_unit")

    monkeypatch.setattr(ports_module, "verify_unit", unavailable)
    summary = await ctrl.run_verification()

    assert summary["ok"] is False
    assert summary["inconclusive"] is True
    assert ctrl.is_verified() is False


async def test_verifying_without_a_workspace_is_refused(recorder):
    ctrl = UnitControllerImpl("run_1", UNIT, [])
    summary = await ctrl.run_verification()
    assert summary["ok"] is False
    assert "workspace" in summary["error"]


# --------------------------------------------------------------------------
# what the model is told
# --------------------------------------------------------------------------

async def test_held_out_failures_are_reported_only_as_a_count(
    recorder, sandbox, monkeypatch
):
    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())

    async def verify_unit(*args, **kwargs):
        return make_verification(passed=False, comparisons=[
            comparison("case_001", "visible", "mismatch"),
            comparison("hcase_001", "held_out", "mismatch"),
            comparison("hcase_002", "held_out", "mismatch"),
        ])

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)
    summary = await ctrl.run_verification()

    assert summary["failing_visible_case_ids"] == ["case_001"]
    assert summary["failing_held_out_count"] == 2
    # The held-out ids must not leak into the model-safe summary.
    assert "hcase_001" not in str(summary["failing_visible_case_ids"])
    assert "hcase_001" not in summary.get("exposed_case_ids", [])


async def test_nothing_is_exposed_while_a_visible_case_is_still_failing(
    recorder, sandbox, monkeypatch
):
    """The model has something to work from, so the held-out suite stays sealed."""
    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())

    async def verify_unit(*args, **kwargs):
        return make_verification(passed=False, comparisons=[
            comparison("case_001", "visible", "mismatch"),
            comparison("hcase_001", "held_out", "mismatch"),
        ])

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)
    summary = await ctrl.run_verification()

    assert summary["exposed_case_ids"] == []
    assert sandbox["exposed"] == []


async def test_one_held_out_case_is_exposed_when_only_held_out_cases_fail(
    recorder, sandbox, monkeypatch
):
    """Otherwise the model is repairing blind. Exactly one, and it is recorded."""
    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())

    async def verify_unit(*args, **kwargs):
        return make_verification(passed=False, comparisons=[
            comparison("hcase_003", "held_out", "mismatch"),
            comparison("hcase_001", "held_out", "mismatch"),
        ])

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)
    summary = await ctrl.run_verification()

    assert summary["exposed_case_ids"] == ["hcase_001"]  # lowest id, deterministic
    assert sandbox["exposed"] == ["hcase_001"]


async def test_exposure_does_not_repeat_for_the_same_case(recorder, sandbox, monkeypatch):
    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)

    async def verify_unit(*args, **kwargs):
        return make_verification(passed=False, comparisons=[
            comparison("hcase_001", "held_out", "mismatch"),
        ])

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)
    await ctrl.submit_patch(make_patch())
    await ctrl.run_verification()
    await ctrl.submit_patch(make_patch())
    await ctrl.run_verification()

    assert sandbox["exposed"] == ["hcase_001"]


# --------------------------------------------------------------------------
# emit
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "event_type", ["agent_turn", "agent_tool_call", "agent_tool_result"]
)
async def test_the_agent_may_emit_its_own_events(recorder, event_type):
    ctrl = UnitControllerImpl("run_1", UNIT, [])
    await ctrl.emit(event_type, {"text_excerpt": "thinking"})
    assert event_type in recorder.types()


@pytest.mark.parametrize(
    "event_type",
    ["verification_passed", "unit_state_changed", "gate_result",
     "snapshot_accepted", "run_state_changed"],
)
async def test_the_agent_cannot_forge_orchestrator_events(recorder, event_type):
    """The model must not be able to write itself a passing verification."""
    ctrl = UnitControllerImpl("run_1", UNIT, [])
    with pytest.raises(ValueError, match="not an agent event"):
        await ctrl.emit(event_type, {"ok": True})
    assert recorder.types() == []


# --------------------------------------------------------------------------
# second review round
# --------------------------------------------------------------------------

async def test_a_rejected_patch_cannot_then_be_verified(recorder, sandbox, monkeypatch):
    """The critical one: a rejected patch leaves the workspace holding the untouched
    baseline. Verifying it would sail through every gate and mark the unit migrated
    when nothing was migrated."""
    async def reject(workspace, patch, allowed_paths):
        return PatchAck(accepted=False, workspace_revision=None,
                        errors=["path not in allowed_paths"])

    monkeypatch.setattr(ports_module, "apply_patch", reject)

    verified = {"called": False}

    async def verify_unit(*args, **kwargs):
        verified["called"] = True
        return make_verification(passed=True)

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)

    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    ack = await ctrl.submit_patch(make_patch())
    assert ack.accepted is False

    summary = await ctrl.run_verification()

    assert summary["ok"] is False
    assert "not applied" in summary["error"]
    assert verified["called"] is False, "verified a tree with no patch applied"
    assert ctrl.is_verified() is False


async def test_an_applied_patch_clears_the_guard(recorder, sandbox, monkeypatch):
    async def verify_unit(*args, **kwargs):
        return make_verification(passed=True)

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)

    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())
    assert ctrl.patch_applied is True
    assert (await ctrl.run_verification())["ok"] is True


async def test_the_workspace_is_created_for_the_repository_not_the_unit(
    recorder, monkeypatch
):
    """create_workspace(run_id, repo_id, base_revision) - unit_id was being passed
    in the repo_id position, so Dev 4 would look up a repository that cannot exist."""
    seen = {}

    async def create_workspace(run_id, repo_id, base_revision):
        seen["run_id"] = run_id
        seen["repo_id"] = repo_id

        class _W:
            revision_id = "rev_1"

        return _W()

    async def apply_patch(workspace, patch, allowed_paths):
        return PatchAck(accepted=True, workspace_revision="rev_1")

    monkeypatch.setattr(ports_module, "create_workspace", create_workspace)
    monkeypatch.setattr(ports_module, "apply_patch", apply_patch)

    ctrl = UnitControllerImpl("run_1", UNIT, [], repository_id="repo_abc",
                              max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())

    assert seen["repo_id"] == "repo_abc"
    assert seen["repo_id"] != UNIT.unit_id


async def test_exposing_a_held_out_case_is_written_to_the_journal(
    recorder, sandbox, monkeypatch
):
    """Without the event the report's 'a held-out case was revealed' caveat can
    never fire, and the run silently loses an independent check."""
    async def verify_unit(*args, **kwargs):
        return make_verification(passed=False, comparisons=[
            comparison("hcase_001", "held_out", "mismatch"),
        ])

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)

    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())
    await ctrl.run_verification()

    exposures = [p for t, p in recorder.events
                 if t == "counterexample_recorded" and p.get("exposed_to_model")]
    assert exposures, "exposure was not journalled"
    assert exposures[0]["case_id"] == "hcase_001"
    assert exposures[0]["visibility"] == "held_out"


async def test_nothing_is_exposed_when_no_attempts_remain(recorder, sandbox, monkeypatch):
    """Exposing a case the model can no longer act on spends an independent check
    for nothing. The held-out suite is only worth anything while it is sealed."""
    async def verify_unit(*args, **kwargs):
        return make_verification(passed=False, comparisons=[
            comparison("hcase_001", "held_out", "mismatch"),
        ])

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)

    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=1)
    await ctrl.submit_patch(make_patch())
    summary = await ctrl.run_verification()

    assert ctrl.patch_attempts_remaining() == 0
    assert summary["exposed_case_ids"] == []
    assert sandbox["exposed"] == []


async def test_a_case_is_still_exposed_while_a_repair_is_possible(
    recorder, sandbox, monkeypatch
):
    async def verify_unit(*args, **kwargs):
        return make_verification(passed=False, comparisons=[
            comparison("hcase_001", "held_out", "mismatch"),
        ])

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)

    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())
    summary = await ctrl.run_verification()

    assert ctrl.patch_attempts_remaining() > 0
    assert summary["exposed_case_ids"] == ["hcase_001"]


# --------------------------------------------------------------------------
# #94: the journal never stores held-out case outputs
# --------------------------------------------------------------------------

async def test_a_gate_result_event_withholds_held_out_case_outputs(
    recorder, sandbox, monkeypatch
):
    """`GET /runs/{id}/events` serves the journal as stored, so a harness log that
    reaches it reaches the browser and the report narrative."""
    harness_log = '{"case_id": "case_hidden", "value": 42}'
    verification = make_verification(
        passed=False, comparisons=[comparison("case_hidden", "held_out", "mismatch")]
    )
    verification.gates = [
        GateResult(gate="differential", status="failed", summary="d",
                   log_excerpt=harness_log),
        GateResult(gate="existing_tests", status="failed", summary="t",
                   log_excerpt="AssertionError in case_hidden: 42 != 41"),
        GateResult(gate="compile", status="passed", summary="c",
                   log_excerpt="compiled 1 file"),
    ]

    async def verify_unit(*args, **kwargs):
        return verification

    monkeypatch.setattr(ports_module, "verify_unit", verify_unit)

    ctrl = UnitControllerImpl("run_1", UNIT, [], max_patch_attempts=3)
    await ctrl.submit_patch(make_patch())
    await ctrl.run_verification()

    logs = {p["gate"]: p["log_excerpt"] for t, p in recorder.events if t == "gate_result"}
    assert logs["differential"] == events_module.WITHHELD_LOG
    assert logs["existing_tests"] == events_module.WITHHELD_LOG
    assert logs["compile"] == "compiled 1 file"
