"""Unit tests for the shared helpers in `runtime.util`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime import util


def test_new_id_is_prefixed_and_unique() -> None:
    first, second = util.new_id("ev"), util.new_id("ev")
    assert first.startswith("ev_")
    assert first != second


def test_slugify_handles_cjk_and_punctuation() -> None:
    slug = util.slugify("对比 LangGraph 与 AutoGen!!")
    assert slug and "?" not in slug and " " not in slug
    assert "langgraph" in slug
    assert util.slugify("!!!") == "topic"  # documented fallback


def test_clamp_bounds() -> None:
    assert util.clamp(-1) == 0.0
    assert util.clamp(2) == 1.0
    assert util.clamp(0.5, 0.0, 2.0) == 0.5


def test_read_write_json_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "state.json"
    util.write_json(target, {"a": 1, "中文": "值"})
    assert json.loads(target.read_text(encoding="utf-8"))["中文"] == "值"
    assert util.read_json(tmp_path / "missing.json", {"fallback": True}) == {"fallback": True}


def test_write_json_is_atomic(tmp_path: Path) -> None:
    target = tmp_path / "graph.json"
    util.write_json(target, {"v": 1})
    leftovers = [item.name for item in tmp_path.iterdir() if item.name != "graph.json"]
    assert leftovers == []


def test_jsonl_append_and_read(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    util.append_jsonl(log, {"n": 1})
    util.append_jsonl(log, {"n": 2})
    assert [row["n"] for row in util.read_jsonl(log)] == [1, 2]
    assert util.read_jsonl(tmp_path / "absent.jsonl") == []


def test_redact_hides_secret_shaped_values() -> None:
    payload = {"authorization": "Bearer abcdefghijklmnop", "token": "tok_live_123456789", "query": "mcp"}
    clean = util.redact(payload)
    assert clean["query"] == "mcp"
    assert "abcdefghijklmnop" not in json.dumps(clean)
    assert "123456789" not in json.dumps(clean["token"])


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("connection refused", "unavailable"),
        ("HTTP 429 Too Many Requests", "rate_limited"),
        ("timed out after 30s", "timeout"),
        ("401 Unauthorized", "auth_missing"),
        ("path does not exist", "not_found"),
        ("InvalidArguments: query required", "invalid_input"),
        ("something weird", "unknown"),
    ],
)
def test_classify_error(message: str, expected: str) -> None:
    assert util.classify_error(message) == expected


def test_validate_accepts_and_rejects() -> None:
    ok, _ = util.validate({"claim": "证据充分的一条结论", "source": {"type": "url"}, "confidence": 0.5,
                           "modality": "text", "source_tier": "primary", "id": "ev_x", "retrieved_at": "x"}, "evidence")
    assert ok
    bad, message = util.validate({"claim": "short", "source": {"type": "url"}}, "evidence")
    assert not bad and message


def test_resolve_env_expands_with_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEXUS_TEST_TOKEN", raising=False)
    assert util.resolve_env("${NEXUS_TEST_TOKEN:-fallback}") == "fallback"
    monkeypatch.setenv("NEXUS_TEST_TOKEN", "real")
    assert util.resolve_env("${NEXUS_TEST_TOKEN:-fallback}") == "real"


def test_load_config_reads_yaml() -> None:
    settings = util.load_config("settings")
    assert settings["loop"]["confidence_threshold"] == 0.85
    assert "graph/research.graph.json" in settings["workspace"]["files"]["graph"]


def test_load_local_env_fills_only_unset_variables(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    private = tmp_path / "local.env"
    private.write_text(
        "# comment\nexport PRIVATE_A=one\nPRIVATE_B=\"two words\"\n\nPRIVATE_C=three\nmalformed line\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("PRIVATE_A", raising=False)
    monkeypatch.setenv("PRIVATE_C", "from-shell")
    assert util.load_local_env(private) == 2
    import os

    assert os.environ["PRIVATE_A"] == "one"
    assert os.environ["PRIVATE_B"] == "two words"
    assert os.environ["PRIVATE_C"] == "from-shell"  # an explicit export always wins


def test_load_local_env_tolerates_a_missing_file(tmp_path) -> None:
    assert util.load_local_env(tmp_path / "does-not-exist") == 0
