"""Verification - owner: Dev 4. SHARED_CONTRACTS §8, Spec §7.3.

**This module is the only source of pass/fail in Axolotl.** Dev 2 records what it
says; Dev 5 displays it; Dev 1's model never sets it.

The differential compares a candidate tree against outcomes recorded from the
*immutable baseline*, not against checked-in expectations - so there is nothing to
quietly edit when a migration fails.

Three rules hold everywhere below:

  - **Inconclusive is never a pass.** A timeout, a harness error or an unavailable
    runner produces `inconclusive`, and `all_required_gates_passed` is False.
  - **No host execution, ever.** If Docker is unavailable, gates are inconclusive.
    Repository code is never run through a host subprocess as a fallback.
  - `all_required_gates_passed` is computed here and nowhere else.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.models.contracts import (
    BaselineSummary,
    BehaviourCase,
    CaseComparison,
    CaseOutcome,
    Counterexample,
    GateResult,
    MigrationUnit,
    VerificationResult,
    Workspace,
)
from app.sandbox import runner, runtimes, workspaces
from app.sandbox.runtimes import Runtime, RuntimePair
from app.verification import comparison, store
from app.verification.cases import (
    collect_cases,
    count_visible_cases,
    load_cases,
    mark_exposed,
    persist_cases,
    write_cases_file,
)

log = logging.getLogger(__name__)

__all__ = [
    "capture_baseline",
    "collect_cases",
    "count_visible_cases",
    "expose_case",
    "get_counterexamples",
    "list_verifications",
    "load_cases",
    "persist_cases",
    "verify_final",
    "verify_unit",
]

# Already implemented in `store`; re-exported so `ports` can resolve it on the package
# like every other Dev 4 call, rather than reaching into a submodule (#45).
list_verifications = store.list_verifications

RUNNER_UNAVAILABLE = (
    "the sandbox runner is unavailable, so nothing could be executed; "
    "this is inconclusive, not a pass"
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(6)


def _run_cases_dir(run_id: str) -> Path:
    return workspaces.workspace_root() / "runs" / run_id / "cases"


async def runtimes_for_run(run_id: str) -> RuntimePair:
    """#22: the run's migration target decides which interpreter runs each side.

    `python2_to_3` records the baseline on 2.7 and checks candidates on 3.14. A run
    with no plan on record keeps the original 3.12 pair; an unknown target raises
    rather than verifying on a guessed interpreter.
    """
    from app.persistence import db

    database = await db.get_db()
    run = await database["migration_runs"].find_one(
        {"run_id": run_id}, {"_id": 0, "plan_id": 1}
    )
    plan = None
    if run:
        plan = await database["migration_plans"].find_one(
            {"plan_id": run["plan_id"]}, {"_id": 0, "target_id": 1}
        )
    return runtimes.for_target(plan["target_id"] if plan else None)


async def _environment(runtime: Runtime, status: dict) -> dict[str, str]:
    """What actually ran: the probed interpreter version, never the intended one."""
    digest = str(status.get("image_digest") or "")
    actual = await runner.probe_python(runtime, digest or None)
    return {
        "python": actual or f"unknown (image built for {runtime.python})",
        "image": runtime.image,
        "image_digest": digest,
        "runner_mode": str(status["mode"]),
    }


# --------------------------------------------------------------------------
# baseline
# --------------------------------------------------------------------------

async def capture_baseline(
    run_id: str, repo_id: str, units: list[MigrationUnit]
) -> BaselineSummary:
    """Run every case against the immutable baseline, once per run.

    This is the oracle. Without it nothing downstream can be verified, so a failure
    here fails the run rather than letting it proceed on nothing.
    """
    from app.ingestion import snapshot_path

    pair = await runtimes_for_run(run_id)
    status = await runner.runner_status(pair.baseline)
    if not status["available"]:
        raise RuntimeError(f"cannot capture a baseline: {status.get('detail')}")

    snapshot = snapshot_path(repo_id)
    cases = await load_cases(repo_id, include_held_out=True)
    if not cases:
        raise RuntimeError(
            "no behaviour cases were found for this repository; there is nothing "
            "to verify against"
        )

    cases_file = write_cases_file(
        [c.case_id for c in cases], snapshot, project_root(),
        _run_cases_dir(run_id) / "baseline.json",
        fixture_id=await fixture_for_repository(repo_id),
    )

    raw, result = await runner.run_behaviour_cases(
        snapshot, cases_file, runtime=pair.baseline
    )
    outcomes = _outcomes_by_case(raw, cases)

    captured = [o for o in outcomes.values() if o.status in {"ok", "raised"}]
    failed = [o for o in outcomes.values() if o.status not in {"ok", "raised"}]
    missing = [c.case_id for c in cases if c.case_id not in outcomes]

    await store.save_baseline_outcomes(run_id, list(outcomes.values()))

    tests = await runner.run_pytest(snapshot, runtime=pair.baseline)
    existing = GateResult(
        gate="existing_tests",
        status="passed" if tests.exit_code == 0 else "failed",
        summary=_pytest_summary(tests),
        log_excerpt=tests.log_excerpt,
    )

    log.info(
        "baseline for %s: %d captured, %d failed, %d missing",
        run_id, len(captured), len(failed), len(missing),
    )

    return BaselineSummary(
        run_id=run_id,
        snapshot_hash=(await _snapshot_hash(repo_id)),
        unit_ids=[u.unit_id for u in units],
        cases_captured=len(captured),
        # Never silently dropped: a case we could not capture is one we cannot
        # verify later, and the report has to be able to say so.
        cases_failed_to_capture=len(failed) + len(missing),
        existing_tests=existing,
        environment={
            **(await _environment(pair.baseline, status)),
            "snapshot": str(snapshot),
            "candidate_python": pair.candidate.python,
        },
    )


async def _snapshot_hash(repo_id: str) -> str:
    from app.persistence import graphstore

    record = await graphstore.get_repository(repo_id)
    return record.snapshot_hash


def _outcomes_by_case(
    raw: list[dict], cases: list[BehaviourCase]
) -> dict[str, CaseOutcome]:
    known = {c.case_id for c in cases}
    outcomes: dict[str, CaseOutcome] = {}
    for index, payload in enumerate(raw):
        case_id = payload.get("case_id")
        if case_id is None and index < len(cases):
            case_id = cases[index].case_id
        if case_id in known:
            outcomes[case_id] = comparison.outcome_from_harness(payload, case_id)
    return outcomes


def _pytest_summary(result: runner.RunResult) -> str:
    for line in reversed(result.stdout.splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            return line.strip()[:200]
    return "no pytest summary line" if result.exit_code else "tests passed"


# --------------------------------------------------------------------------
# unit verification
# --------------------------------------------------------------------------

async def verify_unit(
    run_id: str,
    unit: MigrationUnit,
    workspace: Workspace,
    impacted: list[MigrationUnit],
    attempt: int,
) -> VerificationResult:
    """The five gates, in order. Spec §7.3."""
    started = datetime.now(UTC)
    verification_id = _new_id("ver_")
    candidate = Path(workspace.root)

    runtime = (await runtimes_for_run(run_id)).candidate
    status = await runner.runner_status(runtime)
    if not status["available"]:
        return await _all_inconclusive(
            verification_id, run_id, unit.unit_id, attempt, workspace,
            started, status.get("detail") or RUNNER_UNAVAILABLE,
        )
    environment = await _environment(runtime, status)

    gates: list[GateResult] = []
    policy_clean = True

    # 1. compile -------------------------------------------------------
    compiled = await runner.run_compileall(candidate, runtime=runtime)
    policy_clean &= compiled.policy_clean
    gates.append(GateResult(
        gate="compile",
        status=_status_from(compiled),
        summary="candidate tree compiles" if compiled.exit_code == 0
                else "the candidate tree does not compile",
        log_excerpt=compiled.log_excerpt,
    ))

    # A tree that will not compile cannot be meaningfully executed, so the
    # remaining gates are skipped rather than reported as failures of their own.
    if compiled.exit_code != 0:
        gates.extend(_skipped(["differential", "impacted_tests", "existing_tests"],
                              "skipped: the candidate tree does not compile"))
        gates.append(_policy_gate(policy_clean))
        return await _persist(
            verification_id, run_id, unit.unit_id, attempt, workspace,
            gates, [], [], started, status["mode"], environment,
        )

    baseline = await store.get_baseline_outcomes(run_id)
    repo_id = await repository_for_run(run_id)

    # 2. differential --------------------------------------------------
    unit_cases = await load_cases(repo_id, unit.unit_id, include_held_out=True)
    differential, diff_result = await _differential(
        run_id, candidate, unit_cases, baseline, "differential", runtime
    )
    if diff_result:
        policy_clean &= diff_result.policy_clean
    gates.append(_comparison_gate("differential", differential, diff_result))

    # 3. impacted tests ------------------------------------------------
    impacted_cases: list[BehaviourCase] = []
    for other in impacted:
        impacted_cases.extend(
            await load_cases(repo_id, other.unit_id, include_held_out=True)
        )

    if impacted_cases:
        impacted_comparisons, impacted_result = await _differential(
            run_id, candidate, impacted_cases, baseline, "impacted", runtime
        )
        if impacted_result:
            policy_clean &= impacted_result.policy_clean
        gates.append(
            _comparison_gate("impacted_tests", impacted_comparisons, impacted_result)
        )
    else:
        impacted_comparisons = []
        gates.append(GateResult(
            gate="impacted_tests", status="skipped", required=False,
            summary="no reverse dependants have behaviour cases",
        ))

    # 4. existing tests ------------------------------------------------
    tests = await runner.run_pytest(candidate, runtime=runtime)
    policy_clean &= tests.policy_clean
    gates.append(GateResult(
        gate="existing_tests",
        status=_status_from(tests),
        summary=_pytest_summary(tests),
        log_excerpt=tests.log_excerpt,
    ))

    # 5. sandbox policy ------------------------------------------------
    gates.append(_policy_gate(policy_clean))

    all_comparisons = differential + impacted_comparisons
    counterexamples = _counterexamples(
        run_id, unit.unit_id, attempt, all_comparisons, unit_cases + impacted_cases
    )

    return await _persist(
        verification_id, run_id, unit.unit_id, attempt, workspace,
        gates, all_comparisons, counterexamples, started, status["mode"], environment,
    )


async def repository_for_run(run_id: str) -> str:
    """The repository a run belongs to.

    Cases are keyed by repository but the §8 `verify_unit` signature carries only
    the run, so this reads it back from the run document. Deliberately a lookup
    rather than a process-global cache: a cache would be empty after a restart and
    silently return no cases, which would read as "nothing to verify" - the exact
    kind of quiet wrong answer this project exists to avoid.
    """
    from app.persistence import db

    database = await db.get_db()
    document = await database["migration_runs"].find_one(
        {"run_id": run_id}, {"_id": 0, "repository_id": 1}
    )
    return str(document["repository_id"]) if document else ""


async def fixture_for_repository(repo_id: str) -> str | None:
    """Use the import's trusted identity even after candidate source changes."""
    from app.persistence import graphstore

    repository = await graphstore.get_repository(repo_id)
    return repository.demo_fixture_id if repository.executable else None


