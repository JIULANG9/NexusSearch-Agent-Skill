# Workflow: Graph Planning

## Node types (`schemas/graph.schema.json`)

`goal` → `question` → `topic` → `entity`, plus the judgement layer `claim`, `risk`,
`decision`, `constraint`, and the bookkeeping layer `evidence`, `gap`.

## Edge types

| Edge | Direction | Use |
| --- | --- | --- |
| `decomposes_into` | goal → question → topic | the plan skeleton (acyclic, checked) |
| `depends_on` | task/question → question | ordering; a blocked task is not dispatched |
| `supports` | evidence → claim | corroboration; feeds confidence |
| `contradicts` | evidence ↔ evidence, claim ↔ claim | disagreement that must be resolved |
| `derived_from` | claim → topic/evidence | provenance of an inference |
| `implements` | decision → topic | turns a choice back into work |
| `answers` | claim → question | what a finding is *for* |
| `references` | any → any | soft links, e.g. cross-cutting risks |
| `blocks` | gap → question | stop the loop claiming coverage |
| `mitigates` | risk → decision | every risk needs one |
| `relates_to` | any → any | catch-all; prefer a specific type when possible |

## Shape of a good graph

- **Shallow and wide**: ≤ 8 questions, ≤ 4 topics each. Deep chains amplify one bad
  decomposition; the coverage metric is per-question anyway.
- **Key questions carry weight**: mark the 2–3 questions the report must answer;
  `key_claim_corroboration` gates on claims with `weight ≥ 0.6`.
- **Topics are searchable phrases**, not labels. "成本与运维开销" is a label;
  "self-hosted agent 每 1000 次工具调用的 token 成本" is a topic.
- **One goal node, ever.** Re-scoping means editing `goal`, not adding a second one.

## Determinism first

`nexus intake` seeds the graph mechanically from the spec (headings, bullets, `?`
lines, `约束` sections) plus a filesystem digest of the target project. The Planner
agent then edits: merges duplicates, deletes unfalsifiable questions, adds implicit
constraints. Machine-seeded structure keeps runs reproducible; human/model judgement
makes them relevant.

## Anti-patterns

| Anti-pattern | Symptom | Fix |
| --- | --- | --- |
| Question soup | 20 overlapping questions | merge; keep ≤ 8 |
| Unfalsifiable topic | "is it good?" | rewrite as "what breaks at scale, measured by …" |
| Orphan evidence | `quality_flags: ["unlinked"]` | `nexus graph add-edge ev → node supports` |
| Premature certainty | claim `verified`, 1 domain | `nexus verify --apply` re-derives status |
| Cycle | `validate_now` fails | remove the back-edge, model iteration with `gap` nodes |
