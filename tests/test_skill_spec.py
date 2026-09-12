"""Spec conformance and release-hygiene tests, including a gate on this repository.

The last two tests are deliberate: they assert that *this* tree is loadable by any
Agent Skills host and free of private material, so a regression (a leaked absolute
path in an example, a version bumped in one file only, a deleted governance doc)
fails CI instead of shipping.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from runtime import cli
from runtime.skill_spec import (
    TOKEN_PATTERNS,
    Finding,
    audit_release,
    build_bundle,
    estimate_tokens,
    mask_private,
    parse_frontmatter,
    release_files,
    scan_private,
    validate_skill,
    version_map,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# The release scanner looks for real home directories, so the fixtures below build
# their paths by concatenation: the file never contains a matching absolute path,
# while mask_private/scan_private still see one at runtime.
HOST = "/home" + "/" + "alex"
WIN = "C:" + chr(92) + "Users" + chr(92) + "alex"

GOOD_DESCRIPTION = (
    "Investigate a local workspace and the local web with an evidence graph. "
    "Use when the user asks to research, compare or evaluate something and wants sources."
)


def make_skill(
    root: Path,
    *,
    name: str = "demo-skill",
    description: str = GOOD_DESCRIPTION,
    extra_frontmatter: str = "",
    body: str = "# Demo\n\nRead `agents/researcher.md` before answering.\n",
    touch: tuple[str, ...] = ("agents/researcher.md",),
) -> Path:
    """Create a minimal spec-shaped skill directory on disk."""
    root.mkdir(parents=True, exist_ok=True)
    frontmatter = f"---\nname: {name}\ndescription: {description}\n{extra_frontmatter}---\n\n"
    (root / "SKILL.md").write_text(frontmatter + body, encoding="utf-8")
    for relative in touch:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# placeholder\n", encoding="utf-8")
    return root


def errors(findings: list[Finding]) -> list[str]:
    return [f"{item.area}: {item.message}" for item in findings if item.level == "error"]


# ---------------------------------------------------------------------------
# frontmatter parsing helpers
# ---------------------------------------------------------------------------
def test_parse_frontmatter_supports_one_nested_map() -> None:
    meta = parse_frontmatter('name: x\ndescription: "quoted text"\nmetadata:\n  version: 1.2.0\n')
    assert meta["name"] == "x"
    assert meta["description"] == "quoted text"
    assert meta["metadata"] == {"version": "1.2.0"}


def test_estimate_tokens_counts_cjk_heavier_than_latin() -> None:
    assert estimate_tokens("深度搜索") > estimate_tokens("deep")
    assert estimate_tokens("") == 0


# ---------------------------------------------------------------------------
# validate_skill: the normative spec rules
# ---------------------------------------------------------------------------
def test_a_wellformed_skill_passes(tmp_path: Path) -> None:
    skill = make_skill(tmp_path / "demo-skill")
    assert not errors(validate_skill(skill)), "a spec-shaped skill must produce no errors"


@pytest.mark.parametrize(
    ("extra", "needle"),
    [
        ("trigger: demo\n", "trigger"),
        ("version: 1.0.0\n", "version"),
        ("allowed_tools: []\n", "allowed_tools"),
    ],
)
def test_non_spec_frontmatter_keys_are_rejected(tmp_path: Path, extra: str, needle: str) -> None:
    skill = make_skill(tmp_path / "demo-skill", extra_frontmatter=extra)
    found = errors(validate_skill(skill))
    assert any(needle in item for item in found), found


def test_missing_required_fields_are_errors(tmp_path: Path) -> None:
    skill = tmp_path / "bare"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\ndescription: only a description\n---\n\n# Bare\n", encoding="utf-8")
    assert any("name" in item for item in errors(validate_skill(skill)))


def test_skill_md_without_frontmatter_fails(tmp_path: Path) -> None:
    skill = tmp_path / "nofm"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# just a body\n", encoding="utf-8")
    assert any("frontmatter" in item for item in errors(validate_skill(skill)))


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("nexus-deep-research", True),
        ("skill2", True),
        ("Demo_Skill", False),
        ("-leading", False),
        ("trailing-", False),
        ("double--hyphen", False),
        ("a" * 65, False),
    ],
)
def test_name_shape_rules(tmp_path: Path, name: str, ok: bool) -> None:
    skill = make_skill(tmp_path / ("long-name" if len(name) > 63 else name), name=name)
    has_name_error = any("name" in item for item in errors(validate_skill(skill)))
    assert has_name_error != ok


def test_installed_directory_must_match_name_but_source_tree_may_differ(tmp_path: Path) -> None:
    skill = make_skill(tmp_path / "checkout-folder", name="demo-skill")
    assert any("directory" in item or "folder" in item for item in errors(validate_skill(skill)))
    assert not errors(validate_skill(skill, expect_dir_match=False))


def test_long_description_and_compatibility_are_errors(tmp_path: Path) -> None:
    skill = make_skill(
        tmp_path / "demo-skill",
        description="x" * 1100,
        extra_frontmatter="compatibility: " + "y" * 600 + "\n",
    )
    found = errors(validate_skill(skill))
    assert any("description" in item for item in found), found
    assert any("compatibility" in item for item in found), found


def test_dangling_component_reference_is_reported(tmp_path: Path) -> None:
    skill = make_skill(tmp_path / "demo-skill", body="See `agents/ghost.md` for the protocol.\n")
    assert any("agents/ghost.md" in item for item in errors(validate_skill(skill)))


def test_oversized_skill_md_is_flagged(tmp_path: Path) -> None:
    skill = make_skill(tmp_path / "demo-skill", body="\n".join(f"line {index}" for index in range(600)) + "\n")
    assert any("SKILL.md" in item or "size" in item for item in errors(validate_skill(skill)))


# ---------------------------------------------------------------------------
# privacy and secret scanning
# ---------------------------------------------------------------------------
def test_mask_private_replaces_home_paths_only() -> None:
    text = f'run from "{HOST}/WorkSpace/project/x" and "{WIN}\\x" plus /tmp/ok'
    masked, hits = mask_private(text)
    assert hits == 2, masked
    assert HOST not in masked and WIN not in masked
    assert "$HOME/" in masked and "/tmp/ok" in masked


def test_public_repository_urls_are_not_secret_false_positives() -> None:
    benign = "the validator lives at https://github.com/agentskills/agentskills/tree/main/skills-ref/src"
    assert scan_private(benign) == []
    assert not any(pattern.search(benign) for pattern in TOKEN_PATTERNS)


def test_token_shaped_strings_are_detected() -> None:
    leaked = "export GITHUB_PERSONAL_ACCESS_TOKEN=github_pat_" + "A" * 40 + " and sk-" + "b" * 24
    assert any(pattern.search(leaked) for pattern in TOKEN_PATTERNS)


# ---------------------------------------------------------------------------
# audit_release / build_bundle
# ---------------------------------------------------------------------------
def make_repo(root: Path, *, version: str = "1.0.0", governance: bool = True) -> Path:
    """A miniature repository carrying every file the release gate requires."""
    make_skill(root, name="demo-skill", body="# Demo\n")
    (root / "README.md").write_text(f"# demo {version}\n", encoding="utf-8")
    (root / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text(f"## [{version}]\n", encoding="utf-8")
    (root / "skill.json").write_text(json.dumps({"name": "demo-skill", "version": version}), encoding="utf-8")
    payload = {
        "config/settings.yaml": f"skill:\n  version: {version}\n",
        "runtime/cli.py": f'__version__ = "{version}"\n',
        "runtime/skill_spec.py": "",
        "nexus": "#!/bin/sh\n",
        "agents/planner.md": "# planner\n",
        "workflows/research-loop.md": "# loop\n",
        "schemas/evidence.schema.json": "{}",
    }
    for relative, text in payload.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if governance:
        for relative in (
            "CONTRIBUTING.md",
            "SECURITY.md",
            "CODE_OF_CONDUCT.md",
            "NOTICE",
            "docs/QUICKSTART.md",
            ".github/workflows/ci.yml",
        ):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# governance\n", encoding="utf-8")
    return root


def test_clean_miniature_repo_passes(tmp_path: Path) -> None:
    assert not errors(audit_release(make_repo(tmp_path / "clean")))


def test_audit_flags_private_paths_secrets_and_governance(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "dirty", governance=False)
    (repo / "examples").mkdir()
    (repo / "examples" / "session.md").write_text(f"open {HOST}/private/runs/x\n", encoding="utf-8")
    (repo / "config" / "mcp-map.yaml").write_text("token: github_pat_" + "C" * 40 + "\n", encoding="utf-8")
    found = errors(audit_release(repo))
    assert any("CONTRIBUTING.md" in item for item in found), found
    assert any("SECURITY.md" in item for item in found), found
    assert any("absolute path" in item for item in found), found
    assert any("credential" in item for item in found), found


def test_version_drift_is_an_error(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "drift")
    (repo / "skill.json").write_text(json.dumps({"name": "demo-skill", "version": "9.9.9"}), encoding="utf-8")
    assert "9.9.9" in set(version_map(repo).values())
    assert any("version drift" in item for item in errors(audit_release(repo)))


def test_foreign_homepage_in_manifest_is_an_error(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "homepage")
    (repo / "skill.json").write_text(
        json.dumps(
            {
                "name": "demo-skill",
                "version": "1.0.0",
                "homepage": "https://github.com/nextlevelbuilder/ui-ux-pro-max-skill",
            }
        ),
        encoding="utf-8",
    )
    assert any("third-party repo" in item for item in errors(audit_release(repo)))


def test_build_bundle_masks_paths_and_drops_private_material(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "bundle-src")
    (repo / "README.md").write_text(f"run it from {HOST}/project\n", encoding="utf-8")
    (repo / "Documentation").mkdir()
    (repo / "Documentation" / "private-business-plan.md").write_text("do not publish\n", encoding="utf-8")
    (repo / "TimeLine.md").write_text(f"work journal {HOST}/x\n", encoding="utf-8")
    stale = repo / "runs" / "old-run"
    stale.mkdir(parents=True)
    (stale / "report.md").write_text("stale artefact\n", encoding="utf-8")
    (repo / "memory").mkdir()
    (repo / "memory" / "research-patterns.json").write_text(
        json.dumps({"patterns": [{"note": "from my client acme-corp"}]}), encoding="utf-8"
    )
    cache = repo / "__pycache__"
    cache.mkdir()
    (cache / "junk.pyc").write_text("x", encoding="utf-8")

    payload = build_bundle(repo, tmp_path / "out", name="demo-skill")
    bundle = Path(payload["bundle"])

    shipped = {path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.is_file()}
    assert "SKILL.md" in shipped and "nexus" in shipped
    assert not any(part.startswith(("Documentation", "runs", "TimeLine", "__pycache__")) for part in shipped), shipped
    assert HOST not in (bundle / "README.md").read_text(encoding="utf-8")
    assert payload["masked_files"] >= 1
    assert json.loads((bundle / "memory" / "research-patterns.json").read_text(encoding="utf-8"))["patterns"] == []
    assert Path(payload["archive"]).is_file()
    assert not errors(validate_skill(bundle)), "the emitted bundle must itself be spec compliant"


def test_learned_memory_never_ships_but_is_reported(tmp_path: Path) -> None:
    """The author's ledger holds run names and phrasings from real research."""
    repo = make_repo(tmp_path / "memory")
    (repo / "memory").mkdir()
    ledger = repo / "memory" / "research-patterns.json"
    ledger.write_text(
        json.dumps({"schema_version": 1, "patterns": [{"id": "p1", "source_run": "acme-2026"}]}), encoding="utf-8"
    )
    relative = {path.relative_to(repo).as_posix() for path in release_files(repo)}
    assert "memory/research-patterns.json" not in relative, "user state must not be in the release set"
    areas = {item.area: item.level for item in audit_release(repo) if item.area == "runtime-state"}
    assert areas == {"runtime-state": "pass"}
    payload = build_bundle(repo, tmp_path / "out2", name="demo-skill", archive=False)
    shipped = json.loads((Path(payload["bundle"]) / "memory" / "research-patterns.json").read_text(encoding="utf-8"))
    assert shipped["patterns"] == [], "an install starts with an empty ledger, not the author's"


