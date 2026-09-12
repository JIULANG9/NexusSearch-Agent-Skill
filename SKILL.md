---
name: nexus-deep-research
description: Graph-driven deep research on a local workspace and the local web. Use when the user asks to research, investigate, compare, evaluate, or survey technologies, architectures, libraries, vendors, or a codebase; when a decision needs multi-source evidence with confidence scores; or when input/spec-style research requirements sit in a folder. Triggers include "深度搜索", "调研", "技术选型", "对比 X 和 Y", "研究这个项目", "deep research", "tech selection". Runs entirely on local MCP servers with an evidence graph, a verification loop, and a quality gate; refuses unsupported claims.
license: MIT
compatibility: Requires Python 3.10+ and a local filesystem MCP server. Recommended optional servers: searxng, github, context7, fetch, playwright, codegraph, memory. Network access is optional - `nexus --offline` completes a full research loop locally. Linux, macOS and Windows; the CLI never prompts interactively.
metadata:
  version: 1.2.1
  short-description: Local multi-agent deep research with evidence graph + verification loop
  license: MIT
---

# NexusSearch Deep Research

Turn one sentence into a verified research report. The contract is simple:
**no claim without evidence, no evidence without a source, no report without a gate.**

## Two ways to execute — pick one and say which

| Mode | Who calls MCP | When |
| --- | --- | --- |
| **Agent-driven** (default for a chat session) | You, the host agent, with your own MCP tools | The host has MCP tools wired and the user is watching |
| **Runtime-driven** | The bundled `nexus` CLI (`nexus run`) | Long/batch runs, CI, reproducibility, or a host without MCP tools |

Both modes share the same workspace, graph, evidence store, schemas, and gate.
Never mix them inside one iteration.

## Workspace

Start a run with `nexus quickstart "<question>"` when the user has not already created a
workspace; it scaffolds everything below and prints the next commands. `nexus init` is the
bare form. For humans, the five-minute guide is `docs/QUICKSTART.md`.

Every run lives in one folder (default `runs/<slug>/`):

```
input/     spec.md + user docs + files      <- read only
graph/     research.graph.json               <- state
evidence/  sources.json, claims.json         <- state
state/     research-plan.json, state.json     <- state
agents/logs/ tool-calls.jsonl, loop.jsonl     <- audit
output/    report.md, evidence.json, graph.json, manifest.json
memory/    research-patterns.json            <- long-term learning
```

Write only inside `output/` and the state files. Never modify the target project.

## Pipeline

Run the eight stages in order. Do not skip 5–7; they are the difference between
research and a search-result dump.

1. **Intake** — read `input/` (spec, README, docs) and the user's question. Extract
   objective, deliverable, constraints, audience. `nexus intake` does this
   mechanically; in agent-driven mode read the spec yourself and write the same plan.
   See `workflows/graph-planning.md`.
2. **Decompose** — build the Research Graph: `goal → question → topic → entity`.
   3–12 questions, each atomic and answerable. Every question gets 2–4 search angles.
3. **Context first, web second** — before any external call, understand the local
   workspace with `filesystem`, `codegraph`, `memory`. Local facts become
   `source_type: file|code` evidence and anchor every later comparison.
4. **Plan retrieval** — choose capability per question
   (`docs.library`, `code.repository`, `web.discovery`, `web.read`, `browser.render`,
   `data.structured`). Recall matching patterns from memory before inventing queries.
5. **Harvest** — execute the loop: gap → tasks → MCP calls → evidence → merge.
   Follow `workflows/evidence-harvest.md` and `workflows/multimodal-retrieval.md`.
   One tool failure is never fatal: retry → fallback → record → continue.
6. **Analyze** — the analyst compares options, states trade-offs, and writes claims
   with explicit confidence. Always: pros / cons / alternatives / recommendation.
7. **Verify** — `workflows/verification.md`: corroborate key claims across ≥2
   independent domains, hunt contradictions, mark uncertainty, discard anything you
   cannot source. Then `nexus gate` (8 weighted checks). If it fails, its
   `failed_checks` name the next tasks — go back to 5.
8. **Synthesize & evolve** — render `output/report.md` from `templates/report.md`,
   then `nexus remember` the patterns that worked and the failures that cost time.

## Agent roster

Load only the role you are acting as (`agents/<name>.md` carries the full contract):

| Agent | Phase | Owns | May call |
| --- | --- | --- | --- |
| planner | intake | plan + graph shape | memory, code.local_structure |
| context-agent | intake | project context graph, `file`/`code` evidence | filesystem, codegraph, memory |
| researcher | loop | sources and raw evidence | web.discovery, web.read, docs.library, code.repository, browser.render |
| analyst | analysis | comparison, trade-offs, claims, risks, decisions | none (reasons over evidence) |
| critic | analysis | counterarguments, missing angles, bias flags | memory |
| verifier | verification | corroboration, contradiction, staleness | web.read, docs.library, code.repository |
| synthesizer | output | report + recommendations | none |

