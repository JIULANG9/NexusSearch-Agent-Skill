"""Local MCP router: capability chains, retries, fallbacks, caching, audit.

Design contract (AGENTS.md section 6): every MCP call carries a timeout, a
retry budget, a fallback path, and a log record. Nothing in this module talks
to a paid cloud API; servers come from ``config/mcp-map.yaml`` and credentials
are resolved from the environment at call time and never stored.

The transports are deliberately small and dependency-free (stdlib
``subprocess`` + ``urllib`` + JSON-RPC 2.0) so the skill installs on a machine
with only Python 3.10+.
"""

from __future__ import annotations

import json
import os
import queue
import random
import re
import shlex
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .util import (
    NexusError,
    append_jsonl,
    classify_error,
    clamp,
    ensure_dir,
    load_config,
    now_iso,
    read_json,
    redact,
    resolve_env,
    short,
    write_json,
)
from .host_mcp import HostDetection, detect_host_mcp, match_host_to_map

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "nexussearch-mcp-router", "version": "1.2.1"}
_PLACEHOLDER = re.compile(r"\{\{([^}]+)\}\}")


class McpError(NexusError):
    """A tool-level failure carrying an error class for policy lookup."""

    def __init__(self, message: str, error_class: str | None = None, server: str = "") -> None:
        super().__init__(message, error_class or classify_error(message))
        self.server = server


@dataclass
class McpResult:
    """Outcome of one capability call, including the whole fallback trail."""

    capability: str
    server: str
    tool: str
    ok: bool
    text: str = ""
    data: Any = None
    duration_ms: int = 0
    retries: int = 0
    attempts: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    error_class: str = ""
    degraded: bool = False
    cached: bool = False
    arguments: dict[str, Any] = field(default_factory=dict)
    result_count: int = -1
    retrieved_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return redact(
            {
                "capability": self.capability,
                "server": self.server,
                "tool": self.tool,
                "ok": self.ok,
                "duration_ms": self.duration_ms,
                "retries": self.retries,
                "error": self.error,
                "error_class": self.error_class,
                "degraded": self.degraded,
                "cached": self.cached,
                "arguments": self.arguments,
                "result_count": self.result_count,
                "attempts": self.attempts,
            }
        )


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------
class StdioSession:
    """Newline-delimited JSON-RPC 2.0 client over a subprocess' stdio."""

    def __init__(self, command: list[str], env: dict[str, str], timeout: float = 30.0) -> None:
        self.command = [str(part) for part in command]
        self.env = env
        self.timeout = timeout
        self._proc: subprocess.Popen[str] | None = None
        self._queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._reader: threading.Thread | None = None
        self._counter = 0
        self._lock = threading.Lock()
        self.tools: list[dict[str, Any]] = []

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._proc is not None:
            return
        resolved = {key: resolve_env(value) for key, value in self.env.items()}
        merged = {**os.environ, **{key: value for key, value in resolved.items() if value}}
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - command comes from local config
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=merged,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise McpError(f"cannot spawn {self.command[0]}: {exc}", "unavailable") from exc
        self._queue = queue.Queue()
        self._reader = threading.Thread(target=self._pump, args=(self._proc.stdout,), daemon=True)
        self._reader.start()
        self.initialize()

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:  # noqa: BLE001 - best effort shutdown
            pass
        try:
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            proc.kill()

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # -------------------------------------------------------------- protocol
    def initialize(self) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )
        self.notify("notifications/initialized", {})
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        payload = self.request("tools/list", {})
        self.tools = list(payload.get("tools") or [])
        return self.tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, Any, bool]:
        payload = self.request("tools/call", {"name": name, "arguments": arguments})
        return flatten_content(payload)

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None:
            raise McpError("session not started", "unavailable")
        with self._lock:
            self._counter += 1
            request_id = self._counter
            frame = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            try:
                self._proc.stdin.write(frame + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, ValueError) as exc:
                raise McpError(f"transport closed: {exc}", "unavailable") from exc
        return self._await(request_id)

    def notify(self, method: str, params: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        frame = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        try:
            self._proc.stdin.write(frame + "\n")
            self._proc.stdin.flush()
        except Exception:  # noqa: BLE001 - notifications are best effort
            pass

    # -------------------------------------------------------------- internals
    def _await(self, request_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = self._get(max(0.05, deadline - time.monotonic()))
            if message is None or message.get("id") != request_id:
                continue
            if "error" in message:
                err = message["error"] or {}
                detail = str(err.get("message") or err)
                raise McpError(detail, classify_error(detail))
            result = message.get("result")
            return result if isinstance(result, dict) else {"value": result}
        raise McpError(f"timeout after {self.timeout:.0f}s waiting for response {request_id}", "timeout")

    def _get(self, timeout: float) -> dict[str, Any] | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _pump(self, stream: Any) -> None:
        if stream is None:
            return
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                self._queue.put(payload)


class HttpSession:
    """Minimal MCP streamable-HTTP client (POST JSON-RPC, optional SSE frames)."""

    def __init__(self, url: str, env: dict[str, str], timeout: float = 30.0) -> None:
        self.url = resolve_env(url)
        self.env = env
        self.timeout = timeout
        self.session_id = ""
        self.tools: list[dict[str, Any]] = []

    def start(self) -> None:
        if self.url:
            self.initialize()

    def stop(self) -> None:
        return None

    def alive(self) -> bool:
        return True

    def initialize(self) -> dict[str, Any]:
        return self.request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )

    def list_tools(self) -> list[dict[str, Any]]:
        payload = self.request("tools/list", {})
        self.tools = list(payload.get("tools") or [])
        return self.tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, Any, bool]:
        payload = self.request("tools/call", {"name": name, "arguments": arguments})
        return flatten_content(payload)

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.url:
            raise McpError("http server has no url", "invalid_input")
        body = json.dumps({"jsonrpc": "2.0", "id": int(time.time_ns() % 1000000), "method": method, "params": params})
        headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        for key, value in self.env.items():
            resolved = resolve_env(value)
            if resolved:
                headers[key.lower()] = resolved
        request = urllib.request.Request(self.url, data=body.encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                session = response.headers.get("mcp-session-id")
                if session:
                    self.session_id = str(session)
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300] if exc.fp else str(exc)
            raise McpError(f"http {exc.code}: {detail}", classify_error(f"{exc.code} {detail}")) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise McpError(f"unreachable: {exc}", "unavailable") from exc
        payload = parse_json_or_sse(raw)
        if "error" in payload:
            err = payload["error"] or {}
            detail = str(err.get("message") or err)
            raise McpError(detail, classify_error(detail))
        result = payload.get("result")
        return result if isinstance(result, dict) else {"value": result}


