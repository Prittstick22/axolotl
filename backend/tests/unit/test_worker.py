"""Run worker tests - owner: Dev 2. Covers T-13 and T-14.

The worker is the deterministic heart: it decides what runs next and what a run is
allowed to claim at the end. These tests drive whole runs through a fake world -
in-memory store, recorded events, scripted verification outcomes - so the run algorithm
is exercised without Atlas, Docker or Gemini.

The fake store round-trips through `model_dump()` exactly as Mongo does, so a bug where
the worker relies on object identity instead of persisted state would show up here.

What these are really guarding:
  - nothing is migrated through a failed prerequisite (T-13)
  - final verification always runs (T-14)
  - no run ever reports `completed` unless every unit passed AND the final gates passed
  - no code path raises IllegalTransition at runtime
"""

from datetime import UTC, datetime

import pytest

from app.agents.contexts import UnitSessionOutcome
from app.errors import AppError
from app.models.contracts import (
    BaselineSummary,
    FilePatch,
    GateResult,
    MigrationPlan,
    MigrationUnit,
    PatchAck,
    PatchProposal,
    VerificationResult,
)
from app.orchestration import events as events_module
from app.orchestration import ports as ports_module
from app.orchestration import store as store_module
from app.orchestration import worker as worker_module

# The demo fixture's real shape: main -> orders,refunds; orders -> pricing,customers,
# storage; refunds -> pricing,storage.
UNIT_SPECS = [
    ("unit_pricing", ["module_pricing"], [], ["unit_orders", "unit_refunds"]),
    ("unit_customers", ["module_customers"], [], ["unit_orders"]),
    ("unit_storage", ["module_storage"], [], ["unit_orders", "unit_refunds"]),
    ("unit_orders", ["module_orders"],
     ["unit_customers", "unit_pricing", "unit_storage"], ["unit_main"]),
    ("unit_refunds", ["module_refunds"], ["unit_pricing", "unit_storage"], ["unit_main"]),
    ("unit_main", ["module_main"], ["unit_orders", "unit_refunds"], []),
]


def make_units() -> list[MigrationUnit]:
    return [
        MigrationUnit(
            unit_id=uid, module_ids=mods,
            file_paths=[m.removeprefix("module_") + ".py" for m in mods],
            depends_on_unit_ids=deps, dependant_unit_ids=dependants,
        )
        for uid, mods, deps, dependants in UNIT_SPECS
    ]


def make_plan(units=None) -> MigrationPlan:
    units = units if units is not None else make_units()
    return MigrationPlan(
        plan_id="plan_test000001",
        repository_id="repo_test000001",
        snapshot_hash="sha256:test",
        target_id="modern_python3",
        units=units,
        order=["unit_customers", "unit_pricing", "unit_storage",
               "unit_orders", "unit_refunds", "unit_main"],
        rationale="deterministic",
        risks=[], coverage_gaps=[],
        status="approved",
        model="gemini-4-argon",
        created_at=datetime.now(UTC),
        approved_at=datetime.now(UTC),
    )


