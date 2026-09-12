"""Research Knowledge Graph engine.

Owns the durable research state: Goal -> Question -> Topic -> Entity ->
Evidence -> Claim, plus Risk/Decision/Gap nodes and their typed relations.
Implemented on the stdlib only (no networkx) so the skill stays installable
anywhere Python runs.

Responsibilities
----------------
* structural validation (node/edge types, referential integrity, cycles)
* hierarchical traversal used for coverage scoring
* evidence-driven confidence propagation into claims
* gap detection that drives the next loop iteration
* Mermaid / ASCII renderings used by the report
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence, Iterable

from .evidence_store import EvidenceStore
from .models import EDGE_TYPES, NODE_TYPES, Evidence, GraphEdge, GraphNode
from .util import (
    ASCII_TERM,
    ascii_terms,
    clamp,
    is_cjk_heavy,
    load_config,
    new_id,
    now_iso,
    read_json,
    rotate_pick,
    slugify,
    validate,
    write_json,
)

# Edges that make one node belong to another. ``derived_from`` is included so a
# claim's evidence rolls up into its topic and question; without it those nodes stay
# invisible to coverage(), the gap list never empties, and the loop cannot converge.
HIERARCHY_EDGES = ("decomposes_into", "implements", "refines", "relates_to", "derived_from")

# Shapes use ``{title}`` as a plain token (never ``str.format``): Mermaid itself
# needs literal braces for hexagons, which format() would try to interpret.
MERMAID_SHAPE = {
    "goal": '("{title}")',
    "question": '[["{title}"]]',
    "topic": '["{title}"]',
    "entity": "{{{title}}}",
    "evidence": '(["{title}"])',
    "claim": '[/"{title}"/]',
    "risk": '>"{title}"]',
    "decision": '(("{title}"))',
    "constraint": '[/"{title}"\\]',
    "gap": '(("{title}"))',
}


class GraphError(RuntimeError):
    """Raised on structurally invalid graph operations."""


@dataclass
class ResearchGraph:
    """The research state for one run."""

    path: Path
    goal: dict[str, Any] = field(default_factory=dict)
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    graph_id: str = field(default_factory=lambda: new_id("graph"))
    provenance: dict[str, Any] = field(default_factory=dict)
    _index: dict[str, list[GraphEdge]] | None = field(default=None, repr=False)

    # --------------------------------------------------------------- lifecycle
    @classmethod
    def create(cls, path: str | Path, objective: str, constraints: Iterable[str] = ()) -> "ResearchGraph":
        """Start a graph containing only the goal node."""
        objective = objective.strip()
        graph = cls(path=Path(path), goal={"id": "goal", "title": objective, "summary": objective, "constraints": list(constraints)})
        graph.nodes["goal"] = GraphNode(
            id="goal",
            type="goal",
            title=objective,
            body=objective,
            tags=list(constraints),
            created_by="planner",
        )
        return graph

    @classmethod
    def load(cls, path: str | Path) -> "ResearchGraph":
        """Load a graph from disk, tolerating an empty placeholder file."""
        target = Path(path)
        payload = read_json(target) or {}
        goal = payload.get("goal") or {}
        if not payload.get("nodes"):
            if goal.get("title"):
                return cls.create(target, goal["title"], goal.get("constraints", []))
            return cls(path=target)
        graph = cls(path=target)
        graph.graph_id = payload.get("graph_id", graph.graph_id)
        graph.created_at = payload.get("created_at", graph.created_at)
        graph.updated_at = payload.get("updated_at", graph.updated_at)
        graph.goal = goal
        graph.provenance = payload.get("provenance") or {}
        for item in payload.get("nodes", []):
            node = _node_from_dict(item)
            graph.nodes[node.id] = node
        for item in payload.get("edges", []):
            graph.edges.append(
                GraphEdge(
                    from_id=item["from"],
                    to_id=item["to"],
                    type=item["type"],
                    weight=float(item.get("weight", 1.0)),
                    note=item.get("note", ""),
                    created_by=item.get("created_by", "planner"),
                    created_at=item.get("created_at", now_iso()),
                )
            )
        graph._index = None
        return graph

    def save(self) -> Path:
        """Write the graph atomically and refuse to persist invalid state."""
        payload = self.to_dict()
        ok, message = validate(payload, "graph")
        if not ok:
            raise GraphError(f"refusing to save invalid graph: {message}")
        return write_json(self.path, payload)

    def to_dict(self) -> dict[str, Any]:
        self.updated_at = now_iso()
        goal = dict(self.goal) if self.goal else {}
        if not goal.get("title") and "goal" in self.nodes:
            goal = {"id": "goal", "title": self.nodes["goal"].title, "constraints": self.nodes["goal"].tags}
        goal.setdefault("id", "goal")
        goal.setdefault("title", self.nodes["goal"].title if "goal" in self.nodes else "research")
        return {
            "schema_version": 1,
            "graph_id": self.graph_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "goal": goal,
            "nodes": [node.to_dict() for node in sorted(self.nodes.values(), key=lambda item: (item.type, item.id))],
            "edges": [edge.to_dict() for edge in self.edges],
            "stats": self.stats(),
            "provenance": self.provenance,
        }

    # --------------------------------------------------------------------- CRUD
    def add_node(
        self,
        node_type: str,
        title: str,
        node_id: str | None = None,
        body: str = "",
        created_by: str = "planner",
        iteration: int = 0,
        **attrs: Any,
    ) -> GraphNode:
        """Add or merge a node. Existing ids are updated in place, never duplicated.

        ``parent=`` (with optional ``relation=``) also wires the decomposition
        edge, so callers cannot accidentally create orphan nodes.
        """
        if node_type not in NODE_TYPES:
            raise GraphError(f"unknown node type: {node_type}")
        parent = attrs.pop("parent", None)
        relation = attrs.pop("relation", None) or _default_relation(node_type)
        node_id = node_id or _derive_id(node_type, title)
        existing = self.nodes.get(node_id)
        if existing is not None:
            for key, value in attrs.items():
                if hasattr(existing, key) and value is not None:
                    setattr(existing, key, value)
            if title:
                # an analyst pass must be able to sharpen a claim it already created
                existing.title = title
            if body:
                existing.body = body
            existing.updated_at = now_iso()
            if parent:
                self.add_edge(parent, existing.id, relation, strict=False)
            return existing
        node = GraphNode(
            id=node_id,
            type=node_type,
            title=(title or "").strip() or node_type,
            body=body,
            iteration=iteration,
            created_by=created_by,
        )
        for key, value in attrs.items():
            if hasattr(node, key) and value is not None:
                setattr(node, key, value)
        if node_type == "claim" and not node.status:
            node.status = "proposed"
        self.nodes[node.id] = node
        self._invalidate()
        if parent:
            if parent not in self.nodes:
                raise GraphError(f"unknown parent node: {parent}")
            self.add_edge(parent, node.id, relation)
        return node

    def add_edge(
        self,
        from_id: str,
        to_id: str,
        edge_type: str,
        weight: float = 1.0,
        note: str = "",
        created_by: str = "planner",
        strict: bool = True,
    ) -> GraphEdge | None:
        """Connect two existing nodes; duplicate edges are collapsed."""
        if edge_type not in EDGE_TYPES:
            raise GraphError(f"unknown edge type: {edge_type}")
        if from_id not in self.nodes or to_id not in self.nodes:
            if strict:
                raise GraphError(f"dangling edge {from_id} -[{edge_type}]-> {to_id}")
            return None
        for edge in self.edges:
            if edge.key() == (from_id, to_id, edge_type):
                edge.weight = max(edge.weight, weight)
                return edge
        edge = GraphEdge(from_id, to_id, edge_type, clamp(weight), note, created_by)
        self.edges.append(edge)
        self._invalidate()
        return edge

    def remove_node(self, node_id: str, cascade: bool = True) -> bool:
        """Delete a node and, when asked, every edge touching it."""
        if self.nodes.pop(node_id, None) is None:
            return False
        if cascade:
            self.edges = [edge for edge in self.edges if node_id not in (edge.from_id, edge.to_id)]
        self._invalidate()
        return True

    def get(self, node_id: str) -> GraphNode | None:
        return self.nodes.get(node_id)

    def of_type(self, node_type: str) -> list[GraphNode]:
        return [node for node in self.nodes.values() if node.type == node_type]

    def by_tag(self, tag: str) -> list[GraphNode]:
        return [node for node in self.nodes.values() if tag in node.tags]

    # -------------------------------------------------------------- traversal
    @property
    def index(self) -> dict[str, list[GraphEdge]]:
        """Neighbour buckets; outgoing under ``id``, incoming under ``<id``."""
        if self._index is None:
            buckets: dict[str, list[GraphEdge]] = defaultdict(list)
            for edge in self.edges:
                buckets[edge.from_id].append(edge)
                buckets[f"<{edge.to_id}"].append(edge)
            self._index = buckets
        return self._index

    def _invalidate(self) -> None:
        self._index = None

    def children(self, node_id: str, edge_types: tuple[str, ...] | None = None) -> list[GraphNode]:
        """Outgoing neighbours, optionally filtered by edge type."""
        out: list[GraphNode] = []
        for edge in self.index.get(node_id, []):
            if edge_types and edge.type not in edge_types:
                continue
            node = self.nodes.get(edge.to_id)
            if node is not None and node not in out:
                out.append(node)
        return out

    def parents(self, node_id: str, edge_types: tuple[str, ...] | None = None) -> list[GraphNode]:
        """Incoming neighbours, optionally filtered by edge type."""
        out: list[GraphNode] = []
        for edge in self.index.get(f"<{node_id}", []):
            if edge_types and edge.type not in edge_types:
                continue
            node = self.nodes.get(edge.from_id)
            if node is not None and node not in out:
                out.append(node)
        return out

    def subtree(self, node_id: str) -> list[str]:
        """All descendants of ``node_id`` (breadth-first, cycle-safe), including itself."""
        seen: list[str] = []
        visited: set[str] = set()
        queue: deque[str] = deque([node_id])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            seen.append(current)
            for child in self.children(current, HIERARCHY_EDGES):
                if child.id not in visited:
                    queue.append(child.id)
        return seen

    def ancestors(self, node_id: str) -> list[GraphNode]:
        """First-parent chain upward, guarded against cycles."""
        chain: list[GraphNode] = []
        current = node_id
        seen: set[str] = {node_id}
        while len(chain) < 50:
            parents = self.parents(current, HIERARCHY_EDGES)
            if not parents or parents[0].id in seen:
                break
            chain.append(parents[0])
            seen.add(parents[0].id)
            current = parents[0].id
        return chain

    def path_to_goal(self, node_id: str) -> list[str]:
        """Readable breadcrumb from the goal down to ``node_id``."""
        return [node.id for node in reversed(self.ancestors(node_id))] + [node_id]

    def has_cycle(self) -> list[str]:
        """Return the first cycle found in the hierarchy, else an empty list."""
        state: dict[str, int] = {}
        trail: list[str] = []

        def visit(node_id: str) -> list[str]:
            state[node_id] = 1
            trail.append(node_id)
            for child in self.children(node_id, HIERARCHY_EDGES):
                if state.get(child.id, 0) == 1:
                    return trail[trail.index(child.id):] + [child.id]
                if state.get(child.id, 0) == 0:
                    found = visit(child.id)
                    if found:
                        return found
            trail.pop()
            state[node_id] = 2
            return []

        for node_id in list(self.nodes):
            if state.get(node_id, 0) == 0:
                cycle = visit(node_id)
                if cycle:
                    return cycle
        return []

    def depth(self) -> int:
        """Longest goal-to-leaf hierarchy length."""
        if "goal" not in self.nodes:
            return 0
        best = 0
        stack: list[tuple[str, int]] = [("goal", 1)]
        seen: set[str] = set()
        while stack:
            node_id, level = stack.pop()
            best = max(best, level)
            if node_id in seen:
                continue
            seen.add(node_id)
            for child in self.children(node_id, HIERARCHY_EDGES):
                stack.append((child.id, level + 1))
        return best

    # ---------------------------------------------------------------- evidence
    def attach_evidence(self, evidence: Evidence, node_id: str, store: EvidenceStore) -> GraphNode:
        """Mirror an evidence record into the graph and link it to ``node_id``."""
        if node_id not in self.nodes:
            raise GraphError(f"cannot attach evidence to unknown node: {node_id}")
        if store.get(evidence.id) is None:
            store.add(evidence)
        opposing = bool(evidence.contradicts) or node_id in evidence.contradicts
        node = self.add_node(
            "evidence",
            title=evidence.claim,
            node_id=evidence.id,
            body=evidence.summary,
            created_by="researcher",
            confidence=evidence.confidence,
        )
        self.add_edge(
            evidence.id,
            node_id,
            "contradicts" if opposing else "supports",
            weight=evidence.confidence,
            note=f"tier={evidence.source_tier} via={evidence.retrieved_via.get('server', 'n/a')}",
            created_by="researcher",
        )
        target = self.nodes[node_id]
        if evidence.id not in target.evidence_refs:
            target.evidence_refs.append(evidence.id)
        target.updated_at = now_iso()
        return node

    def evidence_for(self, node_id: str, store: EvidenceStore, include_subtree: bool = True) -> list[Evidence]:
        """Evidence attached to a node, optionally pulled up from its subtree."""
        scope = self.subtree(node_id) if include_subtree else [node_id]
        found: dict[str, Evidence] = {}
        for member in scope:
            for record in store.for_node(member):
                found[record.id] = record
        return sorted(found.values(), key=lambda record: record.id)

    # ----------------------------------------------------------------- scoring
    def coverage(self, node_id: str, store: EvidenceStore) -> dict[str, Any]:
        """How well one node is researched: quality, diversity and volume blended."""
        pool = self.evidence_for(node_id, store)
        if not pool:
            return {
                "node_id": node_id,
                "coverage": 0.0,
                "evidence": 0,
                "domains": [],
                "mean_quality": 0.0,
                "has_primary": False,
            }
        scores = [store.quality_score(record) for record in pool]
        domains = sorted({key for key in (record.independence_key for record in pool) if key})
        mean_quality = sum(scores) / len(scores)
        coverage = clamp(0.45 * mean_quality + 0.30 * clamp(len(domains) / 3.0) + 0.25 * clamp(len(pool) / 4.0))
        return {
            "node_id": node_id,
            "coverage": round(coverage, 3),
            "evidence": len(pool),
            "domains": domains,
            "mean_quality": round(mean_quality, 3),
            "has_primary": any(record.source_tier == "primary" for record in pool),
        }

    def claim_confidence(self, node_id: str, store: EvidenceStore, threshold: float | None = None) -> dict[str, Any]:
        """Compute a claim's confidence from its evidence, deterministically.

        ``0.6 * best + 0.4 * mean`` keeps one strong source useful without
        letting volume alone inflate confidence; corroboration across
        independent domains and verifier confirmation add credit, while
        missing primary sources and unresolved contradictions subtract it.
        """
        supporting = [record for record in store.for_node(node_id) if node_id not in record.contradicts]
        opposing = store.contradicting(node_id)
        if not supporting:
            return {"confidence": 0.0, "status": "proposed", "sources": 0, "domains": [], "contradictions": len(opposing), "reason": "no evidence"}
        weighted = [record.confidence * store.tier_weight(record.source_tier) for record in supporting]
        best, mean = max(weighted), sum(weighted) / len(weighted)
        domains = sorted({key for key in (record.independence_key for record in supporting) if key})
        confirmed = any(record.verification.get("status") == "confirmed" for record in supporting)
        score = 0.6 * best + 0.4 * mean
        score += 0.10 * min(2, max(0, len(domains) - 1))
        score += 0.10 if confirmed else 0.0
        if not any(record.source_tier == "primary" for record in supporting):
            score -= 0.08
        score -= min(0.30, 0.15 * len(opposing))
        score = clamp(score)
        limit = threshold if threshold is not None else load_config("quality").get("gate", {}).get("confidence_threshold", 0.85)
        if opposing:
            status = "contested"
        elif score >= limit and len(domains) >= 2 and confirmed:
            status = "verified"
        elif score >= 0.5:
            status = "supported"
        else:
            status = "proposed"
        return {
            "confidence": round(score, 3),
            "status": status,
            "sources": len(supporting),
            "domains": domains,
            "contradictions": len(opposing),
        }

    def propagate(self, store: EvidenceStore, threshold: float | None = None) -> dict[str, Any]:
        """Refresh confidence/status on every claim and score the whole graph."""
        detail: dict[str, dict[str, Any]] = {}
        for claim in self.of_type("claim"):
            computed = self.claim_confidence(claim.id, store, threshold)
            claim.confidence = computed["confidence"]
            claim.status = computed["status"]
            claim.updated_at = now_iso()
            detail[claim.id] = computed
        claims = list(detail.values())
        overall = clamp(sum(item["confidence"] for item in claims) / len(claims)) if claims else 0.0
        covered = [self.coverage(node.id, store)["coverage"] for node in self.of_type("question")]
        return {
            "claims": len(detail),
            "confidence": round(overall, 3),
            "question_coverage": round(sum(covered) / len(covered), 3) if covered else 0.0,
            "detail": detail,
        }

    def overall_confidence(self) -> float:
        """Mean confidence across claims - the metric the loop stops on."""
        claims = self.of_type("claim")
        if not claims:
            return 0.0
        return round(sum(claim.confidence for claim in claims) / len(claims), 3)

    # ------------------------------------------------------------------- gaps
    def gaps(self, store: EvidenceStore, coverage_threshold: float = 0.7, limit: int = 10) -> list[dict[str, Any]]:
        # The goal usually names the concrete technologies; when a Chinese facet topic
        # supplies no ASCII terms, gap queries fall back to those instead of prose.
        goal = self.nodes.get("goal")
        seed_terms = ascii_terms(goal.title, limit=4) if goal else []
        """Nodes that still need research, worst first, with next-step hints."""
        checks = load_config("quality").get("checks", {})
        min_sources = checks.get("source_diversity", {}).get("min_sources", 5)
        min_domains = checks.get("source_diversity", {}).get("min_independent_domains", 3)
        gaps: list[dict[str, Any]] = []
        for node in self.of_type("question") + self.of_type("topic"):
            report = self.coverage(node.id, store)
            if report["coverage"] >= coverage_threshold:
                continue
            missing: list[str] = []
            if report["evidence"] == 0:
                missing.append("no evidence yet")
            elif report["evidence"] < max(2, min_sources // 2):
                missing.append("needs corroboration")
            if len(report["domains"]) < min_domains:
                missing.append(f"need {min_domains} independent domains, have {len(report['domains'])}")
            if not report["has_primary"]:
                missing.append("no primary source")
            gaps.append(
                {
                    "node_id": node.id,
                    "type": node.type,
                    "title": node.title,
                    "coverage": report["coverage"],
                    "priority": node.priority,
                    "path": " > ".join(self.path_to_goal(node.id)),
                    "missing": missing,
                    "suggested_queries": _suggest_queries(node, missing, seed_terms),
                    "suggested_capability": _suggest_capability(node, missing),
                }
            )
        for claim in self.of_type("claim"):
            computed = self.claim_confidence(claim.id, store)
            if computed["status"] == "contested" or computed["confidence"] < 0.5:
                gaps.append(
                    {
                        "node_id": claim.id,
                        "type": "claim",
                        "title": claim.title,
                        "coverage": computed["confidence"],
                        "priority": claim.priority,
                        "path": " > ".join(self.path_to_goal(claim.id)),
                        "missing": [
                            "contradiction unresolved"
                            if computed["status"] == "contested"
                            else "claim unverified"
                        ],
                        "suggested_queries": _suggest_queries(claim, ["needs corroboration"], seed_terms),
                        "suggested_capability": "web.read",
                    }
                )
        gaps.sort(key=lambda gap: (gap["coverage"], -gap["priority"]))
        return gaps[:limit]

    def contradiction_pairs(self, store: EvidenceStore) -> list[tuple[str, str]]:
        """Evidence pairs flagged as conflicting, for the verification stage."""
        pairs: set[tuple[str, str]] = set()
        for record in store.all():
            for other in record.contradicts:
                if store.get(other) is not None:
                    pairs.add(tuple(sorted((record.id, other))))
        return sorted(pairs)  # type: ignore[arg-type]

    # --------------------------------------------------------------- rendering
    def render_mermaid(self, max_nodes: int = 60) -> str:
        """Mermaid flowchart of the graph, trimmed to the most important nodes."""
        rank = {"goal": 0, "question": 1, "topic": 2, "claim": 3, "decision": 4, "risk": 5, "entity": 6, "constraint": 7, "gap": 8, "evidence": 9}
        ordered = sorted(self.nodes.values(), key=lambda node: (rank.get(node.type, 9), -node.weight))[:max_nodes]
        keep = {node.id for node in ordered}
        lines = ["flowchart TD"]
        for node in ordered:
            label = _escape(node.title)[:44]
            shape = MERMAID_SHAPE.get(node.type, '["{title}"]')
            lines.append(f"  {node.id}{shape.replace('{title}', label)}")
        for edge in self.edges:
            if edge.from_id not in keep or edge.to_id not in keep:
                continue
            arrow = "-.->" if edge.type == "contradicts" else "-->"
            lines.append(f"  {edge.from_id} {arrow}|{edge.type}| {edge.to_id}")
        return "\n".join(lines)

    def render_tree(self) -> str:
        """ASCII tree following the hierarchy edges from the goal."""
        if "goal" not in self.nodes:
            return "(empty graph)"
        lines: list[str] = []

        def walk(node_id: str, prefix: str, is_last: bool, root: bool = False) -> None:
            node = self.nodes[node_id]
            connector = "" if root else ("└── " if is_last else "├── ")
            badge = "" if node.type in {"goal", "topic", "question"} else f" [{node.type}]"
            score = f" ({node.confidence:.2f})" if node.type == "claim" else ""
            lines.append(f"{prefix}{connector}{node.title[:60]}{badge}{score}")
            children = self.children(node_id, HIERARCHY_EDGES)
            next_prefix = prefix + ("" if root else ("    " if is_last else "│   "))
            for index, child in enumerate(children):
                walk(child.id, next_prefix, index == len(children) - 1)

        walk("goal", "", True, root=True)
        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        """Counts used by the CLI, quality gate and run manifest."""
        by_type: dict[str, int] = defaultdict(int)
        for node in self.nodes.values():
            by_type[node.type] += 1
        edges_by_type: dict[str, int] = defaultdict(int)
        for edge in self.edges:
            edges_by_type[edge.type] += 1
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "depth": self.depth(),
            "by_type": dict(by_type),
            "edges_by_type": dict(edges_by_type),
        }

    def validate_now(self) -> tuple[bool, str]:
        """Full structural + schema check used by ``nexus audit``."""
        ok, message = validate(self.to_dict(), "graph")
        if not ok:
            return False, message
        for edge in self.edges:
            if edge.from_id not in self.nodes or edge.to_id not in self.nodes:
                return False, f"dangling edge {edge.from_id} -> {edge.to_id}"
        cycle = self.has_cycle()
        if cycle:
            return False, f"hierarchy cycle: {' -> '.join(cycle)}"
        return True, "ok"

    def snapshot(self, iteration: int) -> dict[str, Any]:
        """Compact history entry used for research replay."""
        return {
            "iteration": iteration,
            "taken_at": now_iso(),
            "stats": self.stats(),
            "confidence": self.overall_confidence(),
        }


# ---------------------------------------------------------------------- helpers
def _default_relation(node_type: str) -> str:
    """How a freshly added node hangs off its parent by default."""
    return "derived_from" if node_type in {"claim", "decision", "risk", "constraint", "evidence"} else "decomposes_into"


def _derive_id(node_type: str, title: str) -> str:
    slug = slugify(title, 36)
    return f"{node_type[:3]}-{slug}" if slug != "topic" or not title else f"{node_type[:3]}-{new_id('x')}"


def _node_from_dict(item: dict[str, Any]) -> GraphNode:
    known = set(GraphNode.__dataclass_fields__)
    payload = {key: value for key, value in item.items() if key in known}
    payload.setdefault("type", "topic")
    payload.setdefault("title", str(payload.get("id", "node")))
    return GraphNode(**payload)


def _escape(text: str) -> str:
    return text.replace('"', "'").replace("[", "(").replace("]", ")").replace("{", "(").replace("}", ")")


_QUERY_STOPWORDS = {"the", "a", "an", "of", "and", "or", "for", "with", "how", "what", "why", "is", "are", "to", "in", "vs"}


def _keywords(node: GraphNode, seed_terms: Sequence[str] = ()) -> list[str]:
    """Query terms for a gap. Topics carry "question -> facet" in their body."""
    title = node.title
    question, marker, facet = (node.body or "").partition("→")
    if marker and facet.strip():
        title = f"{facet.strip()} {question.strip()}"
    terms = ascii_terms(title, limit=6)
    if terms:
        # A Chinese question must not be shipped verbatim to an English-only engine;
        # the facet/topic terms carry the intent instead.
        if is_cjk_heavy(title):
            return terms[:3]  # index-scoped engines degrade past ~3 terms
        # A Chinese facet label ("架构与关键机制如何运作") is prose, not a search term:
        # shipped verbatim it turns the query into junk. Only ASCII tokens survive.
        topic = [
            word
            for word in title.split()
            if len(word) > 2 and word.lower() not in _QUERY_STOPWORDS and ASCII_TERM.fullmatch(word)
        ]
        return (topic[:2] + [term for term in terms if term.lower() not in topic][:4])[:6]
    if seed_terms:
        return list(seed_terms)
    words = [word for word in title.replace("/", " ").split() if len(word) > 2 and word.lower() not in _QUERY_STOPWORDS]
    return words[:6] or [node.title]


def _suggest_queries(node: GraphNode, missing: list[str], seed_terms: Sequence[str] = ()) -> list[str]:
    """Turn a gap into concrete search queries for the researcher."""
    terms = _keywords(node, seed_terms)
    base = " ".join(terms)
    # Padding a 3-4 term query with "official documentation" is what makes
    # bing/google fall back to popular-but-irrelevant pages (measured on this host);
    # past two terms the suffix costs more than it buys, so narrow instead.
    terse = len(terms) > 2
    hints = {
        "no evidence yet": [f"{base} official documentation", f"{base} 2026 comparison"],
        "needs corroboration": [f"{base} benchmark", f"{base} production experience"],
        "no primary source": [f"{base} specification github", f"{base} RFC"],
    }
    queries: list[str] = [base, " ".join(terms[:2])] if terse else []
    for item in missing:
        for key, values in hints.items():
            if key in item and not terse:
                queries.extend(values)
    return list(dict.fromkeys([query for query in queries if query.strip()])) or [f"{base} documentation"]


def _suggest_capability(node: GraphNode, missing: list[str]) -> str:
    """Which MCP capability should close this gap."""
    title = node.title.lower()
    if any(token in title for token in ("库", "api", "framework", "sdk", "library")):
        return "docs.library"
    if any(token in title for token in ("repo", "github", "project", "开源")):
        return "code.repository"
    if node.type == "claim":
        return "web.read"
    return "web.discovery"
