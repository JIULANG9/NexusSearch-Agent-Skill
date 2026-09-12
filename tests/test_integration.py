"""Integration: the PRP section 14 acceptance list, driven like a host agent would."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime import harvest
from runtime.agent_router import AgentRouter
from runtime.cli import Session
from runtime.loop_controller import LoopController
from runtime.mcp_router import McpRouter, StubSession
from runtime.models import Evidence, GraphUpdate
from runtime.quality_gate import QualityGate
from runtime.report_builder import ReportBuilder

SPEC = """# Research Spec

## 研究目标
评估把示例项目改造为 Agent 编排的可行性

## 约束
1. 只使用本地 MCP 服务
2. 关键结论需要两个独立来源

## 研究问题
1. 候选框架的编排能力如何
2. 迁移成本有多少

## mode
quick
"""


@pytest.fixture()
def session(tmp_path: Path) -> Session:
    root = tmp_path / "run"
    handle = Session.open(root, create=True, offline=True)
    (root / "input" / "spec.md").write_text(SPEC, encoding="utf-8")
    return handle


def web_session_factory(server: str, body: dict):
    """A stub that behaves like searxng / fetch so harvesting produces evidence."""
    counters = {"n": 0}

    def handler(method: str, tool: str, arguments: dict):
        counters["n"] += 1
        if "search" in tool or "web_search" in tool:
            query = str(arguments.get("query") or "topic")
            return {
                "results": [
                    {
                        "title": f"{query} — source {index}",
                        "url": f"https://src{index}.example.org/{abs(hash(query)) % 97}",
                        "content": f"Documentation about {query}, including limits and rollout guidance.",
                    }
                    for index in range(1, 6)
                ]
            }
        if "read" in tool or "markdown" in tool or "txt" in tool:
            return {"text": "Verified documentation content. " * 60}
        return {"content": "structured local answer with enough length to be citable. " * 20}

    return StubSession(handler)


def test_skill_loads_all_contract_files() -> None:
    """Acceptance 1: the skill's declared components all exist and parse."""
    root = Path(__file__).resolve().parents[1]
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---") and "name: nexus-deep-research" in skill
    front = skill.split("---")[1]
    assert "description:" in front
    agents = AgentRouter.load()
    for name in agents.names():
        assert (root / agents.get(name).file).is_file()
    for schema in ("plan", "graph", "evidence", "task", "agent-message"):
        payload = json.loads((root / "schemas" / f"{schema}.schema.json").read_text(encoding="utf-8"))
        assert payload.get("type") == "object", schema
    for config in ("settings", "mcp-map", "agents", "quality"):
        assert (root / "config" / f"{config}.yaml").is_file()


