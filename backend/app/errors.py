"""Error envelope - owner: Dev 2. SHARED_CONTRACTS §6.

Every error response is `{"error": {"code": "SNAKE_CASE", "message": "...", "details": {}}}`
with a correct HTTP status code.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Raise this anywhere in the backend to produce the §6 error envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


# --- codes named by SHARED_CONTRACTS §6 / §9 ---
DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
UNSUPPORTED_TARGET = "UNSUPPORTED_TARGET"
REPOSITORY_NOT_EXECUTABLE = "REPOSITORY_NOT_EXECUTABLE"
NOT_FOUND = "NOT_FOUND"
VALIDATION_ERROR = "VALIDATION_ERROR"
INTERNAL_ERROR = "INTERNAL_ERROR"

# --- codes §6 describes by status only, named here by Dev 2 ---
# §6 says "409 if plan not approved" and "404 until generated" without giving codes,
# and names none for unknown ids. These are additive, so they merge immediately under
# SHARED_CONTRACTS §11; tell owners if you add more.
PLAN_NOT_FOUND = "PLAN_NOT_FOUND"
RUN_NOT_FOUND = "RUN_NOT_FOUND"
ARTEFACT_NOT_FOUND = "ARTEFACT_NOT_FOUND"
PLAN_NOT_APPROVED = "PLAN_NOT_APPROVED"
PLAN_ALREADY_DECIDED = "PLAN_ALREADY_DECIDED"
REPORT_NOT_READY = "REPORT_NOT_READY"
RUN_ALREADY_FINISHED = "RUN_ALREADY_FINISHED"
# One active run per repository (#18). Not per plan: the hazard is two Argon sessions
# migrating the same snapshot at once, and a second plan on the same repository does
# that just as well as a second click on one plan.
RUN_ALREADY_ACTIVE = "RUN_ALREADY_ACTIVE"
INTEGRATION_UNAVAILABLE = "INTEGRATION_UNAVAILABLE"
# Re-indexing reads sources from the on-disk snapshot, which is **machine-local**: a
# repository imported on someone else's laptop has no snapshot here. Distinct from
# NOT_FOUND, which would say the repository does not exist - it does, in Atlas.
SNAPSHOT_UNAVAILABLE = "SNAPSHOT_UNAVAILABLE"
# The model was called and failed: a spent quota, a rate limit that outlasted the
# client's wait, a dropped connection. Distinct from INTEGRATION_UNAVAILABLE, which
# means the agent is not wired up at all (#47).
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
# An interpreter image the plan's target needs cannot run here (#95). Checked before
# the run exists, so no Argon call is spent on a candidate nothing could verify.
RUNNER_UNAVAILABLE = "RUNNER_UNAVAILABLE"
# Module docs are already being generated for this repository (#39). Its progress is
# on GET /repositories/{id}/docs; a second start would pay for the same calls twice.
DOCS_GENERATION_ACTIVE = "DOCS_GENERATION_ACTIVE"


def _envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def database_unavailable(detail: str = "MongoDB is unreachable") -> AppError:
    """§9: if MongoDB is unreachable, endpoints return 503. No in-memory fallback."""
    return AppError(DATABASE_UNAVAILABLE, detail, status_code=503)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = NOT_FOUND if exc.status_code == 404 else "HTTP_ERROR"
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(code, str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_envelope(
                VALIDATION_ERROR,
                "Request validation failed",
                {"errors": exc.errors()},
            ),
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        """Anything unhandled still gets the §6 envelope.

        Without this, a reachable-but-failing Atlas (an auth error, a timeout mid
        query) returns a bare FastAPI 500 that clients cannot parse, having been
        told every error has the same shape. The message is the exception type
        only - never its text, which can carry a connection string.
        """
        logging.getLogger("axolotl").exception("unhandled error")
        return JSONResponse(
            status_code=500,
            content=_envelope(
                INTERNAL_ERROR,
                f"Unexpected {type(exc).__name__}",
                {},
            ),
        )
