# unit-test-enabler

Containerized coding agents that enable upstream unit tests (`%check`)
in openSUSE Factory and SUSE Linux Enterprise packages, one package per
container, from implementation to a submit request ready for human
review.

The container image, the scripts and an example data directory are in
this repository. You bring your own credentials (as podman secrets), your
package list, the openSUSE-packaging skill and access to the internal
build service if you work on SLE.

Each container takes one package from `sle15_fix_order.csv` and works on
it unattended:

1. Factory phase. The worker model (DeepSeek V4 Flash, helmcode) enables
   the upstream tests in the devel project, following
   `ENABLE_TESTS_PROMPT.md` and the lessons files. It must pass a local
   `osc build --vm-type=kvm` with `%check` running.
2. Review loop. A reviewer model (`REVIEWER_MODEL`, default DeepSeek V4
   Flash in a separate, read-only session) reviews the change. The worker fixes the findings and rebuilds locally, until the
   reviewer approves or the round limit is reached.
3. Publish. The worker commits to the home branch (OBS), or pushes to a
   fork and opens a "WIP:" PR (git workflow).
4. Observe. The orchestrator polls the OBS builds and compares them with
   the devel project. It only waits for targets the devel builds, gives up
   on blocked or churning ones after a grace, and reports the rest as
   pending. For new failures, the worker reads every failed
   log, fixes the cause, gets the fix reviewed and pushes again.
5. Submit. Once the builds are green, the worker creates the SR (or
   removes the WIP prefix) and records the OBS test counts.
6. SLE phase. The same for the maintained SLE 15 codestreams (`osc
   mbranch` on IBS) and SLFO. It runs only after the Factory request
   exists, and every request references it. With `SUBMIT_SLE=0` (the
   default) it builds in the IBS home project and writes
   `results/<pkg>/sle/submit_plan.md` instead of submitting.
7. Record. The container writes `results/<pkg>/progress-row.md`, and the
   host merges it into `progress.md`.

If the worker reports a result it cannot fix (impossible, blocked,
already enabled), it must give evidence. The reviewer verifies the claim
and can send the worker back.

## Setup

```bash
git clone https://github.com/bzoltan1/unit-test-enabler.git && cd unit-test-enabler
cp -r data-example ~/test-enabler-data   # then put your package list there
./setup.sh                               # first run creates enabler.conf, then stops
$EDITOR enabler.conf                     # identity, hosts, data dir, credential paths, skills source
./setup.sh --smoke                       # checks, skills, secrets, image, end-to-end smoke test
host/run.sh $(host/next-packages.sh 2)
```

`setup.sh` checks the host, builds `skills/`, creates the podman secrets
and builds the image. `--smoke` then starts a throwaway container and checks both OSC
logins, both Gitea SSH logins, the Gitea API, the guard, a real KVM
build (`which`), both models and the Bugzilla MCP. Every step can be
rerun.

The data directory (`ENABLER_DATA`) is separate from the code. It holds
the package list, the master prompt, the lessons and `progress.md`; see
`data-example/README.md` for the format. `host/build-image.sh` copies the
prompt and lessons into the image, so rebuild after editing them.

`host/export-bundle.sh` packs the working tree (without `enabler.conf`
and results) into a tarball for hosts without GitHub access.

Deployment-specific values are settings in `enabler.conf`, not code:
- `IBS_API`: API URL of the internal build service (IBS).
- `IBS_GITEA`: the internal Gitea host.
- `IBS_TEA_LOGIN`: its tea login name.
- `INTERNAL_CA_REPO` / `INTERNAL_CA_PACKAGE`: optional private CA.
- `BUGZILLA_URL`.

The prompt and lessons may use `@IBS_API@`, `@IBS_GITEA@` and similar
placeholders.

## Required resources

Host:

| item | requirement |
|---|---|
| CPU arch | x86_64 (the build VM and the opencode binary) |
| podman | rootless, 4.3 or newer (`--userns=keep-id:uid=`); tested with 6.0.2 |
| KVM | `/dev/kvm` readable and writable by the user (kvm group); no privileged container |
| CPU | 4 per parallel container (`BUILD_JOBS`, default 4) |
| RAM | `BUILD_MEMORY` (default 8 GB) + about 2 GB per parallel container. With 58 GB, 3 to 4 parallel at 6144 MB is safe |
| disk | 3.4 GB image + up to `BUILD_DISK_MB` (default 20 GB) + a few GB of package cache and checkouts per running container. Build roots are removed when a run ends |
| tools | python3, curl, ssh-keygen (setup checks) |
| network | api.opensuse.org, src.opensuse.org, registry.opensuse.org, github.com (opencode release), the model endpoints, Bugzilla, and the internal hosts in `enabler.conf` (`IBS_API`, `IBS_GITEA`, `INTERNAL_CA_REPO`), which usually need the company network or VPN |