class World:
    """A fake Atlas + agent + verifier, scripted per test."""

    def __init__(self, plan: MigrationPlan, *, verdicts=None, final=True,
                 baseline=True, agent=True):
        self.plan = plan
        self.runs: dict[str, dict] = {}
        self.events: list[dict] = []
        self.artefacts: list[dict] = []
        # unit_id -> list of bools, one per verification attempt
        self.verdicts: dict[str, list[bool]] = verdicts or {}
        self.final_passes = final
        self.baseline_available = baseline
        self.agent_available = agent
        self.attempts: dict[str, int] = {}
        self.accepted: list[str] = []
        self.migrated_order: list[str] = []
        self.workspace_counter = 0
        self.accepted_workspaces: list[str] = []
        self.final_verified_revision: str | None = None
        # "normal" stops once verified; "greedy" submits one more unverified patch
        self.agent_mode = "normal"
        self.cancel_after_baseline = False
        self.cancel_during_final = False
        self.missing_images: set[str] = set()   # Python versions with no image (#95)

    # -- store -----------------------------------------------------------
    async def get_plan(self, plan_id):
        return self.plan

    async def save_plan(self, plan):
        self.plan = plan
        return plan

    async def save_run(self, run):
        run.updated_at = datetime.now(UTC)
        self.runs[run.run_id] = run.model_dump()
        return run

    async def get_run(self, run_id):
        from app.models.contracts import RunSummary

        if run_id not in self.runs:
            from app.errors import RUN_NOT_FOUND, AppError

            raise AppError(RUN_NOT_FOUND, "no run", status_code=404)
        return RunSummary(**self.runs[run_id])

    async def supersede_open_plans(self, repo_id, except_plan_id):
        return 0

    async def list_unfinished_runs(self):
        """Non-terminal runs, as the real store's query returns them (#17)."""
        from app.models.contracts import RunSummary
        from app.orchestration.states import TERMINAL_RUN_STATES

        return [
            RunSummary(**document) for document in self.runs.values()
            if document["state"] not in TERMINAL_RUN_STATES
        ]

    async def save_artefact(self, artefact):
        self.artefacts.append(artefact)
        return artefact

    async def list_artefacts(self, run_id):
        return [a for a in self.artefacts if a["run_id"] == run_id]

    # -- events ----------------------------------------------------------
    async def append_event(self, run_id, event_type, unit_id=None, attempt=None,
                           payload=None):
        self.events.append({
            "run_id": run_id, "event_type": event_type, "unit_id": unit_id,
            "attempt": attempt, "payload": payload or {},
        })
        sequence = len(self.events)
        # The real append_event records the highest sequence so _save can keep
        # RunSummary.last_event_sequence level with the journal. A fake that skips
        # that bookkeeping would test itself rather than the worker.
        events_module._LAST_SEQUENCE[run_id] = sequence

        class _E:
            pass

        _E.sequence = sequence
        return _E()

    async def list_events(self, run_id, after_sequence=0, limit=200):
        from app.models.contracts import RunEvent

        return [
            RunEvent(run_id=run_id, sequence=i + 1, timestamp=datetime.now(UTC),
                     event_type=e["event_type"], unit_id=e["unit_id"],
                     attempt=e["attempt"], payload=e["payload"])
            for i, e in enumerate(self.events)
            if e["run_id"] == run_id and i + 1 > after_sequence
        ][:limit]

    def types(self, unit_id=None):
        return [e["event_type"] for e in self.events
                if unit_id is None or e["unit_id"] == unit_id]

    # -- ports -----------------------------------------------------------
    async def runner_status(self, runtime=None):
        python = runtime.python if runtime is not None else "3.12"
        if python in self.missing_images:
            return {"mode": "disabled", "available": False,
                    "detail": f"image for Python {python} not found"}
        return {"mode": "trusted_docker", "available": True, "image_digest": "sha256:x"}

    async def capture_baseline(self, run_id, repo_id, units):
        if not self.baseline_available:
            raise ports_module.IntegrationUnavailable(
                "Dev 4", "app.verification", "capture_baseline")
        if self.cancel_after_baseline:
            worker_module._cancelled.add(run_id)
        return BaselineSummary(
            run_id=run_id, snapshot_hash="sha256:test",
            unit_ids=[u.unit_id for u in units],
            cases_captured=26, cases_failed_to_capture=0,
            existing_tests=GateResult(gate="existing_tests", status="passed",
                                      summary="12 passed", passed=12),
            environment={"python": "3.12.15", "runner_mode": "trusted_docker"},
        )

    async def create_workspace(self, run_id, repo_id, base_revision):
        self.workspace_counter += 1

        class _W:
            revision_id = f"rev_ws{self.workspace_counter:03d}"
            base = base_revision
            root = "/tmp/ws"

        return _W()

    async def apply_patch(self, workspace, patch, allowed_paths):
        return PatchAck(accepted=True, workspace_revision="rev_candidate01")

    async def verify_unit(self, run_id, unit, workspace, impacted, attempt):
        script = self.verdicts.get(unit.unit_id, [True])
        index = min(attempt - 1, len(script) - 1)
        passed = script[index]
        return VerificationResult(
            verification_id=f"ver_{unit.unit_id}_{attempt}",
            run_id=run_id, unit_id=unit.unit_id, attempt=attempt,
            workspace_revision="rev_candidate01",
            gates=[GateResult(gate="differential",
                              status="passed" if passed else "failed",
                              summary="differential",
                              passed=4 if passed else 3, failed=0 if passed else 1)],
            comparisons=[], counterexample_ids=[],
            all_required_gates_passed=passed,
            runner_mode="trusted_docker",
            started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
        )

    async def verify_final(self, run_id, workspace):
        self.final_verified_revision = getattr(workspace, "base", None)
        if self.cancel_during_final:
            worker_module._cancelled.add(run_id)
        if self.final_passes is None:
            raise ports_module.IntegrationUnavailable(
                "Dev 4", "app.verification", "verify_final")
        return VerificationResult(
            verification_id="ver_final", run_id=run_id, unit_id=None, attempt=1,
            workspace_revision="rev_final",
            gates=[GateResult(gate="final_integration",
                              status="passed" if self.final_passes else "failed",
                              summary="final")],
            comparisons=[], counterexample_ids=[],
            all_required_gates_passed=bool(self.final_passes),
            runner_mode="trusted_docker",
            started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
        )

    async def accept_workspace(self, workspace):
        self.accepted_workspaces.append(getattr(workspace, "revision_id", None))
        revision = f"rev_accepted{len(self.accepted):02d}"
        self.accepted.append(revision)
        return revision

    async def run_unit_session(self, context, controller):
        """Stands in for Dev 1: patch, verify, repeat while budget remains."""
        if not self.agent_available:
            raise ports_module.IntegrationUnavailable("Dev 1", "app.agents",
                                                      "run_unit_session")
        self.migrated_order.append(context.unit.unit_id)
        while controller.patch_attempts_remaining() > 0:
            ack = await controller.submit_patch(PatchProposal(
                unit_id=context.unit.unit_id,
                files=[FilePatch(path=p, new_content="x = 1\n")
                       for p in context.unit.file_paths],
                summary="modernise", rationale="types",
            ))
            if not ack.accepted:
                break
            self.attempts[context.unit.unit_id] = \
                self.attempts.get(context.unit.unit_id, 0) + 1
            outcome = await controller.run_verification()
            if outcome.get("ok"):
                if self.agent_mode == "greedy":
                    # The model keeps editing after a pass and never re-verifies.
                    # controller.workspace now points at an UNVERIFIED tree.
                    await controller.submit_patch(PatchProposal(
                        unit_id=context.unit.unit_id,
                        files=[FilePatch(path=p, new_content="y = 2\n")
                               for p in context.unit.file_paths],
                        summary="one more thing", rationale="tidying",
                    ))
                break
        return UnitSessionOutcome(
            ended_because="verified" if controller.is_verified() else "budget_exhausted"
        )

    async def diff(self, run_id, base, revision, paths=None):
        raise ports_module.IntegrationUnavailable("Dev 4", "app.sandbox", "diff")

    async def workspace_path(self, run_id, revision):
        raise ports_module.IntegrationUnavailable("Dev 4", "app.sandbox",
                                                  "workspace_path")

    async def narrate_report(self, evidence):
        raise ports_module.IntegrationUnavailable("Dev 1", "app.agents",
                                                  "narrate_report")


