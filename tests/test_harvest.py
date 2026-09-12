"""Harvesting: raw MCP payloads -> deduplicated, citable evidence."""

from __future__ import annotations

import json
from pathlib import Path

from runtime import harvest
from runtime.evidence_store import EvidenceStore
from runtime.graph_engine import ResearchGraph
from runtime.mcp_router import McpResult, McpRouter, StubSession
from runtime.models import ResearchTask
from runtime.workspace import Workspace

SEARCH_TEXT = """Title: MCP specification
Description: The model context protocol standardises tool abstraction.
URL: https://modelcontextprotocol.io/specification
Relevance Score: 1.000

Title: Deep research loops
Description: Iterative gap filling.
URL: https://arxiv.org/abs/2411.3957
Relevance Score: 0.800
"""


def result(capability: str, text: str = "", data: object = None, server: str = "searxng", tool: str = "search") -> McpResult:
    return McpResult(capability=capability, server=server, tool=tool, ok=True, text=text, data=data)


def test_extract_hits_from_structured_and_text_payloads() -> None:
    structured = result("web.discovery", "", {"results": [{"title": "A", "url": "https://a.dev/x", "snippet": "s"}]})
    assert [hit.url for hit in harvest.extract_hits(structured)] == ["https://a.dev/x"]
    textual = result("web.discovery", SEARCH_TEXT)
    hits = harvest.extract_hits(textual)
    assert {hit.url for hit in hits} == {"https://modelcontextprotocol.io/specification", "https://arxiv.org/abs/2411.3957"}
    assert hits[0].title == "MCP specification"


def test_extract_hits_respects_limit_and_noise() -> None:
    many = result("web.discovery", "", {"results": [{"title": f"t{i}", "url": f"https://a.dev/{i}"} for i in range(20)]})
    assert len(harvest.extract_hits(many, limit=5)) == 5
    noise = harvest.Hit(url="", title="javascript:void(0)", snippet="cookie consent")
    assert harvest.is_noise(noise) is True
    assert harvest.is_noise(harvest.Hit(url="https://a.dev/x", title="real", snippet="s")) is False


def test_evidence_from_hits_dedupes_and_provenances() -> None:
    page = result("web.discovery", SEARCH_TEXT)
    records = harvest.evidence_from_hits(harvest.extract_hits(page), query="mcp", node_ids=["q1"], result=page)
    assert records
    assert all(record.source_type == "url" for record in records)
    assert all(record.supports == ["q1"] for record in records)
    assert all(record.retrieved_via["server"] == "searxng" for record in records)
    again = harvest.evidence_from_hits(harvest.extract_hits(page), query="mcp", node_ids=["q1"], result=page)
    assert len(again) == len(records)


def test_evidence_from_page_marks_truncation_and_tier() -> None:
    long_page = result("web.read", "x" * 5000, server="fetch", tool="markdown")
    record = harvest.evidence_from_page(long_page, url="https://arxiv.org/abs/1", query="q", node_ids=["t1"])
    assert record.source_tier == "primary"
    assert "truncated" in record.quality_flags
    assert record.modality == "text"


def test_evidence_from_text_handles_local_files() -> None:
    record = harvest.evidence_from_text(
        "src/app.py defines the HTTP layer",
        capability="code.local_structure",
        result=result("code.local_structure", "body", server="filesystem", tool="read_text_file"),
        uri="file:///repo/src/app.py",
        objective="理解项目结构",
        node_ids=["q1"],
    )
    assert record.source_type == "file"
    assert record.source_tier == "primary"
    assert record.supports == ["q1"]


def test_source_label_is_readable() -> None:
    assert harvest.source_label("https://modelcontextprotocol.io/specification") == "modelcontextprotocol.io"
    assert harvest.source_label("") == "local"
    assert harvest.source_label("file:///repo/src/app.py")


def test_markdown_links_are_extracted() -> None:
    result_ = McpResult(
        capability="web.discovery", server="searxng", tool="search", ok=True,
        text="see [LangGraph docs](https://langchain-ai.github.io/langgraph/) for details",
    )
    hits = harvest.extract_hits(result_)
    assert [hit.url for hit in hits] == ["https://langchain-ai.github.io/langgraph"]  # trailing slash trimmed
    assert hits[0].title == "LangGraph docs"


# ------------------------------------------------------------------ execution
class RecordingRouter:
    """Minimal router double: answers capabilities from a canned table."""

    def __init__(self, table: dict[str, list[McpResult]]) -> None:
        self.table = table
        self.calls: list[tuple[str, dict]] = []

    def call(self, capability: str, **arguments) -> McpResult:
        self.calls.append((capability, arguments))
        queue = self.table.get(capability) or [McpResult(capability, "", "", ok=False, error="no provider")]
        return queue[min(len(queue) - 1, sum(1 for item, _ in self.calls if item == capability) - 1)]


