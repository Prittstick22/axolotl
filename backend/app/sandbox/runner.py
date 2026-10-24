"""Docker sandbox runner - owner: Dev 4. Spec §7.5, runner/policy.md.

Every execution of repository code goes through here, under the profile in
`runner/policy.md`, or it does not happen at all.

Two rules with no exceptions:

  - **Never fall back to host `subprocess`** for repository code. If Docker is
    unavailable the mode is `disabled`, gates come back `inconclusive`, and
    inconclusive is not a pass.
  - **No credentials ever enter the container.** No `-e`, no env file, no socket
    mount. The model's key stays on the backend.

The three gate commands all run under the same profile; only the command differs.
Every gate takes a `Runtime` (#22) naming the interpreter image to run in; without
one it uses the original 3.12 image. The profile is the same for every image.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.sandbox.runtimes import Runtime
from app.sandbox.runtimes import runtime as default_runtime

log = logging.getLogger(__name__)

HARNESS_DIR = Path(__file__).resolve().parents[3] / "runner" / "harness"

DEFAULT_TIMEOUT_SECONDS = 60
STDOUT_CAP_BYTES = 1024 * 1024        # policy.md: stdout capped at 1 MB
LOG_EXCERPT_BYTES = 4096              # §4: GateResult.log_excerpt, 4 KB

# runner/policy.md. Changing any of these changes what the sandbox protects against,
# so they live in one list and are passed verbatim.
SECURITY_PROFILE = [
    "--rm",
    "--network", "none",
    "--user", "65534:65534",
    "--read-only",
    "--memory", "256m",
    "--cpus", "1",
    "--pids-limit", "64",
    "--security-opt", "no-new-privileges",
    "--cap-drop", "ALL",
    "--tmpfs", "/tmp:size=16m",
]


@dataclass
class RunResult:
    """What came back from one container run."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    killed: bool = False
    oversized_output: bool = False
    docker_error: str | None = None

    @property
    def policy_clean(self) -> bool:
        """The `sandbox_policy` gate: no timeouts, kills or oversized output."""
        return not (self.timed_out or self.killed or self.oversized_output)

    @property
    def log_excerpt(self) -> str:
        combined = (self.stdout + ("\n" + self.stderr if self.stderr else ""))
        encoded = combined.encode("utf-8")
        if len(encoded) <= LOG_EXCERPT_BYTES:
            return combined
        return encoded[:LOG_EXCERPT_BYTES].decode("utf-8", errors="ignore") + "... [truncated]"


@dataclass
class Mount:
    host: Path
    container: str
    read_only: bool = True

    def as_args(self) -> list[str]:
        suffix = ":ro" if self.read_only else ""
        return ["-v", f"{self.host.resolve()}:{self.container}{suffix}"]


@dataclass
class RunnerStatus:
    mode: str
    available: bool
    image_digest: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "available": self.available,
            "image_digest": self.image_digest,
            "detail": self.detail,
        }


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

async def runner_status(runtime: Runtime | None = None) -> dict:
    """SHARED_CONTRACTS §8: {mode, available, image_digest}, for one runtime's image.

    `disabled` whenever the image cannot actually be run, so the rest of the system
    degrades to `inconclusive` rather than discovering it mid-gate. Without a runtime
    this is the original 3.12 image, as `/system/status` reports.
    """
    settings = get_settings()
    runtime = runtime or default_runtime()

    if settings.runner_mode == "disabled":
        return RunnerStatus("disabled", False, detail="RUNNER_MODE=disabled").as_dict()

    daemon = await _docker("version", "--format", "{{.Server.Version}}", timeout=15)
    if daemon.exit_code != 0:
        return RunnerStatus(
            "disabled", False,
            detail=f"Docker daemon unreachable: {daemon.stderr.strip()[:200]}",
        ).as_dict()

    inspect = await _docker(
        "image", "inspect", runtime.image, "--format", "{{.Id}}", timeout=20
    )
    if inspect.exit_code != 0:
        return RunnerStatus(
            "disabled", False,
            detail=(
                f"image {runtime.image} (Python {runtime.python}) not found; "
                f"build it with: {runtime.build_command}"
            ),
        ).as_dict()

    return RunnerStatus(
        settings.runner_mode, True, image_digest=inspect.stdout.strip() or None
    ).as_dict()


async def is_available(runtime: Runtime | None = None) -> bool:
    return bool((await runner_status(runtime))["available"])


# image id -> the Python version that image actually runs
_PROBED: dict[str, str] = {}


