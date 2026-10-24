"""FastAPI application - owner: Dev 2. SHARED_CONTRACTS §2, §6.

Boot:  cd backend && uv run uvicorn app.main:app --reload --port 8000
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import ask, docs, plans_runs, repositories, search, system, targets
from app.config import get_settings
from app.errors import register_error_handlers
from app.persistence import db

API_PREFIX = "/api/v1"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("axolotl")


async def _startup_with_database() -> None:
    """Index creation and restart handling - skipped cleanly when Atlas is unreachable.

    The app must still boot without a database so `/system/status` can report what is
    wrong. It just cannot do any of this.
    """
    from app.orchestration import events as run_events
    from app.orchestration import store, worker
    from app.persistence import indexes

    try:
        await store.ensure_indexes()
        await run_events.ensure_indexes()
        await indexes.ensure_indexes()
        # Dev 4's verification collections.
        from app.verification import store as verification_store

        await verification_store.ensure_indexes()
    except Exception as exc:  # noqa: BLE001 - reported by /system/status
        log.warning("skipping index creation, database unavailable: %s", exc)
        return

    try:
        # Dev 3's Atlas Search and Vector indexes (§9). Creating them returns at once
        # and the build is asynchronous, so this does not hold up boot; readiness is
        # reported by /system/status and by the search endpoints.
        from app.search.indexes import ensure_search_indexes

        states = await ensure_search_indexes()
        log.info("search indexes: keyword=%s vector=%s",
                 states["keyword"], states["vector"])
    except Exception as exc:  # noqa: BLE001 - reported by /system/status
        log.warning("could not ensure search indexes: %s", exc)

    try:
        # A run left mid-flight did not survive the restart. Say so rather than
        # pretending it can be resumed (Dev 2 brief).
        interrupted = await worker.mark_interrupted_runs()
        if interrupted:
            log.warning("marked %d interrupted run(s) failed", len(interrupted))
    except Exception as exc:  # noqa: BLE001
        log.warning("could not check for interrupted runs: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await db.connect(settings)

    await _startup_with_database()

    log.info("Axolotl backend ready: model=%s runner=%s", settings.gemini_model,
             settings.runner_mode)
    try:
        yield
    finally:
        await db.disconnect()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Axolotl",
        description=(
            "Agentic legacy-code migration with verified behaviour preservation. "
            "The verifier is the only source of pass/fail; the model never decides success."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # §6: CORS allows only FRONTEND_ORIGIN.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    v1 = APIRouter(prefix=API_PREFIX)

    @v1.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """Liveness only - no dependency checks. Use /system/status for those."""
        return {"status": "ok"}

    # One router file per owner so nobody fights over merges (SHARED_CONTRACTS §2).
    v1.include_router(ask.router)
    v1.include_router(system.router)
    v1.include_router(repositories.router)
    v1.include_router(search.router)
    v1.include_router(targets.router)
    v1.include_router(plans_runs.router)
    v1.include_router(docs.router)

    app.include_router(v1)
    return app


app = create_app()
