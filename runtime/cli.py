"""``nexus`` - the command line face of the NexusSearch deep research skill.

Every stage of the pipeline is a subcommand, so a host agent (Claude Code,
Codex, Qoder, Copilot) can drive the skill step by step *or* let ``nexus run``
drive the whole research loop by itself:

    nexus init runs/demo
    nexus intake --goal "..." --spec input/spec.md --target .
    nexus tasks --prompts
    nexus run --iterations 3
    nexus verify --all && nexus gate && nexus report

The workspace (graph, evidence, state, logs, output) is plain JSON/Markdown on
disk, so a human can inspect or edit anything between steps.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

if __package__ in (None, ""):  # allow `python runtime/cli.py ...`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from . import harvest
from .agent_router import AgentRouter
from .evidence_store import EvidenceStore
from .graph_engine import ResearchGraph
from .intake import build_plan, context_evidence, find_spec_files, parse_spec, read_spec_files, scan_context, seed_graph
from .loop_controller import LoopController
from .mcp_router import McpError, McpRouter
from .memory_store import MemoryStore
from .models import Evidence, GraphUpdate
from .quality_gate import GateReport, QualityGate
from .report_builder import ReportBuilder, load_loop_log, load_plan
from .skill_spec import (
    PASS,
    TEXT_SUFFIXES,
    Finding,
    GOVERNANCE_FILES,
    audit_release,
    build_bundle,
    release_files,
    scan_private,
    summarize,
    validate_skill,
)
from .util import SKILL_ROOT, ensure_dir, load_config, now_iso, read_json, redact, short, slugify, validate, write_json
from .workspace import Workspace

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
BARE_OK = ("mcp", "doctor", "remember", "skill-check", "quickstart", "open-source", "install")

SECRET_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|password|token|authorization)\b\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{12,})"
)


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------
@dataclass
class Session:
    """Lazy handles for one research run."""

    workspace: Workspace
    offline: bool = False
    verbose: bool = False
    _graph: ResearchGraph | None = field(default=None, repr=False)
    _store: EvidenceStore | None = field(default=None, repr=False)
    _mcp: McpRouter | None = field(default=None, repr=False)
    bare: bool = False
    _agents: AgentRouter | None = field(default=None, repr=False)
    _memory: MemoryStore | None = field(default=None, repr=False)

    @classmethod
    def open(cls, root: str | Path, *, create: bool = False, offline: bool = False, verbose: bool = False) -> "Session":
        return cls(workspace=Workspace.open(root, create=create), offline=offline, verbose=verbose)

    @property
    def root(self) -> Path:
        return self.workspace.root

    @classmethod
    def for_tools(cls, root: str | Path, *, offline: bool = False, verbose: bool = False) -> "Session":
        """Session for MCP/memory tooling: works with or without a workspace."""
        path = Path(root).expanduser()
        if (path / "state" / "workspace.json").is_file():
            # a real workspace: keep the MCP audit trail and cache inside it
            return cls.open(path, offline=offline, verbose=verbose)
        session = cls(workspace=Workspace(path, settings=load_config("settings")), offline=offline, verbose=verbose)
        session.bare = True
        return session

    def graph(self, *, required: bool = True) -> ResearchGraph | None:
        if self._graph is None:
            path = self.workspace.path("graph")
            payload = read_json(path, None)
            if not payload or not payload.get("nodes"):
                if required:
                    raise SystemExit(
                        f"no research graph yet at {path}\nhint: run `nexus intake --goal \"...\"` first"
                    )
                return None
            self._graph = ResearchGraph.load(path)
        return self._graph

    def store(self) -> EvidenceStore:
        if self._store is None:
            self._store = EvidenceStore.open(self.workspace.path("evidence"))
        return self._store

    def agents(self) -> AgentRouter:
        if self._agents is None:
            self._agents = AgentRouter.load()
        return self._agents

    def mcp(self) -> McpRouter:
        if self._mcp is None:
            self._mcp = McpRouter.load(offline=self.offline, workspace=None if self.bare else self.workspace)
        return self._mcp

    def memory(self) -> MemoryStore:
        if self._memory is None:
            self._memory = MemoryStore.open()
        return self._memory

    def loop(self) -> LoopController:
        return LoopController.create(
            self.graph(), self.store(), self.workspace, settings=load_config("settings")
        )

    def plan(self) -> dict[str, Any]:
        return load_plan(self.workspace)

    def record(self, event: str, **payload: Any) -> None:
        """Append a redacted structured event to the run's audit trail."""
        self.workspace.record(event, **redact(payload))


# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------
def emit(args: argparse.Namespace, payload: Any, text: Callable[[], None]) -> int:
    """Print JSON when ``--json`` was asked for, otherwise the text renderer."""
    if getattr(args, "json_out", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        text()
    return EXIT_OK


def table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> str:
    body = [[str(cell) for cell in row] for row in rows]
    widths = [max(len(headers[i]) if i < len(headers) else 0, *(len(row[i]) for row in body)) if body else len(headers[i])
              for i in range(len(headers))]
    lines = ["  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))).rstrip()]
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    lines += ["  ".join(row[i].ljust(widths[i]) for i in range(len(headers))).rstrip() for row in body]
    return "\n".join(lines)



def bullet(text: str) -> None:
    print(f"- {text}")


def heading(text: str) -> None:
    print(f"\n{text}")


# ---------------------------------------------------------------------------
# commands: lifecycle
# ---------------------------------------------------------------------------
def cmd_init(args: argparse.Namespace, session: Session) -> int:
    marker = session.workspace.path("state")
    spec = load_config("settings")
    payload = {
        "workspace": str(session.root),
        "layout": [str(session.root / item) for item in spec.get("workspace", {}).get("layout", [])],
        "marker": str(marker),
        "skill_version": spec.get("skill", {}).get("version", "0"),
    }
    if args.templates:
        written = _write_input_template(session)
        payload["input_template"] = written
    return emit(args, payload, lambda: (print(f"workspace ready: {session.root}"), [bullet(item) for item in payload["layout"]]))


def _write_input_template(session: Session) -> str:
    template = SKILL_ROOT / "templates" / "input-spec.md"
    target = ensure_dir(session.root / "input") / "RESEARCH.md"
    if target.exists():
        return str(target)
    text = template.read_text(encoding="utf-8") if template.is_file() else "# 研究目标\n\n## 研究问题\n- \n"
    target.write_text(text, encoding="utf-8")
    return str(target)


def cmd_intake(args: argparse.Namespace, session: Session) -> int:
    plan_path = session.workspace.path("plan")
    existing = read_json(plan_path, None)
    if existing and existing.get("objective") and not args.force:
        raise SystemExit(f"plan already exists ({plan_path}); pass --force to rebuild")
    spec_paths = find_spec_files(session.root / (args.input_dir or "input"), args.spec or [])
    docs = read_spec_files(spec_paths, max_chars=args.max_spec_chars)
    if not docs and not args.goal:
        input_dir = session.root / (args.input_dir or "input")
        if spec_paths:
            # the usual beginner dead end: init/quickstart dropped a template in
            # input/ and it is still untouched, which is not a research request
            names = " 和 ".join(sorted({path.name for path in spec_paths}))
            raise SystemExit(
                f"{names} is still the unfilled template, so there is nothing to plan yet\n"
                f"edit: {input_dir / 'RESEARCH.md'} - keep「# 研究目标：一句话」and add at least two「## 研究问题」bullets\n"
                f"  or: nexus intake --goal \"你的研究问题\""
            )
        raise SystemExit(
            f"no spec files found under {input_dir} and no --goal given\n"
            f"edit: {input_dir / 'RESEARCH.md'} (nexus quickstart writes a template)\n"
            f"  or: nexus intake --goal \"你的研究问题\""
        )
    parsed = parse_spec(docs, args.goal or "")
    for inline in args.questions or []:
        parsed["questions"] = [*parsed.get("questions", []), *[q.strip() for q in inline.split(";") if q.strip()]][:12]
    target = Path(args.target).expanduser().resolve() if args.target else Path.cwd()
    context = None if args.no_context else scan_context(target, max_files=args.max_context_files)
    plan = build_plan(
        parsed,
        context,
        mode=args.mode,
        language=args.language,
        workspace_root=target,
        deliverables=args.deliverable or [],
    )
    plan["skill"] = {"name": load_config("settings")["skill"]["name"], "version": load_config("settings")["skill"]["version"]}
    ok, message = validate(plan, "plan")
    if not ok:
        raise SystemExit(f"plan failed schema validation: {message}")
    write_json(plan_path, plan)

    graph = ResearchGraph.create(session.workspace.path("graph"), plan["objective"], plan.get("constraints", []))
    session._graph = graph
    counts = seed_graph(graph, plan)
    store = session.store()
    question_ids = [str(item.get("id")) for item in plan.get("questions", []) if item.get("id")]
    records = context_evidence(context, question_ids[:3] or ["goal"]) if context else []
    added = 0
    for record in records:
        stored = store.add(record)
        graph.attach_evidence(stored, stored.supports[0] if stored.supports else "goal", store)
        added += 1
    graph.save()
    store.save()
    state = session.workspace.read_state()
    state.update({"status": "intake_complete", "objective": plan["objective"], "mode": plan["mode"], "questions": len(question_ids)})
    session.workspace.write_state(state)
    session.record("intake", spec_files=[str(path) for path in spec_paths], questions=len(question_ids), context_added=added)

    payload = {
        "plan": str(plan_path),
        "objective": plan["objective"],
        "spec_documents": plan["spec_documents"],
        "questions": plan["questions"],
        "constraints": plan["constraints"],
        "graph_nodes": counts,
        "context": plan.get("context", {}),
        "context_evidence": added,
    }
    def text() -> None:
        print(f"objective: {plan['objective']}")
        bullet(f"spec files: {len(docs)}/{len(spec_paths)} used → {plan_path.name}")
        for entry in plan["questions"]:
            bullet(f"Q {entry.get('id')}: {short(str(entry.get('text')), 80)}")
        for constraint in plan["constraints"][:6]:
            bullet(f"constraint: {short(str(constraint), 80)}")
        bullet(f"graph seeded: {counts}")
        if context:
            bullet(f"context: {context.summary()[:120]}")
            bullet(f"context evidence attached: {added}")
    return emit(args, payload, text)


def cmd_plan(args: argparse.Namespace, session: Session) -> int:
    plan = session.plan()
    if not plan:
        raise SystemExit("no plan yet: run `nexus intake`")
    def text() -> None:
        print(f"{plan.get('title', plan.get('objective', ''))}  [{plan.get('mode', 'auto')}]")
        for entry in plan.get("questions", []):
            bullet(f"{entry.get('id')}: {short(str(entry.get('text')), 90)} → {entry.get('capability_hint', 'web.discovery')}")
        for key in ("constraints", "non_goals", "success_criteria"):
            for item in plan.get(key, []):
                bullet(f"{key}: {short(str(item), 90)}")
        if plan.get("sources"):
            bullet("sources: " + ", ".join(str(item)[:60] for item in plan["sources"][:5]))
    return emit(args, plan, text)


def cmd_status(args: argparse.Namespace, session: Session) -> int:
    graph = session.graph(required=False)
    store = session.store()
    state = session.workspace.read_state()
    payload: dict[str, Any] = {
        "workspace": str(session.root),
        "state": state,
        "plan": bool(session.plan()),
        "offline": session.offline,
    }
    if graph is not None:
        payload["graph"] = graph.stats()
        payload["confidence"] = graph.overall_confidence()
        payload["evidence"] = store.stats()
        gate = QualityGate().evaluate(graph, store)
        payload["gate"] = {"passed": gate.passed, "score": round(gate.score, 3), "failed": [c.id for c in gate.failed()]}
    def text() -> None:
        print(f"workspace: {session.root}")
        bullet(f"plan: {'yes' if payload['plan'] else 'no'}   status: {state.get('status')}   iteration: {state.get('iteration')}")
        if "graph" in payload:
            stats = payload["graph"]
            bullet(f"graph: {stats['nodes']} nodes / {stats['edges']} edges / depth {stats['depth']} {stats['by_type']}")
            bullet(f"confidence: {payload['confidence']}   evidence: {len(store.records)} items, {len(store.domains())} domains")
            gate = payload["gate"]
            bullet(f"gate: {'PASS' if gate['passed'] else 'FAIL'} score={gate['score']} failed={gate['failed'] or 'none'}")
        else:
            bullet("graph: empty - run `nexus intake`")
    return emit(args, payload, text)


# ---------------------------------------------------------------------------
# commands: graph + evidence
# ---------------------------------------------------------------------------
def _print_or_write(args: argparse.Namespace, session: Session, body: str, default_name: str) -> int:
    """Print a rendering, or write it when ``--out`` names a file."""
    target_arg = getattr(args, "out", None)
    if not target_arg:
        print(body)
        return EXIT_OK
    target = session.workspace.guard_write(Path(target_arg))
    ensure_dir(target.parent)
    target.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    print(f"written: {target}")
    return EXIT_OK


def cmd_graph(args: argparse.Namespace, session: Session) -> int:
    graph = session.graph()
    store = session.store()
    action = args.action
    if action == "stats":
        payload = {**graph.stats(), "confidence": graph.overall_confidence()}
        return emit(args, payload, lambda: print(json.dumps(payload, ensure_ascii=False, indent=2)))
    if action == "tree":
        return _print_or_write(args, session, graph.render_tree(), "graph-tree.txt")
    if action == "mermaid":
        return _print_or_write(args, session, graph.render_mermaid(max_nodes=args.max_nodes), "graph.mmd")
    if action == "gaps":
        gaps = graph.gaps(store, session.loop().option("node_coverage", 0.7), limit=args.limit)
        def text() -> None:
            if not gaps:
                print("no open gaps")
                return
            print(table([[g.get("node_id"), short(str(g.get("title", "")), 40), g.get("coverage"), g.get("suggested_capability"), "; ".join(g.get("missing") or [])[:70]] for g in gaps],
                        ["node", "title", "coverage", "capability", "missing"]))
        return emit(args, gaps, text)
    if action == "validate":
        ok, message = graph.validate_now()
        print(f"graph {'ok' if ok else 'INVALID'}: {message}")
        return EXIT_OK if ok else EXIT_FAIL
    if action == "export":
        target = session.workspace.guard_write(args.out or session.root / "output" / "graph.json")
        ensure_dir(Path(target).parent)
        write_json(Path(target), graph.to_dict())
        print(f"graph exported: {target}")
        return EXIT_OK
    if action == "add-node":
        node = graph.add_node(
            args.type,
            args.title,
            node_id=args.id,
            body=args.body or "",
            created_by=args.agent or "host-agent",
            status=args.status,
            confidence=args.confidence,
            risk_level=args.risk_level,
            priority=args.priority,
            weight=args.weight,
        )
        if args.parent:
            graph.add_edge(args.parent, node.id, args.relation or "decomposes_into", strict=False)
        for ref in args.supports or []:
            graph.add_edge(ref, node.id, "derived_from", strict=False)
        graph.save()
        print(f"{node.type} node {node.id} ({short(node.title, 50)})")
        return EXIT_OK
    if action == "add-edge":
        edge_type = args.relation or args.type
        if not edge_type:
            raise SystemExit("add-edge needs --relation TYPE (e.g. --relation supports); --type is for nodes")
        if not (args.frm and args.to):
            raise SystemExit("add-edge needs --frm FROM --to TO")
        graph.add_edge(args.frm, args.to, edge_type, args.weight, args.note, created_by=args.agent or "host-agent")
        graph.save()
        print(f"edge {args.frm} -{edge_type}-> {args.to}")
        return EXIT_OK
    if action == "apply-update":
        payload = read_json(Path(args.file), None)
        if not isinstance(payload, dict):
            raise SystemExit(f"--file must contain a GraphUpdate JSON object, got {args.file}")
        update = GraphUpdate(
            add_nodes=list(payload.get("add_nodes") or []),
            add_edges=list(payload.get("add_edges") or []),
            remove_ids=list(payload.get("remove_ids") or []),
            reason=str(payload.get("reason") or ""),
        )
        added = session.loop().apply_update(update, agent=args.agent or "host-agent")
        graph.propagate(store)
        graph.save()
        print(f"graph update applied: +{added} nodes, reason={short(update.reason, 60)}")
        return EXIT_OK
    if action == "propagate":
        result = graph.propagate(store)
        graph.save()
        return emit(args, result, lambda: print(json.dumps({key: value for key, value in result.items() if key != "detail"}, ensure_ascii=False, indent=2)))
    raise SystemExit(f"unsupported graph action: {action}")


def cmd_evidence(args: argparse.Namespace, session: Session) -> int:
    graph = session.graph()
    store = session.store()
    action = args.action
    rest = list(args.rest or [])
    args.id = rest[0] if rest else ""
    args.ids = rest
    args.left, args.right = (rest[0], rest[1]) if len(rest) >= 2 else ("", "")
    if action == "list":
        records = store.sorted()
        if args.node:
            records = [record for record in records if args.node in record.supports]
        if args.tier:
            records = [record for record in records if record.source_tier == args.tier]
        records = records[: args.limit]
        rows = [[record.id, record.source_type, record.source_tier, f"{record.confidence:.2f}",
                 record.verification.get("status", "unverified"), short(record.domain or Evidence.host_of(record.source_uri) or "local", 22),
                 short(record.claim, args.width or 56)] for record in records]
        return emit(args, [record.to_dict() for record in records],
                    lambda: print(table(rows, ["id", "type", "tier", "conf", "verify", "domain", "claim"])))
    if action == "stats":
        return emit(args, store.stats(), lambda: print(json.dumps(store.stats(), ensure_ascii=False, indent=2)))
    if action == "show":
        record = store.get(args.id)
        if record is None:
            raise SystemExit(f"no evidence with id {args.id}")
        return emit(args, record.to_dict(), lambda: print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2)))
    if action == "add":
        records = _evidence_from_args(args, store)
        stored = [store.add(record) for record in records]
        for record in stored:
            for node_id in record.supports or ["goal"]:
                if node_id in graph.nodes:
                    graph.attach_evidence(record, node_id, store)
        store.save()
        graph.save()
        session.record("evidence_added", ids=[record.id for record in stored])
        print(table([[record.id, record.source_tier, f"{record.confidence:.2f}", short(record.claim, 64)] for record in stored],
                    ["id", "tier", "conf", "claim"]))
        return EXIT_OK
    if action == "verify":
        ids = args.ids or [record.id for record in store.all()]
        touched = 0
        for evidence_id in ids:
            if store.get(evidence_id) is not None:
                store.mark_verified(evidence_id, method=args.method, by="verifier")
                touched += 1
        store.save()
        graph.propagate(store)
        graph.save()
        print(f"verified {touched} evidence records")
        return EXIT_OK
    if action == "contradict":
        if not store.link_contradiction(args.left, args.right):
            raise SystemExit("both evidence ids must exist")
        store.save()
        graph.propagate(store)
        graph.save()
        print(f"{args.left} now contradicts {args.right}")
        return EXIT_OK
    raise SystemExit(f"unsupported evidence action: {action}")


