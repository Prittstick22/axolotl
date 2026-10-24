"""Search - owner: Dev 3. Spec §8.5-§8.7, SHARED_CONTRACTS §8.

Three modes over `code_chunks`:

  keyword  Atlas Search `$search` - exact identifiers and error strings (T-06)
  vector   `$vectorSearch` - meaning, so "customer payment rounding" finds
           `finalise_amount` though it shares no word with it (T-07)
  hybrid   native `$rankFusion` when the cluster supports it, otherwise reciprocal
           rank fusion in Python

`native_hybrid` in the response says which of those two actually ran. Claiming native
fusion while running the fallback would be a quiet lie about how the result was
produced, so it is measured, not assumed.

Embeddings are never returned: 768 floats per hit would dwarf the payload and are of
no use to a caller.
"""

from __future__ import annotations

import logging
from typing import Any

from app.errors import AppError
from app.models.contracts import SearchHit
from app.search.embeddings import EmbeddingUnavailable, embed_one
from app.search.indexes import (
    CHUNKS_COLLECTION,
    TEXT_INDEX,
    VECTOR_INDEX,
    search_status,
    supports_native_hybrid,
)

log = logging.getLogger(__name__)

__all__ = ["find_enrichment", "persist_chunks", "search_repository", "search_status",
           "store_enrichment"]

SEARCH_INDEX_NOT_READY = "SEARCH_INDEX_NOT_READY"
# A second index of a repository already being indexed would spend the Gemini quota
# twice for the same chunks (#11).
INDEXING_IN_PROGRESS = "INDEXING_IN_PROGRESS"
RRF_K = 60   # standard reciprocal-rank-fusion constant
PHRASE_BOOST = 8

# Never sent to a client.
HIDDEN_FIELDS = {"_id": 0, "embedding": 0}


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

async def persist_chunks(repo_id: str, chunks: list[dict[str, Any]]) -> int:
    """Replace this repository's chunks. Called once at import."""
    from app.persistence import db

    database = await db.get_db()
    await database[CHUNKS_COLLECTION].delete_many({"repository_id": repo_id})
    if chunks:
        await database[CHUNKS_COLLECTION].insert_many(chunks)
    return len(chunks)


async def store_enrichment(repo_id: str, chunks: list[dict[str, Any]]) -> int:
    """Add descriptions and embeddings to chunks that are already stored.

    An update in place, not `persist_chunks`: that deletes before it inserts, and
    keyword search must keep finding these chunks while they are being enriched.
    """
    from pymongo import UpdateOne

    from app.persistence import db

    if not chunks:
        return 0
    tag = enrichment_tag()
    database = await db.get_db()
    result = await database[CHUNKS_COLLECTION].bulk_write([
        UpdateOne(
            {"repository_id": repo_id, "chunk_id": c["chunk_id"]},
            {"$set": {"description": c.get("description"),
                      "embedding": c.get("embedding"),
                      "enriched_with": tag if c.get("embedding") else None}},
        )
        for c in chunks
    ], ordered=False)
    return result.modified_count


def enrichment_tag() -> str:
    """Which models produced a chunk's description and embedding, and at what size."""
    from app.config import get_settings

    s = get_settings()
    return f"{s.gemini_model}|{s.gemini_embedding_model}:{s.embedding_dimensions}"


