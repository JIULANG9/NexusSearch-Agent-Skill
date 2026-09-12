"""Agent Skills spec conformance and open-source release hygiene.

Two related jobs live here because they read the same files:

``validate_skill``
    Implements the normative rules of the Agent Skills specification
    (https://agentskills.io/specification.md) plus the constants used by the
    reference validator ``skills-ref`` (ALLOWED_FIELDS, the 64/1024/500 limits,
    "SKILL.md under 500 lines", "references relative to the skill root"). It
    answers one question: *will any spec-compliant host agent load this skill
    correctly?*

``audit_release`` / ``build_bundle``
    Answer a different question: *is this tree safe and pleasant to publish?*
    They look for private absolute paths, credentials, business material, build
    litter, governance gaps and version drift, and can materialise a sanitized
    install bundle under ``dist/`` so nothing has to be deleted from disk.

Stdlib only, like the rest of the runtime, so the checks work in any sandbox.
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------------------
# constants taken from the specification / skills-ref reference validator
# ---------------------------------------------------------------------------
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_COMPATIBILITY_LENGTH = 500
MAX_SKILL_LINES = 500
RECOMMENDED_SKILL_TOKENS = 5000

ALLOWED_FIELDS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}
REQUIRED_FIELDS = ("name", "description")

NAME_SHAPE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# component folders that SKILL.md is allowed to point at
REFERENCED_DIRS = (
    "agents/",
    "workflows/",
    "templates/",
    "config/",
    "schemas/",
    "runtime/",
    "scripts/",
    "docs/",
    "references/",
    "assets/",
)

ERROR = "error"
WARN = "warn"
PASS = "pass"


@dataclass(frozen=True)
class Finding:
    """One check result. ``level`` is :data:`ERROR`, :data:`WARN` or :data:`PASS`."""

    level: str
    area: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "area": self.area, "message": self.message}


def summarize(findings: Iterable[Finding]) -> dict[str, int]:
    items = list(findings)
    return {level: sum(1 for item in items if item.level == level) for level in (ERROR, WARN, PASS)}


# ---------------------------------------------------------------------------
# frontmatter
# ---------------------------------------------------------------------------
def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return ``(frontmatter_block, body)``; the block is ``None`` when absent."""
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    return None, text


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(block: str) -> dict[str, Any]:
    """Parse the deliberately small YAML subset the spec allows in frontmatter.

    Top level ``key: value`` pairs plus one level of nesting (used by
    ``metadata:``). Lists and multi-line scalars are not part of the format.
    """
    data: dict[str, Any] = {}
    current: dict[str, Any] | None = None
    for raw in block.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indented = raw[:1] in (" ", "\t")
        key, sep, value = raw.strip().partition(":")
        if not sep:
            continue
        key, value = key.strip(), _strip_quotes(value)
        if not indented:
            if value:
                data[key] = value
                current = None
            else:
                nested: dict[str, str] = {}
                data[key] = nested
                current = nested
        elif current is not None:
            current[key] = value
    return {key: value for key, value in data.items() if value not in (None, "", {})}


def estimate_tokens(text: str) -> int:
    """Rough token count: CJK ~1 token per char, Latin ~4 characters per token."""
    cjk = sum(1 for char in text if ord(char) > 0x2E7F)
    latin = len(text) - cjk
    return int(cjk * 0.9 + latin / 4)