Accounts and credentials (paths in `enabler.conf`; all files are read by
`host/create-secrets.py` only):

| credential | used for |
|---|---|
| osc config with `[https://api.opensuse.org]` and `[$IBS_API]` sections (password or `sshkey=`; no keyring) | OBS and IBS |
| IBS SSH key, no passphrase | osc signature auth to IBS |
| SSH keys registered on `$IBS_GITEA` and src.opensuse.org, no passphrase | git push to forks |
| tea config with logins `$OBS_TEA_LOGIN` and `$IBS_TEA_LOGIN` (tokens) | git-obs / tea PRs |
| `$IBS_GITEA` API token | Gitea API |
| Bugzilla API key | bugwarden MCP, read only |
| opencode.json with the worker and reviewer providers (API keys) | models |
| Google ADC file for Vertex AI | only for `google-vertex/...` models |

Typical runs on the first packages:
- A Factory change plus 4 SLE codestreams took 1.1 h and 8 agent calls.
- A package with 815 tests needed 3 review rounds and 1 build round, and
  took 4.7 h, mostly waiting for OBS.
- A package whose tests already ran took 15 min.

## Usage

```bash
host/next-packages.sh 5         # next packages without a result (list_status valid/weak)
host/run.sh dovecot gvfs        # one container per package
host/status.sh                  # overview (also merges progress.md)
host/requests.sh --open         # human review / build status of the agents' requests and PRs
podman logs -f te-dovecot       # orchestrator log
host/sync-progress.sh           # merge results into progress.md by hand
host/create-secrets.sh          # after rotating a credential
host/build-skills.sh            # rebuild skills/ from the source (see below)
```

Redeploy a package with `podman rm te-<pkg>` and then `host/run.sh <pkg>`.
The run resumes from `results/<pkg>/state.json`. For a clean restart, also
run `rm -rf results/<pkg>; podman volume rm te-work-<pkg>`.

## Evidence: did the test prevent a customer bug?

This is a second, separate job. It does not enable tests. It takes a
package and proves, or disproves, that the unit test we would enable
would have caught a bug that reached customers.

```bash
host/evidence-next.sh 5          # next packages with no evidence result
host/run-evidence.sh gvfs        # one container per package
host/evidence-status.sh          # overview
```

The job follows `EVIDENCE_PROMPT.md` in the data directory. For every
Bugzilla bug with a maintenance update or a PTF it must show the bug,
the affected version, the fix, the test in the affected tree, and two
builds: the affected source with the test enabled fails for the bug's
reason, and the fixed source passes. A second model audits every proven
case and reruns the key command. Results go to
`results/evidence/<pkg>/evidence.json`, `report.md` and `logs/`.

The verdicts are `PROVEN`, `RECURRENCE_ONLY` (the fix added the test, so
it prevents a regression but could not have caught the original),
`NOT_CAUGHT`, `NOT_RUNNABLE`, `NO_UPDATE`, `INCONCLUSIVE` and
`NOT_A_CASE`. Nothing is submitted, and the package branches are not
modified.

`bin/buglist` prints the cached candidates, for example
`buglist gvfs --fixed --non-security`.

## Learnings

Every run ends with a learnings step, whatever the outcome:
`results/<pkg>/learnings.md`. The worker writes four fixed sections
from its own reports, reviews and logs:

- Package facts
- Reusable lessons, each with evidence
- Prompt and tooling problems, each with a concrete fix
- Reviewer findings it agreed with

The orchestrator then appends the run facts mechanically: rounds,
blocker and major findings, guard blocks and cost.

```bash
host/collect-learnings.py                    # results/LEARNINGS_INBOX.md, unreviewed runs only
host/collect-learnings.py --mark-reviewed gvfs dovecot
host/backfill-learnings.sh <pkg>...          # runs from before this feature
```

The inbox puts "Prompt and tooling problems" first: those are the fixes
for this system. Nothing goes into the containers automatically. Promote
what holds up into `$ENABLER_DATA/LESSONS_CONTAINERS.md`, or fix the
prompt, AGENTS.md or the tools, then run `host/build-image.sh`. Every
`LESSONS*.md` and `*_learnings.md` in the data directory goes into the
image. Review by a person keeps one model's wrong conclusion from
spreading to all later runs.

