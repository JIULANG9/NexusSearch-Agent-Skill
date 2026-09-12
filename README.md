# NexusSearch-Agent-Skill

> 基于**本地 MCP** + **多 Agent** + **Graph 推理** + **Loop 自优化**的可复用 Deep Research Skill。
> A graph-driven, evidence-verified research workflow that runs inside Claude Code,
> Codex, Qoder or GitHub Copilot — no cloud research service, no hidden API.

```
一句研究需求  →  Research Graph  →  多 Agent 协同  →  本地 MCP 取数  →  证据校验  →  研究报告
```

- **Evidence driven**：`claim → evidence → source → 独立来源`，缺证据的结论一律不写。
- **Graph stateful**：研究状态是图，不是一串聊天记录；覆盖率、缺口、置信度可计算。
- **Loop bounded**：6 个停止条件 + 工具调用预算，跑偏会自停，不会无限烧 token。
- **Local first**：能力全部来自你机器上的 MCP server；凭据只以 `${ENV}` 形式引用。
- **Self evolving**：`memory/research-patterns.json` 记录有效查询与失败教训，跨 run 复用。

## 三步上手 / 3-step start（中文优先）

```bash
bash install.sh                                              # 1. 装到 ~/.agents/skills/nexus-deep-research
cd ~/.agents/skills/nexus-deep-research
./nexus quickstart "调研 X 与 Y 的技术选型"                     # 2. 建工作区 + 生成需求模板 + 拆解任务
# 3. 编辑 input/RESEARCH.md，然后：
./nexus run --verify && ./nexus report                        #    output/report.md
```

需求写在哪、产出在哪、断网怎么跑、报错怎么办 —— 全在 **[`docs/QUICKSTART.md`](docs/QUICKSTART.md)**（5 分钟，零前置知识）。
想完全离线试一次：任何命令前加 `--offline`（全局开关写在子命令之前）。

## Install

The install directory name **must be `nexus-deep-research`** — that is what the
`name:` field in `SKILL.md` declares, and the Agent Skills spec requires the two to
match (`nexus skill-check` enforces it).

Prefer the installer, which ships exactly the publishable payload, prunes files a later
version dropped, never touches your own `memory/` or `runs/`, and ends with a spec check:

```bash
bash install.sh                 # or: ./nexus install [--dest ~/.codex/skills] [--dry-run]
```

Or copy this repo into your host's skills directory yourself:

```bash
# Claude Code
cp -R . ~/.claude/skills/nexus-deep-research
# Codex
cp -R . ~/.codex/skills/nexus-deep-research
# Qoder
cp -R . .agent/skills/nexus-deep-research
# GitHub Copilot (project instructions)
mkdir -p .github && cp docs/copilot-instructions.md .github/copilot-instructions.md
```

Per-project install (recommended while iterating): add the repo path to your
project's `AGENTS.md`, or symlink it into the host's skills folder.

Requirements: Python ≥ 3.10 on `PATH`. `jsonschema` and `PyYAML` are optional —
the runtime uses them when installed and degrades gracefully when they are not.
Verify your MCP servers are reachable:

```bash
./nexus doctor            # status / tier / latency per configured server
./nexus -w . mcp caps     # capability → provider chain the router will walk
```

Runtime install as a command: `pip install -e .` gives you `nexus` on `PATH`.

## Quickstart

### A. Let the host agent drive (default)

```
/nexus-deep-research 研究 examples/demo-project 是否应该改用 Agent 编排框架，
约束：只用本地 MCP；结论要有来源和置信度；交付中文选型报告。
```

The agent reads `SKILL.md`, writes `input/RESEARCH.md`, runs `nexus intake`, then calls
its own MCP tools and feeds results back with `nexus evidence add` /
`nexus graph apply-update`.

### B. Let the runtime drive

```bash
./nexus -w runs/demo init --templates
$EDITOR runs/demo/input/RESEARCH.md      # goal, constraints, questions, deliverable
./nexus -w runs/demo intake --target . --mode deep
./nexus -w runs/demo run --verify --iterations 3
./nexus -w runs/demo gate --strict && ./nexus -w runs/demo report
open runs/demo/output/report.md
```

### C. Offline / dry runs (no network at all)

```bash
./nexus --offline -w runs/demo run --dry-run
./nexus --offline -w runs/demo tasks
```

## What a run produces

```
runs/demo/
├── input/RESEARCH.md              your research contract (any file here is read)
├── state/research-plan.json       parsed objective, questions, strategy
├── graph/research.graph.json      goal → question → topic → claim + evidence edges
├── evidence/sources.json          every citable finding, with tier + provenance
├── agents/logs/tool-calls.jsonl   audit of every MCP call (retry/fallback included)
├── agents/logs/loop.jsonl         iteration history and stop reason
└── output/
    ├── report.md                  Executive Summary → Analysis → Recommendation → Risk → References
    ├── evidence.json  graph.json  manifest.json
```

## CLI reference

