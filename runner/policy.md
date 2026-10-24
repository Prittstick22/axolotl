# Sandbox policy

Owner: Dev 4. Every execution of repository code happens under this profile, or it
does not happen at all.

## The profile

```bash
docker run --rm --network none --user 65534:65534 --read-only \
  --memory 256m --cpus 1 --pids-limit 64 \
  --security-opt no-new-privileges --cap-drop ALL \
  --tmpfs /tmp:size=16m \
  -v <code_tree>:/code:ro -v <harness_dir>:/harness:ro -v <cases_file>:/cases.json:ro \
  axolotl-runner:latest \
  python /harness/run_cases.py --code /code --cases /cases.json
```

| Control | Value | What it stops |
|---|---|---|
| `--network none` | no interfaces | exfiltration, downloading payloads, calling home |
| `--user 65534:65534` | nobody | writing anything owned by root |
| `--read-only` + `:ro` mounts | whole rootfs and all three mounts | modifying the baseline, the harness or the cases |
| `--tmpfs /tmp:size=16m` | only writable path | filling the disk |
| `--memory 256m` | hard cap | memory exhaustion of the host |
| `--cpus 1` | one core | starving the demo machine |
| `--pids-limit 64` | hard cap | fork bombs |
| `--cap-drop ALL` | no capabilities | privileged syscalls |
| `--security-opt no-new-privileges` | setuid neutered | privilege escalation inside the container |
| no `-e` / `--env-file` | nothing passed | credentials reaching candidate code (keys stay on the backend) |
| no Docker socket mount | — | container escape via the daemon |
| `--rm` + backend `docker kill` | wall-clock timeout | runaway containers |

The backend caps stdout at 1 MB and truncates `GateResult.log_excerpt` to 4 KB with
secret values redacted.

## Gates run under the same profile

| Gate | Command |
|---|---|
| `compile` | `python -m compileall -q /code` (Python 3), `python /harness/compile_tree.py /code` (Python 2.7) |
| `differential` / `impacted_tests` | `python /harness/run_cases.py --code /code --cases /cases.json` |
| `existing_tests` / `final_integration` | `pytest -q -p no:cacheprovider /code/tests` |

`PYTHONPYCACHEPREFIX=/tmp/pycache` is set in the image because `compileall` writes
bytecode by design and ignores `PYTHONDONTWRITEBYTECODE`; without it the compile gate
fails on the read-only `/code`. `PYTHONPATH=/code` is set because pytest's prepend
import mode would otherwise only put `/code/tests` on the path.

### One image per interpreter (#22)

The same Dockerfile builds `axolotl-runner:latest` (Python 3.12, the default),
`axolotl-runner:py2.7` and `axolotl-runner:py3.14`. Every image runs under the
identical profile above; only the interpreter differs. `python2_to_3` records the
baseline on 2.7 and runs every candidate gate on 3.14 (`app/sandbox/runtimes.py`).

Python 2.7 has no `PYTHONPYCACHEPREFIX`, so `compileall` would try to write `.pyc`
files into the read-only `/code` and fail. On 2.7 the compile gate runs
`harness/compile_tree.py`, which compiles every file in memory and writes nothing.
The harness runs unchanged on 2.7 and 3, and an unchanged program encodes to the
same outcomes on both. Each verdict records the interpreter version probed from
inside the container and the image digest, never the intended version.

## Only trusted fixtures execute

`demo/legacy_orders` is the one allowlisted fixture (`executable=True`). Uploaded ZIPs
are **static-analysis only** and get `executable=False`; asking for a plan on one is a
409 `REPOSITORY_NOT_EXECUTABLE`. `ALLOW_ARBITRARY_CODE_EXECUTION` stays `false`.
Repository code is never run through a host `subprocess`.

If Docker is unavailable, `RUNNER_MODE` reports `disabled`, `runner_status().available`
is `false`, and every gate returns `inconclusive` — which counts as **not passed**.
There is no fallback execution path.

## What this does NOT protect against

Stated plainly, because the Spec requires the real limits to be documented:

- **This is Docker, not a hardened multi-tenant sandbox.** A kernel or runtime exploit
  escapes it. It is adequate for a fixture we wrote ourselves; it is **not** adequate
  for arbitrary third-party uploads, which is why those are static-only.
- On Windows and macOS, Docker Desktop adds a VM boundary. On native Linux there is no
  VM — only namespaces and cgroups.
- `--memory` and `--pids-limit` bound resource use but do not stop a case from simply
  spinning until the wall-clock timeout fires. Such a case is `inconclusive`, never pass.
- The container can still read everything mounted into it, which includes
  `/cases.json`. When that file is the held-out set, the *container* sees held-out
  inputs. The protection against the model seeing them is at the API layer
  (`include_held_out=False` and redaction), not here.
- Passing gates means **agreement on the cases that were run**. It is not a proof that
  behaviour is preserved, and nothing in this repo should claim otherwise.