# --------------------------------------------------------------------------
# final verification
# --------------------------------------------------------------------------

async def verify_final(run_id: str, workspace: Workspace | None) -> VerificationResult:
    """T-14: the whole project, on the final accepted tree. Always runs."""
    started = datetime.now(UTC)
    verification_id = _new_id("ver_")

    runtime = (await runtimes_for_run(run_id)).candidate
    status = await runner.runner_status(runtime)
    if workspace is None or not status["available"]:
        detail = (
            "no accepted revision to verify" if workspace is None
            else (status.get("detail") or RUNNER_UNAVAILABLE)
        )
        return await _all_inconclusive(
            verification_id, run_id, None, 1, workspace, started, detail,
            gate_names=["final_integration", "sandbox_policy"],
        )

    candidate = Path(workspace.root)
    environment = await _environment(runtime, status)
    repo_id = await repository_for_run(run_id)
    all_cases = await load_cases(repo_id, include_held_out=True)
    baseline = await store.get_baseline_outcomes(run_id)

    comparisons, result = await _differential(
        run_id, candidate, all_cases, baseline, "final", runtime
    )
    tests = await runner.run_pytest(candidate, runtime=runtime)

    counts = comparison.summarise(comparisons)
    passed = (
        counts["mismatch"] == 0
        and counts["inconclusive"] == 0
        and tests.exit_code == 0
    )

    gates = [
        GateResult(
            gate="final_integration",
            status="passed" if passed else "failed",
            summary=(
                f"{counts['match']} case(s) agree, {counts['mismatch']} differ, "
                f"{counts['inconclusive']} inconclusive; {_pytest_summary(tests)}"
            ),
            passed=counts["match"],
            failed=counts["mismatch"],
            inconclusive=counts["inconclusive"],
            log_excerpt=tests.log_excerpt,
        ),
        _policy_gate(
            (result.policy_clean if result else True) and tests.policy_clean
        ),
    ]

    counterexamples = _counterexamples(run_id, None, 1, comparisons, all_cases)
    return await _persist(
        verification_id, run_id, None, 1, workspace,
        gates, comparisons, counterexamples, started, status["mode"], environment,
    )