def _task(capability: str, **params) -> ResearchTask:
    return ResearchTask(agent="researcher", objective="对比方案", capability=capability, params=params)


def test_execute_task_discovery_then_reads() -> None:
    discovery = result("web.discovery", SEARCH_TEXT)
    page = result("web.read", "正文内容。" * 100, server="fetch", tool="fetch_markdown")
    router = RecordingRouter({"web.discovery": [discovery], "web.read": [page]})
    outcome = harvest.execute_task(router, _task("web.discovery", queries=["mcp spec"]), max_reads=1)
    assert outcome.ok and outcome.evidence
    assert outcome.tool_calls >= 1
    assert ("web.read", {"url": "https://modelcontextprotocol.io/specification"}) in router.calls or any(
        name == "web.read" for name, _ in router.calls
    )
    urls = {record.source_uri for record in outcome.evidence}
    assert any(url.startswith("https://") for url in urls)


def test_execute_task_marks_manual_capabilities() -> None:
    router = RecordingRouter({})
    capability = sorted(harvest.MANUAL_CAPABILITIES)[0]
    outcome = harvest.execute_task(router, _task(capability, queries=["x"]))
    assert outcome.manual is True and outcome.evidence == [] and router.calls == []


def test_execute_task_surfaces_failure_without_raising() -> None:
    router = RecordingRouter({"web.discovery": [McpResult("web.discovery", "", "", ok=False, error="all providers exhausted")]})
    outcome = harvest.execute_task(router, _task("web.discovery", queries=["mcp"]))
    assert outcome.ok is False or outcome.evidence == []
    assert outcome.notes or outcome.error


def test_execute_task_collects_memory_patterns(tmp_path: Path) -> None:
    from runtime.memory_store import MemoryStore

    store = MemoryStore.open(tmp_path / "patterns.json")
    store.remember("query", "官方文档优先", "把 official documentation 放在查询词尾部", source_run="unit")
    outcome = harvest.execute_task(
        RecordingRouter({}), _task("memory.long_term", queries=["官方文档"]), memory_store=store
    )
    assert outcome.ok and outcome.evidence
    assert outcome.evidence[0].source_type == "file"


def test_offline_execute_task_is_safe(workspace: Workspace) -> None:
    router = McpRouter.load(offline=True, cache_dir=workspace.root / "state")
    graph = ResearchGraph.create(workspace.path("graph"), "离线可跑通")
    graph.add_node("question", "本地能力", node_id="q1", parent="goal")
    outcome = harvest.execute_task(router, _task("web.discovery", queries=["mcp"], nodes=["q1"]), max_reads=0)
    assert isinstance(outcome.evidence, list)
    assert outcome.to_dict()["task_id"] == outcome.task_id


def test_summarise_result_shortens() -> None:
    text = harvest.summarise_result(result("web.read", "y" * 1000), limit=40)
    assert text.startswith("[") and "web.read" in text
    assert len(text) < 200
    assert json.dumps({"ok": True})


# ---------------------------------------------------------------------------
# relevance gating: local metasearch engines return popular-but-off-topic pages
# ---------------------------------------------------------------------------
JUNK_TEXT = """Title: Internationalization - JavaScript | MDN
Description: Any features of the language that are directly relevant to internationalization.
URL: https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Internationalization
Relevance Score: 1.000

Title: Writing style guide
Description: This page provides MDN's guidelines for writing style.
URL: https://developer.mozilla.org/en-US/docs/MDN/Writing_guidelines
Relevance Score: 0.900

Title: LangGraph overview
Description: Build resilient agents with LangGraph and MCP tools.
URL: https://langchain-ai.github.io/langgraph/
Relevance Score: 0.500
"""

JUNK_QUERY = "langgraph autogen mcp official documentation"


def test_query_terms_drops_stopwords_and_keeps_technical_tokens() -> None:
    assert harvest.query_terms(JUNK_QUERY) == {"langgraph", "autogen", "mcp"}
    assert harvest.query_terms("对比 选型 LangGraph official documentation") == {"对比", "选型", "langgraph"}
    assert harvest.query_terms("") == set()


