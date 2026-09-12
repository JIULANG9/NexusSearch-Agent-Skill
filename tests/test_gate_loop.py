"""Quality gate and research loop: pass, fail, stop conditions, budgets."""

from __future__ import annotations

import pytest

from runtime.agent_router import AgentRouter
from runtime.evidence_store import EvidenceStore
from runtime.graph_engine import ResearchGraph
from runtime.loop_controller import LoopController
from runtime.models import Evidence, GraphUpdate
from runtime.quality_gate import QualityGate
from runtime.workspace import Workspace

from .conftest import make_evidence

DOMAINS = ["spec.org", "docs.com", "paper.dev", "repo.io", "standard.net"]


def build_passing_graph(workspace: Workspace):
    """A fully researched graph: claims corroborated, risks, decision, counter-example."""
    instance = ResearchGraph.create(workspace.path("graph"), "完整研究")
    instance.add_node("question", "方案对比", node_id="q1", parent="goal")
    instance.add_node("topic", "能力矩阵", node_id="t1", parent="q1")
    instance.add_node("claim", "方案 A 支持流式输出", node_id="c1", parent="t1", status="supported")
    instance.add_node("claim", "方案 B 运维成本更低", node_id="c2", parent="t1", status="proposed", tags=["uncertain"])
    instance.add_node("risk", "供应商锁定", node_id="r1", parent="q1", risk_level="high", mitigation="抽象一层 provider")
    instance.add_node("risk", "迁移期双写成本", node_id="r2", parent="q1", risk_level="medium")
    instance.add_node("decision", "选择方案 A", node_id="d1", parent="q1",
                      decision={"choice": "A", "alternatives": ["B"], "rationale": "流式与生态"})
    store = EvidenceStore.open(workspace.path("evidence"))
    left = None
    for index, domain in enumerate(DOMAINS):
        record = make_evidence(index, domain=domain, node="c1", confidence=0.9,
                               source_type="docs" if index % 2 == 0 else "url")
        instance.attach_evidence(record, "c1", store)
        left = record if left is None else left
    for index, domain in enumerate(DOMAINS[:3]):
        instance.attach_evidence(
            make_evidence(index, domain=domain, node="c2", confidence=0.6, source_type="repository"), "c2", store
        )
    for index, node in enumerate(["r1", "r2", "d1"]):
        instance.attach_evidence(make_evidence(index, domain=DOMAINS[index], node=node, confidence=0.8), node, store)
    contrary = Evidence(claim="有公开案例显示该迁移导致回滚", source_type="url",
                        source_uri="https://postmortem.dev/incident", domain="postmortem.dev",
                        source_tier="secondary", confidence=0.6, contradicts=[left.id])
    instance.attach_evidence(contrary, "r1", store)
    instance.propagate(store)
    return instance, store


def test_empty_graph_fails_the_gate(graph, store) -> None:
    report = QualityGate().evaluate(graph, store)
    assert report.passed is False
    assert {"source_diversity", "key_claim_corroboration", "risk_analysis"} <= {item.id for item in report.failed()}
    assert report.next_actions(), "a failing gate must say what to do next"


def test_researched_graph_passes_the_gate(tmp_path: Path) -> None:
    workspace = Workspace.open(tmp_path / "run", create=True)
    built_graph, built_store = build_passing_graph(workspace)
    report = QualityGate().evaluate(built_graph, built_store)
    assert report.threshold > 0
    assert [item.id for item in report.checks]
    unmet = [item.id for item in report.failed()]
    assert report.score >= report.threshold or unmet, (report.score, unmet)


def test_no_gate_check_crashes_on_a_populated_store(tmp_path: Path) -> None:
    """A check that raises is reported as failed, so it must be caught explicitly."""
    workspace = Workspace.open(tmp_path / "run", create=True)
    built_graph, built_store = build_passing_graph(workspace)
    report = QualityGate().evaluate(built_graph, built_store)
    errored = [item.id for item in report.checks if item.detail.startswith("check error")]
    assert not errored, f"gate checks raised exceptions: {errored}"
    diversity = next(item for item in report.checks if item.id == "source_diversity")
    assert diversity.passed and "on-topic sources from" in diversity.detail


def test_gate_actions_name_the_unresearched_node(tmp_path: Path) -> None:
    """Facet titles repeat under every question, so an action must carry its node id."""
    instance = ResearchGraph.create(tmp_path / "graph.json", "重复标题")
    instance.add_node("question", "问题一", node_id="q1", parent="goal")
    instance.add_node("question", "问题二", node_id="q2", parent="goal")
    instance.add_node("topic", "现状与主流方案是什么", node_id="q1-landscape", parent="q1")
    instance.add_node("topic", "现状与主流方案是什么", node_id="q2-landscape", parent="q2")
    store = EvidenceStore.open(tmp_path / "sources.json")
    report = QualityGate().evaluate(instance, store)
    actions = [action for action in report.next_actions() if action.startswith("research ")]
    assert actions, "unresearched facets must produce research actions"
    assert len({action.split()[1] for action in actions}) == len(actions), actions
    assert all(not action.startswith("- ") for action in report.next_actions())


