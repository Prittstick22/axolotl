"""Integration points Dev 2 depends on - owner: Dev 2 (these adapters only).

The orchestrator needs three things it does not own:

    Dev 1  `app.agents`        propose_plan, run_unit_session
    Dev 3  `app.graph` etc.    load_networkx, get_repository
    Dev 4  `app.sandbox`       capture_baseline, verify_unit, verify_final, ...

Until those land, calling them raises `IntegrationUnavailable`. The worker turns that
into an honest outcome - gates `inconclusive`, unit `blocked`, run
`completed_with_warnings` - and **never** into a pass. There are no fake results here:
nothing in this file invents a graph, a patch or a verdict.

Owners: delete your entry's fallback by implementing the real function at the path
named below. These adapters resolve lazily on every call, so the moment the function
exists it is used, with no change here.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

log = logging.getLogger(__name__)


class IntegrationUnavailable(Exception):
    """A collaborator's function is not implemented yet.

    Carries enough detail to put an honest reason in a `unit_blocked` event and in the
    final report, rather than a bare traceback.
    """

    def __init__(self, owner: str, module: str, function: str) -> None:
        self.owner = owner
        self.module = module
        self.function = function
        super().__init__(
            f"{module}.{function} is not implemented yet (owner: {owner})"
        )


def _resolve(module: str, function: str, owner: str) -> Any:
    try:
        loaded = importlib.import_module(module)
    except ImportError as exc:
        raise IntegrationUnavailable(owner, module, function) from exc

    candidate = getattr(loaded, function, None)
    if candidate is None or not callable(candidate):
        raise IntegrationUnavailable(owner, module, function)
    return candidate


def is_available(module: str, function: str) -> bool:
    """True when a collaborator's function exists. Used for honest status reporting."""
    try:
        _resolve(module, function, owner="?")
    except IntegrationUnavailable:
        return False
    return True


# --------------------------------------------------------------------------
# Dev 3 - graph and repositories
# --------------------------------------------------------------------------

async def load_networkx(repo_id: str):
    """-> nx.DiGraph of local modules; edge A -> B means A depends on B."""
    return await _resolve("app.graph", "load_networkx", "Dev 3")(repo_id)


async def get_repository(repo_id: str):
    """-> RepositoryRecord."""
    return await _resolve("app.ingestion", "get_repository", "Dev 3")(repo_id)


def snapshot_available(repo_id: str) -> bool:
    """-> True when this machine holds the repository's code (#84)."""
    return _resolve("app.ingestion", "snapshot_available", "Dev 3")(repo_id)


async def get_graph(repo_id: str, run_id: str | None = None):
    """-> GraphPayload."""
    return await _resolve("app.graph", "get_graph", "Dev 3")(repo_id, run_id)


def files_by_module(graph) -> dict[str, str]:
    """module_id -> repo-relative path, read off the graph's node attributes.

    Dev 3 sets `path` on each node. Falls back to deriving a filename from the module
    id so `build_units` still produces usable `file_paths` during development.
    """
    mapping: dict[str, str] = {}
    for node, attributes in graph.nodes(data=True):
        path = attributes.get("path")
        if not path:
            path = node.removeprefix("module_").replace("__", "/") + ".py"
        mapping[node] = path
    return mapping


# --------------------------------------------------------------------------
# Dev 4 - sandbox and verification
# --------------------------------------------------------------------------

async def runner_status(runtime=None) -> dict[str, Any]:
    """-> {mode, available, image_digest, detail} for one runtime's image; the 3.12
    image when `runtime` is None."""
    return await _resolve("app.sandbox", "runner_status", "Dev 4")(runtime)


def runtimes_for_target(target_id: str):
    """-> RuntimePair (baseline, candidate) the target's run uses (#22)."""
    return _resolve("app.sandbox.runtimes", "for_target", "Dev 4")(target_id)


async def capture_baseline(run_id: str, repo_id: str, units: list):
    """-> BaselineSummary."""
    return await _resolve("app.verification", "capture_baseline", "Dev 4")(
        run_id, repo_id, units
    )


async def load_cases(repo_id: str, unit_id: str | None = None, *,
                     include_held_out: bool = False) -> list:
    """-> list[BehaviourCase]. Default excludes held-out cases deliberately."""
    return await _resolve("app.verification", "load_cases", "Dev 4")(
        repo_id, unit_id, include_held_out=include_held_out
    )


async def create_workspace(run_id: str, repo_id: str, base_revision: str):
    """-> Workspace."""
    return await _resolve("app.sandbox", "create_workspace", "Dev 4")(
        run_id, repo_id, base_revision
    )


async def apply_patch(workspace, patch, allowed_paths: list[str]):
    """-> PatchAck."""
    return await _resolve("app.sandbox", "apply_patch", "Dev 4")(
        workspace, patch, allowed_paths
    )


async def verify_unit(run_id: str, unit, workspace, impacted: list, attempt: int):
    """-> VerificationResult."""
    return await _resolve("app.verification", "verify_unit", "Dev 4")(
        run_id, unit, workspace, impacted, attempt
    )


async def verify_final(run_id: str, workspace):
    """-> VerificationResult."""
    return await _resolve("app.verification", "verify_final", "Dev 4")(
        run_id, workspace
    )


async def accept_workspace(workspace) -> str:
    """-> accepted revision id."""
    return await _resolve("app.sandbox", "accept_workspace", "Dev 4")(workspace)


async def workspace_path(run_id: str, revision: str):
    """-> Path of an accepted revision's tree on the backend host.

    Needed to zip the final tree as the `archive` artefact. Dev 4: a small accessor
    over WORKSPACE_ROOT/runs/<run_id>/<revision>/ is enough.
    """
    return await _resolve("app.sandbox", "workspace_path", "Dev 4")(run_id, revision)


async def get_counterexamples(run_id: str, unit_id: str, *, for_model: bool) -> list:
    return await _resolve("app.verification", "get_counterexamples", "Dev 4")(
        run_id, unit_id, for_model=for_model
    )


async def list_verifications(run_id: str, unit_id: str | None = None) -> list:
    return await _resolve("app.verification", "list_verifications", "Dev 4")(
        run_id, unit_id
    )


async def expose_case(run_id: str, case_id: str) -> None:
    await _resolve("app.verification", "expose_case", "Dev 4")(run_id, case_id)


async def diff(run_id: str, base_revision: str, revision: str, paths=None) -> list:
    return await _resolve("app.sandbox", "diff", "Dev 4")(
        run_id, base_revision, revision, paths
    )


# --------------------------------------------------------------------------
# Dev 1 - the Argon agent
# --------------------------------------------------------------------------

async def propose_plan(context):
    """-> PlanProposal. Argon may only reorder within eligibility; see plans.py."""
    return await _resolve("app.agents", "propose_plan", "Dev 1")(context)


async def run_unit_session(context, controller):
    """-> UnitSessionOutcome. The model drives its own loop through the controller."""
    return await _resolve("app.agents", "run_unit_session", "Dev 1")(
        context, controller
    )


async def narrate_report(evidence: dict) -> str:
    """-> narrative string, grounded only in the evidence passed in. Optional."""
    return await _resolve("app.agents", "narrate_report", "Dev 1")(evidence)
