# Unit test enabler: container rules

You run unattended inside a podman container. Nobody answers
questions. An orchestrator script starts you for one phase step at a
time and reads the report file you write. The task description is
`/opt/enabler/agent/prompts/ENABLE_TESTS_PROMPT.md` (the master
prompt). Lessons from earlier packages are in
`/opt/enabler/agent/lessons/`. Read both before you change anything.

## How the master prompt maps to this container

- Paths. `/tmp/opencode/<pkg>/` in the prompt means `/work/<pkg>/`
  here. Keep every checkout and scratch file under `/work/<pkg>/`:
  `/tmp` does not survive into later steps. The data directory
  (`.../sle15_review/` in the prompt) is `/data/` here, for example
  `/data/sle15_fix_order.csv`, read only. Never write under `/data/`. Results, logs and reports
  go to the phase directory the orchestrator gives you
  (`/results/<pkg>/<phase>/`).
- Credentials are already installed: `osc` (aliases `obs` and `ibs`),
  `git-obs` / `tea` (logins `@OBS_TEA_LOGIN@` for src.opensuse.org and
  `@IBS_TEA_LOGIN@` for @IBS_GITEA@), SSH for both Gitea hosts.
  @IBS_GITEA@ API token: `~/.config/enabler/ibs_gitea.token`. The
  internal build service (IBS) API is @IBS_API@ (`osc -A ibs`); OBS is
  `osc -A obs`. Bugzilla (read only): the `bugzilla_*` MCP tools. A bug the
  server calls not accessible is restricted; say so, do not retry. Never print, log or
  commit credential values.
- "Ask me" / "show me" / "wait for my approval" in the prompt are
  replaced by: write the item into your report file and finish the
  step. A reviewer model checks your work, and the orchestrator opens
  the submission gates. Never wait for input.
- "Pick the next package" and "Move on" do not apply. The
  orchestrator gives you one package and one phase.
- Recording in `progress.md` is done by the orchestrator from your
  report. Do not edit `progress.md`.
- The step limits in the orchestrator message replace the limits in
  the prompt.

- Identity. The OBS/IBS and Gitea user is `@OBS_USER@`. Where the
  prompt or lessons name another user's home project, use
  `home:@OBS_USER@`.

## Hard rules

- Writes are allowed only to `home:@OBS_USER@*` projects and to
  `@OBS_USER@/*` git forks. A wrapper around `osc`, `git`, `git-obs`
  and `tea` enforces this and also blocks requests and PRs while the
  matching gate is closed. When it prints `ENABLER GUARD BLOCKED`,
  do not try to get around it (no curl to the API, no other tools).
  Do what the message says.
- Never touch `/work/.gates/`.
- Nothing here asks for permission and nobody answers questions.
  Never wait for input or use interactive commands (editors, pagers,
  `osc vc` without `-m`, `git rebase -i`).
- Local builds go through `local-build` only (it wraps
  `osc build --vm-type=kvm`, saves the log, counts attempts and
  prints whether `%check` ran). Do not call `osc build` directly.
- Never accept, decline, merge or approve requests or PRs.
- Never mask test failures (`|| :`, `|| true`, non-fatal `%check`).
- Do not claim a test count you did not read in a build log.
- When the task is impossible, or needs a decision a human must take,
  say so in the report with evidence and stop. That is a valid
  result. Do not loop.
- Keep `.changes`, commit and request messages short and plain, in the
  style section of the master prompt. No em dashes, no AI wording.

## Tools in PATH

- `local-build <repo> <arch> [extra osc build args]`: run inside the
  package checkout.
- `obs-results <apiurl> <project> <package>`: compact per repo/arch
  build state table.
- Bugzilla: the `bugzilla_*` MCP tools (read only).
- `gitea-fork <src.opensuse.org|@IBS_GITEA@> <owner/repo>`: create your
  fork when `git-obs repo fork` fails (raw curl writes are blocked).
- git-obs puts the subcommand options after the repo:
  `git-obs -G @IBS_TEA_LOGIN@ repo clone pool/<pkg> --directory D`.
- osc commands that do not run inside a checkout need `-A ibs` for
  IBS (for example `osc -A ibs service runall source_validator`); osc
  defaults to OBS.
- `spec-cleaner` is installed. Missing tools: `sudo zypper in <pkg>`
  (only zypper runs as root).
- Skills: `opensuse-packaging`, `upstream-tests-packaging` (load them
  with the skill tool).
