# Changelog

All notable changes to NexusSearch-Agent-Skill are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the version follows SemVer.

### 1.2.1 — 2026-09-13

**Added**
- `runtime/host_mcp.py`: 宿主 MCP 自动检测（Codex / Claude Desktop / Cursor）。
  当 skill 运行在已配置 MCP 服务的宿主 agent 内时，`doctor` 标注 `(via codex)` 等来源，
  `quickstart` 跳过「MCP 未就绪」提示，用户无需额外安装。
- `nexus doctor` 输出增加宿主检测摘要：已配置服务数、匹配数、via host 列表。
- 25 条新测试覆盖 TOML 解析、三宿主检测、匹配逻辑、集成场景。

**Changed**
- `McpRouter.load()` 在初始化时自动探测宿主环境，结果通过 `host_info()` 暴露给 CLI。
- `docs/QUICKSTART.md` 增加宿主 MCP 自动检测说明。

**Removed**
- 开发期 `runs/ltx-dataset-annotation` 数据已清理（安装器按策略不动用户状态，此为源码仓库清理）。
## [1.2.0] - 2026-09-12

Spec conformance pass against the official Agent Skills specification, plus a
beginner path that never shows a traceback. Gap IDs (S1-S11) refer to
`docs/OPTIMIZATION-PLAN.md`.

### Added
- **`runtime/skill_spec.py`** (stdlib only): the normative spec rules in one place -
  frontmatter field whitelist (`name/description/license/compatibility/metadata/allowed-tools`),
  the 64/1024/500 limits, `name` shape + install-directory equality, `SKILL.md` < 500 lines,
  component references must resolve relative to the skill root - together with release
  hygiene (private-path and credential scanners, version-drift map, bundle builder).
- **`nexus skill-check [path] [--release]`** - "will any host agent load this skill?",
  checking the source tree and every installed copy (`~/.agents`, `~/.codex`, `~/.claude`,
  `./.agent`) with the directory rule applied only to installs. Exit 0 = loadable.
- **`nexus quickstart "一句研究问题"`** - one command to a workspace, a requirement
  template, a parsed plan, a task list and copy-pasteable next steps. Works fully
  `--offline`; missing MCP reachability degrades to a hint, never a stack trace.
- **`nexus open-source check|build`** - the publish gate and a sanitized
  `dist/nexus-deep-research/` + zip. Nothing is deleted from disk: private paths are
  masked to `$HOME/`, `Documentation/`, `TimeLine.md`, `runs/`, caches and the author's
  memory ledger are excluded, and the emitted bundle is itself spec-validated. (S2, S3, S4)
- **Governance**: `SECURITY.md` (local-first guarantees, prompt-injection stance, known
  limits, disclosure route), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `NOTICE`
  (attribution: spec, validator, and the two community repos read as layout examples),
  `.github/workflows/ci.yml` (pytest + `skill-check` + offline loop + release gate +
  bundle artifact on Python 3.10/3.12/3.13) and `.github/copilot-instructions.md`. (S5)
- **`nexus install`** (`install.sh` is a thin wrapper) - non-interactive installer with
  `--dest/--dry-run/--list/--force`. The installed payload is *exactly* the release file
  set, so private material can no longer survive in `~/.agents/skills/…` the way a
  hand-maintained `cp -R`/rsync exclude list did: files the payload lacks are pruned,
  while the user's own `memory/` and `runs/` are never removed. It refuses to overwrite a
  directory without a `SKILL.md` unless `--force`, and ends with a spec check.
- **Runtime state no longer travels** (S4): `memory/research-patterns.json` is excluded
  from the release set and from installs - the author's ledger records run names and
  query phrasings from real research. `open-source check` now fails if it ever appears in
  the release set and reports how many learned patterns stayed behind locally.
- **`docs/QUICKSTART.md`** - Chinese five-minute guide answering the three questions
  beginners always ask: where do I write the requirement, where does state live, where
  is the report.
- **35 tests** in `tests/test_skill_spec.py`, including two that gate this repository:
  `SKILL.md` must stay spec-compliant and `audit_release()` must stay error-free.

