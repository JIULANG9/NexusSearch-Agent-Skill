"""Long-term research memory and the self-evolution substrate.

Two layers, both local-first:

1. ``memory/research-patterns.json`` inside the skill repo - curated,
   human-reviewable patterns (query shapes, source recipes, failure lessons).
   This is what makes the skill *evolve*: every finished run can propose a
   pattern, and every new run recalls the best matching ones.
2. The optional ``memory`` MCP server - machine-readable entity graph for a
   given research domain, written through ``mcp_router`` when available.

Recall is a cheap deterministic ranking (no embeddings needed), so the loop
never depends on a network call to remember what it already learned.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .util import SKILL_ROOT, load_config, now_iso, read_json, write_json

DEFAULT_PATTERN_FILE = SKILL_ROOT / "memory" / "research-patterns.json"
KINDS = ("query", "source", "decomposition", "failure", "decision", "tool")


@dataclass
class Pattern:
    """One reusable research lesson."""

    id: str
    kind: str
    title: str
    statement: str
    context: list[str] = field(default_factory=list)
    confidence: float = 0.6
    uses: int = 0
    wins: int = 0
    source_run: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "statement": self.statement,
            "context": self.context,
            "confidence": round(self.confidence, 3),
            "uses": self.uses,
            "wins": self.wins,
            "source_run": self.source_run,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tags": self.tags,
        }
        return {key: value for key, value in payload.items() if value not in (None, [], "", 0)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Pattern":
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in payload.items() if key in known})

    @property
    def success_rate(self) -> float:
        return self.wins / self.uses if self.uses else 0.0


@dataclass
class MemoryStore:
    """Read, rank, and append research patterns."""

    path: Path = DEFAULT_PATTERN_FILE
    patterns: list[Pattern] = field(default_factory=list)
    schema_version: int = 1

    # ------------------------------------------------------------------ loading
    @classmethod
    def open(cls, path: str | Path | None = None) -> "MemoryStore":
        target = Path(path) if path else DEFAULT_PATTERN_FILE
        payload = read_json(target, None)
        if not isinstance(payload, dict):
            return cls(path=target, patterns=[])
        patterns: list[Pattern] = []
        for item in payload.get("patterns") or []:
            if isinstance(item, dict) and item.get("id"):
                try:
                    patterns.append(Pattern.from_dict(item))
                except Exception:  # noqa: BLE001 - one bad record must not break recall
                    continue
        return cls(path=target, patterns=patterns, schema_version=int(payload.get("schema_version") or 1))

    def save(self) -> Path:
        payload = {
            "schema_version": self.schema_version,
            "updated_at": now_iso(),
            "count": len(self.patterns),
            "patterns": [item.to_dict() for item in sorted(self.patterns, key=lambda one: -one.confidence)],
        }
        return write_json(self.path, payload)

    # ------------------------------------------------------------------- recall
    def recall(self, query: str, kind: str | None = None, limit: int = 5) -> list[Pattern]:
        """Rank patterns by token overlap, kind match and observed success."""
        tokens = _tokens(query)
        scored: list[tuple[float, Pattern]] = []
        for pattern in self.patterns:
            if kind and pattern.kind != kind:
                continue
            haystack = _tokens(
                " ".join([pattern.title, pattern.statement, " ".join(pattern.context), " ".join(pattern.tags)])
            )
            overlap = len(tokens & haystack) / len(tokens) if tokens else 0.0
            score = 0.6 * overlap + 0.25 * pattern.confidence + 0.15 * pattern.success_rate
            if kind and pattern.kind == kind:
                score += 0.1
            if score > 0:
                scored.append((score, pattern))
        scored.sort(key=lambda item: -item[0])
        return [pattern for _score, pattern in scored[:limit]]

    def get(self, pattern_id: str) -> Pattern | None:
        return next((item for item in self.patterns if item.id == pattern_id), None)

    # ----------------------------------------------------------------- writing
    def remember(
        self,
        kind: str,
        title: str,
        statement: str,
        *,
        context: Iterable[str] = (),
        tags: Iterable[str] = (),
        confidence: float = 0.6,
        source_run: str = "",
    ) -> Pattern:
        """Add or merge a pattern. Re-adding the same title raises its confidence."""
        kind = kind if kind in KINDS else "query"
        existing = next(
            (item for item in self.patterns if item.title.strip().lower() == title.strip().lower()), None
        )
        if existing is not None:
            existing.confidence = min(0.99, existing.confidence + 0.05)
            existing.uses += 1
            existing.statement = statement or existing.statement
            existing.context = sorted(set(existing.context) | {str(item) for item in context})
            existing.tags = sorted(set(existing.tags) | {str(item) for item in tags})
            existing.updated_at = now_iso()
            return existing
        pattern = Pattern(
            id=f"pat_{len(self.patterns) + 1:04d}",
            kind=kind,
            title=title.strip()[:120],
            statement=statement.strip(),
            context=sorted({str(item) for item in context}),
            tags=sorted({str(item) for item in tags}),
            confidence=max(0.05, min(0.99, confidence)),
            source_run=source_run,
        )
        self.patterns.append(pattern)
        return pattern

    def record_use(self, pattern_id: str, succeeded: bool) -> Pattern | None:
        """Feedback loop: did applying this pattern actually help?"""
        pattern = self.get(pattern_id)
        if pattern is None:
            return None
        pattern.uses += 1
        pattern.wins += 1 if succeeded else 0
        pattern.confidence = max(0.05, min(0.99, pattern.confidence + (0.05 if succeeded else -0.05)))
        pattern.updated_at = now_iso()
        return pattern

    def promote_from_run(
        self,
        gate_report: dict[str, Any],
        loop_log: Iterable[dict[str, Any]] = (),
        *,
        source_run: str = "",
    ) -> list[Pattern]:
        """Distil a finished run into candidate patterns (self-evolution)."""
        created: list[Pattern] = []
        passed = bool(gate_report.get("passed"))
        metrics = gate_report.get("metrics") or {}
        created.append(
            self.remember(
                "decision",
                f"Run {source_run or 'latest'}: gate {'passed' if passed else 'failed'}",
                "Evidence shape at gate time: "
                + json.dumps(
                    {key: metrics.get(key) for key in ("total", "domains", "verified", "tiers") if key in metrics},
                    ensure_ascii=False,
                ),
                context=["quality gate"],
                confidence=0.7 if passed else 0.4,
                source_run=source_run,
            )
        )
        for check in gate_report.get("checks") or []:
            if check.get("passed"):
                continue
            created.append(
                self.remember(
                    "failure",
                    f"Gate check often fails: {check.get('id')}",
                    str(check.get("remediation") or check.get("detail") or ""),
                    context=["quality gate", str(check.get("id"))],
                    confidence=0.55,
                    source_run=source_run,
                )
            )
        for entry in loop_log:
            if int(entry.get("new_evidence") or 0) >= 3 and float(entry.get("gain") or 0) >= 0.05:
                for task in entry.get("tasks") or []:
                    queries = (task.get("params") or {}).get("queries") or []
                    for query in queries[:1]:
                        created.append(
                            self.remember(
                                "query",
                                f"Productive query: {query}",
                                f"iteration {entry.get('index')} added {entry.get('new_evidence')} sources "
                                f"(capability {task.get('capability')})",
                                context=[str(task.get("capability") or ""), str(task.get("agent") or "")],
                                tags=["high-yield"],
                                confidence=0.65,
                                source_run=source_run,
                            )
                        )
        return created

    # --------------------------------------------------------------- statistics
    def stats(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for pattern in self.patterns:
            by_kind[pattern.kind] = by_kind.get(pattern.kind, 0) + 1
        return {
            "total": len(self.patterns),
            "by_kind": by_kind,
            "mean_confidence": round(sum(p.confidence for p in self.patterns) / len(self.patterns), 3)
            if self.patterns
            else 0.0,
            "path": str(self.path),
        }

    # --------------------------------------------------------------------- MCP
    def export_entities(self, limit: int = 40) -> list[dict[str, Any]]:
        """Shape patterns for the ``memory`` MCP ``create_entities`` call."""
        entities: list[dict[str, Any]] = []
        for pattern in sorted(self.patterns, key=lambda item: -item.confidence)[:limit]:
            observations = [
                pattern.statement,
                f"confidence={pattern.confidence:.2f}",
                f"uses={pattern.uses} wins={pattern.wins}",
            ]
            if pattern.source_run:
                observations.append(f"run={pattern.source_run}")
            entities.append(
                {
                    "name": pattern.title,
                    "entityType": f"research-pattern/{pattern.kind}",
                    "observations": [item for item in observations if item],
                }
            )
        return entities

    def sync_observations(self, pattern_id: str, note: str) -> dict[str, Any]:
        """Return the MCP payload that records an observation for a pattern."""
        pattern = self.get(pattern_id)
        if pattern is None:
            return {}
        pattern.updated_at = now_iso()
        return {"entityName": pattern.title, "contents": [note]}


def _tokens(text: str) -> set[str]:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (text or "").lower())
    latin = {word for word in cleaned.split() if len(word) > 2}
    cjk = set(re.findall(r"[\u4e00-\u9fff]", text or ""))
    return latin | cjk


def memory_enabled() -> bool:
    settings = load_config("settings")
    return bool((settings.get("persistence") or {}).get("sqlite_index", True))