def _evidence_from_args(args: argparse.Namespace, store: EvidenceStore) -> list[Evidence]:
    if args.file:
        payload = read_json(Path(args.file), [])
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise SystemExit(f"{args.file} must contain a list of evidence objects")
        records: list[Evidence] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            record = Evidence.from_dict(_expand_source(item))
            records.append(record)
        if not records:
            raise SystemExit("no usable evidence in file")
        return records
    if not args.claim or not args.uri:
        raise SystemExit("evidence add needs --claim and --uri (or --file)")
    supports = list(args.supports or [])
    record = Evidence(
        claim=args.claim,
        source_type=args.type or "url",
        source_uri=args.uri,
        title=args.title or "",
        summary=args.summary or "",
        quote=args.quote or "",
        modality=args.modality or "text",
        source_tier=args.tier or store.infer_tier(Evidence(claim=args.claim, source_type=args.type or "url", source_uri=args.uri)),
        confidence=args.confidence if args.confidence is not None else 0.55,
        confidence_basis=args.basis or "added by host agent",
        supports=supports or ["goal"],
        contradicts=list(args.contradicts or []),
        tags=list(args.tags or []),
        retrieved_via={"server": args.server or "host-agent", "tool": args.tool or "manual", "capability": args.capability or ""},
    )
    return [record]


def _expand_source(item: dict[str, Any]) -> dict[str, Any]:
    """Accept both the schema shape (nested ``source``) and the flat dataclass shape."""
    source = item.get("source")
    if not isinstance(source, dict):
        return item
    clean = {key: value for key, value in item.items() if key != "source"}
    clean.update(
        {
            "source_type": source.get("type", "url"),
            "source_uri": source.get("uri", ""),
            "domain": source.get("domain", ""),
            "title": source.get("title", ""),
        }
    )
    return clean


# ---------------------------------------------------------------------------
# commands: agents + tasks
# ---------------------------------------------------------------------------
def cmd_tasks(args: argparse.Namespace, session: Session) -> int:
    graph = session.graph()
    store = session.store()
    loop = session.loop()
    agents = session.agents()
    gaps = loop.analyze_gap()
    tasks = loop.generate_tasks(agents, gaps)
    if args.limit:
        tasks = tasks[: args.limit]
    bundle: list[dict[str, Any]] = []
    for task in tasks:
        entry: dict[str, Any] = {"task": task.to_dict()}
        if args.prompts:
            entry["agent_contract"] = agents.context_for(task.agent, task)
            entry["prompt_file"] = str(SKILL_ROOT / (agents.get(task.agent).file if agents.get(task.agent).file else f"agents/{task.agent}.md"))
            entry["instructions"] = _prompt_text(agents.get(task.agent))
        bundle.append(entry)
    if args.write:
        write_json(ensure_dir(session.root / "state") / "tasks.json", {"generated_at": now_iso(), "tasks": [task.to_dict() for task in tasks]})
    def text() -> None:
        if not tasks:
            print("no open gaps - graph looks researched; run `nexus gate`")
            return
        print(table([[task.id, task.agent, task.capability, task.question_ref, short(task.objective, 52)] for task in tasks],
                    ["task", "agent", "capability", "node", "objective"]))
        if args.prompts:
            for entry in bundle:
                task = entry["task"]
                print(f"\n### {task['agent']} · {task['capability']} · {short(str(task['objective']), 60)}")
                bullet(f"prompt: {entry['prompt_file']}")
                bullet(f"queries: {', '.join((task.get('params') or {}).get('queries') or [])}")
                bullet(f"acceptance: {(task.get('acceptance') or {})}")
    return emit(args, bundle if args.prompts else [task.to_dict() for task in tasks], text)


def _prompt_text(spec: Any) -> str:
    path = SKILL_ROOT / spec.file
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return spec.role or ""


def cmd_dispatch(args: argparse.Namespace, session: Session) -> int:
    """Ready-to-run assignment bundle for a host agent (no MCP calls made)."""
    graph = session.graph()
    loop = session.loop()
    agents = session.agents()
    gaps = loop.analyze_gap()
    tasks = agents.ready(loop.generate_tasks(agents, gaps))
    mcp = session.mcp()
    payload = {
        "iteration": loop.iteration + 1,
        "objective": graph.nodes["goal"].title if "goal" in graph.nodes else "",
        "confidence": graph.overall_confidence(),
        "budget": {
            "max_tool_calls_per_iteration": loop.budget("max_tool_calls_per_iteration", 40),
            "capabilities": mcp.capability_names(),
        },
        "assignments": [
            {
                "task": task.to_dict(),
                "agent": agents.context_for(task.agent, task),
                "provider_chain": [
                    {"server": item.get("server"), "tool": item.get("tool")}
                    for item in mcp.providers_for(task.capability)
                ]
                if task.capability in mcp.capabilities()
                else [],
                "auto_executable": task.capability in harvest.AUTO_CAPABILITIES,
            }
            for task in tasks
        ],
    }
    def text() -> None:
        print(f"iteration {payload['iteration']} · confidence {payload['confidence']} · {len(payload['assignments'])} assignments")
        for item in payload["assignments"]:
            task = item["task"]
            print(f"\n- [{task['agent']}] {short(str(task['objective']), 72)}")
            bullet(f"capability: {task['capability']} → {'auto' if item['auto_executable'] else 'host agent'}; chain={[one['server'] for one in item['provider_chain']]}")
            bullet(f"prompt: agents/{task['agent']}.md   node: {task.get('question_ref')}")
    return emit(args, payload, text)


