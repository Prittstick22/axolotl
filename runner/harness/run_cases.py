#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Axolotl sandbox harness - owner: Dev 4.

Runs behaviour cases against a candidate code tree inside the sandbox container
and prints one JSON line per case on stdout.

    python /harness/run_cases.py --code /code --cases /cases.json

Each line is a CaseOutcome (SHARED_CONTRACTS §4):

    {"case_id", "status", "value", "exception_type", "exception_message"}

where status is one of "ok" | "raised" | "timeout" | "harness_error", and
`value` is canonically encoded per SHARED_CONTRACTS §5.

This file MUST NOT import anything from the backend. It runs as uid 65534 in a
container with no network, a read-only filesystem and a 16 MB tmpfs at /tmp.
Standard library only. Chess inputs/outputs also run on Python 2.7; runtime
selection for production migrations is separate from this harness.
"""

from __future__ import print_function

import argparse
import datetime
import io
import json
import os
import signal
import sys
import traceback
from decimal import Decimal

DEFAULT_CASE_TIMEOUT_SECONDS = 5

try:
    STRING_TYPES = (basestring,)
    INTEGER_TYPES = (int, long)
except NameError:
    STRING_TYPES = (str,)
    INTEGER_TYPES = (int,)

# Diagnostics go to stderr so stdout stays one-JSON-line-per-case.
def _warn(message):
    print(message, file=sys.stderr)


class CaseTimeout(Exception):
    pass


def _on_alarm(signum, frame):
    raise CaseTimeout("case exceeded its time budget")


# --------------------------------------------------------------------------
# SHARED_CONTRACTS §5 - value encoding
# --------------------------------------------------------------------------

def decode(value):
    """JSON (as written in a cases file) -> live Python value."""
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value

    if "__decimal__" in value:
        return Decimal(value["__decimal__"])
    if "__float__" in value:
        return float(value["__float__"])
    if "__tuple__" in value:
        return tuple(decode(v) for v in value["__tuple__"])
    if "__datetime__" in value:
        return datetime.datetime.fromisoformat(value["__datetime__"])
    if "__date__" in value:
        return datetime.date.fromisoformat(value["__date__"])
    if "__set__" in value:
        return set(decode(v) for v in value["__set__"])
    if "__dict_items__" in value:
        return {decode(k): decode(v) for k, v in value["__dict_items__"]}

    return {k: decode(v) for k, v in value.items()}


def encode(value):
    """Live Python value -> canonical JSON per §5.

    Equality of two outputs is defined as equality of these encodings, so this
    must be deterministic: dict keys sorted, sets sorted, Decimals kept as their
    exact string form.
    """
    if value is None or isinstance(value, (bool,) + INTEGER_TYPES + STRING_TYPES):
        return value

    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}

    if isinstance(value, float):
        return {"__float__": repr(value)}

    if isinstance(value, tuple):
        return {"__tuple__": [encode(v) for v in value]}

    # datetime before date: datetime is a subclass of date.
    if isinstance(value, datetime.datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, datetime.date):
        return {"__date__": value.isoformat()}

    if isinstance(value, (set, frozenset)):
        encoded = [encode(v) for v in value]
        return {"__set__": sorted(encoded, key=_sort_key)}

    if isinstance(value, list):
        return [encode(v) for v in value]

    if isinstance(value, dict):
        if all(isinstance(k, STRING_TYPES) for k in value):
            return {k: encode(value[k]) for k in sorted(value)}
        items = [[encode(k), encode(v)] for k, v in value.items()]
        return {"__dict_items__": sorted(items, key=_sort_key)}

    if hasattr(value, "__dict__"):
        fields = {k: encode(v) for k, v in sorted(vars(value).items())}
        return {"__object__": type(value).__name__, "fields": fields}

    # Anything else is reported by its repr rather than silently dropped.
    return {"__repr__": repr(value)}


def _sort_key(item):
    """Total order over encoded values, so sets and non-str-keyed dicts are stable."""
    return json.dumps(item, sort_keys=True, default=str)


# --------------------------------------------------------------------------
# calling into the candidate tree
# --------------------------------------------------------------------------

def resolve(dotted):
    """'pricing.finalise_amount' -> the live callable."""
    module_name, _, attribute = dotted.rpartition(".")
    if not module_name:
        raise ValueError("function must be dotted, got %r" % dotted)
    module = __import__(module_name)
    for part in module_name.split(".")[1:]:
        module = getattr(module, part)
    return getattr(module, attribute)


def reset_state():
    """Call storage.reset() if the tree has one, so every case starts clean."""
    try:
        import storage
    except ImportError:
        return
    reset = getattr(storage, "reset", None)
    if callable(reset):
        reset()


def run_one(case, timeout_seconds):
    """Execute a single case and return a CaseOutcome dict."""
    case_id = case.get("case_id", "<missing>")
    outcome = {
        "case_id": case_id,
        "status": "ok",
        "value": None,
        "exception_type": None,
        "exception_message": None,
    }

    # A case is either a single call or a short sequence of steps whose last
    # return value is the captured output.
    steps = case.get("steps")
    if steps:
        calls = [(s["function"], s.get("args", []), s.get("kwargs", {})) for s in steps]
    else:
        calls = [(case["function"], case.get("args", []), case.get("kwargs", {}))]

    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(timeout_seconds)
    try:
        reset_state()
        result = None
        for dotted, args, kwargs in calls:
            fn = resolve(dotted)
            result = fn(*[decode(a) for a in args], **{k: decode(v) for k, v in kwargs.items()})
        outcome["value"] = encode(result)
    except CaseTimeout as exc:
        outcome["status"] = "timeout"
        outcome["exception_type"] = "CaseTimeout"
        outcome["exception_message"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - any candidate-code failure is data
        # §5: exceptions match on type name only; the message is reported, never compared.
        outcome["status"] = "raised"
        outcome["exception_type"] = type(exc).__name__
        outcome["exception_message"] = str(exc)[:1000]
    finally:
        signal.alarm(0)

    return outcome


def load_cases(path):
    with io.open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict) and "cases" in data:
        return data["cases"]
    if isinstance(data, list):
        return data
    raise ValueError("cases file must be a JSON array or an object with a 'cases' key")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Axolotl behaviour cases.")
    parser.add_argument("--code", required=True, help="candidate code tree (mounted read-only)")
    parser.add_argument("--cases", required=True, help="cases JSON file (mounted read-only)")
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("AXOLOTL_CASE_TIMEOUT", DEFAULT_CASE_TIMEOUT_SECONDS)),
        help="per-case timeout in seconds (default %d)" % DEFAULT_CASE_TIMEOUT_SECONDS,
    )
    args = parser.parse_args(argv)

    # The candidate tree goes first so it shadows anything else on the path.
    sys.path.insert(0, args.code)

    try:
        cases = load_cases(args.cases)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({
            "case_id": None,
            "status": "harness_error",
            "value": None,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
        }, sort_keys=True))
        return 1

    failures = 0
    for case in cases:
        try:
            outcome = run_one(case, args.timeout)
        except Exception as exc:  # noqa: BLE001 - harness bug, not candidate failure
            _warn(traceback.format_exc())
            outcome = {
                "case_id": case.get("case_id"),
                "status": "harness_error",
                "value": None,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
            }
        if outcome["status"] == "harness_error":
            failures += 1
        print(json.dumps(outcome, sort_keys=True))

    # Exit code reports harness health only. Case outcomes are read from stdout:
    # a "raised" or "timeout" case is a normal result, not a harness failure.
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