## Cost

opencode logs the token counts of every model step. `host/costs.py`
prices them:

```bash
host/costs.py               # per package and per model, total
host/costs.py dovecot -v    # per agent call
host/costs.py --csv
```

- Models with known list prices (for example Vertex): opencode's own
  cost, from models.dev.
- `prices.local.json` (private, not committed) overrides `prices.json`,
  for example to set a subscription model to 0.
- Worker (helmcode): priced from `prices.json` (USD per 1M tokens for
  input, output, cache read and cache write). Until you fill in the
  contract prices, its tokens are shown as "unpriced". Rebuild the image
  after editing, so the containers price it too.
- Each run writes `results/<pkg>/cost.json` after every agent call, and
  the cost goes into its progress row and learnings.
- `COST_LIMIT_USD=5 host/run.sh <pkg>` stops a package (outcome
  `limit_cost`) once its priced cost passes the limit. Only priced models
  count.
- These are list-price estimates. The provider invoices (GCP billing,
  helmcode) are authoritative.

## Requests and PRs: human review status

```bash
host/requests.sh             # every request/PR the agents created; also results/REQUESTS.md
host/requests.sh --open      # hide landed and closed ones
host/requests.sh --landed    # only accepted and merged ones
host/requests.sh gvfs --json
```

- For OBS/IBS requests it shows the state, every review (user, group or
  project, state, comment), people's comments, the supersede chain
  (followed to the newest request) and the source package's build
  results.
- For Gitea PRs it shows the state, merge, WIP, requested reviewers,
  reviews (approved or changes requested), comments and the commit
  status (CI, staging bot).
- It also reads the request and PR links from `progress.md`, so manual
  and hand-made work shows up as well.

Each item gets one of these labels:

| label | meaning |
|---|---|
| LANDED | accepted or merged |
| ACTION | someone has to look: declined, changes requested, a person commented after our last comment, or the source build fails where the target package does not |
| PLAN | dry run: a submit plan waits for you |
| WAITING | open, nothing new |
| CLOSED | revoked, superseded or closed without merge |

Bot comments and build failures that the target package has too are not
counted as needing action.

## Review rounds

```bash
host/review-report.py        # per package and phase; also results/REVIEW_REPORT.md
host/review-report.py -v     # every round
```

For every phase the report shows:
- the review rounds in order, for example `CR(2M,5m) > A(2m) > A(1m)[b1]`:
  the verdict, the findings by severity, and why the round happened
  (initial, `[b1]` after build round 1, `[claim]` claim check);
- rounds to the first approval, review rounds after build failures, and
  build fix rounds;
- time to approval, phase time and reviewer cost;
- the live state of every request or PR. "LANDED" means accepted or
  merged.

The summary gives the share approved in the first round, the mean rounds
to approval, and how many requests have landed. Each run also records
its review history in `state.json`. Each round keeps the diff it
reviewed as `review-N.diff`.

## Comparing reviewer models

`SHADOW_REVIEWERS` (comma-separated `provider/model`, in `enabler.conf`
or the environment) runs extra reviewers in parallel with the primary.
Each one gets the same prompt and the same checkout, and writes
`review-N.shadow-<model>.json`. Only the primary's verdict drives the
run. A shadow that fails leaves a record with the error.

```bash
SHADOW_REVIEWERS=google-vertex/gemini-3.5-flash,helmcode/qwen3.6 host/run.sh <pkg>
SHADOW_REVIEWERS=... host/shadow-replay.sh <pkg>...   # finished runs: last round per phase
host/compare-reviewers.py    # summary per model + results/REVIEWER_COMPARISON.md
```

The comparison shows, per model:
- agreement with the primary verdict;
- "stricter" (asked for changes where the primary approved) and "laxer"
  (the opposite);
- blocker and major findings per review;
- failed reviews;
- cost and minutes per review.

The markdown lists every round's findings side by side. Agreement only
measures similarity to the primary, not quality: judge the findings.

Shadows review independently:
- Each one works in its own view of the results: links to the worker
  report, the diff, the build logs and its own earlier reviews.
- opencode denies it any access to `/results`, so it can't see other
  verdicts, transcripts, `state.json` or the orchestrator log.