# ---------------------------------------------------------------------------
# commands: mcp
# ---------------------------------------------------------------------------
def cmd_mcp(args: argparse.Namespace, session: Session) -> int:
    router = session.mcp()
    action = args.action
    if action == "caps":
        payload = {
            name: {
                "description": body.get("description", ""),
                "providers": [{"server": one.get("server"), "tool": one.get("tool")} for one in body.get("providers") or []],
                "auto": name in harvest.AUTO_CAPABILITIES,
            }
            for name, body in router.capabilities().items()
        }
        def text() -> None:
            print(table([[name, payload[name]["auto"] and "auto" or "manual",
                          " → ".join(f"{one['server']}:{one['tool'] or '?'}" for one in payload[name]["providers"]),
                          short(payload[name]["description"], 40)] for name in sorted(payload)],
                        ["capability", "mode", "provider chain", "purpose"]))
        return emit(args, payload, text)
    if action == "providers":
        print(json.dumps(router.providers_for(args.capability), ensure_ascii=False, indent=2))
        return EXIT_OK
    if action == "doctor":
        report = router.doctor(only=[args.server] if args.server else None)
        host = router.host_info()
        def text() -> None:
            # Show host detection summary first
            if host["detected"] and host["servers"]:
                bullet(f"检测到宿主 agent：{host['host_name']}（{host['config_path']}）")
                bullet(f"  已配置 {len(host['servers'])} 个 MCP 服务：{', '.join(host['servers'][:10])}")
                matched_count = len(host["matched"])
                if matched_count:
                    bullet(f"  其中 {matched_count} 个与本 skill 的 mcp-map 匹配——无需额外安装")
                else:
                    bullet("  与本 skill 的 mcp-map 无匹配项（服务名不同或需要单独配置）")
            print(table([[one["server"], one["transport"], str(one.get("tier", "?")), one["status"].upper(),
                          f'{one.get("latency_ms", 0)}ms', short(one.get("detail", ""), 52)] for one in report],
                        ["server", "transport", "tier", "state", "latency", "detail"]))
            reachable = [str(one["server"]) for one in report if one["status"] == "ok"]
            via_host = [str(one["server"]) for one in report if one.get("via_host")]
            bullet(f"reachable: {len(reachable)}/{len(report)} → {', '.join(reachable)}")
            if via_host:
                bullet(f"via host: {len(via_host)} → {', '.join(via_host)}")
            for one in report:
                if one["status"] != "ok":
                    bullet(f'{one["server"]}: {one["status"]} — {short(str(one.get("detail")), 78)}')
        code = EXIT_OK if any(one["status"] == "ok" for one in report) else EXIT_FAIL
        if args.json_out:
            print(json.dumps({"host": host, "servers": report}, ensure_ascii=False, indent=2, default=str))
            return code
        text()
        return code
    if action == "call":
        arguments = json.loads(args.args or "{}")
        if args.raw:
            if not (args.server and args.tool):
                raise SystemExit("mcp call --raw needs SERVER TOOL --args JSON")
            result = router.call_tool(args.server, args.tool, arguments)
        else:
            capability = args.server or ""
            if capability not in router.capabilities():
                raise SystemExit(f"unknown capability: {capability} (see `nexus mcp caps`)")
            result = router.call(capability, **arguments)
        payload = {**result.to_dict(), "text": result.text[:4000], "data": result.data}
        if not result.ok:
            if args.json_out:
                print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            else:
                needed = "" if args.raw else ", ".join(router.required_arguments(capability))
                extra = f" | arguments this capability needs: {needed}" if needed else ""
                raise SystemExit(
                    f"call failed [{result.error_class or 'unknown'}]: "
                    f"{result.error or 'no usable provider'}{extra}"
                )
            return EXIT_FAIL
        return emit(args, payload, lambda: print(result.text[: (args.show or 1500)]))
    if action == "stats":
        return emit(args, router.stats(), lambda: print(json.dumps(router.stats(), ensure_ascii=False, indent=2)))
    if action == "clear-cache":
        cache = ensure_dir(session.root / "state" / "cache") / "mcp-cache.json"
        if cache.is_file():
            cache.unlink()
        print(f"cache cleared: {cache}")
        return EXIT_OK
    raise SystemExit(f"unsupported mcp action: {action}")


# ---------------------------------------------------------------------------
# commands: verification, gate, loop, report
# ---------------------------------------------------------------------------
def cmd_verify(args: argparse.Namespace, session: Session) -> int:
    graph = session.graph()
    store = session.store()
    threshold = float(load_config("quality").get("gate", {}).get("confidence_threshold", 0.85))
    claims = graph.of_type("claim") if not args.evidence_only else []
    results: list[dict[str, Any]] = []
    if args.ids:
        for evidence_id in args.ids:
            if store.get(evidence_id) is not None:
                store.mark_verified(evidence_id, method=args.method, notes="manual verify")
    for claim in claims:
        pool = store.for_node(claim.id)
        domains = store.domains(pool)
        opposing = store.contradicting(claim.id)
        decision = {"id": claim.id, "title": short(claim.title, 60), "sources": len(pool), "domains": len(domains), "contradictions": len(opposing)}
        if args.apply:
            if len(domains) >= args.min_domains and not opposing:
                for record in pool:
                    store.mark_verified(record.id, method=args.method, notes=f"corroborated by {len(domains)} domains")
                decision["action"] = "verified"
            elif not pool:
                decision["action"] = "no-evidence"
            else:
                decision["action"] = "held"
        results.append(decision)
    if args.apply:
        store.save()
        graph.propagate(store, threshold)
        graph.save()
    statuses = [{"id": claim.id, "status": claim.status, "confidence": claim.confidence, "refs": len(claim.evidence_refs)} for claim in claims]
    payload = {"claims": statuses, "decisions": results, "evidence": store.stats()}
    def text() -> None:
        print(table([[item["id"], item["status"], f"{item['confidence']:.2f}", item["refs"]] for item in statuses], ["claim", "status", "conf", "evidence"]))
        for item in results:
            if item.get("action"):
                bullet(f"{item['id']}: {item['action']} (sources={item['sources']}, domains={item['domains']}, contradictions={item['contradictions']})")
        bullet(f"overall confidence: {graph.overall_confidence()} (threshold {threshold})")
    return emit(args, payload, text)


def cmd_gate(args: argparse.Namespace, session: Session) -> int:
    graph = session.graph()
    store = session.store()
    report = QualityGate().evaluate(graph, store)
    if args.save:
        write_json(ensure_dir(session.root / "state") / "gate.json", report.to_dict())
    def text() -> None:
        print(table([[check.id, "PASS" if check.passed else "FAIL", f"{check.score:.2f}", short(check.detail, 48)] for check in report.checks],
                    ["check", "state", "score", "detail"]))
        bullet(f"quality score {report.score:.3f} (min {report.threshold:.2f}) · confidence {report.confidence:.2f}")
        bullet(f"verdict: {'PASS - report may be generated' if report.passed else 'FAIL - more research required'}")
        if report.next_actions():
            heading("next actions")
            for action in report.next_actions():
                bullet(short(action, 100))
    if args.json_out:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        text()
    return EXIT_OK if (report.passed or not args.strict) else EXIT_FAIL


def cmd_loop(args: argparse.Namespace, session: Session) -> int:
    loop = session.loop()
    if args.abort:
        loop.abort()
        state = session.workspace.read_state()
        state.update({"status": "aborted", "abort_requested": True})
        session.workspace.write_state(state)
        print("abort requested - the loop stops at its next checkpoint")
        return EXIT_OK
    if args.reset:
        session.workspace.write_state({**session.workspace.read_state(), "iteration": 0, "history": [], "status": "reset"})
        print("loop counters reset")
        return EXIT_OK
    if args.show or args.json_out:
        snapshot = loop.snapshot()
        return emit(args, snapshot, lambda: print(table(
            [[entry.get("index"), entry.get("stage"), f"{(entry.get('confidence') or [0, 0])[1]:.0%}",
              entry.get("new_evidence", 0), entry.get("new_nodes", 0), entry.get("stop_reason") or "-"]
             for entry in snapshot.get("history", [])],
            ["iter", "stage", "confidence", "+evidence", "+nodes", "stop"],
        )))
    raise SystemExit("nothing to do: pass --show, --abort or --reset (use `nexus run` to iterate)")


