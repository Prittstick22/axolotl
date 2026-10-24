# Axolotl sandbox runner - owner: Dev 4.
#
# One Dockerfile, one image per interpreter (#22). Build from the repo root:
#     docker build -t axolotl-runner:latest runner/
#     docker build -t axolotl-runner:py2.7 --build-arg PYTHON_VERSION=2.7 --build-arg PYTEST_VERSION=4.6.11 runner/
#     docker build -t axolotl-runner:py3.14 --build-arg PYTHON_VERSION=3.14 --build-arg PYTEST_VERSION=8.4.2 runner/
# The defaults build the original 3.12 image unchanged. pytest 4.6 is the last
# release that runs on Python 2.7.
#
# This image never contains project code, cases or credentials. The candidate
# tree, the harness and the cases file are all bind-mounted read-only at run
# time, and no environment variables are passed in. See runner/policy.md.

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim
ARG PYTEST_VERSION=8.3.3

# /code is read-only at run time, so bytecode cannot be written next to sources.
# PYTHONDONTWRITEBYTECODE is not enough: `compileall` writes bytecode by design and
# ignores it, so PYTHONPYCACHEPREFIX redirects every .pyc into the tmpfs instead.
# uid 65534 (nobody) has no home either, so HOME and TMPDIR point at the tmpfs too.
#
# PYTHONPATH=/code puts the candidate tree's top-level modules on the path. pytest's
# prepend import mode would otherwise insert only /code/tests (tests/ has no
# __init__.py), and `import pricing` from a test would fail.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    PYTHONPYCACHEPREFIX=/tmp/pycache \
    PYTHONPATH=/code \
    HOME=/tmp \
    TMPDIR=/tmp \
    PYTEST_ADDOPTS="-p no:cacheprovider"

# pytest is preinstalled so no gate ever needs network access at run time
# (the container runs with --network none).
# The stdlib is precompiled at build time because nothing can write bytecode at run
# time; sysconfig finds it on every interpreter instead of a hard-coded 3.12 path.
# Only the precompile may fail: the brackets keep `|| true` from also hiding a failed
# pytest install (#100), which would fail every existing_tests gate instead.
RUN pip install --no-cache-dir "pytest==${PYTEST_VERSION}" \
    && (python -c "import compileall, sysconfig; compileall.compile_dir(sysconfig.get_paths()['stdlib'], quiet=1)" >/dev/null 2>&1 || true)

WORKDIR /

# No ENTRYPOINT: the backend supplies the exact command per gate
#   differential   python /harness/run_cases.py --code /code --cases /cases.json
#   compile        python -m compileall -q /code
#   existing_tests pytest -q -p no:cacheprovider /code/tests
CMD ["python", "-c", "import sys, pytest; print('axolotl-runner', sys.version)"]
