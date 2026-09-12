# Workflow: Evidence Harvest

How a raw MCP answer becomes something a claim may rest on
(`runtime/harvest.py`, `runtime/evidence_store.py`).

## Funnel

```
capability call → payload → hits → de-noise → read page → Evidence → attach to node → propagate
   (mcp_router)   (json/text)  (extract_hits)  (hosts)   (web.read)   (schema-checked)
```

1. **Discovery** (`web.discovery`): `extract_hits()` walks the JSON payload looking for
   `url|uri|link|href|html_url` plus a title/snippet, and falls back to a URL regex over
   free text. Noise hosts (social feeds, aggregators) drop out first; richer snippets win.
2. **Leads vs evidence**: a search hit becomes evidence at `confidence ≤ 0.45` and tag
   `lead`. It cannot carry a key claim.
3. **Reading** (`web.read`): the page body (≥ 200 chars) becomes a second record at
   `confidence 0.62` with a verbatim `quote` (≤ 1200 chars), tag `read`.
4. **Local reads** (filesystem/codegraph/memory): `confidence 0.7–0.75`,
   `source_tier: primary` — you ran it, you saw it.
5. **Attachment**: `ResearchGraph.attach_evidence()` creates an `evidence` node plus the
   `supports` edge and links the record's id. Untargeted evidence is flagged `unlinked`
   rather than silently dropped.
6. **Propagation**: `propagate()` recomputes claim status; `EvidenceStore.quality_score()`
   blends tier weight, verification state, quote length, freshness and quality flags.

## Provider chains come from config, not code

`config/mcp-map.yaml` declares an ordered chain per capability; `McpRouter.call()`
walks it: cache → provider → quality check → **reformulate** (tighten the query, raise
result count, switch category) → next provider → local fallback (`urllib` adapter) →
recorded failure. Agents only ask for a *capability*; nothing hardcodes `searxng`.

## Failure classes

`error_policies` in `mcp-map.yaml` map `timeout / unavailable / permission / auth_missing /
rate_limited / invalid_input / low_quality / unknown` to retry counts, backoff and
fallbacks. `permission`/`unavailable`/`auth_missing` mark a server unavailable for the
rest of the run instead of hammering it, and every attempt is appended (redacted) to
`agents/logs/tool-calls.jsonl`.

## What the harvester will never do

- invent a citation for a page it could not read;
- promote a lead to evidence to satisfy `source_diversity`;
- keep two copies of the same claim (dedupe by claim + URI merges `supports` instead);
- exceed `budgets.max_evidence_items` (200) — the loop stops first.
