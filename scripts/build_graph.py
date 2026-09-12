#!/usr/bin/env python3
"""Compatibility wrapper: seed/refresh the Research Graph for one workspace.

    python scripts/build_graph.py --workspace runs/demo [--goal "..."] [--force]

Thin wrapper over ``nexus intake`` + ``nexus graph stats`` so the entry point named in
PRP.md §4 works from a bare shell. The real logic lives in ``runtime/intake.py``.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from runtime.cli import main  # noqa: E402


def run(*argv: str) -> int:
    return main(list(argv))


def cli(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", "-w", default=".")
    parser.add_argument("--goal", default="", help="objective override for intake")
    parser.add_argument("--target", default="", help="project under study for the context scan")
    parser.add_argument("--spec", action="append", default=[], help="extra spec file/dir")
    parser.add_argument("--force", action="store_true", help="rebuild an existing plan")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh-only", action="store_true", help="skip intake, just re-propagate and report stats")
    args = parser.parse_args(argv)

    base = ["--workspace", str(pathlib.Path(args.workspace).expanduser())]
    if args.offline:
        base.insert(0, "--offline")
    if args.refresh_only:
        return run(*base, "graph", "propagate")
    intake = [*base, "intake"]
    if args.goal:
        intake += ["--goal", args.goal]
    if args.target:
        intake += ["--target", args.target]
    for spec in args.spec:
        intake += ["--spec", spec]
    if args.force:
        intake += ["--force"]
    code = run(*intake)
    if code:
        return code
    return run(*base, "graph", "stats")


if __name__ == "__main__":
    raise SystemExit(cli())
