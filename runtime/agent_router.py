"""Agent roster, capability grants and dispatch planning.

The router is the enforcement point for AGENTS.md section 2.2: a specialized
agent may only call capabilities explicitly granted to it in
``config/agents.yaml``. Unknown agents, unknown capabilities and grant/roster
drift are reported by :meth:`AgentRouter.audit` instead of failing silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .models import ResearchTask
from .util import load_config, load_yaml


class RouterError(RuntimeError):
    """Raised when an agent or capability cannot be resolved."""


KNOWN_EMITS = {
    "ResearchPlan",
    "GraphUpdate",
    "ResearchTask",
    "Evidence",
    "Claim",
    "Risk",
    "Decision",
    "Challenge",
    "Verification",
    "Report",
}


@dataclass
class AgentSpec:
    """One entry of ``config/agents.yaml``."""

    name: str
    file: str = ""
    role: str = ""
    phase: str = ""
    capabilities: list[str] = field(default_factory=list)
    emits: list[str] = field(default_factory=list)
    may_write: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    budgets: dict[str, Any] = field(default_factory=dict)
    read_only_workspace: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def allows(self, capability: str) -> bool:
        return capability in self.capabilities

    def budget(self, key: str, default: Any = None) -> Any:
        return self.budgets.get(key, default)

    def prompt(self) -> str:
        return self.file or f"agents/{self.name}.md"


@dataclass
class AgentRouter:
    """Load the roster, resolve aliases, and turn graph gaps into tasks."""

    defaults: dict[str, Any] = field(default_factory=dict)
    agents: dict[str, AgentSpec] = field(default_factory=dict)
    pipeline: list[str] = field(default_factory=list)
    stage_owners: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: str | Path | None = None) -> "AgentRouter":
        config = load_config("agents") if path is None else load_yaml(Path(path))
        roster = config.get("agents") or {}
        router = cls(
            defaults=dict(config.get("defaults") or {}),
            pipeline=list(config.get("pipeline") or []),
            stage_owners=dict(config.get("stage_owners") or {}),
        )
        for name, body in roster.items():
            body = dict(body or {})
            spec = AgentSpec(
                name=name,
                file=str(body.get("file") or f"agents/{name}.md"),
                role=str(body.get("role") or ""),
                phase=str(body.get("phase") or "gather"),
                capabilities=list(body.get("capabilities") or []),
                emits=list(body.get("emits") or []),
                may_write=list(body.get("may_write") or []),
                forbidden=list(body.get("forbidden") or []),
                budgets=dict(body.get("budgets") or {}),
                read_only_workspace=bool(body.get("read_only_workspace")),
                raw=body,
            )
            router.agents[name] = spec
            for alias in body.get("also_known_as") or []:
                router.aliases[str(alias)] = name
            router.aliases.setdefault(name, name)
            router.aliases.setdefault(_stem(spec.file), name)
        if not router.pipeline:
            router.pipeline = list(router.agents)
        return router

    # -------------------------------------------------------------- resolution
    def get(self, name: str) -> AgentSpec:
        resolved = self.aliases.get(str(name)) or self.aliases.get(_stem(str(name)))
        if resolved is None or resolved not in self.agents:
            raise RouterError(f"unknown agent: {name}")
        return self.agents[resolved]

    def names(self) -> list[str]:
        return list(self.agents)

    def order(self, names: Iterable[str] | None = None) -> list[str]:
        """Return agent names in pipeline order (unknown names last)."""
        wanted = list(names) if names is not None else list(self.pipeline)
        ranking = {name: rank for rank, name in enumerate(self.pipeline)}
        return sorted({self.get(name).name for name in wanted}, key=lambda item: ranking.get(item, 99))

    def owner_of(self, stage: str) -> str:
        return self.stage_owners.get(stage, "runtime")

    # ------------------------------------------------------------------ grants
    def grant_check(self, agent_name: str, capability: str) -> bool:
        """True when the agent may use the capability at all."""
        try:
            return self.get(agent_name).allows(capability)
        except RouterError:
            return False

    def audit(self, known_capabilities: Sequence[str] = ()) -> list[dict[str, str]]:
        """Report roster problems: ungraded capabilities, unknown emits, drift."""
        problems: list[dict[str, str]] = []
        known = set(known_capabilities)
        for name, spec in self.agents.items():
            if known:
                for capability in spec.capabilities:
                    if capability not in known:
                        problems.append(
                            {
                                "agent": name,
                                "severity": "error",
                                "issue": f"capability not in mcp-map: {capability}",
                            }
                        )
            for emitted in spec.emits:
                if emitted not in KNOWN_EMITS:
                    problems.append({"agent": name, "severity": "warn", "issue": f"unknown emit type: {emitted}"})
        for name in self.pipeline:
            if name not in self.agents:
                problems.append({"agent": name, "severity": "error", "issue": "pipeline references unknown agent"})
        for stage, owner in self.stage_owners.items():
            if owner != "runtime" and owner not in self.agents:
                problems.append({"agent": owner, "severity": "error", "issue": f"stage owner unknown: {stage}"})
        return problems

    # ---------------------------------------------------------------- dispatch
    def plan_dispatch(
        self,
        gaps: Sequence[dict[str, Any]],
        *,
        iteration: int = 0,
        max_tasks: int = 6,
        prefer: Sequence[str] = ("researcher", "context-agent", "analyst", "verifier"),
    ) -> list[ResearchTask]:
        """Turn graph gaps into prioritised, grant-checked tasks."""
        tasks: list[ResearchTask] = []
        for gap in gaps:
            if len(tasks) >= max_tasks:
                break
            node_id = str(gap.get("node_id") or "")
            capability = str(gap.get("capability") or gap.get("suggested_capability") or "web.discovery")
            missing = [str(item) for item in (gap.get("missing") or [])]
            agent = self._pick_agent(capability, missing, prefer)
            if agent is None:
                continue
            task = ResearchTask(
                agent=agent.name,
                objective=_objective_for(gap, missing),
                capability=capability,
                question_ref=node_id,
                iteration=iteration,
                priority=int(gap.get("priority", 1) or 1),
                status="ready",
                params={
                    "node_id": node_id,
                    "queries": list(gap.get("suggested_queries") or []),
                    "missing": missing,
                    "coverage": gap.get("coverage"),
                },
                acceptance=_acceptance_for(capability),
                budget={
                    "max_tool_calls": int(agent.budget("max_tool_calls", 12) or 12),
                    "timeout_sec": float(agent.raw.get("timeout_sec", 90) or 90),
                },
                created_by="agent_router",
            )
            tasks.append(task)
        return _sort_tasks(tasks)

    def ready(self, tasks: Sequence[ResearchTask]) -> list[ResearchTask]:
        """Tasks whose ``depends_on`` references are all finished."""
        finished = {task.id for task in tasks if task.status in {"done", "failed", "skipped"}}
        open_ids = {task.id for task in tasks if task.id not in finished}
        usable: list[ResearchTask] = []
        for task in tasks:
            if task.status != "ready":
                continue
            if {dep for dep in task.depends_on if dep in open_ids}:
                task.status = "blocked"
                continue
            usable.append(task)
        return _sort_tasks(usable)

    def context_for(self, agent_name: str, task: ResearchTask) -> dict[str, Any]:
        """Compact contract handed to the calling model for one task."""
        spec = self.get(agent_name)
        return {
            "agent": spec.name,
            "prompt_file": spec.prompt(),
            "role": spec.role,
            "phase": spec.phase,
            "capability": task.capability,
            "granted_capabilities": spec.capabilities,
            "emits": spec.emits,
            "read_only_workspace": spec.read_only_workspace,
            "forbidden": spec.forbidden,
            "budget": {
                "max_tool_calls": task.budget.get("max_tool_calls", spec.budget("max_tool_calls", 12)),
                "max_output_items": spec.raw.get(
                    "max_output_items", self.defaults.get("max_output_items", 12)
                ),
            },
            "requires_evidence_for_claims": bool(
                spec.raw.get("requires_evidence_for_claims", self.defaults.get("requires_evidence_for_claims", True))
            ),
        }

    # ------------------------------------------------------------------ helpers
    def _pick_agent(
        self, capability: str, missing: Sequence[str], prefer: Sequence[str]
    ) -> AgentSpec | None:
        ordered = [name for name in self.pipeline if name in prefer] + [
            name for name in self.pipeline if name not in prefer
        ]
        candidates = [self.agents[name] for name in ordered if name in self.agents]
        for spec in candidates:
            if spec.allows(capability) and _matches_missing(spec, missing):
                return spec
        for spec in candidates:
            if spec.allows(capability):
                return spec
        return None


def _matches_missing(spec: AgentSpec, missing: Sequence[str]) -> bool:
    if not missing:
        return True
    text = " ".join(missing).lower()
    if any(token in text for token in ("local", "workspace", "code")):
        return bool(
            {"code.local_structure", "data.structured"} & set(spec.capabilities)
        )
    return True


def _objective_for(gap: dict[str, Any], missing: Sequence[str]) -> str:
    node = str(gap.get("node_title") or gap.get("node_id") or "open question")
    if missing:
        return f"Close evidence gaps for {node}: {', '.join(missing[:4])}"
    return f"Gather evidence for {node}"


def _acceptance_for(capability: str) -> dict[str, Any]:
    acceptance: dict[str, Any] = {"min_evidence": 1 if capability.startswith("memory.") else 2}
    if capability.startswith(("code.", "web.", "docs.")):
        acceptance["require_primary_source"] = True
    return acceptance


def _sort_tasks(tasks: list[ResearchTask]) -> list[ResearchTask]:
    return sorted(tasks, key=lambda task: (task.priority, task.agent, task.id))


def _stem(name: str) -> str:
    return re.sub(r"\.(md|markdown|ya?ml|json)$", "", str(name).rsplit("/", 1)[-1])
