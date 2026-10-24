"""Which interpreter runs which side of a migration (#22).

A migration has two sides, and they need not share an interpreter:

  - the **baseline** is the original program, run on the interpreter it was written
    for, to record the behaviour the migration must keep;
  - the **candidate** is the migrated program, run on the interpreter it targets.

`modern_python3` keeps both sides on 3.12, exactly as before #22. `python2_to_3`
(the chess demo) records the baseline on 2.7 and checks the candidate on 3.14.
Each interpreter is its own image built from `runner/Dockerfile`; nothing here
relabels one image as another, and the version that actually ran is probed from
the container and recorded (see `runner.probe_python`).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings

DEFAULT_PYTHON = "3.12"

# `compileall` writes each .pyc next to its source on Python 2.7, which fails on the
# read-only /code mount; 2.7 has no PYTHONPYCACHEPREFIX to send it to /tmp instead.
# `compile_tree.py` compiles in memory and writes nothing.
COMPILEALL = ("python", "-m", "compileall", "-q", "/code")
COMPILE_TREE = ("python", "/harness/compile_tree.py", "/code")

# target_id -> (baseline python, candidate python)
TARGET_PYTHONS: dict[str, tuple[str, str]] = {
    "modern_python3": ("3.12", "3.12"),
    "python2_to_3": ("2.7", "3.14"),
}


@dataclass(frozen=True)
class Runtime:
    python: str                       # the interpreter this image is built for
    image: str                        # docker image tag, from settings
    compile_command: tuple[str, ...]  # the `compile` gate's command

    @property
    def compile_needs_harness(self) -> bool:
        return self.compile_command[1].startswith("/harness/")

    @property
    def build_command(self) -> str:
        """How to build this image, for the "image not found" message."""
        if self.python == DEFAULT_PYTHON:
            return f"docker build -t {self.image} runner/"
        pytest = "4.6.11" if self.python.startswith("2.") else "8.4.2"
        return (
            f"docker build -t {self.image} --build-arg PYTHON_VERSION={self.python} "
            f"--build-arg PYTEST_VERSION={pytest} runner/"
        )


@dataclass(frozen=True)
class RuntimePair:
    baseline: Runtime
    candidate: Runtime


def runtime(python: str = DEFAULT_PYTHON) -> Runtime:
    settings = get_settings()
    images = {
        "3.12": settings.runner_image,
        "2.7": settings.runner_image_py27,
        "3.14": settings.runner_image_py314,
    }
    if python not in images:
        raise ValueError(f"no runner image for Python {python}")
    command = COMPILE_TREE if python.startswith("2.") else COMPILEALL
    return Runtime(python=python, image=images[python], compile_command=command)


def for_target(target_id: str | None) -> RuntimePair:
    """The runtimes a target's run uses. No target means the original 3.12 pair.

    An unknown target is an error, not a quiet 3.12: verifying a Python 2 candidate
    on the wrong interpreter would produce a verdict about the wrong program.
    """
    if target_id is None:
        baseline, candidate = DEFAULT_PYTHON, DEFAULT_PYTHON
    elif target_id in TARGET_PYTHONS:
        baseline, candidate = TARGET_PYTHONS[target_id]
    else:
        raise ValueError(f"no runtimes are defined for target {target_id!r}")
    return RuntimePair(baseline=runtime(baseline), candidate=runtime(candidate))
