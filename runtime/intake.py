"""Turn a research specification folder into a plan and a seeded graph.

``nexus intake`` is the entry point of the pipeline: it reads whatever the
user dropped into ``input/`` (spec markdown, JSON, notes, papers), looks at
the project under study, and produces the two artifacts every later stage
depends on - ``state/research-plan.json`` and a goal/question/topic skeleton
in ``graph/research.graph.json``.

Everything here is deterministic and local: no model call, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .util import (
    CJK_RUN,
    GENERIC_SEARCH_TERMS,
    SKILL_ROOT,
    ascii_terms,
    is_cjk_heavy,
    now_iso,
    read_json,
    rotate_pick,
    slugify,
)

SPEC_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".json", ".yaml", ".yml", ".csv", ".html"}
SKIP_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build",
    ".next", ".nuxt", "target", "out", "site-packages", ".mypy_cache", ".pytest_cache",
    ".idea", ".vscode", "coverage", ".gradle", "vendor", ".tox", ".ruff_cache",
}
MANIFESTS = (
    "pyproject.toml", "package.json", "requirements.txt", "go.mod", "Cargo.toml",
    "pom.xml", "build.gradle", "build.gradle.kts", "setup.py", "Gemfile", "composer.json",
)
ENTRY_HINTS = (
    "main.py", "cli.py", "__main__.py", "app.py", "server.py", "index.ts", "index.tsx",
    "main.ts", "main.go", "main.rs", "index.js", "App.tsx", "manage.py", "wsgi.py", "asgi.py",
)
CONTEXT_HEADINGS = ("背景", "上下文", "context", "现状", "环境")
# An explicit objective section always outranks a document title: template H1s such
# as "# Research Spec" carry no information about what is actually being researched.
OBJECTIVE_HEADINGS = (
    "研究目标", "研究目的", "目标", "目的", "objective", "goal", "research goal",
    "research objective", "purpose", "要回答的问题",
)
TEMPLATE_TITLES = {
    "research spec", "spec", "specification", "input spec", "research brief", "brief",
    "template", "research template", "研究规范", "研究模板", "输入规范", "需求模板", "文档",
    "deep research", "research",
}
QUESTION_HEADINGS = ("研究问题", "问题", "需求", "目标拆解", "question", "objective", "scope", "关键问题")
CONSTRAINT_HEADINGS = ("约束", "限制", "要求", "constraint", "must", "规则", "安全")
NON_GOAL_HEADINGS = ("非目标", "范围外", "out of scope", "non-goal", "不做")
SOURCE_HEADINGS = ("数据源", "来源", "资料", "source", "参考", "输入文件")
CRITERIA_HEADINGS = ("验收", "成功标准", "success", "done", "标准")

# Generic research facets, used when a spec does not enumerate questions.
DEFAULT_FACETS: tuple[tuple[str, str], ...] = (
    ("landscape", "现状与主流方案是什么"),
    ("architecture", "架构与关键机制如何运作"),
    ("practice", "有哪些可落地的最佳实践"),
    ("tradeoff", "各方案的成本与权衡是什么"),
    ("risk", "主要风险与失败模式是什么"),
    ("implementation", "在本项目中应如何实施"),
)


@dataclass
class SpecText:
    """One input document and the text we could actually read from it."""

    path: Path
    text: str
    kind: str = "spec"
    structured: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class ContextDigest:
    """What the local workspace under study looks like (filesystem only)."""

    root: Path
    name: str = ""
    readme: str = ""
    manifests: list[dict[str, Any]] = field(default_factory=list)
    languages: list[dict[str, Any]] = field(default_factory=list)
    tree: list[str] = field(default_factory=list)
    entrypoints: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    file_count: int = 0
    line_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "name": self.name,
            "file_count": self.file_count,
            "line_count": self.line_count,
            "languages": self.languages,
            "manifests": [item.get("name") for item in self.manifests],
            "entrypoints": self.entrypoints,
            "tests": self.tests[:10],
            "docs": self.docs[:10],
            "skills": self.skills[:10],
        }

    def summary(self) -> str:
        parts = [f"{self.file_count} files"]
        if self.languages:
            parts.append(", ".join(f"{one['language']}:{one['files']}" for one in self.languages[:4]))
        if self.manifests:
            parts.append("manifests: " + ", ".join(str(one["name"]) for one in self.manifests[:4]))
        if self.entrypoints:
            parts.append("entrypoints: " + ", ".join(Path(one).name for one in self.entrypoints[:4]))
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# reading the specification
# ---------------------------------------------------------------------------
def find_spec_files(input_dir: Path | str, extra: Iterable[Path | str] = ()) -> list[Path]:
    """Collect candidate spec files from ``input_dir`` plus explicit paths."""
    found: list[Path] = []
    base = Path(input_dir)
    if base.is_dir():
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SPEC_SUFFIXES:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            found.append(path)
    for item in extra:
        path = Path(item)
        if path.is_dir():
            found.extend(find_spec_files(path))
        elif path.is_file():
            found.append(path)
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def _normalise_spec(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _scaffold_bodies() -> set[str]:
    """Normalised text of the templates ``init --templates`` drops into ``input/``.

    An untouched scaffold is not user input. Left in the corpus it *did* become the
    research objective in a live run: its "非目标" bullets and its two example
    questions outranked the real spec that sat next to it in the same folder.
    """
    bodies: set[str] = set()
    for candidate in sorted((SKILL_ROOT / "templates").glob("*.md")):
        try:
            bodies.add(_normalise_spec(candidate.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return {body for body in bodies if body}


def read_spec_files(paths: Iterable[Path], max_chars: int = 24000) -> list[SpecText]:
    """Read specs, decoding JSON/YAML payloads into structured hints."""
    docs: list[SpecText] = []
    scaffold = _scaffold_bodies()
    budget = max_chars
    for path in paths:
        if budget <= 0:
            break
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _normalise_spec(raw) in scaffold:
            continue  # the user never edited this template copy
        kind = "spec"
        structured: dict[str, Any] = {}
        if path.suffix.lower() == ".json":
            payload = read_json(path, None)
            if isinstance(payload, dict):
                structured = payload
                kind = "structured"
                text = "\n".join(str(value) for value in payload.values())[:budget]
            else:
                text = raw[:budget]
        else:
            text = raw[:budget]
        budget -= len(text)
        docs.append(SpecText(path=path, text=text, kind=kind, structured=structured))
    return docs


def _section_texts(text: str, headings: Iterable[str]) -> list[str]:
    """Return bullet lines that live under any matching markdown heading.

    Heading matching is substring based ("goal" also names "research goals"), so a
    negative-scope heading has to be switched off explicitly: "非目标" contains
    "目标", and letting it match turned an out-of-scope bullet list into the
    research objective of a real run.
    """
    wanted = [word.lower() for word in headings]
    blocked = [word.lower() for word in NON_GOAL_HEADINGS]
    lines = text.splitlines()
    collected: list[str] = []
    active = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            header = stripped.lstrip("#").strip().lower()
            active = not any(word in header for word in blocked) and any(word in header for word in wanted)
            continue
        if not active or not stripped:
            continue
        if stripped.startswith(("-", "*", "+", "•")) or re.match(r"^\d+[.)、]", stripped):
            collected.append(re.sub(r"^([-*+]|\d+[.)、])\s*", "", stripped).strip())
        elif collected and line[:1] in (" ", "\t") and not stripped.startswith(("```", "|")):
            # An indented, unpunctuated continuation of the bullet above it: a spec
            # written with soft-wrapped bullets used to become N fragments of one
            # question, each then driving its own search angle.
            collected[-1] = f"{collected[-1]} {stripped}".strip()
        elif len(stripped) > 6 and not stripped.startswith(("```", "|")):
            collected.append(stripped)
    # ``**Q2 Foo**`` is a formatting convention, not search content: the markers
    # survived into node titles and turned into queries like "现状 **Q2 ltx".
    cleaned = [_strip_markdown(item) for item in collected]
    return [item for item in cleaned if item and not _is_placeholder(item)]


def _strip_markdown(text: str) -> str:
    return re.sub(r"\*\*|\*|`{1,3}", "", text).strip()


def _question_lines(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("```"):
            continue
        cleaned = re.sub(r"^([-*+]|\d+[.)、])\s*", "", stripped).strip()
        if len(cleaned) < 6 or len(cleaned) > 160:
            continue
        if _is_placeholder(cleaned):
            continue  # the untouched spec template must not contribute questions
        if cleaned.endswith(("?", "？")):
            out.append(cleaned)
    return out


PLACEHOLDER_MARKERS = ("在这里", "填写", "待填", "todo", "fixme", "<", "...")
GENERIC_HEADINGS = {
    "研究目标", "目标", "objective", "goal", "title", "研究目的", "需求", "研究问题", "问题",
    "背景", "上下文", "context", "说明", "概述", "overview", "摘要", "abstract", "约束",
    "要求", "验收标准", "验收", "数据源", "来源", "范围", "scope", "结论", "交付物",
    "research goal", "research objective", "非目标", "成功标准", "输入", "input",
}


def _deplaceholder(text: str) -> str:
    return re.sub(r"[（(\[<].*?[)）\]>]", "", text or "").strip(" :：-—").strip()


#: Leading labels used by the spec template for "fill this in" instructions.
SCAFFOLD_LABELS = ("（可选", "（必填", "（需要", "(可选", "(必填", "（在这里", "(在这里")


def _is_placeholder(text: str) -> bool:
    cleaned = _deplaceholder(text)
    lowered = text.strip().lower()
    if not cleaned or len(cleaned) < 4:
        return True
    if cleaned.lower() in GENERIC_HEADINGS:
        return True
    if text.strip().startswith(SCAFFOLD_LABELS):
        return True  # a labelled instruction from a spec template, not content
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS) and len(cleaned) < 12


def _first_meaningful_title(text: str) -> str:
    """Prefer a real H1; otherwise the first non-placeholder line under it."""
    for match in re.finditer(r"^#{1,3}\s+(.+)$", text, flags=re.MULTILINE):
        candidate = match.group(1).strip()
        if not _is_placeholder(candidate):
            return candidate
        for line in text[match.end() : match.end() + 600].splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if len(stripped) >= 8 and not _is_placeholder(stripped):
                return stripped
            break
    return ""


def _goal_from(docs: Sequence[SpecText], fallback: str) -> str:
    for doc in docs:
        for key in ("objective", "goal", "research_goal", "title"):
            value = doc.structured.get(key)
            if isinstance(value, str) and not _is_placeholder(value) and len(value.strip()) > 4:
                return value.strip()
    for doc in docs:
        stated = _section_texts(doc.text, OBJECTIVE_HEADINGS)
        if stated:
            parts = [item.strip().rstrip("，,；;。 ") for item in stated[:2] if not _is_placeholder(item)]
            objective = "；".join(part for part in parts if part)
            if objective and not _looks_like_boilerplate(objective):
                return objective[:240]
    for doc in docs:
        candidate = _first_meaningful_title(doc.text)
        if candidate and not _looks_like_boilerplate(candidate):
            return candidate
    if fallback.strip():
        return fallback.strip()
    raise ValueError("no objective found: pass --goal or provide an input spec")


def _looks_like_boilerplate(text: str) -> bool:
    lowered = _deplaceholder(text).lower().strip(" :：#")
    return lowered in TEMPLATE_TITLES | {
        "readme", "index", "table of contents", "目录", "说明", "overview", "概述", ""
    }


def _keywords(goal: str, docs: Sequence[SpecText], limit: int = 10) -> list[str]:
    counts: dict[str, int] = {}
    corpus = goal + "\n" + "\n".join(doc.text for doc in docs[:4])
    for match in re.finditer(r"[A-Za-z][A-Za-z0-9+.#-]{2,24}", corpus):
        lowered = match.group(0).lower()
        if lowered in _STOPWORDS or lowered in GENERIC_SEARCH_TERMS:
            continue
        # A token that is part of a local path (docs/DECISIONS.md) describes the
        # workspace, not the subject, and poisons every query built from it.
        before = corpus[match.start() - 1] if match.start() else ""
        after = corpus[match.end()] if match.end() < len(corpus) else ""
        if before in "/\\_" or after in "/\\":
            continue
        counts[lowered] = counts.get(lowered, 0) + 1
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,8}", corpus):
        counts[chunk] = counts.get(chunk, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    picked: list[str] = []
    for word, _count in ranked:
        if word not in picked:
            picked.append(word)
        if len(picked) >= limit:
            break
    return picked


_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "our", "are", "was",
    "will", "can", "all", "any", "use", "using", "based", "system", "research", "project",
    "http", "https", "www", "com", "org", "md", "yaml", "json", "true", "false", "none",
    "需要", "可以", "进行", "一个", "我们", "如何", "什么", "以及", "使用", "支持", "相关",
}


def parse_spec(docs: Sequence[SpecText], goal_fallback: str = "") -> dict[str, Any]:
    """Extract a plan skeleton from spec documents (no model required)."""
    text = "\n\n".join(doc.text for doc in docs)
    goal = _goal_from(docs, goal_fallback)
    keywords = _keywords(goal, docs)

    questions = [one for doc in docs for one in _section_texts(doc.text, QUESTION_HEADINGS)]
    questions = questions or _question_lines(text)
    if not questions:
        # Facet *questions* (not keys) so `_question_kind` can classify each angle.
        questions = [title for _facet, title in DEFAULT_FACETS]
    deduped: list[str] = []
    for question in questions:
        if question not in deduped:
            deduped.append(question)
        if len(deduped) >= 12:
            break

    constraints = [one for doc in docs for one in _section_texts(doc.text, CONSTRAINT_HEADINGS)]
    non_goals = [one for doc in docs for one in _section_texts(doc.text, NON_GOAL_HEADINGS)]
    criteria = [one for doc in docs for one in _section_texts(doc.text, CRITERIA_HEADINGS)]
    sources = sorted({url for doc in docs for url in re.findall(r"https?://[^\s)\"'<>]+", doc.text)})[:20]
    files = [str(doc.path) for doc in docs]
    local_inputs = [one for doc in docs for one in _section_texts(doc.text, SOURCE_HEADINGS) if "://" not in one]

    return {
        "objective": goal,
        "title": goal[:120],
        "questions": deduped[:12] or [goal],
        "constraints": constraints[:12],
        "non_goals": non_goals[:12],
        "success_criteria": criteria[:12],
        "keywords": keywords,
        "sources": sources,
        "context_inputs": sorted(set([*files, *local_inputs]))[:40],
        "spec_documents": files,
        "created_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# workspace context
# ---------------------------------------------------------------------------
def _walk(root: Path, limit_files: int = 4000):
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            yield path


LANGUAGE_BY_SUFFIX = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript", ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".rb": "ruby", ".php": "php", ".sh": "shell",
    ".sql": "sql", ".html": "html", ".css": "css", ".vue": "vue", ".md": "markdown",
    ".yaml": "yaml", ".yml": "yaml", ".json": "json", ".toml": "toml",
}


def scan_context(target: Path | str, max_files: int = 4000) -> ContextDigest:
    """Summarise the project under study with filesystem reads only."""
    root = Path(target).expanduser().resolve()
    digest = ContextDigest(root=root, name=root.name)
    if not root.is_dir():
        return digest
    suffix_counts: dict[str, int] = {}
    by_language: dict[str, dict[str, int]] = {}
    tree: dict[str, int] = {}
    for path in _walk(root, max_files):
        if digest.file_count >= max_files:
            break
        digest.file_count += 1
        relative = path.relative_to(root)
        if relative.parts:
            tree[relative.parts[0]] = tree.get(relative.parts[0], 0) + 1
        suffix = path.suffix.lower()
        suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
        language = LANGUAGE_BY_SUFFIX.get(suffix)
        if language:
            bucket = by_language.setdefault(language, {"language": language, "files": 0, "lines": 0})
            bucket["files"] += 1
            if language not in {"markdown", "json", "yaml", "toml"} and path.stat().st_size < 400_000:
                try:
                    lines = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace"))
                except OSError:
                    lines = 0
                bucket["lines"] += lines
                digest.line_count += lines
        name = path.name
        if name in MANIFESTS:
            digest.manifests.append({"name": name, "path": str(relative), "size": path.stat().st_size})
        if name in ENTRY_HINTS:
            digest.entrypoints.append(str(relative))
        if name.lower().startswith("readme"):
            try:
                digest.readme = path.read_text(encoding="utf-8", errors="replace")[:2500]
            except OSError:
                pass
        parts_lower = {part.lower() for part in relative.parts}
        if parts_lower & {"test", "tests", "spec", "e2e"}:
            digest.tests.append(str(relative))
        if parts_lower & {"docs", "doc", "documentation", "wiki"} or suffix == ".md":
            digest.docs.append(str(relative))
        if name in {"SKILL.md", "AGENTS.md", "CLAUDE.md", ".cursorrules"} or path.parent.name == "skills":
            digest.skills.append(str(relative))
    digest.languages = sorted(by_language.values(), key=lambda item: (-item["files"], item["language"]))[:10]
    digest.tree = [f"{name}/ ({count})" for name, count in sorted(tree.items(), key=lambda item: -item[1])[:14]]
    digest.manifests = digest.manifests[:12]
    return digest


# ---------------------------------------------------------------------------
# plan + graph
# ---------------------------------------------------------------------------
def _question_kind(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("对比", "选型", "vs", "versus", "compare", "哪个好", "权衡")):
        return "comparative"
    if any(word in lowered for word in ("风险", "安全", "评估", "是否", "值得", "可信", "risk")):
        return "evaluative"
    if any(word in lowered for word in ("如何", "怎么", "步骤", "实施", "how", "migrate", "部署")):
        return "procedural"
    if any(word in lowered for word in ("预测", "趋势", "未来", "will", "2027")):
        return "predictive"
    return "factual"


def _search_strategy(
    question: str, capability_hint: str, keywords: Sequence[str], *, angle: int = 0
) -> list[str]:
    """Query shapes that consistently work with a local metasearch server."""
    base = _strip_markdown(question).rstrip("?？。.").strip()
    # Terms the question itself supplies win; otherwise fall back to the plan
    # keywords, rotated per question so sibling queries probe different angles.
    own = ascii_terms(base)
    pool = own or ascii_terms(" ".join(keywords), limit=8) or [str(word) for word in keywords]
    if CJK_RUN.search(base):  # any Chinese prose at all: the ASCII terms carry it
        # Chinese prose must never reach an English-only engine (measured: it
        # returns geo-junk), so the ASCII technical terms carry the intent.
        # Four terms is the ceiling for this host: "vlm qwen2.5-vl
        # cogvlm2-video official documentation" made bing/google answer with
        # South African speed-test portals, while "LTX-Video LoRA dataset caption"
        # returned the upstream repository. Over-specified queries are answered by
        # popularity, not by relevance, and popularity is geo-personalised.
        core = " ".join(rotate_pick(pool, angle, 4))[:110].strip()
        terse = len(core.split()) > 2
    else:
        core = " ".join([base, *rotate_pick(pool, angle, 2)])[:110].strip()
        terse = False
    if capability_hint == "docs.library":
        suffixes = ("official documentation", "api reference", "changelog")
    elif capability_hint == "code.local_structure":
        return [f"local: {base}"]
    elif capability_hint == "code.repository":
        suffixes = ("github", "open source implementation", "example repository")
    else:
        suffixes = ("official documentation", "2026 comparison", "specification", "limitations criticism")
    # The bare term query goes first: index-scoped engines (github, pypi,
    # stackoverflow) rank it far better than a sentence with "official
    # documentation" glued on.
    if terse:  # the engine-group rotation supplies the variety instead of padding
        return [core]
    return list(dict.fromkeys([core, *(f"{core} {suffix}".strip() for suffix in suffixes)]))


def build_plan(
    parsed: dict[str, Any],
    context: ContextDigest | None,
    *,
    mode: str = "auto",
    language: str = "auto",
    workspace_root: Path | str = "",
    deliverables: Iterable[str] = (),
) -> dict[str, Any]:
    """Assemble the ``plan.schema.json`` document the whole pipeline reads."""
    keywords = [str(item) for item in (parsed.get("keywords") or [])]
    if context is not None:
        # A directory or module named in the spec is a local artefact, not a web
        # search term: "demo-project" once turned every query into a repo-scoped
        # dead end and the loop collected nothing but Microsoft Learn pages.
        names = [*[str(item) for item in (context.tree or [])], *context.entrypoints, *context.tests, *context.docs]
        local = {Path(str(name)).name.split(".")[0].lower() for name in names}
        local |= {str(name).split(".")[0].lower() for name in names if "/" not in str(name)}
        local |= {str(manifest.get("name") or "").lower() for manifest in (context.manifests or [])}
        keywords = [word for word in keywords if word.lower() not in local] or keywords
    objective = str(parsed.get("objective") or "")
    raw_questions = list(parsed.get("questions") or []) or [objective]
    questions: list[dict[str, Any]] = []
    for index, entry in enumerate(raw_questions[:12], start=1):
        question = entry if isinstance(entry, str) else str(entry.get("question") or entry.get("text") or "")
        question = question.strip()
        if not question:
            continue
        hint = _hint_for(question, keywords)
        questions.append(
            {
                "id": f"q{index}",
                "text": question[:240],
                "kind": _question_kind(question),
                "key": index <= 3,
                "priority": 1 if index <= 3 else 2,
                "capability_hint": hint,
                "topics": keywords[:4],
                "search_strategy": _search_strategy(question, hint, keywords, angle=index - 1),
            }
        )
    if not questions:
        questions = [{"id": "q1", "text": objective, "kind": "factual", "key": True, "capability_hint": "web.discovery"}]
    resolved = mode if mode in {"quick", "standard", "deep"} else ("deep" if len(questions) >= 5 or mode == "auto" and len(keywords) > 8 else "standard")
    context_inputs = [
        {"path": str(item), "kind": "spec", "role": "research specification"}
        for item in (parsed.get("context_inputs") or [])
    ]
    return {
        "schema_version": 1,
        "objective": objective,
        "title": str(parsed.get("title") or objective)[:120],
        "scope": f"{objective} (workspace: {Path(workspace_root).name if workspace_root else 'n/a'})",
        "constraints": list(parsed.get("constraints") or []),
        "non_goals": list(parsed.get("non_goals") or []),
        "questions": questions,
        "sources": list(parsed.get("sources") or []),
        "context_inputs": context_inputs[:40],
        "spec_documents": list(parsed.get("spec_documents") or []),
        "deliverables": list(deliverables) or ["output/report.md", "output/evidence.json", "output/graph.json"],
        "success_criteria": list(parsed.get("success_criteria") or [])
        or ["quality gate passed", "every key claim backed by 2+ independent sources"],
        "mode": resolved,
        "language": language,
        "workspace": str(workspace_root or ""),
        "keywords": keywords,
        "context": context.to_dict() if context else {},
        "context_summary": context.summary() if context else "",
        "created_at": parsed.get("created_at") or now_iso(),
    }


def _hint_for(question: str, keywords: Sequence[str]) -> str:
    lowered = question.lower()
    if any(word in lowered for word in ("代码", "项目", "仓库结构", "架构", "实现", "codebase", "workspace")):
        return "code.local_structure"
    if any(word in lowered for word in ("风险", "安全", "隐私", "risk", "security", "privacy")):
        return "web.read"
    if any(word in lowered for word in ("api", "接口", "文档", "doc", "reference", "sdk", "库")):
        return "docs.library"
    if any(word in lowered for word in ("开源项目", "仓库", "repository", "github", "实现参考")):
        return "code.repository"
    return "web.discovery"


def seed_graph(graph: Any, plan: dict[str, Any]) -> dict[str, int]:
    """Create goal/question/topic/constraint nodes for a fresh plan."""
    added = {"questions": 0, "topics": 0, "constraints": 0}
    objective = str(plan.get("objective") or "research")
    if "goal" not in graph.nodes:
        graph.add_node("goal", objective, node_id="goal", body=objective, created_by="planner")
    else:
        graph.nodes["goal"].title = objective or graph.nodes["goal"].title
        graph.nodes["goal"].body = objective
    keywords = [str(item) for item in (plan.get("keywords") or [])][:8]
    facets = _facets_for(objective, plan)
    for entry in plan.get("questions") or []:
        if isinstance(entry, str):
            entry = {"id": None, "text": entry}
        question = str(entry.get("text") or entry.get("question") or "").strip()
        if not question:
            continue
        node_id = str(entry.get("id") or f"q-{slugify(question, 24)}")
        graph.add_node(
            "question",
            question[:200],
            node_id=node_id,
            body=question,
            created_by="planner",
            priority=int(entry.get("priority", 1) or 1),
            tags=[str(entry.get("capability_hint") or "web.discovery"), *keywords[:3]],
        )
        graph.add_edge("goal", node_id, "decomposes_into", strict=False)
        added["questions"] += 1
        for facet_key, facet_title in facets:
            topic_id = f"{node_id}-{facet_key}"
            graph.add_node(
                "topic",
                f"{facet_title}",
                node_id=topic_id,
                body=f"{question} → {facet_title}",
                created_by="planner",
                tags=[facet_key, *(keywords[:2])],
            )
            graph.add_edge(node_id, topic_id, "decomposes_into", strict=False)
            added["topics"] += 1
    for constraint in plan.get("constraints") or []:
        node_id = f"con-{slugify(str(constraint), 24)}"
        graph.add_node("constraint", str(constraint)[:200], node_id=node_id, created_by="planner")
        graph.add_edge("goal", node_id, "relates_to", strict=False)
        added["constraints"] += 1
    return added


def _facets_for(objective: str, plan: dict[str, Any]) -> list[tuple[str, str]]:
    lowered = objective.lower()
    facets = list(DEFAULT_FACETS[:4])
    if any(word in lowered for word in ("风险", "安全", "risk", "security")):
        facets = [item for item in DEFAULT_FACETS if item[0] != "risk"]
    if plan.get("mode") == "quick":
        return facets[:2]
    return facets[:4]


def context_evidence(context: ContextDigest, question_ids: Sequence[str]) -> list[Any]:
    """Turn a workspace scan into citable evidence records (local files)."""
    from .models import Evidence  # local import: avoids a cycle at module load

    if not context or not context.file_count:
        return []
    target = list(question_ids) or ["goal"]
    records: list[Evidence] = []
    readings = [
        (
            f"本地工作区 {context.name} 共 {context.file_count} 个文件, {context.line_count} 行代码, "
            f"语言分布 {context.summary()}",
            str(context.root / "README.md" if context.readme else context.root),
            context.readme[:900] or context.summary(),
            "structured",
        ),
    ]
    if context.skills:
        readings.append(
            (
                f"工作区已存在 Skill/Agent 规范文件: {', '.join(context.skills[:6])}",
                str(context.root / context.skills[0]),
                ", ".join(context.skills[:12]),
                "text",
            )
        )
    if context.manifests:
        readings.append(
            (
                f"依赖与构建清单为 {', '.join(str(one['name']) for one in context.manifests[:5])}, "
                "决定了可用的本地技术栈",
                str(context.root / context.manifests[0]["name"]),
                f"entrypoints: {', '.join(context.entrypoints[:6]) or 'n/a'}",
                "text",
            )
        )
    for claim, uri, quote, modality in readings:
        records.append(
            Evidence(
                claim=claim[:600],
                source_type="file",
                source_uri=uri,
                source_tier="primary",
                modality=modality,
                confidence=0.75,
                confidence_basis="direct filesystem read of the workspace under study",
                supports=list(target),
                summary=quote[:900],
                retrieved_via={"server": "filesystem", "tool": "read", "capability": "code.local_structure"},
                tags=["context", "workspace"],
            )
        )
    return records
