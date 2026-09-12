"""Report builder: graph + evidence -> auditable markdown report.

The synthesizer agent writes prose; this module writes the *skeleton* with all
mechanically knowable content (claims with confidence and citations, evidence
appendix, references, mermaid graph, quality-gate table, loop history). That
split keeps every number in the report traceable to a file on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .evidence_store import EvidenceStore
from .graph_engine import ResearchGraph
from .models import Evidence
from .quality_gate import GateReport
from .util import SKILL_ROOT, load_config, now_iso, read_json

# PRP.md section 11 fixes the section order; Evidence / Analysis / Quality Gate /
# Loop Trace are NexusSearch extras that keep every number auditable.
SECTIONS = (
    "Executive Summary",
    "Research Objective",
    "Current Landscape",
    "Architecture Analysis",
    "Evidence",
    "Analysis",
    "Implementation Plan",
    "Recommendation",
    "Risk Analysis",
    "References",
)


@dataclass
class ReportBuilder:
    """Assemble ``output/report.md`` and its JSON twins."""

    graph: ResearchGraph
    store: EvidenceStore
    settings: dict[str, Any] = field(default_factory=dict)
    gate_report: GateReport | None = None
    loop_log: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        graph: ResearchGraph,
        store: EvidenceStore,
        *,
        gate_report: GateReport | None = None,
        loop_log: Iterable[dict[str, Any]] = (),
        plan: dict[str, Any] | None = None,
    ) -> "ReportBuilder":
        return cls(
            graph=graph,
            store=store,
            settings=load_config("settings"),
            gate_report=gate_report,
            loop_log=list(loop_log),
            plan=plan or {},
        )

    # ----------------------------------------------------------------- helpers
    def template_text(self) -> str:
        relative = (self.settings.get("report") or {}).get("template", "templates/report.md")
        path = SKILL_ROOT / relative
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return ""

    def _claims(self) -> list[Any]:
        return sorted(self.graph.of_type("claim"), key=lambda node: -node.confidence)

    def _citations(self, node_id: str) -> list[int]:
        order = {record.id: index for index, record in enumerate(self.store.sorted(), start=1)}
        return [order.get(ref, 0) for ref in self._refs(node_id)]

    def _refs(self, node_id: str) -> list[str]:
        node = self.graph.get(node_id)
        if node is not None and node.evidence_refs:
            return list(node.evidence_refs)
        return [record.id for record in self.store.for_node(node_id)]

    def confidence_label(self, value: float) -> str:
        if value >= 0.85:
            return "high"
        if value >= 0.7:
            return "medium"
        if value >= 0.5:
            return "low"
        return "very low"

    # ------------------------------------------------------------------ render
    def render(self, extra_sections: dict[str, str] | None = None) -> str:
        """Produce the full markdown report."""
        extra = dict(extra_sections or {})
        blocks: list[str] = [self._header()]
        for section in SECTIONS:
            if section in extra and extra[section].strip():
                blocks.append(f"## {section}\n\n{extra[section].strip()}\n")
                continue
            renderer = {
                "Executive Summary": self._executive_summary,
                "Research Objective": self._research_scope,
                "Current Landscape": self._current_landscape,
                "Architecture Analysis": self._architecture,
                "Evidence": self._evidence,
                "Analysis": self._analysis,
                "Implementation Plan": self._implementation_plan,
                "Recommendation": self._recommendation,
                "Risk Analysis": self._risks,
                "References": self._references,
            }.get(section)
            blocks.append(renderer() if renderer else "")
        # Extra sections that are not part of the fixed skeleton (an agent adding
        # "Benchmark Data" should not have its work silently dropped).
        for section, body in extra.items():
            if section in SECTIONS or not body.strip():
                continue
            blocks.append(f"## {section}\n\n{body.strip()}\n")
        if self.gate_report is not None:
            blocks.append(self._gate_table())
        if self.loop_log:
            blocks.append(self._loop_table())
        if (self.settings.get("report") or {}).get("include_evidence_appendix", True):
            blocks.append(self._appendix())
        return "\n".join(block for block in blocks if block).strip() + "\n"

    def _header(self) -> str:
        goal = self.graph.goal or {}
        return (
            f"# {goal.get('title') or 'Research Report'}\n\n"
            f"> Generated {now_iso()} by NexusSearch "
            f"{(self.settings.get('skill') or {}).get('version', '1.0.0')} · "
            f"graph `{self.graph.graph_id}` · "
            f"{len(self.store.records)} evidence items · {len(self.graph.nodes)} nodes\n"
        )

    def _executive_summary(self) -> str:
        claims = self._claims()
        verified = [claim for claim in claims if claim.status == "verified"]
        contested = [claim for claim in claims if claim.status == "contested"]
        confidence = self.graph.overall_confidence()
        lines = [
            "## Executive Summary",
            "",
            f"- **Overall confidence:** {confidence:.0%} ({self.confidence_label(confidence)})",
            f"- **Key findings:** {len(claims)} claims, {len(verified)} verified, {len(contested)} contested",
            f"- **Sources:** {len(self.store.records)} across {len(self.store.domains())} independent domains",
        ]
        if claims:
            lines += ["", "Top findings:"]
            for claim in claims[:5]:
                refs = ", ".join(f"[{num}]" for num in self._citations(claim.id) if num)
                uncertain = " *(uncertain)*" if "uncertain" in (claim.tags or []) else ""
                lines.append(f"1. {claim.title} — confidence {claim.confidence:.2f}{uncertain} {refs}")
        if self.gate_report is not None:
            verdict = "PASSED" if self.gate_report.passed else "NOT PASSED"
            lines += ["", f"Quality gate: **{verdict}** (score {self.gate_report.score:.0%})."]
        return "\n".join(lines) + "\n"

    def _research_scope(self) -> str:
        goal = self.graph.goal or {}
        questions = self.graph.of_type("question")
        lines = [
            "## Research Objective",
            "",
            f"- **Objective:** {goal.get('title', '-')}",
            f"- **Constraints:** {', '.join(goal.get('constraints') or []) or 'none recorded'}",
            f"- **Research questions:** {len(questions)}",
            "",
            "| Question | Coverage | Evidence | Status |",
            "| --- | ---: | ---: | --- |",
        ]
        for question in questions:
            report = self.graph.coverage(question.id, self.store)
            computed = self.graph.claim_confidence(question.id, self.store)
            lines.append(
                f"| {question.title[:70]} | {report['coverage']:.0%} | {report['evidence']} | {computed['status']} |"
            )
        topics = self.graph.of_type("topic")
        if topics:
            lines += ["", f"Topics decomposed: {len(topics)}; entities tracked: {len(self.graph.of_type('entity'))}."]
        return "\n".join(lines) + "\n"

    def _architecture(self) -> str:
        include = bool((self.settings.get("report") or {}).get("include_mermaid", True))
        lines = ["## Architecture Analysis", "", self.graph.render_tree(), ""]
        if include:
            lines += ["```mermaid", self.graph.render_mermaid(), "```", ""]
        decisions = self.graph.of_type("decision")
        if decisions:
            lines.append("### Decision candidates")
            lines.append("")
            for decision in decisions:
                body = decision.decision or {}
                chosen = body.get("chosen") or "undecided"
                lines.append(f"- **{decision.title}** → `{chosen}`")
                for key in ("rationale", "pros", "cons", "alternatives"):
                    value = body.get(key)
                    if not value:
                        continue
                    if isinstance(value, list):
                        lines.append(f"  - {key}: {'; '.join(str(item) for item in value[:6])}")
                    else:
                        lines.append(f"  - {key}: {value}")
        return "\n".join(lines) + "\n"

    def _evidence(self) -> str:
        lines = ["## Evidence", ""]
        for claim in self._claims():
            refs = [ref for ref in self._refs(claim.id) if self.store.get(ref)]
            uncertain = " *(uncertain — needs corroboration)*" if "uncertain" in (claim.tags or []) else ""
            lines.append(f"### {claim.title}")
            lines.append("")
            lines.append(
                f"- confidence **{claim.confidence:.2f}** · status `{claim.status}` · "
                f"{len(refs)} sources · domains: {', '.join(self.store.domains([self.store.get(r) for r in refs])[:6]) or '-'}"
                f"{uncertain}"
            )
            for ref in refs:
                record = self.store.get(ref)
                if record is None:
                    continue
                number = self._number_of(ref)
                verdict = (record.verification or {}).get("status", "")
                marker = {"confirmed": " ✓ verified", "rejected": " ✗ rejected"}.get(verdict, "")
                lines.append(
                    f"  - [{number}] {record.title or record.claim[:60]} — "
                    f"`{record.source_tier}` `{record.source_uri or record.domain}`{marker}"
                )
            if claim.body:
                lines += ["", f"> {claim.body}"]
            lines.append("")
        orphans = [record for record in self.store.all() if not record.supports]
        if orphans:
            lines += [f"_Unattached observations: {len(orphans)} (see appendix)._"]
        return "\n".join(lines) + "\n"

    def _analysis(self) -> str:
        lines = ["## Analysis", ""]
        comparisons = [node for node in self.graph.of_type("topic") if node.body]
        for node in comparisons[:6]:
            lines += [f"### {node.title}", "", node.body, ""]
        pairs = self.graph.contradiction_pairs(self.store)
        if pairs:
            lines += ["### Contradictions found", ""]
            for left, right in pairs[:8]:
                a, b = self.store.get(left), self.store.get(right)
                if a is None or b is None:
                    continue
                lines.append(
                    f"- [{self._number_of(left)}] {a.claim[:90]} **vs** "
                    f"[{self._number_of(right)}] {b.claim[:90]}"
                )
            lines.append("")
            adjudicated = [
                pair for pair in pairs if (self.store.get(pair[0]) or Evidence(claim="", source_type="url"))
            ]
            _ = adjudicated
        gaps = self.graph.gaps(self.store)
        if gaps:
            lines += ["### Open questions (what this report does not establish)", ""]
            for gap in gaps[:8]:
                lines.append(f"- {gap['title']} — {', '.join(gap.get('missing') or [])}")
            lines.append("")
        if not lines[2:]:
            lines.append("_The analyst agent has not written narrative analysis yet._")
        return "\n".join(lines) + "\n"

    def _recommendation(self) -> str:
        decisions = self.graph.of_type("decision")
        lines = ["## Recommendation", ""]
        if not decisions:
            lines.append("_No decision nodes yet — the analyst has not recorded recommendations._")
        for decision in decisions:
            body = decision.decision or {}
            chosen = body.get("chosen") or "undecided"
            lines.append(f"- **{decision.title}:** adopt `{chosen}`")
            if body.get("rationale"):
                lines.append(f"  - why: {body['rationale']}")
            if body.get("alternatives"):
                lines.append(f"  - alternatives considered: {', '.join(map(str, body['alternatives']))}")
            refs = self._citations(decision.id)
            if refs:
                lines.append(f"  - evidence: {', '.join(f'[{num}]' for num in refs if num)}")
        next_steps = []
        if self.gate_report is not None:
            next_steps = self.gate_report.next_actions()
        if next_steps:
            lines += ["", "### Next research steps", ""] + [f"- [ ] {step}" for step in next_steps[:8]]
        return "\n".join(lines) + "\n"

    def _risks(self) -> str:
        risks = self.graph.of_type("risk")
        lines = ["## Risk Analysis", ""]
        if not risks:
            return "\n".join(lines + ["_No risks recorded._"]) + "\n"
        lines += ["| Risk | Level | Evidence | Mitigation |", "| --- | --- | --- | --- |"]
        for risk in sorted(risks, key=lambda item: -_level_rank(item.risk_level)):
            refs = ", ".join(f"[{num}]" for num in self._citations(risk.id) if num) or "-"
            lines.append(
                f"| {risk.title[:80]} | {risk.risk_level or 'unrated'} | {refs} | {(risk.mitigation or '-')} |"
            )
        return "\n".join(lines) + "\n"

    def _current_landscape(self) -> str:
        """What exists today, per the sources found: entities and who says so."""
        lines = ["## Current Landscape", ""]
        entities = self.graph.of_type("entity")
        if entities:
            lines.append("| Entity | Kind | Confidence | Sources |")
            lines.append("| --- | --- | ---: | ---: |")
            for entity in sorted(entities, key=lambda item: -item.confidence)[:20]:
                refs = [ref for ref in self._refs(entity.id) if self.store.get(ref)]
                lines.append(
                    f"| {entity.title[:70]} | {entity.entity_type or 'entity'} | "
                    f"{entity.confidence:.2f} | {len(refs)} |"
                )
            lines.append("")
        claims = self._claims()
        if claims:
            statuses: dict[str, int] = {}
            for claim in claims:
                statuses[claim.status or "proposed"] = statuses.get(claim.status or "proposed", 0) + 1
            lines.append(
                f"Claims so far: {len(claims)} ("
                + ", ".join(f"{count} {status}" for status, count in sorted(statuses.items()))
                + ")."
            )
        domains = self.store.domains()
        if domains:
            lines += ["", f"Evidence comes from {len(domains)} independent domains: {', '.join(domains[:12])}."]
        if not entities and not claims:
            lines.append("_Nothing harvested yet — the researcher has not populated the graph._")
        return "\n".join(lines) + "\n"

    def _implementation_plan(self) -> str:
        """Ordered, verifiable steps derived from decisions, risks and open gaps."""
        lines = ["## Implementation Plan", ""]
        steps: list[str] = []
        for decision in self.graph.of_type("decision"):
            body = decision.decision or {}
            chosen = body.get("chosen") or "undecided"
            for index, step in enumerate(body.get("steps") or [], start=1):
                steps.append(f"P{index} · {decision.title[:60]} → {step}")
            if not body.get("steps") and body.get("chosen"):
                steps.append(f"Adopt `{chosen}` for {decision.title[:60]}")
                for key in ("migrate", "rollout", "rollback"):
                    if body.get(key):
                        steps.append(f"  - {key}: {body[key]}")
        mitigations = [f"Mitigate {risk.title[:70]}: {risk.mitigation}" for risk in self.graph.of_type("risk") if risk.mitigation]
        steps += mitigations
        if self.gate_report is not None:
            steps += [action for action in self.gate_report.next_actions()[:5]]
        if steps:
            lines += [f"- [ ] {step}" for step in dict.fromkeys(steps)]
        else:
            lines.append(
                "_No decision or risk nodes yet. The synthesizer must turn the "
                "recommendation into ordered steps with a measurable check per step._"
            )
        return "\n".join(lines) + "\n"

    def _references(self) -> str:
        lines = ["## References", ""]
        for number, record in enumerate(self.store.sorted(), start=1):
            meta = []
            if record.domain:
                meta.append(record.domain)
            if record.source_tier:
                meta.append(record.source_tier)
            if record.retrieved_at:
                meta.append(record.retrieved_at[:10])
            title = record.title or record.claim[:70]
            target = record.source_uri or record.source_type
            lines.append(f"{number}. {title} — <{target}> ({'; '.join(meta)})")
        return "\n".join(lines) + "\n"

    def _gate_table(self) -> str:
        report = self.gate_report
        assert report is not None
        lines = [
            "## Quality Gate",
            "",
            f"Score **{report.score:.0%}** · confidence **{report.confidence:.0%}** · "
            f"verdict **{'PASS' if report.passed else 'FAIL'}**",
            "",
            "| Check | Passed | Score | Detail |",
            "| --- | :---: | ---: | --- |",
        ]
        for check in report.checks:
            mark = "✅" if check.passed else "❌"
            lines.append(f"| {check.id} | {mark} | {check.score:.0%} | {check.detail} |")
        return "\n".join(lines) + "\n"

    def _loop_table(self) -> str:
        lines = ["## Research Loop Trace", "", "| # | Conf. Δ | Coverage | New evidence | New nodes | Stop |", "| --- | --- | --- | ---: | ---: | --- |"]
        for entry in self.loop_log:
            before, after = (entry.get("confidence") or [0, 0])[:2]
            cov = (entry.get("coverage") or [0, 0])[:2]
            lines.append(
                f"| {entry.get('index')} | {before:.0%} → {after:.0%} | {cov[1]:.0%} | "
                f"{entry.get('new_evidence', 0)} | {entry.get('new_nodes', 0)} | {entry.get('stop_reason') or '-'} |"
            )
        return "\n".join(lines) + "\n"

    def _appendix(self) -> str:
        lines = ["## Appendix: Evidence Records", ""]
        lines.append("```json")
        payload = [record.to_dict() for record in self.store.sorted()]
        import json

        lines.append(json.dumps(payload, ensure_ascii=False, indent=1)[:120000])
        lines.append("```")
        return "\n".join(lines) + "\n"

    def _number_of(self, evidence_id: str) -> int:
        for number, record in enumerate(self.store.sorted(), start=1):
            if record.id == evidence_id:
                return number
        return 0

    # ---------------------------------------------------------------- outputs
    def to_dict(self) -> dict[str, Any]:
        """Machine-readable twin of the report (``output/evidence.json``)."""
        return {
            "generated_at": now_iso(),
            "goal": self.graph.goal,
            "confidence": self.graph.overall_confidence(),
            "claims": [
                {
                    "id": claim.id,
                    "title": claim.title,
                    "status": claim.status,
                    "confidence": claim.confidence,
                    "evidence_refs": self._refs(claim.id),
                    "tags": claim.tags,
                }
                for claim in self._claims()
            ],
            "evidence": [record.to_dict() for record in self.store.sorted()],
            "graph": self.graph.stats(),
            "gate": self.gate_report.to_dict() if self.gate_report else {},
            "loop": self.loop_log,
        }

    def manifest(self, written: Sequence[str]) -> dict[str, Any]:
        return {
            "generated_at": now_iso(),
            "skill": (self.settings.get("skill") or {}),
            "artifacts": list(written),
            "graph_id": self.graph.graph_id,
            "nodes": len(self.graph.nodes),
            "edges": len(self.graph.edges),
            "evidence": len(self.store.records),
            "domains": self.store.domains(),
            "confidence": self.graph.overall_confidence(),
            "gate_passed": bool(self.gate_report.passed) if self.gate_report else None,
        }


def _level_rank(level: str | None) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(str(level or "").lower(), 0)


def load_loop_log(path: Any) -> list[dict[str, Any]]:
    """Read ``agents/logs/loop.jsonl`` tolerantly."""
    from .util import read_jsonl

    try:
        return read_jsonl(path)
    except Exception:  # noqa: BLE001
        return []


def load_plan(workspace: Any) -> dict[str, Any]:
    try:
        payload = read_json(workspace.path("plan"), {}) or {}
        return payload if isinstance(payload, dict) else {}
    except Exception:  # noqa: BLE001
        return {}
