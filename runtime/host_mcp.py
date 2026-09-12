"""Detect host agent environments and their pre-configured MCP servers.

When the skill runs inside a host like Codex, Claude Desktop, or Cursor, the host
already manages MCP server processes. This module detects that and lets the router
reuse the host's servers instead of spawning duplicate stdio sessions.

Detection is read-only and never modifies the host's configuration.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Host environment detectors
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HostServer:
    """One MCP server as declared by the host's configuration."""
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    transport: str = "stdio"
    host: str = ""  # which host provided this: "codex", "claude", "cursor"


@dataclass(frozen=True)
class HostDetection:
    """Result of probing the local environment for a host agent."""
    detected: bool = False
    host_name: str = ""
    config_path: str = ""
    servers: dict[str, HostServer] = field(default_factory=dict)
    detail: str = ""

    def server_names(self) -> list[str]:
        return sorted(self.servers)

    def has_server(self, name: str) -> bool:
        return name in self.servers


# ---------------------------------------------------------------------------
# Individual host detectors
# ---------------------------------------------------------------------------

def _detect_codex() -> HostDetection:
    """Detect Codex (Desktop or CLI) via CODEX_HOME / config.toml."""
    codex_home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    config_path = Path(codex_home) / "config.toml"
    if not config_path.is_file():
        return HostDetection(detail="no config.toml found")

    # Minimal TOML parser for [mcp_servers.*] sections — stdlib only, no deps.
    # We only need server name, command, args, env, and url.
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return HostDetection(detail="cannot read config.toml")

    servers: dict[str, HostServer] = {}
    current_server = ""
    current_subsection = ""  # "env" or ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # Sub-section [mcp_servers.name.env] — must be checked BEFORE the
        # top-level pattern, otherwise [mcp_servers.foo.env] matches as server "foo.env".
        match = re.match(r"^\[mcp_servers\.([^.]+)\.env\]$", line)
        if match:
            current_server = match.group(1).strip()
            current_subsection = "env"
            if current_server not in servers:
                servers[current_server] = HostServer(name=current_server, host="codex")
            continue

        # Top-level [mcp_servers.name] — no dots allowed in the name
        match = re.match(r"^\[mcp_servers\.([^.]+)\]$", line)
        if match:
            current_server = match.group(1).strip()
            current_subsection = ""
            if current_server not in servers:
                servers[current_server] = HostServer(name=current_server, host="codex")
            continue

        # Any other [section] exits mcp_servers context
        if line.startswith("["):
            current_server = ""
            current_subsection = ""
            continue

        if not current_server:
            continue

        # Key = value
        match = re.match(r'^(\w+)\s*=\s*(.+)$', line)
        if not match:
            continue
        key = match.group(1).strip()
        raw_value = match.group(2).strip()
        value = _parse_toml_value(raw_value)

        existing = servers[current_server]
        if current_subsection == "env":
            new_env = dict(existing.env)
            new_env[key] = str(value)
            servers[current_server] = HostServer(
                name=existing.name, command=existing.command, args=existing.args,
                env=new_env, url=existing.url, transport=existing.transport, host="codex",
            )
        elif key == "command":
            servers[current_server] = HostServer(
                name=existing.name, command=str(value), args=existing.args,
                env=existing.env, url=existing.url, transport=existing.transport, host="codex",
            )
        elif key == "args" and isinstance(value, list):
            servers[current_server] = HostServer(
                name=existing.name, command=existing.command, args=[str(v) for v in value],
                env=existing.env, url=existing.url, transport=existing.transport, host="codex",
            )
        elif key == "url":
            transport = "http" if str(value).startswith("http") else existing.transport
            servers[current_server] = HostServer(
                name=existing.name, command=existing.command, args=existing.args,
                env=existing.env, url=str(value), transport=transport, host="codex",
            )
        elif key == "type":
            transport = "http" if str(value) == "http" else existing.transport
            servers[current_server] = HostServer(
                name=existing.name, command=existing.command, args=existing.args,
                env=existing.env, url=existing.url, transport=transport, host="codex",
            )

    if not servers:
        return HostDetection(detected=True, host_name="codex", config_path=str(config_path),
                             detail="config.toml found but no [mcp_servers] defined")

    return HostDetection(
        detected=True, host_name="codex", config_path=str(config_path),
        servers=servers, detail=f"{len(servers)} server(s) in config.toml",
    )