# ---------------------------------------------------------------------------
# spec conformance
# ---------------------------------------------------------------------------
def validate_skill(skill_dir: Path | str, *, expect_dir_match: bool = True) -> list[Finding]:
    """Check a skill directory against the Agent Skills specification.

    ``expect_dir_match`` is off when auditing a *source repository*, whose folder
    name legitimately differs from the ``skills/<name>/`` install directory; any
    real install is checked with it on.
    """
    root = Path(skill_dir).expanduser().resolve()
    if not root.exists():
        return [Finding(ERROR, "skill", f"path does not exist: {root}")]
    if not root.is_dir():
        return [Finding(ERROR, "skill", f"not a directory: {root}")]

    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        return [Finding(ERROR, "skill", "missing required file: SKILL.md")]

    findings: list[Finding] = []
    text = skill_md.read_text(encoding="utf-8", errors="ignore")
    block, body = split_frontmatter(text)
    if block is None:
        return [Finding(ERROR, "frontmatter", "SKILL.md has no YAML frontmatter block")]

    meta = parse_frontmatter(block)

    extra = sorted(set(meta) - ALLOWED_FIELDS)
    if extra:
        findings.append(
            Finding(
                ERROR,
                "frontmatter",
                "field(s) not defined by the spec: "
                + ", ".join(extra)
                + " - the spec allows only "
                + ", ".join(sorted(ALLOWED_FIELDS))
                + " (move custom keys under `metadata:`)",
            )
        )
    else:
        findings.append(Finding(PASS, "frontmatter", f"only spec fields present ({', '.join(sorted(meta))})"))

    for field in REQUIRED_FIELDS:
        value = meta.get(field)
        if not isinstance(value, str) or not value.strip():
            findings.append(Finding(ERROR, "frontmatter", f"missing required field: {field}"))

    name = str(meta.get("name") or "").strip()
    if name:
        if len(name) > MAX_NAME_LENGTH:
            findings.append(Finding(ERROR, "name", f"{name!r} is {len(name)} chars, limit {MAX_NAME_LENGTH}"))
        if not NAME_SHAPE.match(name):
            findings.append(
                Finding(
                    ERROR,
                    "name",
                    f"{name!r} must be lowercase letters, digits and single hyphens "
                    "(no leading, trailing or consecutive hyphen)",
                )
            )
        if expect_dir_match and name != root.name:
            findings.append(
                Finding(
                    ERROR,
                    "name",
                    f"name {name!r} must match the directory name {root.name!r} - install it as skills/{name}/",
                )
            )
        else:
            findings.append(Finding(PASS, "name", f"{name} matches the directory name"))

    description = str(meta.get("description") or "")
    if description:
        if len(description) > MAX_DESCRIPTION_LENGTH:
            findings.append(Finding(ERROR, "description", f"{len(description)} chars, limit {MAX_DESCRIPTION_LENGTH}"))
        elif len(description) < 40:
            findings.append(
                Finding(WARN, "description", "too short to trigger reliably - say what it does *and* when to use it")
            )
        else:
            findings.append(Finding(PASS, "description", f"{len(description)} chars, within {MAX_DESCRIPTION_LENGTH}"))
        if not re.search(r"(use when|use for|when the user|触发|使用场景|适用于)", description, re.IGNORECASE):
            findings.append(
                Finding(WARN, "description", "no explicit 'when to use' clause - activation will be unreliable")
            )

    compatibility = str(meta.get("compatibility") or "")
    if compatibility:
        if len(compatibility) > MAX_COMPATIBILITY_LENGTH:
            findings.append(Finding(ERROR, "compatibility", f"{len(compatibility)} chars, limit {MAX_COMPATIBILITY_LENGTH}"))
        else:
            findings.append(Finding(PASS, "compatibility", "environment requirements declared"))
    else:
        findings.append(
            Finding(WARN, "compatibility", "not declared - this skill needs Python and local MCP servers, say so")
        )

    metadata = meta.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        findings.append(Finding(ERROR, "metadata", "must be a map of string keys to string values"))
    elif isinstance(metadata, dict):
        bad = [str(key) for key, value in metadata.items() if not isinstance(value, str)]
        if bad:
            findings.append(Finding(ERROR, "metadata", f"values must be strings, not: {', '.join(bad)}"))

    line_count = len(text.splitlines())
    if line_count > MAX_SKILL_LINES:
        findings.append(
            Finding(ERROR, "size", f"SKILL.md is {line_count} lines, the spec limit is {MAX_SKILL_LINES} - move detail into references/")
        )
    else:
        findings.append(Finding(PASS, "size", f"SKILL.md {line_count} lines (< {MAX_SKILL_LINES})"))
    tokens = estimate_tokens(body)
    if tokens > RECOMMENDED_SKILL_TOKENS:
        findings.append(
            Finding(WARN, "size", f"body about {tokens} tokens, above the recommended {RECOMMENDED_SKILL_TOKENS} - split into referenced files")
        )

    findings.extend(_check_references(root, body))
    if re.search(r"(^|[\s\"'(=])/home/|/Users/|[A-Za-z]:[\\/]Users[\\/]", body):
        findings.append(Finding(ERROR, "paths", "SKILL.md uses absolute host paths - reference files relative to the skill root"))
    else:
        findings.append(Finding(PASS, "paths", "all file references are relative to the skill root"))
    return findings