@pytest.fixture
def world(monkeypatch):
    """Install a World. Tests mutate `w` before calling start_run."""
    w = World(make_plan())

    for name in ("get_plan", "save_plan", "save_run", "get_run",
                 "supersede_open_plans", "save_artefact", "list_artefacts",
                 "list_unfinished_runs"):
        monkeypatch.setattr(store_module, name, getattr(w, name))
    for name in ("append_event", "list_events"):
        monkeypatch.setattr(events_module, name, getattr(w, name))
    for name in ("runner_status", "capture_baseline", "create_workspace",
                 "apply_patch", "verify_unit", "verify_final", "accept_workspace",
                 "run_unit_session", "diff", "narrate_report", "workspace_path"):
        monkeypatch.setattr(ports_module, name, getattr(w, name))
    monkeypatch.setattr(ports_module, "snapshot_available", lambda repo_id: True)

    # Reports write to disk; keep the worker's call but make it a no-op here.
    async def no_report(run):
        return None

    monkeypatch.setattr(worker_module, "_generate_report", no_report)
    worker_module._cancelled.discard("run_test000001")
    return w


async def run_to_completion(w):
    """Start a run and await the background task."""
    summary = await worker_module.start_run("plan_test000001")
    task = worker_module._tasks.get(summary.run_id)
    if task is not None:
        await task
    return await store_module.get_run(summary.run_id)


# --------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------

async def test_a_clean_run_completes_with_every_unit_passed(world):
    run = await run_to_completion(world)

    assert run.state == "completed"
    assert run.error is None
    assert run.units_verified == 6
    assert run.units_blocked == 0
    assert {u.status for u in run.units} == {"passed"}


async def test_units_are_migrated_dependency_first(world):
    await run_to_completion(world)
    order = world.migrated_order

    assert order.index("unit_pricing") < order.index("unit_orders")
    assert order.index("unit_storage") < order.index("unit_refunds")
    assert order.index("unit_orders") < order.index("unit_main")
    assert order.index("unit_refunds") < order.index("unit_main")


async def test_the_baseline_is_captured_before_any_unit_is_migrated(world):
    await run_to_completion(world)
    types = world.types()
    assert types.index("baseline_captured") < types.index("patch_proposed")


async def test_each_passing_unit_accepts_a_snapshot(world):
    await run_to_completion(world)
    assert world.types().count("snapshot_accepted") == 6


# --------------------------------------------------------------------------
# T-14: final verification always runs
# --------------------------------------------------------------------------

async def test_final_verification_runs_even_when_everything_passed(world):
    run = await run_to_completion(world)
    states = [e["payload"].get("to") for e in world.events
              if e["event_type"] == "run_state_changed"]
    assert "final_verification" in states
    assert run.state == "completed"


async def test_final_verification_runs_even_when_a_unit_blocked(world):
    world.verdicts = {"unit_pricing": [False, False, False]}
    await run_to_completion(world)
    states = [e["payload"].get("to") for e in world.events
              if e["event_type"] == "run_state_changed"]
    assert "final_verification" in states


async def test_a_failing_final_gate_fails_the_whole_run(world):
    world.final_passes = False
    run = await run_to_completion(world)
    assert run.state == "failed"


async def test_an_inconclusive_final_verification_is_never_completed(world):
    """Verifier unavailable: inconclusive counts as not passed."""
    world.final_passes = None
    run = await run_to_completion(world)
    assert run.state == "completed_with_warnings"
    assert run.state != "completed"


# --------------------------------------------------------------------------
# T-13: gated progression
# --------------------------------------------------------------------------

async def test_a_blocked_unit_skips_its_dependants_and_they_are_never_migrated(world):
    world.verdicts = {"unit_pricing": [False, False, False]}
    run = await run_to_completion(world)

    by_id = {u.unit_id: u.status for u in run.units}
    assert by_id["unit_pricing"] == "blocked"
    assert by_id["unit_orders"] == "skipped"
    assert by_id["unit_refunds"] == "skipped"
    assert by_id["unit_main"] == "skipped"
    # independent leaves still get migrated
    assert by_id["unit_customers"] == "passed"
    assert by_id["unit_storage"] == "passed"

    # the crucial part: the agent was never asked to migrate a skipped unit
    assert "unit_orders" not in world.migrated_order
    assert "unit_main" not in world.migrated_order


async def test_a_run_with_a_blocked_unit_is_completed_with_warnings(world):
    world.verdicts = {"unit_pricing": [False, False, False]}
    run = await run_to_completion(world)
    assert run.state == "completed_with_warnings"
    assert run.units_blocked == 1