- The worker and the primary reviewer may not read shadow verdicts.
- Reviewers are capped at `SHADOW_MAX_STEPS` / `REVIEWER_MAX_STEPS` tool
  steps, after which they must answer in text, and identical repeated
  tool calls are refused.
- A verdict given only in the final reply is recovered and counted in
  the "text" column, since the file instruction wasn't followed.
- `MODEL_LIMITS` tells opencode the context size of models your provider
  config doesn't describe, so it compacts long reviews before the backend
  rejects them.

A replay can only use the last round, because the checkout no longer
matches earlier rounds, and the last round is usually an approval. So a
replay measures false alarms, not whether a model catches problems. Live
shadows on new runs also cover the rounds where the primary asked for
changes. Their providers must be in `OPENCODE_CONFIG`; rerun
`host/create-secrets.sh` after adding one.

## Settings (environment of host/run.sh)

| variable | default | meaning |
|---|---|---|
| SUBMIT_FACTORY | 1 | create real Factory SRs / PRs |
| SUBMIT_SLE | 0 | 0 = dry run: IBS branch builds, plan file instead of MR/PR |
| PHASES | factory,sle | phases to run |
| LOCAL_BUILD_LIMIT | 8 | local builds per phase |
| REVIEW_ROUNDS | 4 | review rounds per phase |
| OBS_ROUNDS | 4 | OBS/IBS build fix rounds per phase |
| WALL_HOURS | 24 | total time per package |
| BUILD_MEMORY / BUILD_JOBS | 8192 / 4 | build VM size |
| BUILD_DISK_MB | 20000 | build VM disk; the worker can rerun one build with more |
| SHADOW_REVIEWERS | empty | extra reviewer models for comparison (see above) |
| SHADOW_MAX_STEPS / REVIEWER_MAX_STEPS | 120 / 200 | tool steps before a final answer is forced |
| MODEL_LIMITS | empty | `provider/model=context:output` for models without limits in the opencode config |
| COST_LIMIT_USD | 0 (none) | stop a package when its priced model cost exceeds this |
| BUILD_WAIT_HOURS | 6 | end the build observation after this, report the rest as pending |
| TARGET_GRACE_HOURS | 3 | give up on one target that is not final after this |
| BUILD_CHURN_LIMIT | 3 | give up on a target that rebuilds this often with unchanged sources |
| CPUS / MEMORY | unset | cgroup limits (need delegated cgroup controllers) |
| STATUSES (next-packages.sh) | valid weak_reference | which list rows to pick |

## Progress tracking with many containers

- A container never writes a shared file except `results/index.jsonl`,
  where it appends one line under `flock`. Its result goes to
  `results/<pkg>/progress-row.md`, written with an atomic rename.
- `host/sync-progress.py` merges those rows into `progress.md`:
  - It takes an exclusive `flock` on `progress.md.lock`, so concurrent
    syncs serialise.
  - It upserts one row per package. Only rows marked `[test-enabler`
    are replaced; rows written by people are never touched.
  - It writes a temp file and renames it into place.

  The sync runs automatically when each container exits (started by
  `run.sh`) and on every `status.sh`. Tested with 20 concurrent syncs
  while rows were being rewritten: one row per package, all current.
- Claiming a package: the container name `te-<pkg>` is the claim.
  podman refuses a second container with the same name, so two parallel
  `run.sh` calls for one package cannot both start.
- Not covered:
  - Editing `progress.md` by hand while a sync runs (editors don't take
    the lock). Use `host/sync-progress.sh --lock-only -- $EDITOR progress.md`.
  - Several hosts: claims and the lock are per host. Use one deployment
    host per data directory, or split the list between hosts. The worker's
    "existing work" check, which looks for open SRs and PRs, is the last
    line of defence.

## Skills

The image carries two agent skills in `skills/`, which is generated by
`host/build-skills.sh` (also run by `setup.sh`) and never committed:

- `opensuse-packaging`: the openSUSE-packaging agent skill by Martin
  Pluskal. It is the core packaging knowledge of this work. It is **not**
  in this repository: set `SKILLS_SRC` (a local directory containing
  `opensuse-packaging/`) or `SKILLS_GIT` / `SKILLS_REF` in `enabler.conf`.
- `upstream-tests-packaging`: recipe for shipping upstream test suites,
  in `skills-src/`.

`host/sanitize-skills.py` builds the copy that goes into the image:

- It drops `scripts/leap-sync.sh`, which pushes and opens a PR through
  the Gitea API with `curl`, a path the submission gates cannot see.
