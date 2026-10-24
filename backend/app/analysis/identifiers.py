"""Deterministic identifiers - owner: Dev 3. SHARED_CONTRACTS §3.

Every id here is a pure function of the content it names, so re-importing the same
tree produces the same ids and a run's references stay valid. **Never derived from
model output** (§3).
"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import PurePosixPath

REPOSITORY_PREFIX = "repo_"
MODULE_PREFIX = "module_"
EXTERNAL_PREFIX = "ext_"
SYMBOL_PREFIX = "sym_"
EDGE_PREFIX = "edge_"

SHORT_ID_BYTES = 6   # 12 hex characters
DIGEST_CHARS = 16    # §3: 16 hex for symbol and edge ids


def new_repository_id() -> str:
    """`repo_` + 12 hex. Random: a repository is an import event, not content."""
    return REPOSITORY_PREFIX + secrets.token_hex(SHORT_ID_BYTES)


def _dotted(relative_path: str) -> str:
    path = PurePosixPath(str(relative_path).replace("\\", "/"))
    parts = list(path.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][: -len(".py")]
    # A package's __init__.py names the package itself.
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def module_id_for(relative_path: str) -> str:
    """`module_` + dotted name with `.` -> `__` (§3).

    `pricing.py` -> `module_pricing`; `billing/tax.py` -> `module_billing__tax`.
    """
    return MODULE_PREFIX + _dotted(relative_path).replace(".", "__")


def dotted_name_for(relative_path: str) -> str:
    """`billing/tax.py` -> `billing.tax`. The importable name, not the id.

    Read from the path, never decoded back from the id: the id writes `.` as `__`, so
    a name that contains `__` itself (`pkg/__main__.py`) can't be decoded. It came
    back as `pkg..main.`, and its relative imports resolved from there (#140).
    """
    return _dotted(relative_path)


def external_id_for(package: str) -> str:
    """`ext_` + top-level package (§3). `os.path` -> `ext_os`."""
    top = str(package).split(".", 1)[0]
    return EXTERNAL_PREFIX + top


def symbol_id_for(
    snapshot_hash: str, path: str, qualified_name: str, start_line: int
) -> str:
    """`sym_` + 16 hex of sha256(snapshot_hash, path, qualified_name, start_line)."""
    return SYMBOL_PREFIX + _digest(
        snapshot_hash, str(path).replace("\\", "/"), qualified_name, str(start_line)
    )


def edge_id_for(
    source: str, target: str, edge_type: str, source_line: int | None
) -> str:
    """`edge_` + 16 hex of sha256(source, target, type, source_line)."""
    return EDGE_PREFIX + _digest(
        source, target, edge_type, "" if source_line is None else str(source_line)
    )


def _digest(*parts: str) -> str:
    # NUL-joined so ("a", "bc") and ("ab", "c") cannot collide.
    payload = "\0".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:DIGEST_CHARS]


def snapshot_hash_for(file_hashes: dict[str, str]) -> str:
    """`sha256:` + hex over the sorted (path, file-hash) list (§3).

    Sorted, so the hash depends on content and layout and not on walk order.
    """
    digest = hashlib.sha256()
    for path in sorted(file_hashes):
        digest.update(path.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hashes[path].encode("utf-8"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def hash_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
