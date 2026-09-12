"""Typed records exchanged between agents (AGENTS.md section 7).

Agents never mutate each other's state; they emit these records, and the
runtime is the only writer of the Research Graph and Evidence Store.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

from .util import new_id, now_iso

NODE_TYPES = (
    "goal",
    "question",
    "topic",
    "entity",
    "evidence",
    "claim",
    "risk",
    "decision",
    "constraint",
    "gap",
)

EDGE_TYPES = (
    "decomposes_into",
    "depends_on",
    "supports",
    "contradicts",
    "derived_from",
    "implements",
    "relates_to",
    "answers",
    "references",
    "blocks",
    "mitigates",
    "refines",
)


@dataclass
class GraphNode:
    """A single node in the Research Knowledge Graph."""

    id: str
    type: str
    title: str
    body: str = ""
    status: str | None = None
    priority: int = 1
    confidence: float = 0.0
    weight: float = 1.0
    iteration: int = 0
    entity_type: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    decision: dict[str, Any] | None = None
    risk_level: str | None = None
    mitigation: str | None = None
    tags: list[str] = field(default_factory=list)
    created_by: str = "planner"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        payload = {key: value for key, value in raw.items() if value not in (None, [], {}, "")}
        if self.type == "claim":
            payload["status"] = self.status or "proposed"
            payload["evidence_refs"] = list(self.evidence_refs)
        return payload


@dataclass
class GraphEdge:
    """A typed relation between two nodes."""

    from_id: str
    to_id: str
    type: str
    weight: float = 1.0
    note: str = ""
    created_by: str = "planner"
    created_at: str = field(default_factory=now_iso)

    def key(self) -> tuple[str, str, str]:
        return (self.from_id, self.to_id, self.type)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "from": self.from_id,
            "to": self.to_id,
            "type": self.type,
            "weight": self.weight,
        }
        if self.note:
            payload["note"] = self.note
        payload["created_by"] = self.created_by
        payload["created_at"] = self.created_at
        return payload


@dataclass
class Evidence:
    """One citable finding, always tied to a concrete source."""

    claim: str
    source_type: str
    source_uri: str = ""
    id: str = field(default_factory=lambda: new_id("ev"))
    summary: str = ""
    quote: str = ""
    domain: str = ""
    title: str = ""
    modality: str = "text"
    source_tier: str = "secondary"
    confidence: float = 0.5
    confidence_basis: str = ""
    supports: list[str] = field(default_factory=list)
    contradicts: list[str] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    retrieved_via: dict[str, Any] = field(default_factory=dict)
    retrieved_at: str = field(default_factory=now_iso)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        source: dict[str, Any] = {"type": self.source_type}
        if self.source_uri:
            source["uri"] = self.source_uri
        if self.domain:
            source["domain"] = self.domain
        if self.title:
            source["title"] = self.title
        payload: dict[str, Any] = {
            "id": self.id,
            "claim": self.claim,
            "source": source,
            "retrieved_at": self.retrieved_at,
            "confidence": round(self.confidence, 3),
            "modality": self.modality,
            "source_tier": self.source_tier,
        }
        for key in ("summary", "quote", "confidence_basis"):
            if getattr(self, key):
                payload[key] = getattr(self, key)
        for key in ("supports", "contradicts", "quality_flags", "tags"):
            if getattr(self, key):
                payload[key] = getattr(self, key)
        for key in ("verification", "retrieved_via"):
            if getattr(self, key):
                payload[key] = getattr(self, key)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Evidence":
        """Accept both the schema shape (nested ``source``) and a flat record."""
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        return cls(
            id=payload.get("id") or new_id("ev"),
            claim=payload.get("claim", ""),
            source_type=payload.get("source_type") or source.get("type", "url"),
            source_uri=payload.get("source_uri") or source.get("uri", ""),
            domain=payload.get("domain") or source.get("domain", ""),
            title=payload.get("title") or source.get("title", ""),
            summary=payload.get("summary", ""),
            quote=payload.get("quote", ""),
            modality=payload.get("modality", "text"),
            source_tier=payload.get("source_tier", "secondary"),
            confidence=float(payload.get("confidence", 0.5)),
            confidence_basis=payload.get("confidence_basis", ""),
            supports=list(payload.get("supports") or []),
            contradicts=list(payload.get("contradicts") or []),
            quality_flags=list(payload.get("quality_flags") or []),
            verification=dict(payload.get("verification") or {}),
            retrieved_via=dict(payload.get("retrieved_via") or {}),
            retrieved_at=payload.get("retrieved_at", now_iso()),
            tags=list(payload.get("tags") or []),
        )

    @property
    def independence_key(self) -> str:
        """What makes this record a separate corroborating source.

        Web sources are keyed by registrable host. Local workspace files are not a
        publisher, so all of them collapse into one ``workspace`` bucket: a claim
        backed by four repo files still counts as one corroborating source, while a
        claim backed by one file plus one vendor doc counts as two.
        """
        host = self.domain or self.host_of(self.source_uri)
        if host:
            return host
        return "workspace" if self.source_type in {"file", "code"} else ""

    @staticmethod
    def host_of(uri: str) -> str:
        """Registrable host so ``a.dev/x`` and ``a.dev/y`` count as one domain."""
        if not uri:
            return ""
        parsed = urlparse(uri) if "://" in uri else None
        netloc = parsed.netloc if parsed else uri.split("/")[0]
        host = netloc.split(":")[0].lower()
        parts = host.split(".")
        if len(parts) > 2 and parts[-2] in {"co", "com", "org", "net", "gov", "edu", "ac"}:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:]) if len(parts) > 1 else host


@dataclass
class ResearchTask:
    """A unit of delegated work for exactly one agent."""

    agent: str
    objective: str
    capability: str
    id: str = field(default_factory=lambda: new_id("task"))
    question_ref: str = ""
    iteration: int = 0
    priority: int = 1
    status: str = "pending"
    depends_on: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    acceptance: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    created_by: str = "planner"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        return {key: value for key, value in raw.items() if value not in (None, [], {}, "")}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ResearchTask":
        known = set(cls.__dataclass_fields__)
        clean = {key: value for key, value in payload.items() if key in known}
        return cls(**clean)


@dataclass
class GraphUpdate:
    """A proposed mutation to the graph, applied by the runtime only."""

    add_nodes: list[dict[str, Any]] = field(default_factory=list)
    add_edges: list[dict[str, Any]] = field(default_factory=list)
    remove_ids: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "add_nodes": self.add_nodes,
            "add_edges": self.add_edges,
            "remove_ids": self.remove_ids,
            "reason": self.reason,
        }


@dataclass
class AgentMessage:
    """Envelope for every inter-agent exchange."""

    sender: str
    recipient: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("msg"))
    iteration: int = 0
    task_id: str = ""
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "from": self.sender,
            "to": self.recipient,
            "kind": self.kind,
            "iteration": self.iteration,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "payload": self.payload,
        }