- It removes personal home project sections, replaces a hard-coded
  `.changes` author with git's identity, and replaces mail addresses,
  SSH key file names and helper paths with placeholders.
- It scrubs `OBS_USER`, `GIT_NAME`, `GIT_EMAIL` and
  `SANITIZE_EXTRA_IDS` from the result.
- It fails if a structural rule stops matching, or if anything personal,
  a home path, a private key or a token-shaped string remains.
- It writes `skills/MANIFEST.sha256`, which every image build checks,
  and `skills/PROVENANCE`, which records the source.

## Security model

- Credentials are podman secrets. They are not in the image or the
  bundle (`export-bundle.sh` scans for this), and the container installs
  them in its own home only.
- `/opt/enabler/guard` wraps `osc`, `git`, `git-obs` and `tea`:
  - Writes are allowed only to `home:$OBS_USER*` and `$OBS_USER/*` forks.
  - `osc api` writes are limited to the same projects.
  - Requests and PRs are blocked until the orchestrator opens the gate:
    after reviewer approval and green builds, and for SLE only after the
    Factory request exists.
  - accept, decline and merge are always blocked.
  - Blocked attempts are logged to `results/<pkg>/guard.log`.
- opencode denies raw HTTP writes (`curl -X POST`, `--data`, and so on)
  for both agents. The reviewer cannot edit files or run any publishing
  command. Bugzilla goes through bugwarden `--read-only` with its
  packaged policy, and the write tools are disabled.
- Nothing prompts: every permission is allow or deny, `opencode run
  --auto`, ssh BatchMode, `GIT_TERMINAL_PROMPT=0`, no pagers or editors.
- Limits: the guard and the deny patterns are filters, not a sandbox. A
  model that writes its own HTTP client in Python could still use the
  tokens. The real boundary is what the credentials can do. If that
  matters, use accounts and tokens limited to the needed scopes.

## Layout

- `setup.sh`, `enabler.conf.example`: setup and configuration.
- `LICENSE`: GPL-2.0-or-later.
- `host/leak-check.sh`, `.leak-allow`: pre-commit check against leaking deployment data.
- `Containerfile`: Tumbleweed, osc/build/qemu-kvm, spec-cleaner,
  bugwarden, opencode (pinned release with sha256) and `skills/`. It
  patches `build-vm-kvm` to avoid io_uring, which podman's seccomp
  profile blocks.
- `bin/orchestrate.py`: the state machine (limits, gates, polling, records).
- `bin/local-build`, `bin/obs-results`, `bin/gitea-fork`, `bin/smoke-test`: tools inside the container.
- `bin/costlib.py`, `prices.json`: token and cost accounting.
- `bin/entrypoint.sh`, `bin/make_opencode_config.py`: container setup.
- `guard/guard.py`: the submission gate.
- `agent/AGENTS.md`: container rules that map the master prompt to the container.
- `host/`: deployment, secrets, progress sync, skills build, learnings, costs, bundle export.
- `skills-src/`: skills shipped with this repository.
- `list-tools/`: validate a package list against OBS, IBS, Repology,
  Fedora, Debian and the shipped build logs (`ENABLER_DATA`, `IBS_API`
  from the environment; hand-checked findings in
  `$ENABLER_DATA/list_overrides.json` and `buildlog_overrides.json`).
- `data-example/`: example data directory (prompt, lessons, list format).
- `results/<pkg>/`:
  - `state.json`, `orchestrator.log`
  - `agent-logs/`: full transcripts and prompts
  - `<phase>/`: `report*.json`, `review-*.json`, `change.diff`,
    `logs/` (every local and OBS build log), `submit_plan.md`
  - `progress-row.md`, `learnings.md`, `cost.json`

## Contributing changes

The working copy is a clone of this repository. Private and generated
files are ignored: `enabler.conf`, `results/`, `skills/`, and the
prompt and lessons copies under `agent/`. Install the leak check once
per clone:

```bash
ln -sf ../../host/leak-check.sh .git/hooks/pre-commit
host/leak-check.sh --all     # check every tracked file
```

The hook rejects a commit whose added lines contain any of these:
- a value from your `enabler.conf` (user, name, mail, `SANITIZE_EXTRA_IDS`,
  the internal hosts, the data path, `$HOME`)
- a private key or a token-shaped string

Intentional mentions go into `.leak-allow`, one regex per line.

## License

GPL-2.0-or-later, see `LICENSE`. The openSUSE-packaging skill is not part
of this repository and keeps its author's terms.