### Fixed
- `skill.json` advertised a third-party repository as this skill's `homepage` and
  carried a `package` id that contradicted `name` (S1); README now credits those
  repositories as references only.
- 72 absolute `/home/<user>/...` paths in `examples/sessions/` masked to `$HOME/` (S3).
- Version declared in six places is now 1.2.0 everywhere and drift is a CI failure (S6);
  `license = { text = "MIT" }` replaced by PEP 639 `license = "MIT"` + `license-files`,
  with Python-version classifiers, `[project.urls]` and a note that `pip install` ships
  only the CLI while `install.sh`/`cp -R` ships the skill (S8).
- `.gitignore` covers `.ruff_cache/`, `build/`, `dist/`, `*.egg-info/` and the private
  `Documentation/`, `TimeLine.md` (S7).
- `SKILL.md` declares `license` and `compatibility`; the previously missing
  environment contract is now explicit (S9).
- **Beginner-path crashes**: `quickstart` with no goal aborted with
  `no research graph yet`, and its printed next steps suggested `run` before a plan
  existed; both branches now give the correct next command. `--out <dir>` produced a
  bundle in a folder whose name violated the spec's directory rule - the bundle is now
  always nested under `<name>/`.
- Global flags after a subcommand (`quickstart "x" --offline`) are honoured. (S11)

### Known limits
- `python -m build` still puts `config/`, `agents/` and `schemas/` into the wheel via
  `package-data` rather than shipping `SKILL.md`; the supported install path for the
  skill payload is `install.sh` / `cp -R`, which the README and `pyproject.toml` now say
  out loud instead of pretending the wheel is the distribution.
- `postgresql` stays opt-in (`NEXUS_PG_ENABLED=1`): the reachable local `devdb` holds
  business tables, not a research corpus.

## [1.1.0] - 2026-09-12

Hardening pass driven by the first real research run (`runs/ltx-dataset-annotation`,
LTX-2.5 数据集准备与标注), plus the local SearXNG egress fix.

### Added
- **Private local env loader**: `runtime/util.load_local_env()` reads
  `~/.config/nexus/local.env` (chmod 600, outside the repository) at import time and
  only fills *unset* variables, so `${GITHUB_PERSONAL_ACCESS_TOKEN}` and
  `${NEXUS_PG_URL}` resolve in `./nexus`, `python -m runtime` and the installed skill
  without any credential ever entering the repo or a report. `NEXUS_LOCAL_ENV` relocates it.
- **Opt-in providers**: a server may declare `opt_in: true` + `enable_env: <VAR>`.
  Disabled providers are skipped in 0 ms by both `doctor` and the capability chain
  instead of burning a transport timeout. `postgresql` is now opt-in: the reachable
  local `devdb` is a business database (users, bills, wechat_pay_config), not a
  research corpus, so joining it implicitly would violate AGENTS.md 2.1.
- **Per-run engine override**: `NEXUS_SEARXNG_ENGINES` / `NEXUS_SEARXNG_ENGINE_GROUPS`
  (`|` separates groups) now reach `server_option()` as well as `_server_defaults()`,
  so raw-option readers and the argument templater cannot disagree.
- **Query language routing**: `harvest.search_language()` sends `en-US` for Latin
  queries and `zh-CN` for Chinese ones. The instance default (`default_lang: zh-CN`)
  turned a niche English technical query into Korean camping portals; pinning the
  locale surfaced `Lightricks/LTX-Video` instead.
- **Script-aware engine groups**: `harvest.ordered_groups()` picks the SearXNG engine
  group from the query's script instead of its position in the plan. Measured on this
  host with `视频微调 数据集 标注 规范`: the Chinese-capable group (`360search,ecosia`)
  returned 7 on-topic guides while `bing,google` returned Microsoft support pages and a
  Malaysian forum. CJK queries open on the Chinese groups and ASCII queries close on
  them, with each partition rotated by query angle so sibling angles still spread. A
  group that answers with nothing relevant is rotated away before it is ever cited.
  Tunable through `servers.searxng.search.cjk_engines`.
