"""Migration plans - owner: Dev 2. SHARED_CONTRACTS §6, §8.

A plan is deterministic code's view of the work, optionally *reordered* by Argon.
Argon never decides what is eligible and never adds a unit: if its `suggested_order`
is not a valid topological order of the condensation, it is discarded and the
deterministic order is used, with the reason recorded in `rationale` (T-08 neighbours).
"""

from __future__ import annotations

import logging

from app.api.targets import require_supported_target
from app.config import get_settings
from app.errors import (
    INTEGRATION_UNAVAILABLE,
    MODEL_UNAVAILABLE,
    PLAN_ALREADY_DECIDED,
    REPOSITORY_NOT_EXECUTABLE,
    SNAPSHOT_UNAVAILABLE,
    AppError,
)
from app.models.contracts import MigrationPlan, MigrationUnit, PlanProposal
from app.orchestration import ports, store
from app.orchestration.ids import new_plan_id
from app.orchestration.scheduler import (
    build_units,
    is_valid_order,
    rescore_risk,
    topological_order,
)

log = logging.getLogger(__name__)

ORDER_OVERRIDDEN = (
    "Argon's suggested order was not a valid dependency-first order of the "
    "condensation, so the deterministic order was used instead."
)
NO_AGENT = (
    "No plan rationale: the Argon agent is not wired up yet, so the deterministic "
    "dependency-first order was used."
)


async def create_plan(repo_id: str, target_id: str) -> MigrationPlan:
    """Build units from the real graph, ask Argon to order them, validate, persist."""
    settings = get_settings()
    target = require_supported_target(target_id)  # 422 UNSUPPORTED_TARGET (T-08)

    try:
        repository = await ports.get_repository(repo_id)
        graph = await ports.load_networkx(repo_id)
    except ports.IntegrationUnavailable as exc:
        # Without a real graph there is no plan. We do not invent one.
        raise AppError(
            INTEGRATION_UNAVAILABLE,
            f"Cannot build a plan yet: {exc}",
            status_code=503,
            details={"owner": exc.owner, "function": f"{exc.module}.{exc.function}"},
        ) from exc

    if not repository.executable:
        # Uploads are static-analysis only (§0.5, Spec §3.4).
        raise AppError(
            REPOSITORY_NOT_EXECUTABLE,
            "This repository is static-analysis only; executable migration is "
            "enabled for allowlisted trusted fixtures.",
            status_code=409,
            details={"repository_id": repo_id, "origin": repository.origin},
        )
    require_snapshot(repo_id)

    units = build_units(graph, ports.files_by_module(graph))
    cases_known = await _apply_visible_case_counts(repo_id, units)
    if cases_known:
        # Risk was scored before the counts existed, so every unit carried
        # `no_visible_cases` and its weight (#3). When the cases are genuinely
        # unavailable the factor is honest, so only rescore when they are not.
        rescore_risk(units)
    deterministic = topological_order(units)

    proposal, rationale_prefix = await _ask_argon(repo_id, units, target)

    if proposal is None:
        order, rationale = deterministic, NO_AGENT
        risks: list[str] = []
        coverage_gaps: list[str] = []
        interaction_id = None
    elif is_valid_order(proposal.suggested_order, units):
        order = proposal.suggested_order
        rationale = rationale_prefix + proposal.rationale
        risks, coverage_gaps = proposal.risks, proposal.coverage_gaps
        interaction_id = proposal.interaction_id
    else:
        log.warning("discarding Argon order for %s: not a topological order", repo_id)
        order = deterministic
        rationale = f"{ORDER_OVERRIDDEN}\n\n{proposal.rationale}"
        risks, coverage_gaps = proposal.risks, proposal.coverage_gaps
        interaction_id = proposal.interaction_id

    plan = MigrationPlan(
        plan_id=new_plan_id(),
        repository_id=repo_id,
        snapshot_hash=repository.snapshot_hash,
        target_id=target.target_id,
        units=units,
        order=order,
        rationale=rationale,
        risks=risks,
        coverage_gaps=coverage_gaps + _structural_gaps(units, cases_known),
        status="draft",
        model=settings.gemini_model,
        interaction_id=interaction_id,
        created_at=store.utcnow(),
    )

    await store.save_plan(plan)
    await store.supersede_open_plans(repo_id, except_plan_id=plan.plan_id)
    return plan


