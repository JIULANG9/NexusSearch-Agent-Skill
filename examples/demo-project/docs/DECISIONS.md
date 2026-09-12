# Decision record

## ADR-001 — no agent framework (2026-02-11)
Status: accepted, under review

We call the model directly from `summariser.py`. Reason: one prompt, one call,
and we did not want a dependency. Costs we now see: no retries, no streaming,
no tool use, no observability, and every new feature repeats the same plumbing.

Open question: is a graph-based orchestrator (LangGraph) or a conversation-based
one (AutoGen) the right replacement, or should we keep hand-rolling?
