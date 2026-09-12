"""Research Loop controller (design doc section 6, PRP section 6).

The loop is a state machine over the Research Graph:

    analyze_gap -> generate_tasks -> dispatch -> collect -> update_graph
                -> verify_claims -> (stop? | next iteration)

The controller never calls an LLM itself. It computes *what still needs
research*, hands work to agents through ``next_actions`` records, and absorbs
whatever the agent layer returns via :meth:`LoopController.collect`. That keeps
the loop deterministic, replayable (``agents/logs/loop.jsonl``) and testable,
and lets the same runtime drive Claude, Codex, Qoder or a human operator.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .evidence_store import EvidenceStore
from .graph_engine import ResearchGraph
from .models import Evidence, GraphUpdate, ResearchTask
from .quality_gate import GateReport, QualityGate
from .util import clamp, load_config, now_iso, validate


class LoopError(RuntimeError):
    """Raised when the loop cannot advance."""


STAGES = (
    "analyze_gap",
    "generate_tasks",
    "dispatch",
    "collect",
    "update_graph",
    "verify_claims",
    "gate",
    "stop",
)


@dataclass
class LoopIteration:
    """One pass of the loop, kept for replay and reporting."""

    index: int
    started_at: str = field(default_factory=now_iso)
    finished_at: str = ""
    stage: str = "analyze_gap"
    confidence_before: float = 0.0
    confidence_after: float = 0.0
    coverage_before: float = 0.0
    coverage_after: float = 0.0
    new_evidence: int = 0
    new_nodes: int = 0
    tasks: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=dict)
    stop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stage": self.stage,
            "confidence": [self.confidence_before, self.confidence_after],
            "coverage": [self.coverage_before, self.coverage_after],
            "new_evidence": self.new_evidence,
            "new_nodes": self.new_nodes,
            "gain": round(self.confidence_after - self.confidence_before, 4),
            "tasks": self.tasks,
            "gaps": self.gaps[:6],
            "gate": self.gate,
            "stop_reason": self.stop_reason,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LoopIteration":
        """Rehydrate a persisted iteration; partial records keep their defaults."""

        def pair(key: str) -> tuple[float, float]:
            values = payload.get(key)
            if not isinstance(values, (list, tuple)):
                values = []
            numbers = [float(item) for item in values if isinstance(item, (int, float))]
            numbers = (numbers + [0.0, 0.0])[:2]
            return numbers[0], numbers[1]

        confidence_before, confidence_after = pair("confidence")
        coverage_before, coverage_after = pair("coverage")
        return cls(
            index=int(payload.get("index") or 0),
            started_at=str(payload.get("started_at") or ""),
            finished_at=str(payload.get("finished_at") or ""),
            stage=str(payload.get("stage") or "analyze_gap"),
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            coverage_before=coverage_before,
            coverage_after=coverage_after,
            new_evidence=int(payload.get("new_evidence") or 0),
            new_nodes=int(payload.get("new_nodes") or 0),
            tasks=list(payload.get("tasks") or []),
            gaps=list(payload.get("gaps") or []),
            gate=dict(payload.get("gate") or {}),
            stop_reason=str(payload.get("stop_reason") or ""),
        )


@dataclass
class LoopController:
    """Own the loop state, budgets and stop conditions."""

    graph: ResearchGraph
    store: EvidenceStore
    workspace: Any | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    gate: QualityGate = field(default_factory=QualityGate)
    iteration: int = 0
    history: list[LoopIteration] = field(default_factory=list)
    tool_calls: int = 0
    started_monotonic: float = field(default_factory=time.monotonic)
    aborted: bool = False

    # ------------------------------------------------------------------ setup
    @classmethod
    def create(
        cls,
        graph: ResearchGraph,
        store: EvidenceStore,
        workspace: Any | None = None,
        settings: dict[str, Any] | None = None,
    ) -> "LoopController":
        controller = cls(
            graph=graph,
            store=store,
            workspace=workspace,
            settings=settings or load_config("settings"),
            gate=QualityGate(),
        )
        controller.restore()
        return controller

    def restore(self) -> None:
        """Rehydrate counters and history from ``state/state.json``.

        A resumed run must not get a fresh iteration budget, and ``loop --show``
        has to render the iterations that already happened.
        """
        if self.workspace is None:
            return
        try:
            state = self.workspace.read_state()
        except Exception:  # noqa: BLE001 - a damaged state file must not block a read
            return
        self.iteration = int(state.get("iteration") or 0)
        self.tool_calls = int(state.get("tool_calls") or 0)
        entries = state.get("history")
        if isinstance(entries, list):
            self.history = [LoopIteration.from_dict(item) for item in entries if isinstance(item, dict)]

    def option(self, key: str, default: Any = None) -> Any:
        return (self.settings.get("loop") or {}).get(key, default)

    def budget(self, key: str, default: Any = None) -> Any:
        return (self.settings.get("budgets") or {}).get(key, default)

    # -------------------------------------------------------------- state view
    def state(self) -> dict[str, Any]:
        """Compact loop state persisted into ``state/state.json``."""
        current = self.history[-1] if self.history else None
        return {
            "iteration": self.iteration,
            "stage": current.stage if current else "analyze_gap",
            "confidence": self.graph.overall_confidence(),
            "confidence_threshold": float(self.option("confidence_threshold", 0.85)),
            "max_iterations": int(self.option("max_iterations", 5)),
            "tool_calls": self.tool_calls,
            "evidence": len(self.store.records),
            "nodes": len(self.graph.nodes),
            "stop_reason": current.stop_reason if current else "",
            "updated_at": now_iso(),
        }

    def coverage_now(self) -> float:
        questions = self.graph.of_type("question")
        if not questions:
            return 0.0
        target = float(self.option("node_coverage", 0.7))
        scored = [self.graph.coverage(node.id, self.store)["coverage"] for node in questions]
        return clamp(sum(min(1.0, value / target) for value in scored) / len(scored))

    # ------------------------------------------------------------------ stages
    def analyze_gap(self) -> list[dict[str, Any]]:
        gaps = self.graph.gaps(
            self.store,
            float(self.option("node_coverage", 0.7)),
            limit=int(self.budget("max_open_tasks_per_iteration", 6) or 6),
        )
        return gaps

    def generate_tasks(self, router: Any, gaps: Iterable[dict[str, Any]] | None = None) -> list[ResearchTask]:
        """Ask the agent router to turn gaps into grant-checked tasks."""
        open_gaps = list(gaps) if gaps is not None else self.analyze_gap()
        tasks = router.plan_dispatch(
            open_gaps,
            iteration=self.iteration,
            max_tasks=int(self.option("max_open_tasks_per_iteration", 6) or 6),
        )
        for task in tasks:
            ok, message = validate(task.to_dict(), "task")
            if not ok:
                raise LoopError(f"generated an invalid task: {message}")
            self.graph.add_node(
                "gap",
                task.objective[:90],
                node_id=f"gap-{task.id}",
                body=task.objective,
                created_by="loop",
                iteration=self.iteration,
                priority=task.priority,
                tags=[task.capability, task.agent],
            )
            if task.question_ref:
                self.graph.add_edge(task.question_ref, f"gap-{task.id}", "blocks", strict=False)
        return tasks

    def begin_iteration(self, tasks: Sequence[Any] | None = None) -> LoopIteration:
        """Open a iteration record (call before dispatching work)."""
        self.iteration += 1
        record = LoopIteration(
            index=self.iteration,
            confidence_before=self.graph.overall_confidence(),
            coverage_before=self.coverage_now(),
            tasks=[_task_dict(task) for task in (tasks or [])],
        )
        self.history.append(record)
        return record

    def collect(
        self,
        evidence: Iterable[Evidence] = (),
        updates: Iterable[GraphUpdate] = (),
        *,
        node_id: str = "",
        agent: str = "researcher",
    ) -> dict[str, int]:
        """Absorb agent output; the runtime stays the only graph writer."""
        record = self._current()
        added_evidence = 0
        added_nodes = 0
        nodes_before = len(self.graph.nodes)
        for item in evidence:
            target = node_id or (item.supports[0] if item.supports else "")
            if target not in self.graph.nodes:
                item.quality_flags = list(dict.fromkeys([*item.quality_flags, "unlinked"]))
                target = "goal"
            self.graph.attach_evidence(item, target, self.store)
            added_evidence += 1
        added_nodes += len(self.graph.nodes) - nodes_before
        for update in updates:
            added_nodes = added_nodes + self.apply_update(update, agent=agent)
        self.store.save()
        self.graph.save()
        record.new_evidence += added_evidence
        record.new_nodes += added_nodes
        # Storing a record is not an MCP call: charging evidence volume against the
        # tool budget made rich iterations look expensive and stopped runs early.
        return {"evidence": added_evidence, "nodes": added_nodes}

    def apply_update(self, update: GraphUpdate, agent: str = "planner") -> int:
        """Apply a proposed GraphUpdate, returning the number of new nodes."""
        new = 0
        for node in update.add_nodes or []:
            node_type = str(node.get("type") or "topic")
            title = str(node.get("title") or "")
            if not title:
                continue
            before = len(self.graph.nodes)
            self.graph.add_node(
                node_type,
                title,
                node_id=node.get("id"),
                body=str(node.get("body") or ""),
                created_by=agent,
                iteration=self.iteration,
                **{key: value for key, value in node.items() if key not in {"type", "title", "id", "body"}},
            )
            new += len(self.graph.nodes) - before
        for edge in update.add_edges or []:
            self.graph.add_edge(
                str(edge.get("from") or ""),
                str(edge.get("to") or ""),
                str(edge.get("type") or "relates_to"),
                float(edge.get("weight", 1.0) or 1.0),
                str(edge.get("note") or ""),
                created_by=agent,
                strict=False,
            )
        for node_id in update.remove_ids or []:
            self.graph.remove_node(str(node_id))
        return new

    def verify_claims(self, evidence_ids: Iterable[str] = (), method: str = "cross_source") -> int:
        """Mark evidence verified (only the verifier agent should call this)."""
        touched = 0
        for evidence_id in evidence_ids:
            if self.store.get(evidence_id) is not None:
                self.store.mark_verified(evidence_id, method=method, by="verifier")
                touched += 1
        self.store.save()
        self.graph.propagate(self.store, float(self.option("confidence_threshold", 0.85)))
        self.graph.save()
        return touched

    def run_gate(self) -> GateReport:
        report = self.gate.evaluate(self.graph, self.store)
        self._current().gate = report.to_dict()
        return report

    # -------------------------------------------------------------- loop driver
    def should_stop(self, gate_report: GateReport | None = None) -> tuple[bool, str]:
        """Evaluate the configured stop conditions in priority order."""
        record = self._current()
        record.confidence_after = self.graph.overall_confidence()
        record.coverage_after = self.coverage_now()
        threshold = float(self.option("confidence_threshold", 0.85))
        max_iterations = int(self.option("max_iterations", 5))
        min_iterations = int(self.option("min_iterations", 1))
        report = gate_report or self.run_gate()
        if self.aborted:
            return True, "user_abort"
        if report.passed:
            return True, "quality_gate_passed"
        if record.confidence_after >= threshold and min_iterations <= self.iteration:
            return True, "confidence_threshold_reached"
        if self.iteration >= max_iterations:
            return True, "max_iterations_reached"
        if self._budget_exhausted():
            return True, "budget_exhausted"
        gain = record.confidence_after - record.confidence_before
        if self.iteration > 1 and gain < float(self.option("min_gain", 0.03)) and record.new_evidence == 0:
            return True, "diminishing_returns"
        return False, ""

    def finish_iteration(self, stop_reason: str = "") -> LoopIteration:
        record = self._current()
        record.confidence_after = self.graph.overall_confidence()
        record.coverage_after = self.coverage_now()
        record.gaps = self.analyze_gap()
        record.stage = "stop" if stop_reason else "analyze_gap"
        record.stop_reason = stop_reason
        record.finished_at = now_iso()
        self._record(record)
        if self.workspace is not None:
            self.workspace.write_state({**self.state(), "history": [item.to_dict() for item in self.history][-10:]})
        return record

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state(),
            "iteration": self.iteration,
            "history": [item.to_dict() for item in self.history],
        }

    # ----------------------------------------------------------------- internal
    def _current(self) -> LoopIteration:
        if not self.history:
            self.begin_iteration()
        return self.history[-1]

    def _budget_exhausted(self) -> bool:
        if self.budget("max_total_tool_calls", 0) and self.tool_calls >= int(self.budget("max_total_tool_calls")):
            return True
        limit = float(self.budget("max_wall_clock_minutes", 0) or 0)
        if limit and (time.monotonic() - self.started_monotonic) / 60.0 >= limit:
            return True
        if self.budget("max_evidence_items", 0) and len(self.store.records) >= int(self.budget("max_evidence_items")):
            return True
        return False

    def _count_tool_calls(self, count: int) -> None:
        self.tool_calls += max(0, int(count))

    def count_tool_calls(self, count: int) -> int:
        """Record MCP calls made by a host agent so budgets stay honest."""
        self._count_tool_calls(count)
        return self.tool_calls

    def abort(self, reason: str = "user_abort") -> None:
        """Ask the loop to stop at the next checkpoint (``nexus loop --abort``)."""
        self.aborted = True
        if self.workspace is not None:
            self.workspace.record("loop_abort", reason=reason)

    def _record(self, record: LoopIteration) -> None:
        if self.workspace is None:
            return
        try:
            self.workspace.record("loop_iteration", stream="loop", **record.to_dict())
        except Exception:  # noqa: BLE001 - replay logs are diagnostic only
            pass


def _task_dict(task: Any) -> dict[str, Any]:
    if isinstance(task, dict):
        return task
    if isinstance(task, ResearchTask):
        return task.to_dict()
    return {"raw": str(task)}
