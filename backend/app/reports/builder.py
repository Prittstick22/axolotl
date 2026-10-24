"""Run reports - owner: Dev 2. Dev 2 brief "Reports", Spec §11.2, §11.3.

Every number here comes from recorded evidence: `verification_results`,
`counterexamples` and `run_events`. **Never from model text.** The optional
`narrative` from Argon is clearly separated and is the only prose the model writes.

The caveats are not boilerplate. "Behaviour preserved" in this project means agreement
on the cases that were actually run, and the report has to say so plainly, every time.
"""

from __future__ import annotations

import logging

from app.models.contracts import RunReport, RunSummary
from app.orchestration import events, ports, store
from app.reports import artefacts
from app.reports.performance import build_performance

log = logging.getLogger(__name__)

NOT_A_PROOF = (
    "Agreement shown only for {cases} tested case(s) across {verified} of {units} "
    "migration unit(s); this is not a proof of equivalence."
)


async def build_report(run: RunSummary) -> RunReport:
    """Assemble the report from what was recorded, and persist it."""
    # The WHOLE journal, not the first 1000 events: the last `snapshot_accepted`
    # decides which revision gets published as the verified one.
    journal = await events.list_all_events(run.run_id)

    gate_events = _final_gate_results(journal)
    passed = sum(int(e.payload.get("passed", 0)) for e in gate_events)
    failed = sum(int(e.payload.get("failed", 0)) for e in gate_events)
    inconclusive = sum(int(e.payload.get("inconclusive", 0)) for e in gate_events)

    counterexamples = _unique_counterexamples(journal)
    accepted = [e for e in journal if e.event_type == "snapshot_accepted"]
    final_revision = accepted[-1].payload.get("revision") if accepted else None

    changed_files = await _changed_files(run, final_revision)

    # Artefacts first, so their ids and the patch hash can go into the report.
    artefact_ids: list[str] = []
    final_artefact_hash: str | None = None
    t16: dict | None = None

    if final_revision:
        patch = await artefacts.write_patch_artefact(run, final_revision)
        if patch is not None:
            artefact_ids.append(patch["artefact_id"])
            final_artefact_hash = patch["sha256"]
            # T-16: the diff we are about to publish must be the diff of the
            # revision that was actually verified.
            t16 = await artefacts.verify_patch_matches_revision(
                run.run_id, final_revision
            )

        # The archive was implemented and tested but never actually called.
        archive = await _write_archive(run, final_revision)
        if archive is not None:
            artefact_ids.append(archive["artefact_id"])

    report = RunReport(
        run_id=run.run_id,
        verdict=_verdict_of(run),
        units=run.units,
        final_verification_id=_final_verification_id(journal),
        changed_files=changed_files,
        tests_passed=passed,
        tests_failed=failed,
        tests_inconclusive=inconclusive,
        counterexamples_found=len(counterexamples),
        counterexamples_resolved=_resolved_count(run, counterexamples),
        baseline_snapshot_hash=_baseline_hash(journal),
        final_artefact_hash=final_artefact_hash,
        coverage_caveats=_caveats(run, journal, passed),
        limitations=_limitations(run, journal, t16),
        narrative=await _narrative(run, journal),
        performance=await build_performance(run, final_revision, _final_verification_id(journal)),
        artefact_ids=artefact_ids,
        generated_at=store.utcnow(),
    )

    # The report is itself a downloadable artefact, so it is written after the
    # report exists and its id appended.
    await _persist(report)
    record = await artefacts.write_report_artefact(report)
    report.artefact_ids.append(record["artefact_id"])
    await _persist(report)
    return report


def _final_gate_results(journal) -> list:
    """The last `gate_result` per (unit_id, gate).

    Every repair attempt re-emits that unit's gates, so summing all of them reports
    more passing tests than the suite contains - a unit verified three times would
    triple its own counts. The honest number is the one from the attempt that
    actually stands.

    Final-verification gates carry `unit_id=None`, which forms its own key and is
    therefore counted alongside the per-unit totals rather than overwriting them.

    The journal arrives in ascending sequence order, so a later event overwrites an
    earlier one for the same key.
    """
    latest: dict[tuple[str | None, str], object] = {}
    for recorded in journal:
        if recorded.event_type != "gate_result":
            continue
        latest[(recorded.unit_id, str(recorded.payload.get("gate", "")))] = recorded
    return list(latest.values())


def _verdict_of(run: RunSummary):
    if run.state in {"completed", "completed_with_warnings", "failed", "cancelled"}:
        return run.state
    # A report should only be built from a finished run; say so rather than guess.
    return "failed"


def _final_verification_id(journal) -> str | None:
    """The id of the FINAL verification, not the last unit's.

    Final verification emits with `unit_id=None` and `scope="final"`; unit
    verifications always carry a unit_id. Matching on event type alone would return
    whichever unit happened to be verified last, which is a different run artefact
    entirely.
    """
    for event in reversed(journal):
        if event.event_type not in {"verification_passed", "verification_failed"}:
            continue
        if event.unit_id is None or event.payload.get("scope") == "final":
            return event.payload.get("verification_id")
    return None


def _baseline_hash(journal) -> str:
    for event in journal:
        if event.event_type == "baseline_captured":
            return str(event.payload.get("snapshot_hash", ""))
    return ""


