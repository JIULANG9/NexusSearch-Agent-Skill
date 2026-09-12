# Workflow: Research Loop

The loop is what makes this a *research* skill instead of a search skill: the graph
decides what is missing, and the run stops on measured quality, not on vibes.

## State machine

```
        ┌──────────────────────────────────────────────────────────┐
        │                                                          │
 intake → analyze_gap → generate_tasks → dispatch → collect → update_graph → verify_claims
        │                                                          │
        └────────────────────── should_stop? ── no ────────────────┘
                                  │ yes
                                  ▼
                             gate → report → remember
```

Stage owners come from `config/agents.yaml: stage_owners` (`planner` reasons,
`runtime` schedules, `verifier` judges).

## One iteration, concretely

```bash
nexus -w runs/demo status         # confidence, evidence, gate score
nexus -w runs/demo graph gaps     # worst-covered nodes, with suggested queries
nexus -w runs/demo tasks --prompts   # or `dispatch` for the full assignment bundle
nexus -w runs/demo run --iterations 1 --verify
nexus -w runs/demo gate --save && nexus -w runs/demo report
```

`run` executes the machine-drivable part (MCP calls → evidence). For judgement work
(claims, risks, prose) the host agent does the task and writes back with
`evidence add`, `graph add-node`, `graph apply-update`, then re-enters the loop.

## Stop conditions (`config/settings.yaml: loop.stop_conditions`)

Evaluated in priority order by `LoopController.should_stop`; the first hit wins.

| Condition | Meaning |
| --- | --- |
| `user_abort` | `nexus loop --abort`, or Ctrl-C (state stays resumable) |
| `quality_gate_passed` | all enabled checks pass and score ≥ `min_quality_score` |
| `confidence_threshold_reached` | mean claim confidence ≥ 0.85 after `min_iterations` |
| `max_iterations_reached` | hard ceiling (default 5) |
| `budget_exhausted` | tool calls, wall clock, or evidence ceiling hit |
| `diminishing_returns` | gain < `min_gain` (0.03) **and** zero new evidence |

`budgets` exist so a runaway loop cannot quietly spend the afternoon:
`max_total_tool_calls: 120`, `max_tool_calls_per_iteration: 40`,
`max_wall_clock_minutes: 45`, `max_evidence_items: 200`,
`max_new_nodes_per_iteration: 40`.

## Gap → task → agent

`ResearchGraph.gaps()` scores every question/topic:
`0.45·mean evidence quality + 0.30·(domains/3) + 0.25·(evidence/4)`, then reports the
worst with `missing[]`, `suggested_queries[]`, `suggested_capability`.
`AgentRouter.plan_dispatch()` maps capability → the first agent in pipeline order whose
**grant** includes that capability, and stamps a budget on the task. A task can never
ask an agent for a tool it is not granted (`grant_check`), which is what keeps a
"researcher" from silently rewriting the graph.

## Resumability

Everything is on disk: `state/state.json` (iteration, stage, confidence, budgets),
`agents/logs/loop.jsonl` (one record per iteration), `agents/logs/tool-calls.jsonl`
(redacted audit trail). Re-running `nexus run -w runs/demo` continues where it stopped;
`nexus loop --reset` restarts counting without losing evidence.
