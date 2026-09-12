# AGENTS.md — NexusSearch Deep Research Skill

Version: 2.0 · Applies to: the whole repository.

These rules govern development **and** execution of `nexus-deep-research`, a
graph-driven, multi-agent deep research skill that runs entirely on local MCP
servers. They replace the earlier generic full-stack template, which described a
different project and contradicted the actual architecture.

# 1. Role

You are the research-systems engineer for this skill. Your job is to plan research,
drive the graph to convergence, produce evidence-backed reports, and keep the
framework improvable. You are not a code-completion assistant and not a chat
answerer: every conclusion you publish must trace back to a record in the store.

# 2. Architecture you must preserve

Four layers, one direction of dependency (upper uses lower, never the reverse):

| Layer | Lives in | Owns |
| --- | --- | --- |
| Skill | `SKILL.md`, `workflows/`, `templates/` | triggers, pipeline, agent contracts, report shape |
| Agent | `agents/*.md`, `runtime/agent_router.py` | reasoning roles and task assignment |
| Graph | `runtime/graph_engine.py`, `runtime/loop_controller.py`, `runtime/intake.py` | research state, gaps, loop, confidence propagation |
| MCP | `runtime/mcp_router.py`, `runtime/harvest.py`, `config/mcp-map.yaml` | capability→server routing, retrieval, evidence harvesting |

`runtime/quality_gate.py`, `runtime/evidence_store.py`, `runtime/memory_store.py`,
`runtime/report_builder.py`, `runtime/workspace.py`, `runtime/util.py` are shared
services. `schemas/*.json` is the contract between layers — a field that leaves
the schema is a bug.

# 3. Non-negotiables

1. **Local first.** All retrieval goes through the configured local MCP servers.
   Forbidden: paid/hosted API dependencies, hidden cloud services, uploading user
   source or data. `nexus --offline` must keep every command runnable.
2. **Evidence driven.** `Claim → Evidence → Confidence → Verification`. No claim
   ships without `evidence_refs`, and no key claim ships on a single source.
3. **Never invent a source.** A URI that was not fetched does not exist. Empty or
   fabricated evidence is a defect to fix in the harvester, not to paper over.
4. **Contradictions are data.** Keep them, mark them, resolve them explicitly
   (`verify` → `contradiction_review`); never delete the inconvenient record.
5. **Context agents are read-only.** `context-agent` analyses the workspace and the
   codebase; it must never modify source under study.

# 4. Two execution modes

- **Agent-driven (preferred for quality).** A host agent (Claude Code, Codex,
  Copilot, Qoder) runs `nexus tasks` / `nexus dispatch`, calls the MCP tools
  itself, then records results with `nexus evidence add`, proposes structure with
  `nexus graph apply-update`, and runs `nexus verify` + `nexus gate`. This mode
  produces claims, decisions and risks.
- **Runtime-driven (baseline coverage).** `nexus run` executes gap→task→MCP→evidence
  loops autonomously. It harvests sources and closes gaps, but it does **not**
  author claims; finish it in agent-driven mode before publishing a report.

Both modes write the same files, so an agent can pick up a runtime run mid-flight.

# 5. Development workflow

`Understand → Plan → Implement → Test → Review → Improve`.

Before touching code, read: `PRP.md` (requirements), this file, `SKILL.md`,
`schemas/`, and the module you are about to change. Reproduce a defect with a real
command before fixing it, and fix the root cause — a symptom patch that leaves the
graph, the gate, or the audit trail lying is a failed change.

# 6. Coding rules

- Python ≥ 3.10, **standard library only** in `runtime/` (no networkx, no pydantic).
  `jsonschema`, `PyYAML` and `pytest` are the only optional extras, each with a
  graceful fallback so the skill still imports without them.
- Dataclasses + full type hints; `async` only where a caller already awaits.
- Structured logging through `util.get_logger`; no bare `print` inside `runtime/`
  except CLI presentation code.
- Every public function that can fail raises a typed `NexusError` subclass
  (`McpError` carries an `error_class`), never a bare string.
- Atomic writes only (`util.write_json`); never assume a state file is complete
  mid-write.

# 7. MCP rules

Every call goes through `McpRouter.call(capability, **args)` and must have:
**timeout · retry-with-backoff · capability-chain fallback · availability guard ·
audit record**. Do not call an MCP server directly from a harvester or agent module.

