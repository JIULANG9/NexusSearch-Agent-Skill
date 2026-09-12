"""Turn MCP results into citable :class:`Evidence` records.

The loop controller dispatches tasks; this module is the only place that
knows how to convert a raw tool payload (search hit list, fetched page,
codegraph answer, recalled pattern) into evidence that satisfies
``schemas/evidence.schema.json``.

Design rules (AGENTS.md 2.3 / 6):

* nothing is invented - a record without a concrete source URI is dropped;
* search hits stay low-confidence until the page behind them is read;
* every record carries ``retrieved_via`` so the report can prove where it came from.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

from .models import Evidence, ResearchTask
from .util import get_logger, is_cjk_heavy, now_iso, slugify

LOGGER = get_logger("nexus.harvest")

URL_PATTERN = re.compile(r"https?://[^\s)\]}>\"'`<]+")
ENGINE_PATTERN = re.compile(r"^Engines:\s*(.+)$", re.M)
URL_KEYS = ("url", "uri", "link", "href", "display_url", "html_url", "file_path", "path")
TITLE_KEYS = ("title", "name", "heading", "label", "repo", "full_name")
SNIPPET_KEYS = ("snippet", "content", "description", "summary", "body", "text", "excerpt", "quote")

# Terms that carry no topical signal and must not count as evidence of relevance.
# Words that are topically empty but survive stopword lists.
GENERIC_FILLER = {"workflow", "workflows", "framework", "frameworks", "comparison",
                  "best", "practices", "guide", "guides", "tutorial", "tutorials"}

QUERY_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "what", "when", "where", "which",
    "how", "why", "are", "was", "were", "has", "have", "you", "your", "about", "into",
    "official", "documentation", "reference", "comparison", "example", "implementation",
    "repository", "specification", "changelog", "api", "2025", "2026", "2027",
}

TERM_PATTERN = re.compile(r"[a-z][a-z0-9+#._-]{2,}|[\u4e00-\u9fff]{2,6}")
LATIN_TERM = re.compile(r"[a-z][a-z0-9+#._-]{2,}")
CJK_SEQ = re.compile(r"[\u4e00-\u9fff]+")
CJK_GRAM_SIZES = (2, 3)


def terms_of(text: str) -> set[str]:
    """Comparable terms for a query or a hit, with Chinese runs expanded.

    ``TERM_PATTERN`` takes one greedy 2-6 character CJK match, which makes a
    Chinese query almost never overlap the page it is supposed to describe: the
    query says ``数据集`` while the title reads ``微调数据集标注``, and two disjoint
    tokens score a genuinely on-topic page as irrelevant. Expanding every CJK run
    into its full form plus 2- and 3-character windows lets the overlap land, the
    standard trick for languages without spaces.
    """
    lowered = (text or "").lower()
    terms = {token for token in LATIN_TERM.findall(lowered) if token not in QUERY_STOPWORDS}
    for run in CJK_SEQ.findall(lowered):
        terms.add(run)
        for size in CJK_GRAM_SIZES:
            if size <= len(run):
                terms.update(run[start : start + size] for start in range(len(run) - size + 1))
    return terms
ASCII_TERM = re.compile(r"[a-z][a-z0-9+#._-]{2,24}$")
CJK_RUN = re.compile(r"[\u4e00-\u9fff]")

# A hit must share at least this many meaningful query terms with its title/snippet/URL.
MIN_RELEVANCE = int(os.environ.get("NEXUS_MIN_RELEVANCE", "1"))
# How many zero-overlap hits survive per query, as explicitly weak leads. Search
# backends rank semantically related pages that share no literal term with the
# query, so dropping them all would blind the loop; keeping them unflagged is
# what produced 108 junk records in one live run. They are capped and flagged.
MAX_WEAK_LEADS = int(os.environ.get("NEXUS_MAX_WEAK_LEADS", "2"))
# Distinct query terms a fetched page must contain to keep read-level confidence.
MIN_PAGE_RELEVANCE = int(os.environ.get("NEXUS_MIN_PAGE_RELEVANCE", "2"))

# Sites that rarely carry primary research value for technical work.
NOISE_HOSTS = {
    "pinterest.com", "facebook.com", "instagram.com", "tiktok.com", "x.com",
    "twitter.com", "quora.com", "medium.com@", "linkedin.com/posts",
}
AUTO_CAPABILITIES = {
    "web.discovery",
    "web.read",
    "docs.library",
    "code.repository",
    "code.local_structure",
    "memory.long_term",
    "data.structured",
    "multimodal.image",
}
MANUAL_CAPABILITIES = {"browser.render", "ui.component", "domain.business"}


@dataclass
class Hit:
    """One candidate source extracted from a tool payload."""

    url: str
    title: str = ""
    snippet: str = ""
    relevance: int = -1  # query-term overlap; -1 = never scored
    engine: str = ""     # search backend that surfaced this hit, when knowable

    @property
    def host(self) -> str:
        return Evidence.host_of(self.url)


@dataclass
class HarvestOutcome:
    """Result of executing a single task, ready for ``LoopController.collect``."""

    task_id: str
    agent: str
    capability: str
    ok: bool
    evidence: list[Evidence] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tool_calls: int = 0
    error: str = ""
    manual: bool = False
    server: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "capability": self.capability,
            "ok": self.ok,
            "evidence": [record.id for record in self.evidence],
            "notes": self.notes,
            "tool_calls": self.tool_calls,
            "error": self.error,
            "manual": self.manual,
            "server": self.server,
        }


# ---------------------------------------------------------------------------
# payload mining
# ---------------------------------------------------------------------------
def _iter_dicts(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        yield payload
        for value in payload.values():
            yield from _iter_dicts(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from _iter_dicts(value)


SCRIPT_PATTERN = re.compile(r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>", re.S | re.I)
BOILERPLATE_LINE = re.compile(
    r"^\s*(?:\(function\s*\(|window\.|document\.|localStorage\.|try\s*\{|catch\s*\(|//|<|!\[)"
)


def strip_boilerplate(text: str) -> str:
    """Drop tag soup and inline JS that fetchers return alongside real prose.

    Without this a page whose body is 4KB of theme-switching script becomes a
    "high confidence" quotation — one live run stored MDN's localStorage snippet
    as evidence about agent frameworks.
    """
    cleaned = SCRIPT_PATTERN.sub(" ", text or "")
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if len(lines) < 3:  # single-paragraph payloads (markdown, JSON) are already clean
        return cleaned
    prose = [line for line in lines if not BOILERPLATE_LINE.match(line)]
    joined = "\n".join(prose)
    # A page that is nothing but scripts keeps nothing: callers treat the short
    # result as unreadable, which is the honest outcome.
    return joined if joined.strip() else cleaned


def _clean(text: str, limit: int = 600) -> str:
    collapsed = re.sub(r"\s+", " ", strip_boilerplate(text)).strip()
    return collapsed[:limit]


def _looks_like_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://", "file://", "/"))


def is_noise(hit: Hit) -> bool:
    if not (hit.url or "").strip():
        return True
    host = hit.host.lower()
    return any(host.endswith(noise.rstrip("@")) or noise in host for noise in NOISE_HOSTS if "@" not in noise)


def query_terms(query: str) -> set[str]:
    """Meaningful, lowercased terms a hit must overlap to look on-topic."""
    return terms_of(query)


def relaxed_query(query: str, keep: int = 2) -> str:
    """Shorten a keyword query for index-scoped engines.

    GitHub, PyPI and StackOverflow rank literal identifiers, so "langgraph autogen
    stateful workflow orchestration" starves them to a single hit while
    "langgraph autogen" returns on-topic repositories. Only used after a miss.
    """
    terms = [term for term in query_terms(query) if term not in GENERIC_FILLER]
    if len(terms) <= keep:
        return ""
    ranked = sorted(terms, key=lambda term: (-len(term), term))
    return " ".join(ranked[:keep])


def relevance(query: str, hit: Hit) -> int:
    """Count query terms present in a hit's title, snippet, or URL."""
    terms = query_terms(query)
    if not terms:
        return MIN_RELEVANCE
    haystack = terms_of(f"{hit.title} {hit.snippet} {hit.url}")
    return len(terms & haystack)