- **Term ceiling**: gap-closing and planning queries are capped at 4 technical terms
  and no longer padded with "official documentation"/"2026 comparison" past term two,
  which is where google/bing switch from relevance ranking to popularity ranking.

### Fixed
- `resolve_env` regex lost its `$` escape (a documentation sweep touched code), which
  silently un-resolved every `${VAR}` placeholder in `mcp-map.yaml`.
- Intake split soft-wrapped spec bullets into N fragment questions; indented
  continuation lines now merge back into their bullet.
- Intake leaked `**bold**` / `` `code` `` markers into node titles and search queries.
- Gap queries could ship a Chinese facet label ("架构与关键机制如何运作") as a search
  term; `_keywords` now keeps ASCII tokens only, and `Q2`-style section labels plus
  auxiliary verbs are out of the term pool.
- Chinese queries could never score as relevant: `TERM_PATTERN` took one greedy
  2-6-character CJK run, so the query token `数据集` and the title token
  `微调数据集标注` never intersected and every genuinely on-topic Chinese page was
  dropped as off-topic junk. `harvest.terms_of()` now expands each CJK run into its
  full form plus 2- and 3-character windows. Verified live: `relevance()` for the
  Tencent Cloud 微调数据集标注 guide went 0 → 6 while junk stayed at 0, and a real
  `nexus run` iteration produced 20 Chinese evidence nodes at weight 1.0 (was: all
  zero-overlap weak leads).
- A name collision with the pre-existing `ASCII_TERM` constant shadowed the new term
  regex; the helper now uses its own `LATIN_TERM`.
- `nexus doctor` reported 30 s timeouts for postgresql (unresolved DSN placeholder)
  and no longer probes inactive providers at all.

### Changed
- `config/mcp-map.yaml`: SearXNG engine groups rebuilt after re-measuring every
  engine on the proxy-routed instance (`bing,google` / `github,stackoverflow` /
  `pypi,npm,huggingface` / `semantic scholar,wikipedia` / `360search,ecosia`), with
  the known-bad list and the reason it is bad documented in place.
- Skill installed for reuse at `~/.agents/skills/nexus-deep-research` (100 files,
  no caches); `~/.config/nexus/local.env` stays out of both copies.

### Docs
- SearXNG deployment guide gained 第十二节「开通用搜索引擎」: symptom → root cause
  (container egress, not engine enablement), the `outgoing.proxies` +
  `network_mode: host` fix, a per-engine availability table, the desktop-system-proxy
  misconception, alternatives (TUN mode) and a rollback recipe.

## [1.0.0] - 2026-09-12

First release implementing `PRP.md` on top of the design in
`Deep Research Skill 设计方案.md`.

### Added
- **Skill layer**: `SKILL.md` (progressive disclosure frontmatter), 7 agent role
  definitions in `agents/`, 6 reusable procedures in `workflows/`, 3 templates.
- **Graph layer**: stdlib-only research graph (`runtime/graph_engine.py`) with
  10 node types / 12 edge types, confidence propagation, gap detection,
  suggested queries, Mermaid + tree rendering, atomic JSON persistence.
- **Loop layer**: `runtime/loop_controller.py` implements
  `gap → task → execute → merge → verify` with 6 stop conditions
  (confidence, max iterations, gate pass, diminishing returns, budget, abort),
  public `count_tool_calls()` / `abort()` and per-iteration history.
- **MCP layer**: `config/mcp-map.yaml` capability → server → tool chains with
  timeout / retry / backoff / jitter / cache / fallback / audit for every call;
  error taxonomy policy (`timeout`, `rate_limited`, `permission`, `auth_missing`,
  `low_quality`, ...) and `nexus mcp doctor` reachability probes.
- **Evidence system**: `runtime/evidence_store.py` + `schemas/evidence.schema.json`
  with schema validation, dedupe, tier inference, quality penalties,
  contradiction linking and verification records.
- **Quality gate**: `runtime/quality_gate.py` with 8 weighted checks
  (coverage, corroboration, source diversity, primary-source presence,
  contradiction handling, risk analysis, uncertainty marking, …).