# --------------------------------------------------------------------------
# shared machinery
# --------------------------------------------------------------------------

async def _differential(
    run_id: str,
    candidate: Path,
    cases: list[BehaviourCase],
    baseline: dict[str, CaseOutcome],
    label: str,
    runtime: Runtime | None = None,
) -> tuple[list[CaseComparison], runner.RunResult | None]:
    if not cases:
        return [], None

    cases_file = write_cases_file(
        [c.case_id for c in cases], candidate, project_root(),
        _run_cases_dir(run_id) / f"{label}-{secrets.token_hex(4)}.json",
        fixture_id=await fixture_for_repository(await repository_for_run(run_id)),
    )
    raw, result = await runner.run_behaviour_cases(candidate, cases_file, runtime=runtime)
    new_outcomes = _outcomes_by_case(raw, cases)

    comparisons: list[CaseComparison] = []
    for case in cases:
        old = baseline.get(case.case_id)
        new = new_outcomes.get(case.case_id)
        if old is None or new is None:
            # One side missing means we cannot say the behaviour matched.
            comparisons.append(comparison.compare(
                case.case_id, case.visibility,
                old or CaseOutcome(case_id=case.case_id, status="harness_error"),
                new or CaseOutcome(case_id=case.case_id, status="harness_error"),
            ))
            continue
        comparisons.append(comparison.compare(case.case_id, case.visibility, old, new))

    return comparisons, result


