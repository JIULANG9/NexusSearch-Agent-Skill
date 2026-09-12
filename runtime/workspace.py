"""Workspace resolution: where a research run keeps its state.

A workspace is a directory created by ``nexus init`` (design doc section 12).
Every runtime component reaches disk through this class so writes stay inside
the run's own tree (privacy rule: never write outside the workspace).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .util import append_jsonl, ensure_dir, load_config, now_iso, read_json, write_json


class WorkspaceError(RuntimeError):
    """Raised when a directory is not a usable research workspace."""


@dataclass
class Workspace:
    """Filesystem view over one research run."""

    root: Path
    settings: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def open(cls, root: str | Path, create: bool = False) -> "Workspace":
        """Open an existing workspace, optionally initialising a new one."""
        path = Path(root).expanduser().resolve()
        workspace = cls(root=path, settings=load_config("settings"))
        if not workspace.is_valid():
            if not create:
                if not path.exists():
                    raise WorkspaceError(f"workspace does not exist: {path}")
                if any(path.iterdir()):
                    raise WorkspaceError(f"not a nexus workspace (missing state/): {path}")
                # An empty directory is ambiguous: refuse to scaffold silently, because a
                # typo in -w would otherwise "succeed" in the wrong place.
                raise WorkspaceError(f"empty directory is not a research workspace: {path}")
            workspace.init()
        return workspace

    # ------------------------------------------------------------------ layout
    @property
    def layout(self) -> list[str]:
        return list(self.settings.get("workspace", {}).get("layout", []))

    @property
    def files(self) -> dict[str, str]:
        return dict(self.settings.get("workspace", {}).get("files", {}))

    def path(self, key: str) -> Path:
        """Resolve a logical file key (``graph``, ``evidence``...) to a path."""
        relative = self.files.get(key)
        if not relative:
            raise WorkspaceError(f"unknown workspace file key: {key}")
        return self.root / relative

    def is_valid(self) -> bool:
        return (self.root / "state" / "workspace.json").is_file()

    def init(self) -> Path:
        """Create the directory skeleton plus seed files; idempotent."""
        for relative in self.layout:
            ensure_dir(self.root / relative)
        ensure_dir(self.root / self.settings.get("multimodal", {}).get("capture_dir", "evidence/captures"))
        marker = self.root / "state" / "workspace.json"
        existing = read_json(marker, {}) or {}
        existing.setdefault("created_at", now_iso())
        existing["updated_at"] = now_iso()
        existing["skill_version"] = self.settings.get("skill", {}).get("version", "0")
        write_json(marker, existing)
        for key in ("graph", "evidence", "claims", "plan", "state"):
            target = self.path(key)
            if not target.exists():
                _seed_file(target)
        return self.root

    # ------------------------------------------------------------------- state
    def read_state(self) -> dict[str, Any]:
        """Load ``state/state.json`` with defaults applied."""
        state = read_json(self.path("state"), {}) or {}
        state.setdefault("iteration", 0)
        state.setdefault("status", "initialized")
        state.setdefault("confidence", 0.0)
        state.setdefault("history", [])
        state.setdefault("tool_calls", 0)
        state.setdefault("started_at", now_iso())
        return state

    def write_state(self, state: dict[str, Any]) -> None:
        """Persist run state atomically."""
        state["updated_at"] = now_iso()
        write_json(self.path("state"), state)

    # ------------------------------------------------------------------- audit
    def audit_log(self) -> Path:
        """Path of the JSONL audit trail for MCP/tool calls."""
        return self.root / self.files.get("audit", "agents/logs/tool-calls.jsonl")

    def loop_log(self) -> Path:
        """Path of the JSONL loop-iteration log (research replay)."""
        return self.root / self.files.get("loop_log", "agents/logs/loop.jsonl")

    def record(self, event: str, *, stream: str = "audit", **payload: Any) -> None:
        """Append a structured event to an append-only JSONL trail.

        ``stream="audit"`` writes to the tool-call audit log; ``stream="loop"``
        writes research-loop iterations to their own replay log.
        """
        target = self.loop_log() if stream == "loop" else self.audit_log()
        append_jsonl(target, {"ts": now_iso(), "event": event, **payload})

    def guard_write(self, target: str | Path) -> Path:
        """Refuse writes outside the workspace when sandboxing is enabled."""
        resolved = Path(target).expanduser().resolve()
        if self.settings.get("workspace", {}).get("sandbox_write", True):
            if self.root not in resolved.parents and resolved != self.root:
                raise WorkspaceError(f"write outside workspace refused: {resolved}")
        return resolved

    def relative(self, target: str | Path) -> str:
        """Path relative to the workspace root, for readable reports."""
        try:
            return str(Path(target).resolve().relative_to(self.root))
        except ValueError:
            return str(target)


def _seed_file(target: Path) -> None:
    """Create an empty-but-valid placeholder for a state file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "items": []}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