async def test_a_blocked_unit_emits_a_reason(world):
    """One block, one `unit_blocked`. Everything downstream is `unit_skipped` (#27).

    All of them used to be `unit_blocked`, so one real block showed as four red rows
    on the Run screen.
    """
    world.verdicts = {"unit_pricing": [False, False, False]}
    await run_to_completion(world)

    blocked = [e for e in world.events if e["event_type"] == "unit_blocked"]
    skipped = [e for e in world.events if e["event_type"] == "unit_skipped"]

    assert [e["unit_id"] for e in blocked] == ["unit_pricing"], (
        "only the unit that was tried and could not be verified is blocked"
    )
    assert all(e["payload"].get("reason") for e in blocked + skipped)

    downstream = [e["payload"]["reason"] for e in skipped
                  if e["unit_id"] == "unit_orders"]
    assert downstream, "the dependant was not journalled as skipped"
    assert "unit_pricing" in downstream[0]


# --------------------------------------------------------------------------
# repair
# --------------------------------------------------------------------------

async def test_a_unit_that_fails_then_passes_is_repaired_not_blocked(world):
    world.verdicts = {"unit_pricing": [False, True]}
    run = await run_to_completion(world)

    by_id = {u.unit_id: u.status for u in run.units}
    assert by_id["unit_pricing"] == "passed"
    assert run.state == "completed"
    assert "repair_started" in world.types("unit_pricing")
    assert world.attempts["unit_pricing"] == 2


async def test_the_budget_is_three_attempts_then_the_unit_blocks(world):
    world.verdicts = {"unit_pricing": [False, False, False, True]}
    run = await run_to_completion(world)

    assert world.attempts["unit_pricing"] == 3
    by_id = {u.unit_id: u.status for u in run.units}
    assert by_id["unit_pricing"] == "blocked"


# --------------------------------------------------------------------------
# failure paths
# --------------------------------------------------------------------------

async def test_no_baseline_means_the_run_fails_rather_than_proceeding(world):
    """Without an oracle nothing downstream can be verified, so do not pretend."""
    world.baseline_available = False
    run = await run_to_completion(world)

    assert run.state == "failed"
    assert "baseline" in (run.error or "")
    assert world.migrated_order == []


async def test_a_baseline_that_captured_nothing_fails_the_run(world, monkeypatch):
    """#96: every case failed to capture, so there is no oracle either. The run used
    to carry on and spend Argon calls on every unit."""
    capture = world.capture_baseline

    async def capture_nothing(run_id, repo_id, units):
        summary = await capture(run_id, repo_id, units)
        return summary.model_copy(update={"cases_captured": 0,
                                          "cases_failed_to_capture": 26})

    monkeypatch.setattr(ports_module, "capture_baseline", capture_nothing)
    run = await run_to_completion(world)

    assert run.state == "failed"
    assert "captured no behaviour cases (26 failed" in (run.error or "")
    assert world.migrated_order == []


async def test_an_unavailable_agent_blocks_its_units_without_crashing_the_run(world):
    world.agent_available = False
    run = await run_to_completion(world)

    assert run.state == "completed_with_warnings"
    assert all(u.status in {"blocked", "skipped"} for u in run.units)
    assert run.units_verified == 0


async def test_a_cancelled_run_stops_and_says_so(world):
    worker_module._cancelled.add("run_test000001")
    try:
        summary = await worker_module.start_run("plan_test000001")
        worker_module._cancelled.add(summary.run_id)
        task = worker_module._tasks.get(summary.run_id)
        if task:
            await task
        run = await store_module.get_run(summary.run_id)
        assert run.state == "cancelled"
    finally:
        worker_module._cancelled.clear()


# --------------------------------------------------------------------------
# the central invariant
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "verdicts",
    [
        {},
        {"unit_pricing": [False, True]},
        {"unit_pricing": [False, False, False]},
        {"unit_orders": [False, False, False]},
        {"unit_main": [False, False, False]},
        {"unit_pricing": [False, False, False], "unit_storage": [False, False, False]},
    ],
)
async def test_no_run_ever_reports_completed_without_everything_passing(
    world, verdicts
):
    world.verdicts = verdicts
    run = await run_to_completion(world)

    if run.state == "completed":
        assert all(u.status == "passed" for u in run.units), (
            "a run claimed `completed` with a unit that did not pass"
        )


@pytest.mark.parametrize(
    "verdicts",
    [
        {},
        {"unit_pricing": [False, True]},
        {"unit_pricing": [False, False, False]},
        {"unit_customers": [False, False, False]},
    ],
)
async def test_no_run_leaves_a_unit_in_a_non_terminal_state(world, verdicts):
    world.verdicts = verdicts
    run = await run_to_completion(world)

    terminal = {"passed", "blocked", "failed", "skipped"}
    stuck = [u.unit_id for u in run.units if u.status not in terminal]
    assert not stuck, f"units left mid-flight: {stuck}"


@pytest.mark.parametrize(
    "verdicts",
    [
        {},
        {"unit_pricing": [False, True]},
        {"unit_pricing": [False, False, False]},
        {"unit_refunds": [False, False, False]},
    ],
)
async def test_no_run_path_raises_an_illegal_transition(world, verdicts):
    """The worker swallows exceptions into run.error, so an illegal transition would
    look like a quietly failed run rather than a crash. Catch it here."""
    world.verdicts = verdicts
    run = await run_to_completion(world)

    assert "IllegalTransition" not in (run.error or ""), run.error
    errors = [e["payload"].get("message", "") for e in world.events
              if e["event_type"] == "error"]
    assert not any("IllegalTransition" in m for m in errors), errors