def test_extract_hits_drops_off_topic_and_caps_weak_leads() -> None:
    payload = result("web.discovery", JUNK_TEXT)
    unscored = harvest.extract_hits(payload, limit=10)
    assert len(unscored) == 3, "no query means no filtering"
    kept = harvest.extract_hits(payload, limit=10, query=JUNK_QUERY)
    # one on-topic hit, then the capped weak leads, in provider-rank order
    assert [hit.relevance for hit in kept] == [1, 0, 0]
    assert kept[0].url == "https://langchain-ai.github.io/langgraph"
    # a query that matches nothing still surfaces the top-ranked hits, capped and scored 0
    weak = harvest.extract_hits(payload, limit=10, query="kubernetes helm chart")
    assert [hit.relevance for hit in weak] == [0] * harvest.MAX_WEAK_LEADS


def test_weak_leads_are_flagged_and_low_confidence() -> None:
    payload = result("web.discovery", JUNK_TEXT)
    records = harvest.evidence_from_hits(
        harvest.extract_hits(payload, limit=10, query="kubernetes helm chart"),
        query="kubernetes helm chart",
        node_ids=["q1"],
        result=payload,
    )
    assert records and all(record.quality_flags == ["unrelated"] for record in records)
    assert all(record.confidence <= 0.25 for record in records)
    assert all("弱相关" in record.claim for record in records)
    mixed_hits = harvest.extract_hits(payload, limit=10, query=JUNK_QUERY)
    strong = harvest.evidence_from_hits(
        [hit for hit in mixed_hits if hit.relevance > 0],
        query=JUNK_QUERY,
        node_ids=["q1"],
        result=payload,
    )
    assert len(strong) == 1 and not strong[0].quality_flags and strong[0].confidence == 0.4


def test_relevance_scores_token_overlap() -> None:
    hit = harvest.Hit(url="https://a.dev/langgraph", title="State machines", snippet="")
    assert harvest.relevance("langgraph autogen", hit) == 1
    assert harvest.relevance("postgres indexing", hit) == 0


# ---------------------------------------------------------------------------
# page-level quality: boilerplate removal + judging the body, not the title
# ---------------------------------------------------------------------------
MDN_BOILERPLATE = """MDN GitHub repositories - MDN Web Docs

(function () { try { var stored = localStorage.getItem('theme'); } catch (e) { console.warn('x'); } })();
try { if (localStorage.getItem('theme')) { document.documentElement.dataset.theme = 'light'; } } catch (e) {}
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {});
"""

LANGGRAPH_PAGE = """LangGraph overview
LangGraph is a framework for building resilient long-running stateful agents.
It provides streaming, checkpointing, and human-in-the-loop primitives.
"""


def test_strip_boilerplate_removes_script_noise_but_keeps_prose() -> None:
    cleaned = harvest.strip_boilerplate(MDN_BOILERPLATE)
    assert "localStorage" not in cleaned
    assert "MDN GitHub repositories" in cleaned
    assert "streaming" in harvest.strip_boilerplate(LANGGRAPH_PAGE)


def test_page_read_drops_confidence_when_body_misses_the_query() -> None:
    class PageRouter:
        def call(self, capability, **kwargs):  # noqa: ANN001, D102
            if capability == "web.read":
                return result("web.read", MDN_BOILERPLATE, server="fetch", tool="markdown")
            return result(
                "web.discovery",
                "Title: MDN GitHub repositories\nURL: https://developer.mozilla.org/en-US/docs/x\n",
            )

    outcome = harvest.execute_task(
        PageRouter(), _task("web.discovery", queries=["langgraph autogen stateful"], nodes=["q1"]), max_reads=1
    )
    pages = [item for item in outcome.evidence if item.source_type == "url" and "localStorage" not in item.quote]
    assert pages, outcome.evidence
    assert all("unrelated" in item.quality_flags for item in pages)
    assert all(item.confidence <= 0.3 for item in pages)


def test_page_read_keeps_confidence_for_on_topic_body() -> None:
    class PageRouter:
        def call(self, capability, **kwargs):  # noqa: ANN001, D102
            if capability == "web.read":
                return result("web.read", LANGGRAPH_PAGE * 6, server="fetch", tool="markdown")
            return result(
                "web.discovery",
                "Title: LangGraph overview\nURL: https://langchain-ai.github.io/langgraph/\n",
            )

    outcome = harvest.execute_task(
        PageRouter(), _task("web.discovery", queries=["langgraph stateful streaming"], nodes=["q1"]), max_reads=1
    )
    strong = [item for item in outcome.evidence if "unrelated" not in item.quality_flags]
    assert len(strong) == len(outcome.evidence), "on-topic leads and pages must survive unflagged"
    assert max(item.confidence for item in strong) >= 0.5


