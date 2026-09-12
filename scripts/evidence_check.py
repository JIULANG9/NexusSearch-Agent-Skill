#!/usr/bin/env python3
"""Verify evidence quality for a workspace and print the gate report.

    python scripts/evidence_check.py -w runs/demo [--apply] [--json]

Wraps ``nexus verify`` + ``nexus gate`` (PRP.md §4 entry point). Exit code 1 means the
quality gate failed, so this doubles as a CI assertion.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from runtime.cli import main  # noqa: E402


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", "-w", default=".")
    parser.add_argument("--apply", action="store_true", help="write verification status + re-propagate")
    parser.add_argument("--min-domains", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    base = ["--workspace", str(pathlib.Path(args.workspace).expanduser())]
    if args.offline:
        base.insert(0, "--offline")
    verify = [*base, "verify", "--min-domains", str(args.min_domains)]
    if args.apply:
        verify.append("--apply")
    if args.json:
        verify.append("--json")
    code = main(verify)
    gate = [*base, "gate", "--save"] + (["--json"] if args.json else [])
    return int(main(gate)) or int(code or 0)


if __name__ == "__main__":
    raise SystemExit(cli())
