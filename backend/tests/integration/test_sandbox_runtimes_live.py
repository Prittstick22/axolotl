r"""#22, for real: the chess demo on Python 2.7 and Python 3.14 containers.

These run the **actual images** under the **actual security profile** against the
**actual chess program**. No mocks. They skip themselves when an image is absent:

    docker build -t axolotl-runner:py2.7 \
        --build-arg PYTHON_VERSION=2.7 --build-arg PYTEST_VERSION=4.6.11 runner/
    docker build -t axolotl-runner:py3.14 \
        --build-arg PYTHON_VERSION=3.14 --build-arg PYTEST_VERSION=8.4.2 runner/
    uv run pytest -m docker -q

The claim they prove: the same unchanged program, run on both interpreters, encodes
to the same outcomes. Without that, a Python 2 baseline compared against a Python 3
candidate would report the interpreter change itself as a behaviour change.
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.sandbox import runner, runtimes

CHESS = Path(__file__).resolve().parents[3] / "demo" / "chess"

# Plain-argument functions from the chess program: text, tuples, None, an object
# built on its Python 2 enum, and an exception.
SMOKE_CASES = [
    {"case_id": "parse", "function": "local_chess.common.parse_square", "args": ["e4"]},
    {"case_id": "parse_bad", "function": "local_chess.common.parse_square", "args": ["z9"]},
    {"case_id": "name", "function": "local_chess.common.square_name",
     "args": [{"__tuple__": [4, 4]}]},
    {"case_id": "piece", "function": "local_chess.pieces.piece_from_symbol", "args": ["Q"]},
    {"case_id": "raises", "function": "local_chess.common.parse_square", "args": [None]},
]


def _ready(python: str) -> bool:
    try:
        return bool(asyncio.run(runner.runner_status(runtimes.runtime(python)))["available"])
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not CHESS.is_dir(), reason="demo/chess not present"),
    pytest.mark.skipif(
        not (_ready("2.7") and _ready("3.14")),
        reason="Python 2.7 and 3.14 runner images unavailable; see this module's docstring",
    ),
]

PY27 = runtimes.runtime("2.7")
PY314 = runtimes.runtime("3.14")
BOTH = pytest.mark.parametrize("runtime", [PY27, PY314], ids=["py2.7", "py3.14"])


def _tree(tmp_path: Path, name: str, source: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "module.py").write_text(source, encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# the profile holds on both images
# --------------------------------------------------------------------------

@BOTH
async def test_the_container_has_no_network(runtime):
    result = await runner._run_in_container(
        mounts=[runner.Mount(CHESS, "/code")],
        command=["python", "-c",
                 "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)"],
        runtime=runtime,
    )
    assert result.exit_code != 0, f"the {runtime.python} sandbox reached the network"


@BOTH
async def test_the_code_mount_is_read_only(runtime):
    result = await runner._run_in_container(
        mounts=[runner.Mount(CHESS, "/code")],
        command=["python", "-c", "open('/code/written_by_candidate', 'w')"],
        runtime=runtime,
    )
    assert result.exit_code != 0, f"the {runtime.python} sandbox wrote into /code"


@BOTH
async def test_the_probe_reports_the_interpreter_that_ran(runtime):
    status = await runner.runner_status(runtime)
    version = await runner.probe_python(runtime, status["image_digest"])

    assert version is not None
    assert version.startswith(runtime.python + ".")


# --------------------------------------------------------------------------
# the gates work on both interpreters
# --------------------------------------------------------------------------

@BOTH
async def test_chess_compiles(runtime):
    result = await runner.run_compileall(CHESS, runtime=runtime)

    assert result.exit_code == 0, result.log_excerpt


async def test_python3_only_syntax_fails_the_27_compile_gate(tmp_path):
    tree = _tree(tmp_path, "py3only", "def f(x: int) -> int:\n    return x\n")

    result = await runner.run_compileall(tree, runtime=PY27)

    assert result.exit_code != 0
    assert "module.py" in result.stdout


async def test_a_python2_print_statement_passes_on_27_and_fails_on_314(tmp_path):
    """compile_tree.py must not leak its own print_function into the code under test."""
    tree = _tree(tmp_path, "py2only", 'print "hello"\n')

    assert (await runner.run_compileall(tree, runtime=PY27)).exit_code == 0
    assert (await runner.run_compileall(tree, runtime=PY314)).exit_code != 0


@BOTH
async def test_chess_own_tests_pass(runtime):
    result = await runner.run_pytest(CHESS, runtime=runtime)

    assert result.exit_code == 0, result.log_excerpt
    assert "8 passed" in result.stdout


async def test_unchanged_chess_encodes_identically_on_27_and_314(tmp_path):
    cases_file = tmp_path / "cases.json"
    cases_file.write_text(json.dumps(SMOKE_CASES), encoding="utf-8")

    old, old_result = await runner.run_behaviour_cases(CHESS, cases_file, runtime=PY27)
    new, new_result = await runner.run_behaviour_cases(CHESS, cases_file, runtime=PY314)

    assert old_result.exit_code == 0, old_result.stderr
    assert new_result.exit_code == 0, new_result.stderr
    assert [o["case_id"] for o in old] == [c["case_id"] for c in SMOKE_CASES]
    # Timings differ by interpreter/run; canonical behaviour must still agree.
    for before, after in zip(old, new, strict=True):
        assert {k: v for k, v in before.items() if not k.startswith("duration_")} == {
            k: v for k, v in after.items() if not k.startswith("duration_")
        }
        for outcome in (before, after):
            if outcome["status"] == "ok":
                assert len(outcome["duration_samples_ns"]) == 5
                assert outcome["duration_ns"] == sorted(outcome["duration_samples_ns"])[2]
                assert outcome["duration_ns"] >= 0

    by_id = {o["case_id"]: o for o in new}
    assert by_id["parse"]["value"] == {"__tuple__": [4, 4]}
    assert by_id["name"]["value"] == "e4"
    assert by_id["piece"]["value"]["__object__"] == "Queen"
    assert (by_id["raises"]["status"], by_id["raises"]["exception_type"]) == ("raised", "TypeError")
