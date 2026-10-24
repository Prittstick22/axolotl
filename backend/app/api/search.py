"""Search API - owner: Dev 3. SHARED_CONTRACTS §6.

  GET  /repositories/{repo_id}/search  ?q=&mode=keyword|vector|hybrid&limit=10
       -> {mode_used, native_hybrid: bool, hits: SearchHit[]}
  POST /repositories/{repo_id}/index
       -> {chunks, described, embedded, warnings}
  GET  /repositories/{repo_id}/index
       -> {repository_id, state: indexing|ready|failed|null, embedded, total, ...}

`native_hybrid` is measured, not assumed: `$rankFusion` needs MongoDB 8.0+, and
claiming native fusion while a Python fallback actually ran would misrepresent how the
answer was produced.

A missing index is a 503 `SEARCH_INDEX_NOT_READY`, never an empty result list. "Nothing
matched" and "the index has not finished building" look identical to a UI otherwise,
and they are very different answers.
"""

from typing import Any, Literal

from fastapi import APIRouter, Query

from app import search
from app.errors import VALIDATION_ERROR, AppError
from app.persistence import graphstore
from app.persistence.routing import DatabaseAwareRoute
from app.search import progress
from app.search.pipeline import reindex_repository

# Atlas going away mid-request is a 503 DATABASE_UNAVAILABLE, never a bare 500 (§9).
router = APIRouter(tags=["search"], route_class=DatabaseAwareRoute)

MAX_QUERY_CHARS = 500


@router.get("/repositories/{repo_id}/search")
async def search_repository(
    repo_id: str,
    q: str = Query(..., min_length=1, description="Natural language or identifier"),
    mode: Literal["keyword", "vector", "hybrid"] = "hybrid",
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    # 404 before searching: an unknown repository returning zero hits reads as "no
    # matches" and sends the caller looking for a query problem that is not there.
    await graphstore.get_repository(repo_id)

    if len(q) > MAX_QUERY_CHARS:
        raise AppError(
            VALIDATION_ERROR,
            f"Query is longer than {MAX_QUERY_CHARS} characters",
            status_code=422,
        )

    return await search.search_repository(repo_id, q, mode=mode, limit=limit)


@router.post("/repositories/{repo_id}/index")
async def index_repository(repo_id: str) -> dict[str, Any]:
    """Re-chunk, re-describe and re-embed from the stored snapshot.

    Needed when `INDEX_ON_IMPORT` was off, when a Gemini key arrives after an import,
    when background indexing failed, or when the embedding model or dimension count
    changes. 409 `INDEXING_IN_PROGRESS` while the repository is already being indexed.
    """
    return await reindex_repository(repo_id)


@router.get("/repositories/{repo_id}/index")
async def index_status(repo_id: str) -> dict[str, Any]:
    """Search indexing progress for one repository (#28).

    `state` is `indexing`, `ready` or `failed`, with `embedded` of `total` chunks;
    null when no indexing was ever recorded (imported before #28, or with
    `INDEX_ON_IMPORT` off).
    """
    await graphstore.get_repository(repo_id)   # 404 for an unknown repository
    status = await progress.get(repo_id)
    return status or {"repository_id": repo_id, "state": None}
