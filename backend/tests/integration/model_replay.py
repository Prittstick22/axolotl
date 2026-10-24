"""Record-and-replay for real Gemini calls in live tests - owner: Dev 3.

The live search test imports the demo fixture and embeds all ~40 chunks, plus a query
per vector or hybrid search: about 48 embedding calls a run. Debugging it on 23 Oct
spent a free key's whole daily embedding quota in an evening.

This keeps every answer **real** - each one comes from the actual model, the first time
it is asked - but records it in pytest's cache (`.pytest_cache/`, never committed) and
replays it on later runs. Atlas, the indexes, the stored vectors and `$rankFusion` are
untouched and run for real every time. Only the repeat round trip to Gemini is skipped.

Keys include the model, the dimension count, the task type and the full text, so any
change to what would be sent is a cache miss and a real call. To check against the live
model again from scratch:

    uv run pytest --cache-clear -m live tests/integration/test_search_live.py
"""

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.config import get_settings
from app.search.embeddings import DOCUMENT_TASK, QUERY_TASK
from app.search.settings import description_model

PREFIX = "axolotl/gemini"


class Store(Protocol):
    """The subset of pytest's `config.cache` this uses."""

    def get(self, key: str, default: Any) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...


class MemoryStore:
    """For when pytest's cache plugin is disabled (`-p no:cacheprovider`)."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def get(self, key: str, default: Any) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value


def cache_key(kind: str, *parts: object) -> str:
    digest = hashlib.sha256("\0".join(str(p) for p in parts).encode()).hexdigest()[:32]
    return f"{PREFIX}/{kind}/{digest}"


def new_stats() -> dict[str, int]:
    return {"embeddings_called": 0, "embeddings_replayed": 0,
            "descriptions_called": 0, "descriptions_replayed": 0}


def replaying_embed_one(
    real: Callable[..., Awaitable[list[float]]], store: Store, stats: dict[str, int]
) -> Callable[..., Awaitable[list[float]]]:
    """Wraps `app.search.embeddings.embed_one`. Only successful vectors are recorded."""

    async def embed_one(text: str, *, task_type: str = QUERY_TASK) -> list[float]:
        settings = get_settings()
        key = cache_key("embedding", settings.gemini_embedding_model,
                        settings.embedding_dimensions, task_type, text)
        recorded = store.get(key, None)
        if recorded is not None:
            stats["embeddings_replayed"] += 1
            return list(recorded)
        vector = await real(text, task_type=task_type)  # failures propagate, unrecorded
        store.set(key, list(vector))
        stats["embeddings_called"] += 1
        return vector

    return embed_one


def replaying_embed_many(
    real: Callable[..., Awaitable[list[list[float]]]], store: Store,
    stats: dict[str, int],
) -> Callable[..., Awaitable[list[list[float]]]]:
    """Wraps `app.search.embeddings.embed_many`, the batched chunk path.

    Same keys as `replaying_embed_one`, so a text recorded either way replays either
    way. Only the texts with nothing recorded go to the real function, as one batch.
    """

    def key_for(text: str, task_type: str) -> str:
        settings = get_settings()
        return cache_key("embedding", settings.gemini_embedding_model,
                         settings.embedding_dimensions, task_type, text)

    async def embed_many(texts: list[str], *, task_type: str = DOCUMENT_TASK
                         ) -> list[list[float]]:
        vectors: list[list[float] | None] = []
        for text in texts:
            recorded = store.get(key_for(text, task_type), None)
            vectors.append(list(recorded) if recorded is not None else None)
        stats["embeddings_replayed"] += sum(v is not None for v in vectors)

        missing = [i for i, v in enumerate(vectors) if v is None]
        if missing:
            fresh = await real([texts[i] for i in missing], task_type=task_type)
            for i, vector in zip(missing, fresh, strict=True):
                store.set(key_for(texts[i], task_type), list(vector))
                vectors[i] = vector
            stats["embeddings_called"] += len(missing)
            stats["embedding_requests"] = stats.get("embedding_requests", 0) + 1
        return vectors  # type: ignore[return-value]

    return embed_many


def replaying_describe_chunks(
    real: Callable[[list[dict[str, Any]]], Awaitable[list[str]]],
    store: Store, stats: dict[str, int],
) -> Callable[[list[dict[str, Any]]], Awaitable[list[str]]]:
    """Wraps `app.search.descriptions.describe_chunks`.

    Recorded descriptions are filled in; only the chunks with none recorded go to the
    real function. A failed description is not recorded, so it is retried next run.
    """

    def key_for(chunk: dict[str, Any]) -> str:
        return cache_key("description", description_model(), chunk.get("path"),
                         chunk.get("qualified_name"), chunk.get("source"))

    async def describe_chunks(chunks: list[dict[str, Any]]) -> list[str]:
        missing = []
        for chunk in chunks:
            recorded = store.get(key_for(chunk), None)
            if recorded:
                chunk["description"] = recorded
                stats["descriptions_replayed"] += 1
            else:
                missing.append(chunk)

        warnings = await real(missing) if missing else []
        for chunk in missing:
            if chunk.get("description"):
                store.set(key_for(chunk), chunk["description"])
                stats["descriptions_called"] += 1
        return warnings

    return describe_chunks