# --------------------------------------------------------------------------
# regressions found by the adversarial review
# --------------------------------------------------------------------------

async def test_only_the_verified_workspace_is_ever_accepted(world):
    """The model passes verification, then edits again without re-verifying.

    Accepting `controller.workspace` would accept a tree no verifier ever looked
    at - a direct violation of the central invariant.
    """
    world.agent_mode = "greedy"
    run = await run_to_completion(world)

    assert run.state == "completed"
    # Each unit created 2 workspaces (verified, then the greedy one). The accepted
    # workspace must always be the earlier, verified one.
    for accepted in world.accepted_workspaces:
        assert accepted is not None
    assert len(set(world.accepted_workspaces)) == len(world.accepted_workspaces)

    # The greedy patch always creates a workspace numbered one higher; none of
    # those may have been accepted.
    greedy = {f"rev_ws{n:03d}" for n in range(2, world.workspace_counter + 1, 2)}
    assert not (set(world.accepted_workspaces) & greedy), (
        f"accepted an unverified workspace: {world.accepted_workspaces}"
    )


async def test_final_verification_runs_against_the_last_accepted_revision(world):
    """It was previously called with workspace=None, so it saw no tree at all."""
    await run_to_completion(world)
    assert world.final_verified_revision is not None
    assert world.final_verified_revision.startswith("rev_accepted")


async def test_final_verification_uses_the_baseline_when_nothing_was_accepted(world):
    world.verdicts = {uid: [False, False, False] for uid, *_ in UNIT_SPECS}
    await run_to_completion(world)
    assert world.final_verified_revision == "rev_baseline"


async def test_cancelling_mid_run_does_not_crash_the_worker(world):
    """_is_cancelled is called twice in a row; the second must not re-transition."""
    world.cancel_after_baseline = True
    run = await run_to_completion(world)

    assert run.state == "cancelled"
    assert "IllegalTransition" not in (run.error or ""), run.error
    errors = [e["payload"].get("message", "") for e in world.events
              if e["event_type"] == "error"]
    assert not any("IllegalTransition" in m for m in errors), errors


async def test_a_cancelled_run_leaves_no_unit_mid_flight(world):
    world.cancel_after_baseline = True
    run = await run_to_completion(world)

    terminal = {"passed", "blocked", "failed", "skipped"}
    stuck = [u.unit_id for u in run.units if u.status not in terminal]
    assert not stuck, f"cancelled run left units mid-flight: {stuck}"


async def test_the_run_summary_reports_real_test_counts(world):
    """These were hard-coded 0 forever, which made the report's caveats dead code."""
    run = await run_to_completion(world)
    assert run.tests_passed > 0, "tests_passed never left zero"


async def test_repeat_attempts_do_not_inflate_the_run_summary_counts(world):
    """Same inflation bug as the report: a retry must replace, not add."""
    world.verdicts = {"unit_pricing": [False, False, True]}
    run = await run_to_completion(world)

    assert run.tests_passed > 0
    # Six units contribute one differential gate each (4 passing cases), plus the
    # final gate. Three attempts on unit_pricing must replace, not add.
    assert run.tests_passed <= 6 * 4 + 12, (
        f"counts look inflated by retries: {run.tests_passed}"
    )


async def test_attempts_and_accepted_revision_are_recorded_on_each_unit(world):
    world.verdicts = {"unit_pricing": [False, True]}
    run = await run_to_completion(world)

    by_id = {u.unit_id: u for u in run.units}
    assert by_id["unit_pricing"].attempts == 2
    assert by_id["unit_pricing"].accepted_revision is not None
    assert by_id["unit_pricing"].last_verification_id is not None


async def test_a_failure_message_is_redacted_before_it_is_persisted(world, monkeypatch):
    """run.error is served straight out of GET /runs/{id}. The same string is
    redacted on the event path; persisting it raw would leak a connection string."""
    from app.orchestration import events as events_module

    secret = "mongodb+srv://EXAMPLE-NOT-A-REAL-CONNECTION-STRING/axolotl"
    monkeypatch.setattr(events_module, "_secret_values", lambda: [secret])

    async def explode(run_id, repo_id, units):
        raise ports_module.IntegrationUnavailable("Dev 4", "app.verification",
                                                  f"capture_baseline against {secret}")

    monkeypatch.setattr(ports_module, "capture_baseline", explode)

    run = await run_to_completion(world)

    assert run.state == "failed"
    assert secret not in (run.error or ""), "a secret was persisted on the run document"
    assert "[redacted]" in (run.error or "")


async def test_cancelling_journals_every_unit_it_abandons(world):
    """T-15: the audit trail must show the units being abandoned. Writing statuses
    straight onto the run document left the journal claiming they were still running.
    """
    world.cancel_after_baseline = True
    run = await run_to_completion(world)

    journalled = [
        e for e in world.events
        if e["event_type"] == "unit_state_changed" and e["payload"].get("to") == "skipped"
    ]
    on_document = [u for u in run.units if u.status == "skipped"]

    assert on_document, "nothing was abandoned, so this test proves nothing"
    assert len(journalled) == len(on_document), (
        f"{len(on_document)} unit(s) skipped on the document but "
        f"{len(journalled)} in the journal"
    )
    # Abandoned, not failed: `unit_skipped` (#27).
    reasons = [e for e in world.events if e["event_type"] == "unit_skipped"]
    assert any("cancelled" in (e["payload"].get("reason") or "") for e in reasons)
    assert not [e for e in world.events if e["event_type"] == "unit_blocked"], (
        "a cancellation marked a unit as blocked, which reads as a failure"
    )


