"""UnitControllerImpl - owner: Dev 2. SHARED_CONTRACTS §8, Dev 2 brief "UnitControllerImpl".

This is the whole surface the model gets. Everything it can do to a run goes through
here, which is what makes the safety properties enforceable rather than requested:

  - a patch may only touch `unit.file_paths`; Dev 4 enforces it, we pass the allowlist
  - attempts are counted here, so the model cannot talk its way into a fourth try
  - `is_verified()` reads the latest *persisted* VerificationResult and nothing else
  - held-out case data is redacted unless the exposure policy has released it
  - `emit` accepts only the three agent event types

Nothing the model says changes a status. Only a recorded verification does.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import get_settings
from app.models.contracts import (
    MigrationUnit,
    PatchAck,
    PatchProposal,
    UnitStatus,
    VerificationResult,
)
from app.orchestration import events, ports
from app.orchestration.states import transition_unit

log = logging.getLogger(__name__)

AGENT_EVENT_TYPES = frozenset({"agent_turn", "agent_tool_call", "agent_tool_result"})

NO_ATTEMPTS_LEFT = "patch attempt budget exhausted"
REVISION_BASELINE = "rev_baseline"

# At most one held-out case may ever be revealed for a single unit.
MAX_EXPOSED_PER_UNIT = 1


class UnitControllerImpl:
    """One per unit per run. Dev 1 receives this as `controller`."""

    def __init__(
        self,
        run_id: str,
        unit: MigrationUnit,
        impacted: list[MigrationUnit],
        *,
        repository_id: str = "",
        base_revision: str = REVISION_BASELINE,
        max_patch_attempts: int | None = None,
        on_status_change: Callable[[UnitStatus], Awaitable[None]] | None = None,
    ) -> None:
        settings = get_settings()
        self.run_id = run_id
        self.unit = unit
        self.impacted = impacted
        self.repository_id = repository_id
        self.base_revision = base_revision
        # Set only when a patch has actually been APPLIED. Without it, a rejected
        # or no-op patch would leave an untouched baseline workspace that sails
        # through every gate and marks the unit passed with nothing migrated.
        self.patch_applied = False
        # Write-through so the run document and this controller never hold two
        # different ideas of the unit's status. Without it the worker would try
        # migrating -> passed and the state machine would (correctly) reject it.
        self._on_status_change = on_status_change
        self.max_patch_attempts = (
            settings.max_patch_attempts if max_patch_attempts is None else max_patch_attempts
        )

        self.attempts_used = 0
        self.status: UnitStatus = "migrating"
        self.workspace: Any = None
        # The workspace that produced a PASSING verification. `self.workspace` moves
        # on with every later submit_patch, so accepting that one could accept a
        # revision no verifier ever looked at.
        self.verified_workspace: Any = None
        self.latest_verification: VerificationResult | None = None
        self.exposed_case_ids: list[str] = []
        self.block_reason: str | None = None

    def set_status_callback(
        self, callback: Callable[[UnitStatus], Awaitable[None]]
    ) -> None:
        """Set the write-through callback after construction.

        The worker's callback reports this controller's `attempts_used` as well as
        the status (#32), so it needs the controller to exist before it is defined.
        Binding it here keeps that order explicit instead of relying on a closure
        over a loop variable, which would read whichever unit the loop had reached.
        """
        self._on_status_change = callback

    # ----------------------------------------------------------------- status

    async def _set_status(self, target: UnitStatus) -> None:
        if target == self.status:
            return
        previous = self.status
        self.status = transition_unit(self.unit.unit_id, self.status, target)
        if self._on_status_change is not None:
            await self._on_status_change(target)
        await events.append_event(
            self.run_id,
            "unit_state_changed",
            unit_id=self.unit.unit_id,
            payload={"from": previous, "to": target},
        )

    # ------------------------------------------------------------ submit_patch

    def patch_attempts_remaining(self) -> int:
        return max(0, self.max_patch_attempts - self.attempts_used)

    async def submit_patch(self, patch: PatchProposal) -> PatchAck:
        """Apply a proposed patch to a fresh workspace from the last accepted revision.

        Fresh every attempt, so a rejected or failed attempt never accumulates into the
        next one.
        """
        if self.patch_attempts_remaining() <= 0:
            await events.append_event(
                self.run_id, "patch_rejected", unit_id=self.unit.unit_id,
                attempt=self.attempts_used, payload={"errors": [NO_ATTEMPTS_LEFT]},
            )
            return PatchAck(accepted=False, workspace_revision=None, errors=[NO_ATTEMPTS_LEFT])

        self.attempts_used += 1
        attempt = self.attempts_used

        total_bytes = sum(len(f.new_content.encode("utf-8")) for f in patch.files)
        await events.append_event(
            self.run_id, "patch_proposed", unit_id=self.unit.unit_id, attempt=attempt,
            payload={
                "files": [f.path for f in patch.files],
                "summary": patch.summary,
                "bytes": total_bytes,
            },
        )

        if self.status == "repairing":
            # Spec §6.2 runs verifying -> repairing -> migrating. The failed
            # verification put the unit in `repairing`; starting a fresh patch is
            # what moves it back to `migrating`.
            await events.append_event(
                self.run_id, "repair_started", unit_id=self.unit.unit_id, attempt=attempt,
                payload={
                    "attempt": attempt,
                    "budget_remaining": self.patch_attempts_remaining(),
                },
            )
            await self._set_status("migrating")

        try:
            self.workspace = await ports.create_workspace(
                self.run_id, self.repository_id, self.base_revision
            )
            # The allowlist is the unit's own files and nothing else.
            ack = await ports.apply_patch(
                self.workspace, patch, self.unit.file_paths
            )
        except ports.IntegrationUnavailable as exc:
            self.patch_applied = False
            # Infrastructure never reached the model's patch, so do not charge it an
            # attempt - otherwise a sandbox outage blocks the unit as if the model
            # had exhausted its budget, which blames it for someone else's failure.
            self.attempts_used -= 1
            errors = [str(exc)]
            await events.append_event(
                self.run_id, "patch_rejected", unit_id=self.unit.unit_id,
                attempt=attempt, payload={"errors": errors, "attempt_refunded": True},
            )
            return PatchAck(accepted=False, workspace_revision=None, errors=errors)

        self.patch_applied = bool(ack.accepted)
        if ack.accepted:
            await events.append_event(
                self.run_id, "patch_applied", unit_id=self.unit.unit_id, attempt=attempt,
                payload={"workspace_revision": ack.workspace_revision},
            )
        else:
            await events.append_event(
                self.run_id, "patch_rejected", unit_id=self.unit.unit_id,
                attempt=attempt, payload={"errors": ack.errors},
            )
        return ack

    # -------------------------------------------------------- run_verification

    async def run_verification(self) -> dict[str, Any]:
        """Run the gates and persist the result. Returns a deliberately reduced summary.

        The model is told which gates failed and which *visible* cases failed. It is
        told only a count of failing held-out cases, unless the exposure policy below
        releases exactly one.
        """
        if self.workspace is None:
            return {
                "ok": False,
                "error": "no workspace: submit a patch before verifying",
                "gates": [],
            }

        if not self.patch_applied:
            # The last patch was rejected, so the workspace still holds the
            # unmodified baseline. Verifying it would pass every gate trivially and
            # mark the unit migrated when nothing was migrated.
            return {
                "ok": False,
                "error": (
                    "the last patch was not applied, so there is nothing to verify; "
                    "submit an accepted patch first"
                ),
                "gates": [],
            }

        await self._set_status("verifying")
        await events.append_event(
            self.run_id, "verification_started", unit_id=self.unit.unit_id,
            attempt=self.attempts_used,
            payload={
                "workspace_revision": getattr(self.workspace, "revision_id", None),
                "scope": "unit",
            },
        )

        try:
            result = await ports.verify_unit(
                self.run_id, self.unit, self.workspace, self.impacted, self.attempts_used
            )
        except ports.IntegrationUnavailable as exc:
            # No verifier means no verdict. Inconclusive is NOT a pass.
            self.block_reason = str(exc)
            await events.append_event(
                self.run_id, "error", unit_id=self.unit.unit_id,
                attempt=self.attempts_used,
                payload={"code": "VERIFIER_UNAVAILABLE", "message": str(exc)},
            )
            await self._after_failed_verification()
            return {"ok": False, "error": str(exc), "gates": [], "inconclusive": True}

        self.latest_verification = result

        for gate in result.gates:
            await events.append_event(
                self.run_id, "gate_result", unit_id=self.unit.unit_id,
                attempt=self.attempts_used, payload=gate.model_dump(),
            )

        failing_visible = [
            c.case_id for c in result.comparisons
            if c.verdict != "match" and c.visibility == "visible"
        ]
        failing_held_out = [
            c.case_id for c in result.comparisons
            if c.verdict != "match" and c.visibility == "held_out"
        ]

        for counterexample_id in result.counterexample_ids:
            await events.append_event(
                self.run_id, "counterexample_recorded", unit_id=self.unit.unit_id,
                attempt=self.attempts_used,
                payload={"counterexample_id": counterexample_id},
            )

        passed = result.all_required_gates_passed
        await events.append_event(
            self.run_id,
            "verification_passed" if passed else "verification_failed",
            unit_id=self.unit.unit_id,
            attempt=self.attempts_used,
            payload={
                "verification_id": result.verification_id,
                "failed_case_ids": failing_visible,
                "counterexample_ids": result.counterexample_ids,
            },
        )

        exposed = await self._maybe_expose(failing_visible, failing_held_out)

        # One verification per applied patch. Without this the model could re-roll
        # `run_verification` for free until a flaky gate happened to pass, and the
        # latest result wins. Verifying again requires submitting a new patch, which
        # costs an attempt.
        self.patch_applied = False

        if passed:
            # Pin the exact workspace this verdict was produced from.
            self.verified_workspace = self.workspace

        # NOTE: a passing verification does NOT mark the unit `passed` here. The
        # worker does that, and only after `accept_workspace` succeeds - otherwise a
        # failed acceptance would leave a unit terminally `passed` with nothing
        # accepted behind it. The controller cannot mark a unit passed at all, which
        # is one less place that could violate the central invariant.
        if not passed:
            await self._after_failed_verification()

        return {
            "ok": passed,
            "gates": [
                {"gate": g.gate, "status": g.status, "summary": g.summary}
                for g in result.gates
            ],
            "failing_visible_case_ids": failing_visible,
            "failing_held_out_count": len(failing_held_out),
            "exposed_case_ids": exposed,
            "attempts_remaining": self.patch_attempts_remaining(),
        }

    async def _after_failed_verification(self) -> None:
        """Spec §6.2: verifying -> repairing while budget remains.

        With the budget spent the unit stays in `verifying`; the worker is what moves
        it to `blocked`. The controller never blocks a unit itself, so there is one
        place that decides a unit has failed.
        """
        if self.patch_attempts_remaining() > 0:
            await self._set_status("repairing")

    async def _maybe_expose(
        self, failing_visible: list[str], failing_held_out: list[str]
    ) -> list[str]:
        """Release one held-out case when the model has nothing else to work from.

        Only when every failure is held-out and at least one attempt has been made.
        Otherwise the model would be repairing blind. Exposure is recorded so the
        report can say how much of the held-out suite was revealed.
        """
        if failing_visible or not failing_held_out:
            return []
        if self.attempts_used < 1:
            return []
        if self.patch_attempts_remaining() <= 0:
            # Nothing left to repair with, so exposing a case would spend an
            # independent check the model can no longer act on. The held-out suite
            # only has value while it is sealed; do not trade one away for nothing.
            return []

        if len(self.exposed_case_ids) >= MAX_EXPOSED_PER_UNIT:
            # The cap is per UNIT, not per case: a 3-attempt unit could otherwise
            # release three different held-out cases and quietly dismantle the
            # independent suite one attempt at a time.
            return list(self.exposed_case_ids)

        candidate = sorted(failing_held_out)[0]
        if candidate in self.exposed_case_ids:
            return list(self.exposed_case_ids)

        try:
            await ports.expose_case(self.run_id, candidate)
        except ports.IntegrationUnavailable:
            return []

        self.exposed_case_ids.append(candidate)
        log.info("exposed held-out case %s for %s", candidate, self.unit.unit_id)

        # Journal it, or the report's "a held-out case was revealed" caveat can
        # never fire and the run silently loses an independent check.
        await events.append_event(
            self.run_id, "counterexample_recorded", unit_id=self.unit.unit_id,
            attempt=self.attempts_used,
            payload={
                "case_id": candidate,
                "visibility": "held_out",
                "exposed_to_model": True,
            },
        )
        return [candidate]

    # ------------------------------------------------------- counterexamples

    async def get_counterexamples(self) -> list[dict[str, Any]]:
        """Model-safe view: held-out inputs and values redacted unless exposed."""
        try:
            found = await ports.get_counterexamples(
                self.run_id, self.unit.unit_id, for_model=True
            )
        except ports.IntegrationUnavailable:
            return []
        return [c if isinstance(c, dict) else c.model_dump() for c in found]

    # ------------------------------------------------------------------ emit

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Agent-authored events only. The model cannot write run or gate events."""
        if event_type not in AGENT_EVENT_TYPES:
            raise ValueError(
                f"{event_type!r} is not an agent event; allowed: "
                f"{', '.join(sorted(AGENT_EVENT_TYPES))}"
            )
        await events.append_event(
            self.run_id, event_type, unit_id=self.unit.unit_id,
            attempt=self.attempts_used or None, payload=payload,
        )

    # ----------------------------------------------------------- is_verified

    def is_verified(self) -> bool:
        """The latest persisted VerificationResult, and nothing else, decides this.

        Deliberately the *latest*, not "any": if the model gets a pass and then
        submits a further patch that fails, the unit is not verified. The last word
        belongs to the last verification.
        """
        if self.latest_verification is None:
            return False
        return bool(self.latest_verification.all_required_gates_passed)
