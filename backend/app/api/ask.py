"""Ask API - owner: Dev 2. #123, additive under SHARED_CONTRACTS §11.

  POST /repositories/{repo_id}/ask

One question about one imported repository, answered from the repository's own code
through the five read-only agent tools. See `app/agents/ask.py` for why those five and
not the other five.

The response carries its own retrieval: which tools ran, what they were asked, which
engine answered and how many hits came back. That is the point of the endpoint as much
as the prose is - an answer whose evidence you cannot see is worth less than one you
can check, and the hybrid search doing the work is otherwise invisible.

`grounded` is false when nothing was retrieved. The answer then says it could not find
anything rather than reaching for general knowledge, which is the same rule the
verifier follows: an inconclusive result is never reported as a pass.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app import ingestion
from app.agents import ask as ask_agent
from app.agents.gemini_client import ModelUnavailable
from app.errors import MODEL_UNAVAILABLE, AppError
from app.persistence.routing import DatabaseAwareRoute

log = logging.getLogger(__name__)
router = APIRouter(tags=["ask"], route_class=DatabaseAwareRoute)

MAX_HISTORY = 20


class AskTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    # In the payload only: no conversation is stored, so there is no new collection
    # and nothing to leak between viewers.
    history: list[AskTurn] = Field(default_factory=list, max_length=MAX_HISTORY)


@router.post("/repositories/{repo_id}/ask")
async def ask_repository(repo_id: str, body: AskRequest) -> dict[str, Any]:
    # 404s on its own if the repository is not here, before any model call is paid for.
    await ingestion.get_repository(repo_id)

    try:
        result = await ask_agent.answer_question(
            repo_id,
            body.question,
            [turn.model_dump() for turn in body.history],
        )
    except ModelUnavailable as exc:
        # An unanswered question is a 503. Never an answer with nothing behind it.
        raise AppError(
            MODEL_UNAVAILABLE,
            f"The model could not answer: {exc}",
            status_code=503,
            details={"repository_id": repo_id},
        ) from exc

    return {
        "answer": result.answer,
        "grounded": result.grounded,
        "citations": [
            {
                "path": c.path,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "symbol": c.symbol,
                "snippet": c.snippet,
            }
            for c in result.citations
        ],
        "retrieval": [
            {
                "tool": r.tool,
                "query": r.query,
                "mode_used": r.mode_used,
                "native_hybrid": r.native_hybrid,
                "hit_count": r.hit_count,
            }
            for r in result.retrieval
        ],
        "model": result.model,
        "truncated": result.truncated,
    }
