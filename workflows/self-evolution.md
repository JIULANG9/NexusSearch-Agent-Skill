# Workflow: Self-Evolution

The skill should be better on run *n+1* than on run *n*, without anyone editing code.
Two local stores, no vector DB required:

- `memory/research-patterns.json` — curated, human-reviewable patterns
  (`runtime/memory_store.py`)
- optional `memory` MCP graph (`@modelcontextprotocol/server-memory`) — entities and
  observations for a domain, pushed with `nexus remember sync --push`

## Pattern kinds

| kind | Question it answers | Example |
| --- | --- | --- |
| `query` | which query shapes actually returned usable sources | "specification github" beats bare keywords |
| `source` | which domains are reliably primary for a topic | modelcontextprotocol.io, arxiv, readthedocs |
| `decomposition` | how to split a class of objective | goal → 现状/架构/实践/权衡 works for tooling reviews |
| `failure` | which gate checks keep failing | `source_diversity` fails when all hits share one domain |
| `decision` | what a finished run concluded about method/config | early stop at `min_gain` saved 2 iterations |
| `tool` | provider quirks worth encoding | classschedule is down → route `domain.business` to searxng |

## The loop

```
run → gate report + loop log → promote_from_run() → patterns (confidence 0.4–0.7)
 ▲                                          │
 └──── recall(query) biases next tasks ◄────┘
```

`LoopController` + `nexus run` call `MemoryStore.promote_from_run()` automatically
(`--no-memory` disables it). Recall feeds the `memory.long_term` capability and the
Planner's `nexus remember recall "<topic>"`.

**Feedback closes it**: after a run, score each pattern you used —
`nexus remember use <id>` (+0.05 confidence) or `--failed` (−0.05). Confidence is capped
at 0.05–0.99, so a pattern must keep earning trust. `success_rate = wins / uses`.

## Promotion rules (keep the file honest)

1. A pattern needs a *reason* and a *context*; "always use arxiv" is not a pattern.
2. One bad run must not delete a pattern; only repeated `--failed` may demote it.
3. Patterns about **this user's workspace** stay in the run folder, not the shared file.
4. Anything containing a credential, private path, or client name never leaves the run.
5. Review the file before committing (`git diff memory/research-patterns.json`); it is
   data, and data deserves a code review.

## Research replay

`agents/logs/loop.jsonl` records per iteration: tasks, new evidence, new nodes,
confidence before/after, coverage, gate snapshot, gaps, stop reason. Replay = re-run a
finished question offline to compare prompt/config changes:

```bash
nexus loop --show -w runs/demo          # machine readable history
nexus report -w runs/demo               # renders the Loop Trace table
```
