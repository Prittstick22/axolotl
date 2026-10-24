"""Atlas reads and writes for module docs - owner: Dev 1. #39.

Two collections, both additive under SHARED_CONTRACTS §11:

  module_docs       one cached purpose per (repository_id, path, source_hash). Keyed by
                    the source's hash, so a purpose is reused only for the exact code it
                    was written from, and two laptops never pay for the same module.
  docs_generation   one status document per repository: state, done, total, model.

Facts are never stored: they are read from the trees on request, like #34.
"""

from __future__ import annotations

from typing import Any

from app.persistence import db
from app.search.indexes import CHUNKS_COLLECTION

DOCS = "module_docs"
GENERATION = "docs_generation"


async def cached_purposes(repo_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    database = await db.get_db()
    cursor = database[DOCS].find({"repository_id": repo_id}, {"_id": 0})
    return {(d["path"], d["source_hash"]): d async for d in cursor}


async def save_purpose(document: dict[str, Any]) -> None:
    database = await db.get_db()
    key = {k: document[k] for k in ("repository_id", "path", "source_hash")}
    await database[DOCS].replace_one(key, document, upsert=True)


async def module_descriptions(repo_id: str) -> dict[str, str]:
    """Descriptions the search index already wrote for module-level chunks, by path."""
    database = await db.get_db()
    cursor = database[CHUNKS_COLLECTION].find(
        {"repository_id": repo_id, "kind": "module", "description": {"$nin": [None, ""]}},
        {"_id": 0, "path": 1, "description": 1},
    )
    return {d["path"]: d["description"] async for d in cursor}


async def get_generation(repo_id: str) -> dict[str, Any] | None:
    database = await db.get_db()
    return await database[GENERATION].find_one({"repository_id": repo_id}, {"_id": 0})


async def set_generation(repo_id: str, **fields: Any) -> None:
    database = await db.get_db()
    await database[GENERATION].update_one(
        {"repository_id": repo_id},
        {"$set": {"repository_id": repo_id, **fields}},
        upsert=True,
    )