# --------------------------------------------------------------------------
# final round of review fixes
# --------------------------------------------------------------------------

async def test_a_cancellation_during_final_verification_is_not_dropped(world):
    """The last path where a run could overstate itself: cancel arrives while the
    final check is running, and the verdict reports `completed` anyway."""
    world.cancel_during_final = True
    run = await run_to_completion(world)

    assert run.state == "cancelled"
    assert run.state != "completed"


async def test_last_event_sequence_tracks_the_journal(world):
    """It used to advance only on run_state_changed, so the value the frontend
    polls from sat far behind the events it had already been served."""
    run = await run_to_completion(world)

    assert run.last_event_sequence == len(world.events), (
        f"persisted {run.last_event_sequence} but the journal holds "
        f"{len(world.events)} events"
    )


async def test_last_event_sequence_never_goes_backwards(world):
    world.verdicts = {"unit_pricing": [False, True]}
    run = await run_to_completion(world)
    assert run.last_event_sequence >= len(world.events) - 1


# --------------------------------------------------------------------------
# #47: a failed Argon call must not read as a verification failure
# --------------------------------------------------------------------------

async def test_an_agent_error_is_journalled_and_named_as_the_block_reason(
    world, monkeypatch
):
    """Two live runs on 24 Oct each lost a unit to this.

    `run_unit_session` catches its own exceptions and *returns*
    `ended_because="error"` instead of raising, so the worker's `except` clauses never
    fired, the outcome was discarded, and the unit was blocked with the default "no
    verification passed within the attempt budget". The Run screen showed a
    verification failure for a unit whose model call had returned 429 - no verification
    was ever attempted - and no `error` event existed to say otherwise.
    """
    async def model_call_failed(context, controller):
        return UnitSessionOutcome(
            ended_because="error",
            final_message="429 Too Many Requests from POST /v1beta/interactions",
        )

    monkeypatch.setattr(ports_module, "run_unit_session", model_call_failed)

    run = await run_to_completion(world)

    errors = [e for e in world.events if e["event_type"] == "error"]
    assert errors, "an agent error left no error event, so the UI cannot show one"
    assert any("429" in (e["payload"].get("message") or "") for e in errors)
    assert all(e["payload"].get("code") == "AGENT_SESSION_FAILED" for e in errors)

    blocked = [e["payload"].get("reason") or ""
               for e in world.events if e["event_type"] == "unit_blocked"]
    skipped = [e["payload"].get("reason") or ""
               for e in world.events if e["event_type"] == "unit_skipped"]
    assert blocked, "nothing was blocked, so this test proves nothing"

    # The units whose own session failed name the real cause.
    assert any("429" in r for r in blocked), f"the real cause is missing from {blocked}"
    # Nothing is described as a failed verification, because none was attempted.
    assert not any("no verification passed" in r for r in blocked + skipped), (
        "a failed model call is still being reported as a failed verification"
    )
    # Dependants are *skipped*, for their own accurate reason (#27).
    assert any("dependency" in r for r in skipped)
    assert run.units_verified == 0


async def test_a_model_that_gives_up_is_not_called_a_budget_failure(world, monkeypatch):
    """Each ending explains itself; they used to share one sentence."""
    async def gave_up(context, controller):
        return UnitSessionOutcome(
            ended_because="model_gave_up", final_message="I cannot migrate this safely."
        )

    monkeypatch.setattr(ports_module, "run_unit_session", gave_up)

    await run_to_completion(world)

    blocked = [e for e in world.events if e["event_type"] == "unit_blocked"]
    assert blocked
    reason = blocked[0]["payload"].get("reason") or ""
    assert "stopped without a verified patch" in reason
    assert "cannot migrate this safely" in reason
    # Not an error: giving up is an ordinary ending, and an error event would show as
    # a failure on the Run screen.
    assert not [e for e in world.events if e["event_type"] == "error"]


async def test_a_controller_reason_is_not_overwritten_by_the_generic_one(
    world, monkeypatch
):
    """A rejected patch says why; the session's ending is vaguer than that."""
    async def gave_up_after_setting_a_reason(context, controller):
        controller.block_reason = "patch rejected: pricing.py is outside the unit"
        return UnitSessionOutcome(ended_because="model_gave_up", final_message="done")

    monkeypatch.setattr(ports_module, "run_unit_session",
                        gave_up_after_setting_a_reason)

    await run_to_completion(world)

    blocked = [e for e in world.events if e["event_type"] == "unit_blocked"]
    assert "outside the unit" in (blocked[0]["payload"].get("reason") or "")


async def test_a_verified_session_records_no_error_and_no_block(world, monkeypatch):
    """The ordinary path stays silent: the verifier alone decides success."""
    run = await run_to_completion(world)

    assert run.units_verified == 6
    assert not [e for e in world.events if e["event_type"] == "error"]
    assert not [e for e in world.events if e["event_type"] == "unit_blocked"]


# --------------------------------------------------------------------------
# #16: a run's closing event is journalled before it reads terminal
# --------------------------------------------------------------------------

def kinds_of(world) -> list[str]:
    return [e["event_type"] for e in world.events]


