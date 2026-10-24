"""Differential comparison - owner: Dev 4. SHARED_CONTRACTS §5.

The comparison contract, in one sentence: **outputs are equal if and only if their
canonical encodings are equal.** The harness already emits canonical form, so this
module compares structure rather than reformatting anything.

Exceptions match on **type name only**. Messages are reported but never compared,
because a reworded error message is not a behaviour change - and treating it as one
would drown the real regressions in noise.

Anything that is not a clean match is a `mismatch`, except where we genuinely could
not tell, which is `inconclusive`. Inconclusive is never a pass.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.models.contracts import CaseComparison, CaseOutcome

log = logging.getLogger(__name__)

# A case we could not evaluate on either side tells us nothing either way.
INCONCLUSIVE_STATUSES = frozenset({"timeout", "harness_error"})


def canonical(value: Any) -> str:
    """Stable string form of an already-§5-encoded value."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def outcome_from_harness(payload: dict[str, Any], fallback_case_id: str) -> CaseOutcome:
    """One JSON line from `run_cases.py` into a CaseOutcome."""
    status = payload.get("status", "harness_error")
    if status not in {"ok", "raised", "timeout", "harness_error"}:
        status = "harness_error"
    samples = payload.get("duration_samples_ns")
    valid_timing = (
        status == "ok" and isinstance(samples, list) and len(samples) == 5
        and all(type(n) is int and n >= 0 for n in samples)
        and type(payload.get("duration_ns")) is int
        and payload["duration_ns"] == sorted(samples)[2]
    )
    return CaseOutcome(
        case_id=payload.get("case_id") or fallback_case_id,
        status=status,
        value=payload.get("value"),
        exception_type=payload.get("exception_type"),
        exception_message=payload.get("exception_message"),
        duration_ns=payload["duration_ns"] if valid_timing else None,
        duration_samples_ns=samples if valid_timing else None,
    )


def compare(
    case_id: str, visibility: str, old: CaseOutcome, new: CaseOutcome
) -> CaseComparison:
    """Compare one case's baseline and candidate outcomes."""
    verdict = _verdict(old, new)
    return CaseComparison(
        case_id=case_id,
        visibility=visibility,  # type: ignore[arg-type]
        verdict=verdict,
        old=old,
        new=new,
    )


def _verdict(old: CaseOutcome, new: CaseOutcome) -> str:
    # Could not evaluate one side: we learned nothing, and nothing is not a pass.
    if old.status in INCONCLUSIVE_STATUSES or new.status in INCONCLUSIVE_STATUSES:
        return "inconclusive"

    if old.status != new.status:
        return "mismatch"

    if old.status == "raised":
        # §5: type name only. A reworded message is not a behaviour change.
        return "match" if old.exception_type == new.exception_type else "mismatch"

    return "match" if canonical(old.value) == canonical(new.value) else "mismatch"


def summarise(comparisons: list[CaseComparison]) -> dict[str, int]:
    counts = {"match": 0, "mismatch": 0, "inconclusive": 0}
    for comparison in comparisons:
        counts[comparison.verdict] = counts.get(comparison.verdict, 0) + 1
    return counts


def describe_difference(comparison: CaseComparison) -> str:
    """A short human-readable difference, for events and the UI.

    Deliberately does not reveal held-out *inputs* - only what changed - so it is
    safe to put in an event payload.
    """
    old, new = comparison.old, comparison.new

    if old.status != new.status:
        return f"{old.status} -> {new.status}"
    if old.status == "raised":
        return f"raised {old.exception_type} -> raised {new.exception_type}"

    old_value, new_value = _render(old.value), _render(new.value)
    return f"{old_value} -> {new_value}"


def _render(value: Any) -> str:
    """Render a §5-encoded value compactly. Decimals read as the money they are."""
    if isinstance(value, dict):
        if "__decimal__" in value:
            return str(value["__decimal__"])
        if "__float__" in value:
            return str(value["__float__"])
        if "total" in value and isinstance(value["total"], dict):
            inner = value["total"]
            if "__decimal__" in inner:
                return f"total {inner['__decimal__']}"
    rendered = canonical(value)
    return rendered if len(rendered) <= 120 else rendered[:117] + "..."
