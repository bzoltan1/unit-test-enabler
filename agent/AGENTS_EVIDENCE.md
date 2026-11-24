# Evidence job: container rules

You run unattended in a podman container. The task is
`/opt/enabler/evidence.md`. That file is the only job prompt for this
run. Do not follow the enablement task in `ENABLE_TESTS_PROMPT.md`. The
enablement results under `/results/<pkg>/` are read only inputs, and the
enablement phases, gates and report files are not yours.

## Paths

- Work in `/work/<pkg>/`.
- Write results only under `/results/evidence/<pkg>/`
  (`ENABLER_PHASE_DIR`).
- `/data` is read only. Never write under `/data`, `/results/<pkg>/`, or
  `progress.md`.
- Never touch `/work/.gates/`.

## Hard rules

- Read only on OBS, IBS, Gitea and Bugzilla. Never commit, branch, push,
  create or accept a request or PR, comment, or change a bug.
- Local builds only through `local-build`. Never call `osc build`
  directly. When it prints `LOCAL BUILD LIMIT REACHED`, follow the budget
  in the evidence prompt, not the enablement advice.
- Bugzilla through the `bugzilla_*` MCP tools (read only). A bug the
  server calls not accessible is restricted. Say so, do not retry.
- Do not lower the bar to fill the report. Zero PROVEN cases with honest
  verdicts is a good report.
- Write `evidence.json` after every candidate, to a temp file then `mv`.
  If it exists at start, load it and continue.
- Nothing here prompts and nobody answers. Never wait for input. No
  editors, pagers or interactive commands.
- Never print, log or save a secret. Build and job logs can contain
  tokens and passwords. Reference the key, not the value.
- The reviewer audits every PROVEN case and writes `review.json` and the
  ledger row. The worker fixes or downgrades what the reviewer rejects.

## Tools

- `osc` (aliases `obs` and `ibs`), `git-obs` / `tea`, `local-build`,
  `obs-results`, `buglist`.
- Skills: `opensuse-packaging`, `upstream-tests-packaging`.
- `/data` holds the list, the bug cache and the changelogs.