- **Intake**: `runtime/intake.py` parses `input/` specs (Chinese + English) into a
  schema-valid research plan, scans the target workspace for context and seeds
  the graph.
- **Harvest**: `runtime/harvest.py` turns tool payloads into `Hit` / `Evidence`
  records for web, docs, repo, local-file, browser and structured data sources.
- **Reporting**: `runtime/report_builder.py` renders `output/report.md` plus
  `evidence.json`, `graph.json` and `manifest.json`.
- **Self-evolution**: `runtime/memory_store.py` + `memory/research-patterns.json`
  recall/use/promote/sync lifecycle, optional write-through to the `memory` MCP server.
- **CLI**: `nexus` (and `python -m runtime`) with 17 subcommands, `--offline`
  mode, `--json` machine output, and `nexus audit` privacy/consistency checks.
- **Tooling**: `scripts/build_graph.py`, `scripts/evidence_check.py`,
  `scripts/merge_results.py`; unit + integration + CLI end-to-end tests.

### Changed
- **Retrieval quality.** A live run that stored 110 records of which 108 were
  irrelevant MDN landing pages forced a real fix rather than a tuning tweak:
  `runtime/harvest.py` now scores every hit against the query
  (`relevance()`/`query_terms()`), keeps at most `NEXUS_MAX_WEAK_LEADS`
  zero-overlap hits and flags them `unrelated` at confidence <= 0.25, and judges
  *fetched pages* by their body (`NEXUS_MIN_PAGE_RELEVANCE`) so a search-result
  title can no longer launder an off-topic page into read-level confidence.
  `strip_boilerplate()` drops `<script>` blocks and inline JS that fetchers
  return as page text. Verified on the demo spec: on-topic share went from 2% to
  78% and independent domains from 2 to 10+.
- **Engine fan-out.** `web.discovery` spreads a task's queries across narrow
  `servers.searxng.search.engine_groups` instead of asking every engine at once:
  a fat multi-engine call is won by whichever engine answers first (in practice
  Microsoft Learn), which is what collapsed diversity to a single domain. Passing
  `categories` alongside `engines` was also removed — SearXNG answers for the
  whole category and ignores the engine filter (measured: 10 mdn / 1 github).
- **Query construction.** Chinese research questions are reduced to ASCII
  technical terms before reaching English-only engines (`util.ascii_terms`,
  `util.rotate_pick`), gap queries fall back to goal terms when a facet topic
  supplies none, and local artefact names (`demo-project`) are dropped from
  keywords. Sibling questions now get different queries instead of identical ones.
- **Server defaults keep their YAML type** — `num_results: 20` no longer reaches
  the MCP tool as the string `"20"`, which the tool rejects outright.
- `num_results` raised to the tool maximum (20, capped: 24 is an invalid
  argument) because SearXNG divides it across all requested engines.

### Fixed
- Loop iterations were written to the tool-call audit log instead of
  `agents/logs/loop.jsonl`, so reports had no loop trace (`Workspace.record(stream=)`).
- Harvested evidence was linked to nothing when a task carried only `question_ref`
  (`harvest.task_node_ids`), so gaps never closed and the loop could not converge.
- Stored records were charged against the MCP tool budget, which stopped rich
  iterations with `budget_exhausted` before they ran out of calls.
- Weak search leads were auto-fetched, converting engine misfires into
  high-confidence evidence; only on-topic hits are read now.
- Re-reading already-recorded URLs wasted budget and duplicated evidence
  (`execute_task(known_urls=)`).
- `nexus report` crashed on any graph containing `risk`/`entity`/`constraint` nodes
  (Mermaid shape templates were `str.format`-style parsed).
- `schemas/graph.schema.json` rejected nested `stats.by_type` maps, so graph saves failed.
- Evidence records could not round-trip between the schema shape and the flat shape.
- Placeholder headings in input specs hijacked the research objective.
- `nexus mcp` / `doctor` / `remember` created skeleton workspaces when run outside one.
- Quality-rejected MCP results left no trace in the attempt log (now recorded as
  `quality_rejected`).

