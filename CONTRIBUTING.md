# Contributing

Thanks for improving `nexus-deep-research`. This file is the short version of what
actually happens on this repo; `AGENTS.md` holds the deeper engineering rules.

## Ground rules

1. **Local first.** Every retrieval path must go through a configured local MCP
   server. No hosted research API, no telemetry, no uploading user content.
   `nexus --offline <command>` must keep working for every command.
2. **Evidence driven.** A conclusion without an `Evidence` record and a source URI
   is a bug report, not a feature. Keep the gate honest - do not lower a threshold
   to make a test pass.
3. **Stdlib runtime.** `runtime/` imports nothing beyond the standard library.
   `jsonschema` and `PyYAML` stay optional and must degrade gracefully.
4. **Spec compliant.** `SKILL.md` follows the
   [Agent Skills specification](https://agentskills.io/specification.md): only the
   allowed frontmatter fields, name matching the install directory, body under
   500 lines, references relative to the skill root.
5. **No private material.** Never commit research runs, host absolute paths
   (`/home/...`, `/Users/...`), credentials, or third-party documents you do not
   own. Secrets live in `~/.config/nexus/local.env` and are referenced `${ENV_VAR}`.

## Workflow

```bash
git checkout -b feat/your-change
# ... edit ...
python3 -m pytest tests/ -q            # unit + integration
./nexus skill-check --release          # spec conformance + release readiness
./nexus quickstart "测试目标" --offline   # the beginner path still works
```

Open a pull request that states: the problem, the approach, what you tested, and
which files a reviewer should read first. Smaller is better; a change to one
pipeline stage beats a rewrite of four.

## Adding or changing an agent role

1. Edit `agents/<role>.md` - role, inputs, outputs, and the rules it may not break.
2. Declare it in `config/agents.yaml` with its capability grants.
3. Map those capabilities in `config/mcp-map.yaml`; every grant has to resolve.
4. Add a case in `tests/test_routers.py`, then run `./nexus audit` on an example
   run to confirm `agents` reports no unresolved grants.

## Adding an MCP capability

Edit `config/mcp-map.yaml` under `capabilities`, list providers in preference
order with `tier`, `tool` and `arguments`, then confirm `./nexus mcp caps` shows
the chain you intended. A capability with no reachable provider is fine - the
router records the failure and continues - but say so in the pull request.

## Tests and examples

Every behavioural change needs a test in `tests/`. Use the fixtures in
`tests/conftest.py`; they build a throwaway workspace, so tests never touch a
real run. If you change report output, update `examples/` so the documented shape
matches reality.

## Release checklist (maintainers)

```bash
./nexus skill-check --release   # 1. one version everywhere (config/settings.yaml wins)
$EDITOR CHANGELOG.md            # 2. add a ## [x.y.z] section
./nexus open-source build       # 3. sanitized artefact under dist/
./nexus open-source check       # 4. gate must be green before publishing
python3 -m pytest tests/ -q
```

`open-source build` writes `dist/nexus-deep-research/` plus a zip with caches,
runs and non-publishable material removed and host paths masked. Nothing in the
working tree is deleted: publishing is a build step, not a cleanup chore.

## Style

Type hints on public functions, structured logging through `util.get_logger`, and
error messages that say what to type next. No drive-by refactors: if a file is not
part of the change, leave it alone.
