"""Which uploads may be executed - owner: Dev 2. Spec §3.4, §0.5.

Executable migration is for **allowlisted trusted fixtures only**. Selecting a fixture
by id is obviously trusted. An *upload* is not, and the demo needs one: since 24 Oct
chess is the MVP demo and the UI uploads it as a folder (#19, #21).

The rule here is an exact content match. An uploaded tree is executable only when every
path and every file's content is identical to a trusted tree that lives in this
repository, where it is version-controlled and reviewable. One changed byte in one file,
one extra file, one missing file, and the match fails and the upload stays
static-analysis only. Nothing about the upload's *name* or origin earns trust.

The trusted trees are the reference, not `docs/chess-source-manifest.json`. That
manifest records provenance, and its hashes were taken on a checkout whose Python files
had CRLF line endings, so 13 of its 16 entries do not match the same source in this
repository. A trust anchor that depends on the line endings in someone's working tree
would accept chess on their machine and reject it on everyone else's.

Which is also why the comparison normalises line endings. `\\r\\n` and `\\n` are the one
difference that the same logical source legitimately has on different machines - git
normalises on commit, an upload carries whatever the uploader's disk had - and it cannot
change what Python does. Both sides are normalised the same way, so a trusted tree and a
faithful copy of it agree whatever each checkout did to its newlines.

The *recorded* `snapshot_hash` is never normalised. It is the immutability guarantee for
the baseline (T-09) and must be the hash of the exact bytes on disk. This module's
normalised hash exists only to answer "is this the trusted source?".
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.analysis.identifiers import snapshot_hash_for
from app.ingestion.safety import is_ignored

log = logging.getLogger(__name__)

# fixture id -> its tree, relative to the repository root. A fixture only belongs here
# if its source is in this repository and has been read by someone on the team.
TRUSTED_TREES: dict[str, str] = {
    "legacy_orders": "demo/legacy_orders",
    "chess": "demo/chess",
}


def normalised_hash(path: Path) -> str:
    """sha256 of the file with CRLF collapsed to LF.

    Read whole rather than streamed: a `\\r\\n` pair split across two chunks would
    normalise differently from the same file read in one go, which is exactly the kind
    of hash that works until the file crosses a buffer boundary.
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def normalised_tree_hash(root: Path) -> str | None:
    """The normalised snapshot hash of a directory, or None if it is not there."""
    if not root.is_dir():
        return None

    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if is_ignored(relative):
            continue
        hashes[relative] = normalised_hash(path)

    return snapshot_hash_for(hashes) if hashes else None


def normalised_snapshot_hash(root: Path, relative_paths: list[str]) -> str:
    """The same hash for an already-extracted snapshot, over the files it kept.

    Takes the paths the import decided to keep, so the two sides of the comparison
    cover the same set of files rather than re-walking with different rules.
    """
    return snapshot_hash_for({
        relative: normalised_hash(root / relative)
        for relative in relative_paths
        if (root / relative).is_file()
    })


def trusted_fixture_for(root: Path, relative_paths: list[str]) -> str | None:
    """The fixture an uploaded tree is an exact copy of, or None.

    None is the safe answer and the common one: an ordinary upload matches nothing and
    stays static-analysis only.
    """
    from app.ingestion import repo_root

    candidate = normalised_snapshot_hash(root, relative_paths)

    for fixture_id, relative_tree in sorted(TRUSTED_TREES.items()):
        expected = normalised_tree_hash(repo_root() / relative_tree)
        if expected is None:
            log.warning("trusted tree %s is missing from this checkout", relative_tree)
            continue
        if expected == candidate:
            log.info("upload matches the trusted %s source exactly", fixture_id)
            return fixture_id

    return None
