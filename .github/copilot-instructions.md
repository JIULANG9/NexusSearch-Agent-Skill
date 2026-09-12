# GitHub Copilot — Deep Research instructions

When asked to research, compare, evaluate, or survey anything:

1. Read `SKILL.md` in this repository (skill: `nexus-deep-research`) and follow its
   8-stage pipeline: intake → decompose → context-first → plan retrieval → harvest →
   analyze → verify → synthesize.
2. Act as one agent at a time from `agents/` and honour its capability grants in
   `config/agents.yaml`.
3. Use only local MCP servers mapped in `config/mcp-map.yaml`, in this priority:
   `filesystem` → `codegraph` → `context7` → `github` → `searxng` → `fetch` →
   `playwright`/`chrome-devtools`. Every call has timeout, retry, fallback, logging.
4. Persist state through the CLI (`nexus -w <workspace> ...`), never by editing the
   graph file by hand. The runtime is the only writer of `research.graph.json`.
5. Write no claim without evidence, no evidence without a source URI, and label every
   conclusion with a confidence value. Contradictions are reported, not deleted.
6. Finish with `nexus gate` before presenting a report; if it fails, run the
   `next_actions` it prints instead of declaring the research done.
7. Never send workspace contents to an external service and never inline credentials —
   reference `${GITHUB_PERSONAL_ACCESS_TOKEN}` style names only.
