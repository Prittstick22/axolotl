"""One model-written purpose sentence per module - owner: Dev 1. #39.

Written from the **legacy** source and shown on both sides: a migration that passed
verification preserves behaviour, so what a module is for does not change. The one
place prose costs a model call, and it is only made on request
(`POST /repositories/{id}/docs`), in the background, once per exact source.

Reuse before spending:
  1. a purpose already cached for this path and source hash;
  2. the description the search index already wrote for the module's chunk;
  3. only then `describe_chunks`, unchanged, which frames the code as untrusted data
     and handles pacing, retries and a spent quota. Its model is
     `GEMINI_DESCRIPTION_MODEL` (Flash), never the agent's Argon credit by design.

Test modules are never sent: they are skipped, so visible case inputs stay out of the
prompt, and held-out cases live outside the snapshot altogether. A failed call leaves
the purpose empty with a warning. Nothing is ever invented in its place.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.analysis.astwalk import ModuleAnalysis
from app.docs import store

log = logging.getLogger(__name__)

GENERATING, READY, PARTIAL, FAILED = "generating", "ready", "partial", "failed"
NOT_GENERATED = "not_generated"
# A status left at `generating` by a backend that stopped mid-run would otherwise
# block every later request for that repository.
STALE_AFTER = timedelta(minutes=15)
MODEL_GENERATED = "model_generated"

_background: set[asyncio.Task[None]] = set()


def source_hash(source: str) -> str:
    return hashlib.sha256(source.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def candidates(analyses: dict[str, ModuleAnalysis]) -> list[ModuleAnalysis]:
    """The modules a purpose is written for: non-test and not empty, sorted by path."""
    return sorted(
        (a for a in analyses.values() if not a.is_test and a.source.strip()),
        key=lambda a: a.path,
    )


async def purposes(repo_id: str, analyses: dict[str, ModuleAnalysis]) -> dict[str, dict]:
    """The cached purpose for each module whose source still matches, by path."""
    cached = await store.cached_purposes(repo_id)
    found = {}
    for analysis in analyses.values():
        hit = cached.get((analysis.path, source_hash(analysis.source)))
        if hit and hit.get("text"):
            found[analysis.path] = {k: hit[k] for k in
                                    ("text", "model", "origin", "generated_at", "basis")}
    return found


def status_view(status: dict[str, Any] | None) -> dict[str, Any]:
    if not status:
        return {"state": NOT_GENERATED, "done": 0, "total": 0, "model": None,
                "warnings": []}
    return {k: status.get(k) for k in
            ("state", "done", "total", "model", "warnings", "started_at", "finished_at")}


def is_active(status: dict[str, Any] | None) -> bool:
    if not status or status.get("state") != GENERATING:
        return False
    started = status.get("started_at")
    if isinstance(started, str):
        started = datetime.fromisoformat(started)
    if started is None:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return datetime.now(UTC) - started < STALE_AFTER


async def start(repo_id: str, analyses: dict[str, ModuleAnalysis], model: str) -> None:
    """Record the run as started, then generate in the background."""
    todo = candidates(analyses)
    await store.set_generation(
        repo_id, state=GENERATING, done=0, total=len(todo), model=model, warnings=[],
        started_at=datetime.now(UTC), finished_at=None,
    )
    task = asyncio.create_task(generate(repo_id, todo, model))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def generate(repo_id: str, todo: list[ModuleAnalysis], model: str) -> None:
    warnings: list[str] = []
    done = 0
    try:
        cached = await store.cached_purposes(repo_id)
        indexed = await store.module_descriptions(repo_id)
        missing: list[ModuleAnalysis] = []
        for analysis in todo:
            digest = source_hash(analysis.source)
            if (analysis.path, digest) in cached:
                done += 1
            elif analysis.path in indexed:
                await _save(repo_id, analysis, digest, indexed[analysis.path],
                            model="search index description", origin="search_index")
                done += 1
            else:
                missing.append(analysis)
        await store.set_generation(repo_id, done=done)

        if missing:
            from app.search.descriptions import describe_chunks

            chunks = [{"path": a.path, "qualified_name": a.dotted, "source": a.source}
                      for a in missing]
            warnings.extend(await describe_chunks(chunks))
            for analysis, chunk in zip(missing, chunks, strict=True):
                if chunk.get("description"):
                    await _save(repo_id, analysis, source_hash(analysis.source),
                                chunk["description"], model=model, origin="generated")
                    done += 1
    except Exception as exc:  # noqa: BLE001 - reported on the status, never raised
        log.warning("docs generation for %s failed: %s", repo_id, exc)
        warnings.append(f"generation stopped: {type(exc).__name__}: {str(exc)[:200]}")

    total = len(todo)
    state = READY if done == total else (PARTIAL if done else FAILED)
    await store.set_generation(repo_id, state=state, done=done, warnings=warnings[:20],
                               finished_at=datetime.now(UTC))
    log.info("docs for %s: %d/%d purposes (%s)", repo_id, done, total, state)


async def _save(repo_id: str, analysis: ModuleAnalysis, digest: str, text: str, *,
                model: str, origin: str) -> None:
    await store.save_purpose({
        "repository_id": repo_id, "path": analysis.path, "source_hash": digest,
        "text": text, "model": model, "origin": origin, "basis": MODEL_GENERATED,
        "generated_at": datetime.now(UTC).isoformat(),
    })
