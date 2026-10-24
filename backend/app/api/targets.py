"""Migration targets API - owner: Dev 2. SHARED_CONTRACTS §6.

  GET /migration-targets -> MigrationTarget[]

Only `modern_python3` is supported. The rest are returned with `supported=false` and a
reason so the UI can show them disabled with an explanation rather than hiding them -
asking for one is a 422 UNSUPPORTED_TARGET (T-08). Support wording follows Spec §3.2.

Each `unsupported_reason` is **one short line**. Dev 5 found the longer ones took two
to four lines each on the Plan screen at 1280x720 and pushed the target column below
the fold, so the reader had to scroll to see the supported target.

`target_python` lets the Plan screen say "Python 2.7 (declared) -> Python 3.14
(target)" without hardcoding a version in the page. The declared side comes from the
repository's own runtime metadata, so only the target lives here.
"""

from fastapi import APIRouter

from app.errors import UNSUPPORTED_TARGET, AppError
from app.models.contracts import MigrationTarget

router = APIRouter(tags=["targets"])

MIGRATION_TARGETS: list[MigrationTarget] = [
    MigrationTarget(
        target_id="modern_python3",
        label="Modern Python 3",
        supported=True,
        description=(
            "Legacy-style Python 3 to modern typed Python 3: same public behaviour, "
            "cleaner structure, type hints, dataclasses where safe."
        ),
        target_python="3.12",   # what the runner image provides today
    ),
    MigrationTarget(
        target_id="fastapi",
        label="Flask to FastAPI",
        supported=False,
        description="Port a Flask-style web layer to FastAPI.",
        unsupported_reason="Stretch goal: not implemented in the MVP.",
    ),
    MigrationTarget(
        target_id="async_python",
        label="Sync to async Python",
        supported=False,
        description="Convert synchronous call paths to async/await.",
        unsupported_reason="Not in the MVP: async changes observable behaviour.",
    ),
    MigrationTarget(
        target_id="python_to_typescript",
        label="Python to TypeScript",
        supported=False,
        description="Translate Python modules to TypeScript.",
        unsupported_reason="Not in the MVP: no shared runtime to compare in.",
    ),
    MigrationTarget(
        target_id="python2_to_3",
        label="Python 2 to Python 3",
        supported=False,
        description="Upgrade a Python 2 codebase to Python 3.14.",
        # Flip `supported` to True once #22 (a sandbox that runs 2.7 and 3.14) and #23
        # (chess behaviour cases) land. Marking it supported sooner would move the
        # failure from a clear 422 at planning time into a baseline that cannot run,
        # which is a worse place to discover it - mid-demo.
        unsupported_reason="Not in the MVP yet: needs Python 2.7 in the sandbox.",
        target_python="3.14",
    ),
]

_BY_ID = {target.target_id: target for target in MIGRATION_TARGETS}


@router.get("/migration-targets")
async def list_migration_targets() -> list[MigrationTarget]:
    return MIGRATION_TARGETS


def require_supported_target(target_id: str) -> MigrationTarget:
    """Resolve a target or raise 422 UNSUPPORTED_TARGET (T-08).

    Used by `POST /repositories/{repo_id}/plans`. An unknown id and a known but
    unsupported id both fail the same way, so nothing can start a run on a target the
    verifier cannot handle.
    """
    target = _BY_ID.get(target_id)
    if target is None:
        raise AppError(
            UNSUPPORTED_TARGET,
            f"Unknown migration target {target_id!r}",
            status_code=422,
            details={"target_id": target_id, "supported": ["modern_python3"]},
        )
    if not target.supported:
        raise AppError(
            UNSUPPORTED_TARGET,
            f"Migration target {target_id!r} is not supported",
            status_code=422,
            details={
                "target_id": target_id,
                "reason": target.unsupported_reason,
                "supported": ["modern_python3"],
            },
        )
    return target
