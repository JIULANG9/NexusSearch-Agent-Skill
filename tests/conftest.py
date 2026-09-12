"""Shared fixtures: an isolated workspace plus a small, valid research graph."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.evidence_store import EvidenceStore  # noqa: E402
from runtime.graph_engine import ResearchGraph  # noqa: E402
from runtime.models import Evidence  # noqa: E402
from runtime.workspace import Workspace  # noqa: E402

CLAIMS = [
    "MCP 用统一的工具抽象把本地能力暴露给 Agent",
    "图结构让研究覆盖率成为可计算的指标",
    "缺少交叉来源的结论必须标注为不确定",
]


@pytest.fixture()
def workspace(tmp_path: Path) -> Workspace:
    """A freshly initialised research workspace in a temp directory."""
    return Workspace.open(tmp_path / "run", create=True)


@pytest.fixture()
def graph(workspace: Workspace) -> ResearchGraph:
    """A two-question graph with a claim and a risk, mirroring a real intake seed."""
    instance = ResearchGraph.create(workspace.path("graph"), "评估本地深度研究框架")
    instance.add_node("question", "主流方案有哪些", node_id="q1", parent="goal")
    instance.add_node("question", "运维成本如何", node_id="q2", parent="goal")
    instance.add_node("topic", "编排能力对比", node_id="t1", parent="q1")
    instance.add_node("claim", CLAIMS[0], node_id="c1", status="proposed", parent="t1",
                      relation="derived_from")
    instance.add_node("risk", "外部依赖不可达", node_id="r1", parent="q1", risk_level="medium")
    return instance


@pytest.fixture()
def store(workspace: Workspace) -> EvidenceStore:
    return EvidenceStore.open(workspace.path("evidence"))


def make_evidence(index: int, *, domain: str, node: str = "t1", **kwargs: object) -> Evidence:
    """Build a schema-valid evidence record on a given domain."""
    payload: dict[str, object] = {
        "claim": CLAIMS[index % len(CLAIMS)],
        "source_type": "url",
        "source_uri": f"https://{domain}/docs/{index}",
        "domain": domain,
        "source_tier": "primary",
        "confidence": 0.8,
        "supports": [node],
    }
    payload.update(kwargs)
    return Evidence(**payload)  # type: ignore[arg-type]


@pytest.fixture()
def evidence_factory() -> type[Evidence]:
    return Evidence


@pytest.fixture()
def spec_dir(tmp_path: Path) -> Path:
    """An ``input/`` folder with a Chinese research spec, like a real run."""
    target = tmp_path / "input"
    target.mkdir(parents=True)
    (target / "spec.md").write_text(
        "# Research Spec\n\n"
        "## 研究目标\n对比 A 与 B 两个编排框架\n\n"
        "## 约束\n1. 只使用本地 MCP\n2. 结论需来源\n\n"
        "## 研究问题\n1. A 的流式支持如何\n2. B 的运维成本多少\n\n"
        "## mode\nquick\n",
        encoding="utf-8",
    )
    return target
