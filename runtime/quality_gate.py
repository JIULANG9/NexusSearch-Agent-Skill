"""Quality gate: the deterministic contract between "an answer" and
"a researched answer" (PRP section 7, AGENTS.md section 8).

Every check here reads the graph and the evidence store; nothing trusts a
model's self-report. The gate output is what the loop controller uses to
decide whether another iteration is worth running, and what the report embeds
so a reader can audit the conclusions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .evidence_store import EvidenceStore
from .graph_engine import ResearchGraph
from .models import Evidence
from .util import clamp, load_config, now_iso, short


@dataclass
class CheckResult:
    """One gate check."""

    id: str
    passed: bool
    score: float
    detail: str
    requirement: str = ""
    remediation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "passed": self.passed,
            "score": round(self.score, 3),
            "detail": self.detail,
            "requirement": self.requirement,
            "remediation": self.remediation,
        }


@dataclass
class GateReport:
    """Aggregate gate outcome."""

    passed: bool
    score: float
    threshold: float = 0.8
    checks: list[CheckResult] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    created_at: str = field(default_factory=now_iso)

    def failed(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.passed]

    def next_actions(self) -> list[str]:
        actions = [check.remediation for check in self.failed() if check.remediation]
        actions += [
            # facet titles repeat under every question, so the node id is part of the action
            f"research {gap.get('node_id')} ({short(str(gap.get('title', '')), 24)}): "
            f"{', '.join(gap.get('missing') or [])}"
            for gap in self.gaps[:5]
        ]
        seen: list[str] = []
        for action in actions:
            if action and action not in seen:
                seen.append(action)
        return seen[:10]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 3),
            "threshold": round(self.threshold, 3),
            "confidence": round(self.confidence, 3),
            "checks": [check.to_dict() for check in self.checks],
            "gaps": self.gaps,
            "metrics": self.metrics,
            "created_at": self.created_at,
        }


class QualityGate:
    """Run the checks declared in ``config/quality.yaml``."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_config("quality")
        self.settings = load_config("settings")
        self._checks: dict[str, Callable[..., CheckResult]] = {
            "source_diversity": self.check_source_diversity,
            "key_claim_corroboration": self.check_key_claim_corroboration,
            "contradiction_review": self.check_contradiction_review,
            "risk_analysis": self.check_risk_analysis,
            "counterexample": self.check_counterexample,
            "uncertainty_marking": self.check_uncertainty_marking,
            "code_grounding": self.check_code_grounding,
            "primary_source_bias": self.check_primary_source_bias,
        }

    # ------------------------------------------------------------------ config
    def rule(self, check_id: str, key: str, default: Any = None) -> Any:
        body = (self.config.get("checks") or {}).get(check_id) or {}
        return body.get(key, default)

    def threshold(self, key: str, default: float) -> float:
        return float((self.config.get("gate") or {}).get(key, default))

    # --------------------------------------------------------------- top level
    def evaluate(self, graph: ResearchGraph, store: EvidenceStore) -> GateReport:
        """Run every enabled check and combine them into one gate report."""
        checks: list[CheckResult] = []
        for check_id, runner in self._checks.items():
            if not self.rule(check_id, "enabled", True):
                continue
            try:
                checks.append(runner(graph, store))
            except Exception as exc:  # noqa: BLE001 - a broken check must not crash the run
                checks.append(
                    CheckResult(check_id, False, 0.0, f"check error: {exc}", "check runs cleanly", "fix the check")
                )
        weights = self.config.get("scoring") or {}
        buckets = {
            "evidence_coverage": [item for item in checks if item.id in {"source_diversity", "code_grounding"}],
            "source_reliability": [item for item in checks if item.id in {"primary_source_bias"}],
            "corroboration": [item for item in checks if item.id in {"key_claim_corroboration"}],
            "contradiction_control": [item for item in checks if item.id in {"contradiction_review", "counterexample"}],
            "uncertainty_transparency": [
                item for item in checks if item.id in {"uncertainty_marking", "risk_analysis"}
            ],
        }
        score = 0.0
        for bucket, items in buckets.items():
            weight = float(weights.get(bucket, 0.0) or 0.0)
            bucket_score = sum(item.score for item in items) / len(items) if items else 0.0
            score += weight * bucket_score
        propagated = graph.propagate(store, self.threshold("confidence_threshold", 0.85))
        confidence = float(propagated["confidence"])
        require_all = bool((self.config.get("gate") or {}).get("require_all", True))
        passed = score >= self.threshold("min_quality_score", 0.8) and confidence >= self.threshold(
            "confidence_threshold", 0.85
        )
        if require_all:
            passed = passed and not [item for item in checks if not item.passed]
        loop_conf = self.settings.get("loop", {}).get("node_coverage", 0.7)
        return GateReport(
            passed=passed,
            score=clamp(score),
            threshold=self.threshold("min_quality_score", 0.8),
            checks=checks,
            gaps=graph.gaps(store, loop_conf),
            metrics={
                **store.stats(),
                **{f"graph_{key}": value for key, value in propagated.items() if key != "detail"},
            },
            confidence=confidence,
        )

    # ------------------------------------------------------------------ checks
    def check_source_diversity(self, graph: ResearchGraph, store: EvidenceStore) -> CheckResult:
        min_sources = int(self.rule("source_diversity", "min_sources", 5))
        min_domains = int(self.rule("source_diversity", "min_independent_domains", 3))
        # store.records is an id -> Evidence mapping; iterating it yields ids, which
        # used to raise inside this check and silently report diversity as zero.
        pool = store.all()
        usable = [item for item in pool if "unrelated" not in (item.quality_flags or [])]
        junk = len(pool) - len(usable)
        total = len(usable)
        domains = {item.domain for item in usable if item.domain}
        score = clamp(0.5 * clamp(total / max(1, min_sources)) + 0.5 * clamp(len(domains) / max(1, min_domains)))
        detail = f"{total} on-topic sources from {len(domains)} domains"
        if junk:
            detail += f" ({junk} flagged unrelated, not counted)"
        return CheckResult(
            "source_diversity",
            total >= min_sources and len(domains) >= min_domains,
            score,
            detail,
            f">= {min_sources} sources across >= {min_domains} domains",
            "run the researcher on a new query angle to reach more independent domains",
        )

    def check_key_claim_corroboration(self, graph: ResearchGraph, store: EvidenceStore) -> CheckResult:
        minimum = int(self.rule("key_claim_corroboration", "min_sources_per_claim", 2))
        claims = graph.of_type("claim")
        if not claims:
            return CheckResult(
                "key_claim_corroboration",
                False,
                0.0,
                "no claims in the graph",
                f"each key claim has >= {minimum} sources",
                "the analyst must promote findings into claim nodes",
            )
        weak = []
        for claim in claims:
            pool = [record for record in store.for_node(claim.id) if claim.id not in record.contradicts]
            domains = {key for key in (record.independence_key for record in pool) if key}
            if len(pool) < minimum or len(domains) < min(2, minimum):
                weak.append(claim.id)
        score = clamp((len(claims) - len(weak)) / len(claims))
        return CheckResult(
            "key_claim_corroboration",
            not weak,
            score,
            f"{len(claims) - len(weak)}/{len(claims)} claims corroborated; weak: {', '.join(weak[:5]) or '-'}",
            f"each key claim has >= {minimum} sources",
            f"add independent sources for: {', '.join(weak[:5]) or '-'}",
        )

    def check_contradiction_review(self, graph: ResearchGraph, store: EvidenceStore) -> CheckResult:
        pairs = graph.contradiction_pairs(store)
        unresolved = [pair for pair in pairs if not _pair_reviewed(store, pair)]
        require_resolved = bool(self.rule("contradiction_review", "require_resolved", True))
        score = 1.0 if not pairs else clamp(1.0 - len(unresolved) / len(pairs))
        return CheckResult(
            "contradiction_review",
            (not unresolved) if require_resolved else True,
            score,
            f"{len(pairs)} contradiction pairs, {len(unresolved)} unreviewed",
            "contradictions resolved or explicitly reported",
            "have the critic and verifier adjudicate the unreviewed contradiction pairs",
        )

    def check_risk_analysis(self, graph: ResearchGraph, store: EvidenceStore) -> CheckResult:
        minimum = int(self.rule("risk_analysis", "min_risks", 2))
        risks = graph.of_type("risk")
        evidenced = [risk for risk in risks if store.for_node(risk.id)]
        score = clamp(len(evidenced) / max(1, minimum))
        return CheckResult(
            "risk_analysis",
            len(risks) >= minimum and bool(evidenced),
            score,
            f"{len(risks)} risks ({len(evidenced)} evidenced), minimum {minimum}",
            f">= {minimum} risks documented, backed by evidence",
            "the analyst must add risk nodes with evidence and mitigations",
        )

    def check_counterexample(self, graph: ResearchGraph, store: EvidenceStore) -> CheckResult:
        has_negative = any(record.contradicts for record in store.all()) or any(
            edge.type == "contradicts" for edge in graph.edges
        )
        alternatives = [node for node in graph.of_type("decision") if (node.decision or {}).get("alternatives")]
        rejected = [node for node in graph.of_type("gap") if "rejected" in (node.tags or [])]
        passed = bool(has_negative or alternatives or rejected)
        return CheckResult(
            "counterexample",
            passed,
            1.0 if passed else 0.0,
            f"contradictions={int(has_negative)} decisions_with_alternatives={len(alternatives)} rejected={len(rejected)}",
            "at least one counter-example or rejected alternative is documented",
            "record at least one rejected alternative with the reason it was rejected",
        )

    def check_uncertainty_marking(self, graph: ResearchGraph, store: EvidenceStore) -> CheckResult:
        threshold = float(self.rule("uncertainty_marking", "threshold", 0.7))
        graph.propagate(store, self.threshold("confidence_threshold", 0.85))
        claims = graph.of_type("claim")
        if not claims:
            return CheckResult("uncertainty_marking", False, 0.0, "no claims", "low claims are labelled", "add claims")
        unmarked = [
            claim.id
            for claim in claims
            if claim.confidence < threshold and "uncertain" not in (claim.tags or [])
        ]
        score = clamp((len(claims) - len(unmarked)) / len(claims))
        return CheckResult(
            "uncertainty_marking",
            not unmarked,
            score,
            f"{len(unmarked)} low-confidence claims not marked uncertain",
            f"claims under {threshold} confidence are labelled",
            f"mark as uncertain or gather more evidence for: {', '.join(unmarked[:5]) or '-'}",
        )

    def check_code_grounding(self, graph: ResearchGraph, store: EvidenceStore) -> CheckResult:
        targets = {node.id for node in graph.of_type("claim")} | {node.id for node in graph.of_type("decision")}
        if not targets:
            return CheckResult(
                "code_grounding", True, 1.0, "nothing to ground", "workspace claims cite local files", "-"
            )
        local = [record for record in store.all() if record.source_type in {"file", "code"}]
        grounded = {node for record in local for node in record.supports}
        hits = len(grounded & targets)
        score = clamp(0.4 + 0.6 * (hits / max(1, len(targets)))) if local else 0.5
        return CheckResult(
            "code_grounding",
            bool(local),
            score,
            f"{len(local)} local-file citations grounding {hits} graph nodes",
            "recommendations touching the workspace cite local files",
            "run the context-agent so workspace claims cite real files",
        )

    def check_primary_source_bias(self, graph: ResearchGraph, store: EvidenceStore) -> CheckResult:
        limit = float(self.rule("primary_source_bias", "max_secondary_only_ratio", 0.4))
        claims = graph.of_type("claim")
        if not claims:
            return CheckResult("primary_source_bias", True, 1.0, "no claims", "key claims use primary sources", "-")
        secondary_only = sum(
            1
            for claim in claims
            if store.for_node(claim.id)
            and not any(record.source_tier == "primary" for record in store.for_node(claim.id))
        )
        ratio = secondary_only / len(claims)
        return CheckResult(
            "primary_source_bias",
            ratio <= limit,
            clamp(1.0 - ratio),
            f"{secondary_only}/{len(claims)} claims rest only on secondary/tertiary sources",
            f"<= {limit:.0%} of claims without a primary source",
            "fetch official docs, papers or repository source for the unsupported claims",
        )


def _pair_reviewed(store: EvidenceStore, pair: tuple[str, str]) -> bool:
    for evidence_id in pair:
        record = store.get(evidence_id)
        if record is None:
            return False
        if (record.verification or {}).get("status") not in {"confirmed", "rejected", "adjudicated"}:
            return False
    return True
