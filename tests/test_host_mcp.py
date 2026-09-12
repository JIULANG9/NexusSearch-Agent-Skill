"""Tests for runtime.host_mcp — host agent MCP detection."""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from unittest import mock

import pytest

from runtime.host_mcp import (
    HostDetection,
    HostServer,
    _detect_claude,
    _detect_codex,
    _detect_cursor,
    _parse_toml_value,
    _split_toml_array,
    detect_host_mcp,
    match_host_to_map,
)


# ---------------------------------------------------------------------------
# TOML value parser
# ---------------------------------------------------------------------------
class TestTomlParser:
    def test_string_double_quotes(self):
        assert _parse_toml_value('"hello"') == "hello"

    def test_string_single_quotes(self):
        assert _parse_toml_value("'hello'") == "hello"

    def test_bool_true(self):
        assert _parse_toml_value("true") is True

    def test_bool_false(self):
        assert _parse_toml_value("false") is False

    def test_integer(self):
        assert _parse_toml_value("42") == 42

    def test_float(self):
        assert _parse_toml_value("3.14") == 3.14

    def test_inline_array(self):
        assert _parse_toml_value('["a", "b", "c"]') == ["a", "b", "c"]

    def test_inline_array_single_quotes(self):
        assert _parse_toml_value("['x', 'y']") == ["x", "y"]

    def test_bare_value(self):
        assert _parse_toml_value("something") == "something"

    def test_split_array_basic(self):
        assert _split_toml_array('"a", "b", "c"') == ['"a"', ' "b"', ' "c"']

    def test_split_array_empty(self):
        assert _split_toml_array("") == []


# ---------------------------------------------------------------------------
# Codex detection
# ---------------------------------------------------------------------------
class TestDetectCodex:
    def test_no_config(self, tmp_path):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(tmp_path / "nonexistent")}):
            result = _detect_codex()
        assert not result.detected or not result.servers

    def test_empty_config(self, tmp_path):
        config = tmp_path / "config.toml"
        config.write_text("# empty\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(tmp_path)}):
            result = _detect_codex()
        assert result.detected
        assert not result.servers

    def test_parses_servers(self, tmp_path):
        config = tmp_path / "config.toml"
        config.write_text(textwrap.dedent("""\
            [mcp_servers.searxng]
            command = "npx"
            args = ["-y", "mcp-searxng"]

            [mcp_servers.searxng.env]
            SEARXNG_URL = "http://127.0.0.1:8080"

            [mcp_servers.github]
            command = "npx"
            args = ["-y", "@modelcontextprotocol/server-github"]
        """), encoding="utf-8")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(tmp_path)}):
            result = _detect_codex()
        assert result.detected
        assert len(result.servers) == 2
        assert "searxng" in result.servers
        assert "github" in result.servers
        searxng = result.servers["searxng"]
        assert searxng.command == "npx"
        assert searxng.args == ["-y", "mcp-searxng"]
        assert searxng.env == {"SEARXNG_URL": "http://127.0.0.1:8080"}
        assert searxng.host == "codex"

    def test_env_subsection_not_separate_server(self, tmp_path):
        """Regression: [mcp_servers.foo.env] must not create server 'foo.env'."""
        config = tmp_path / "config.toml"
        config.write_text(textwrap.dedent("""\
            [mcp_servers.redis]
            command = "npx"
            args = ["-y", "@modelcontextprotocol/server-redis"]

            [mcp_servers.redis.env]
            REDIS_URL = "redis://localhost:6379"
        """), encoding="utf-8")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(tmp_path)}):
            result = _detect_codex()
        assert len(result.servers) == 1
        assert "redis" in result.servers
        assert "redis.env" not in result.servers
        assert result.servers["redis"].env == {"REDIS_URL": "redis://localhost:6379"}

    def test_http_transport(self, tmp_path):
        config = tmp_path / "config.toml"
        config.write_text(textwrap.dedent("""\
            [mcp_servers.cloudflare]
            type = "http"
            url = "https://mcp.cloudflare.com/mcp"
        """), encoding="utf-8")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(tmp_path)}):
            result = _detect_codex()
        assert result.servers["cloudflare"].transport == "http"
        assert result.servers["cloudflare"].url == "https://mcp.cloudflare.com/mcp"


