"""Per-repository search indexing status - owner: Dev 3. Issue #28.

An import responds as soon as the repository, graph and chunks are stored; the Gemini
descriptions and embeddings follow in the background. This records where that work
is, one document per repository in `search_index_status`:

  indexing   descriptions and embeddings are being added; `embedded` of `total` so far
  ready      finished; `embedded` chunks take part in vector search
  failed     stopped, with `reason`; POST /repositories/{id}/index runs it again

No document means nothing was ever indexed in the background: a repository imported
before #28, or with `INDEX_ON_IMPORT` off. Search treats that as before.

**A restart cannot leave a repository "indexing" for ever.** The work lives in one
server process and dies with it. Each document records the host and process that own
it, and a reader on the same host that finds a different process - or this process
with nothing running - marks it failed. A document owned by another machine is left
alone: snapshots are machine-local, so only the importing machine indexes, and on a
shared cluster a teammate's import in progress must not be failed from here.
"""

from __future__ import annotations

import logging
import socket
import uuid
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

COLLECTION = "search_index_status"
INDEXING, READY, FAILED = "indexing", "ready", "failed"

HOST = socket.gethostname()
PROCESS_ID = uuid.uuid4().hex     # new on every start, which is what detects a restart

INTERRUPTED_REASON = (
    "indexing was interrupted because the server stopped; "
    "POST /repositories/{id}/index to run it again"
)

# Repositories this process is indexing right now.
_active: set[str] = set()

# Never sent to a client: they identify the machine, and only this module needs them.
_INTERNAL_FIELDS = ("_id", "host", "process_id")


def claim(repository_id: str) -> bool:
    """Mark a repository as being indexed by this process. False if it already is."""
    if repository_id in _active:
        return False
    _active.add(repository_id)
    return True


def release(repository_id: str) -> None:
    _active.discard(repository_id)


def is_active(repository_id: str) -> bool:
    return repository_id in _active


def is_interrupted(document: dict[str, Any]) -> bool:
    if document.get("state") != INDEXING or document.get("host") != HOST:
        return False
    if document.get("process_id") != PROCESS_ID:
        return True
    return document.get("repository_id") not in _active


async def _collection():
    from app.persistence import db

    return (await db.get_db())[COLLECTION]


async def _write(repository_id: str, fields: dict[str, Any]) -> None:
    collection = await _collection()
    await collection.update_one(
        {"repository_id": repository_id},
        {"$set": {**fields, "repository_id": repository_id,
                  "updated_at": datetime.now(UTC)}},
        upsert=True,
    )


async def _read(repository_id: str) -> dict[str, Any] | None:
    collection = await _collection()
    return await collection.find_one({"repository_id": repository_id})


async def begin(repository_id: str, *, total: int | None) -> None:
    await _write(repository_id, {
        "state": INDEXING, "total": total, "described": 0, "embedded": 0,
        "reason": None, "warnings": [],
        "started_at": datetime.now(UTC), "finished_at": None,
        "host": HOST, "process_id": PROCESS_ID,
    })


async def advance(repository_id: str, *, described: int, embedded: int) -> None:
    await _write(repository_id, {"described": described, "embedded": embedded})


async def finish(
    repository_id: str, state: str, *, described: int, embedded: int,
    total: int | None = None, reason: str | None = None,
    warnings: list[str] | None = None,
) -> None:
    fields: dict[str, Any] = {
        "state": state, "described": described, "embedded": embedded,
        "reason": reason, "warnings": (warnings or [])[:20],
        "finished_at": datetime.now(UTC),
    }
    if total is not None:
        fields["total"] = total
    await _write(repository_id, fields)


async def get(repository_id: str) -> dict[str, Any] | None:
    """The repository's indexing status, or None if none was ever recorded."""
    document = await _read(repository_id)
    if document is None:
        return None

    if is_interrupted(document):
        log.warning("indexing of %s was interrupted; marking it failed", repository_id)
        document = {**document, "state": FAILED, "reason": INTERRUPTED_REASON,
                    "finished_at": datetime.now(UTC)}
        await _write(repository_id, {"state": FAILED, "reason": INTERRUPTED_REASON,
                                     "finished_at": document["finished_at"]})

    return {k: v for k, v in document.items() if k not in _INTERNAL_FIELDS}
