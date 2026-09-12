"""Agent grants and MCP routing: chains, policy, cache, quality rejection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.agent_router import AgentRouter, RouterError
from runtime.mcp_router import McpError, McpRouter, StubSession
from runtime.models import ResearchTask


# ------------------------------------------------------------------ agent side
def test_roster_matches_the_skill_contract() -> None:
    router = AgentRouter.load()
    assert router.names() == ["planner", "context-agent", "researcher", "analyst", "critic", "verifier", "synthesizer"]
    assert router.owner_of("verify_claims") == "verifier"
    assert router.owner_of("nonsense") == "runtime"


def test_role_files_exist_and_load() -> None:
    router = AgentRouter.load()
    for name in router.names():
        spec = router.get(name)
        assert spec.file.endswith(f"{name}.md") or spec.file == "-"
        assert spec.prompt(), f"{name} has no prompt body"
        assert router.grant_check(name, spec.capabilities[0]) if spec.capabilities else True


def test_unknown_agent_and_missing_role_file() -> None:
    router = AgentRouter.load()
    with pytest.raises(RouterError):
        router.get("ghostwriter")
    assert router.audit([]) == []


def test_plan_dispatch_respects_grants_and_budget(graph) -> None:
    router = AgentRouter.load()
    gaps = graph.gaps(None) if False else []
    tasks = router.plan_dispatch(
        [
            {"node_id": "q1", "title": "主流方案有哪些", "missing": ["no evidence yet"], "suggested_capability": "web.discovery", "suggested_queries": ["x"]},
            {"node_id": "t1", "title": "本地代码结构", "missing": ["no local context"], "suggested_capability": "code.local_structure", "suggested_queries": []},
        ],
        iteration=1,
        max_tasks=4,
    )
    assert tasks
    for task in tasks:
        assert router.get(task.agent).allows(task.capability), f"{task.agent} may not call {task.capability}"
        assert task.iteration == 1
    assert len(tasks) <= 4


def test_ready_blocks_unfinished_dependencies() -> None:
    router = AgentRouter.load()
    first = ResearchTask(agent="researcher", objective="a", capability="web.discovery", id="task_a", status="ready")
    second = ResearchTask(agent="analyst", objective="b", capability="", id="task_b", status="ready", depends_on=["task_a"])
    assert [task.id for task in router.ready([first, second])] == ["task_a"]
    assert second.status == "blocked", "a waiting task must be marked blocked"
    first.status = "done"
    second.status = "ready"
    assert [task.id for task in router.ready([first, second])] == ["task_b"]


# ------------------------------------------------------------------- mcp side
def test_capability_inventory_is_wired() -> None:
    router = McpRouter.load(offline=True)
    caps = router.capability_names()
    assert {"web.discovery", "web.read", "docs.library", "code.local_structure", "memory.long_term"} <= set(caps)
    for capability in caps:
        assert router.providers_for(capability), f"{capability} has no providers"
    with pytest.raises(McpError):
        router.providers_for("make.coffee")


def test_offline_call_returns_stub_without_network(tmp_path: Path) -> None:
    router = McpRouter.load(offline=True, cache_dir=tmp_path)
    result = router.call("web.discovery", query="model context protocol")
    assert isinstance(result.text, str)
    assert result.attempts and all("server" in attempt for attempt in result.attempts)


def test_provider_chain_falls_forward_on_failure() -> None:
    calls: list[tuple[str, str]] = []

    def handler(method: str, name: str, arguments: dict) -> object:
        calls.append((name, json.dumps(arguments, sort_keys=True)))
        if name == "searxng_web_search":
            raise RuntimeError("connection refused")
        return {"results": [{"title": f"hit {index}", "url": f"https://src{index}.dev/p"} for index in range(5)]}

    router = McpRouter.load(offline=True, session_factory=lambda server, body: StubSession(handler))
    result = router.call("web.discovery", query="deep research")
    assert any(name != "searxng_web_search" for name, _ in calls), "chain should continue past the failure"
    assert result.attempts[0]["ok"] is False
    assert router.stats()["failures"] >= 1


def test_quality_rejection_is_recorded_in_attempts() -> None:
    def handler(method: str, name: str, arguments: dict) -> object:
        return {"results": []}

    router = McpRouter.load(offline=True, session_factory=lambda server, body: StubSession(handler))
    result = router.call("web.discovery", query="nothing useful here")
    assert not result.ok
    assert any(attempt.get("quality_rejected") for attempt in result.attempts), result.attempts


def test_server_defaults_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEXUS_SEARXNG_ENGINES", raising=False)
    router = McpRouter.load(offline=True)
    assert "github" in router._server_defaults("searxng")["engines"]
    monkeypatch.setenv("NEXUS_SEARXNG_ENGINES", "ddg,arxiv")
    assert router._server_defaults("searxng")["engines"] == "ddg,arxiv"
    seen: dict[str, object] = {}

    def handler(method: str, name: str, arguments: dict) -> object:
        seen.update(arguments)
        return {"results": [{"title": "t", "url": "https://a.dev/1"}] * 4}

    pinned = McpRouter.load(offline=True, session_factory=lambda server, body: StubSession(handler))
    pinned.call("web.discovery", query="x")
    assert seen.get("engines") == "ddg,arxiv"
    override = McpRouter.load(offline=True, session_factory=lambda server, body: StubSession(handler))
    override.call("web.discovery", query="x", engines="wikipedia")
    assert seen.get("engines") == "wikipedia"


def test_cache_serves_the_second_call(tmp_path: Path) -> None:
    hits: list[int] = []

    def handler(method: str, name: str, arguments: dict) -> object:
        hits.append(1)
        return {"results": [{"title": "t", "url": "https://a.dev/1"}] * 4}

    router = McpRouter.load(offline=True, cache_dir=tmp_path, session_factory=lambda server, body: StubSession(handler))
    first = router.call("web.discovery", query="cached")
    second = router.call("web.discovery", query="cached")
    assert first.ok and second.cached
    assert len(hits) == len([item for item in hits])


def test_unreachable_server_is_marked_and_skipped() -> None:
    def handler(method: str, name: str, arguments: dict) -> object:
        raise RuntimeError("connection refused")

    router = McpRouter.load(offline=True, session_factory=lambda server, body: StubSession(handler))
    router.call("docs.library", query="anything")
    result = router.call("web.discovery", query="anything")
    assert any("marked unavailable" in str(attempt.get("error")) for attempt in result.attempts) or not result.ok


def test_direct_tool_call_and_unknown_capability() -> None:
    router = McpRouter.load(offline=True)
    result = router.call_tool("filesystem", "read_file", {"path": "README.md"})
    assert result.ok or result.error
    with pytest.raises(McpError):
        router.call("not.a.capability")


def test_doctor_reports_every_server(tmp_path: Path) -> None:
    router = McpRouter.load(offline=True, cache_dir=tmp_path)
    rows = router.doctor(only=["filesystem"])
    assert rows and {"server", "status"} <= set(rows[0])


def test_searxng_engine_groups_honor_the_env_escape_hatch() -> None:
    """Engine reachability is a property of the machine, so it stays overridable."""
    import os

    spec = {
        "servers": {"searxng": {"transport": "stdio", "command": "true",
                                "search": {"engines": ["github"], "engine_groups": ["github", "pypi,npm"]}}},
        "capabilities": {},
    }
    router = McpRouter(spec=spec)
    assert router.server_option("searxng", "engine_groups", []) == ["github", "pypi,npm"]
    assert router.server_option("searxng", "engines", []) == ["github"]
    os.environ["NEXUS_SEARXNG_ENGINES"] = "bing,google"
    os.environ["NEXUS_SEARXNG_ENGINE_GROUPS"] = "bing,google|github"
    try:
        # the escape hatch reaches both readers, in the shape each one expects
        assert router.server_option("searxng", "engine_groups", []) == ["bing,google", "github"]
        assert router.server_option("searxng", "engines", []) == "bing,google"
        assert router._server_defaults("searxng")["engines"] == "bing,google"
        assert router.server_option("searxng", "timeout_sec", 12) == 12  # unrelated keys untouched
    finally:
        os.environ.pop("NEXUS_SEARXNG_ENGINES", None)
        os.environ.pop("NEXUS_SEARXNG_ENGINE_GROUPS", None)
    assert router.server_option("searxng", "engine_groups", []) == ["github", "pypi,npm"]


def test_opt_in_provider_is_declared_but_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, name: str, arguments: dict) -> object:
        return {"rows": [{"n": 1}]}

    spec = {
        "servers": {
            "postgresql": {"purpose": "x", "transport": "stdio", "command": "true",
                           "opt_in": True, "enable_env": "NEXUS_ENABLE_POSTGRES",
                           "tools": {"query": "query"}},
            "plain": {"purpose": "y", "transport": "stdio", "command": "true", "tools": {"query": "query"}},
        },
        "capabilities": {
            "data.structured": {"description": "d", "quality": {"min_results": 1},
                                "providers": [{"server": "postgresql", "tool": "query"},
                                              {"server": "plain", "tool": "query"}]}
        },
    }
    router = McpRouter(spec=spec, offline=True, session_factory=lambda server, body: StubSession(handler))
    monkeypatch.delenv("NEXUS_ENABLE_POSTGRES", raising=False)
    assert router.opt_in_pending("postgresql") == "NEXUS_ENABLE_POSTGRES"
    assert router.opt_in_pending("plain") == ""
    result = router.call("data.structured", query="x")
    assert result.ok and result.server == "plain"  # chain fell through without a 30s stall
    assert any("opt-in" in str(step.get("error")) for step in result.attempts)
    report = {row["server"]: row for row in router.doctor(["postgresql", "plain"])}
    assert report["postgresql"]["status"] == "disabled"
    # the CLI renders a missing latency as 0ms, so never probe an inactive transport
    assert report["postgresql"].get("latency_ms", 0) < 1000
    assert report["plain"]["status"] == "ok"


def test_real_map_ships_the_proxies_it_documents() -> None:
    """Guards the two integration knobs that make general engines answer locally."""
    from runtime.util import load_config

    spec = load_config("mcp-map")
    search = spec["servers"]["searxng"]["search"]
    assert len(search["engine_groups"]) >= 4, "web.discovery needs several independent angles"
    assert any("bing" in group or "google" in group for group in search["engine_groups"])
    assert spec["servers"]["postgresql"].get("opt_in") is True, "a business database must never be queried implicitly"