def test_engine_groups_come_from_the_server_search_block() -> None:
    class FakeRouter:
        def providers_for(self, capability):  # noqa: ANN001, D102
            return [{"server": "searxng"}]

        def server_option(self, server, key, default=None):  # noqa: ANN001, D102
            return [["github", "codeberg"], "hackernews", ""] if key == "engine_groups" else default

    assert harvest.engine_groups(FakeRouter(), "web.discovery") == ["github, codeberg", "hackernews"]
    assert harvest.engine_groups(object(), "web.discovery") == []


def test_search_language_follows_the_query_not_the_instance_default() -> None:
    """A zh-CN instance default turns a niche English query into geo-junk."""
    assert harvest.search_language("LTX-Video LoRA dataset caption length frames") == "en-US"
    assert harvest.search_language("视频微调数据集如何标注穿模负样本") == "zh-CN"
    assert harvest.search_language("") == "en-US"


def test_web_discovery_providers_pass_an_explicit_language() -> None:
    from runtime.util import load_config

    entry = load_config("mcp-map")["capabilities"]["web.discovery"]
    query_providers = [
        provider
        for provider in entry["providers"]
        if provider.get("server") in {"searxng", "fetch"}
        and isinstance(provider.get("args"), dict)
        and "query" in str(provider.get("args"))
    ]
    assert len(query_providers) >= 2, query_providers
    for provider in query_providers:
        assert "language" in str(provider["args"]), f"{provider} still relies on the instance default locale"


GROUPS = ["bing,google", "github,stackoverflow", "pypi,npm,huggingface", "semantic scholar,wikipedia", "360search,ecosia"]


def test_cjk_queries_start_on_the_chinese_capable_group() -> None:
    """bing/google answer a Chinese query with generic support pages."""
    order = harvest.ordered_groups(GROUPS, "视频微调 数据集 标注 规范", angle=0)
    assert order[0] == "360search,ecosia"
    assert set(order) == set(GROUPS)
    assert order.index("360search,ecosia") < order.index("bing,google")


def test_ascii_queries_do_not_open_on_the_chinese_group() -> None:
    order = harvest.ordered_groups(GROUPS, "LTX-2 LoRA dataset caption requirements", angle=0)
    assert order[0] != "360search,ecosia"
    assert order[-1] == "360search,ecosia"


def test_group_rotation_spreads_coverage_within_each_partition() -> None:
    """Sibling queries must not all hammer the same engine."""
    first = harvest.ordered_groups(GROUPS, "video dataset caption", angle=0)
    second = harvest.ordered_groups(GROUPS, "video dataset caption", angle=1)
    assert first[0] != second[0]
    assert first[-1] == second[-1] == "360search,ecosia"
    assert harvest.ordered_groups([], "any query", angle=3) == []
    assert harvest.ordered_groups(["bing"], "视频标注", angle=7) == ["bing"]


def test_cjk_engine_list_is_config_overridable() -> None:
    class FakeRouter:
        def server_option(self, server, key, default=None):  # noqa: ANN001, D102
            assert (server, key) == ("searxng", "cjk_engines")
            return default if key not in {"cjk_engines"} else ["Baidu", " sogou ", ""]

    assert harvest.cjk_engines(FakeRouter()) == ("baidu", "sogou")
    assert harvest.CJK_ENGINES_DEFAULT[0] in harvest.cjk_engines(object())


def test_discovery_rotates_engine_groups_until_one_is_relevant() -> None:
    """A group that answers with junk is replaced, not cited."""
    junk = result("web.discovery", "### Results\n\n1. [Microsoft Support](https://support.microsoft.com/zh-cn)\n   创建影片\n")
    good = result("web.discovery", "### Results\n\n1. [微调数据集标注：众包与自动化](https://cloud.tencent.com/developer/article/2589109)\n   大模型微调数据集标注规范\n")

    class GroupRouter(RecordingRouter):
        def providers_for(self, capability):  # noqa: ANN001, D102
            return [{"server": "searxng"}]

        def server_option(self, server, key, default=None):  # noqa: ANN001, D102
            return GROUPS if key == "engine_groups" else default

    router = GroupRouter({"web.discovery": [junk, good]})
    outcome = harvest.execute_task(router, _task("web.discovery", queries=["视频微调 数据集 标注 规范"]), max_reads=0)
    engines = [call[1].get("engines") for call in router.calls if call[0] == "web.discovery"]
    assert engines[0] == "360search,ecosia", engines
    assert len(engines) == 2, engines
    assert engines[1] != engines[0], engines
    assert any("rotating" in note for note in outcome.notes), outcome.notes
