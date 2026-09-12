# Workflow: Multimodal Retrieval

Text is the default, not the whole story. Screenshots, dashboards, diagrams and tables
often carry the evidence that decides an architecture question.

## Modalities and where they come from

| Modality | Capability | Provider chain | Notes |
| --- | --- | --- | --- |
| text | `web.read` | fetch → searxng → playwright → chrome-devtools | markdown extraction first |
| rendered DOM | `web.read` | playwright / chrome-devtools | SPA, cookie walls, lazy tables |
| image | `multimodal.image` | searxng (`categories=images`) → screenshots → `filesystem` reads of `input/files/` | visual comparison, diagrams |
| tabular | `data.structured` | postgresql `query`, redis `get`, local CSV/JSON via `filesystem` | numbers beat adjectives |
| structured | `code.local_structure` | codegraph explore/files | graphs, symbol traces |
| code | `code.repository` | github → raw fetch | version-pinned blobs |

## Rules

1. **Every image needs a text surrogate**: `Evidence.summary` states what is visible and
   why it matters; `source_type: "screenshot"`, `modality: "image"`, and the local path in
   `evidence/captures/` (settings `multimodal.capture_dir`, `max_capture_bytes` 5 MiB).
2. Screenshots are **primary** for UI/behaviour claims ("the console exposes X"), never
   for quantitative claims.
3. A diagram in a README is *secondary* until the spec or code confirms it.
4. DB/Redis evidence must record the query in `quote` so a human can re-run it.
5. Local data first: never ship a workspace table to an external service to "understand"
   it (`privacy.forbid_external_upload: true`).
6. When the runtime cannot drive a browser (no MCP client for it), `nexus dispatch` marks
   the task `auto_executable: false` and the host agent performs the capture, then
   `nexus evidence add --type screenshot --modality image --uri evidence/captures/x.png`.

## Why it matters for the loop

Coverage counts *quality*, not clicks. One screenshot that settles a question can be
worth five blog posts; without the `modality` + `summary` fields the graph cannot tell
them apart, and the gate cannot prove the claim.
