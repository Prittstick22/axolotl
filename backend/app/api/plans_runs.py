"""Plans & runs API - owner: Dev 2. SHARED_CONTRACTS §6.

The frontend polls GET /runs/{id} and /runs/{id}/events?after_sequence=N every 1.5 s
while a run is non-terminal.

Held-out case inputs are redacted on the unit endpoint unless `exposed_to_model`:
the API is the boundary that protects the held-out suite, not the sandbox.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.errors import ARTEFACT_NOT_FOUND, REPORT_NOT_READY, AppError
from app.models.contracts import (
    MigrationPlan,
    RunEvent,
    RunReport,
    RunSummary,
)
from app.orchestration import events, plans, ports, store, worker
from app.persistence.routing import DatabaseAwareRoute
from app.reports import artefacts
from app.reports.builder import get_report

# `route_class` turns a driver failure into 503 DATABASE_UNAVAILABLE instead of a bare
# 500 (Dev 3's DatabaseAwareRoute). Deliberately per-router rather than a global
# handler in errors.py: `/system/status` must answer 200 while *reporting* mongodb as
# unavailable, and a global handler would turn that status report into a 503.
router = APIRouter(tags=["plans", "runs"], route_class=DatabaseAwareRoute)
log = logging.getLogger(__name__)


class CreatePlanRequest(BaseModel):
    target_id: str


class ApprovePlanRequest(BaseModel):
    approved: bool


# --------------------------------------------------------------------------
# plans
# --------------------------------------------------------------------------

@router.post("/repositories/{repo_id}/plans")
async def create_plan(repo_id: str, body: CreatePlanRequest) -> MigrationPlan:
    """422 UNSUPPORTED_TARGET (T-08), 409 REPOSITORY_NOT_EXECUTABLE."""
    return await plans.create_plan(repo_id, body.target_id)


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str) -> MigrationPlan:
    return await store.get_plan(plan_id)


@router.post("/plans/{plan_id}/approve")
async def approve_plan(plan_id: str, body: ApprovePlanRequest) -> MigrationPlan:
    return await plans.approve_plan(plan_id, body.approved)


# --------------------------------------------------------------------------
# runs
# --------------------------------------------------------------------------

@router.post("/plans/{plan_id}/runs")
async def start_run(plan_id: str) -> RunSummary:
    """409 PLAN_NOT_APPROVED if the plan has not been approved."""
    return await worker.start_run(plan_id)


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> RunSummary:
    return await store.get_run(run_id)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> RunSummary:
    return await worker.cancel_run(run_id)


@router.get("/runs/{run_id}/events")
async def list_run_events(
    run_id: str,
    after_sequence: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
) -> list[RunEvent]:
    await store.get_run(run_id)  # 404 RUN_NOT_FOUND for an unknown run
    return await events.list_events(run_id, after_sequence, limit)


@router.get("/runs/{run_id}/units/{unit_id}")
async def get_run_unit(run_id: str, unit_id: str) -> dict[str, Any]:
    """Verifications and counterexamples for one unit.

    `for_model=False`: this is the human view, so exposed held-out cases are labelled
    rather than hidden. Dev 4 still redacts unexposed held-out values.
    """
    run = await store.get_run(run_id)
    unit = next((s for s in run.units if s.unit_id == unit_id), None)
    if unit is None:
        raise AppError(
            "UNIT_NOT_FOUND", f"No unit {unit_id!r} in run {run_id}",
            status_code=404, details={"run_id": run_id, "unit_id": unit_id},
        )

    try:
        counterexamples = await ports.get_counterexamples(
            run_id, unit_id, for_model=False
        )
    except ports.IntegrationUnavailable:
        counterexamples = []

    try:
        verifications = await ports.list_verifications(run_id, unit_id)
    except ports.IntegrationUnavailable:
        verifications = []

    plain_counterexamples = [
        c if isinstance(c, dict) else c.model_dump() for c in counterexamples
    ]
    # Which held-out cases the reviewer may see in full: the ones repair already
    # exposed. A held-out case that passed has no counterexample and stays sealed.
    exposed = {
        c.get("case_id") for c in plain_counterexamples if c.get("exposed_to_model")
    }

    return {
        "unit": unit.model_dump(),
        "verifications": [
            _withhold_harness_logs(_redact_comparisons(
                v if isinstance(v, dict) else v.model_dump(mode="json"), exposed
            ))
            for v in verifications
        ],
        "counterexamples": [_redact_held_out(c) for c in plain_counterexamples],
    }


REDACTED_HELD_OUT = "held-out case - redacted"


def _redact_held_out(counterexample: dict[str, Any]) -> dict[str, Any]:
    """Strip held-out inputs and values unless the case was exposed during repair.

    `for_model=False` asks Dev 4 for the full record, which is right for the human
    view: a reviewer may see that a held-out case failed, and may see an exposed
    one in full. They must not see the inputs or expected values of a held-out case
    that is still sealed - the UI is a read path like any other, and leaking there
    would quietly destroy the independence of the held-out suite.
    """
    if counterexample.get("visibility") != "held_out":
        return counterexample
    if counterexample.get("exposed_to_model"):
        return counterexample

    redacted = dict(counterexample)
    for field in ("args", "kwargs", "old", "new", "function"):
        if field in redacted:
            redacted[field] = REDACTED_HELD_OUT
    redacted["redacted"] = True
    return redacted


def _redact_comparisons(
    verification: dict[str, Any], exposed: set[str | None]
) -> dict[str, Any]:
    """Same rule as the counterexamples, applied to each case comparison.

    The Diff screen needs the per-case old and new outputs, which live only here
    (#45). A held-out comparison carries the expected value of a sealed
    case, so showing it would do exactly what the held-out suite exists to prevent -
    and the UI is a read path like any other. The verdict stays visible either way: a
    reviewer may see *that* a held-out case failed without seeing what it expected.
    """
    comparisons = verification.get("comparisons")
    if not isinstance(comparisons, list):
        return verification

    redacted: list[dict[str, Any]] = []
    for comparison in comparisons:
        if not isinstance(comparison, dict):
            continue
        if (comparison.get("visibility") != "held_out"
                or comparison.get("case_id") in exposed):
            redacted.append(comparison)
            continue

        sealed = dict(comparison)
        for side in ("old", "new"):
            outcome = sealed.get(side)
            if isinstance(outcome, dict):
                # Keep the shape and the status; drop what the case expected.
                sealed[side] = {
                    **outcome,
                    "value": REDACTED_HELD_OUT,
                    "exception_message": (
                        REDACTED_HELD_OUT if outcome.get("exception_message") else None
                    ),
                }
            elif outcome is not None:
                sealed[side] = REDACTED_HELD_OUT
        sealed["redacted"] = True
        redacted.append(sealed)

    return {**verification, "comparisons": redacted}


# `HARNESS_GATES`: these gates' log is the harness's raw output, held-out cases
# included, so it carries the very values `_redact_comparisons` removes. Shared with
# the event journal, which withholds the same logs (#94).
HARNESS_GATES = events.HARNESS_GATES
WITHHELD_LOG = events.WITHHELD_LOG


def _withhold_harness_logs(verification: dict[str, Any]) -> dict[str, Any]:
    """Drop the harness logs whole. The comparisons carry the same results, redacted
    case by case; filtering the log line by line would trust a 4 KB truncation not to
    cut a held-out outcome away from its case id.

    Two layers, because the first one is a list of names. `HARNESS_GATES` is exactly
    right today - only `differential` and `impacted_tests` are built by
    `_comparison_gate`, the one place harness output becomes a gate log - but a gate
    added to that builder later would leak until someone remembered this set. The
    second layer withholds any gate log that mentions a still-sealed case, whatever
    the gate is called, so the guarantee does not depend on that memory.

    The first layer stays: it withholds a harness log even when no case happens to be
    sealed, so the response shape does not change depending on the data.
    """
    gates = verification.get("gates")
    if not isinstance(gates, list):
        return verification

    sealed = _sealed_case_ids(verification)
    return {**verification, "gates": [
        _withhold_gate(g, sealed) if isinstance(g, dict) else g for g in gates
    ]}


def _sealed_case_ids(verification: dict[str, Any]) -> set[str]:
    """Case ids this response is already refusing to show values for."""
    comparisons = verification.get("comparisons")
    if not isinstance(comparisons, list):
        return set()
    return {
        c["case_id"] for c in comparisons
        if isinstance(c, dict) and c.get("redacted") and c.get("case_id")
    }


def _withhold_gate(gate: dict[str, Any], sealed: set[str]) -> dict[str, Any]:
    log = gate.get("log_excerpt")
    if not log:
        return gate
    if gate.get("gate") in HARNESS_GATES:
        return {**gate, "log_excerpt": WITHHELD_LOG}
    if any(case_id in log for case_id in sealed):
        log_name = gate.get("gate")
        log_message = (
            f"case outputs withheld: this {log_name} log mentions a held-out case."
        )
        return {**gate, "log_excerpt": log_message}
    return gate


@router.get("/runs/{run_id}/diff")
async def get_run_diff(run_id: str, unit_id: str | None = None) -> dict[str, Any]:
    """T-16: the diff shown must be the diff of the verified accepted revision.

    **Of the revision that unit's verdict is on** (#55). Asked about a unit, this used
    to diff the run's *last* accepted revision and merely filter it to the unit's
    files. On `run_e5380c4d180c` that served `rev_7295a37a6e68` for all four accepted
    units, though only `unit_orders` was verified on it. The file content happened to
    agree, because a later unit cannot touch another unit's files, but the Diff screen
    shows this `revision` in its header beside the gates of the verification on the
    unit's own revision - so for every unit except the last, the two disagreed.

    A unit that accepted nothing - blocked, skipped, still running - gets
    `revision: null, files: []`. Serving the last revision filtered to its files
    returned an empty list too, which reads as "accepted, nothing changed" rather than
    "nothing was accepted".
    """
    run = await store.get_run(run_id)

    # Resolved before the early return below: a question about a unit that is not in
    # the plan is a 404 whether or not the run accepted anything.
    paths = None
    if unit_id:
        plan = await store.get_plan(run.plan_id)
        unit = next((u for u in plan.units if u.unit_id == unit_id), None)
        if unit is None:
            # Silently returning the whole-run diff would answer a different
            # question than the one asked.
            raise AppError(
                "UNIT_NOT_FOUND",
                f"No unit {unit_id!r} in the plan for run {run_id}",
                status_code=404,
                details={"run_id": run_id, "unit_id": unit_id},
            )
        paths = unit.file_paths

    # The whole journal: with a 1000-event ceiling a long run would hand us an
    # intermediate revision and we would serve it as the verified one (T-16).
    accepted = [
        e for e in await events.list_all_events(run_id)
        if e.event_type == "snapshot_accepted"
    ]
    if unit_id:
        # This unit's own acceptances, in order. The journal is append-only and is
        # what T-16 is argued from, so it stays the source here rather than
        # `UnitRunState.accepted_revision` - one source, not two that can disagree.
        accepted = [e for e in accepted if e.unit_id == unit_id]

    if not accepted:
        return {"base_revision": "rev_baseline", "revision": None, "files": []}

    revision = accepted[-1].payload.get("revision")

    try:
        files = await ports.diff(run_id, "rev_baseline", revision, paths)
    except ports.IntegrationUnavailable as exc:
        raise AppError(
            "INTEGRATION_UNAVAILABLE", str(exc), status_code=503,
            details={"owner": exc.owner},
        ) from exc

    return {"base_revision": "rev_baseline", "revision": revision, "files": files}


@router.get("/runs/{run_id}/report")
async def get_run_report(run_id: str) -> RunReport:
    """404 until the run has finished and a report has been generated."""
    await store.get_run(run_id)
    report = await get_report(run_id)
    if report is None:
        raise AppError(
            REPORT_NOT_READY,
            f"No report for {run_id} yet; it is generated when the run finishes",
            status_code=404,
            details={"run_id": run_id},
        )
    return report


@router.get("/runs/{run_id}/summary")
async def get_run_summary(run_id: str) -> dict[str, Any]:
    """#35: one overview of what the run changed, before the Report's detail.

    Per unit: outcome, attempts, checks run, counterexamples, lines added and removed,
    and Argon's own summary of its patch (labelled model-written). Totals, the declared
    Python version before and after, dependency changes and the report's caveats.
    Every number is read from the report and the journal; 404 until the report exists.
    """
    from app.reports.summary import run_summary

    return await run_summary(run_id)


# --------------------------------------------------------------------------
# artefacts
# --------------------------------------------------------------------------

@router.get("/runs/{run_id}/artefacts")
async def list_run_artefacts(run_id: str) -> list[dict[str, Any]]:
    await store.get_run(run_id)
    return [
        {
            "artefact_id": a["artefact_id"],
            "kind": a["kind"],
            "sha256": a["sha256"],
            "download_url": f"/api/v1/artefacts/{a['artefact_id']}/download",
        }
        for a in await store.list_artefacts(run_id)
    ]


@router.get("/artefacts/{artefact_id}/download")
async def download_artefact(artefact_id: str) -> FileResponse:
    artefact = await store.get_artefact(artefact_id)
    path = Path(artefact["path"])
    if not path.is_file():
        raise AppError(
            ARTEFACT_NOT_FOUND,
            f"Artefact {artefact_id} is recorded but its file is missing",
            status_code=404,
            details={"artefact_id": artefact_id},
        )
    return FileResponse(path, filename=path.name)


@router.get("/runs/{run_id}/export")
async def export_run(run_id: str) -> FileResponse:
    """#40: the migrated project as a ZIP. 404 until the report exists, or when no
    unit was accepted and the project is still the legacy code."""
    run = await store.get_run(run_id)
    report = await get_report(run_id)
    if report is None:
        raise AppError(
            REPORT_NOT_READY,
            f"No report for {run_id} yet; the export is built from it",
            status_code=404,
            details={"run_id": run_id},
        )
    accepted = [
        e for e in await events.list_all_events(run_id)
        if e.event_type == "snapshot_accepted"
    ]
    if not accepted:
        raise AppError(
            ARTEFACT_NOT_FOUND,
            f"No unit was accepted in {run_id}, so there is no migrated project",
            status_code=404,
            details={"run_id": run_id},
        )
    revision = accepted[-1].payload.get("revision")
    try:
        tree = Path(await ports.workspace_path(run_id, revision))
    except ports.IntegrationUnavailable as exc:
        raise AppError(
            "INTEGRATION_UNAVAILABLE", str(exc), status_code=503,
            details={"owner": exc.owner},
        ) from exc
    if not tree.is_dir():
        raise AppError(
            ARTEFACT_NOT_FOUND,
            f"The accepted tree {revision} of {run_id} is no longer on this machine",
            status_code=404,
            details={"run_id": run_id, "revision": revision},
        )
    try:
        from app.docs import export_files

        docs = await export_files(run_id)
    except Exception as exc:  # noqa: BLE001 - the docs are extra; never fail the export
        log.warning("export of %s ships without docs: %s", run_id, exc)
        docs = None
    try:
        from app.reports.summary import run_summary

        summary = (await run_summary(run_id))["markdown"]
    except Exception as exc:  # noqa: BLE001 - the summary is extra too (#35)
        log.warning("export of %s ships without a summary: %s", run_id, exc)
        summary = None
    path = await artefacts.write_export(
        report, await store.get_plan(run.plan_id), revision, tree,
        docs=docs, summary=summary,
    )
    return FileResponse(path, filename=f"{run_id}-migrated.zip")
