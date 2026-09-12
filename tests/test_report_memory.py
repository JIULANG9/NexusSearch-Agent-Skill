"""Report rendering and the self-evolution memory loop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.memory_store import MemoryStore
from runtime.quality_gate import QualityGate
from runtime.report_builder import ReportBuilder
from runtime.workspace import Workspace

from .conftest import make_evidence
from .test_gate_loop import build_passing_graph

SECTION_WORDS = [
    "Executive Summary",
    "Research Objective",
    "Evidence",
    "Risk",
    "Recommendation",
    "References",
]


def test_report_covers_every_required_section(tmp_path: Path) -> None:
    workspace = Workspace.open(tmp_path / "run", create=True)
    graph, store = build_passing_graph(workspace)
    report = QualityGate().evaluate(graph, store)
    plan = {"objective": graph.goal["title"], "questions": [{"id": "q1", "text": "方案对比"}], "mode": "deep"}
    markdown = ReportBuilder.create(graph, store, gate_report=report, loop_log=[], plan=plan).render()
    for word in SECTION_WORDS:
        assert word.lower() in markdown.lower(), f"missing section: {word}"
    assert "https://" in markdown, "report must cite sources"
    assert "置信度" in markdown or "confidence" in markdown.lower()


def test_report_marks_uncertainty_and_confidence_labels(tmp_path: Path) -> None:
    workspace = Workspace.open(tmp_path / "run", create=True)
    graph, store = build_passing_graph(workspace)
    builder = ReportBuilder.create(graph, store, gate_report=QualityGate().evaluate(graph, store), plan=None)
    markdown = builder.render()
    assert builder.confidence_label(0.95).lower() in {"high", "高", "强"}
    assert builder.confidence_label(0.2) != builder.confidence_label(0.95)
    assert markdown.lstrip().startswith("#"), "report needs a title"


def test_report_renders_extra_sections_and_dict_twins(tmp_path: Path) -> None:
    workspace = Workspace.open(tmp_path / "run", create=True)
    graph, store = build_passing_graph(workspace)
    builder = ReportBuilder.create(graph, store, gate_report=QualityGate().evaluate(graph, store))
    markdown = builder.render(extra_sections={"Appendix": "基准测试原始数据"})
    assert "基准测试原始数据" in markdown
    payload = builder.to_dict()
    assert payload["evidence"] and payload["graph"]["nodes"]
    manifest = builder.manifest(["output/report.md"])
    assert manifest["artifacts"] == ["output/report.md"]


def test_report_survives_an_empty_workspace(workspace: Workspace, graph, store) -> None:
    builder = ReportBuilder.create(graph, store, gate_report=None, plan={})
    markdown = builder.render()
    assert markdown
    assert "no evidence" in markdown.lower() or "证据" in markdown


# ------------------------------------------------------------------- memory
@pytest.fixture()
def memory(tmp_path: Path) -> MemoryStore:
    return MemoryStore.open(tmp_path / "patterns.json")


def test_remember_and_recall_rank_by_relevance(memory: MemoryStore) -> None:
    memory.remember("query", "官方文档优先", "查询尾部加 official documentation", context=["docs"], tags=["docs"])
    memory.remember("source", "arxiv 适合论文", "论文类问题优先 arxiv 分类", context=["paper"], tags=["paper"])
    hits = memory.recall("docs library official documentation")
    assert hits and hits[0].title == "官方文档优先"
    assert memory.recall("完全不相关的查询词xyz", limit=5) is not None
    memory.save()
    assert MemoryStore.open(memory.path).get(hits[0].id) is not None


def test_record_use_moves_confidence(memory: MemoryStore) -> None:
    pattern = memory.remember("tool", "playwright 渲染 SPA", "对 JS 站点改用 browser.render", confidence=0.5)
    up = memory.record_use(pattern.id, succeeded=True)
    assert up is not None and up.wins == 1
    after_success = up.confidence
    assert after_success > 0.5, "a helpful pattern must gain confidence"
    down = memory.record_use(pattern.id, succeeded=False)
    assert down is not None and down.confidence < after_success
    assert down.uses == 2 and down.success_rate == 0.5


def test_recall_is_capped_and_deterministic(memory: MemoryStore) -> None:
    for index in range(12):
        memory.remember("query", f"模式 {index} 的标题", f"陈述 {index}", context=["shared"])
    first = [item.id for item in memory.recall("shared 模式", limit=3)]
    assert len(first) == 3
    assert first == [item.id for item in memory.recall("shared 模式", limit=3)]


def test_promote_from_run_creates_failure_patterns(memory: MemoryStore) -> None:
    gate = {
        "passed": False,
        "score": 0.4,
        "checks": [
            {"id": "source_diversity", "passed": False, "remediation": "换查询角度覆盖更多独立域名"},
            {"id": "risk_analysis", "passed": False, "remediation": "补充风险节点"},
        ],
        "gaps": [{"node_id": "q1", "title": "成本", "missing": ["no primary source"]}],
    }
    created = memory.promote_from_run(gate, [{"index": 1, "stop_reason": "max_iterations_reached"}], source_run="unit")
    assert created
    kinds = {pattern.kind for pattern in created}
    assert kinds & {"failure", "query", "decomposition"}
    assert all(pattern.source_run == "unit" for pattern in created)
    assert memory.stats()["total"] == len(memory.patterns)


def test_export_entities_shapes_memory_payload(memory: MemoryStore) -> None:
    memory.remember("decision", "选型偏好图编排", "在长流程任务上优先 LangGraph", confidence=0.8)
    entities = memory.export_entities(limit=5)
    assert entities
    assert {"entityType", "name", "observations"} <= set(entities[0])
    json.dumps(entities, ensure_ascii=False)


def test_stats_report_shape(memory: MemoryStore) -> None:
    memory.remember("source", "github 引擎可达", "在受限网络下用 engines=github", context=["web"], tags=["env"])
    stats = memory.stats()
    assert stats["total"] == 1
    assert stats["by_kind"]["source"] == 1
    assert "mean_confidence" in stats or "avg_confidence" in stats
