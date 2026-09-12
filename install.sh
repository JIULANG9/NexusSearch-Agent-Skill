#!/usr/bin/env bash
# NexusSearch Deep Research - installer wrapper.
#
# All install logic lives in `nexus install` (runtime/cli.py) so the file set has one
# source of truth: exactly what `nexus open-source check` considers publishable.
# This script only makes sure it runs from the right directory.
#
#   bash install.sh                          # -> ~/.agents/skills/nexus-deep-research
#   bash install.sh --dest ~/.codex/skills   # -> ~/.codex/skills/nexus-deep-research
#   bash install.sh --dry-run                # what would change, touch nothing
#   bash install.sh --list                   # supported skills roots
#   bash install.sh --offline                # skip the MCP reachability hint
#
# Exit codes: 0 ok, 1 spec check failed, 2 bad usage / not a skill tree.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "${SRC}/runtime/cli.py" ] || { printf 'runtime/cli.py not found next to install.sh - run it from a NexusSearch checkout\n' >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { printf 'python3 is required (>= 3.10)\n' >&2; exit 2; }

cd "${SRC}"
exec python3 -m runtime.cli install "$@"