| Command | Purpose |
| --- | --- |
| `nexus init [path]` | create a workspace (`--templates` writes `input/RESEARCH.md`) |
| `nexus intake` | parse `input/` → plan → seeded graph (`--goal`, `--spec`, `--target`, `--mode`, `--questions`) |
| `nexus plan` / `status` / `loop` | inspect plan, coverage, iteration history |
| `nexus graph stats\|tree\|mermaid\|gaps\|validate\|export\|propagate\|add-node\|add-edge\|apply-update` | read and mutate the research graph |
| `nexus evidence list\|show\|add\|stats\|verify\|contradict` | the citation ledger |
| `nexus tasks` / `dispatch` | gap-driven assignments per agent (`--json` for machines) |
| `nexus mcp caps\|providers\|doctor\|call\|stats\|clear-cache` | inspect and call MCP directly |
| `nexus run` | execute the loop end to end (`--verify`, `--dry-run`, `--iterations`) |
| `nexus verify` | cross-check claims before the gate |
| `nexus gate` | 8 weighted quality checks (`--strict` → non-zero exit) |
| `nexus report` | render `output/report.md` and JSON twins |
| `nexus remember list\|recall\|add\|use\|promote\|sync\|stats` | long-term research memory |
| `nexus audit` | consistency, coverage and credential-leak checks |
| `nexus doctor` | MCP reachability report |

Global flags: `-w/--workspace`, `--offline`, `-v`, and `--json` on every subcommand.

## Layout

```
SKILL.md              entry point the host model loads (progressive disclosure)
agents/               7 role contracts + 3 aliases (planner … synthesizer)
workflows/            6 procedures: graph-planning, evidence-harvest,
                      multimodal-retrieval, verification, research-loop, self-evolution
config/               settings · mcp-map · agents · quality
schemas/              plan, graph, evidence, task, agent-message (JSON Schema 2020-12)
runtime/              the Python engine: graph, loop, routers, intake, harvest, gate, report
scripts/              standalone helpers for non-CLI integrations
templates/            input spec + report skeletons
memory/               curated cross-run research patterns (seeded, grows with use)
examples/             demo-project, prompt cookbook, a finished session
tests/                unit + integration + CLI end-to-end
docs/                 this architecture, IDE instruction files
```

`docs/ARCHITECTURE.md` explains the four layers and the loop in detail;
`AGENTS.md` holds the rules an implementing agent must follow;
`examples/sessions/ai-agent-framework/README.md` walks through one finished run —
including the ten framework bugs it exposed and which parts of the research it
deliberately left unproven.

## Extending

- **New MCP server** → add it under `servers:` in `config/mcp-map.yaml`, then reference
  it from a capability's `providers:` chain. `nexus doctor` verifies it.
- **New agent role** → drop `agents/<name>.md`, grant capabilities in `config/agents.yaml`.
- **New quality rule** → add it to `config/quality.yaml` and `QualityGate._checks`.
- **Different report shape** → edit `templates/report.md`; `report_builder.py` fills it.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `not a nexus workspace` | run `nexus -w <dir> init` first, or pass the right `-w` |
| `auth_missing: GITHUB_PERSONAL_ACCESS_TOKEN` | export the variable; the chain then uses `fetch` on raw.githubusercontent.com |
| `web.discovery` returns 0 results or one domain only | which SearXNG engines answer is deployment-specific: pin `servers.searxng.search.engines` and split coverage across `engine_groups` (override with `NEXUS_SEARXNG_ENGINES`). One fat multi-engine call is won by the fastest engine; see `docs/ARCHITECTURE.md` §5 |
| `classschedule` down | recorded as `known_state: unavailable`; no capability depends on it |
| gate keeps failing `source_diversity` | you have one domain, not one source: `nexus tasks` shows the missing angles |
| report has no evidence table | evidence was never attached: `nexus graph propagate` then `nexus audit` |

## Security

Local execution only. The skill never uploads workspace content to a third party,
never stores credentials in the repo, and logs every tool call for audit.
`nexus audit` flags secret-shaped strings inside a run before you commit it.

## Governance

| File | Who reads it |
| --- | --- |
| [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | first-time users (Chinese) |
| [`SKILL.md`](SKILL.md) | the agent that executes a research run |
| [`AGENTS.md`](AGENTS.md) | the agent that *implements* this repository |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | four layers, graph model, loop budget |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | how to change anything without breaking the gate |
| [`SECURITY.md`](SECURITY.md) | local-first guarantees, known limits, how to report |
| [`docs/OPTIMIZATION-PLAN.md`](docs/OPTIMIZATION-PLAN.md) | spec-gap audit behind the 1.2.0 changes |

Two release commands keep those promises honest: `nexus open-source check` fails on
leaked paths, credential-shaped strings, version drift and missing governance files;
`nexus open-source build` emits a sanitized, installable `dist/nexus-deep-research/`
bundle instead of asking you to delete local files. Both run in CI.

## Acknowledgements / 致谢

- The [Agent Skills specification](https://agentskills.io/specification.md) and the
  [`skills-ref`](https://github.com/agentskills/agentskills) validator define the
  packaging rules this skill follows; [`anthropics/skills`](https://github.com/anthropics/skills)
  served as the reference repository layout.
- Two community skill repositories were read as **structural examples only** during
  design — no text, code or assets were copied from them:
  [`nextlevelbuilder/ui-ux-pro-max-skill`](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)
  and [`sanshao85/claude-skills-guide`](https://github.com/sanshao85/claude-skills-guide).

## License

MIT — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