def _check_references(root: Path, body: str) -> list[Finding]:
    """Every component path named in the body must exist, one level deep at most."""
    findings: list[Finding] = []
    candidates = set(re.findall(r"[A-Za-z0-9_.\-/]+\.(?:md|json|ya?ml|py|sh)", body))
    named = sorted(item for item in candidates if item.split("/")[0] + "/" in REFERENCED_DIRS)
    missing = [item for item in named if not (root / item).exists()]
    for item in missing:
        findings.append(Finding(ERROR, "references", f"SKILL.md points at {item}, which does not exist"))
    for item in named:
        if item.count("/") > 1:
            findings.append(Finding(WARN, "references", f"{item} is more than one level deep - keep references flat"))
    if not missing:
        findings.append(Finding(PASS, "references", f"{len(named)} component file references all resolve"))
    return findings


# ---------------------------------------------------------------------------
# version single source of truth
# ---------------------------------------------------------------------------
def _regex_version(pattern: str) -> Callable[[Path], str | None]:
    compiled = re.compile(pattern)

    def reader(path: Path) -> str | None:
        if not path.is_file():
            return None
        match = compiled.search(path.read_text(encoding="utf-8", errors="ignore"))
        return match.group(1) if match else None

    return reader


VERSION_SOURCES: dict[str, Callable[[Path], str | None]] = {
    "config/settings.yaml": _regex_version(r"(?m)^\s+version:\s*([0-9][^\s]*)$"),
    "SKILL.md": _regex_version(r"(?m)^\s+version:\s*\"?([0-9][^\s\"]*)\"?$"),
    "runtime/__init__.py": _regex_version(r'__version__\s*=\s*"([^"]+)"'),
    "runtime/mcp_router.py": _regex_version(r"CLIENT_INFO\s*=\s*\{[^}]*\"version\":\s*\"([^\"]+)\""),
    "pyproject.toml": _regex_version(r'(?m)^version\s*=\s*"([^"]+)"'),
    "skill.json": _regex_version(r'"version":\s*"([^"]+)"'),
}


def version_map(root: Path) -> dict[str, str | None]:
    return {name: reader(root / name) for name, reader in VERSION_SOURCES.items()}


# ---------------------------------------------------------------------------
# release hygiene
# ---------------------------------------------------------------------------
PRIVATE_PATH = re.compile(
    r"/home/[A-Za-z0-9._-]+/|/Users/[A-Za-z0-9._-]+/|/root/[A-Za-z0-9._-]+/|[A-Za-z]:[\\/]Users[\\/][^\"'\s\\]*"
)
TOKEN_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9_\-./])"),  # trailing guard: no match inside URLs
    re.compile(r"(?i)\b(?:api[_-]?key|secret|password|token|authorization)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.]{16,}"),
)

# Never shipped: caches, build litter, run artefacts, and private material.
RELEASE_EXCLUDE_DIRS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".playwright-mcp",
    ".online",
    ".venv",
    "venv",
    "__pycache__",
    "build",
    "dist",
    "runs",
    "node_modules",
    "Documentation",
    ".idea",
    ".vscode",
}
RELEASE_EXCLUDE_FILES = {"TimeLine.md", ".DS_Store"}
# Runtime state, not skill payload: the author's ledger records run names and query
# phrasings from real research, which is exactly what must not travel with an install.
# `build_bundle` replaces it with an empty template; `cmd_install` seeds one instead.
RUNTIME_STATE_FILES = {"memory/research-patterns.json"}
# Valuable to contributors, useless inside an installed skill.
REPO_ONLY_FILES = {
    "PRP.md",
    "AGENTS.md",
    "Deep Research Skill 设计方案.md",
    "docs/OPTIMIZATION-PLAN.md",
}
RELEASE_REQUIRED_FILES = (
    "SKILL.md",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "skill.json",
    "config/settings.yaml",
    "runtime/cli.py",
    "runtime/skill_spec.py",
    "nexus",
    "agents/planner.md",
    "workflows/research-loop.md",
    "schemas/evidence.schema.json",
)
GOVERNANCE_FILES = (
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "NOTICE",
    "docs/QUICKSTART.md",
    ".github/workflows/ci.yml",
)
# third-party repositories that must not masquerade as our own origin
FOREIGN_REPOS = ("nextlevelbuilder/ui-ux-pro-max-skill",)

TEXT_SUFFIXES = {".md", ".json", ".jsonl", ".py", ".yaml", ".yml", ".txt", ".sh", ".toml", ".mmd", ".cfg", ".ini"}