Agents communicate **only** through `ResearchTask`, `Evidence`, `GraphUpdate`,
`AgentMessage` (`runtime/models.py`, `schemas/`). Never edit another agent's state;
the runtime is the single writer of the graph.

## MCP policy

Local only. Priority order for a capability with several providers:

```
filesystem → codegraph → context7 → github → searxng → fetch → playwright / chrome-devtools
```

- `config/mcp-map.yaml` is the only place that names servers and tools.
- Every call carries `timeout`, `retry`, `fallback`, `logging` (`mcp.default_policy`).
- Never read real projects, files, or env for credentials — `${GITHUB_PERSONAL_ACCESS_TOKEN}`
  style indirection only. `nexus doctor` reports what is actually reachable.
- **Never upload user code to an external service.** Web calls send a query string, nothing else.
- Restricted networks: default search engines may time out, and a wide engine list
  is worse than a narrow one (SearXNG merges whatever answers first, so one engine
  dominates and diversity collapses). Pin `servers.searxng.search.engines` plus
  `engine_groups` (override with `NEXUS_SEARXNG_ENGINES`); `nexus mcp call
  web.discovery --args '{"query":"...","engines":"github"}'` is the fast way to
  find out what your instance actually answers. Zero
  results from SearXNG is a `low_quality` signal, not "no such information".

## Loop and stop conditions

```
while not stopped:
    gaps = graph.gaps(evidence)          # coverage per question
    if not gaps: break
    tasks = [plan(q) for q in gaps]
    results = execute(tasks)             # MCP calls, budgeted
    graph.merge(results); verify(); gate()
```

Defaults (`config/settings.yaml`): `confidence_threshold 0.85`, `max_iterations 5`,
`min_gain 0.03`, budgets `120` tool calls / `40` per iteration / `45` minutes.
Stop early on diminishing returns instead of burning tokens; say so in the report.

## Output contract

`output/report.md`, in the order fixed by PRP §11: **Executive Summary → Research
Objective → Current Landscape → Architecture Analysis → Evidence Table (claim ·
source · tier · confidence) → Analysis → Implementation Plan → Recommendation →
Risk Analysis → References**, plus a Quality Gate and Loop Trace appendix. Every factual sentence is traceable to an
evidence id; recommendations name their supporting claims; unknowns stay labelled
unknown. Machine twins: `evidence.json`, `graph.json`, `manifest.json`.

## Commands you will need

```bash
./nexus -w runs/demo init                       # scaffold a workspace
./nexus -w runs/demo intake --goal "对比 A 与 B"  # spec -> plan -> seeded graph
./nexus -w runs/demo tasks                       # gap-driven assignments per agent
./nexus -w runs/demo dispatch                    # full bundle for the host agent
./nexus -w runs/demo run --verify                # runtime-driven loop, end to end
./nexus -w runs/demo gate --json                 # 12 weighted quality checks
./nexus -w runs/demo report                      # render output/report.md
./nexus -w runs/demo audit                        # consistency + privacy check
./nexus doctor                                   # what is reachable right now

# onboarding, packaging, release - run these before handing this skill to a user
./nexus quickstart "一句研究问题"                    # workspace + requirement template + plan + next commands
./nexus install [--dest ~/.codex/skills] [--dry-run]  # copy exactly the publishable payload into a host
./nexus skill-check [path] [--release]                # Agent Skills spec conformance (source + installed copy)
./nexus open-source check                             # privacy / secrets / governance / version-drift gate
./nexus open-source build                             # sanitized dist/<name>/ + zip; deletes nothing
```

Agent-driven feedback loop, after each real MCP call you make:

```bash
./nexus -w runs/demo evidence add --claim "..." --uri https://... --type url \
    --tier primary --confidence 0.8 --supports <node>
./nexus -w runs/demo graph add-node claim --title "..." --id c1 --parent <node>
./nexus -w runs/demo graph apply-update --file /tmp/update.json   # GraphUpdate
```

Read `workflows/research-loop.md` before a long run, `workflows/verification.md`
before writing any claim, and `workflows/self-evolution.md` when a run keeps failing
the same way. `docs/ARCHITECTURE.md` explains the four layers; `examples/` has a
walkthrough session.

## Hard rules

- Fabricating a source, a URL, or a number ends the run: cite or say you cannot.
- Contradictory evidence is reported, never deleted.
- Confidence is not enthusiasm: `0.9` requires a primary source plus corroboration.
- A single source is `single_source` flagged and cannot carry a key claim.
- Do not simplify the architecture under time pressure — reduce scope instead.