def _comparison_gate(
    name: str, comparisons: list[CaseComparison], result: runner.RunResult | None
) -> GateResult:
    counts = comparison.summarise(comparisons)
    if counts["mismatch"]:
        status = "failed"
    elif counts["inconclusive"]:
        status = "inconclusive"
    elif not comparisons:
        status = "skipped"
    else:
        status = "passed"

    return GateResult(
        gate=name,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        # Only an empty comparison gate is optional. Held-out cases still count;
        # compile, existing tests, sandbox policy and any impacted cases still apply.
        required=bool(comparisons),
        summary=(
            f"{counts['match']} case(s) agree with the baseline, "
            f"{counts['mismatch']} differ, {counts['inconclusive']} inconclusive"
        ) if comparisons else "no behaviour cases for this unit",
        passed=counts["match"],
        failed=counts["mismatch"],
        inconclusive=counts["inconclusive"],
        log_excerpt=result.log_excerpt if result else "",
    )


def _policy_gate(clean: bool) -> GateResult:
    return GateResult(
        gate="sandbox_policy",
        status="passed" if clean else "failed",
        summary="no timeouts, kills or oversized output" if clean
                else "a sandbox limit was hit (timeout, kill or oversized output)",
    )


def _status_from(result: runner.RunResult) -> str:
    if result.timed_out or result.docker_error:
        return "inconclusive"
    return "passed" if result.exit_code == 0 else "failed"


def _skipped(names: list[str], reason: str) -> list[GateResult]:
    return [
        GateResult(gate=n, status="skipped", summary=reason)  # type: ignore[arg-type]
        for n in names
    ]


