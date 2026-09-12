"""End-to-end CLI coverage: the exact commands documented in README/SKILL.md."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime import cli
from runtime.workspace import Workspace


def run(*argv: str, workspace: Path | None = None) -> int:
    args = ["--offline"]
    if workspace is not None:
        args += ["-w", str(workspace)]
    return cli.main([*args, *argv])


@pytest.fixture()
def session_root(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    assert cli.main(["--offline", "init", str(root)]) == 0
    (root / "input" / "spec.md").write_text(
        "# Research Spec\n\n## 研究目标\n评估本地 MCP 深度研究方案\n\n"
        "## 约束\n1. 只用本地服务\n\n## 研究问题\n1. 图结构带来什么收益\n2. 循环如何收敛\n\n## mode\nquick\n",
        encoding="utf-8",
    )
    return root


def test_help_and_version_exit_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as state:
        cli.main(["--help"])
    assert state.value.code == 0
    assert "pipeline:" in capsys.readouterr().out
    with pytest.raises(SystemExit) as state:
        cli.main(["--version"])
    assert "nexus" in capsys.readouterr().out


def test_commands_outside_a_workspace_do_not_create_one(tmp_path: Path) -> None:
    stray = tmp_path / "nowhere"
    stray.mkdir()
    assert run("mcp", "caps", workspace=stray) == 0
    assert sorted(item.name for item in stray.iterdir()) == [], "bare commands must not scaffold a workspace"
    assert run("doctor", "--json", workspace=stray) in (0, 1, 2)
    assert (stray / "state").exists() is False
    assert run("remember", "list", workspace=stray) == 0
    assert (stray / "memory").exists() is False


def test_unknown_workspace_reports_a_hint(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    other = tmp_path / "not-a-workspace"
    other.mkdir()
    (other / "main.py").write_text("print(1)\n", encoding="utf-8")
    code = run("status", workspace=other)
    assert code != 0
    assert "init" in capsys.readouterr().err
    assert list(other.iterdir()) == [other / "main.py"], "a failed command must not scaffold anything"


def test_intake_writes_plan_and_seeds_graph(session_root: Path) -> None:
    assert run("intake", "--target", str(session_root), workspace=session_root) == 0
    workspace = Workspace.open(session_root)
    plan = json.loads(workspace.path("plan").read_text(encoding="utf-8"))
    assert plan["objective"] == "评估本地 MCP 深度研究方案"
    graph = json.loads(workspace.path("graph").read_text(encoding="utf-8"))
    assert len(graph["nodes"]) >= 3
    assert run("plan", workspace=session_root) == 0
    assert run("status", workspace=session_root) == 0


def test_graph_and_evidence_editing(session_root: Path) -> None:
    assert run("intake", workspace=session_root) == 0
    assert run("graph", "add-node", "--type", "claim", "--title", "图结构提升可验证性",
               "--id", "cX", "--parent", "t1", workspace=session_root) == 0
    assert run("graph", "add-edge", "--frm", "cX", "--to", "q1", "--relation", "answers",
               workspace=session_root) == 0
    with pytest.raises(SystemExit):
        run("graph", "add-edge", "--frm", "cX", "--to", "q1", workspace=session_root)
    assert run("graph", "stats", "--json", workspace=session_root) == 0
    assert run("graph", "validate", workspace=session_root) == 0
    assert run("graph", "tree", workspace=session_root) == 0
    assert run("graph", "mermaid", "--out", str(session_root / "output" / "g.mmd"), workspace=session_root) == 0
    assert (session_root / "output" / "g.mmd").read_text(encoding="utf-8").startswith("flowchart")

    payload = {
        "claim": "循环收敛依赖可度量的覆盖率",
        "source_type": "docs",
        "source_uri": "https://example.org/spec",
        "source_tier": "primary",
        "confidence": 0.8,
        "supports": ["cX"],
    }
    assert run("evidence", "add", "--file", str(_write_json(session_root, payload)), workspace=session_root) == 0
    assert run("evidence", "list", "--json", workspace=session_root) == 0
    assert run("evidence", "stats", workspace=session_root) == 0


def test_gate_verify_report_pipeline(session_root: Path) -> None:
    assert run("intake", workspace=session_root) == 0
    assert run("gate", workspace=session_root) == 0  # non-strict: informs, does not block
    assert run("verify", workspace=session_root) in (0, 1)
    assert run("report", workspace=session_root) == 0
    report = session_root / "output" / "report.md"
    assert report.is_file() and report.stat().st_size > 400
    assert (session_root / "output" / "evidence.json").is_file()
    assert (session_root / "output" / "manifest.json").is_file()


def test_tasks_and_dispatch_are_machine_readable(session_root: Path) -> None:
    assert run("intake", workspace=session_root) == 0
    assert run("tasks", "--json", workspace=session_root) == 0
    assert run("dispatch", "--json", workspace=session_root) == 0


def test_run_dry_run_and_loop(session_root: Path) -> None:
    assert run("intake", workspace=session_root) == 0
    assert run("run", "--dry-run", workspace=session_root) == 0
    assert run("loop", "--json", workspace=session_root) == 0
    assert run("loop", "--abort", workspace=session_root) == 0
    assert run("run", "--iterations", "1", "--max-reads", "0", workspace=session_root) == 0


def test_memory_commands(session_root: Path) -> None:
    assert run("remember", "add", "查询模板", "--statement", "把 official documentation 放句尾",
               "--kind", "query", workspace=session_root) == 0
    assert run("remember", "list", "--json", workspace=session_root) == 0
    assert run("remember", "recall", "official documentation", workspace=session_root) == 0
    assert run("remember", "stats", workspace=session_root) == 0


def test_audit_reports_a_healthier_run(session_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("intake", workspace=session_root) == 0
    assert run("audit", workspace=session_root) in (0, 1)
    assert "audit" in capsys.readouterr().out.lower()


def test_mcp_capability_errors_are_actionable(session_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as state:
        run("mcp", "call", "web.discovery", workspace=session_root)
    assert "query" in str(state.value).lower(), state.value
    with pytest.raises(SystemExit):
        run("mcp", "call", "nope.nope", workspace=session_root)


def _write_json(root: Path, payload: dict) -> Path:
    target = root / "state" / "seed-evidence.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps([payload], ensure_ascii=False), encoding="utf-8")
    return target
