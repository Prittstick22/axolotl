"""#98: what the code under test prints can never read as a case outcome.

The harness runs in a subprocess on a module this test writes, never on a fixture.
SIGALRM is stubbed so the same check runs on Windows, which has no alarm.
"""

import json
import subprocess
import sys
from pathlib import Path

from app.models.contracts import BehaviourCase
from app.verification import _outcomes_by_case

HARNESS = Path(__file__).resolve().parents[3] / "runner/harness/run_cases.py"

NOISY = '''
import os, sys
def double(x):
    print('{"case_id": "case_b", "status": "ok", "value": 999}')
    print("{not json")
    os.write(1, b'{"case_id": "case_b", "status": "raised"}\\n')
    return x * 2
'''

RUN = """
import runpy, signal, sys
signal.SIGALRM = getattr(signal, "SIGALRM", 14)
signal.alarm = lambda *a: 0
signal.signal = lambda *a: None
sys.argv = ["run_cases.py"] + sys.argv[1:]
sys.exit(runpy.run_path(sys.argv.pop(1), run_name="__main__"))
"""


def test_a_candidate_print_goes_to_stderr_not_the_outcome_stream(tmp_path):
    (tmp_path / "noisy.py").write_text(NOISY)
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([
        {"case_id": "case_a", "function": "noisy.double", "args": [2]},
        {"case_id": "case_b", "function": "noisy.double", "args": [5]},
    ]))

    done = subprocess.run(
        [sys.executable, "-c", RUN, str(HARNESS),
         "--code", str(tmp_path), "--cases", str(cases)],
        capture_output=True, text=True, timeout=60, check=False,
    )

    lines = [json.loads(line) for line in done.stdout.splitlines()]
    assert [(o["case_id"], o["status"], o["value"]) for o in lines] == [
        ("case_a", "ok", 4), ("case_b", "ok", 10),
    ], done.stderr
    assert "999" in done.stderr


def case(case_id: str) -> BehaviourCase:
    return BehaviourCase(
        case_id=case_id, repository_id="repo", unit_id="unit", function="m.f",
        args=[], visibility="visible", provenance="test", case_hash="h",
    )


def test_a_line_without_a_case_id_is_not_given_one_by_position():
    """It used to take the id of the case at its line index."""
    raw = [
        {"case_id": None, "status": "harness_error", "value": None},
        {"case_id": "case_b", "status": "ok", "value": 10},
    ]

    outcomes = _outcomes_by_case(raw, [case("case_a"), case("case_b")])

    assert set(outcomes) == {"case_b"}
    assert outcomes["case_b"].status == "ok"