def _detect_claude() -> HostDetection:
    """Detect Claude Desktop via its config file."""
    candidates = [
        Path.home() / ".config" / "claude" / "claude_desktop_config.json",
        Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json",
    ]
    for config_path in candidates:
        if config_path.is_file():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            raw_servers = data.get("mcpServers") or {}
            if not raw_servers:
                return HostDetection(detected=True, host_name="claude", config_path=str(config_path),
                                     detail="config found but no mcpServers")
            servers: dict[str, HostServer] = {}
            for name, body in raw_servers.items():
                if not isinstance(body, dict):
                    continue
                servers[name] = HostServer(
                    name=name,
                    command=str(body.get("command", "")),
                    args=[str(a) for a in (body.get("args") or [])],
                    env={str(k): str(v) for k, v in (body.get("env") or {}).items()},
                    url=str(body.get("url", "")),
                    transport="http" if body.get("url") else "stdio",
                    host="claude",
                )
            return HostDetection(
                detected=True, host_name="claude", config_path=str(config_path),
                servers=servers, detail=f"{len(servers)} server(s) configured",
            )
    return HostDetection(detail="no Claude Desktop config found")


def _detect_cursor() -> HostDetection:
    """Detect Cursor via .cursor/mcp.json (project or user level)."""
    candidates = [
        Path.cwd() / ".cursor" / "mcp.json",
        Path.home() / ".cursor" / "mcp.json",
    ]
    for config_path in candidates:
        if config_path.is_file():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            raw_servers = data.get("mcpServers") or {}
            if not raw_servers:
                return HostDetection(detected=True, host_name="cursor", config_path=str(config_path),
                                     detail="config found but no mcpServers")
            servers: dict[str, HostServer] = {}
            for name, body in raw_servers.items():
                if not isinstance(body, dict):
                    continue
                servers[name] = HostServer(
                    name=name,
                    command=str(body.get("command", "")),
                    args=[str(a) for a in (body.get("args") or [])],
                    env={str(k): str(v) for k, v in (body.get("env") or {}).items()},
                    url=str(body.get("url", "")),
                    transport="http" if body.get("url") else "stdio",
                    host="cursor",
                )
            return HostDetection(
                detected=True, host_name="cursor", config_path=str(config_path),
                servers=servers, detail=f"{len(servers)} server(s) configured",
            )
    return HostDetection(detail="no Cursor MCP config found")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_host_mcp() -> HostDetection:
    """Probe the environment for a host agent with pre-configured MCP servers.

    Checks in priority order: Codex → Claude Desktop → Cursor.
    Returns the first detected host, or an empty detection if none found.
    """
    # Strong signal: running inside Codex right now
    if os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_HOME"):
        result = _detect_codex()
        if result.detected and result.servers:
            return result

    # Try each host in order
    for detector in (_detect_codex, _detect_claude, _detect_cursor):
        result = detector()
        if result.detected and result.servers:
            return result

    # Return the most informative "nothing found" result
    return HostDetection(detail="no host agent with MCP servers detected")


def match_host_to_map(host: HostDetection, map_servers: dict[str, Any]) -> dict[str, str]:
    """Map our mcp-map.yaml server names to host-provided server names.

    Returns {our_name: host_server_name} for servers the host already provides.
    Matching is by name (exact or normalised) since hosts and our map usually
    use the same server names (searxng, github, memory, etc.).
    """
    if not host.detected or not host.servers:
        return {}
    matched: dict[str, str] = {}
    host_names = {name.lower(): name for name in host.server_names()}
    for our_name in map_servers:
        key = our_name.lower()
        if key in host_names:
            matched[our_name] = host_names[key]
    return matched


# ---------------------------------------------------------------------------
# TOML value parser (minimal, stdlib-only)
# ---------------------------------------------------------------------------

def _parse_toml_value(raw: str) -> Any:
    """Parse a TOML value: string, integer, float, bool, or inline array of strings."""
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    if raw.startswith("["):
        # Inline array: ["a", "b", "c"]
        inner = raw.strip("[]")
        items = []
        for part in _split_toml_array(inner):
            part = part.strip().strip('"').strip("'")
            if part:
                items.append(part)
        return items
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _split_toml_array(inner: str) -> list[str]:
    """Split a TOML inline array body on commas, respecting quoted strings."""
    items: list[str] = []
    current: list[str] = []
    in_quote = False
    quote_char = ""
    for char in inner:
        if in_quote:
            current.append(char)
            if char == quote_char:
                in_quote = False
        elif char in ('"', "'"):
            in_quote = True
            quote_char = char
            current.append(char)
        elif char == ",":
            items.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        items.append("".join(current))
    return items
