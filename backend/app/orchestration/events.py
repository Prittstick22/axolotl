"""Append-only run event journal - owner: Dev 2. SHARED_CONTRACTS §7.

`run_events` is written **only** through `append_event`. Sequence numbers come from an
atomic `$inc` on `counters` (`_id: run_id`) and are unique and monotonic per run,
starting at 1, so the frontend can poll `?after_sequence=N` and never miss or repeat an
event (T-15).

Every payload passes through `redact()` before it is stored. Excerpts are capped at
2 KB - note `GateResult.log_excerpt` is a different field with a 4 KB cap; do not
conflate the two.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument

from app.config import get_settings
from app.models.contracts import RunEvent
from app.persistence import db

log = logging.getLogger(__name__)

EVENTS_COLLECTION = "run_events"
COUNTERS_COLLECTION = "counters"

EXCERPT_LIMIT_BYTES = 2048
REDACTED = "[redacted]"

# run_id -> highest sequence written in this process. `RunSummary.last_event_sequence`
# is what the frontend polls from, so it has to track the journal rather than only
# advancing on run_state_changed, which left it far behind.
_LAST_SEQUENCE: dict[str, int] = {}

# Shorter than this and a "secret" is likely a common word we would mangle.
MIN_SECRET_LENGTH = 8


def _secret_values() -> list[str]:
    try:
        return get_settings().secret_values()
    except Exception:  # noqa: BLE001 - redaction must never break event writing
        return []


def redact(value: Any, secrets: list[str] | None = None) -> Any:
    """Strip anything matching an env secret value, recursively.

    Applies to strings anywhere in the structure, including dict keys, because a
    connection string can end up in either position.
    """
    if secrets is None:
        secrets = _secret_values()
    secrets = [s for s in secrets if s and len(s) >= MIN_SECRET_LENGTH]

    if isinstance(value, bytes):
        # Bytes slip past both the string branch below and `truncate`, so a secret
        # inside one would be stored verbatim. Payloads must be JSON-serialisable
        # anyway, so there is no legitimate reason for bytes to be here.
        return f"<{len(value)} bytes omitted>"
    if isinstance(value, str):
        for secret in secrets:
            if secret in value:
                value = value.replace(secret, REDACTED)
        return value
    if isinstance(value, dict):
        return {redact(k, secrets): redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v, secrets) for v in value)
    return value


def truncate(value: Any, limit: int = EXCERPT_LIMIT_BYTES) -> Any:
    """Cap strings at `limit` bytes, recursively, marking anything cut."""
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if len(encoded) <= limit:
            return value
        return encoded[:limit].decode("utf-8", errors="ignore") + "... [truncated]"
    if isinstance(value, dict):
        return {k: truncate(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [truncate(v, limit) for v in value]
    return value


def sanitise(
    payload: dict[str, Any] | None, secrets: list[str] | None = None
) -> dict[str, Any]:
    """Redact then truncate. Always call this before anything reaches the journal.

    `secrets` defaults to the configured env values. Pass it explicitly to redact
    something that is not in settings, and so this is testable without a live .env.

    Order matters: redact first. Truncating first could cut a secret in half and
    leave the first 2 KB of it in the journal.
    """
    return truncate(redact(payload or {}, secrets))


# These gates' log is the harness's raw output: one JSON outcome per case, held-out
# cases included. The unit endpoint (`api/plans_runs.py`) and the journal both
# withhold it, from this one list (#94).
HARNESS_GATES = {"differential", "impacted_tests"}
WITHHELD_LOG = "case outputs withheld: they include held-out cases. See comparisons."


def gate_payload(gate: Any, held_out_case_ids: set[str]) -> dict[str, Any]:
    """A `gate_result` payload without held-out case outputs (#94).

    `GET /runs/{id}/events` serves the journal as stored, and the report narrative
    hands its tail to Argon, so the log is withheld before it is written. The same
    two layers as the unit endpoint: a harness gate's log always, and any other
    gate's log that mentions a held-out case. Exposure is not known yet when a gate
    is journalled, so every held-out case counts as sealed here.
    """
    payload = gate.model_dump()
    log_text = payload.get("log_excerpt") or ""
    if log_text and (
        payload.get("gate") in HARNESS_GATES
        or any(case_id in log_text for case_id in held_out_case_ids)
    ):
        payload["log_excerpt"] = WITHHELD_LOG
    return payload


async def next_sequence(run_id: str) -> int:
    """Atomically reserve the next sequence number for this run.

    `find_one_and_update` with `$inc` and `upsert` is a single round trip and is
    atomic per document, so two concurrent writers can never take the same number.
    """
    database = await db.get_db()
    document = await database[COUNTERS_COLLECTION].find_one_and_update(
        {"_id": run_id},
        {"$inc": {"sequence": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(document["sequence"])


async def append_event(
    run_id: str,
    event_type: str,
    unit_id: str | None = None,
    attempt: int | None = None,
    payload: dict[str, Any] | None = None,
) -> RunEvent:
    """Write one event. The only way anything gets into `run_events`."""
    event = RunEvent(
        run_id=run_id,
        sequence=await next_sequence(run_id),
        timestamp=datetime.now(UTC),
        event_type=event_type,
        unit_id=unit_id,
        attempt=attempt,
        payload=sanitise(payload),
    )

    database = await db.get_db()
    await database[EVENTS_COLLECTION].insert_one(event.model_dump())
    _LAST_SEQUENCE[run_id] = max(_LAST_SEQUENCE.get(run_id, 0), event.sequence)
    log.debug("event %s#%d %s", run_id, event.sequence, event_type)
    return event


def last_sequence(run_id: str) -> int:
    """Highest sequence written for this run in this process.

    Used to keep `RunSummary.last_event_sequence` current so the frontend's
    `?after_sequence=N` polling starts from the right place.
    """
    return _LAST_SEQUENCE.get(run_id, 0)


def forget_run(run_id: str) -> None:
    """Drop the cached sequence once a run is finished."""
    _LAST_SEQUENCE.pop(run_id, None)


async def list_events(
    run_id: str, after_sequence: int = 0, limit: int = 200
) -> list[RunEvent]:
    """Events for a run in ascending sequence order, for polling (§6)."""
    database = await db.get_db()
    cursor = (
        database[EVENTS_COLLECTION]
        .find({"run_id": run_id, "sequence": {"$gt": after_sequence}}, {"_id": 0})
        .sort("sequence", 1)
        .limit(max(1, min(limit, 1000)))
    )
    return [RunEvent(**document) async for document in cursor]


async def list_all_events(run_id: str, *, hard_cap: int = 100_000) -> list[RunEvent]:
    """Every event for a run, paged.

    `list_events` clamps `limit` to 1000, which is right for polling but wrong for
    anything that needs the whole journal. The report and the diff endpoint look for
    the **last** `snapshot_accepted` event: read through a 1000-event ceiling, a long
    run would hand them an intermediate revision and they would publish it as the
    verified one. That is a T-16 breach - the diff shown would not be the diff of
    what was verified.

    `hard_cap` is a runaway guard, not an expected limit; it is logged if reached.
    """
    collected: list[RunEvent] = []
    after = 0
    while len(collected) < hard_cap:
        page = await list_events(run_id, after, 1000)
        if not page:
            break
        collected.extend(page)
        after = page[-1].sequence
        if len(page) < 1000:
            break
    if len(collected) >= hard_cap:
        log.warning("journal for %s hit the %d-event read cap", run_id, hard_cap)
    return collected


async def ensure_indexes() -> None:
    """Idempotent. `(run_id, sequence)` unique is what makes the journal trustworthy."""
    database = await db.get_db()
    await database[EVENTS_COLLECTION].create_index(
        [("run_id", 1), ("sequence", 1)], unique=True, name="run_sequence_unique"
    )