async def test_the_report_is_journalled_before_the_run_reads_completed(
    world, monkeypatch
):
    """§9.3 says a client polls only while the run is non-terminal, so a client that
    does exactly that used to stop before `report_generated` - 19 s before it in
    run_bb0da2e79534 - and never see it.
    """
    async def generate_report(run):
        await world.append_event(run.run_id, "report_generated",
                                 payload={"artefact_ids": []})

    monkeypatch.setattr(worker_module, "_generate_report", generate_report)

    run = await run_to_completion(world)
    kinds = kinds_of(world)

    assert run.state == "completed"
    assert "report_generated" in kinds, "no closing event was recorded"
    # The run document only reads terminal after the report is in the journal, so the
    # terminal state change is the last event of the run.
    assert kinds.index("report_generated") < max(
        i for i, k in enumerate(kinds) if k == "run_state_changed"
    )
    assert kinds[-1] == "run_state_changed"


async def test_a_failed_run_journals_why_before_it_reads_failed(world):
    world.baseline_available = False

    run = await run_to_completion(world)
    kinds = kinds_of(world)

    assert run.state == "failed"
    assert "error" in kinds, "the run failed without saying why"
    assert kinds.index("error") < max(
        i for i, k in enumerate(kinds) if k == "run_state_changed"
    ), "the run reached its terminal state before saying why"


async def test_a_cancelled_run_journals_why_before_it_reads_cancelled(world):
    world.cancel_after_baseline = True

    run = await run_to_completion(world)
    kinds = kinds_of(world)

    assert run.state == "cancelled"
    cancelled = [e for e in world.events
                 if e["event_type"] == "error"
                 and e["payload"].get("code") == "RUN_CANCELLED"]
    assert cancelled, "a cancelled run never said so"
    assert kinds.index("error") < max(
        i for i, k in enumerate(kinds) if k == "run_state_changed"
    )


# --------------------------------------------------------------------------
# #32: attempts is right during the run, not only after it
# --------------------------------------------------------------------------

async def test_attempts_is_written_through_while_a_unit_repairs(world, monkeypatch):
    """The summary said `repairing` with `attempts: 0` for a whole minute in
    run_eb826f4da0d5, because attempts was set only once the session ended."""
    seen: list[tuple[str, int]] = []

    async def watchful_session(context, controller):
        while controller.patch_attempts_remaining() > 0:
            await controller.submit_patch(PatchProposal(
                unit_id=context.unit.unit_id,
                files=[FilePatch(path=p, new_content="x = 1\n")
                       for p in context.unit.file_paths],
                summary="try", rationale="types",
            ))
            await controller.run_verification()
            # Mid-session: what the document says now is what the Run screen shows.
            run = await store_module.get_run(controller.run_id)
            for state in run.units:
                if state.unit_id == context.unit.unit_id:
                    seen.append((state.status, state.attempts))
        return UnitSessionOutcome(ended_because="budget_exhausted")

    monkeypatch.setattr(ports_module, "run_unit_session", watchful_session)
    world.verdicts = {"unit_pricing": [False, False, False]}

    await run_to_completion(world)

    assert seen, "the session never looked at the document"
    assert all(attempts >= 1 for _status, attempts in seen), (
        f"attempts was still 0 after a patch had been applied: {seen}"
    )
    # It counts up rather than sticking at 1.
    assert max(attempts for _status, attempts in seen) > 1


# --------------------------------------------------------------------------
# #17: an interrupted run releases its units and says what happened
# --------------------------------------------------------------------------

async def test_an_interrupted_run_releases_its_units_and_journals_why(world):
    """A bulk update used to flip the run to `failed` and leave its units at
    `migrating` and `queued`, so the Run screen showed a failed run whose graph still
    said "Migrating" and whose event log simply stopped.
    """
    summary = await worker_module.start_run("plan_test000001")
    task = worker_module._tasks.get(summary.run_id)
    if task is not None:
        await task

    # Put it back mid-flight, as a restart would have left it.
    document = world.runs[summary.run_id]
    document["state"] = "migrating"
    document["current_unit"] = "unit_pricing"
    for unit in document["units"]:
        unit["status"] = "migrating" if unit["unit_id"] == "unit_pricing" else "queued"
    world.events.clear()

    marked = await worker_module.mark_interrupted_runs()

    assert marked == [summary.run_id]
    run = await store_module.get_run(summary.run_id)
    assert run.state == "failed"
    assert run.error == store_module.INTERRUPTED
    assert run.current_unit is None
    assert {u.status for u in run.units} == {"skipped"}, (
        "a unit was left mid-flight inside a terminal run"
    )

    kinds = kinds_of(world)
    assert "unit_skipped" in kinds, "nothing in the journal explains the skips"
    assert "unit_blocked" not in kinds, (
        "a restart marked units as blocked, which reads as a verification failure"
    )
    assert any(e["payload"].get("code") == "RUN_INTERRUPTED"
               for e in world.events if e["event_type"] == "error")
    assert kinds.index("error") < max(
        i for i, k in enumerate(kinds) if k == "run_state_changed"
    )


async def test_a_finished_run_is_left_alone_on_startup(world):
    await run_to_completion(world)
    states_before = {rid: doc["state"] for rid, doc in world.runs.items()}

    assert await worker_module.mark_interrupted_runs() == []
    assert {rid: doc["state"] for rid, doc in world.runs.items()} == states_before


# --------------------------------------------------------------------------
# #18: one active run per repository
# --------------------------------------------------------------------------