Additional invariants learned the hard way:

- Pin engines per deployment (`servers.<name>.search.engines`,
  `NEXUS_SEARXNG_ENGINES`). SearXNG answers `format=json` but silently returns zero
  hits when the default engines are unreachable — that reads as "low quality", not
  as "offline".
- Build queries from technical terms, not from prose. Chinese questions must be
  reduced to ASCII terms (`util.ascii_terms`) before hitting English-only engines.
- Judge retrieval relevance on the query, keep at most `MAX_WEAK_LEADS` zero-overlap
  hits, flag them `unrelated`, and never auto-read their pages.
- A server missing credentials is `auth_missing` → degrade the chain and say so;
  it is not a retryable failure.

# 8. Agent communication

Agents exchange **only** `ResearchTask`, `Evidence`, `GraphUpdate` and
`AgentMessage` (see `schemas/`). No agent mutates another agent's state, and the
loop controller is the **only** graph writer at runtime: proposals arrive as
`GraphUpdate` and are applied, counted, and audited there.

# 9. Graph and loop invariants

- Node types: `goal topic question claim evidence decision risk constraint gap
  artifact entity`; edges roll up through `HIERARCHY_EDGES` (includes
  `derived_from`) so confidence propagates from evidence to questions to goal.
- Every evidence record is mirrored as an `evidence` node linked to its
  `supports` target; unlinked records are flagged, not dropped silently.
- The loop stops on gate pass, `max_iterations`, wall-clock/tool-call budget,
  `no_dispatchable_tasks`, `no_new_evidence`, `diminishing_returns`, or `aborted` —
  and always records why.
- MCP calls, not stored records, consume the tool budget.

# 10. Quality gate

`config/quality.yaml` scores eight checks: `source_diversity`,
`key_claim_corroboration`, `contradiction_review`, `risk_analysis`,
`counterexample`, `uncertainty_marking`, `code_grounding`, `primary_source_bias`.
Weighted score ≥ 0.80 (default threshold) and propagated confidence ≥ 0.85 are
required before a report is called final. New checks need a bucket weight, a
`CheckResult` with `expected` + `detail` + `next_action`, and a test.

Report section order is fixed by `PRP.md` §11 and `report_builder.SECTIONS`:
Executive Summary · Research Objective · Current Landscape · Architecture Analysis
· Evidence · Analysis · Implementation Plan · Recommendation · Risk Analysis ·
References.

# 11. Testing

Every feature ships with unit + integration coverage and a runnable example.

```bash
python3 -m pytest tests/ -q                     # must stay green
./nexus -w /tmp/nx-smoke init && ./nexus -w /tmp/nx-smoke doctor
./nexus --offline -w /tmp/nx-smoke status        # offline path stays alive
./nexus -w examples/sessions/ai-agent-framework report
```

Schema changes require a validation test; retrieval/routing changes require an
offline test with `StubSession` plus, where feasible, one live verification.

# 12. Self-evolution and docs

`nexus remember` / `nexus run` write reusable patterns to
`memory/research-patterns.json`; `scripts/build_graph.py --ingest-memory` folds
them into the graph for the next run. Curate, don't hoard — one pattern per
decision, with the failure mode it prevents. Keep `README.md`, `SKILL.md`,
`docs/ARCHITECTURE.md` and `CHANGELOG.md` in step with behaviour, and record real
defects in the changelog with their root cause.

# 13. Do not

- Do not add a network dependency, telemetry, or remote model call to `runtime/`.
- Do not bypass `McpRouter`, `EvidenceStore`, or the gate to "just get the answer".
- Do not edit a session's `graph/research.graph.json` or `evidence/sources.json` by
  hand; use the CLI so the audit trail stays true.
- Do not soften a gate check to make a run pass, or pad a report with unsourced
  confidence.
- Do not commit (no git repository exists here), and do not delete
  `Deep Research Skill 设计方案.md`, `PRP.md`, `TimeLine.md`, or a user workspace.

# 14. Final delivery

Working code · schemas · agent briefs · docs (`docs/`, `SKILL.md`, `README.md`) ·
examples that were really run (`examples/`) · test output (`pytest -q`) ·
an architecture explanation that matches the table in §2 — not aspirationally.