async def _write_archive(run: RunSummary, revision: str):
    """Zip the final accepted tree, if Dev 4 can tell us where it is on disk."""
    try:
        tree = await ports.workspace_path(run.run_id, revision)
    except ports.IntegrationUnavailable:
        log.info("no archive artefact for %s: workspace_path not available", run.run_id)
        return None
    return await artefacts.write_archive_artefact(run, tree)


def _unique_counterexamples(journal) -> list:
    """One entry per distinct counterexample.

    A unit that fails the same case on every attempt re-emits the same
    counterexample, so counting raw events inflates `counterexamples_found` the
    same way summing every attempt's gates inflated the test counts. Deduped on
    `counterexample_id`, falling back to (unit_id, case_id) when an emitter did
    not supply one.
    """
    seen: dict[object, object] = {}
    for recorded in journal:
        if recorded.event_type != "counterexample_recorded":
            continue
        key = recorded.payload.get("counterexample_id") or (
            recorded.unit_id,
            recorded.payload.get("case_id"),
        )
        seen.setdefault(key, recorded)
    return list(seen.values())


def _resolved_count(run: RunSummary, counterexamples) -> int:
    """A counterexample counts as resolved only if its unit ended up passed.

    Final-verification counterexamples have no unit_id; they are never resolved by
    definition, since nothing runs after the final check.
    """
    passed_units = {s.unit_id for s in run.units if s.status == "passed"}
    return sum(
        1 for e in counterexamples if e.unit_id is not None and e.unit_id in passed_units
    )


async def _changed_files(run: RunSummary, final_revision: str | None) -> list[str]:
    if final_revision is None:
        return []
    try:
        files = await ports.diff(run.run_id, "rev_baseline", final_revision)
    except ports.IntegrationUnavailable:
        return []
    return sorted(f["path"] for f in files if "path" in f)


def _caveats(run: RunSummary, journal, cases_tested: int) -> list[str]:
    """Always present, always specific. Spec §11.2."""
    # Count the units that actually passed, not units_total: a run where four of
    # six units were skipped must not claim agreement "across 6 units".
    verified_count = sum(1 for s in run.units if s.status == "passed")
    caveats = [
        NOT_A_PROOF.format(
            cases=cases_tested, verified=verified_count, units=run.units_total
        ),
    ]

    exposed = [
        e.payload.get("case_id")
        for e in journal
        if e.event_type == "counterexample_recorded"
        and e.payload.get("exposed_to_model")
    ]
    if exposed:
        caveats.append(
            f"{len(exposed)} held-out case(s) were revealed to the model during "
            "repair, so they no longer function as an independent check."
        )

    verified = [s.unit_id for s in run.units if s.status == "passed"]
    if run.tests_inconclusive:
        caveats.append(
            f"{run.tests_inconclusive} check(s) were inconclusive. Inconclusive is "
            "not a pass."
        )

    skipped = [s.unit_id for s in run.units if s.status == "skipped"]
    if skipped:
        caveats.append(
            "Not attempted because a prerequisite was blocked: " + ", ".join(sorted(skipped))
        )

    if not verified:
        caveats.append("No unit completed verification in this run.")
    return caveats


def _limitations(run: RunSummary, journal, t16: dict | None = None) -> list[str]:
    limitations = [
        "Execution is limited to allowlisted trusted fixtures; uploaded archives are "
        "analysed statically and never run.",
        "The sandbox is a Docker container, not a hardened multi-tenant isolate. See "
        "runner/policy.md for what it does and does not protect against.",
    ]
    if run.runner_mode != "trusted_docker":
        limitations.append(
            f"The sandbox runner was {run.runner_mode!r} for this run, so no gate "
            "could execute code. Nothing in this report was verified by execution."
        )
    # T-16. A mismatch is stated outright rather than quietly shipping a diff that
    # is not the one that was verified.
    if t16 is not None and t16.get("checked") and not t16.get("matches"):
        limitations.append(
            "The downloadable patch does NOT match the verified revision: "
            f"{t16.get('reason')}. Do not treat the diff as evidence of what was "
            "verified until this is resolved."
        )

    errors = {
        e.payload.get("code") for e in journal if e.event_type == "error"
    }
    if errors:
        limitations.append(
            "Errors recorded during the run: " + ", ".join(sorted(c for c in errors if c))
        )
    return limitations


async def _narrative(run: RunSummary, journal) -> str | None:
    """Optional Argon summary. It may cite only evidence we hand it."""
    try:
        return await ports.narrate_report(
            {
                "run_id": run.run_id,
                "state": run.state,
                "units": [s.model_dump() for s in run.units],
                "events": [e.model_dump() for e in journal[-50:]],
            }
        )
    except ports.IntegrationUnavailable:
        return None
    except Exception:  # noqa: BLE001 - a narrative is never worth failing a report for
        log.exception("narrate_report failed for %s", run.run_id)
        return None


# SHARED_CONTRACTS §9 lists no collection for reports, but §6 needs
# GET /runs/{run_id}/report to return one. `run_reports` is additive (§11) - tell the
# team if you rename it.
REPORTS = "run_reports"


async def _persist(report: RunReport) -> None:
    from app.persistence import db

    database = await db.get_db()
    await database[REPORTS].replace_one(
        {"run_id": report.run_id}, report.model_dump(), upsert=True
    )


async def get_report(run_id: str) -> RunReport | None:
    """None until the run has finished and a report has been generated (§6: 404)."""
    from app.persistence import db

    database = await db.get_db()
    document = await database[REPORTS].find_one({"run_id": run_id}, {"_id": 0})
    return RunReport(**document) if document else None