async def probe_python(runtime: Runtime, image_digest: str | None = None) -> str | None:
    """The interpreter version the image really runs, read from inside it.

    Recorded with every baseline and verification, so a verdict names the Python it
    was produced on rather than the one the image was meant to have. None when the
    probe fails; never a guess. Cached per image id, which changes on every rebuild.
    """
    if image_digest and image_digest in _PROBED:
        return _PROBED[image_digest]
    result = await _run_in_container(
        mounts=[],
        command=["python", "-c", "import platform; print(platform.python_version())"],
        timeout=30,
        runtime=runtime,
    )
    version = result.stdout.strip() if result.exit_code == 0 else ""
    if not version:
        return None
    if image_digest:
        _PROBED[image_digest] = version
    return version


# --------------------------------------------------------------------------
# the three gate commands
# --------------------------------------------------------------------------

async def run_behaviour_cases(
    code_dir: Path,
    cases_file: Path,
    *,
    timeout: int | None = None,
    runtime: Runtime | None = None,
) -> tuple[list[dict], RunResult]:
    """Execute cases in the sandbox. Returns (outcomes, raw result).

    The harness prints one `CaseOutcome` JSON object per line; anything unparseable
    is reported rather than skipped, because a silently dropped case would look like
    a case that passed.
    """
    result = await _run_in_container(
        mounts=[
            Mount(code_dir, "/code"),
            Mount(HARNESS_DIR, "/harness"),
            Mount(cases_file, "/cases.json"),
        ],
        command=[
            "python", "/harness/run_cases.py",
            "--code", "/code", "--cases", "/cases.json",
        ],
        timeout=timeout,
        runtime=runtime,
    )

    outcomes: list[dict] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            outcomes.append(json.loads(line))
        except json.JSONDecodeError:
            outcomes.append({
                "case_id": None, "status": "harness_error", "value": None,
                "exception_type": "UnparseableOutput",
                "exception_message": line[:200],
            })
    return outcomes, result


async def run_compileall(
    code_dir: Path, *, timeout: int | None = None, runtime: Runtime | None = None
) -> RunResult:
    """The `compile` gate, on the runtime's interpreter.

    Python 3: `compileall`, with PYTHONPYCACHEPREFIX in the image keeping .pyc off the
    read-only /code. Python 2.7 has no such variable, so `compile_tree.py` compiles
    in memory instead (see `runtimes`).
    """
    runtime = runtime or default_runtime()
    mounts = [Mount(code_dir, "/code")]
    if runtime.compile_needs_harness:
        mounts.append(Mount(HARNESS_DIR, "/harness"))
    return await _run_in_container(
        mounts=mounts,
        command=list(runtime.compile_command),
        timeout=timeout,
        runtime=runtime,
    )


async def run_pytest(
    code_dir: Path, *, timeout: int | None = None, runtime: Runtime | None = None
) -> RunResult:
    """The `existing_tests` gate: the project's own suite, on the runtime's interpreter."""
    return await _run_in_container(
        mounts=[Mount(code_dir, "/code")],
        command=["pytest", "-q", "-p", "no:cacheprovider", "/code/tests"],
        timeout=timeout,
        runtime=runtime,
    )


# --------------------------------------------------------------------------
# docker
# --------------------------------------------------------------------------

async def _run_in_container(
    *,
    mounts: list[Mount],
    command: list[str],
    timeout: int | None = None,
    runtime: Runtime | None = None,
) -> RunResult:
    image = (runtime or default_runtime()).image
    seconds = timeout or DEFAULT_TIMEOUT_SECONDS
    name = f"axolotl-{secrets.token_hex(6)}"

    args = ["run", "--name", name, *SECURITY_PROFILE]
    for mount in mounts:
        args.extend(mount.as_args())
    args.append(image)
    args.extend(command)

    result = await _docker(*args, timeout=seconds)

    if result.timed_out:
        # --rm does not help a container that is still running; kill it explicitly.
        await _docker("kill", name, timeout=15)
        log.warning("killed %s after %ss", name, seconds)

    return result


async def _docker(*args: str, timeout: int) -> RunResult:
    try:
        process = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as exc:
        return RunResult(
            exit_code=127, stdout="", stderr=str(exc), docker_error=str(exc)
        )

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return RunResult(exit_code=124, stdout="", stderr="timed out", timed_out=True)

    oversized = len(stdout) > STDOUT_CAP_BYTES
    if oversized:
        stdout = stdout[:STDOUT_CAP_BYTES]

    return RunResult(
        exit_code=process.returncode if process.returncode is not None else -1,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        oversized_output=oversized,
    )