def extract_hits(
    result: Any,
    limit: int = 8,
    *,
    query: str = "",
    min_relevance: int | None = None,
) -> list[Hit]:
    """Pull structured hits first, then fall back to URLs inside free text.

    When ``query`` is given, off-topic hits are dropped: search backends with a
    narrow engine (or a poor language match) return popular-but-irrelevant
    landing pages, and storing those as "evidence" inflates counts while teaching
    the loop nothing.
    """
    hits: dict[str, Hit] = {}
    payload = getattr(result, "data", None)
    text = str(getattr(result, "text", "") or "")
    for item in _iter_dicts(payload):
        url = next((item[key] for key in URL_KEYS if _looks_like_url(item.get(key))), "")
        if not url:
            continue
        title = _clean(str(next((item[key] for key in TITLE_KEYS if isinstance(item.get(key), str)), "")), 160)
        snippet = _clean(str(next((item[key] for key in SNIPPET_KEYS if isinstance(item.get(key), str)), "")), 600)
        key = url.rstrip("/")
        if key not in hits:
            hits[key] = Hit(url=url, title=title or Evidence.host_of(url), snippet=snippet)
    if not hits and text:
        for index, url in enumerate(URL_PATTERN.findall(text)):
            key = url.rstrip("/).,")
            if key in hits:
                continue
            hits[key] = Hit(url=key, title=_title_from_text(text, index) or Evidence.host_of(key))
    keep = [hit for hit in hits.values() if not is_noise(hit)]
    if not query:
        return keep[:limit]
    for hit in keep:
        hit.relevance = relevance(query, hit)
    threshold = MIN_RELEVANCE if min_relevance is None else min_relevance
    on_topic = [hit for hit in keep if hit.relevance >= threshold]
    weak = [hit for hit in keep if hit.relevance < threshold][:MAX_WEAK_LEADS]
    dropped = len(keep) - len(on_topic) - len(weak)
    if dropped:
        LOGGER.info(
            "query %r: kept %d on-topic, %d weak, dropped %d off-topic",
            query[:60], len(on_topic), len(weak), dropped,
        )
    # Provider order already encodes relevance (SearXNG sorts by score); keep it.
    return (on_topic + weak)[:limit]