def test_agent_mcp_graph_loop_report_end_to_end(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance 2-6: agent dispatch -> MCP call -> graph -> loop -> report."""
    root = session.root
    (root / "input" / "spec.md").write_text(SPEC, encoding="utf-8")

    from runtime import intake

    plan = intake.build_plan(
        intake.parse_spec(intake.read_spec_files(intake.find_spec_files(root / "input"))),
        None,
        mode="quick",
        workspace_root=root,
    )
    graph = session.graph(required=False) or __import__("runtime.graph_engine", fromlist=["x"]).ResearchGraph.create(
        session.workspace.path("graph"), plan["objective"]
    )
    intake.seed_graph(graph, plan)
    store = session.store()
    for record in intake.context_evidence(intake.scan_context(root), [item["id"] for item in plan["questions"]]):
        graph.attach_evidence(record, record.supports[0], store)
    graph.save()
    store.save()

    router = McpRouter.load(offline=False, session_factory=web_session_factory)
    agents = AgentRouter.load()
    loop = LoopController.create(graph, store, session.workspace)
    executed = 0
    for _turn in range(3):
        gaps = loop.analyze_gap()
        tasks = agents.ready(loop.generate_tasks(agents, gaps))
        assert tasks, "gaps must produce dispatchable tasks"
        loop.begin_iteration(tasks)
        for task in tasks[:4]:
            outcome = harvest.execute_task(router, task, max_reads=1)
            executed += 1
            loop.collect(outcome.evidence, agent=task.agent)
            task.status = "done"
        report = loop.run_gate()
        stopped, reason = loop.should_stop(report)
        loop.finish_iteration(reason)
        if stopped:
            break
    assert executed >= 3
    assert len(store.records) > 2, "the run must have harvested evidence"
    assert loop.snapshot()["history"], "iterations must be logged"
    assert graph.stats()["nodes"] >= len(plan["questions"])

    gate = QualityGate().evaluate(graph, store)
    builder = ReportBuilder.create(graph, store, gate_report=gate, loop_log=loop.snapshot()["history"], plan=plan)
    markdown = builder.render()
    assert "## Executive Summary" in markdown
    assert "## References" in markdown
    numbers = [line for line in markdown.splitlines() if line.startswith("1. ")]
    assert numbers, "references must be numbered"
    payload = builder.to_dict()
    assert payload["evidence"] and payload["graph"]["nodes"]


def test_agent_driven_mode_records_host_findings(session: Session) -> None:
    """Agent-driven mode: a host agent writes evidence + GraphUpdate back."""
    from runtime.graph_engine import ResearchGraph

    graph = ResearchGraph.create(session.workspace.path("graph"), "宿主 Agent 驱动")
    graph.add_node("question", "本地能力有哪些", node_id="q1", parent="goal")
    graph.add_node("topic", "MCP 能力映射", node_id="t1", parent="q1")
    graph.save()
    store = session.store()
    record = Evidence(
        claim="能力映射把 MCP server 抽象成可降级 capability",
        source_type="file",
        source_uri="file:///config/mcp-map.yaml",
        source_tier="primary",
        confidence=0.8,
        supports=["t1"],
    )
    graph.attach_evidence(record, "t1", store)
    applied = session.loop().apply_update(
        GraphUpdate(
            add_nodes=[{"id": "c1", "type": "claim", "title": "映射层是本地化的关键", "status": "supported"}],
            add_edges=[{"from": "c1", "to": "t1", "type": "derived_from"}],
            reason="host analyst",
        ),
        agent="analyst",
    )
    graph.save()
    store.save()
    assert applied == 1
    coverage = graph.coverage("q1", store)
    assert coverage["evidence"] >= 1 and coverage["coverage"] > 0
    gaps = graph.gaps(store)
    assert gaps and any("corroboration" in " ".join(gap["missing"]) for gap in gaps), "one source must stay a gap"
    for index, domain in enumerate(["b.org", "c.dev", "d.io"]):
        graph.attach_evidence(
            Evidence(
                claim="能力映射把 MCP server 抽象成可降级 capability",
                source_type="file",
                source_uri=f"file:///config/mcp-map{index}.yaml",
                source_tier="primary",
                confidence=0.85,
                supports=["t1"],
            ),
            "t1",
            store,
        )
    assert graph.coverage("q1", store)["coverage"] > coverage["coverage"]
    assert graph.gaps(store) == [], "corroborated nodes must leave the gap list"


def test_loop_budget_stops_a_run_in_progress(session: Session) -> None:
    from runtime.graph_engine import ResearchGraph

    graph = ResearchGraph.create(session.workspace.path("graph"), "预算测试")
    graph.add_node("question", "无限调研", node_id="q1", parent="goal")
    store = session.store()
    loop = LoopController.create(
        graph, store, session.workspace, settings={"loop": {"max_iterations": 9}, "budgets": {"max_total_tool_calls": 4}}
    )
    loop.begin_iteration([])
    loop.count_tool_calls(4)
    assert loop.should_stop() == (True, "budget_exhausted")


def test_audit_is_clean_after_a_valid_run(session: Session) -> None:
    from runtime.graph_engine import ResearchGraph

    graph = ResearchGraph.create(session.workspace.path("graph"), "审计")
    graph.add_node("question", "问题一", node_id="q1", parent="goal")
    graph.save()
    store = session.store()
    store.add(Evidence(claim="一条带来源的结论", source_type="file", source_uri="file:///README.md", supports=["q1"]))
    store.save()
    session.workspace.record("test_event", ok=True)
    lines = [json.loads(line) for line in session.workspace.audit_log().read_text(encoding="utf-8").splitlines() if line]
    assert lines and all("event" in entry for entry in lines)
