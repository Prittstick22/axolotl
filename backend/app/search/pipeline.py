"""Indexing pipeline - owner: Dev 3. Spec §8.5.

Turns a persisted snapshot into searchable chunks, in three stages that fail
independently:

  1. build + persist chunks   pure, no network. Keyword search works the moment this
                              finishes, even with no API key at all.
  2. descriptions             one Gemini call per chunk. Failure costs semantic
                              quality, never the chunk.
  3. embeddings               one call per chunk. A chunk with no embedding simply
                              does not take part in vector search.

Stage 1 runs on every import, before the import responds. Stages 2 and 3 take about
two Gemini calls per chunk - minutes for chess's 122 chunks - so an import runs them
in the background (#28), in batches, with progress in `search_index_status` (see
`progress.py`). They are skipped when `INDEX_ON_IMPORT` is off, and can be run later
against a stored snapshot via `reindex_repository`.

Nothing here invents a description or a zero vector to fill a gap: a wrong
description makes search confidently misleading, and a zero vector sits at a fixed
distance from every query and quietly pollutes every result.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.errors import SNAPSHOT_UNAVAILABLE, AppError
from app.models.contracts import SymbolRecord
from app.search.chunking import build_chunks, embedding_text

log = logging.getLogger(__name__)

# Atlas Search is eventually consistent: freshly inserted documents are not searchable
# for a few seconds. Import waits for them, because "I imported it and search found
# nothing" is indistinguishable from a broken search.
SEARCHABLE_TIMEOUT_SECONDS = 45.0
SEARCHABLE_POLL_SECONDS = 1.5

# Background enrichment works through the chunks in batches, storing each one, so
# progress is real and a failure part-way keeps what was already done.
ENRICH_BATCH_SIZE = 20

# The running background tasks. asyncio keeps only weak references to tasks, so one
# held nowhere else can be garbage-collected mid-run.
_background: set[asyncio.Task[None]] = set()


async def index_repository(
    repository_id: str,
    symbols: list[SymbolRecord],
    sources_by_module: dict[str, str],
    *,
    enrich: bool = True,
    in_background: bool = False,
) -> dict[str, Any]:
    """-> {chunks, described, embedded, warnings}. Never raises.

    `in_background` stores the chunks, then schedules their descriptions and
    embeddings and returns at once (#28); `enrich` is then ignored.
    """
    from app.persistence import graphstore
    from app.search import persist_chunks

    warnings: list[str] = []
    chunks = build_chunks(symbols, sources_by_module)
    if not chunks:
        # This early return is also what stops `persist_chunks` below from deleting a
        # repository's existing chunks and replacing them with nothing. Keep it.
        return {"chunks": 0, "described": 0, "embedded": 0,
                "warnings": ["no indexable symbols, so nothing was chunked"]}

    if in_background:
        enrich = False     # scheduled below, once the chunks are stored
    elif enrich:
        warnings.extend(await _describe(chunks))
        warnings.extend(await _embed(chunks))
    else:
        warnings.append(
            "descriptions and embeddings were skipped (INDEX_ON_IMPORT is off), so "
            "only keyword search will work for this repository"
        )

    # Say which models produced each vector, so a later import of the same code can
    # reuse it (find_enrichment) instead of paying for it again.
    from app.search import chunk_tag

    for chunk in chunks:
        chunk["enriched_with"] = chunk_tag(chunk)

    try:
        await persist_chunks(repository_id, chunks)
    except Exception as exc:  # noqa: BLE001 - an un-indexed repository is still usable
        log.warning("could not persist chunks for %s: %s", repository_id, exc)
        return {"chunks": 0, "described": 0, "embedded": 0,
                "warnings": [*warnings, f"chunks were not persisted: {exc}"]}

    # SymbolRecord.source_chunk_id (§4): symbol -> its chunk, set only once the chunk
    # really is stored. Its own try/except, because it is a second write and failing
    # it does not un-store the chunks. Sharing the block above reported a failure here
    # as "chunks were not persisted" with `chunks: 0`, which would send the caller
    # re-running an index that had in fact succeeded.
    try:
        await graphstore.set_source_chunk_ids(
            repository_id, {c["symbol_id"]: c["chunk_id"] for c in chunks}
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("could not link symbols to chunks for %s: %s", repository_id, exc)
        warnings.append(
            f"the chunks were stored, but symbols were not linked back to them "
            f"({exc}); search works, `SymbolRecord.source_chunk_id` is stale"
        )

    if not await wait_until_searchable(repository_id):
        warnings.append(
            "the chunks were stored but are not searchable yet; Atlas Search indexes "
            "new documents a few seconds behind the write, so a search made now may "
            "return nothing"
        )

    if in_background:
        warnings.extend(await schedule_enrichment(repository_id, chunks))

    described = sum(1 for c in chunks if c.get("description"))
    embedded = sum(1 for c in chunks if c.get("embedding"))
    log.info(
        "indexed %s: %d chunk(s), %d described, %d embedded",
        repository_id, len(chunks), described, embedded,
    )
    return {"chunks": len(chunks), "described": described,
            "embedded": embedded, "warnings": warnings}


# --------------------------------------------------------------------------
# background enrichment (#28)
# --------------------------------------------------------------------------

async def schedule_enrichment(
    repository_id: str, chunks: list[dict[str, Any]]
) -> list[str]:
    """Record `indexing` and start describing and embedding stored chunks.

    The status is written before this returns, so a search made the moment the import
    responds already gets the 503 with progress, not an empty result.
    """
    from app.search import progress

    if not progress.claim(repository_id):
        return ["this repository is already being indexed"]
    try:
        await progress.begin(repository_id, total=len(chunks))
    except Exception as exc:  # noqa: BLE001 - keyword search still works
        progress.release(repository_id)
        log.warning("could not record indexing status for %s: %s", repository_id, exc)
        return [f"descriptions and embeddings were not started: the indexing status "
                f"could not be stored ({exc}); POST /repositories/{{id}}/index "
                f"adds them"]

    task = asyncio.create_task(
        _enrich(repository_id, chunks), name=f"enrich-{repository_id}"
    )
    _background.add(task)
    task.add_done_callback(_background.discard)
    return []


async def _enrich(repository_id: str, chunks: list[dict[str, Any]]) -> None:
    """Describe and embed in batches. Always ends `ready` or `failed`."""
    from app.search import progress, store_enrichment
    from app.search.settings import search_settings

    warnings: list[str] = []
    # Descriptions are one model call per chunk. Off by default at import (#11):
    # chunks embed from their source and identifiers, and POST .../index adds them.
    describing = search_settings().index_descriptions
    if not describing:
        warnings.append("descriptions were not generated at import (INDEX_DESCRIPTIONS "
                        "is off); POST /repositories/{id}/index adds them")
    stopped: str | None = None
    reused = described = embedded = 0

    try:
        reused = await _reuse_enrichment(repository_id, chunks,
                                         need_description=describing)
        described = sum(1 for c in chunks if c.get("description"))
        embedded = reused
        if reused:
            await progress.advance(repository_id, described=described, embedded=embedded)
        pending = [c for c in chunks if not c.get("embedding")]

        for start in range(0, len(pending), ENRICH_BATCH_SIZE):
            batch = pending[start:start + ENRICH_BATCH_SIZE]

            if describing:
                warnings.extend(await _describe(batch))
                batch_described = sum(1 for c in batch if c.get("description"))
                # No key, or the quota is gone: every later batch would fail the same
                # way, and each failure is a request against the quota (#11).
                if batch_described == 0:
                    describing = False
                described += batch_described

            warnings.extend(await _embed(batch))
            batch_embedded = sum(1 for c in batch if c.get("embedding"))
            embedded += batch_embedded

            await store_enrichment(repository_id, batch)
            await progress.advance(repository_id, described=described,
                                   embedded=embedded)

            if batch_embedded == 0:
                stopped = (f"embedding stopped after {embedded} of {len(chunks)} "
                           f"chunk(s): {warnings[-1] if warnings else 'no vectors'}")
                break

        if embedded and not await wait_until_vector_searchable(repository_id):
            warnings.append("the embeddings were stored but vector search had not "
                            "caught up within the wait; results may lag briefly")

        if embedded == 0:
            await progress.finish(repository_id, progress.FAILED, described=described,
                                  embedded=0, reason=stopped or "no chunk was embedded",
                                  warnings=warnings)
        else:
            if stopped:
                warnings.append(stopped)
            await progress.finish(repository_id, progress.READY, described=described,
                                  embedded=embedded, warnings=warnings)
        log.info("background indexing of %s: %d described, %d embedded of %d "
                 "(%d reused)", repository_id, described, embedded, len(chunks), reused)
    except asyncio.CancelledError:
        # Server shutting down. Best effort: the database may already be closed, and
        # if so the next reader on this host marks it failed instead.
        await _finish_quietly(repository_id, described, embedded,
                              "indexing was cancelled because the server stopped")
        raise
    except Exception as exc:  # noqa: BLE001 - recorded, never silent
        log.warning("background indexing of %s failed: %s", repository_id, exc)
        await _finish_quietly(repository_id, described, embedded,
                              f"{type(exc).__name__}: {exc}")
    finally:
        progress.release(repository_id)


async def _reuse_enrichment(
    repository_id: str, chunks: list[dict[str, Any]], *, need_description: bool = True
) -> int:
    """Fill in what Atlas already holds for identical code; returns how many (#11)."""
    from app.search import find_enrichment, store_enrichment

    try:
        found = await find_enrichment([c["chunk_id"] for c in chunks],
                                      need_description=need_description)
    except Exception as exc:  # noqa: BLE001 - only costs quota, never correctness
        log.warning("could not look up earlier enrichment for %s: %s", repository_id, exc)
        return 0

    reused = [c for c in chunks if c["chunk_id"] in found]
    for chunk in reused:
        chunk["description"] = found[chunk["chunk_id"]]["description"]
        chunk["embedding"] = found[chunk["chunk_id"]]["embedding"]
    if reused:
        await store_enrichment(repository_id, reused)
        log.info("reused stored descriptions and embeddings for %d of %d chunk(s)",
                 len(reused), len(chunks))
    return len(reused)


async def _finish_quietly(
    repository_id: str, described: int, embedded: int, reason: str
) -> None:
    from app.search import progress

    try:
        await progress.finish(repository_id, progress.FAILED, described=described,
                              embedded=embedded, reason=reason)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not record failed indexing of %s: %s", repository_id, exc)


async def wait_until_vector_searchable(
    repository_id: str, *, timeout: float = SEARCHABLE_TIMEOUT_SECONDS
) -> bool:
    """Poll the vector index until this repository's embeddings appear in it.

    The query vector is one of the repository's own stored embeddings, so this never
    calls Gemini. False on timeout, or when the vector index is not ready at all.
    """
    from app.persistence import db
    from app.search.indexes import CHUNKS_COLLECTION, VECTOR_INDEX, search_status

    if (await search_status())["vector"] != "ok":
        return False

    database = await db.get_db()
    collection = database[CHUNKS_COLLECTION]
    sample = await collection.find_one(
        {"repository_id": repository_id, "embedding": {"$type": "array"}},
        {"_id": 0, "embedding": 1},
    )
    if sample is None:
        return False

    pipeline = [
        {"$vectorSearch": {
            "index": VECTOR_INDEX, "path": "embedding",
            "queryVector": sample["embedding"],
            "filter": {"repository_id": repository_id},
            "numCandidates": 10, "limit": 1,
        }},
        {"$project": {"_id": 0, "chunk_id": 1}},
    ]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            if [row async for row in await collection.aggregate(pipeline)]:
                return True
        except Exception as exc:  # noqa: BLE001 - index may still be catching up
            log.info("waiting for the vector index: %s", exc)
        await asyncio.sleep(SEARCHABLE_POLL_SECONDS)
    return False


async def _describe(chunks: list[dict[str, Any]]) -> list[str]:
    from app.search.descriptions import describe_chunks

    try:
        return await describe_chunks(chunks)
    except Exception as exc:  # noqa: BLE001
        log.warning("describing chunks failed: %s", exc)
        return [f"chunk descriptions failed ({type(exc).__name__})"]


async def _embed(chunks: list[dict[str, Any]]) -> list[str]:
    from app.search.embeddings import embed_documents

    failures: list[str] = []
    try:
        vectors = await embed_documents([embedding_text(c) for c in chunks],
                                        failures=failures)
    except Exception as exc:  # noqa: BLE001
        log.warning("embedding chunks failed: %s", exc)
        return [f"chunk embeddings failed ({type(exc).__name__}), so vector and "
                f"hybrid search will return nothing for this repository"]

    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk["embedding"] = vector

    # The first error says why - a spent quota reads very differently from a bad key.
    why = f" ({failures[0]})" if failures else ""
    missing = sum(1 for v in vectors if v is None)
    if missing == len(vectors):
        return [f"no chunk was embedded{why}, so vector and hybrid search will return "
                "nothing for this repository"]
    if missing:
        return [f"{missing} of {len(vectors)} chunk(s) could not be embedded{why} and "
                f"are absent from vector search"]
    return []


async def reindex_repository(
    repository_id: str, *, enrich: bool = True
) -> dict[str, Any]:
    """Re-run the whole pipeline from the stored snapshot and symbols.

    `enrich` defaults to True and ignores `INDEX_ON_IMPORT`: asking for a re-index
    explicitly means do the work, whatever the import default is. Tests pass False to
    stay off the network.

    Used by POST /repositories/{repo_id}/index - after an import with enrichment off,
    after a key becomes available, or after the embedding model changes.
    """
    from app.ingestion import snapshot_path
    from app.persistence import graphstore

    await graphstore.get_repository(repository_id)   # 404s if unknown
    symbols = await graphstore.list_symbols(repository_id)
    root = snapshot_path(repository_id)

    # The snapshot is on local disk, not in Atlas, so a repository imported on another
    # machine cannot be re-indexed here. Saying so beats the alternative: every source
    # read fails, `build_chunks` sees no sources and returns nothing, and the caller
    # gets a 200 reading `chunks=0, "no indexable symbols"` - which is wrong twice
    # over, because the symbols are all present and the repository is fine.
    if symbols and not root.is_dir():
        raise AppError(
            SNAPSHOT_UNAVAILABLE,
            "This repository has no snapshot on this machine, so it cannot be "
            "re-indexed here. Snapshots are local to whoever imported the "
            "repository; import it again on this machine to index it.",
            status_code=409,
            details={"repository_id": repository_id, "expected_path": str(root),
                     "symbols": len(symbols)},
        )

    # Sources come from the snapshot, which is immutable, so a re-index describes the
    # same code the symbols were extracted from.
    sources: dict[str, str] = {}
    for symbol in symbols:
        if symbol.module_id in sources:
            continue
        path = root / symbol.path
        if not path.is_file():
            continue
        try:
            sources[symbol.module_id] = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("could not read %s: %s", path, exc)

    if not enrich:
        return await index_repository(repository_id, symbols, sources, enrich=False)
    await _refuse_if_indexing(repository_id)
    return await _tracked_reindex(repository_id, symbols, sources)


async def _refuse_if_indexing(repository_id: str) -> None:
    from app.search import INDEXING_IN_PROGRESS, progress

    status = await progress.get(repository_id)
    if (status and status["state"] == progress.INDEXING) or \
            progress.is_active(repository_id):
        raise AppError(
            INDEXING_IN_PROGRESS,
            "This repository is already being indexed. Indexing it again now would "
            "spend the Gemini quota twice on the same chunks; wait for it to finish.",
            status_code=409,
            details={"repository_id": repository_id,
                     "embedded": (status or {}).get("embedded"),
                     "total": (status or {}).get("total")},
        )


async def _tracked_reindex(
    repository_id: str, symbols: list[SymbolRecord], sources: dict[str, str]
) -> dict[str, Any]:
    """A synchronous re-index, recorded in the indexing status like a background one."""
    from app.search import progress

    if not progress.claim(repository_id):
        await _refuse_if_indexing(repository_id)
    try:
        await progress.begin(repository_id, total=None)
        result = await index_repository(repository_id, symbols, sources, enrich=True)
        embedded = result["embedded"]
        await progress.finish(
            repository_id, progress.READY if embedded else progress.FAILED,
            total=result["chunks"], described=result["described"], embedded=embedded,
            reason=(None if embedded else
                    "no chunk was embedded" if result["chunks"] else
                    "no indexable symbols, so there was nothing to embed"),
            warnings=result["warnings"],
        )
        return result
    except BaseException as exc:
        await _finish_quietly(repository_id, 0, 0, f"{type(exc).__name__}: {exc}")
        raise
    finally:
        progress.release(repository_id)


async def wait_until_searchable(
    repository_id: str, *, timeout: float = SEARCHABLE_TIMEOUT_SECONDS
) -> bool:
    """Poll the text index until this repository's chunks appear in it.

    Returns False on timeout rather than raising: the chunks are stored either way,
    and the caller records that search may lag rather than failing the import.
    """
    from app.persistence import db
    from app.search.indexes import CHUNKS_COLLECTION, TEXT_INDEX

    pipeline = [
        {"$search": {
            "index": TEXT_INDEX,
            "equals": {"path": "repository_id", "value": repository_id},
        }},
        {"$limit": 1},
        {"$project": {"_id": 0, "chunk_id": 1}},
    ]

    database = await db.get_db()
    collection = database[CHUNKS_COLLECTION]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while loop.time() < deadline:
        try:
            rows = [row async for row in await collection.aggregate(pipeline)]
        except Exception as exc:  # noqa: BLE001 - index may still be building
            log.info("waiting for the text index: %s", exc)
            rows = []
        if rows:
            return True
        await asyncio.sleep(SEARCHABLE_POLL_SECONDS)

    log.warning("chunks for %s were not searchable within %.0fs",
                repository_id, timeout)
    return False