def test_release_file_set_excludes_caches_without_deleting_anything(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "sets")
    dist = repo / "dist"
    dist.mkdir()
    (dist / "junk.zip").write_text("x", encoding="utf-8")
    relative = {path.relative_to(repo).as_posix() for path in release_files(repo)}
    assert "SKILL.md" in relative
    assert not any(item.startswith(("dist/", "build/")) for item in relative)
    assert (dist / "junk.zip").is_file(), "auditing must never touch the source tree"


# ---------------------------------------------------------------------------
# this repository: the two gates that must stay green
# ---------------------------------------------------------------------------
def test_repository_skill_md_is_spec_compliant() -> None:
    assert not errors(validate_skill(REPO_ROOT, expect_dir_match=False))


def test_repository_tree_is_publishable() -> None:
    findings = audit_release(REPO_ROOT)
    assert not errors(findings), f"run `nexus open-source check`: {errors(findings)}"
    assert "MIT" in (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI surface for the beginner path
# ---------------------------------------------------------------------------
def test_skill_check_command_accepts_a_path(tmp_path: Path) -> None:
    assert cli.main(["skill-check", str(make_skill(tmp_path / "demo-skill"))]) == 0


def test_skill_check_fails_a_broken_skill(tmp_path: Path) -> None:
    broken = make_skill(tmp_path / "demo-skill", extra_frontmatter="trigger: nope\n")
    assert cli.main(["skill-check", str(broken)]) != 0


# ---------------------------------------------------------------------------
# nexus install: the payload an agent host actually loads
# ---------------------------------------------------------------------------
def test_install_list_and_dry_run_change_nothing(tmp_path: Path) -> None:
    dest = tmp_path / "skills"
    assert cli.main(["install", "--list"]) == 0
    assert cli.main(["install", "--dest", str(dest), "--dry-run"]) == 0
    assert not dest.exists(), "--dry-run must not create the destination"


def test_install_ships_only_the_publishable_payload(tmp_path: Path) -> None:
    dest = tmp_path / "skills"
    assert cli.main(["install", "--dest", str(dest)]) == 0
    target = dest / "nexus-deep-research"
    assert (target / "SKILL.md").is_file() and (target / "runtime" / "skill_spec.py").is_file()
    assert os.access(target / "nexus", os.X_OK), "the launcher must stay executable"
    for private in ("Documentation", "TimeLine.md", "PRP.md", "AGENTS.md", "dist", "build"):
        assert not (target / private).exists(), f"{private} must not be installed"
    ledger = json.loads((target / "memory" / "research-patterns.json").read_text(encoding="utf-8"))
    assert ledger["patterns"] == []


def test_install_prunes_stale_files_and_keeps_user_state(tmp_path: Path) -> None:
    dest = tmp_path / "skills"
    assert cli.main(["install", "--dest", str(dest)]) == 0
    target = dest / "nexus-deep-research"
    (target / "Documentation").mkdir()
    (target / "Documentation" / "old-private.md").write_text("leaked earlier\n", encoding="utf-8")
    (target / "leftover.txt").write_text("stale\n", encoding="utf-8")
    run = target / "runs" / "my-research"
    run.mkdir(parents=True)
    (run / "report.md").write_text("my work\n", encoding="utf-8")
    ledger = target / "memory" / "research-patterns.json"
    ledger.write_text(json.dumps({"schema_version": 1, "patterns": [{"id": "p1"}]}), encoding="utf-8")

    assert cli.main(["install", "--dest", str(dest)]) == 0
    assert not (target / "Documentation").exists(), "files the payload lacks are pruned"
    assert not (target / "leftover.txt").exists()
    assert (run / "report.md").is_file(), "research output is never removed"
    assert json.loads(ledger.read_text(encoding="utf-8"))["patterns"] == [{"id": "p1"}]


def test_install_refuses_to_overwrite_an_unrelated_directory(tmp_path: Path) -> None:
    dest = tmp_path / "skills"
    (dest / "nexus-deep-research").mkdir(parents=True)
    (dest / "nexus-deep-research" / "important.txt").write_text("not a skill\n", encoding="utf-8")
    assert cli.main(["install", "--dest", str(dest)]) != 0
    assert (dest / "nexus-deep-research" / "important.txt").is_file()
    assert cli.main(["install", "--dest", str(dest), "--force"]) == 0


def test_quickstart_offline_creates_a_usable_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--offline", "quickstart", "调研本地深度搜索方案"]) == 0
    templates = list((tmp_path / "runs").glob("*/input/RESEARCH.md"))
    assert templates, "quickstart must write a requirement template the user can edit"
    text = templates[0].read_text(encoding="utf-8")
    assert "研究目标" in text and "研究问题" in text
    root = templates[0].parent.parent  # RESEARCH.md -> input/ -> workspace
    assert (root / "state" / "research-plan.json").is_file()
    assert (root / "graph" / "research.graph.json").is_file()
    assert cli.main(["--offline", "-w", str(root), "run", "--iterations", "1"]) == 0
    assert cli.main(["--offline", "-w", str(root), "report"]) == 0
    report = (root / "output" / "report.md").read_text(encoding="utf-8")
    assert report.strip(), "an offline loop must still produce a readable report"


def test_quickstart_without_a_goal_still_scaffolds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A beginner who forgets the question must get guidance, not an abort."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--offline", "quickstart"]) == 0
    templates = list((tmp_path / "runs").glob("*/input/RESEARCH.md"))
    assert templates
    out = capsys.readouterr().out
    assert "Traceback" not in out and "no research graph" not in out
    assert "nexus intake" in out, "next steps must lead to intake when no plan exists yet"
    root = templates[0].parent.parent
    # intake must refuse an untouched template *and say what to do about it*
    with pytest.raises(SystemExit) as state:
        cli.main(["--offline", "-w", str(root), "intake"])
    message = str(state.value)
    assert "RESEARCH.md" in message and "--goal" in message, message
    # then accept it once the user has actually written a requirement
    (root / "input" / "RESEARCH.md").write_text(
        "# 研究目标：为本地项目选择合适的深度检索方案\n\n"
        "## 研究问题\n- 本地 MCP 方案有哪些？\n- 图结构带来什么收益？\n",
        encoding="utf-8",
    )
    assert cli.main(["--offline", "-w", str(root), "intake"]) == 0
    assert (root / "state" / "research-plan.json").is_file()


def test_open_source_build_writes_a_clean_bundle(tmp_path: Path) -> None:
    assert cli.main(["open-source", "build", "--out", str(tmp_path / "dist" / "nexus-deep-research")]) == 0
    bundle = tmp_path / "dist" / "nexus-deep-research"
    assert (bundle / "SKILL.md").is_file() and (bundle / "docs" / "QUICKSTART.md").is_file()
    assert not (bundle / "Documentation").exists(), "private business docs must not ship"
    assert not (bundle / "PRP.md").exists(), "repo-only design docs must not ship"
    leaked = [
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file()
        and path.suffix in {".md", ".json", ".jsonl", ".py", ".yaml", ".sh", ".toml"}
        and scan_private(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert not leaked, f"bundle still contains host paths: {leaked}"
