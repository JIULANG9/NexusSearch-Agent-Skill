<!--
Report skeleton enforced by `runtime/report_builder.py` (SECTIONS) and PRP.md §11.
`nexus report` fills every section from the graph + evidence store; the Synthesizer
then rewrites the prose. Section order is fixed. Keep every [n] citation resolvable
in output/evidence.json.
-->

# {{objective}}

> Generated {{generated_at}} by NexusSearch {{version}} · graph `{{graph_id}}` ·
> {{evidence_count}} evidence items · {{node_count}} nodes

## Executive Summary

- Overall confidence: {{confidence}} ({{confidence_label}})
- Key findings: {{claims}} claims, {{verified}} verified, {{contested}} contested
- Sources: {{evidence_count}} across {{domain_count}} independent domains
- Quality gate: {{gate_verdict}} (score {{gate_score}})

1. {{key claim 1}} — confidence 0.91 [1][3]
2. {{key claim 2}} — confidence 0.86 [2][5] *(uncertain)*

## Research Objective

| Question | Coverage | Evidence | Status |
| --- | ---: | ---: | --- |
| {{q1}} | 82 % | 6 | verified |

Non-goals: {{...}} · Success criteria: {{...}}

## Current Landscape

| Entity | Kind | Confidence | Sources |
| --- | --- | ---: | ---: |
| {{entity}} | {{kind}} | 0.80 | {{n}} |

## Architecture Analysis

{{graph tree}}

```mermaid
{{graph mermaid}}
```

### Decision candidates

- **{{decision}}** → `{{chosen}}` — rationale, pros, cons, alternatives

## Evidence

| # | Source | Tier | Confidence | Supports |
| --- | --- | --- | ---: | --- |
| [1] | {{title}} ({{domain}}) | primary | 0.75 | {{claim}} |

## Analysis

Answer → evidence → reasoning → caveat, per key question. Include the rejected
alternative and why it lost. Every number cites a source.

## Implementation Plan

- [ ] {{step}} — measure: {{metric}} — rollback: {{rollback}}

## Recommendation

Ordered, executable steps: which files/mechanisms change, in what order, what to
measure to know it worked, and what to do if the measurement fails.

## Risk Analysis

| Risk | Level | Mitigation | Owner | Evidence |
| --- | --- | --- | --- | --- |
| {{risk}} | high | {{mitigation}} | {{owner}} | [4] |

## References

[1] {{uri}} — retrieved {{retrieved_at}} via {{server}}/{{tool}}

## Quality Gate

| Check | State | Score | Detail |
| --- | --- | ---: | --- |

## Loop Trace

| Iter | Tasks | +Evidence | Confidence | Gate | Stop |
| ---: | ---: | ---: | --- | ---: | --- |