def scan_private(text: str) -> list[str]:
    """Return the private-path / credential matches in *text*.

    Shared by the audit and the bundle residual check so the detector is never
    duplicated as a literal inside a scanned file (a pattern that contains its
    own trigger string matches itself).
    """
    hits = [match.group(0) for pattern in TOKEN_PATTERNS for match in pattern.finditer(text)]
    hits += PRIVATE_PATH.findall(text)
    return hits


def _cache(relative: Path) -> bool:
    """True for folders that are machine state rather than source."""
    parts = relative.parts
    return any(part in RELEASE_EXCLUDE_DIRS for part in parts[:-1]) or any(part.endswith(".egg-info") for part in parts)


def _excluded(relative: Path) -> bool:
    """True for anything that must not enter an installable bundle."""
    if _cache(relative):
        return True
    posix = relative.as_posix()
    return posix in RELEASE_EXCLUDE_FILES or posix in REPO_ONLY_FILES or posix in RUNTIME_STATE_FILES


def release_files(root: Path) -> list[Path]:
    """Files that ship in the bundle (tests included, caches and private material not)."""
    return [path for path in sorted(root.rglob("*")) if path.is_file() and not _excluded(path.relative_to(root))]


def audit_release(root: Path | str) -> list[Finding]:
    """Is this tree publishable? Privacy, secrets, governance, version drift, spec conformance."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        return [Finding(ERROR, "release", f"not a directory: {root}")]
    findings: list[Finding] = []
    files = release_files(root)

    for relative in RELEASE_REQUIRED_FILES:
        if not (root / relative).exists():
            findings.append(Finding(ERROR, "release-files", f"required skill payload missing: {relative}"))
    findings.append(Finding(PASS, "release-files", f"{len(files)} files would ship"))

    for relative in GOVERNANCE_FILES:
        if not (root / relative).is_file():
            hard = relative in ("CONTRIBUTING.md", "SECURITY.md")
            findings.append(Finding(ERROR if hard else WARN, "governance", f"missing {relative}"))
    if all((root / relative).is_file() for relative in GOVERNANCE_FILES):
        findings.append(Finding(PASS, "governance", "contributing, security, conduct, notice, quickstart and CI all present"))

    secret_hits: list[str] = []
    private_hits: list[str] = []
    for path in files:
        if path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        relative = path.relative_to(root).as_posix()
        if any(pattern.search(text) for pattern in TOKEN_PATTERNS):
            secret_hits.append(relative)
        if PRIVATE_PATH.search(text):
            private_hits.append(relative)
    for relative in secret_hits:
        findings.append(
            Finding(ERROR, "secrets", f"credential-shaped string in {relative} - rotate it and reference an env var instead")
        )
    if not secret_hits:
        findings.append(Finding(PASS, "secrets", "no credentials in the release set"))
    for relative in private_hits[:12]:
        findings.append(Finding(ERROR, "privacy", f"host-specific absolute path in {relative} - `nexus open-source build` masks these"))
    if len(private_hits) > 12:
        findings.append(Finding(ERROR, "privacy", f"...and {len(private_hits) - 12} more files with absolute host paths"))
    if not private_hits:
        findings.append(Finding(PASS, "privacy", "no /home, /Users or drive-letter user paths in the release set"))

    shipped_state = [
        path.relative_to(root).as_posix() for path in files if path.relative_to(root).as_posix() in RUNTIME_STATE_FILES
    ]
    for relative in shipped_state:
        findings.append(Finding(ERROR, "runtime-state", f"{relative} is user state and must not be in the release set"))
    if not shipped_state:
        ledger = root / "memory" / "research-patterns.json"
        note = ""
        if ledger.is_file():
            local = read_json_or_none(ledger) or {}
            patterns = local.get("patterns") if isinstance(local, dict) else None
            if patterns:
                note = f" (local ledger holds {len(patterns)} learned patterns; they stay on this machine)"
        findings.append(Finding(PASS, "runtime-state", "memory ledger excluded from the release set" + note))

    if (root / "skill.json").is_file():
        manifest = read_json_or_none(root / "skill.json")
        if manifest is None:
            findings.append(Finding(ERROR, "skill.json", "is not valid JSON"))
        else:
            homepage = str(manifest.get("homepage") or "")
            if any(repo in homepage for repo in FOREIGN_REPOS):
                findings.append(
                    Finding(ERROR, "skill.json", f"homepage points at a third-party repo ({homepage}) - this manifest describes *this* skill")
                )
            elif homepage:
                findings.append(Finding(PASS, "skill.json", "homepage is not a foreign repository"))
            package = manifest.get("package")
            if package and package != manifest.get("name"):
                findings.append(
                    Finding(WARN, "skill.json", f"package {package!r} differs from name {manifest.get('name')!r} - keep one identifier")
                )
            front = read_frontmatter(root) or {}
            if str(manifest.get("name") or "") != str(front.get("name") or ""):
                findings.append(Finding(WARN, "skill.json", "name disagrees with the SKILL.md frontmatter"))

    versions = {key: value for key, value in version_map(root).items() if value}
    distinct = sorted(set(versions.values()))
    if len(distinct) > 1:
        detail = ", ".join(f"{name}={value}" for name, value in sorted(versions.items()))
        findings.append(Finding(ERROR, "version", f"version drift: {detail} - config/settings.yaml is the source of truth"))
    elif len(versions) < len(VERSION_SOURCES):
        missing = sorted(set(VERSION_SOURCES) - set(versions))
        findings.append(Finding(WARN, "version", f"no version string found in {', '.join(missing)}"))
    else:
        findings.append(Finding(PASS, "version", f"single version {distinct[0]} across {len(versions)} declarations"))

    if (root / ".gitignore").is_file():
        ignored = (root / ".gitignore").read_text(encoding="utf-8", errors="ignore")
        present = {path.name for path in root.iterdir()}
        for relative in sorted(RELEASE_EXCLUDE_DIRS & present):
            if relative in {"__pycache__", ".git"}:
                continue
            if relative not in ignored:
                findings.append(Finding(WARN, "gitignore", f"{relative}/ exists in the tree but is not ignored"))

    findings.extend(validate_skill(root, expect_dir_match=False))
    front_name = str((read_frontmatter(root) or {}).get("name") or "")
    if front_name and front_name != root.name:
        findings.append(
            Finding(PASS, "install-name", f"source folder is {root.name!r}; hosts must install it as skills/{front_name}/")
        )
    return findings


def read_frontmatter(root: Path) -> dict[str, Any] | None:
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        return None
    block, _ = split_frontmatter(skill_md.read_text(encoding="utf-8", errors="ignore"))
    return parse_frontmatter(block) if block else None


def read_json_or_none(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# sanitized bundle
# ---------------------------------------------------------------------------
def mask_private(text: str) -> tuple[str, int]:
    """Replace host-specific home paths with a neutral placeholder, counting replacements."""
    count = 0

    def replace(_match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "$HOME/"

    return PRIVATE_PATH.sub(replace, text), count


MEMORY_TEMPLATE = {
    "schema_version": 1,
    "patterns": [],
    "note": "Learned query patterns are written here at runtime by `nexus remember`.",
}


def build_bundle(
    root: Path | str,
    destination: Path | str | None = None,
    *,
    name: str = "nexus-deep-research",
    archive: bool = True,
) -> dict[str, Any]:
    """Copy the publishable subset into ``dist/<name>/`` with private paths masked.

    Nothing is deleted from the source tree: this is a build artefact, so a
    private research workspace and a shareable skill coexist on the same disk.
    """
    root = Path(root).expanduser().resolve()
    destination = Path(destination).expanduser().resolve() if destination else root / "dist"
    # the spec binds the skill name to the install directory name, so a bundle in
    # a folder called anything else cannot be loaded; treat --out as a parent dir
    if destination.name != name:
        destination = destination / name
    if destination.exists():
        shutil.rmtree(destination)
    masked_files = 0
    copied: list[str] = []
    for source in release_files(root):
        relative = source.relative_to(root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix not in TEXT_SUFFIXES:
            shutil.copy2(source, target)
        else:
            masked, hits = mask_private(source.read_text(encoding="utf-8", errors="ignore"))
            if hits:
                masked_files += 1
            target.write_text(masked, encoding="utf-8")
        copied.append(relative.as_posix())

    # the shipped memory file must be an empty template, never a real ledger
    memory = destination / "memory" / "research-patterns.json"
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text(json.dumps(MEMORY_TEMPLATE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    version = version_map(root).get("config/settings.yaml") or "unversioned"
    payload: dict[str, Any] = {"bundle": str(destination), "files": len(copied), "masked_files": masked_files, "archive": None}
    if archive:
        archive_path = destination.parent / f"{name}-{version}.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as bundle:
            for item in sorted(destination.rglob("*")):
                if item.is_file():
                    bundle.write(item, Path(name) / item.relative_to(destination))
        payload["archive"] = str(archive_path)
    return payload