def cmd_run(args: argparse.Namespace, session: Session) -> int:
    """Drive the full research loop: gaps → tasks → MCP → evidence → gate."""
    graph = session.graph()
    store = session.store()
    agents = session.agents()
    router = session.mcp()
    memory = session.memory() if not args.no_memory else None
    loop = session.loop()
    settings = load_config("settings")
    max_iterations = args.iterations or int((settings.get("loop") or {}).get("max_iterations", 5))
    if session.workspace.read_state().get("abort_requested"):
        loop.abort()
    iterations: list[dict[str, Any]] = []
    gate_report: GateReport | None = None
    stop_reason = ""
    for _step in range(max_iterations):
        gaps = loop.analyze_gap()
        tasks = agents.ready(loop.generate_tasks(agents, gaps))
        if not tasks:
            stop_reason = "no_dispatchable_tasks"
            break
        if args.dry_run:
            print(table([[task.agent, task.capability, task.question_ref, short(task.objective, 60)] for task in tasks],
                        ["agent", "capability", "node", "objective"]))
            return EXIT_OK
        loop.begin_iteration(tasks)
        known_urls = {record.source_uri for record in store.all() if record.source_uri}
        for task in tasks:
            try:
                outcome = harvest.execute_task(
                    router, task, max_reads=args.max_reads, memory_store=memory, known_urls=known_urls
                )
            except McpError as error:
                outcome = harvest.HarvestOutcome(task_id=task.id, agent=task.agent, capability=task.capability, ok=False, error=str(error))
            loop.count_tool_calls(max(1, outcome.tool_calls))
            if outcome.evidence:
                collected = loop.collect(outcome.evidence, node_id=str((task.params or {}).get("node_id") or ""), agent=task.agent)
                outcome.notes.append(f"collected {collected['evidence']} evidence, {collected['nodes']} nodes")
            task.result = outcome.to_dict()
            task.status = "done" if outcome.ok else "failed"
            if not args.quiet:
                print(f"  [{task.agent}/{task.capability}] {task.status}: {len(outcome.evidence)} evidence"
                      + (f" · {short('; '.join(outcome.notes), 60)}" if outcome.notes else ""))
            session.record("task_executed", **outcome.to_dict())
        if args.verify:
            loop.verify_claims()
        gate_report = loop.run_gate()
        stop, reason = loop.should_stop(gate_report)
        record = loop.finish_iteration(reason)
        iterations.append(record.to_dict())
        stop_reason = reason
        if not args.quiet:
            print(f"iteration {record.index}: evidence +{record.new_evidence}, nodes +{record.new_nodes}, "
                  f"confidence {record.confidence_before:.2f}→{record.confidence_after:.2f}, gate {gate_report.score:.2f}"
                  + (f" → stop:{reason}" if stop else ""))
        if stop:
            break
    else:
        stop_reason = stop_reason or "requested_iterations_exhausted"
    state = session.workspace.read_state()
    state.update({"status": "loop_complete", "stop_reason": stop_reason, "confidence": graph.overall_confidence()})
    session.workspace.write_state(state)
    promoted: list[str] = []
    if memory is not None and gate_report is not None and not args.no_memory:
        patterns = memory.promote_from_run(gate_report.to_dict(), load_loop_log(session.workspace.loop_log()), source_run=session.root.name)
        memory.save()
        promoted = [pattern.title for pattern in patterns]
    payload = {
        "stop_reason": stop_reason,
        "iterations": iterations,
        "confidence": graph.overall_confidence(),
        "gate": gate_report.to_dict() if gate_report else {},
        "evidence": store.stats(),
        "memory_patterns": promoted,
    }
    def text() -> None:
        heading("run summary")
        bullet(f"stop reason: {stop_reason}")
        bullet(f"iterations: {len(iterations)} · confidence {graph.overall_confidence()} · evidence {len(store.records)} ({len(store.domains())} domains)")
        if gate_report is not None:
            bullet(f"gate: {'PASS' if gate_report.passed else 'FAIL'} score={gate_report.score:.3f} failed={[c.id for c in gate_report.failed()]}")
            for action in gate_report.next_actions()[:4]:
                bullet(f"next: {short(action, 100)}")
        if promoted:
            bullet(f"self-evolution: {len(promoted)} patterns written to {memory.path}")
        bullet(f"next: `nexus verify --apply` then `nexus report`")
    return emit(args, payload, text)


def cmd_report(args: argparse.Namespace, session: Session) -> int:
    graph = session.graph()
    store = session.store()
    gate_report = None
    if args.gate:
        gate_report = QualityGate().evaluate(graph, store)
    builder = ReportBuilder.create(
        graph,
        store,
        gate_report=gate_report,
        loop_log=load_loop_log(session.workspace.loop_log()),
        plan=session.plan(),
    )
    if args.stdout:
        print(builder.render())
        return EXIT_OK
    written = _write_report(session, builder, gate_report)
    def text() -> None:
        for item in written:
            bullet(str(item))
        bullet(f"report confidence: {graph.overall_confidence()} · evidence {len(store.records)}")
    return emit(args, {"written": [str(item) for item in written]}, text)


def _write_report(session: Session, builder: ReportBuilder, gate_report: GateReport | None) -> list[Path]:
    markdown = builder.render()
    out = ensure_dir(session.root / "output")
    targets = {
        session.workspace.files.get("report", "output/report.md"): markdown,
    }
    payload_json = {
        session.workspace.files.get("evidence_json", "output/evidence.json"): [record.to_dict() for record in session.store().sorted()],
        session.workspace.files.get("graph_json", "output/graph.json"): session.graph().to_dict(),
        session.workspace.files.get("manifest", "output/manifest.json"): builder.manifest([str(path) for path in targets]),
    }
    written: list[Path] = []
    for relative, body in targets.items():
        target = session.workspace.guard_write(session.root / relative)
        ensure_dir(target.parent)
        target.write_text(body, encoding="utf-8")
        written.append(target)
    for relative, body in payload_json.items():
        target = session.workspace.guard_write(session.root / relative)
        ensure_dir(target.parent)
        write_json(target, body)
        written.append(target)
    (out / "graph.mmd").write_text(session.graph().render_mermaid(), encoding="utf-8")
    written.append(out / "graph.mmd")
    session.record("report_written", files=[str(item) for item in written])
    return written


def cmd_remember(args: argparse.Namespace, session: Session) -> int:
    store = session.memory()
    action = args.action
    if action == "list":
        patterns = store.patterns
        def text() -> None:
            print(table([[item.id, item.kind, f"{item.confidence:.2f}", f"{item.wins}/{item.uses}", short(item.title, 60)] for item in patterns],
                        ["id", "kind", "conf", "win/uses", "title"]))
        return emit(args, [item.to_dict() for item in patterns], text)
    if action == "recall":
        found = store.recall(args.query or "", kind=args.kind, limit=args.limit)
        def text() -> None:
            if not found:
                print("nothing recalled")
                return
            for item in found:
                bullet(f"[{item.kind} {item.confidence:.2f}] {item.title} — {short(item.statement, 90)}")
        return emit(args, [item.to_dict() for item in found], text)
    if action == "add":
        title = args.title or args.query
        if not title or not args.statement:
            raise SystemExit("remember add needs TITLE and --statement")
        pattern = store.remember(args.kind, title, args.statement, context=args.context or [], tags=args.tags or [], confidence=args.confidence, source_run=session.root.name)
        store.save()
        print(f"{pattern.id}: {pattern.title}")
        return EXIT_OK
    if action == "use":
        pattern = store.record_use(args.id, not args.failed)
        if pattern is None:
            raise SystemExit(f"unknown pattern id {args.id}")
        store.save()
        print(f"{pattern.id}: confidence {pattern.confidence:.2f} (uses {pattern.uses}, wins {pattern.wins})")
        return EXIT_OK
    if action == "promote":
        gate_payload = read_json(session.root / "state" / "gate.json", None) or QualityGate().evaluate(session.graph(), session.store()).to_dict()
        patterns = store.promote_from_run(gate_payload, load_loop_log(session.workspace.loop_log()), source_run=session.root.name)
        store.save()
        print(table([[item.id, item.kind, item.title] for item in patterns], ["id", "kind", "title"]))
        return EXIT_OK
    if action == "sync":
        entities = store.export_entities(limit=args.limit)
        payload = {"entities": entities, "observations": store.sync_observations(args.id or "", args.note or "")}
        if args.push:
            result = session.mcp().call_tool("memory", "create_entities", {"entities": entities})
            payload["mcp"] = {**result.to_dict(), "text": result.text[:1500]}
        return emit(args, payload, lambda: print(json.dumps(payload, ensure_ascii=False, indent=2)))
    if action == "stats":
        return emit(args, store.stats(), lambda: print(json.dumps(store.stats(), ensure_ascii=False, indent=2)))
    raise SystemExit(f"unsupported remember action: {action}")