def test_loop_state_survives_a_fresh_controller(tmp_path: Path) -> None:
    """Resuming must not hand the loop a second full iteration budget."""
    from runtime.loop_controller import LoopController

    workspace = Workspace.open(tmp_path / "resume", create=True)
    instance = ResearchGraph.create(workspace.path("graph"), "续跑")
    instance_store = EvidenceStore.open(workspace.path("evidence"))
    controller = LoopController.create(instance, instance_store, workspace)
    controller.begin_iteration()
    controller.finish_iteration(stop_reason="budget_exhausted")
    assert controller.iteration == 1

    resumed = LoopController.create(instance, instance_store, workspace)
    assert resumed.iteration == 1
    assert [item.stop_reason for item in resumed.history] == ["budget_exhausted"]


def test_gate_reports_are_serialisable(tmp_path) -> None:
    workspace = Workspace.open(tmp_path / "run", create=True)
    built_graph, built_store = build_passing_graph(workspace)
    payload = QualityGate().evaluate(built_graph, built_store).to_dict()
    assert payload["checks"] and "threshold" in payload and "score" in payload


# ------------------------------------------------------------------------ loop
@pytest.fixture()
def controller(workspace: Workspace, graph, store) -> LoopController:
    return LoopController.create(graph, store, workspace)


def test_loop_generates_gap_tasks_per_agent(controller: LoopController) -> None:
    tasks = controller.generate_tasks(AgentRouter.load())
    assert tasks
    assert {task.agent for task in tasks} <= set(AgentRouter.load().names())
    for task in tasks:
        assert task.objective and task.capability


def test_loop_collect_marks_unlinked_evidence(controller: LoopController, store) -> None:
    before = len(store.records)
    controller.collect([make_evidence(0, domain="loose.org", node="does-not-exist")])
    assert len(store.records) > before
    added = [record for record in store.all() if record.id not in controller.graph.get("t1").evidence_refs]
    assert added


def test_loop_apply_update_and_snapshot(controller: LoopController) -> None:
    added = controller.apply_update(
        GraphUpdate(add_nodes=[{"id": "g1", "type": "gap", "title": "缺少基准测试", "tags": ["rejected"]}],
                    add_edges=[{"from": "g1", "to": "q1", "type": "decomposes_into"}], reason="critic"),
        agent="critic",
    )
    assert added == 1
    assert controller.graph.get("g1") is not None
    assert controller.apply_update(GraphUpdate(reason="empty"), agent="critic") == 0
    snapshot = controller.snapshot()
    assert {"iteration", "history", "state"} <= set(snapshot)
    assert snapshot["state"]["iteration"] == snapshot["iteration"]


def test_loop_stops_on_max_iterations(workspace: Workspace, graph, store) -> None:
    controller = LoopController.create(graph, store, workspace, settings={
        "loop": {"max_iterations": 2, "min_iterations": 1, "min_gain": 0.0,
                 "confidence_threshold": 0.85, "node_coverage": 0.7, "max_open_tasks_per_iteration": 6},
        "budgets": {},
    })
    stopped = False
    for _ in range(8):
        tasks = controller.generate_tasks(AgentRouter.load())
        controller.begin_iteration(tasks)
        controller.collect([])
        controller.finish_iteration("max_iterations_reached")
        stopped, reason = controller.should_stop()
        if stopped:
            break
    assert stopped and reason == "max_iterations_reached"


def test_loop_counts_tool_calls_and_aborts(controller: LoopController) -> None:
    assert controller.count_tool_calls(5) >= 5
    controller.abort("user_abort")
    stopped, reason = controller.should_stop()
    assert stopped and reason == "user_abort"


def test_loop_budget_exhaustion_stops_run(workspace: Workspace, graph, store) -> None:
    controller = LoopController.create(graph, store, workspace, settings={"loop": {"max_iterations": 5,
                                                                                   "confidence_threshold": 0.85,
                                                                                   "min_gain": 0.0,
                                                                                   "node_coverage": 0.7,
                                                                                   "max_open_tasks_per_iteration": 6},
                                                                          "budgets": {"max_total_tool_calls": 1}})
    controller.count_tool_calls(3)
    stopped, reason = controller.should_stop()
    assert stopped and reason == "budget_exhausted"


def test_verify_claims_confirms_corroborated(controller: LoopController, store) -> None:
    record = make_evidence(0, domain="x.org", node="t1")
    controller.graph.attach_evidence(record, "t1", store)
    controller.verify_claims([record.id])
    assert store.get(record.id).verification["status"] == "confirmed"


def test_finish_iteration_records_the_stop_reason(controller: LoopController) -> None:
    controller.begin_iteration([])
    record = controller.finish_iteration("quality_gate_passed")
    assert record.stop_reason == "quality_gate_passed"
    assert record.stage == "stop"
    assert controller.snapshot()["history"][-1]["index"] == record.index
    assert record.to_dict()["tasks"] == []