class StubSession:
    """Deterministic offline session used by tests and ``--offline`` runs."""

    def __init__(self, handler: Callable[[str, str, dict[str, Any]], Any] | None = None) -> None:
        self.handler = handler
        self.tools: list[dict[str, Any]] = [{"name": "stub", "description": "offline stub"}]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def alive(self) -> bool:
        return True

    def initialize(self) -> dict[str, Any]:
        return {"serverInfo": CLIENT_INFO}

    def list_tools(self) -> list[dict[str, Any]]:
        return self.tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, Any, bool]:
        self.calls.append((name, arguments))
        if self.handler is None:
            return json.dumps({"stub": True, "tool": name, "arguments": arguments}), None, False
        value = self.handler("tools/call", name, arguments)
        if isinstance(value, str):
            return value, None, False
        return json.dumps(value, ensure_ascii=False), value, False


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
@dataclass
class McpRouter:
    """Walk capability chains against local MCP servers with policy enforcement."""

    spec: dict[str, Any] = field(default_factory=dict)
    offline: bool = False
    cache_path: Path | None = None
    audit_path: Path | None = None
    session_factory: Callable[[str, dict[str, Any]], Any] | None = None
    _sessions: dict[str, Any] = field(default_factory=dict, repr=False)
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)
    _unavailable: set[str] = field(default_factory=set, repr=False)
    _counters: dict[str, int] = field(
        default_factory=lambda: {"calls": 0, "failures": 0, "retries": 0}, repr=False
    )
    _host: HostDetection = field(default_factory=HostDetection, repr=False)
    _host_match: dict[str, str] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ setup
    @classmethod
    def load(
        cls,
        *,
        offline: bool = False,
        workspace: Any | None = None,
        cache_dir: str | Path | None = None,
        session_factory: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> "McpRouter":
        spec = load_config("mcp-map")
        router = cls(spec=spec, offline=offline, session_factory=session_factory)
        if workspace is not None:
            router.audit_path = workspace.audit_log()
            router.cache_path = ensure_dir(workspace.root / "state" / "cache") / "mcp-cache.json"
        elif cache_dir is not None:
            router.cache_path = ensure_dir(Path(cache_dir)) / "mcp-cache.json"
        if router.cache_path is not None:
            cached = read_json(router.cache_path, default=None)
            router._cache = cached if isinstance(cached, dict) else {}
        # Detect host agent MCP servers — if the user's host already provides
        # these servers, doctor can report them without extra probing.
        try:
            router._host = detect_host_mcp()
            router._host_match = match_host_to_map(router._host, router.servers())
        except Exception:  # noqa: BLE001 - host detection must never break the router
            pass
        return router

    # -------------------------------------------------------------- inventory
    def servers(self) -> dict[str, Any]:
        return dict(self.spec.get("servers") or {})

    def server_option(self, server: str, key: str, default: Any = None) -> Any:
        """Value from ``servers.<server>.search``, with the environment applied.

        Same ``NEXUS_<SERVER>_<KEY>`` convention as :meth:`_server_defaults`, so a
        raw-option reader and the argument templater cannot disagree about which
        engines are reachable: engine reachability is a property of the machine's
        egress path and changes without the map changing. ``engine_groups`` uses
        ``group1|group2`` because a comma inside a group is meaningful there.
        """
        body = (self.servers().get(server) or {}).get("search") or {}
        value = body.get(key, default)
        raw = os.environ.get(f"NEXUS_{server.upper().replace('-', '_')}_{key.upper()}")
        if raw is None:
            return value
        if key.endswith("groups"):
            groups = [chunk.strip() for chunk in raw.split("|") if chunk.strip()]
            return groups or default
        return _unstring(raw, value)

    def opt_in_pending(self, server: str) -> str:
        """Return the enable-variable name when a server is opt-in and not enabled.

        A declared-but-inactive provider must never cost a 30s transport timeout in
        the middle of a research chain, and never be reached implicitly: research
        on a local business database is opt-in per run (see AGENTS.md 2.1, user data
        stays out of reports unless the spec explicitly asks for it).
        """
        body = self.servers().get(server) or {}
        if not body.get("opt_in"):
            return ""
        name = str(body.get("enable_env") or "NEXUS_ENABLE_%s" % server.upper().replace("-", "_"))
        return "" if os.environ.get(name) else name

    def capabilities(self) -> dict[str, Any]:
        return dict(self.spec.get("capabilities") or {})

    def capability_names(self) -> list[str]:
        return list(self.capabilities())

    def providers_for(self, capability: str) -> list[dict[str, Any]]:
        entry = self.capabilities().get(capability)
        if not entry:
            raise McpError(f"unknown capability: {capability}", "invalid_input")
        return list(entry.get("providers") or [])

    def required_arguments(self, capability: str) -> list[str]:
        """Argument names a caller must supply for this capability to be runnable."""
        wanted: list[str] = []
        for provider in self.providers_for(capability):
            defaults = self._server_defaults(str(provider.get("server") or ""))
            for name in _missing_arguments(provider, {}, defaults):
                if name not in wanted:
                    wanted.append(name)
        return wanted

    def default(self, key: str, fallback: Any = None) -> Any:
        return (self.spec.get("defaults") or {}).get(key, fallback)

    def policy_for(self, error_class: str) -> dict[str, Any]:
        return dict((self.spec.get("error_policies") or {}).get(error_class) or {"action": "fallback"})

    # -------------------------------------------------------------- execution
    def call(self, capability: str, **arguments: Any) -> McpResult:
        """Try each provider in the chain until one yields usable output."""
        if capability not in self.capabilities():
            raise McpError(f"unknown capability: {capability}", "invalid_input")
        arguments = _absolutise_paths(arguments)
        entry = self.capabilities()[capability]
        chain = self.providers_for(capability)
        started = time.monotonic()
        attempts: list[dict[str, Any]] = []

        cache_key = _cache_key(capability, arguments)
        cached = self._cache_get(cache_key, int(self.default("cache_ttl_sec", 900) or 0))
        if cached is not None:
            result = McpResult(
                capability=capability,
                server=str(cached.get("server", "cache")),
                tool=str(cached.get("tool", "")),
                ok=True,
                text=str(cached.get("text", "")),
                data=cached.get("data"),
                cached=True,
                attempts=[{"attempt": 0, "server": "cache", "tool": str(cached.get("tool", "")), "ok": True}],
            )
            return self._finish(result, started, arguments)

        last_error = ""
        last_class = "unknown"
        for index, provider in enumerate(chain, start=1):
            server = str(provider.get("server") or "")
            if server in self._unavailable:
                attempts.append({"attempt": index, "server": server, "ok": False, "error": "marked unavailable"})
                continue
            pending_env = self.opt_in_pending(server)
            if pending_env:
                self._unavailable.add(server)
                attempts.append(
                    {"attempt": index, "server": server, "ok": False, "error": f"opt-in provider off (set {pending_env}=1)"}
                )
                continue
            tool_name = self._tool_name(server, str(provider.get("tool") or ""))
            args = self._arguments(provider, arguments)
            if args is None:
                missing = ", ".join(_missing_arguments(provider, arguments, self._server_defaults(server)))
                attempts.append(
                    {
                        "attempt": index,
                        "server": server,
                        "tool": tool_name,
                        "ok": False,
                        "error": f"missing argument: {missing or 'unknown'}",
                    }
                )
                hint = f" (supply --args with the key: {missing.split(',')[0]})" if missing else ""
                last_error, last_class = f"{server}.{tool_name} needs {missing}{hint}", "invalid_input"
                continue
            ok, text, data, error, error_class, retries = self._attempt_with_policy(
                server, tool_name, args, provider, attempts, index
            )
            if ok:
                usable, reason = _passes_quality(text, data, entry.get("quality") or {})
                if usable:
                    return self._success(
                        capability,
                        server,
                        tool_name,
                        text,
                        data,
                        retries,
                        attempts,
                        started,
                        cache_key,
                        arguments=arguments,
                    )
                error, error_class = reason or "low quality result", "low_quality"
                attempts.append(
                    {
                        "attempt": index,
                        "server": server,
                        "tool": tool_name,
                        "ok": False,
                        "retries": retries,
                        "error": error,
                        "quality_rejected": True,
                    }
                )
                retry_result = self._reformulate(
                    capability, entry, server, tool_name, args, attempts, index, cache_key
                )
                if retry_result is not None:
                    return self._finish(retry_result, started, arguments)
            if error_class in {"permission", "unavailable", "auth_missing"}:
                self._unavailable.add(server)
            last_error, last_class = error, error_class or "unknown"

        fallback = self._local_fallback(capability, arguments)
        if fallback is not None:
            text, data = fallback
            result = McpResult(
                capability=capability,
                server="local-fetch-adapter",
                tool="urllib",
                ok=True,
                text=text,
                data=data,
                degraded=True,
                attempts=attempts,
            )
            return self._finish(result, started, arguments)

        unusable = sum(
            1
            for attempt in attempts
            if attempt.get("quality_rejected") or "candidate sources" in str(attempt.get("error") or "")
        )
        if unusable and last_class in {"invalid_input", "low_quality"}:
            # A trailing provider that was never runnable must not be reported as the
            # research outcome: the chain ran, it just found nothing usable.
            skipped = sum(1 for attempt in attempts if "missing argument" in str(attempt.get("error") or ""))
            last_class = "no_results"
            last_error = (
                f"{len(chain)} providers tried, {unusable} returned no usable candidates"
                + (f", {skipped} skipped for missing arguments" if skipped else "")
                + f"; last error: {short(str(last_error), 70)}"
            )
        result = McpResult(
            capability=capability,
            server="",
            tool="",
            ok=False,
            error=last_error or "all providers exhausted",
            error_class=last_class,
            attempts=attempts,
        )
        self._counters["failures"] += 1
        return self._finish(result, started, arguments)

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any] | None = None) -> McpResult:
        """Invoke one server tool directly (used by ``nexus mcp call``)."""
        started = time.monotonic()
        arguments = _absolutise_paths(arguments or {})
        body = self.servers().get(server) or {}
        name = tool if tool in self._declared_tools(server) else self._tool_name(server, tool)
        attempts: list[dict[str, Any]] = []
        ok, text, data, error, error_class, retries = self._attempt_with_policy(
            server, name, arguments or {}, {"server": server, "tool": tool}, attempts, 1
        )
        if ok:
            return self._success(
                "direct", server, name, text, data, retries, attempts, started, arguments=arguments
            )
        result = McpResult(
            capability="direct",
            server=server,
            tool=name,
            ok=False,
            error=error,
            error_class=error_class,
            retries=retries,
        )
        return self._finish(result, started, arguments)

    # ----------------------------------------------------------------- doctor
    def doctor(self, only: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Probe servers and report reachability without doing research work."""
        report: list[dict[str, Any]] = []
        wanted = {str(name) for name in only} if only else None
        for name, body in self.servers().items():
            if wanted and name not in wanted:
                continue
            record: dict[str, Any] = {
                "server": name,
                "transport": str(body.get("transport") or "stdio"),
                "tier": body.get("tier", 4),
                "purpose": str(body.get("purpose") or ""),
                "declared_tools": sorted(str(item) for item in (body.get("tools") or {}).values()),
            }
            pending_env = self.opt_in_pending(name)
            if pending_env:
                record.update(status="disabled", detail=f"opt-in provider, off by default (set {pending_env}=1 to enable)")
                report.append(record)
                continue
            missing_env = [key for key in body.get("requires_env") or [] if not resolve_env("${%s}" % key)]
            if missing_env:
                record.update(status="auth_missing", detail="missing env: " + ", ".join(missing_env))
                report.append(record)
                continue
            started = time.monotonic()
            probe = body.get("probe") or {}
            host_server = self._host_match.get(name)
            try:
                if probe.get("kind") == "http":
                    ok, detail = _probe_http(resolve_env(str(probe.get("url") or "")), 6.0)
                elif self.offline:
                    ok, detail = True, "offline mode (probe skipped)"
                else:
                    session = self._session(name, body)
                    ok, detail = True, f"{len(session.list_tools())} tools"
                record["status"] = "ok" if ok else "unavailable"
                record["detail"] = detail
                if ok and host_server:
                    record["detail"] += f" (via {self._host.host_name})"
                    record["via_host"] = self._host.host_name
            except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
                kind = classify_error(str(exc))
                record["status"] = kind if kind != "unknown" else "unavailable"
                record["detail"] = str(exc)[:240]
                if host_server:
                    record["detail"] += f" | host {self._host.host_name} has this configured but it is not responding"
            if body.get("known_state") and record["status"] != "ok":
                record["detail"] += f" | last known state: {body['known_state']}"
            record["latency_ms"] = int((time.monotonic() - started) * 1000)
            report.append(record)
        return report

    def stats(self) -> dict[str, Any]:
        return {
            **self._counters,
            "unavailable_servers": sorted(self._unavailable),
            "cache_entries": len(self._cache),
            "offline": self.offline,
        }

    def host_info(self) -> dict[str, Any]:
        """Return host detection results for CLI display."""
        return {
            "detected": self._host.detected,
            "host_name": self._host.host_name,
            "config_path": self._host.config_path,
            "servers": self._host.server_names(),
            "matched": dict(self._host_match),
            "detail": self._host.detail,
        }

    def close(self) -> None:
        for session in list(self._sessions.values()):
            try:
                session.stop()
            except Exception:  # noqa: BLE001
                pass
        self._sessions.clear()
        if self.cache_path is not None:
            try:
                write_json(self.cache_path, self._cache)
            except Exception:  # noqa: BLE001 - the cache is disposable
                pass

    # ---------------------------------------------------------------- helpers
    def _success(
        self,
        capability: str,
        server: str,
        tool: str,
        text: str,
        data: Any,
        retries: int,
        attempts: list[dict[str, Any]],
        started: float,
        cache_key: str = "",
        arguments: dict[str, Any] | None = None,
    ) -> McpResult:
        result = McpResult(
            capability=capability,
            server=server,
            tool=tool,
            ok=True,
            text=text,
            data=data,
            retries=retries,
            attempts=attempts,
        )
        if cache_key:
            self._cache_put(cache_key, {"server": server, "tool": tool, "text": text, "data": data})
        return self._finish(result, started, arguments)

    def _finish(
        self, result: McpResult, started: float, arguments: dict[str, Any] | None = None
    ) -> McpResult:
        result.duration_ms = int((time.monotonic() - started) * 1000)
        if arguments and not result.arguments:
            result.arguments = _audit_arguments(arguments)
        if result.result_count < 0:
            result.result_count = _result_count(result.data)
        self._audit(result, started)
        return result

    def _reformulate(
        self,
        capability: str,
        entry: dict[str, Any],
        server: str,
        tool_name: str,
        args: dict[str, Any],
        attempts: list[dict[str, Any]],
        index: int,
        cache_key: str = "",
    ) -> McpResult | None:
        policy = self.policy_for("low_quality")
        for step in range(int(policy.get("max", 1) or 0)):
            repaired = _tighten(args)
            if not repaired or repaired == args:
                break
            ok, text, data, error, _class, _retries = self._attempt_with_policy(
                server, tool_name, repaired, {"server": server, "tool": tool_name}, attempts, index
            )
            attempts.append(
                {
                    "attempt": index,
                    "server": server,
                    "tool": tool_name,
                    "ok": ok,
                    "retries": step + 1,
                    "strategy": "reformulate",
                    "error": error,
                }
            )
            if ok:
                usable, reason = _passes_quality(text, data, entry.get("quality") or {})
                if usable:
                    result = McpResult(
                        capability=capability,
                        server=server,
                        tool=tool_name,
                        ok=True,
                        text=text,
                        data=data,
                        retries=step + 1,
                        attempts=attempts,
                    )
                    if cache_key:
                        self._cache_put(cache_key, {"server": server, "tool": tool_name, "text": text, "data": data})
                    return result
        return None

    def _attempt_with_policy(
        self,
        server: str,
        tool_name: str,
        args: dict[str, Any],
        provider: dict[str, Any],
        attempts: list[dict[str, Any]],
        index: int,
    ) -> tuple[bool, str, Any, str, str, int]:
        body = self.servers().get(server) or {}
        timeout = float(
            provider.get("timeout_sec") or body.get("timeout_sec") or self.default("timeout_sec", 30)
        )
        # A slow search engine does not get faster on retry, so servers may cap
        # retries locally; the global default still applies where they do not.
        max_retries = int(
            provider.get("retries")
            if provider.get("retries") is not None
            else body.get("retries")
            if body.get("retries") is not None
            else self.default("retries", 3)
            or 0
        )
        last_error = ""
        last_class = ""
        retries = 0
        for attempt in range(max_retries + 1):
            try:
                session = self._session(server, body, timeout)
                text, data, is_error = session.call_tool(tool_name, args)
                if is_error:
                    raise McpError(text or f"{server}.{tool_name} reported an error", "unknown", server)
                return True, text, data, "", "", retries
            except McpError as exc:
                last_error, last_class = str(exc), exc.error_class
                retries = attempt
                action = self.policy_for(exc.error_class).get("action", "fallback")
                if attempt >= max_retries or action not in {"retry", "backoff"}:
                    break
                time.sleep(min(_backoff_ms(self, attempt) / 1000.0, 8.0))
            except Exception as exc:  # noqa: BLE001 - normalise transport bugs into McpError
                last_error, last_class = str(exc), classify_error(str(exc))
                break
        self._counters["failures"] += 1
        self._counters["retries"] += retries
        attempts.append(
            {"attempt": index, "server": server, "tool": tool_name, "ok": False, "retries": retries, "error": last_error}
        )
        return False, "", None, last_error, last_class or "unknown", retries

    def _session(self, server: str, body: dict[str, Any], timeout: float | None = None) -> Any:
        if server in self._sessions:
            return self._sessions[server]
        if self.session_factory is not None:
            session = self.session_factory(server, body)
            self._sessions[server] = session
            return session
        if self.offline:
            session = StubSession()
            self._sessions[server] = session
            return session
        transport = str(body.get("transport") or "stdio")
        resolved_timeout = float(timeout or self.default("timeout_sec", 30))
        env = {str(key): str(value) for key, value in (body.get("env") or {}).items()}
        if transport in {"http", "sse", "streamable-http"}:
            session: Any = HttpSession(str(body.get("url") or ""), env, resolved_timeout)
        else:
            command = self._command(server, body)
            if not shutil.which(command[0]):
                raise McpError(f"command not found: {command[0]}", "unavailable", server)
            session = StdioSession(command, env, resolved_timeout)
        session.start()
        self._sessions[server] = session
        return session

    def _command(self, server: str, body: dict[str, Any]) -> list[str]:
        parts = [str(body.get("command") or "")] + [str(item) for item in (body.get("args") or [])]
        expanded: list[str] = []
        substitutions = {"home": str(Path.home()), "workspace": os.getcwd()}
        for part in parts:
            rendered = resolve_env(_fill(part, substitutions))
            if rendered:
                expanded.append(rendered)
        if not expanded or not expanded[0]:
            raise McpError(f"server {server} has no command", "invalid_input", server)
        flattened: list[str] = []
        for part in expanded:
            flattened.extend(shlex.split(part) if " " in part else [part])
        return flattened

    def _tool_name(self, server: str, key: str) -> str:
        declared = (self.servers().get(server) or {}).get("tools") or {}
        if key in declared:
            return str(declared[key])
        return key or ""

    def _declared_tools(self, server: str) -> set[str]:
        declared = (self.servers().get(server) or {}).get("tools") or {}
        return {str(value) for value in declared.values()}

    def _server_defaults(self, server: str) -> dict[str, Any]:
        """Server-level search defaults, overridable per key by the environment.

        ``search: {engines: [...], categories: it}`` in ``mcp-map.yaml`` becomes
        ``NEXUS_<SERVER>_ENGINES`` / ``NEXUS_<SERVER>_CATEGORIES``. Values keep
        their YAML type: MCP servers type-check arguments, so ``num_results: 20``
        must stay an integer (``"20"`` is rejected as "Invalid arguments").
        """
        body = self.servers().get(server) or {}
        out: dict[str, Any] = {}
        for key, value in (body.get("search") or {}).items():
            if key.startswith("probe"):
                continue
            env_key = f"NEXUS_{server.upper().replace('-', '_')}_{key.upper()}"
            raw = os.environ.get(env_key)
            if raw is not None:
                value = _unstring(raw, value)
            if isinstance(value, (list, tuple)):
                value = ",".join(str(item) for item in value)
            if value not in (None, ""):
                out[str(key)] = value
        return out

    def _arguments(self, provider: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any] | None:
        """Merge templated provider args with caller kwargs; ``None`` = unrunnable."""
        merged: dict[str, Any] = {}
        defaults = self._server_defaults(str(provider.get("server") or ""))
        for key, value in (provider.get("args") or {}).items():
            if isinstance(value, str) and "{{" in value:
                # A placeholder with no inline fallback that resolves to nothing is
                # a missing argument, even inside a compound template: an empty
                # `q=` must not silently turn into a request for empty results.
                for name in _PLACEHOLDER.findall(value):
                    field = name.strip().partition("|")[0]
                    if field in supplied and supplied[field] not in (None, ""):
                        continue
                    if field in (defaults or {}) and defaults[field] not in (None, ""):
                        continue
                    if "|" in name or (key and key in supplied and supplied[key] not in (None, "")):
                        continue
                    return None
                rendered = _fill(value, supplied, key, defaults)
                if rendered is None or rendered == "":
                    return None
                name = _PLACEHOLDER.search(value)
                reference = defaults.get(name.group(1).strip().partition("|")[0]) if name else None
                merged[key] = _unstring(rendered, reference)
            else:
                merged[key] = value
        for key, value in supplied.items():
            if key.startswith("_") or value is None:
                continue
            merged.setdefault(key, value)
        return merged

    def _local_fallback(self, capability: str, arguments: dict[str, Any]) -> tuple[str, Any] | None:
        chain = {str(provider.get("server")) for provider in self.providers_for(capability)}
        if "fetch" not in chain or self.offline:
            return None
        url = resolve_env(str(arguments.get("url") or ""))
        if not url.startswith(("http://", "https://")):
            return None
        try:
            return local_fetch_adapter(url), None
        except Exception:  # noqa: BLE001 - fallback is best effort
            return None

    # ------------------------------------------------------------ cache/audit
    def _cache_get(self, key: str, ttl: int) -> dict[str, Any] | None:
        if not self.default("cache", True) or ttl <= 0:
            return None
        item = self._cache.get(key)
        if not isinstance(item, dict):
            return None
        if time.time() - float(item.get("_stored_at") or 0) > ttl:
            self._cache.pop(key, None)
            return None
        return item

    def _cache_put(self, key: str, value: dict[str, Any]) -> None:
        if not self.default("cache", True):
            return
        self._cache[key] = redact({**value, "_stored_at": time.time()})
        if len(self._cache) > 500:
            stale = sorted(self._cache, key=lambda item: self._cache[item].get("_stored_at") or 0)[:100]
            for key in stale:
                self._cache.pop(key, None)

    def _audit(self, result: McpResult, started: float) -> None:
        self._counters["calls"] += 1
        if self.audit_path is None:
            return
        record = result.to_dict()
        record["event"] = "mcp_call"
        record["at"] = now_iso()
        record["duration_ms"] = result.duration_ms or int((time.monotonic() - started) * 1000)
        try:
            append_jsonl(self.audit_path, record)
        except Exception:  # noqa: BLE001 - auditing must never break research
            pass


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------
#: Argument keys that identify *what* was asked, without logging whole payloads.
_AUDIT_ARG_KEYS = ("query", "url", "uri", "path", "package", "owner", "repo", "symbol", "pattern", "node")


def _audit_arguments(arguments: dict[str, Any], limit: int = 150) -> dict[str, str]:
    """Compact, redacted digest of a capability call so a log reader can audit intent."""
    brief: dict[str, str] = {}
    for key in _AUDIT_ARG_KEYS:
        value = arguments.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            brief[key] = short(" ".join(str(value).split()), limit)
    engines = arguments.get("engines") or arguments.get("method")
    if isinstance(engines, str) and engines.strip():
        brief["engines"] = short(engines, 60)
    return dict(redact(brief))


#: Argument keys that name a filesystem location.
PATH_ARG_KEYS = ("path", "dir", "directory", "file_path", "filepath", "projectPath", "target")


def _absolutise_paths(arguments: dict[str, Any]) -> dict[str, Any]:
    """Resolve relative filesystem arguments against the caller's working directory.

    Every local MCP server roots a bare relative name somewhere else — the
    filesystem server used its own allowed root, so ``{"path":
    "examples/demo-project"}`` became ``~/examples/demo-project`` and failed with
    ENOENT. Resolving once here keeps a capability call reproducible from the shell.
    """
    resolved = dict(arguments)
    for key in PATH_ARG_KEYS:
        value = resolved.get(key)
        if isinstance(value, str) and value.strip():
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                resolved[key] = str((Path.cwd() / candidate).resolve())
    return resolved


def _result_count(data: Any) -> int:
    """How many items a discovery/read call actually produced (-1 = not countable)."""
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("results", "items", "nodes", "matches", "entries"):
            if isinstance(data.get(key), list):
                return len(data[key])
        return 1 if data else 0
    if isinstance(data, str):
        return 1 if data.strip() else 0
    return -1


def local_fetch_adapter(url: str, timeout: float = 20.0, max_chars: int = 40000) -> str:
    """Dependency-free text extraction used when the fetch MCP is unavailable."""
    request = urllib.request.Request(
        resolve_env(url),
        headers={"user-agent": "NexusSearch/1.0 (local research skill)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read(2_000_000).decode(charset, "replace")
    return html_to_text(raw)[:max_chars]


def html_to_text(html: str) -> str:
    """Crude but dependency-free HTML-to-text for degraded evidence capture."""
    html = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<(br|/p|/div|/li|/h[1-6]|/tr)[^>]*>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    for entity, replacement in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&#39;", "'"),
        ("&quot;", '"'),
    ):
        text = text.replace(entity, replacement)
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def flatten_content(payload: dict[str, Any]) -> tuple[str, Any, bool]:
    """Return ``(text, structured, is_error)`` from an MCP ``tools/call`` result."""
    if not isinstance(payload, dict):
        return str(payload), None, False
    is_error = bool(payload.get("isError"))
    structured = payload.get("structuredContent")
    chunks: list[str] = []
    for item in payload.get("content") or []:
        if isinstance(item, dict):
            if item.get("type") == "text":
                chunks.append(str(item.get("text", "")))
            elif item.get("type") == "image":
                chunks.append(f"[image {item.get('mimeType', 'image')}]")
            else:
                chunks.append(json.dumps(item, ensure_ascii=False))
        else:
            chunks.append(str(item))
    text = "\n".join(part for part in chunks if part)
    if not text and structured is not None:
        text = json.dumps(structured, ensure_ascii=False)
    if structured is None and text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        structured = parsed if isinstance(parsed, (dict, list)) else None
    return text, structured, is_error


def parse_json_or_sse(raw: str) -> dict[str, Any]:
    """Accept either a JSON body or an SSE stream and return one JSON-RPC envelope."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    if raw.startswith("{") or raw.startswith("["):
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {"result": payload}
        except json.JSONDecodeError:
            return {"result": raw}
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if not body or body == "[DONE]":
            continue
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and ("result" in payload or "error" in payload):
            return payload
    return {"result": raw}


def _passes_quality(text: str, data: Any, quality: dict[str, Any]) -> tuple[bool, str]:
    if not (text or data):
        return False, "empty response"
    minimum_chars = int(quality.get("min_chars", 0) or 0)
    if minimum_chars and len(text) < minimum_chars:
        return False, f"response shorter than {minimum_chars} chars"
    minimum_results = int(quality.get("min_results", 0) or 0)
    if minimum_results:
        items = _candidate_items(text, data)
        if len(items) < minimum_results:
            return False, f"only {len(items)} candidate sources (< {minimum_results})"
    if quality.get("require_url") and "http" not in text:
        return False, "no urls in response"
    return True, ""


def _candidate_items(text: str, data: Any) -> list[Any]:
    payload = data
    if payload is None:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return [line for line in text.splitlines() if "http" in line]
    if isinstance(payload, dict):
        for key in ("results", "data", "items", "urllist", "answers", "repositories"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload] if payload else []
    if isinstance(payload, list):
        return payload
    return []


def _unstring(text: str, reference: Any) -> Any:
    """Restore the YAML type of a defaulted argument that passed through a string.

    Templated values and environment overrides arrive as strings, but MCP servers
    type-check their inputs: ``num_results="20"`` is refused while ``20`` works.
    """
    if isinstance(reference, bool):
        lowered = text.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
        return text
    if isinstance(reference, int) and not isinstance(reference, bool):
        try:
            return int(text.strip())
        except ValueError:
            return text
    if isinstance(reference, float):
        try:
            return float(text.strip())
        except ValueError:
            return text
    return text


def _fill(
    value: str,
    supplied: dict[str, Any],
    key: str = "",
    defaults: dict[str, str] | None = None,
) -> str:
    """Resolve ``{{field}}`` / ``{{field|default}}`` placeholders.

    Lookup order: call arguments, server-level ``search:`` defaults (which the
    environment can override), the argument named after the target key, then the
    inline literal after ``|``.
    """

    def _sub(match: re.Match[str]) -> str:
        name, _, fallback = match.group(1).strip().partition("|")
        for source in (supplied, defaults or {}):
            if name in source and source[name] not in (None, ""):
                return str(source[name])
        if key and key in supplied and supplied[key] not in (None, ""):
            return str(supplied[key])
        return fallback

    return _PLACEHOLDER.sub(_sub, value)


def _missing_arguments(provider: dict[str, Any], supplied: dict[str, Any], defaults: dict[str, str] | None = None) -> list[str]:
    """Which ``{{placeholders}}`` in a provider template cannot be resolved."""
    missing: list[str] = []
    for key, value in (provider.get("args") or {}).items():
        if not (isinstance(value, str) and "{{" in value):
            continue
        for name in (match.group(1).strip().partition("|")[0] for match in _PLACEHOLDER.finditer(value)):
            has_fallback = "|" in match_text(value, name)
            if name in supplied and supplied[name] not in (None, ""):
                continue
            if defaults and defaults.get(name) not in (None, ""):
                continue
            if key in supplied and supplied[key] not in (None, ""):
                continue
            if not has_fallback and name not in missing:
                missing.append(name)
    return missing


def match_text(value: str, name: str) -> str:
    for match in _PLACEHOLDER.finditer(value):
        if match.group(1).strip().partition("|")[0] == name:
            return match.group(0)
    return ""


def _tighten(args: dict[str, Any]) -> dict[str, Any] | None:
    query = args.get("query")
    if isinstance(query, str) and len(query.split()) > 3:
        return {**args, "query": " ".join(query.split()[:4])}
    return None


def _cache_key(capability: str, arguments: dict[str, Any]) -> str:
    clean = {
        key: value
        for key, value in sorted(arguments.items())
        if not key.startswith("_") and value is not None
    }
    digest = json.dumps(clean, ensure_ascii=False, sort_keys=True, default=str)[:300]
    return f"{capability}:{digest}"


def _backoff_ms(router: McpRouter, attempt: int) -> float:
    base = float(router.default("backoff_ms", 500) or 500)
    multiplier = float(router.default("backoff_multiplier", 2.0) or 2.0)
    delay = base * (multiplier**attempt)
    if router.default("jitter", True):
        delay *= random.uniform(0.8, 1.3)
    return clamp(delay, 0, 8000)


def _probe_http(url: str, timeout: float = 6.0) -> tuple[bool, str]:
    if not url:
        return False, "no probe url"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            body = response.read(4096).decode("utf-8", "replace")
            return 200 <= response.status < 400, f"HTTP {response.status}, {len(body)} bytes"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]
