# Workflow: Verification

Evidence-driven research means every conclusion has a chain:
`claim → evidence → source → independent source`. This workflow is the audit of that
chain, and `runtime/quality_gate.py` is its deterministic executor.

## Three layers

1. **Mechanical** (`nexus verify --apply`) — resolves URIs, counts independent
   registrable domains, links tier weights, flags stale evidence, and re-propagates
   claim status. Fast, boring, catches 80 % of problems.
2. **Adversarial** (Critic, `agents/critic.md`) — opposition queries, "is this really
   primary?", "would the opposite be plausible?". Only for high-stakes or contested
   claims: debate costs tokens and produces little when nothing is contested.
3. **Gate** (`nexus gate`) — 8 checks from `config/quality.yaml`. It reads the graph
   and evidence store only; it never trusts a model's self-report of quality.

## Gate checks

| Check | Rule (defaults) | Remediation |
| --- | --- | --- |
| `source_diversity` | ≥ 5 sources, ≥ 3 domains | new query angle |
| `key_claim_corroboration` | ≥ 2 sources per key claim | researcher on that claim |
| `contradiction_review` | every `contradicts` edge reviewed | verifier resolves or reports |
| `risk_analysis` | ≥ 2 risk nodes with mitigation | analyst |
| `counterexample` | ≥ 1 rejected alternative | analyst |
| `uncertainty_marking` | no low-confidence claim unlabelled | synthesizer/planner |
| `code_grounding` | workspace advice cites files | context-agent |
| `primary_source_bias` | ≤ 40 % secondary-only key claims | researcher |

Score = coverage 0.35 + reliability 0.20 + corroboration 0.20 + contradiction control
0.15 + uncertainty transparency 0.10; `require_all: true` means hard checks still
override a decent score, and `min_quality_score: 0.8`.

## Fabrication and contradiction

- `quality_flags: ["fabricated"]` ⇒ penalty 1.0 ⇒ claim discarded. Non-negotiable.
- Contradiction is **data**, not noise: keep both records, `nexus evidence contradict A B`,
  and let the report state the conflict. Silent deletion is the failure mode of naive
  "summarise the top results" tools.
- Single-domain key claims cost 0.25 (`penalties.single_domain_key_claims`); evidence
  older than 365 days loses 0.1 (`stale_evidence_days`).

## Status ladder

`proposed → supported (≥ 0.5) → verified (≥ 0.85 + ≥ 2 domains + verifier confirmed)`,
with `contested` whenever contradiction exists. Only the Verifier moves a claim up.
