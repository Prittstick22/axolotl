# runner

Sandbox image for executing fixture code. Owner: **Dev 4**.

```bash
docker build -t axolotl-runner:latest runner/
docker build -t axolotl-runner:py2.7 --build-arg PYTHON_VERSION=2.7 --build-arg PYTEST_VERSION=4.6.11 runner/
docker build -t axolotl-runner:py3.14 --build-arg PYTHON_VERSION=3.14 --build-arg PYTEST_VERSION=8.4.2 runner/
```

One Dockerfile builds one image per interpreter (#22): 3.12 by default, 2.7 for
Python 2 baselines and 3.14 for their candidates. pytest 4.6.11 is the last
release that runs on Python 2.7.

Each image contains its Python and pytest and nothing else — no project code, no cases,
no credentials. Everything is bind-mounted read-only at run time.

See [policy.md](policy.md) for the exact `docker run` profile, the three gate commands,
and an honest statement of what the sandbox does and does not protect against.

`harness/run_cases.py` is standard-library only and must never import from the backend.