# ---------------------------------------------------------------------------
# commands: audit
# ---------------------------------------------------------------------------
def cmd_audit(args: argparse.Namespace, session: Session) -> int:
    findings: list[dict[str, Any]] = []

    def add(state: str, area: str, message: str) -> None:
        findings.append({"state": state, "area": area, "message": message})

    spec = load_config("settings")
    for relative in spec.get("workspace", {}).get("layout", []):
        if not (session.root / relative).is_dir():
            add("fail", "workspace", f"missing directory: {relative}")

    plan = session.plan()
    if not plan:
        add("fail", "plan", "state/research-plan.json is empty - run `nexus intake`")
    else:
        ok, message = validate(plan, "plan")
        add("pass" if ok else "fail", "plan", message if not ok else f"{len(plan.get('questions') or [])} questions")
        if not plan.get("success_criteria"):
            add("warn", "plan", "no success criteria - the gate falls back to defaults")

    graph = session.graph(required=False)
    store = session.store()
    if graph is None:
        add("fail", "graph", "graph/research.graph.json has no nodes")
    else:
        ok, message = graph.validate_now()
        add("pass" if ok else "fail", "graph", message if ok else f"invalid: {message}")
        claims = graph.of_type("claim")
        if not claims:
            add("warn", "graph", "no claim nodes yet - conclusions cannot be gated")
        for claim in claims:
            if not store.for_node(claim.id):
                add("fail", "evidence", f"claim {claim.id} has no evidence (unsupported conclusion)")
            if claim.status == "verified" and len(store.domains(store.for_node(claim.id))) < 2:
                add("fail", "evidence", f"claim {claim.id} verified on a single domain")
        unlinked = [record.id for record in store.all() if "unlinked" in record.quality_flags]
        if unlinked:
            add("warn", "evidence", f"{len(unlinked)} evidence records not attached to any node")
    # evidence.schema.json describes one record, so the store is validated item by item
    offenders = []
    for record in store.sorted():
        ok, message = validate(record.to_dict(), "evidence")
        if not ok:
            offenders.append(f"{record.id}: {message}")
    if offenders:
        add("fail", "evidence", f"{len(offenders)} records violate evidence.schema.json - {offenders[0]}")
    else:
        add("pass", "evidence", f"all {len(store.records)} records satisfy evidence.schema.json")

    router_capabilities = McpRouter.load(offline=True).capability_names()
    problems = session.agents().audit(router_capabilities)
    for problem in problems:
        add("warn" if problem.get("severity") == "warn" else "fail", "agents",
            f"{problem.get('agent')}: {problem.get('issue')}")
    if not problems:
        add("pass", "agents", f"{len(session.agents().names())} agents, all grants resolve in mcp-map")

    try:
        report = QualityGate().evaluate(graph, store) if graph is not None else None
    except Exception as error:  # noqa: BLE001 - audit must never crash
        add("fail", "gate", f"quality gate raised {type(error).__name__}: {error}")
        report = None
    if report is not None:
        add("pass" if report.passed else "warn", "gate", f"score {report.score:.3f} failed={[c.id for c in report.failed()]}")

    for area, path in (("privacy", session.root / "output"), ("audit", session.root / "agents" / "logs")):
        if not path.is_dir():
            continue
        hits = 0
        for target in list(path.rglob("*.json"))[:200] + list(path.rglob("*.jsonl"))[:200] + list(path.rglob("*.md"))[:200]:
            try:
                text = target.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if SECRET_PATTERN.search(text) or re.search(r"ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}", text):
                hits += 1
                add("fail", area, f"possible credential in {session.workspace.relative(target)}")
        if not hits:
            add("pass", area, f"no credentials found under {path.name}/")

    payload = {"findings": findings, "summary": {state: sum(1 for item in findings if item["state"] == state) for state in ("pass", "warn", "fail")}}
    def text() -> None:
        print(table([[item["state"].upper(), item["area"], short(item["message"], 78)] for item in findings], ["state", "area", "message"]))
        bullet(f"{payload['summary']}")
    if args.json_out:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        text()
    return EXIT_OK if payload["summary"]["fail"] == 0 else EXIT_FAIL


# ---------------------------------------------------------------------------
# commands: spec conformance, onboarding, release
# ---------------------------------------------------------------------------
def _skill_name() -> str:
    return str((load_config("settings").get("skill") or {}).get("name") or "nexus-deep-research")


def _print_findings(findings: Sequence[Finding], *, width: int = 96) -> int:
    """Render findings as a table and return the process exit code."""
    marks = {"pass": "ok  ", "warn": "WARN", "error": "FAIL"}
    rows = [[marks[item.level], item.area, short(item.message, width)] for item in findings]
    print(table(rows, ["", "area", "check"]))
    counts = summarize(findings)
    bullet(f"{counts['pass']} pass, {counts['warn']} warn, {counts['error']} fail")
    for item in findings:
        if item.level == "error":
            bullet(f"fix: {short(item.message, width)}")
    return EXIT_OK if counts["error"] == 0 else EXIT_FAIL


def cmd_skill_check(args: argparse.Namespace, session: Session) -> int:
    """Validate this skill (and optionally the installed copy) against the Agent Skills spec."""
    targets: list[tuple[str, Path]] = []
    if args.path:
        targets.append(("custom", Path(args.path).expanduser()))
    else:
        targets.append(("source", SKILL_ROOT))
        for candidate in _install_candidates():
            if (candidate / "SKILL.md").is_file():
                targets.append(("installed", candidate))
    code = EXIT_OK
    for kind, target in targets:
        heading(f"{kind}: {target}")
        # a source checkout has its own folder name; only an installed skill must
        # live in skills/<name>/, so the directory rule is checked per context
        findings = validate_skill(target, expect_dir_match=kind != "source")
        if kind == "source" and (target / "SKILL.md").is_file():
            findings.append(
                Finding(PASS, "name", f"source tree - install as skills/{_skill_name()}/ (the folder name here does not matter)")
            )
        code = max(code, _print_findings(findings))
        if args.release:
            code = max(code, _print_findings(audit_release(target)))
    if code == EXIT_OK:
        bullet("spec compliant - any Agent Skills host can load this skill")
    else:
        bullet("not spec compliant - fix the FAIL rows above, then run `nexus skill-check` again")
    return code


HOSTS = (
    ("~/.agents/skills", "agents / Codex desktop (default)"),
    ("~/.codex/skills", "Codex CLI"),
    ("~/.claude/skills", "Claude Code"),
    ("./.agent/skills", "Qoder (per project)"),
)


