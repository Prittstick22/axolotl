#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Axolotl compile gate for interpreters without PYTHONPYCACHEPREFIX - owner: Dev 4.

    python /harness/compile_tree.py /code

`python -m compileall` on Python 2.7 writes each .pyc next to its source, which
fails on the read-only /code mount. This compiles every .py file in memory
instead and writes nothing, so the gate checks the same thing: does the tree
compile on this interpreter. One line per failure on stdout, like compileall -q;
exit 1 if any file fails.

Standard library only, and runs unchanged on Python 2.7 and Python 3 (#22).
"""

from __future__ import print_function

import os
import sys


def compile_tree(root):
    failures = 0
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = sorted(d for d in subdirectories if d != "__pycache__")
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(directory, name)
            with open(path, "rb") as handle:
                source = handle.read()
            try:
                # dont_inherit: this file's own `print_function` import must not leak
                # into the code under test, where `print x` is valid Python 2.
                compile(source, path, "exec", 0, True)
            except (SyntaxError, TypeError, ValueError) as exc:
                failures += 1
                print("*** Error compiling %s: %s" % (path, exc))
    return failures


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: compile_tree.py ROOT", file=sys.stderr)
        return 2
    return 1 if compile_tree(args[0]) else 0


if __name__ == "__main__":
    sys.exit(main())
