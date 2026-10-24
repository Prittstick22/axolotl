"""Read declared Python requirements without importing uploaded setup.py."""

import ast
import re
import tomllib
from pathlib import Path

from app.analysis.python2 import python2_tree


def inspect_runtime(root: Path) -> dict:
    # Snapshots are local to the importing machine; the record is shared via Atlas.
    if not root.is_dir():
        return {
            "declared_python": None,
            "evidence": [],
            "runtime_verified": False,
            "note": (
                "The source isn't on this machine: snapshots stay on the machine that "
                "imported them. Import the repository again here to read its metadata."
            ),
        }
    evidence = []
    requirements = []
    # Also accept archives containing a single top-level project directory.
    roots = [root]
    children = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink()]
    if len(children) == 1:
        roots.append(children[0])
    for base in roots:
        for name in ("setup.py", "pyproject.toml"):
            path = base / name
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 65536:
                continue
            try:
                text = path.read_text(encoding="utf-8")
                if name == "pyproject.toml":
                    requirement = tomllib.loads(text).get("project", {}).get("requires-python")
                    values = [requirement] if isinstance(requirement, str) else []
                else:
                    try:
                        tree = ast.parse(text)
                    except SyntaxError:
                        # A Python 2 setup.py is exactly where the declaration lives
                        # (#24). Read, never executed, never rewritten.
                        tree = python2_tree(text, name)
                        if tree is None:
                            raise
                    values = [
                        kw.value.value
                        for node in ast.walk(tree)
                        if isinstance(node, ast.Call)
                        for kw in node.keywords
                        if kw.arg == "python_requires"
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                    ]
                for requirement in values:
                    requirements.append(requirement)
                    evidence.append(f"{path.relative_to(root).as_posix()}: {requirement}")
            except (ValueError, SyntaxError, UnicodeError, OSError):
                continue
    unique = list(dict.fromkeys(requirements))
    declared = None
    if len(unique) == 1:
        # Only label an exact legacy family when metadata excludes Python 3.
        req = unique[0].replace(" ", "")
        if re.search(r"(?:>=|==|~=)2\.7(?:[,.*]|$)", req) and (
            re.search(r"<3(?:\.0)?(?:,|$)", req) or req.startswith("==2.7")
        ):
            declared = "2.7"
        else:
            declared = req
    return {
        "declared_python": declared,
        "evidence": evidence,
        "runtime_verified": False,
        "note": "Read from package metadata; no uploaded code was executed.",
    }
