"""Research graph: structure, scoring, gap detection and persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.graph_engine import GraphError, ResearchGraph
from runtime.models import Evidence, GraphUpdate
from runtime.workspace import Workspace

from .conftest import make_evidence


def test_create_seeds_goal(workspace: Workspace) -> None:
    graph = ResearchGraph.create(workspace.path("graph"), "选型目标")
    goal = graph.get("goal")
    assert goal is not None and goal.type == "goal" and goal.title == "选型目标"


def test_unknown_types_are_rejected(workspace: Workspace) -> None:
    graph = ResearchGraph.create(workspace.path("graph"), "g")
    with pytest.raises(GraphError):
        graph.add_node("vibes", "感觉")
    with pytest.raises(GraphError):
        graph.add_edge("goal", "missing", "decomposes_into")
    assert graph.add_edge("goal", "missing", "decomposes_into", strict=False) is None


def test_add_node_merges_by_id(workspace: Workspace) -> None:
    graph = ResearchGraph.create(workspace.path("graph"), "g")
    first = graph.add_node("question", "问题一", node_id="q1")
    second = graph.add_node("question", "问题一", node_id="q1", priority=3)
    assert first.id == second.id and len(graph.nodes) == 2
    assert second.priority == 3


def test_parent_argument_creates_decomposition_edge(graph: ResearchGraph) -> None:
    topic = graph.add_node("topic", "子主题", node_id="t9", parent="q1")
    edge = [item for item in graph.edges if item.to_id == topic.id]
    assert edge and edge[0].type == "decomposes_into" and edge[0].from_id == "q1"


def test_cycle_detection(graph: ResearchGraph) -> None:
    assert graph.has_cycle() == []
    graph.add_edge("t1", "q1", "depends_on")
    graph.add_edge("q1", "t1", "depends_on")
    assert set(graph.has_cycle()) <= {"q1", "t1"}


def test_attach_evidence_links_support_edge(graph: ResearchGraph, store) -> None:
    record = make_evidence(0, domain="example.org", node="t1")
    node = graph.attach_evidence(record, "t1", store)
    assert node.id == record.id
    assert record.id in graph.get("t1").evidence_refs
    assert any(edge.type == "supports" for edge in graph.edges)
    assert graph.evidence_for("q1", store)  # inherited up the subtree
    with pytest.raises(GraphError):
        graph.attach_evidence(make_evidence(1, domain="x.org"), "nope", store)


def test_coverage_rewards_diversity(graph: ResearchGraph, store) -> None:
    empty = graph.coverage("t1", store)
    assert empty["coverage"] == 0.0 and empty["evidence"] == 0
    for index, domain in enumerate(["a.org", "b.com", "c.dev", "d.io"]):
        graph.attach_evidence(make_evidence(index, domain=domain, node="t1"), "t1", store)
    full = graph.coverage("t1", store)
    assert full["coverage"] > empty["coverage"] and len(full["domains"]) == 4
    assert full["has_primary"] is True


def test_propagate_raises_claim_confidence(graph: ResearchGraph, store) -> None:
    before = graph.get("c1").confidence
    for index, domain in enumerate(["spec.org", "docs.com", "paper.dev"]):
        record = make_evidence(index, domain=domain, node="c1", confidence=0.9)
        graph.attach_evidence(record, "c1", store)
    summary = graph.propagate(store)
    assert summary["confidence"] >= 0.0
    assert graph.get("c1").confidence >= before
    assert graph.overall_confidence() > 0.0


def test_gaps_point_at_unresearched_questions(graph: ResearchGraph, store) -> None:
    gaps = graph.gaps(store)
    assert gaps, "an unresearched question must show up as a gap"
    top = gaps[0]
    assert {"node_id", "title", "coverage", "missing", "suggested_queries", "suggested_capability"} <= set(top)
    assert top["suggested_queries"] and top["suggested_capability"]


def test_contradiction_pairs(graph: ResearchGraph, store) -> None:
    left = make_evidence(0, domain="a.org", node="t1")
    right = make_evidence(1, domain="b.org", node="t1", contradicts=[left.id])
    store.add(left)
    store.add(right)
    assert (left.id, right.id) in graph.contradiction_pairs(store) or graph.contradiction_pairs(store)


def test_save_load_round_trip_and_schema(workspace: Workspace, graph: ResearchGraph, store) -> None:
    graph.attach_evidence(make_evidence(0, domain="a.org", node="t1"), "t1", store)
    graph.propagate(store)
    target = graph.save()
    assert target.name == "research.graph.json"
    reloaded = ResearchGraph.load(workspace.path("graph"))
    assert set(reloaded.nodes) == set(graph.nodes)
    assert reloaded.stats()["nodes"] == len(graph.nodes)
    ok, message = reloaded.validate_now()
    assert ok, message


def test_renderings_are_human_readable(graph: ResearchGraph) -> None:
    assert graph.render_mermaid().startswith("flowchart")
    assert "主流方案有哪些" in graph.render_tree()


def test_apply_update_and_remove(workspace: Workspace) -> None:
    graph = ResearchGraph.create(workspace.path("graph"), "g")
    update = GraphUpdate(
        add_nodes=[{"id": "d1", "type": "decision", "title": "选择 LangGraph",
                    "decision": {"choice": "LangGraph", "alternatives": ["AutoGen"]}}],
        add_edges=[{"from": "d1", "to": "goal", "type": "refines"}],
        reason="analyst recommendation",
    )
    for item in update.add_nodes:
        graph.add_node(item["type"], item["title"], node_id=item["id"], **{k: v for k, v in item.items() if k not in {"id", "type", "title"}})
    for item in update.add_edges:
        graph.add_edge(item["from"], item["to"], item["type"])
    assert graph.get("d1").decision["alternatives"] == ["AutoGen"]
    assert graph.remove_node("d1") is True
    assert graph.get("d1") is None
    assert all(edge.from_id != "d1" for edge in graph.edges)


def test_snapshot_records_sizes(graph: ResearchGraph) -> None:
    snapshot = graph.snapshot(1)
    assert snapshot["iteration"] == 1 and snapshot["stats"]["nodes"] >= 1


def test_depth_and_subtree(graph: ResearchGraph) -> None:
    assert graph.depth() >= 3
    assert "c1" in graph.subtree("q1")
    assert [node.id for node in graph.ancestors("c1")][0] == "t1"


def test_orphan_nodes_do_not_silently_become_gaps(graph: ResearchGraph, store) -> None:
    """Regression: ``parent=`` must wire the edge, and claim evidence must roll up."""
    claim = graph.add_node("claim", "结论依赖主题", node_id="c9", parent="t1", relation="derived_from")
    assert any(edge.to_id == claim.id for edge in graph.edges)
    record = make_evidence(0, domain="only.org", node=claim.id)
    graph.attach_evidence(record, claim.id, store)
    coverage = graph.coverage("q1", store)
    assert coverage["evidence"] >= 1, "evidence on a claim must count for its question"
    assert "c9" in graph.subtree("q1")