def require_snapshot(repo_id: str) -> None:
    """409 before any model call when this machine cannot run the code (#84).

    Records are shared through Atlas but a snapshot stays on the machine that imported
    it, so on any other laptop a plan cost an Argon call and its run then failed copying
    the baseline. Built-in fixtures pass: `snapshot_path` rebuilds them from the checkout.
    """
    if not ports.snapshot_available(repo_id):
        raise AppError(
            SNAPSHOT_UNAVAILABLE,
            "This repository's code isn't on this machine: snapshots stay on the "
            "machine that imported them. Import it again here to plan or run it.",
            status_code=409,
            details={"repository_id": repo_id},
        )


async def _ask_argon(
    repo_id: str, units: list[MigrationUnit], target
) -> tuple[PlanProposal | None, str]:
    """Ask Dev 1 for an ordering. Absence is not an error - we fall back and say so."""
    try:
        graph_payload = await ports.get_graph(repo_id)
        repository = await ports.get_repository(repo_id)
    except ports.IntegrationUnavailable:
        return None, ""

    from app.agents.contexts import PlanContext

    context = PlanContext(
        repository=repository,
        graph=graph_payload,
        units=units,
        target=target,
        module_summaries=_module_summaries(graph_payload),
    )
    from app.agents.gemini_client import ModelUnavailable

    try:
        return await ports.propose_plan(context), ""
    except ports.IntegrationUnavailable:
        return None, ""
    except ModelUnavailable as exc:
        # Not the NO_AGENT fallback: that says the agent is not wired up, which would
        # be false here. The caller gets the model's reason, and can try again (#47).
        raise AppError(
            MODEL_UNAVAILABLE,
            f"Argon could not propose a plan: {exc}",
            status_code=503,
            details={"model": get_settings().gemini_model},
        ) from exc


def _module_summaries(graph_payload) -> dict[str, str]:
    """One short line per module for the planning prompt. No source, no secrets."""
    return {
        node.id: f"{node.label} ({node.function_count} functions, "
        f"{node.class_count} classes)"
        for node in graph_payload.nodes
        if node.kind == "local_module"
    }


async def _apply_visible_case_counts(
    repo_id: str, units: list[MigrationUnit]
) -> bool:
    """Fill `visible_case_count` from Dev 4's cases. False when they are unavailable.

    Set on the units by `unit_id`, not passed into `build_units` as a module-keyed
    map: cases carry a `unit_id`, so going through module ids would be a needless
    key translation that could silently mismatch.
    """
    try:
        cases = await ports.load_cases(repo_id, None, include_held_out=False)
    except ports.IntegrationUnavailable:
        return False

    counts: dict[str, int] = {}
    for case in cases:
        unit_id = getattr(case, "unit_id", None) or (
            case.get("unit_id") if isinstance(case, dict) else None
        )
        if unit_id:
            counts[unit_id] = counts.get(unit_id, 0) + 1

    for unit in units:
        unit.visible_case_count = counts.get(unit.unit_id, 0)
    return True


def _structural_gaps(units: list[MigrationUnit], cases_known: bool) -> list[str]:
    """Coverage gaps we can see without the model, from the units themselves."""
    gaps: list[str] = []

    if not cases_known:
        # Every unit reads as 0 cases when the counts were never supplied. Saying
        # "no visible cases" then would be a false coverage claim about the fixture.
        gaps.append(
            "Behaviour case coverage per unit is not known yet: the case loader is "
            "not wired up, so no per-unit coverage gap can be computed."
        )
    else:
        untested = sorted(u.unit_id for u in units if u.visible_case_count == 0)
        if untested:
            gaps.append(
                "No visible behaviour cases for: " + ", ".join(untested)
                + ". A migration of these is checked only by the compile and "
                "existing-test gates, not by differential comparison."
            )
    cycles = sorted(u.unit_id for u in units if u.is_cycle)
    if cycles:
        gaps.append(
            "Cyclic unit(s) migrated as a single step: " + ", ".join(cycles)
            + ". Their modules cannot be verified independently of each other."
        )
    return gaps


async def approve_plan(plan_id: str, approved: bool) -> MigrationPlan:
    """Record the human decision. Only an approved plan can start a run."""
    plan = await store.get_plan(plan_id)

    if plan.status in {"approved", "rejected", "superseded"}:
        raise AppError(
            PLAN_ALREADY_DECIDED,
            f"Plan {plan_id} is already {plan.status}",
            status_code=409,
            details={"plan_id": plan_id, "status": plan.status},
        )

    plan.status = "approved" if approved else "rejected"
    plan.approved_at = store.utcnow() if approved else None
    await store.save_plan(plan)
    return plan