async def test_a_second_run_on_the_same_repository_is_refused(world):
    """A double click used to start two runs, each with its own Argon session:
    the quota spent twice, and two competing sets of accepted revisions from one
    snapshot."""
    first = await worker_module.start_run("plan_test000001")

    with pytest.raises(AppError) as caught:
        await worker_module.start_run("plan_test000001")

    assert caught.value.status_code == 409
    assert caught.value.code == "RUN_ALREADY_ACTIVE"
    assert caught.value.details["active_run_id"] == first.run_id

    task = worker_module._tasks.get(first.run_id)
    if task is not None:
        await task


async def test_a_run_whose_candidate_image_is_missing_is_refused_before_it_starts(world):
    """#95: only the 3.12 image used to be checked, so a chess run started and every
    unit spent Argon calls on a 3.14 candidate nothing could verify."""
    world.plan.target_id = "python2_to_3"
    world.missing_images = {"3.14"}

    with pytest.raises(AppError) as caught:
        await worker_module.start_run("plan_test000001")

    assert caught.value.status_code == 503
    assert caught.value.code == "RUNNER_UNAVAILABLE"
    assert "Python 3.14" in caught.value.message
    assert world.runs == {}, "a run was created anyway"


async def test_two_concurrent_starts_create_one_run(world, monkeypatch):
    """#99: the check and the save were separated by awaits (the runner check is a
    `docker version` call), so a second click arriving in between passed too."""
    import asyncio

    runner_status = world.runner_status

    async def slow_runner_status(*args):
        await asyncio.sleep(0.01)
        return await runner_status(*args)

    monkeypatch.setattr(ports_module, "runner_status", slow_runner_status)

    results = await asyncio.gather(
        worker_module.start_run("plan_test000001"),
        worker_module.start_run("plan_test000001"),
        return_exceptions=True,
    )

    started = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, AppError)]
    assert len(started) == 1 and len(world.runs) == 1
    assert [r.code for r in refused] == ["RUN_ALREADY_ACTIVE"]

    task = worker_module._tasks.get(started[0].run_id)
    if task is not None:
        await task


async def test_another_plan_on_the_same_repository_is_refused_too(world):
    """Scoped to the repository, not the plan: a second plan migrating the same
    snapshot is the same hazard."""
    first = await worker_module.start_run("plan_test000001")

    # The World serves one plan whatever id is asked for, which is exactly the case
    # here: a different plan id, the same repository.
    with pytest.raises(AppError) as caught:
        await worker_module.start_run("plan_test000002")
    assert caught.value.code == "RUN_ALREADY_ACTIVE"
    assert caught.value.details["repository_id"] == world.plan.repository_id

    task = worker_module._tasks.get(first.run_id)
    if task is not None:
        await task


async def test_a_new_run_is_allowed_once_the_first_has_finished(world):
    """The guard is about *active* runs; a repository can be migrated twice."""
    await run_to_completion(world)

    second = await worker_module.start_run("plan_test000001")
    assert second.run_id
    task = worker_module._tasks.get(second.run_id)
    if task is not None:
        await task


# --------------------------------------------------------------------------
# #27: what the Run screen asked for
# --------------------------------------------------------------------------

async def test_the_run_records_the_attempt_budget_it_ran_under(world):
    """The header says "Attempt 2 of 3" and had no source for the 3."""
    run = await run_to_completion(world)

    from app.config import get_settings

    assert run.max_patch_attempts == get_settings().max_patch_attempts
    assert run.max_patch_attempts >= 1


async def test_a_cancelled_run_gets_a_report_however_early_it_was_cancelled(
    world, monkeypatch
):
    """It used to depend on when the user clicked: cancelling during final
    verification produced a report, cancelling during baselining did not, and the
    Report screen could not explain the difference to someone who did the same thing
    both times.
    """
    reported: list[str] = []

    async def generate_report(run):
        reported.append(run.state)
        await world.append_event(run.run_id, "report_generated",
                                 payload={"artefact_ids": []})

    monkeypatch.setattr(worker_module, "_generate_report", generate_report)
    world.cancel_after_baseline = True

    run = await run_to_completion(world)

    assert run.state == "cancelled"
    assert reported == ["cancelled"], (
        f"a cancelled run produced {len(reported)} report(s), from state {reported}"
    )
    # The report is in the journal before the run reads terminal (#16).
    kinds = kinds_of(world)
    assert kinds.index("report_generated") < max(
        i for i, k in enumerate(kinds) if k == "run_state_changed"
    )


async def test_a_cancelled_run_reports_only_once(world, monkeypatch):
    """`_is_cancelled` is called from more than one place and is safe to repeat."""
    count = {"n": 0}

    async def generate_report(run):
        count["n"] += 1

    monkeypatch.setattr(worker_module, "_generate_report", generate_report)
    world.cancel_during_final = True

    await run_to_completion(world)
    assert count["n"] == 1, f"the report was generated {count['n']} times"


async def test_a_run_is_refused_when_the_code_is_not_on_this_machine(world, monkeypatch):
    """#84: refused before a run exists, so nothing is journalled or spent."""
    monkeypatch.setattr(ports_module, "snapshot_available", lambda repo_id: False)

    with pytest.raises(AppError) as caught:
        await worker_module.start_run("plan_test000001")

    assert caught.value.status_code == 409
    assert caught.value.code == "SNAPSHOT_UNAVAILABLE"
    assert world.runs == {}
