"""Run worker - owner: Dev 2. Spec §6.6, Dev 2 brief "Run worker" and "Run algorithm".

The deterministic heart. It decides what runs next, records what happened, and refuses
to call anything verified that a recorded VerificationResult did not verify.

Work never rides on an HTTP request: `start_run` persists the run, emits `run_created`
and hands off to an asyncio task. If the process dies mid-run, startup marks the run
`failed` with "interrupted by server restart" rather than pretending it can resume.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.errors import PLAN_NOT_APPROVED, RUN_ALREADY_FINISHED, AppError
from app.models.contracts import (
    MigrationPlan,
    MigrationUnit,
    RunState,
    RunSummary,
    UnitRunState,
    UnitStatus,
)
from app.orchestration import events, ports, store
from app.orchestration.controller import REVISION_BASELINE, UnitControllerImpl
from app.orchestration.ids import new_run_id
from app.orchestration.scheduler import eligible_units, impacted_units, units_to_skip
from app.orchestration.states import (
    TERMINAL_RUN_STATES,
    TERMINAL_UNIT_STATUSES,
    final_verdict,
    transition_run,
    transition_unit,
)

log = logging.getLogger(__name__)

# Hold references so tasks are not garbage collected mid-run.
_tasks: dict[str, asyncio.Task] = {}
_cancelled: set[str] = set()
# run_id -> {(unit_id, gate): (passed, failed, inconclusive)}; cleared when a run ends.
_GATE_TOTALS: dict[str, dict[tuple[str | None, str], tuple[int, int, int]]] = {}


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------

async def start_run(plan_id: str) -> RunSummary:
    """Create a run from an approved plan and start it in the background."""
    settings = get_settings()
    plan = await store.get_plan(plan_id)

    if plan.status != "approved":
        raise AppError(
            PLAN_NOT_APPROVED,
            f"Plan {plan_id} is {plan.status}; approve it before starting a run",
            status_code=409,
            details={"plan_id": plan_id, "status": plan.status},
        )

    runner = await _runner_mode()
    run = RunSummary(
        run_id=new_run_id(),
        plan_id=plan.plan_id,
        repository_id=plan.repository_id,
        state="queued",
        current_unit=None,
        units=[
            UnitRunState(unit_id=u.unit_id, module_ids=u.module_ids, status="queued")
            for u in plan.units
        ],
        units_total=len(plan.units),
        units_verified=0,
        units_blocked=0,
        tests_passed=0,
        tests_failed=0,
        tests_inconclusive=0,
        last_event_sequence=0,
        model=settings.gemini_model,
        runner_mode=runner,
        created_at=store.utcnow(),
        updated_at=store.utcnow(),
    )
    await _save(run)

    await events.append_event(
        run.run_id, "run_created",
        payload={"plan_id": plan.plan_id, "target_id": plan.target_id},
    )
    await _save(run)

    _tasks[run.run_id] = asyncio.create_task(_execute(run.run_id, plan))
    return run


async def cancel_run(run_id: str) -> RunSummary:
    """Set the cancel flag. The worker checks it between steps."""
    run = await store.get_run(run_id)
    if run.state in {"completed", "completed_with_warnings", "failed", "cancelled"}:
        raise AppError(
            RUN_ALREADY_FINISHED,
            f"Run {run_id} already finished as {run.state}",
            status_code=409,
            details={"run_id": run_id, "state": run.state},
        )
    _cancelled.add(run_id)
    log.info("cancellation requested for %s", run_id)
    return run


async def mark_interrupted_runs() -> list[str]:
    """Called from the app lifespan. Honest, not resumed."""
    return await store.mark_interrupted_runs()


async def _runner_mode() -> str:
    try:
        status = await ports.runner_status()
        return str(status.get("mode", "disabled"))
    except ports.IntegrationUnavailable:
        return "disabled"


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

async def _save(run: RunSummary) -> None:
    """Persist the run, keeping `last_event_sequence` level with the journal.

    It used to advance only on `run_state_changed`, so the value the frontend polls
    from sat far behind the events it had already been served.
    """
    run.last_event_sequence = max(
        run.last_event_sequence, events.last_sequence(run.run_id)
    )
    await store.save_run(run)


async def _finish(run: RunSummary, verdict: RunState) -> None:
    """End the run with its closing event already in the journal (#16).

    Spec §9.3 says a client polls only while the run is non-terminal, so a client that
    does exactly that used to stop before `report_generated` - 19 seconds before it,
    in `run_bb0da2e79534` - and never see it. For those seconds the report endpoint
    also had no report for a run that already read as finished.

    The verdict is validated and set in memory first, so the report records the state
    it is the report *of*; the document is only written, and so only reads terminal to
    anyone polling, once `report_generated` or `error` is in the journal.
    `_generate_report` always appends one or the other and never raises, so this
    cannot leave a run un-transitioned.
    """
    previous = run.state
    run.state = transition_run(run.run_id, run.state, verdict)
    await _generate_report(run)
    await events.append_event(
        run.run_id, "run_state_changed", payload={"from": previous, "to": verdict}
    )
    await _save(run)


async def _transition(run: RunSummary, target: RunState) -> None:
    previous = run.state
    run.state = transition_run(run.run_id, run.state, target)
    await events.append_event(
        run.run_id, "run_state_changed", payload={"from": previous, "to": target}
    )
    await _save(run)


async def _set_unit(run: RunSummary, unit_id: str, target: UnitStatus) -> None:
    for state in run.units:
        if state.unit_id != unit_id:
            continue
        if state.status == target:
            return
        previous = state.status
        state.status = transition_unit(unit_id, state.status, target)
        await _save(run)
        await events.append_event(
            run.run_id, "unit_state_changed", unit_id=unit_id,
            payload={"from": previous, "to": target},
        )
        return


def _statuses(run: RunSummary) -> dict[str, UnitStatus]:
    return {state.unit_id: state.status for state in run.units}


async def _execute(run_id: str, plan: MigrationPlan) -> None:
    """The run algorithm. Every exit path records why."""
    by_id = {u.unit_id: u for u in plan.units}
    run = None

    try:
        # Inside the try: a failed read must still clear the task handle rather
        # than leaving the run `queued` forever with a leaked reference.
        run = await store.get_run(run_id)
        # --- baseline -----------------------------------------------------
        await _transition(run, "baselining")
        if await _is_cancelled(run):
            return

        try:
            summary = await ports.capture_baseline(run_id, plan.repository_id, plan.units)
            await events.append_event(
                run_id, "baseline_captured",
                payload={
                    "snapshot_hash": summary.snapshot_hash,
                    "cases_captured": summary.cases_captured,
                    "cases_failed_to_capture": summary.cases_failed_to_capture,
                    "environment": summary.environment,
                },
            )
        except ports.IntegrationUnavailable as exc:
            # No baseline means no oracle, so nothing downstream can be verified.
            await _fail(run, f"cannot capture a baseline: {exc}")
            return

        # --- per unit, dependency first ------------------------------------
        accepted_revision = await _migrate_units(run, plan, by_id)
        if await _is_cancelled(run):
            return

        # --- final verification (always, T-14) ------------------------------
        await _transition(run, "final_verification")
        final_passed = await _final_verification(run, accepted_revision)

        # A cancellation arriving DURING final verification must not be dropped.
        # Without this check the verdict below could report `completed` for a run
        # the user had already stopped - the last path where a run could overstate
        # itself.
        if await _is_cancelled(run):
            await _generate_report(run)
            return

        await _finish(run, final_verdict(_statuses(run), final_passed))

    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a crashed run must still be recorded
        log.exception("run %s crashed", run_id)
        if run is not None and run.state not in TERMINAL_RUN_STATES:
            await _fail(run, f"{type(exc).__name__}: {exc}")
    finally:
        _tasks.pop(run_id, None)
        _cancelled.discard(run_id)
        _GATE_TOTALS.pop(run_id, None)
        events.forget_run(run_id)


async def _migrate_units(
    run: RunSummary, plan: MigrationPlan, by_id: dict[str, MigrationUnit]
) -> str:
    """Walk eligible units in the plan's order until nothing is left to do.

    Returns the last accepted revision, which final verification runs against.
    """
    settings = get_settings()
    accepted_revision = REVISION_BASELINE

    while True:
        if await _is_cancelled(run):
            return accepted_revision

        # Anything downstream of a blocked unit is skipped, never migrated (T-13).
        for unit_id, reason in units_to_skip(plan.units, _statuses(run)).items():
            await _set_unit(run, unit_id, "skipped")
            await events.append_event(
                run.run_id, "unit_blocked", unit_id=unit_id,
                payload={"reason": reason, "verification_id": None},
            )

        ready = eligible_units(plan.units, _statuses(run))
        if not ready:
            return accepted_revision

        # Argon's order where it is valid, deterministic order otherwise.
        unit_id = next((u for u in plan.order if u in ready), ready[0])
        unit = by_id[unit_id]

        run.current_unit = unit_id
        await _save(run)
        await _set_unit(run, unit_id, "migrating")
        if run.state != "migrating":
            await _transition(run, "migrating")

        impacted = [by_id[i] for i in impacted_units(unit_id, plan.units) if i in by_id]

        controller = UnitControllerImpl(
            run.run_id, unit, impacted,
            repository_id=run.repository_id,
            base_revision=accepted_revision,
            max_patch_attempts=settings.max_patch_attempts,
        )

        async def write_status(
            target: UnitStatus,
            _unit_id: str = unit_id,
            _controller: UnitControllerImpl = controller,
        ) -> None:
            """Mirror the controller's status and attempt count onto the run document.

            The controller has already validated the transition, so this writes
            through rather than re-validating. Without it the run document would
            never see `verifying`, and the worker's own `migrating -> passed` would
            be rejected by the state machine.

            `attempts` is written here too (#32). It used to be set only after the
            session ended, so the summary said `repairing` with `attempts: 0` for the
            whole of a repair - a minute of it for `storage.py` in `run_eb826f4da0d5`.

            Both the unit id and the controller are bound as defaults rather than
            captured: this runs inside the loop over units, so a closure would read
            whichever unit the loop had reached by the time it fired.
            """
            for state in run.units:
                if state.unit_id == _unit_id:
                    state.status = target
                    state.attempts = _controller.attempts_used
                    break
            await _save(run)

        controller.set_status_callback(write_status)

        await _run_unit_session(run, plan, unit, controller)

        for state in run.units:
            if state.unit_id == unit_id:
                state.attempts = controller.attempts_used
                state.last_verification_id = (
                    controller.latest_verification.verification_id
                    if controller.latest_verification else None
                )

        if controller.latest_verification is not None:
            _count_gates(run, unit_id, controller.latest_verification)

        if controller.is_verified():
            # Accept first. Only once a revision is actually accepted does the unit
            # become `passed` - otherwise a failed acceptance would leave a unit
            # terminally passed with nothing behind it.
            try:
                # The workspace the PASSING verification was produced from, not
                # whatever the model submitted most recently.
                accepted_revision = await ports.accept_workspace(
                    controller.verified_workspace
                )
            except ports.IntegrationUnavailable as exc:
                await _block(run, unit_id, f"could not accept the workspace: {exc}")
                run.current_unit = None
                await _save(run)
                continue

            await events.append_event(
                run.run_id, "snapshot_accepted", unit_id=unit_id,
                payload={"revision": accepted_revision},
            )
            await _set_unit(run, unit_id, "passed")
            for state in run.units:
                if state.unit_id == unit_id:
                    state.accepted_revision = accepted_revision
            run.units_verified += 1
            await _save(run)
        else:
            await _block(
                run, unit_id,
                controller.block_reason or "no verification passed within the attempt budget",
            )

        run.current_unit = None
        await _save(run)


async def _run_unit_session(run, plan, unit, controller) -> None:
    """Hand control to Dev 1's agent. Its outcome is advisory; the verifier decides.

    Advisory about *success* - the verifier alone decides that. But the outcome is the
    only record of **why a session ended early**, so it is not optional.

    Discarding it cost two live runs a unit each on 24 Oct (#47).
    `run_unit_session` catches its own exceptions and *returns* `ended_because="error"`
    rather than raising, so neither `except` below fires, `block_reason` stayed unset,
    and `_block` fell back to "no verification passed within the attempt budget". The
    Run screen then showed a verification failure for a unit whose model call had
    actually returned 429, with no verification ever attempted and no `error` event to
    say so. The real reason for the second run is simply lost.
    """
    from app.agents.contexts import UnitSessionContext
    from app.api.targets import require_supported_target

    settings = get_settings()
    context = UnitSessionContext(
        run_id=run.run_id,
        repository_id=plan.repository_id,
        unit=unit,
        target=require_supported_target(plan.target_id),
        plan_notes=plan.rationale[:2000],
        previous_interaction_id=plan.interaction_id,
        max_patch_attempts=settings.max_patch_attempts,
        max_tool_calls=settings.max_tool_calls_per_unit,
    )
    try:
        outcome = await ports.run_unit_session(context, controller)
    except ports.IntegrationUnavailable as exc:
        controller.block_reason = str(exc)
        await events.append_event(
            run.run_id, "error", unit_id=unit.unit_id,
            payload={"code": "AGENT_UNAVAILABLE", "message": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001 - an agent crash blocks its unit, not the run
        controller.block_reason = f"agent session failed: {type(exc).__name__}: {exc}"
        log.exception("agent session failed for %s", unit.unit_id)
        await events.append_event(
            run.run_id, "error", unit_id=unit.unit_id,
            payload={"code": "AGENT_SESSION_FAILED", "message": str(exc)[:500]},
        )
    else:
        await _record_session_outcome(run, unit.unit_id, outcome, controller)


async def _record_session_outcome(run, unit_id: str, outcome, controller) -> None:
    """Explain an early end, whatever ended it.

    `ended_because` is one of `verified`, `budget_exhausted`, `model_gave_up`, `error`.
    Only `error` gets an `error` event: Dev 5's Run screen renders that type already and
    asked for exactly this. The others are ordinary endings, so they travel in the block
    reason instead of inventing an event type no screen matches on yet.

    A reason the controller has already set is more specific than anything here - a
    rejected patch, say - so it is never overwritten.
    """
    ended = getattr(outcome, "ended_because", None)
    message = (getattr(outcome, "final_message", "") or "").strip()

    if ended == "error":
        await events.append_event(
            run.run_id, "error", unit_id=unit_id,
            payload={"code": "AGENT_SESSION_FAILED",
                     "message": message[:500] or "the agent session ended in an error"},
        )

    if ended in (None, "verified") or controller.block_reason:
        return

    controller.block_reason = _SESSION_END_REASONS.get(ended, ended) + (
        f": {message}" if message else ""
    )


# What to tell the user for each non-verified ending. "no verification passed within
# the attempt budget" was being said for all of them, including a 429.
_SESSION_END_REASONS = {
    "error": "the agent session ended in an error",
    "budget_exhausted": "the agent ran out of its attempt budget before verifying",
    "model_gave_up": "the agent stopped without a verified patch",
}


async def _final_verification(
    run: RunSummary, accepted_revision: str
) -> bool | None:
    """Always run (T-14). None means inconclusive, which is never a pass.

    Runs against a workspace at the **last accepted revision** - the whole point is
    to check the units together, so it must see the real final tree.
    """
    try:
        workspace = await ports.create_workspace(
            run.run_id, run.repository_id, accepted_revision
        )
        result = await ports.verify_final(run.run_id, workspace)
    except ports.IntegrationUnavailable as exc:
        await events.append_event(
            run.run_id, "error",
            payload={"code": "FINAL_VERIFICATION_UNAVAILABLE", "message": str(exc)},
        )
        return None

    for gate in result.gates:
        await events.append_event(
            run.run_id, "gate_result", payload=gate.model_dump()
        )

    # Final-verification counterexamples were never journalled, so the report
    # undercounted them.
    for counterexample_id in result.counterexample_ids:
        await events.append_event(
            run.run_id, "counterexample_recorded",
            payload={"counterexample_id": counterexample_id, "scope": "final"},
        )

    passed = bool(result.all_required_gates_passed)
    # unit_id stays None so the report can tell this apart from a unit's
    # verification - otherwise final_verification_id picks up the last UNIT result.
    await events.append_event(
        run.run_id,
        "verification_passed" if passed else "verification_failed",
        payload={
            "verification_id": result.verification_id,
            "scope": "final",
            "failed_case_ids": [],
            "counterexample_ids": result.counterexample_ids,
        },
    )

    _count_gates(run, None, result)
    await _save(run)
    return passed


def _count_gates(run: RunSummary, unit_id: str | None, result) -> None:
    """Keep RunSummary's test counters current from recorded gate results.

    Stored per (unit_id, gate) and re-summed, so a repair attempt replaces that
    unit's previous numbers instead of adding to them - the same reason the report
    takes the last gate_result per key.
    """
    for gate in result.gates:
        _GATE_TOTALS.setdefault(run.run_id, {})[(unit_id, gate.gate)] = (
            gate.passed, gate.failed, gate.inconclusive
        )
    totals = _GATE_TOTALS.get(run.run_id, {}).values()
    run.tests_passed = sum(t[0] for t in totals)
    run.tests_failed = sum(t[1] for t in totals)
    run.tests_inconclusive = sum(t[2] for t in totals)


async def _generate_report(run: RunSummary) -> None:
    from app.reports.builder import build_report

    try:
        report = await build_report(run)
        await events.append_event(
            run.run_id, "report_generated", payload={"artefact_ids": report.artefact_ids}
        )
    except Exception as exc:  # noqa: BLE001 - a missing report must not rewrite the verdict
        log.exception("report generation failed for %s", run.run_id)
        await events.append_event(
            run.run_id, "error",
            payload={"code": "REPORT_FAILED", "message": str(exc)[:500]},
        )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

async def _block(run: RunSummary, unit_id: str, reason: str) -> None:
    await _set_unit(run, unit_id, "blocked")
    run.units_blocked += 1
    await _save(run)
    await events.append_event(
        run.run_id, "unit_blocked", unit_id=unit_id,
        payload={"reason": reason, "verification_id": None},
    )


async def _fail(run: RunSummary, error: str) -> None:
    # The same string goes to the event journal, where it is redacted and capped.
    # Persisting it raw on the run document would serve a connection string
    # straight out of GET /runs/{id} - the API is a read path like any other.
    run.error = events.truncate(events.redact(error))
    await _save(run)
    # The error event before the terminal transition (#16): a client that polls only
    # while the run is non-terminal, as §9.3 says, would otherwise stop without ever
    # seeing why the run failed.
    await events.append_event(
        run.run_id, "error", payload={"code": "RUN_FAILED", "message": error}
    )
    await _transition(run, "failed")


async def _is_cancelled(run: RunSummary) -> bool:
    """True once the run has been cancelled. Safe to call repeatedly.

    `_execute` and `_migrate_units` both check this, so without the terminal guard
    the second call would attempt cancelled -> cancelled and raise inside the
    handler that is meant to be recording the cancellation.
    """
    if run.run_id not in _cancelled:
        return False
    if run.state == "cancelled":
        return True

    # Nothing further will be attempted, so do not leave units mid-flight inside a
    # terminal run. Routed through _set_unit so each change is validated and lands
    # in the journal - writing straight onto the document would leave the audit
    # trail (T-15) claiming those units were still running.
    for state in list(run.units):
        if state.status not in TERMINAL_UNIT_STATUSES:
            await _set_unit(run, state.unit_id, "skipped")
            await events.append_event(
                run.run_id, "unit_blocked", unit_id=state.unit_id,
                payload={"reason": "run cancelled before this unit finished",
                         "verification_id": None},
            )

    # The closing event before the terminal transition (#16), as in `_fail`.
    await events.append_event(
        run.run_id, "error",
        payload={"code": "RUN_CANCELLED", "message": "cancelled by request"},
    )
    await _transition(run, "cancelled")
    return True