def cmd_install(args: argparse.Namespace, session: Session) -> int:
    """Copy the skill payload into an agent host's skills directory.

    One source of truth for what an install contains: exactly the files
    :func:`release_files` would publish. That keeps private material, run
    artefacts and stale leftovers out of the installed copy, which a hand
    maintained exclude list in a shell script historically does not.
    """
    name = _skill_name()
    if args.list:
        print(table([[host, note] for host, note in HOSTS], ["skills root", "host"]))
        bullet(f"install directory name is fixed by the spec: <root>/{name}")
        return EXIT_OK

    dest_root = Path(args.dest).expanduser() if args.dest else Path.home() / ".agents" / "skills"
    target = dest_root / name
    payload = release_files(SKILL_ROOT)
    if args.dry_run:
        bullet(f"would install {len(payload)} files -> {target}")
        bullet("user state is never removed: memory/, runs/; everything else is replaced or pruned")
        return EXIT_OK

    if target.exists() and not (target / "SKILL.md").is_file() and not args.force:
        print(f"refusing to replace {target}: it has no SKILL.md (pass --force if you are sure)", file=sys.stderr)
        return EXIT_USAGE
    if not dest_root.is_dir():
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            print(f"cannot create {dest_root}: {error}", file=sys.stderr)
            print("hint: pass --dest <a writable skills directory>", file=sys.stderr)
            return EXIT_USAGE

    # Refresh in place instead of wiping the target: research output and the learned
    # memory ledger belong to the user, so they survive; anything the payload does
    # not contain is pruned, which is what keeps stale private files from lingering
    # in the skills directory after an earlier copy-based install.
    keep = ("memory", "runs")
    copied = 0
    for source in payload:
        destination = target / source.relative_to(SKILL_ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += 1
    pruned = 0
    if target.is_dir():
        wanted = {source.relative_to(SKILL_ROOT).as_posix() for source in payload}
        for stale in sorted(target.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if not stale.exists() or stale.is_dir():
                continue
            relative = stale.relative_to(target).as_posix()
            if relative in wanted or relative.split("/")[0] in keep:
                continue
            stale.unlink()
            pruned += 1
        for directory in sorted((path for path in target.rglob("*") if path.is_dir()), key=lambda item: len(item.parts), reverse=True):
            relative = directory.relative_to(target).as_posix()
            if relative.split("/")[0] in keep:
                continue
            try:
                directory.rmdir()  # only removes directories left empty by the prune
            except OSError:
                pass
    try:
        ledger = target / "memory" / "research-patterns.json"
        if not ledger.is_file():
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "patterns": [],
                        "note": "Learned query patterns are written here at runtime by `nexus remember`.",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        launcher = target / "nexus"
        if launcher.is_file():
            launcher.chmod(0o755)
    except OSError as error:
        print(f"install failed: {error}", file=sys.stderr)
        return EXIT_USAGE

    print(f"installed  {target}  ({copied} files, {pruned} stale removed)")
    findings = validate_skill(target)
    code = _print_findings(findings)
    missing = [relative for relative in GOVERNANCE_FILES if not (target / relative).is_file()]
    if missing:
        bullet(f"note: not installed (repo-only): {', '.join(missing)}")
    if code == EXIT_OK:
        bullet("loadable by any Agent Skills host - restart the host, then mention the trigger words")
        bullet(f"try:  cd {target} && ./nexus quickstart \"你的研究问题\"")
    else:
        bullet("the installed copy failed the spec check - fix the FAIL rows above before using the skill")
    return code


def _install_candidates() -> list[Path]:
    """Every place a host might have loaded this skill from, in lookup order."""
    name = _skill_name()
    home = Path.home()
    return [
        home / ".agents" / "skills" / name,
        home / ".codex" / "skills" / name,
        home / ".claude" / "skills" / name,
        Path.cwd() / ".agent" / "skills" / name,
    ]


def cmd_quickstart(args: argparse.Namespace, session: Session) -> int:
    """One command from a research question to a workspace, a plan and next steps."""
    goal = " ".join(args.goal).strip()
    cwd = Path.cwd()
    if args.at:
        target = Path(args.at).expanduser()
    elif (cwd / "state" / "workspace.json").is_file():
        target = cwd
    else:
        target = cwd / "runs" / (slugify(goal or "research") or "research")
    print(f"nexus quickstart - 目标：{goal or '(未提供，请编辑 input/RESEARCH.md)'}")
    step(1, f"创建工作区 {target}")
    quick = Session.open(target, create=True, offline=args.offline, verbose=args.verbose)
    template = _write_input_template(quick)
    step(2, f"需求文件已就绪 {template}")
    had_plan = (quick.workspace.path("plan")).is_file() and read_json(quick.workspace.path("plan"), {}).get("objective")
    code = EXIT_OK
    if goal and not had_plan:
        step(3, "解析需求，生成研究计划与研究图")
        code = cmd_intake(
            argparse.Namespace(
                goal=goal, spec=None, input_dir="input", target=args.target or str(Path.cwd()),
                questions=None, mode="standard", language="auto", deliverable=None,
                no_context=args.no_context, max_context_files=4000, max_spec_chars=24000,
                force=False, json_out=False,
            ),
            quick,
        )
        if code != EXIT_OK:
            bullet("intake 失败——工作区仍然可用，稍后手动运行 `nexus intake`")
    else:
        step(3, "跳过解析（已有计划，或未提供目标）")
    # the workspace seeds an empty placeholder graph file, so existence is not the
    # question - a plan and at least one node are
    has_graph = bool((read_json(quick.workspace.path("graph"), None) or {}).get("nodes"))
    step(4, "列出将要执行的检索任务（不联网、不调用 MCP）")
    if has_graph and had_plan:
        cmd_plan(argparse.Namespace(json_out=False), quick)
        cmd_tasks(argparse.Namespace(prompts=False, json_out=False, limit=8, write=False), quick)
    else:
        # no goal yet means no plan: guide the beginner instead of letting
        # cmd_tasks() abort with "no research graph yet"
        bullet("还没有研究计划。下一步二选一：")
        bullet(f"  A. 编辑 {template} 后运行：{SKILL_ROOT / 'nexus'} intake")
        bullet(f"  B. 直接带问题重来：{SKILL_ROOT / 'nexus'} quickstart \"你的研究问题\"")
    reachable = None
    host_detected = None
    if not args.offline:
        try:
            router = quick.mcp()
            report = router.doctor()
            reachable = sum(1 for one in report if one["status"] == "ok")
            host = router.host_info()
            if host["detected"] and host["servers"]:
                host_detected = host
                matched = len(host["matched"])
                step(5, f"检测到 {host['host_name']} 已配置 {len(host['servers'])} 个 MCP 服务"
                        + (f"（{matched} 个匹配本 skill，无需额外安装）" if matched else ""))
                step(5, f"本地 MCP 自检：{reachable}/{len(report)} 个服务可达")
            else:
                step(5, f"本地 MCP 自检：{reachable}/{len(report)} 个服务可达")
        except Exception as error:  # noqa: BLE001 - onboarding must never traceback
            reachable = 0
            step(5, f"本地 MCP 自检未完成（{type(error).__name__}）— 可稍后运行 `nexus doctor`")
    heading("下一步（复制粘贴即可）")
    launcher = SKILL_ROOT / "nexus"
    # keep the flags consistent with the mode this run actually used, so a
    # beginner can paste the lines without learning where global flags go
    pre = "--offline " if args.offline else ""
    lines = [
        f"  cd {target}",
        "  $EDITOR input/RESEARCH.md        # 用中文补充目标/问题/约束，标题词见模板注释",
    ]
    if has_graph:
        lines += [
            f"  {launcher} {pre}run --verify                        # 跑检索循环（含交叉验证）",
            f"  {launcher} {pre}gate && {launcher} {pre}report      # 过质量门，产出报告",
        ]
    else:
        # never hand a beginner a command that will abort: intake first, then run
        lines += [
            f"  {launcher} intake                              # 读完 RESEARCH.md 后解析成研究计划",
            f"  {launcher} {pre}run --verify && {launcher} {pre}report   # 跑循环并产出报告",
        ]
    for line in lines:
        print(line)
    bullet(f"报告落点：{quick.root / 'output' / 'report.md'}")
    if reachable == 0 and not args.offline:
        if host_detected:
            bullet(f"提示：{host_detected['host_name']} 已配置 MCP 但全部不可达——运行 `./nexus doctor` 排查")
        else:
            bullet("提示：本机 MCP 未就绪，先运行 `./nexus doctor` 查看配置，或用 `--offline` 走本地降级链路")
    elif reachable and reachable < len(report or []) and host_detected and not args.offline:
        bullet(f"提示：{host_detected['host_name']} 提供了部分服务，其余可稍后配置（`./nexus doctor` 查看详情）")
    guide = SKILL_ROOT / "docs" / "QUICKSTART.md"
    bullet(f"完整新手指南：{guide}（5 分钟）" if guide.is_file() else "完整新手指南：docs/QUICKSTART.md（5 分钟）")
    return code


def step(number: int, message: str) -> None:
    print(f"  [{number}/5] {message}")


def cmd_open_source(args: argparse.Namespace, session: Session) -> int:
    """Release gate: check publishability, or build a sanitized install bundle."""
    root = SKILL_ROOT
    if args.action == "check":
        findings = audit_release(root)
        def text() -> None:
            _print_findings(findings)
        code = _print_findings(findings)
        if code == EXIT_FAIL:
            heading("blocked - 以上 FAIL 项修复前不要公开发布")
            bullet("跑 `nexus open-source build` 会生成已脱敏的 dist/ 安装包，无需删除本地文件")
        return code
    if args.action == "build":
        payload = build_bundle(root, args.out, archive=not args.no_archive)
        bullet(f"bundle: {payload['bundle']} ({payload['files']} files, {payload['masked_files']} masked)")
        if payload.get("archive"):
            bullet(f"archive: {payload['archive']}")
        findings = validate_skill(Path(payload["bundle"]))
        _print_findings(findings)
        leftover = []
        for path in Path(payload["bundle"]).rglob("*"):
            if path.is_file() and path.suffix in TEXT_SUFFIXES:
                if scan_private(path.read_text(encoding="utf-8", errors="ignore")):
                    leftover.append(str(path.relative_to(payload["bundle"])))
        if leftover:
            for item in leftover[:10]:
                bullet(f"FAIL residual private content in {item}")
            return EXIT_FAIL
        bullet("bundle clean - no host paths or credentials; install with `cp -R` into your skills directory")
        return EXIT_OK if summarize(findings)["error"] == 0 else EXIT_FAIL
    raise SystemExit(f"unsupported action: {args.action}")


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", dest="json_out", action="store_true", help="machine readable output")


def build_parser() -> argparse.ArgumentParser:
    settings = load_config("settings")
    parser = argparse.ArgumentParser(
        prog="nexus",
        description=f"{settings['skill']['display_name']} - graph-driven local deep research",
        epilog="pipeline: init → intake → tasks/dispatch → run → verify → gate → report",
    )
    parser.add_argument("--version", action="version", version=f"nexus {settings['skill']['version']}")
    parser.add_argument("-w", "--workspace", default=".", help="research workspace root (default: cwd)")
    parser.add_argument("--offline", action="store_true", help="never contact any MCP server; degrade to local adapters")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a research workspace")
    p.add_argument("path", nargs="?", default=None, help="workspace directory (default: --workspace)")
    p.add_argument("--templates", action="store_true", help="also write input/RESEARCH.md")
    _add_common(p)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("intake", help="parse the spec folder into a plan and a seeded graph")
    p.add_argument("--goal", help="research objective (fallback when no spec states one)")
    p.add_argument("--spec", action="append", help="extra spec file or directory (repeatable)")
    p.add_argument("--input-dir", default="input", help="workspace input directory (default: input)")
    p.add_argument("--target", help="project under study for the context scan (default: cwd)")
    p.add_argument("--questions", action="append", help="semicolon separated research questions to add")
    p.add_argument("--mode", default="auto", choices=("auto", "quick", "standard", "deep"))
    p.add_argument("--language", default="auto")
    p.add_argument("--deliverable", action="append", help="expected output artifact")
    p.add_argument("--no-context", action="store_true", help="skip the local workspace scan")
    p.add_argument("--max-context-files", type=int, default=4000)
    p.add_argument("--max-spec-chars", type=int, default=24000)
    p.add_argument("--force", action="store_true", help="overwrite an existing plan")
    _add_common(p)
    p.set_defaults(func=cmd_intake)

    p = sub.add_parser("plan", help="show the research plan")
    _add_common(p)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("status", help="compact run status")
    _add_common(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("graph", help="inspect or edit the research graph")
    p.add_argument("action", choices=("stats", "tree", "mermaid", "gaps", "validate", "export", "propagate", "add-node", "add-edge", "apply-update"))
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--max-nodes", type=int, default=60)
    p.add_argument("--out")
    p.add_argument("--type", help="node or edge type")
    p.add_argument("--title")
    p.add_argument("--id")
    p.add_argument("--body")
    p.add_argument("--agent")
    p.add_argument("--parent")
    p.add_argument("--relation")
    p.add_argument("--status")
    p.add_argument("--confidence", type=float)
    p.add_argument("--risk-level", dest="risk_level")
    p.add_argument("--priority", type=int, default=1)
    p.add_argument("--weight", type=float, default=1.0)
    p.add_argument("--supports", action="append")
    p.add_argument("--frm", help="edge source node")
    p.add_argument("--to", help="edge target node")
    p.add_argument("--note")
    p.add_argument("--file", help="GraphUpdate JSON for apply-update")
    _add_common(p)
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("evidence", help="inspect or add evidence records")
    p.add_argument("action", choices=("list", "show", "add", "stats", "verify", "contradict"))
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--node")
    p.add_argument("--tier", choices=("primary", "secondary", "tertiary"))
    p.add_argument("--width", type=int, default=56)
    p.add_argument("rest", nargs="*", help="evidence id(s) for show/verify/contradict")
    p.add_argument("--file", help="JSON file with evidence objects")
    p.add_argument("--claim")
    p.add_argument("--uri")
    p.add_argument("--type", dest="type", help="source type (url, file, repository, ...)")
    p.add_argument("--summary")
    p.add_argument("--quote")
    p.add_argument("--modality")
    p.add_argument("--basis")
    p.add_argument("--title")
    p.add_argument("--tags", action="append")
    p.add_argument("--contradicts", action="append")
    p.add_argument("--server")
    p.add_argument("--tool")
    p.add_argument("--capability")
    p.add_argument("--method", default="manual", choices=("cross_source", "primary_source", "reproduction", "code_inspection", "official_docs", "manual"))
    _add_common(p)
    p.set_defaults(func=cmd_evidence)

    p = sub.add_parser("tasks", help="list gap-driven tasks for the agent team")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--prompts", action="store_true", help="include agent contracts and prompt text")
    p.add_argument("--write", action="store_true", help="also save state/tasks.json")
    _add_common(p)
    p.set_defaults(func=cmd_tasks)

    p = sub.add_parser("dispatch", help="assignment bundle for a host agent")
    _add_common(p)
    p.set_defaults(func=cmd_dispatch)

    p = sub.add_parser("mcp", help="inspect and call local MCP servers")
    p.add_argument("action", choices=("caps", "providers", "doctor", "call", "stats", "clear-cache"))
    p.add_argument("server", nargs="?", help="capability or server name for call")
    p.add_argument("tool", nargs="?", help="tool name (with --raw: server tool)")
    p.add_argument("--capability", required=False)
    p.add_argument("--args", default="{}", help="JSON arguments for call")
    p.add_argument("--raw", action="store_true", help="call one server tool directly")
    p.add_argument("--show", type=int, default=0, help="characters of the answer to print")
    _add_common(p)
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("verify", help="cross-check claims before the gate")
    p.add_argument("--ids", nargs="*", help="specific evidence ids to confirm")
    p.add_argument("--apply", action="store_true", help="write verification status and re-propagate")
    p.add_argument("--min-domains", type=int, default=2)
    p.add_argument("--method", default="cross_source", choices=("cross_source", "primary_source", "reproduction", "code_inspection", "official_docs", "manual"))
    p.add_argument("--evidence-only", action="store_true", help="skip claim decisions")
    _add_common(p)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("gate", help="run the quality gate")
    p.add_argument("--strict", action="store_true", help="exit non-zero when the gate fails")
    p.add_argument("--save", action="store_true", help="write state/gate.json")
    _add_common(p)
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("run", help="execute the research loop end to end")
    p.add_argument("--iterations", type=int, default=0, help="max iterations (default: settings.loop.max_iterations)")
    p.add_argument("--max-reads", type=int, default=3, help="pages fetched per discovery query")
    p.add_argument("--verify", action="store_true", help="auto-verify corroborated evidence each iteration")
    p.add_argument("--no-memory", action="store_true", help="skip recalling and writing research patterns")
    p.add_argument("--dry-run", action="store_true", help="show the plan of work without calling MCP")
    p.add_argument("--quiet", action="store_true")
    _add_common(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("loop", help="show or steer the loop")
    p.add_argument("--show", action="store_true")
    p.add_argument("--abort", action="store_true", help="request a stop at the next checkpoint")
    p.add_argument("--reset", action="store_true")
    _add_common(p)
    p.set_defaults(func=cmd_loop)

    p = sub.add_parser("report", help="render output/report.md and its JSON twins")
    p.add_argument("--gate", action="store_true", help="re-run the quality gate for the report header")
    p.add_argument("--stdout", action="store_true", help="print instead of writing")
    _add_common(p)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("remember", help="long-term research memory (self-evolution)")
    p.add_argument("action", choices=("list", "recall", "add", "use", "promote", "sync", "stats"))
    p.add_argument("query", nargs="?", default="", help="recall query / add title")
    p.add_argument("--statement", default="", help="add: the pattern statement")
    p.add_argument("--kind", default="query", choices=("query", "source", "decomposition", "failure", "decision", "tool"))
    p.add_argument("--title")
    p.add_argument("--id")
    p.add_argument("--context", action="append")
    p.add_argument("--tags", action="append")
    p.add_argument("--confidence", type=float, default=0.6)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--failed", action="store_true", help="use: the pattern did not help")
    p.add_argument("--note")
    p.add_argument("--push", action="store_true", help="sync: also write through the memory MCP server")
    _add_common(p)
    p.set_defaults(func=cmd_remember)

    p = sub.add_parser("audit", help="consistency, coverage and privacy checks")
    _add_common(p)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("quickstart", help="5-minute onboarding: question -> workspace -> plan -> next command")
    p.add_argument("goal", nargs="*", help="the research question, in one sentence (any language)")
    p.add_argument("--at", help="workspace directory (default: runs/<slug> under cwd)")
    p.add_argument("--target", help="project under study for the context scan (default: cwd)")
    p.add_argument("--no-context", action="store_true", help="skip the local workspace scan")
    p.add_argument("--offline", dest="trailing_offline", action="store_true", help="same as the global --offline flag")
    _add_common(p)
    p.set_defaults(func=cmd_quickstart)

    p = sub.add_parser("install", help="copy the skill payload into an agent host's skills directory")
    p.add_argument("--dest", help="skills root (default: ~/.agents/skills)")
    p.add_argument("--dry-run", action="store_true", help="report what would be installed, change nothing")
    p.add_argument("--list", action="store_true", help="list supported skills roots and exit")
    p.add_argument("--force", action="store_true", help="replace a target directory that has no SKILL.md")
    _add_common(p)
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("skill-check", help="validate SKILL.md against the Agent Skills specification")
    p.add_argument("path", nargs="?", default=None, help="skill directory to check (default: this skill)")
    p.add_argument("--release", action="store_true", help="also run the open-source readiness audit")
    _add_common(p)
    p.set_defaults(func=cmd_skill_check)

    p = sub.add_parser("open-source", help="release gate: check publishability, or build a sanitized bundle")
    p.add_argument("action", choices=("check", "build"))
    p.add_argument("--out", help="build: destination directory (default: dist/nexus-deep-research)")
    p.add_argument("--no-archive", action="store_true", help="build: skip the .zip")
    _add_common(p)
    p.set_defaults(func=cmd_open_source)

    p = sub.add_parser("doctor", help="alias for `mcp doctor`")
    p.add_argument("--server")
    _add_common(p)
    p.set_defaults(func=lambda a, s: cmd_mcp(argparse.Namespace(action="doctor", server=a.server, json_out=a.json_out), s))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "trailing_offline", False):  # `quickstart ... --offline` reads naturally
        args.offline = True
    root = getattr(args, "path", None) or args.workspace
    # These commands inspect the MCP layer or the shared memory file, so they
    # must work outside a research workspace.
    try:
        if args.command == "init":
            session = Session.open(root, create=True, offline=args.offline, verbose=args.verbose)
        elif args.command in BARE_OK:
            session = Session.for_tools(root, offline=args.offline, verbose=args.verbose)
        else:
            session = Session.open(root, create=False, offline=args.offline, verbose=args.verbose)
    except Exception as error:  # noqa: BLE001 - CLI reports clean messages, no tracebacks
        print(f"error: {error}", file=sys.stderr)
        print(f"hint: run `nexus init {root}` first", file=sys.stderr)
        return EXIT_USAGE
    try:
        return int(args.func(args, session) or 0)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except McpError as error:
        print(f"MCP error [{error.error_class}]: {error}", file=sys.stderr)
        return EXIT_FAIL
    except (SystemExit,):
        raise
    except Exception as error:  # noqa: BLE001
        if args.verbose:
            raise
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
