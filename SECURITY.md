# Security Policy

NexusSearch is a **local-first** research skill. It has no server, no telemetry and
no account. Everything below is enforced by code or by a check you can run.

## What this project guarantees

- **No credentials in the repository.** Secrets are referenced as environment
  variables only (`GITHUB_PERSONAL_ACCESS_TOKEN`, `NEXUS_*` overrides). MCP config
  lives in your host's own config file, never in a skill folder.
- **No user content leaves the machine by design.** Outbound calls are limited to the
  endpoints you configure (`searxng` base URL, `github`, `context7`, public web fetch).
  `nexus --offline` disables all of them and still completes a full research loop.
- **Workspace containment.** A run writes only inside its own workspace
  (`input/`, `graph/`, `evidence/`, `state/`, `output/`, `agents/logs/`). The target
  project is read-only by contract; `context-agent` and `analyst` never edit source.
- **Prompt-injection surface is treated as data.** Web pages, READMEs and issue text
  enter the graph as `evidence` with a source and a confidence score. A claim cannot
  become a conclusion without passing the verification loop and the quality gate, so
  a hostile page cannot silently instruct the report.
- **Log redaction.** Tool-call logs drop `Authorization`, `Cookie` and token-shaped
  strings before they are written to `agents/logs/`.

## Report a vulnerability

Open a private report through the repository's **Security -> Report a vulnerability**
tab, or email the maintainer directly. Do not file a public issue for an unfixed
vulnerability.

Include:

1. Version (`nexus --version`) and commit SHA if you built from source.
2. The exact command line and the MCP servers configured (`nexus doctor` output).
3. Whether the run had network access (`--offline` or not).

We aim to acknowledge within 5 business days and ship a fix or a documented
mitigation within 30.

## Known limits (read before using on sensitive material)

| Limit | Practical consequence |
| --- | --- |
| The runtime calls whatever MCP servers you configured | a malicious or compromised server can return crafted content; treat third-party servers as untrusted input |
| `fetch` / browser tools retrieve arbitrary URLs | do not point the skill at URLs whose content you are not allowed to read |
| Reports are plain files in `output/` | they inherit your filesystem permissions; nothing encrypts them |
| `memory/research-patterns.json` persists across runs | it can retain phrasing derived from confidential sources; mask it before publishing a workspace |

## Self-check before you publish anything

```bash
./nexus audit                 # secret-shaped strings inside a run
./nexus skill-check           # spec conformance of the skill
./nexus open-source check     # privacy + secrets + governance gate for the whole tree
./nexus open-source build     # the same tree, sanitized, into dist/ for sharing
```

`open-source check` fails on any home-directory-shaped absolute path (a `/home/<user>/`
or `/Users/<user>/` prefix, or a Windows `C:<backslash>Users<backslash>` prefix) and on
credential-shaped strings, so an example workspace cannot accidentally leak a username
or a token into a published repo. `open-source build` masks those paths to `$HOME/`
instead of asking you to delete files by hand.

## Rotating a leaked token

If a token ever appears in a chat log, screenshot or commit: revoke it at
<https://github.com/settings/tokens> first, then update the MCP config and re-run
`nexus doctor`. This project cannot revoke it for you.
