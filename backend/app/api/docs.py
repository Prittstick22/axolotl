"""Module documentation API - owner: Dev 1. #39.

  GET  /repositories/{repo_id}/docs   legacy docs, no model call
  GET  /runs/{run_id}/docs            legacy and migrated docs, no model call
  POST /repositories/{repo_id}/docs   202: generate the missing purposes in the
                                      background, one Flash call per module at most

Every module carries `facts` (from the parse tree), a `purpose` that is model-generated
and labelled, and the same content as `markdown`. See app/docs/__init__.py.
"""

from typing import Any

from fastapi import APIRouter

from app import docs
from app.persistence.routing import DatabaseAwareRoute

# Atlas going away mid-request is a 503 DATABASE_UNAVAILABLE, never a bare 500 (§9).
router = APIRouter(tags=["docs"], route_class=DatabaseAwareRoute)


@router.get("/repositories/{repo_id}/docs")
async def get_repository_docs(repo_id: str) -> dict[str, Any]:
    """Facts for every module of the legacy code, and any purposes already generated.

    409 `SNAPSHOT_UNAVAILABLE` when the snapshot isn't on this machine.
    """
    return await docs.repository_docs(repo_id)


@router.get("/runs/{run_id}/docs")
async def get_run_docs(run_id: str) -> dict[str, Any]:
    """Legacy and migrated docs for each module, against the run's last accepted
    revision: facts, Argon's summary of the accepted patch, the unit's verdict and
    the module's dependency changes. 409 when either tree isn't on this machine."""
    return await docs.run_docs(run_id)


@router.post("/repositories/{repo_id}/docs", status_code=202)
async def generate_repository_docs(repo_id: str) -> dict[str, Any]:
    """Start writing the missing purposes. Reuses cached ones and the search index's
    descriptions first, so a second request spends nothing. 409
    `DOCS_GENERATION_ACTIVE` while a generation is running; 503 `MODEL_UNAVAILABLE`
    with no Gemini key."""
    return await docs.start_generation(repo_id)