async def find_enrichment(chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Descriptions and embeddings already stored for these chunks, by any repository.

    Quota (#11): re-importing chess on demo day would otherwise pay for 244 Gemini
    calls to get answers Atlas already holds. A chunk id is derived from the snapshot
    hash and the symbol, so the same id means byte-identical code; `enriched_with`
    must also match, so a change of model or dimensions is never served stale vectors.
    These are the models' real earlier answers for exactly this code.
    """
    from app.persistence import db

    if not chunk_ids:
        return {}
    database = await db.get_db()
    cursor = database[CHUNKS_COLLECTION].find(
        {"chunk_id": {"$in": chunk_ids}, "enriched_with": enrichment_tag(),
         "embedding": {"$type": "array"}, "description": {"$type": "string"}},
        {"_id": 0, "chunk_id": 1, "description": 1, "embedding": 1},
    )
    found: dict[str, dict[str, Any]] = {}
    async for row in cursor:
        found.setdefault(row["chunk_id"], row)
    return found


async def count_chunks(repo_id: str) -> int:
    from app.persistence import db

    database = await db.get_db()
    return int(await database[CHUNKS_COLLECTION].count_documents(
        {"repository_id": repo_id}
    ))


# --------------------------------------------------------------------------
# pipelines
# --------------------------------------------------------------------------

def _keyword_stage(repo_id: str, query: str, limit: int) -> list[dict[str, Any]]:
    """Boosted so an exact identifier beats an incidental mention in a body."""
    return [
        {
            "$search": {
                "index": TEXT_INDEX,
                "compound": {
                    "filter": [
                        {"equals": {"path": "repository_id", "value": repo_id}}
                    ],
                    "should": [
                        # Whole dotted name, one token: the most precise match there
                        # is, so it outranks everything.
                        {"text": {"query": query,
                                  "path": {"value": "qualified_name",
                                           "multi": "exact"},
                                  "score": {"boost": {"value": 10}}}},
                        # Split into words, so `finalise_amount` - or just
                        # `finalise` - finds `pricing.finalise_amount` (T-06).
                        {"text": {"query": query, "path": "qualified_name",
                                  "score": {"boost": {"value": 6}}}},
                        # The query as an exact phrase in the body: an error string
                        # ("quantity must not be negative") should find the function
                        # that raises it ahead of a test whose *name* merely shares
                        # two of its words.
                        {"phrase": {"query": query, "path": "source",
                                    "score": {"boost": {"value": PHRASE_BOOST}}}},
                        {"text": {"query": query, "path": "identifiers",
                                  "score": {"boost": {"value": 4}}}},
                        {"text": {"query": query, "path": "description",
                                  "score": {"boost": {"value": 3}}}},
                        {"text": {"query": query, "path": "docstring",
                                  "score": {"boost": {"value": 2}}}},
                        {"text": {"query": query, "path": "source"}},
                    ],
                    "minimumShouldMatch": 1,
                },
            }
        },
        {"$limit": limit},
    ]


def _vector_stage(
    repo_id: str, vector: list[float], limit: int
) -> list[dict[str, Any]]:
    return [
        {
            "$vectorSearch": {
                "index": VECTOR_INDEX,
                "path": "embedding",
                "queryVector": vector,
                # Filtered inside the stage so one repository cannot crowd out another.
                "filter": {"repository_id": repo_id},
                "numCandidates": max(limit * 15, 100),
                "limit": limit,
            }
        }
    ]


# --------------------------------------------------------------------------
# the public entry point
# --------------------------------------------------------------------------

async def search_repository(
    repo_id: str, query: str, mode: str = "hybrid", limit: int = 8
) -> dict[str, Any]:
    """-> {mode_used, native_hybrid, hits}.

    Returns a dict rather than the bare `list[SearchHit]` of §8 because §6's response
    carries `mode_used` and `native_hybrid` alongside the hits, and the caller must be
    able to tell the user which engine answered.
    """
    from app.persistence import db

    query = (query or "").strip()
    if not query:
        raise AppError("VALIDATION_ERROR", "A search query is required", 422)

    limit = max(1, min(limit, 50))
    status = await search_status()
    database = await db.get_db()
    collection = database[CHUNKS_COLLECTION]

    if mode == "keyword":
        _require(status["keyword"], "keyword")
        hits = await _keyword(collection, repo_id, query, limit)
        return {"mode_used": "keyword", "native_hybrid": False, "hits": hits}

    # Keyword search works from the moment the chunks are stored. Vector and hybrid
    # need this repository's embeddings, which may still be on their way (#28).
    await _require_repository_indexed(repo_id)

    if mode == "vector":
        _require(status["vector"], "vector")
        hits = await _vector(collection, repo_id, query, limit)
        return {"mode_used": "vector", "native_hybrid": False, "hits": hits}

    return await _hybrid(collection, repo_id, query, limit, status)


def _require(state: str, which: str) -> None:
    if state == "ok":
        return
    if state == "building":
        raise AppError(
            SEARCH_INDEX_NOT_READY,
            f"The {which} index is still building. This is not an empty result - "
            "try again shortly.",
            status_code=503, details={"index": which, "state": state},
        )
    raise AppError(
        SEARCH_INDEX_NOT_READY,
        f"The {which} index does not exist yet.",
        status_code=503, details={"index": which, "state": state},
    )


async def _require_repository_indexed(repo_id: str) -> None:
    """503 while this repository's embeddings are missing, never an empty result.

    No status document means the repository was never indexed in the background (it
    predates #28, or `INDEX_ON_IMPORT` was off), and search behaves as it always did.
    """
    from app.search import progress

    status = await progress.get(repo_id)
    if status is None or status["state"] == progress.READY:
        return

    details = {
        "repository_id": repo_id, "state": status["state"],
        "embedded": status.get("embedded", 0), "total": status.get("total"),
    }
    if status["state"] == progress.INDEXING:
        total = status.get("total")
        of_total = f" of {total}" if total is not None else ""
        raise AppError(
            SEARCH_INDEX_NOT_READY,
            f"This repository is still being indexed: {details['embedded']}{of_total} "
            "chunk(s) embedded. Keyword search works now; vector and hybrid search "
            "will once indexing finishes.",
            status_code=503, details=details,
        )
    raise AppError(
        SEARCH_INDEX_NOT_READY,
        f"Indexing this repository failed ({status.get('reason')}), so vector and "
        "hybrid search are unavailable for it. Keyword search still works; "
        "POST /repositories/{id}/index runs the indexing again.",
        status_code=503, details={**details, "reason": status.get("reason")},
    )


async def _keyword(collection, repo_id, query, limit) -> list[SearchHit]:
    pipeline = [
        *_keyword_stage(repo_id, query, limit),
        {"$addFields": {"score": {"$meta": "searchScore"}}},
        {"$project": HIDDEN_FIELDS},
    ]
    rows = [row async for row in await collection.aggregate(pipeline)]
    return [_to_hit(r, "keyword", i + 1, keyword_rank=i + 1) for i, r in enumerate(rows)]


async def _vector(collection, repo_id, query, limit) -> list[SearchHit]:
    vector = await _embed_query(query)
    pipeline = [
        *_vector_stage(repo_id, vector, limit),
        {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
        {"$project": HIDDEN_FIELDS},
    ]
    rows = [row async for row in await collection.aggregate(pipeline)]
    return [_to_hit(r, "vector", i + 1, vector_rank=i + 1) for i, r in enumerate(rows)]


async def _hybrid(collection, repo_id, query, limit, status) -> dict[str, Any]:
    """Native `$rankFusion` where available, Python RRF otherwise - labelled either way."""
    keyword_ready = status["keyword"] == "ok"
    vector_ready = status["vector"] == "ok"

    if not keyword_ready and not vector_ready:
        _require(status["keyword"], "keyword")

    # One side missing is a degraded answer, not a failure - but say which ran.
    if keyword_ready and not vector_ready:
        return {"mode_used": "keyword", "native_hybrid": False,
                "hits": await _keyword(collection, repo_id, query, limit)}
    if vector_ready and not keyword_ready:
        return {"mode_used": "vector", "native_hybrid": False,
                "hits": await _vector(collection, repo_id, query, limit)}

    try:
        vector = await _embed_query(query)
    except AppError:
        return {"mode_used": "keyword", "native_hybrid": False,
                "hits": await _keyword(collection, repo_id, query, limit)}

    if await supports_native_hybrid():
        try:
            return {
                "mode_used": "hybrid", "native_hybrid": True,
                "hits": await _rank_fusion(collection, repo_id, query, vector, limit),
            }
        except Exception as exc:  # noqa: BLE001 - fall back, but never claim native
            log.warning("$rankFusion failed, falling back to Python RRF: %s", exc)

    return {
        "mode_used": "hybrid", "native_hybrid": False,
        "hits": await _python_rrf(collection, repo_id, query, vector, limit),
    }


async def _rank_fusion(collection, repo_id, query, vector, limit) -> list[SearchHit]:
    """MongoDB 8.0+ native hybrid search."""
    pipeline = [
        {
            "$rankFusion": {
                "input": {
                    "pipelines": {
                        "keyword": _keyword_stage(repo_id, query, limit * 3),
                        "vector": _vector_stage(repo_id, vector, limit * 3),
                    }
                },
                "combination": {"weights": {"keyword": 1, "vector": 1}},
                "scoreDetails": True,
            }
        },
        {"$limit": limit},
        {"$addFields": {
            "score": {"$meta": "score"},
            "scoreDetails": {"$meta": "scoreDetails"},
        }},
        {"$project": HIDDEN_FIELDS},
    ]
    rows = [row async for row in await collection.aggregate(pipeline)]

    hits: list[SearchHit] = []
    for index, row in enumerate(rows):
        keyword_rank, vector_rank = _ranks_from_details(row.get("scoreDetails"))
        hits.append(_to_hit(row, "hybrid", index + 1,
                            keyword_rank=keyword_rank, vector_rank=vector_rank))
    return hits


def _ranks_from_details(details: Any) -> tuple[int | None, int | None]:
    """Pull each pipeline's contribution out of $rankFusion's scoreDetails.

    Best effort: the shape is version-dependent, and a missing breakdown should cost
    a UI hint, not the whole search.
    """
    keyword_rank = vector_rank = None
    if not isinstance(details, dict):
        return keyword_rank, vector_rank
    for entry in details.get("details", []) or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("inputPipelineName")
        rank = entry.get("rank") or entry.get("inputPipelineRank")
        if name == "keyword" and isinstance(rank, int):
            keyword_rank = rank
        elif name == "vector" and isinstance(rank, int):
            vector_rank = rank
    return keyword_rank, vector_rank


async def _python_rrf(collection, repo_id, query, vector, limit) -> list[SearchHit]:
    """Reciprocal rank fusion, for clusters without `$rankFusion`."""
    keyword_hits = await _keyword(collection, repo_id, query, limit * 3)
    vector_hits = await _vector(collection, repo_id, query, limit * 3)

    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    by_id: dict[str, SearchHit] = {}

    for source, hits in (("keyword", keyword_hits), ("vector", vector_hits)):
        for rank, hit in enumerate(hits, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            ranks.setdefault(hit.chunk_id, {})[source] = rank
            by_id.setdefault(hit.chunk_id, hit)

    ordered = sorted(scores, key=lambda c: scores[c], reverse=True)[:limit]
    fused: list[SearchHit] = []
    for index, chunk_id in enumerate(ordered, start=1):
        hit = by_id[chunk_id]
        fused.append(hit.model_copy(update={
            "match_method": "hybrid",
            "score": round(scores[chunk_id], 6),
            "rank": index,
            "keyword_rank": ranks[chunk_id].get("keyword"),
            "vector_rank": ranks[chunk_id].get("vector"),
        }))
    return fused


async def _embed_query(query: str) -> list[float]:
    try:
        return await embed_one(query)
    except EmbeddingUnavailable as exc:
        raise AppError(
            SEARCH_INDEX_NOT_READY,
            f"Cannot embed the query, so vector search is unavailable: {exc}",
            status_code=503,
        ) from exc


def _to_hit(
    row: dict[str, Any], method: str, rank: int,
    keyword_rank: int | None = None, vector_rank: int | None = None,
) -> SearchHit:
    source = row.get("source") or ""
    return SearchHit(
        chunk_id=row["chunk_id"],
        symbol_id=row.get("symbol_id"),
        module_id=row.get("module_id", ""),
        path=row.get("path", ""),
        start_line=int(row.get("start_line", 0)),
        end_line=int(row.get("end_line", 0)),
        snapshot_hash=row.get("snapshot_hash", ""),
        qualified_name=row.get("qualified_name"),
        kind=row.get("kind"),
        snippet=source[:600],
        description=row.get("description"),
        match_method=method,  # type: ignore[arg-type]
        score=float(row.get("score", 0.0)),
        rank=rank,
        keyword_rank=keyword_rank,
        vector_rank=vector_rank,
    )