_LABELLED_TITLE = re.compile(r"^\s*(?:title|标题|name|页面)\s*[:：]\s*(?P<value>.+?)\s*$", re.IGNORECASE)


def _title_from_text(text: str, url_index: int) -> str:
    """Best-effort title for a URL found in free text (SearXNG MCP answers as text)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()][:200]
    seen = 0
    for position, line in enumerate(lines):
        if not URL_PATTERN.search(line):
            continue
        seen += 1
        if seen != url_index + 1:
            continue
        # markdown anchor text beats any other guess: [Label](https://…)
        anchor = re.search(r"\[(?P<label>[^\]]{1,160})\]\(\s*<?https?://", line)
        if anchor and anchor.group("label").strip():
            return _clean(anchor.group("label"), 160)
        # "Title: x" on the same line, then the few lines above the URL.
        inline = _LABELLED_TITLE.match(line)
        if inline:
            return _clean(inline.group("value"), 160)
        for previous in reversed(lines[:position][-4:]):
            labelled = _LABELLED_TITLE.match(previous)
            if labelled:
                return _clean(labelled.group("value"), 160)
        remainder = _clean(re.sub(URL_PATTERN, "", line).strip(" :-：*[]"), 160)
        if remainder and remainder.lower() not in {"url", "link", "href", "地址", "source"}:
            return remainder
        for previous in reversed(lines[:position][-2:]):
            if previous.lower() not in {"url", "link", "href"} and not previous.endswith((":", "：")):
                return _clean(previous, 160)
        return ""
    return ""


# ---------------------------------------------------------------------------
# evidence builders
# ---------------------------------------------------------------------------
def _lead_tags(query: str, result: Any, hit: Hit) -> list[str]:
    """Traceable tags: query, lead strength, and the engine that surfaced the hit."""
    engines = hit.engine or engines_of(result)
    tags = [f"query:{slugify(query, 24)}", "weak-lead" if hit.relevance == 0 else "lead"]
    if engines:
        tags.append(f"engine:{slugify(engines, 32)}")
    if hit.relevance > 0:
        tags.append(f"rel:{hit.relevance}")
    return tags[:6]


def evidence_from_hits(
    hits: Sequence[Hit],
    *,
    query: str,
    node_ids: Sequence[str],
    result: Any,
    confidence: float = 0.4,
    source_type: str = "url",
    modality: str = "text",
) -> list[Evidence]:
    """Low-confidence leads: the source was found, not yet read."""
    records: list[Evidence] = []
    for hit in hits:
        weak = hit.relevance == 0
        claim = f"检索「{query[:70]}」命中来源 {hit.host}: {hit.title[:120]}"
        if hit.snippet:
            claim = f"{claim} — {hit.snippet[:180]}"
        if weak:
            claim = f"[弱相关，标题/摘要未命中查询词] {claim}"
        records.append(
            Evidence(
                claim=claim[:600],
                source_type=source_type,
                source_uri=hit.url,
                title=hit.title[:200],
                domain=hit.host,
                summary=hit.snippet[:900] or f"search hit for {query}",
                quote=hit.snippet[:1100],
                modality=modality,
                source_tier=_tier_for(hit.url),
                confidence=min(confidence, 0.25) if weak else confidence,
                quality_flags=["unrelated"] if weak else [],
                confidence_basis=(
                    "ranked by the search engine but shares no query term with the "
                    "title or snippet; treat as an unverified lead"
                    if weak
                    else "search result surfaced by local MCP, page body not yet read"
                ),
                supports=list(node_ids) or ["goal"],
                retrieved_via=_via(result),
                tags=_lead_tags(query, result, hit),
            )
        )
    return records


def evidence_from_page(
    result: Any,
    *,
    url: str,
    query: str,
    node_ids: Sequence[str],
    confidence: float = 0.62,
    source_type: str = "url",
) -> Evidence:
    """A read page: the quote is real text, so confidence is higher."""
    text = _clean(str(getattr(result, "text", "") or ""), 4000)
    host = Evidence.host_of(url)
    first_line = next((line.strip() for line in text.splitlines() if len(line.strip()) > 12), host)
    claim = f"{host} 就「{query[:60] if query else first_line[:60]}」指出: {first_line[:220]}"
    return Evidence(
        claim=claim[:600],
        source_type=source_type,
        source_uri=url,
        title=first_line[:180],
        domain=host,
        summary=text[:900],
        quote=text[:1150],
        modality="text" if source_type == "url" else "code",
        source_tier=_tier_for(url),
        confidence=confidence,
        confidence_basis=f"full text retrieved via {getattr(result, 'server', 'mcp')}/{getattr(result, 'tool', 'call')}",
        supports=list(node_ids) or ["goal"],
        retrieved_via=_via(result),
        quality_flags=["truncated"] if len(text) >= 4000 else [],
        tags=[f"query:{slugify(query, 24)}", "read"],
    )


def evidence_from_text(
    text: str,
    *,
    capability: str,
    result: Any,
    uri: str,
    objective: str,
    node_ids: Sequence[str],
    source_type: str = "file",
    confidence: float = 0.7,
    max_chars: int = 2000,
) -> Evidence:
    """Local reads (workspace files, recalled patterns) are primary evidence."""
    body = _clean(text, max_chars)
    head = next((line.strip() for line in body.splitlines() if len(line.strip()) > 8), objective[:120])
    return Evidence(
        claim=f"本地资料 {source_label(uri)} 支撑「{objective[:80]}」: {head[:200]}"[:600],
        source_type=source_type,
        source_uri=uri,
        title=head[:180],
        domain=Evidence.host_of(uri) if uri.startswith("http") else "local",
        summary=body[:900],
        quote=body[:1150],
        modality="code" if source_type in {"code", "file", "repository"} else "text",
        source_tier="primary",
        confidence=confidence,
        confidence_basis=f"read locally through capability {capability}",
        supports=list(node_ids) or ["goal"],
        retrieved_via=_via(result),
        tags=[f"capability:{capability}", "local"],
    )


def engines_of(result: Any) -> str:
    """Which SearXNG engine(s) actually produced a payload.

    The MCP server drops the per-result ``Engines`` field, so provenance has to be
    recovered from the response text; without it an engine that answers with
    off-topic landing pages is indistinguishable from one that answers well.
    """
    text = str(getattr(result, "text", "") or "")
    found = [name.strip() for value in ENGINE_PATTERN.findall(text) for name in value.split(",")]
    return ",".join(sorted(set(found))) if found else ""


def _via(result: Any) -> dict[str, Any]:
    return {
        "server": str(getattr(result, "server", "") or "local"),
        "tool": str(getattr(result, "tool", "") or ""),
        "capability": str(getattr(result, "capability", "") or ""),
        "duration_ms": int(getattr(result, "duration_ms", 0) or 0),
        "retries": int(getattr(result, "retries", 0) or 0),
    }


def _tier_for(uri: str) -> str:
    """Crude but honest tiering: vendors/specs/repos are primary, rest secondary."""
    lowered = uri.lower()
    primary_markers = (
        "github.com", "gitlab.com", "readthedocs.io", "docs.", "/docs", "specification",
        "arxiv.org", "wikipedia.org" , "/api", "reference", "developer.", ".dev/docs", "/blog/engineering",
    )
    tertiary_markers = ("aggregator", "ai-summary", "content-farm", "seo")
    if any(marker in lowered for marker in tertiary_markers):
        return "tertiary"
    if any(marker in lowered for marker in primary_markers):
        return "primary"
    return "secondary"


def source_label(uri: str) -> str:
    """Short, human-readable source label for a claim sentence."""
    cleaned = (uri or "local").strip()
    if cleaned.startswith("file://"):
        cleaned = cleaned[len("file://") :]
    if "://" in cleaned:
        host = Evidence.host_of(cleaned)
        if host:
            return host
    return cleaned.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "local"


# ---------------------------------------------------------------------------
# task execution
# ---------------------------------------------------------------------------
def _node_ids(task: ResearchTask) -> list[str]:
    node_id = str((task.params or {}).get("node_id") or task.question_ref or "")
    return [node_id] if node_id else []


def _queries(task: ResearchTask) -> list[str]:
    params = task.params or {}
    queries = [str(item) for item in (params.get("queries") or []) if str(item).strip()]
    if not queries:
        for key in ("query", "search_query", "library"):
            if params.get(key):
                queries = [str(params[key])]
                break
    if not queries:
        queries = [task.objective.strip()[:120] or "research"]
    return queries[:3]


def execute_task(
    router: Any,
    task: ResearchTask,
    *,
    max_reads: int = 3,
    hit_limit: int = 8,
    memory_store: Any | None = None,
    known_urls: Iterable[str] = (),
) -> HarvestOutcome:
    """Run one task through the MCP router and return harvestable evidence."""
    outcome = HarvestOutcome(
        task_id=task.id,
        agent=task.agent,
        capability=task.capability,
        ok=False,
    )
    if task.capability in MANUAL_CAPABILITIES:
        outcome.manual = True
        outcome.ok = True
        outcome.notes.append(
            f"capability {task.capability} needs a host agent with that MCP client; recorded as a manual task"
        )
        return outcome
    if task.capability not in AUTO_CAPABILITIES:
        outcome.error = f"no automatic harvester for capability {task.capability}"
        outcome.notes.append(outcome.error)
        return outcome
    node_ids = _node_ids(task)
    if task.capability == "memory.long_term":
        _collect_memory(router, task, outcome, node_ids, memory_store)
        return outcome
    _collect_via_mcp(
        router, task, outcome, node_ids,
        max_reads=max_reads, hit_limit=hit_limit, known_urls=set(known_urls),
    )
    return outcome


def _collect_memory(router: Any, task: ResearchTask, outcome: HarvestOutcome, node_ids: Sequence[str], store: Any) -> None:
    """Local-first: recall patterns from the pattern file before touching MCP."""
    query = " ".join(_queries(task))
    if store is not None:
        for pattern in store.recall(query, limit=3):
            body = f"{pattern.statement}\ncontext: {', '.join(pattern.context)}\ntags: {', '.join(pattern.tags)}"
            outcome.evidence.append(
                Evidence(
                    claim=f"历史研究模式「{pattern.title[:80]}」适用于当前问题: {pattern.statement[:200]}"[:600],
                    source_type="file",
                    source_uri=str(getattr(store, "path", "memory/research-patterns.json")),
                    domain="local",
                    title=pattern.title[:180],
                    summary=body[:900],
                    quote=body[:1150],
                    modality="structured",
                    source_tier="primary",
                    confidence=min(0.85, 0.45 + 0.5 * float(pattern.success_rate)),
                    confidence_basis=f"self-evolution memory: used {pattern.uses}x, wins {pattern.wins}",
                    supports=list(node_ids) or ["goal"],
                    retrieved_via={"server": "memory", "tool": "recall", "capability": "memory.long_term"},
                    tags=["memory", pattern.kind],
                )
            )
        if outcome.evidence:
            outcome.ok = True
            outcome.server = "memory"
            outcome.notes.append(f"recalled {len(outcome.evidence)} patterns from long-term memory")
            return
    result = router.call("memory.long_term", query=query)
    outcome.tool_calls += 1
    if result.ok:
        outcome.evidence.append(
            evidence_from_text(
                result.text,
                capability="memory.long_term",
                result=result,
                uri="memory/research-patterns.json",
                objective=task.objective,
                node_ids=node_ids,
                confidence=0.6,
            )
        )
    outcome.ok = result.ok
    outcome.error = result.error
    outcome.server = result.server


def search_language(query: str) -> str:
    """Language code to hand the metasearch engine for this query.

    A SearXNG instance carries a ``general.default_lang`` (``zh-CN`` here) and sends
    it as the UI/locale hint, which the general engines read as a region signal. For
    a niche technical query that is destructive: measured on this host,
    ``LTX-Video LoRA dataset caption`` with no language answered with YouTube and
    Korean camping portals, while ``language=en-US`` answered with the actual
    ``Lightricks/LTX-Video`` repository and LTX LoRA training guides. Pick the locale
    from the query instead of the instance default; keep Chinese queries on Chinese
    engines, where they are strongest.
    """
    return "zh-CN" if is_cjk_heavy(query) else "en-US"


# SearXNG's general engines are not interchangeable across scripts. Measured on this
# host with ``视频微调 数据集 标注 规范``: the Chinese-capable group answered with
# on-topic Tencent Cloud / CSDN / Baidu guides, while ``bing,google`` answered with
# Microsoft support pages and a Malaysian forum. The reverse holds for English
# technical queries, where the same Chinese engines thin out. So the group choice is
# a function of the query's script, not just of its position in the plan.
CJK_ENGINES_DEFAULT = ("360search", "sogou", "baidu", "quark", "ecosia")


def cjk_engines(router: Any) -> tuple[str, ...]:
    """Engine names that carry strong Chinese-index coverage (config-overridable)."""
    names: tuple[str, ...] = ()
    try:
        raw = router.server_option("searxng", "cjk_engines", [])
    except Exception:  # noqa: BLE001 - routers without the option simply use the default
        raw = []
    if isinstance(raw, str):
        raw = [raw]
    for entry in raw or []:
        text = str(entry).strip().lower()
        if text:
            names += (text,)
    return names or CJK_ENGINES_DEFAULT


def _is_cjk_group(group: str, names: Sequence[str]) -> bool:
    return any(engine in group.lower() for engine in names)


def ordered_groups(
    groups: Sequence[str],
    query: str,
    angle: int,
    router: Any = None,
) -> list[str]:
    """Engine groups to try for one query, best-first for that query's script.

    Groups strong in the query's language come first; the rest follow, each
    partition rotated by ``angle`` so sibling queries still spread their coverage
    instead of all hammering the same engine.
    """
    if not groups:
        return []
    names = cjk_engines(router) if router is not None else CJK_ENGINES_DEFAULT
    wants_cjk = is_cjk_heavy(query)
    strong = [group for group in groups if _is_cjk_group(group, names) is wants_cjk]
    weak = [group for group in groups if group not in strong]

    def rotate(pool: list[str]) -> list[str]:
        if not pool:
            return []
        shift = angle % len(pool)
        return pool[shift:] + pool[:shift]

    return rotate(strong) + rotate(weak)


def engine_groups(router: Any, capability: str) -> list[str]:
    """Engine groups configured for the first provider of a web capability.

    SearXNG fans out to every engine and merges whatever answers before the
    client timeout, so one fat multi-engine call is won by the single fastest
    engine (in practice Microsoft Learn) and the run ends up citing one domain.
    Asking one narrow group per query spreads coverage instead.
    """
    try:
        providers = router.providers_for(capability)
        server = str(providers[0].get("server") or "") if providers else ""
        raw = router.server_option(server, "engine_groups", []) if server else []
    except Exception:  # noqa: BLE001 - routers without a spec simply fan out nowhere
        return []
    if isinstance(raw, str):
        raw = [raw]
    groups: list[str] = []
    for item in raw or []:
        text = ", ".join(str(entry) for entry in item) if isinstance(item, (list, tuple)) else str(item)
        if text.strip():
            groups.append(text.strip())
    return groups


def _collect_via_mcp(
    router: Any,
    task: ResearchTask,
    outcome: HarvestOutcome,
    node_ids: Sequence[str],
    *,
    max_reads: int,
    hit_limit: int,
    known_urls: set[str] | None = None,
) -> None:
    params = dict(task.params or {})
    capability = task.capability
    queries = _queries(task)
    known: set[str] = set(known_urls or ())

    relaxed = {query: candidate for query, candidate in ((q, relaxed_query(q)) for q in queries)}
    if capability == "web.discovery":
        groups = engine_groups(router, capability)
        for query in queries:
            angle = queries.index(query)
            extra: dict[str, Any] = {"language": search_language(query)}
            queue = ordered_groups(groups, query, angle, router)[:2] or [None]
            tried: list[str] = []
            result = None
            hits: list[Hit] = []
            for attempt, engine_group in enumerate(queue):
                call_args = dict(extra)
                if engine_group:
                    call_args["engines"] = engine_group
                    tried.append(engine_group)
                result = router.call("web.discovery", query=query, **call_args)
                outcome.tool_calls += 1
                outcome.server = result.server or outcome.server
                if not result.ok:
                    outcome.error = result.error
                    outcome.notes.append(f"discovery failed for '{query[:40]}': {result.error_class}")
                    continue
                hits = extract_hits(result, limit=hit_limit, query=query)
                if [hit for hit in hits if hit.relevance > 0]:
                    break
                if attempt + 1 < len(queue):
                    outcome.notes.append(
                        f"engine group '{engine_group}' gave nothing relevant for '{query[:28]}', rotating"
                    )
            if result is None or not result.ok:
                continue
            unused = [group for group in ordered_groups(groups, query, angle, router) if group not in tried]
            if not [hit for hit in hits if hit.relevance > 0]:
                # One relaxation attempt before giving up on the angle.
                alternate = relaxed.get(query) or ""
                if alternate and alternate not in queries:
                    outcome.notes.append(f"relaxed '{query[:30]}' -> '{alternate}'")
                    relax_engines = dict(extra)
                    relax_engines["language"] = search_language(alternate)
                    if unused:
                        relax_engines["engines"] = unused[0]
                    result = router.call("web.discovery", query=alternate, **relax_engines)
                    outcome.tool_calls += 1
                    query = alternate
                    hits = extract_hits(result, limit=hit_limit, query=alternate) if result.ok else []
            if not hits:
                outcome.notes.append(f"no usable URLs in results for '{query[:40]}'")
                continue
            fresh = [hit for hit in hits if hit.url not in known]
            if len(fresh) != len(hits):
                outcome.notes.append(f"skipped {len(hits) - len(fresh)} already-recorded sources")
            if not fresh:
                continue
            outcome.evidence.extend(
                evidence_from_hits(fresh[: max_reads * 2], query=query, node_ids=node_ids, result=result)
            )
            # Only on-topic hits get a page read: reading a weak lead turns an
            # engine misfire into a high-confidence record, which is how one run
            # accumulated 108 irrelevant sources.
            for hit in [item for item in fresh[:max_reads] if item.relevance > 0]:
                known.add(hit.url)
                page = router.call("web.read", url=hit.url)
                outcome.tool_calls += 1
                outcome.server = outcome.server or page.server
                if page.ok and len(_clean(page.text, 4000)) >= 200:
                    record = evidence_from_page(page, url=hit.url, query=query, node_ids=node_ids)
                    body = relevance(query, Hit(url=hit.url, title=record.title, snippet=str(page.text)[:4000]))
                    if body < MIN_PAGE_RELEVANCE:
                        # Reading a page must not launder an off-topic hit into
                        # read-level confidence.
                        record.confidence = min(record.confidence, 0.3)
                        record.quality_flags = list(dict.fromkeys([*record.quality_flags, "unrelated"]))
                        outcome.notes.append(f"page body missed the query ({body} terms): {hit.host}")
                    outcome.evidence.append(record)
                else:
                    outcome.notes.append(f"page unreadable, kept as lead only: {hit.host}")
        outcome.ok = bool(outcome.evidence)
        return

    if capability == "web.read":
        urls = [str(item) for item in ([params.get("url")] if params.get("url") else []) if item]
        urls += [str(item) for item in (params.get("urls") or []) if item]
        if not urls:
            urls = [hit.url for query in queries for hit in extract_hits(router.call("web.discovery", query=query), limit=2)]
            outcome.tool_calls += 1
        for url in [item for item in urls[:max_reads] if item not in known]:
            known.add(url)
            result = router.call("web.read", url=url)
            outcome.tool_calls += 1
            outcome.server = result.server or outcome.server
            if result.ok:
                outcome.evidence.append(
                    evidence_from_page(result, url=url, query=str(params.get("query") or task.objective), node_ids=node_ids)
                )
            else:
                outcome.error = result.error
        outcome.ok = bool(outcome.evidence)
        return

    # Everything else: one focused call, whole answer becomes one evidence record.
    arguments: dict[str, Any] = {"query": queries[0]}
    if capability == "docs.library":
        arguments.update({"library": params.get("library") or queries[0], "topic": params.get("topic") or ""})
        source_type, uri = "docs", params.get("url") or f"context7://{slugify(queries[0], 32)}"
    elif capability == "code.repository":
        arguments.update({"q": queries[0], "owner": params.get("owner") or "", "repo": params.get("repo") or ""})
        source_type, uri = "repository", params.get("url") or f"github://search/{slugify(queries[0], 32)}"
    elif capability == "code.local_structure":
        arguments.update({"path": params.get("path") or ".", "projectPath": params.get("path") or "."})
        source_type, uri = "file", str(params.get("path") or ".")
    elif capability == "data.structured":
        arguments.update({"sql": params.get("sql") or "", "key": params.get("key") or ""})
        source_type, uri = "database", params.get("target") or "local-database"
    elif capability == "multimodal.image":
        arguments.update({"categories": "images"})
        result = router.call(capability, **arguments)
        outcome.tool_calls += 1
        if result.ok:
            outcome.evidence.extend(
                evidence_from_hits(
                    extract_hits(result, limit=max_reads),
                    query=queries[0],
                    node_ids=node_ids,
                    result=result,
                    confidence=0.45,
                    modality="image",
                )
            )
        outcome.ok = bool(outcome.evidence)
        outcome.error = "" if outcome.ok else result.error
        outcome.server = result.server
        return
    else:  # pragma: no cover - AUTO_CAPABILITIES is exhaustive
        source_type, uri, arguments = "api", params.get("uri") or "local", {"query": queries[0]}

    result = router.call(capability, **arguments)
    outcome.tool_calls += 1
    outcome.server = result.server
    outcome.ok = result.ok
    outcome.error = result.error if not result.ok else ""
    if result.ok:
        body = _clean(result.text or str(result.data or ""), 3000)
        if len(body) < 80:
            outcome.notes.append(f"{capability} returned too little text to cite ({len(body)} chars)")
            outcome.ok = False
            return
        outcome.evidence.append(
            evidence_from_text(
                body,
                capability=capability,
                result=result,
                uri=uri,
                objective=task.objective,
                node_ids=node_ids,
                source_type=source_type,
                confidence=0.6 if capability != "code.local_structure" else 0.75,
            )
        )


def summarise_result(result: Any, limit: int = 240) -> str:
    """Compact, timestamped description of an MCP answer for the audit log."""
    return f"[{now_iso()}] {getattr(result, 'capability', '')} <- {getattr(result, 'server', '')}:{_clean(str(getattr(result, 'text', '')), limit)}"
