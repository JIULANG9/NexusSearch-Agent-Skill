"""Spec parsing, workspace context scan, plan building and graph seeding."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from runtime import intake
from runtime.evidence_store import EvidenceStore
from runtime.graph_engine import ResearchGraph
from runtime.util import validate

SPEC_BODY = (
    "# Research Spec\n\n"
    "## 研究目标\n评估把阻塞式 LLM 调用改造为编排框架的可行性\n\n"
    "## 背景\n当前实现无重试、无流式。\n\n"
    "## 约束\n1. 只使用本地 MCP 服务\n2. 结论必须有来源与置信度\n\n"
    "## 研究问题\n1. 各框架的流式支持成熟度如何\n2. 迁移成本是多少\n\n"
    "## mode\nstandard\n"
)


@pytest.fixture()
def spec_file(tmp_path: Path) -> Path:
    target = tmp_path / "input"
    target.mkdir(parents=True, exist_ok=True)
    path = target / "spec.md"
    path.write_text(SPEC_BODY, encoding="utf-8")
    return path


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "README.md").write_text("# Demo\n用途说明", encoding="utf-8")
    (root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "requirements.txt").write_text("fastapi==0.115\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_main.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("x", encoding="utf-8")
    return root


# ------------------------------------------------------------------- discovery
def test_find_spec_files_prefers_named_specs(tmp_path: Path) -> None:
    (tmp_path / "RESEARCH.md").write_text("# 目标\n研究 X", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("无关笔记", encoding="utf-8")
    found = intake.find_spec_files(tmp_path)
    assert found and found[0].name == "RESEARCH.md"
    extra = tmp_path.parent / "elsewhere.md"
    extra.write_text("## 研究目标\n补充规范", encoding="utf-8")
    assert extra in intake.find_spec_files(tmp_path, [extra])
    assert intake.find_spec_files(tmp_path.parent / "nothing-here") == []


def test_read_spec_files_respects_the_char_budget(tmp_path: Path) -> None:
    big = tmp_path / "big.md"
    big.write_text("# 大文档\n" + ("字" * 5000), encoding="utf-8")
    docs = intake.read_spec_files([big], max_chars=500)
    assert len(docs[0].text) <= 500


# ------------------------------------------------------------------ spec parse
def test_parse_spec_reads_chinese_sections(spec_file: Path) -> None:
    parsed = intake.parse_spec(intake.read_spec_files([spec_file]))
    assert parsed["objective"] == "评估把阻塞式 LLM 调用改造为编排框架的可行性"
    assert len(parsed["questions"]) == 2
    assert parsed["constraints"] == ["只使用本地 MCP 服务", "结论必须有来源与置信度"]
    assert parsed["spec_documents"] == [str(spec_file)]


def test_explicit_objective_beats_a_template_heading(tmp_path: Path) -> None:
    """Regression: "# Research Spec" must not become the research objective."""
    path = tmp_path / "spec.md"
    path.write_text(SPEC_BODY, encoding="utf-8")
    parsed = intake.parse_spec(intake.read_spec_files([path]), goal_fallback="兜底目标")
    assert "Research Spec" not in parsed["objective"]


def test_placeholder_objective_falls_back_to_the_user_goal(tmp_path: Path) -> None:
    path = tmp_path / "spec.md"
    path.write_text("# Research Spec\n\n## 研究目标\n\n待填写\n\n## 约束\n无\n", encoding="utf-8")
    parsed = intake.parse_spec(intake.read_spec_files([path]), goal_fallback="用户给定的目标")
    assert parsed["objective"] == "用户给定的目标"


def test_missing_objective_raises_a_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "spec.md"
    path.write_text("# Research Spec\n\n随便一些内容，没有目标小节。\n", encoding="utf-8")
    with pytest.raises(ValueError):
        intake.parse_spec(intake.read_spec_files([path]))


def test_specs_without_questions_get_default_facets(tmp_path: Path) -> None:
    path = tmp_path / "spec.md"
    path.write_text("## 研究目标\n调研本地向量数据库的现状\n", encoding="utf-8")
    parsed = intake.parse_spec(intake.read_spec_files([path]))
    assert len(parsed["questions"]) >= 3
    assert any("风险" in item for item in parsed["questions"]), "default facets must include a risk angle"


# --------------------------------------------------------------------- context
def test_scan_context_digests_a_project(project: Path) -> None:
    digest = intake.scan_context(project)
    summary = digest.to_dict()
    assert summary["file_count"] >= 4
    assert "README.md" in summary["docs"]
    assert "src/main.py" in summary["entrypoints"]
    assert "tests/test_main.py" in summary["tests"]
    assert "requirements.txt" in summary["manifests"]
    assert isinstance(digest.summary(), str) and digest.summary()


def test_scan_context_ignores_noise(project: Path) -> None:
    digest = intake.scan_context(project)
    blob = json.dumps(digest.to_dict(), ensure_ascii=False)
    assert "node_modules" not in blob


def test_context_evidence_supports_question_nodes(project: Path) -> None:
    digest = intake.scan_context(project)
    records = intake.context_evidence(digest, ["q1"])
    assert records
    assert all(record.source_type in {"file", "code"} for record in records)
    assert all("q1" in record.supports for record in records)
    assert all(len(record.claim) >= 8 for record in records)


# ------------------------------------------------------------------------ plan
def test_build_plan_is_schema_valid_and_carries_strategy(spec_file: Path, project: Path) -> None:
    parsed = intake.parse_spec(intake.read_spec_files([spec_file]))
    context = intake.scan_context(project)
    plan = intake.build_plan(parsed, context, mode="auto", workspace_root=project.parent)
    ok, message = validate(plan, "plan")
    assert ok, f"plan failed schema: {message}"
    assert plan["mode"] in {"quick", "standard", "deep"}
    assert plan["questions"], "plan must carry questions"
    for question in plan["questions"]:
        assert isinstance(question, dict) and question["search_strategy"]
        assert question["capability_hint"] and question["id"].startswith("q")
    assert plan["context_inputs"] and isinstance(plan["context_inputs"][0], dict)
    assert plan["objective"] == parsed["objective"]


def test_untouched_spec_template_cannot_hijack_a_real_spec(tmp_path: Path) -> None:
    """`init --templates` scaffolds input/RESEARCH.md; a real spec must win."""
    from runtime.intake import find_spec_files, parse_spec, read_spec_files
    from runtime.util import SKILL_ROOT

    inputs = tmp_path / "input"
    inputs.mkdir()
    shutil.copy(SKILL_ROOT / "templates" / "input-spec.md", inputs / "RESEARCH.md")
    (inputs / "spec.md").write_text(
        "# 研究目标\n\n评估 FastAPI 服务是否值得引入任务队列。\n\n"
        "## 研究问题\n\n- Celery 与 arq 的运维成本差异是什么？\n\n"
        "## 非目标\n\n- 不做供应商比价\n",
        encoding="utf-8",
    )
    parsed = parse_spec(read_spec_files(find_spec_files(inputs)), "")
    assert parsed["objective"].startswith("评估 FastAPI"), parsed["objective"]
    assert "供应商比价" not in parsed["objective"]
    assert [str(q) for q in parsed["questions"]] == ["Celery 与 arq 的运维成本差异是什么？"], parsed["questions"]


def test_mode_auto_scales_with_scope(spec_file: Path, tmp_path: Path) -> None:
    parsed = intake.parse_spec(intake.read_spec_files([spec_file]))
    quick = intake.build_plan(parsed, None, mode="quick")
    deep = intake.build_plan(parsed, None, mode="deep")
    assert quick["mode"] == "quick" and deep["mode"] == "deep"
    quick_graph = ResearchGraph.create(tmp_path / "g1.json", quick["objective"])
    deep_graph = ResearchGraph.create(tmp_path / "g2.json", deep["objective"])
    intake.seed_graph(quick_graph, quick)
    intake.seed_graph(deep_graph, deep)
    assert len(deep_graph.nodes) > len(quick_graph.nodes), "deep mode must expand more facets"


# ------------------------------------------------------------------- seed graph
def test_seed_graph_wires_every_question(spec_file: Path, tmp_path: Path) -> None:
    parsed = intake.parse_spec(intake.read_spec_files([spec_file]))
    plan = intake.build_plan(parsed, None, mode="quick", workspace_root=tmp_path)
    graph = ResearchGraph.create(tmp_path / "graph.json", plan["objective"])
    counts = intake.seed_graph(graph, plan)
    assert counts["questions"] == len(plan["questions"])
    for question in plan["questions"]:
        node = graph.get(question["id"])
        assert node is not None and node.type == "question"
        assert any(edge.to_id == question["id"] for edge in graph.edges), "no orphan questions"
    gaps = graph.gaps(EvidenceStore.open(tmp_path / "evidence.json"))
    assert {gap["node_id"] for gap in gaps} & {question["id"] for question in plan["questions"]}


def test_seed_graph_is_idempotent(spec_file: Path, tmp_path: Path) -> None:
    parsed = intake.parse_spec(intake.read_spec_files([spec_file]))
    plan = intake.build_plan(parsed, None, mode="quick")
    graph = ResearchGraph.create(tmp_path / "graph.json", plan["objective"])
    first = intake.seed_graph(graph, plan)
    size = len(graph.nodes)
    second = intake.seed_graph(graph, plan)
    assert len(graph.nodes) == size
    assert second["questions"] == first["questions"]


def test_cjk_questions_become_ascii_keyword_queries() -> None:
    """English-only engines return junk for Chinese prose, so lead with technical terms."""
    keywords = ["langgraph", "autogen", "mcp", "架构", "选型"]
    queries = intake._search_strategy("各框架在少量确定性步骤上的抽象差异是什么？", "docs.library", keywords)
    assert queries
    assert all(query.startswith("langgraph autogen mcp") for query in queries)
    joined = " ".join(queries)
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in joined), "CJK prose must not reach the engine"


def test_latin_questions_keep_their_wording() -> None:
    queries = intake._search_strategy("What is the MCP transport model?", "code.repository", ["mcp", "transport"])
    assert queries[0].startswith("What is the MCP transport model mcp")