def _counterexamples(
    run_id: str,
    unit_id: str | None,
    attempt: int,
    comparisons: list[CaseComparison],
    cases: list[BehaviourCase],
) -> list[Counterexample]:
    by_id = {c.case_id: c for c in cases}
    found: list[Counterexample] = []

    for item in comparisons:
        if item.verdict != "mismatch":
            continue
        case = by_id.get(item.case_id)
        found.append(Counterexample(
            counterexample_id=_new_id("cex_"),
            run_id=run_id,
            unit_id=unit_id or (case.unit_id if case else ""),
            attempt=attempt,
            case_id=item.case_id,
            visibility=item.visibility,
            exposed_to_model=bool(case.exposed_to_model) if case else False,
            function=case.function if case else "",
            args=case.args if case else [],
            kwargs=case.kwargs if case else {},
            old=item.old,
            new=item.new,
            status="open",
        ))
    return found


async def _persist(
    verification_id: str,
    run_id: str,
    unit_id: str | None,
    attempt: int,
    workspace: Workspace | None,
    gates: list[GateResult],
    comparisons: list[CaseComparison],
    counterexamples: list[Counterexample],
    started: datetime,
    runner_mode: str,
    environment: dict[str, str] | None = None,
) -> VerificationResult:
    # Computed here and nowhere else.
    passed = all(g.status == "passed" for g in gates if g.required)

    result = VerificationResult(
        verification_id=verification_id,
        run_id=run_id,
        unit_id=unit_id,
        attempt=attempt,
        workspace_revision=workspace.revision_id if workspace else "rev_baseline",
        gates=gates,
        comparisons=comparisons,
        counterexample_ids=[c.counterexample_id for c in counterexamples],
        all_required_gates_passed=passed,
        runner_mode=runner_mode,
        environment=environment,
        started_at=started,
        finished_at=datetime.now(UTC),
    )

    await store.save_verification(result)
    await store.save_counterexamples(counterexamples)
    if passed and unit_id:
        await store.resolve_counterexamples(run_id, unit_id, verification_id)
    return result


async def _all_inconclusive(
    verification_id: str,
    run_id: str,
    unit_id: str | None,
    attempt: int,
    workspace: Workspace | None,
    started: datetime,
    detail: str,
    gate_names: list[str] | None = None,
) -> VerificationResult:
    """Nothing could be executed. Inconclusive, never a pass."""
    names = gate_names or [
        "compile", "differential", "impacted_tests", "existing_tests", "sandbox_policy"
    ]
    gates = [
        GateResult(gate=n, status="inconclusive", summary=detail)  # type: ignore[arg-type]
        for n in names
    ]
    log.warning("verification inconclusive for %s: %s", run_id, detail)
    return await _persist(
        verification_id, run_id, unit_id, attempt, workspace,
        gates, [], [], started, get_settings().runner_mode,
    )


# --------------------------------------------------------------------------
# counterexamples for the model and the UI
# --------------------------------------------------------------------------

async def get_counterexamples(
    run_id: str, unit_id: str, *, for_model: bool
) -> list[Counterexample]:
    """`for_model=True` redacts held-out inputs and values unless already exposed."""
    items = await store.list_counterexamples(run_id, unit_id)
    if not for_model:
        return items

    safe: list[Counterexample] = []
    for item in items:
        if item.visibility == "held_out" and not item.exposed_to_model:
            safe.append(item.model_copy(update={
                "args": [], "kwargs": {}, "function": "",
                "old": CaseOutcome(case_id=item.case_id, status="ok"),
                "new": CaseOutcome(case_id=item.case_id, status="ok"),
            }))
        else:
            safe.append(item)
    return safe


async def expose_case(run_id: str, case_id: str) -> None:
    """Reveal one held-out case during repair. Recorded so the report can say so."""
    await store.mark_case_exposed(run_id, case_id)
    repo_id = await repository_for_run(run_id)
    if repo_id:
        await mark_exposed(repo_id, case_id)
    log.info("exposed held-out case %s in run %s", case_id, run_id)