### Fixed (found by finishing the example session)
- `quality_gate.check_source_diversity` iterated `EvidenceStore.records`
  (an id → Evidence mapping), so it received bare strings, raised, and had the
  error swallowed into a `check error` detail — source diversity was pinned at 0
  for every populated store. Fixed by iterating `store.all()`; regression test
  `tests/test_gate_loop.py::test_no_gate_check_crashes_on_a_populated_store`
  asserts that no check detail starts with `check error`.
- `nexus mcp call` inside a real workspace wrote no audit records at all:
  `Session.for_tools()` forced `bare=True`, which dropped the router's
  `audit_path` and cache. Bare is now reserved for directories that are not
  workspaces.
- MCP audit lines recorded only capability/server/ok/duration, so a negative
  result could not be re-checked. `McpResult` now carries a redacted `arguments`
  digest (query/url/engines) and a `result_count`.
- The last provider in `web.discovery` was a copy of the direct-metasearch entry
  with no `url`, so its `invalid_input` reply became the reported failure for a
  chain that had actually run and found nothing. The dead provider is gone and an
  exhausted chain now reports `error_class=no_results` with
  `"<n> providers tried, <m> returned no usable candidates"`.
- `graph apply-update` silently dropped `title` when merging an existing node,
  so an analyst pass could never correct a claim it had already created.
- `nexus loop --show` rendered an empty table and a resumed run got a fresh
  `max_iterations` budget: `LoopController.create()` never rehydrated
  `iteration` / `tool_calls` / `history` from `state/state.json`
  (`LoopIteration.from_dict` + `restore()`, regression test
  `test_loop_state_survives_a_fresh_controller`).
- Relative filesystem arguments were passed to local MCP servers verbatim, so
  `nexus mcp call code.local_structure --args '{"path":"examples/demo-project"}'`
  resolved against the filesystem server's own root and died with `ENOENT` on
  `~/examples/demo-project`. `McpRouter` now absolutises `path`/`dir`/`file_path`/
  `projectPath`/`target` against the caller's working directory in `call()` and
  `call_tool()`, which makes a capability call mean the same thing from any shell.
- An untouched `input/RESEARCH.md` (written by `nexus init --templates`) hijacked
  intake in a live run: the research objective became the template's *非目标*
  bullets — `"目标" in "非目标"` matched as a substring — and two of its example
  questions joined the real spec's. `_section_texts()` now deactivates
  negative-scope headings, `_is_placeholder()` recognises scaffold labels such as
  `（必填…）`, and `read_spec_files()` skips a file that is byte-for-byte the shipped
  template. Regression test
  `tests/test_intake.py::test_untouched_spec_template_cannot_hijack_a_real_spec`;
  `nexus intake` now reports `spec files: <used>/<found> used`.
- `nexus audit` validated the whole evidence store against the single-record
  `evidence.schema.json` and therefore always failed with `'id' is a required
  property`; records are now validated one by one and the message names the count.

### Changed (finishing the example session)
- `Evidence.independence_key` gives local `file`/`code` sources one shared
  `workspace` bucket: four repo files no longer masquerade as four independent
  corroborating domains, and the report lists `workspace` instead of a blank domain.
- Gate remediation names the node (`research q2-landscape (现状与主流方案是什么): …`),
  because facet titles repeat under every question.
- `memory/research-patterns.json` gained 9 curated field notes (CJK→ASCII queries,
  one narrow engine group per call, `categories` overriding `engines`,
  `num_results` as a shared int budget, `web_url_read` over `fetch_markdown` for
  JS doc sites, registry facts as cross-checks, negative results as gaps, and the
  gate-vs-coverage distinction) alongside the auto-learned patterns.
- `examples/sessions/ai-agent-framework` is now a finished run with an analyst
  pass on top of the loop: 159 nodes / 192 edges / 113 evidence records,
  9 claims, 3 risks, 1 decision with 3 rejected alternatives, 2 honest gaps
  (missing benchmark, no public post-mortem found), gate PASS 0.925 and its own
  `README.md` that documents both the result and the residual gaps.