# ---------------------------------------------------------------------------
# Claude detection
# ---------------------------------------------------------------------------
class TestDetectClaude:
    def test_no_config(self, tmp_path):
        with mock.patch("runtime.host_mcp.Path.home", return_value=tmp_path):
            result = _detect_claude()
        assert not result.detected

    def test_parses_servers(self, tmp_path):
        config_dir = tmp_path / ".config" / "claude"
        config_dir.mkdir(parents=True)
        config = config_dir / "claude_desktop_config.json"
        config.write_text(json.dumps({
            "mcpServers": {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                },
                "github": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "xxx"},
                },
            }
        }), encoding="utf-8")
        with mock.patch("runtime.host_mcp.Path.home", return_value=tmp_path):
            result = _detect_claude()
        assert result.detected
        assert result.host_name == "claude"
        assert len(result.servers) == 2
        assert result.servers["github"].env == {"GITHUB_TOKEN": "xxx"}


# ---------------------------------------------------------------------------
# Cursor detection
# ---------------------------------------------------------------------------
class TestDetectCursor:
    def test_no_config(self, tmp_path):
        with mock.patch("runtime.host_mcp.Path.home", return_value=tmp_path), \
             mock.patch("runtime.host_mcp.Path.cwd", return_value=tmp_path):
            result = _detect_cursor()
        assert not result.detected

    def test_parses_project_config(self, tmp_path):
        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        config = cursor_dir / "mcp.json"
        config.write_text(json.dumps({
            "mcpServers": {
                "playwright": {"command": "npx", "args": ["-y", "@playwright/mcp"]},
            }
        }), encoding="utf-8")
        with mock.patch("runtime.host_mcp.Path.home", return_value=tmp_path), \
             mock.patch("runtime.host_mcp.Path.cwd", return_value=tmp_path):
            result = _detect_cursor()
        assert result.detected
        assert result.host_name == "cursor"
        assert "playwright" in result.servers


# ---------------------------------------------------------------------------
# Host-to-map matching
# ---------------------------------------------------------------------------
class TestMatchHostToMap:
    def test_empty_host(self):
        host = HostDetection()
        assert match_host_to_map(host, {"searxng": {}}) == {}

    def test_exact_match(self):
        host = HostDetection(
            detected=True, host_name="codex",
            servers={"searxng": HostServer(name="searxng", host="codex"),
                     "github": HostServer(name="github", host="codex")},
        )
        result = match_host_to_map(host, {"searxng": {}, "github": {}, "filesystem": {}})
        assert result == {"searxng": "searxng", "github": "github"}
        assert "filesystem" not in result

    def test_case_insensitive(self):
        host = HostDetection(
            detected=True, host_name="codex",
            servers={"SearXNG": HostServer(name="SearXNG", host="codex")},
        )
        result = match_host_to_map(host, {"searxng": {}})
        assert result == {"searxng": "SearXNG"}


# ---------------------------------------------------------------------------
# Integration: detect_host_mcp
# ---------------------------------------------------------------------------
class TestDetectHostMcp:
    def test_codex_session_id_triggers_codex(self, tmp_path):
        config = tmp_path / "config.toml"
        config.write_text(textwrap.dedent("""\
            [mcp_servers.memory]
            command = "npx"
            args = ["-y", "@modelcontextprotocol/server-memory"]
        """), encoding="utf-8")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(tmp_path), "CODEX_SESSION_ID": "test"}):
            result = detect_host_mcp()
        assert result.detected
        assert result.host_name == "codex"
        assert "memory" in result.servers

    def test_no_host_returns_empty(self, tmp_path):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("runtime.host_mcp.Path.home", return_value=tmp_path), \
             mock.patch("runtime.host_mcp.Path.cwd", return_value=tmp_path):
            # Remove CODEX_HOME / CODEX_SESSION_ID if present
            os.environ.pop("CODEX_HOME", None)
            os.environ.pop("CODEX_SESSION_ID", None)
            result = detect_host_mcp()
        assert not result.detected or not result.servers
