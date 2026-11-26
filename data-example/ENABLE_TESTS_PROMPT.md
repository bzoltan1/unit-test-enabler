# Task: enable upstream unit tests in SLE packages, one package at a time

## Context
In an earlier session we found SLES 15 packages that don't run their
upstream unit tests, even though Fedora, Debian or Ubuntu run the tests
for the same package. We put these packages in priority order using
their Bugzilla history.

Working data is in the data directory (`$ENABLER_DATA`):
- `sle15_fix_order.csv`: the fix order (`priority_rank`), with the
  `difficulty` class and `effort` for each package
- `SLE15_fix_priority.md`, `SLE15_enablement_difficulty.md`: the method
- optional: cached specs and classification evidence from building
  the list

Packages live in one of three places:
- **OBS** (build.opensuse.org, `osc -A https://api.opensuse.org`):
  Factory/Tumbleweed and its devel projects
- **IBS** (the internal build service, `osc -A @IBS_API@`): SLE 15
  maintenance (`SUSE:SLE-15-SPx:Update`)
- **@IBS_GITEA@** (Gitea, git packaging workflow): SLE 16 / SLFO

Some Factory devel projects also use the git workflow on
**src.opensuse.org** (for example `java-packages/<pkg>`). There the
Phase A change is a PR from a fork, and every push to the PR branch
rebuilds the PR's staging project
(`home:autogits_obs_staging_bot:<org>:PR:<n>`) on about 30
repositories.

## Reference requests (match their style and quality)
- OBS devel, freerdp: https://build.opensuse.org/request/show/1397741
  Passes `-DBUILD_TESTING=ON` so the existing `%ctest` actually runs
  tests. Sets `LD_LIBRARY_PATH` because RPATH is disabled. Skips the
  H.264 test (no encoder) and adds xvfb-run plus xorg-x11-server-Xvfb.
  183 tests pass on TW x86_64 and i586.
- OBS devel, xrdp: https://build.opensuse.org/request/show/1397742
  Adds `--enable-tests`, `%check` with `make check`, and
  check-devel/libcmocka-devel. Drops the installed paintertest helper.
  Guarded with `%if 0%{?suse_version} > 1500` so the SLE 15 builds
  don't change. 213 tests pass.
- IBS maintenance, freerdp SLE-15-SP7: (an internal IBS maintenance request)
  A `maintenance_incident` that adapts the change to the older 3.10.3
  test set (no skips needed). All 5 architectures (aarch64, i586,
  ppc64le, s390x, x86_64) build and pass 150 tests.

## Credentials and file locations (read before starting)
- **Ask for all permissions once, at the start of the session.**
  Before step 1, list every credential and access you will need:
  `osc` for api.opensuse.org and @IBS_API@, @IBS_GITEA@ / Gitea
  (`git-obs`, `tea`), Bugzilla, and anything else. Ask me to approve
  them in a single question. Don't stop mid-task later to ask for
  new credentials. If you hit one you didn't foresee, stop, explain
  why you need it, and ask once.
- **Never ask for access to my home directory** (`$HOME`) and
  never write there. Allowed locations:
  - the data directory (`$ENABLER_DATA`) (data,
    `progress.md`)
  - `/tmp/opencode/` for everything else: `osc` checkouts, git
    clones, build roots, logs, scratch files

  Run `osc co` / `osc branch` checkouts and `git clone` under
  `/tmp/opencode/<pkg>/`, and point `osc build` at a build root
  under `/tmp` (for example `--root /tmp/opencode/buildroot`).
- Use the existing `osc` / git configuration as it is. Don't edit,
  copy or print config files or tokens in my home directory. If a
  tool needs a config path, pass it on the command line or through
  an environment variable pointing into `/tmp/opencode`, or ask me.
- Never print, log or commit credential values.
- Known credential locations (pre-approved, no permission prompt):
  - @IBS_GITEA@ API token: the IBS Gitea token file
    (user `@OBS_USER@`; SSH `gitea@@IBS_GITEA@` with
    `<IBS Gitea SSH key>`). `tea --login @IBS_TEA_LOGIN@` and `git-obs`
    are already configured.
  - src.opensuse.org: `tea`/`git-obs` login `opensuse` is configured.
  - `osc` for both api.opensuse.org and @IBS_API@: your osc config
    as it is.
  Do not report "no credentials" before checking these.

## Writing style for .changes, commit and SR messages
- Keep it brief and plain. A `.changes` entry is usually 1 to 3
  bullets. A commit or SR message is a short summary plus 1 to 3 short
  paragraphs at most.
- Write like a packager, not like a chatbot. Say what changed, why,
  and the test result. Nothing else.
- No em dashes or en dashes. Use a period, comma or parentheses
  instead.
- No AI-style wording: no "This change", "comprehensive", "robust",
  "ensure", "leverage", "seamless", "enhance", "streamline", no
  marketing tone, no filler or summary sentences, no emojis.
- No bold text, headings or nested lists in these messages.
- Lines wrap at 72 characters.
- Good `.changes` example:
  ```
  - Enable the upstream unit tests: build with -DBUILD_TESTING=ON
    and run them with %ctest. Skip TestFreeRDPCodecH264 (no
    encoder in the build environment).
  ```
- Reread the text before showing it to me, and remove anything that
  reads like generated prose.

## Workflow for each package
1. **Pick** the next package by `priority_rank` that has no row in
   `progress.md` yet. Tell me which one before you start.
2. **Check for existing work.** Search OBS, IBS and @IBS_GITEA@ for
   open or recent requests that already enable tests. If Factory
   already runs the tests, skip Phase A and backport the Factory
   change to SLE, citing the Factory change. If both Factory and SLE
   already run the tests, record that and skip the package.
3. **Locate the sources.** Find all three:
   - the Factory devel project (`osc -A https://api.opensuse.org
     develproject openSUSE:Factory <pkg>`)
   - the IBS codestream(s) where SLE 15 maintains the package (for
     example `osc -A @IBS_API@ maintained <pkg>`). The SLE
     package name can differ from Factory (for example `freerdp2` in
     SLE vs `freerdp` in Factory). **`maintained` also lists product
     lines that are out of life cycle.** Keep only the codestreams
     listed in `supported_codestreams.txt` in the data directory, drop
     the rest, and record them as skipped in the report.
   - the SLFO / @IBS_GITEA@ repo, if there is one

   **Factory first is mandatory.** Every package gets two changes:
   - **Phase A, Factory:** an SR to the devel project, which then
     goes on to openSUSE:Factory.
   - **Phase B, SLE:** a maintenance request on IBS for each
     maintained SLE 15 codestream that is still supported (see
     `supported_codestreams.txt`), plus a @IBS_GITEA@ PR where it
     applies. Never open a request for a product line that is out of
     support.

   Phase B starts only after the Phase A SR is accepted, or at least
   submitted to Factory. Every Phase B request must reference the
   Factory SR.
4. **Check the test history.** Find out whether the build time tests
   were ever enabled and later turned off. Do this separately for
   Factory and for each SLE codestream, because the history can
   differ.
   - Search the `.changes` file for `check`, `test`, `%check`,
     `disable`, `skip`, `BUILD_TESTING`, `make check`, `ctest`,
     `pytest`, `testsuite`.
   - Read the spec history: `osc log`, `osc rdiff` between revisions,
     `git log -p -- *.spec` on @IBS_GITEA@, and Factory requests
     (`osc rq list -s all -P <prj> -p <pkg>`). Look for a removed
     `%check`, a dropped test switch, `|| :` / `|| true` after the
     test command, or `%if 0` around `%check`.
   - If tests are disabled by a patch (`*notests*.patch` and the
     like), read the whole patch. It often does more than skip the
     run, for example it also removes test compilation from the
     default build target. Dropping it then makes `%build` compile
     the tests, and their dependencies must be available there too.
   - Use the bug references in the `.changes` entry or commit
     (bsc#, boo#, jsc#) and read those bugs.
   - If tests were turned off, write down when, by whom, in which
     codestream and why: the bug, the changelog text or the request
     message.
   - **Check whether the reason still applies** with the current
     version and build environment, for example a flaky test that
     upstream has fixed since, a missing dependency that now exists,
     an arch-specific failure, build timeouts, or network access.
     Prove it with a build, not an assumption.
   - If the reason still applies, don't just enable everything.
     Either skip only the affected tests (with a comment pointing at
     the original reason) or stop and report it to me.
   - If there is no history of the tests ever being enabled, record
     "never enabled".
5. **Research the test setup.**
   - Read how Fedora (`%check` in the spec) and Debian
     (`debian/rules`, `dh_auto_test`, `override_dh_auto_test`) run
     the tests: switches, skips, extra deps.
   - Check the upstream build system for the test switch
     (`BUILD_TESTING`, `--enable-tests`, `meson test`, `make check`,
     `pytest`, ...).
   - Check whether the package already has a `%check` that runs zero
     tests (freerdp had this).
   - A commented-out test call in Fedora (`#test`, `%dnl %check`)
     means they never got the suite working. Treat it as a warning,
     and read what they removed to get that far.
   - **Understand the test harness before skipping anything.** Find
     how tests are discovered and aggregated (suite classes,
     `AllTests`, regression arrays, pytest collection, ctest labels,
     Go packages). Look for aggregators that stop at the first
     failure, for example one JUnit test that loops over an array of
     sub-tests and calls `fail()` on the first bad one. Each build
     then shows only one failure per aggregator, so failures can
     never be found one build at a time.
   - Find the skip mechanisms the build system already supports:
     ctest `-E`, `pytest --deselect`/`-k`, `go test -skip`, Ant
     `<exclude>` or an exclude env var, Maven/Gradle excludes, meson
     test suites. Prefer these to deleting or editing test sources.
   - **Derive skips from causes, by searching the test sources,
     not by trial and error on OBS.** Grep for each cause that
     cannot work in an OBS build and list every hit with its cause:
     - network: external hosts, URLs, DNS, servers that need
       privileges
     - external test data: helpers that look for a data directory
       outside the tarball (an env var or property pointing at a
       separate repo), git LFS files
     - unpackaged dependencies: imports of libraries that openSUSE
       does not ship (check with `osc search` / `osc ls -b`).
       `%prep` usually deletes bundled jars and binaries, and
       pointing the build at a bundled binary is not acceptable
       anyway, so skip those tests.
     - hardware and environment: TPM, GPU, CPU features, kernel
       modules (SCTP), root, a display, `$HOME`, timezone or locale
     - timing: benchmarks, throughput tests, tight timeouts
     Skip the narrowest unit that has the cause (the test case or
     class, not the suite that contains it).
6. **Edit the spec** in a branch under `home:@OBS_USER@:branches:...`
   (OBS/IBS) or in a fork (Gitea; clone under `/tmp/opencode`, never
   in `$HOME`). Use the `openSUSE-packaging` skill.
   - Change as little as possible: add the test switch, `%check`,
     and only the BuildRequires the tests need.
   - Skip tests one by one, only when a test can't work in the build
     environment (network, hardware, encoder, timezone data). Add a
     short spec comment saying why each test is skipped.
   - Don't ship test helpers that the test switch installs.
   - Keep RPATH and LD_LIBRARY_PATH issues local to `%check`.
   - If the change would alter SLE 15 builds from a shared spec, guard
     it with `%if 0%{?suse_version} > 1500`.
   - **Every source edit must fail loudly when it does not apply.**
     A `sed` whose pattern matches nothing is a silent no-op. Use a
     helper in `%prep`, and match the call form actually used in the
     source (for example `addTest(...)` vs `addTestSuite(...)`):
     ```
     drop_line() {  # file pattern
       grep -q -- "$2" "$1" || { echo "'$2' not in $1"; exit 1; }
       sed -i "\|$2|d" "$1"
     }
     ```
   - Before deleting a test source file, grep for its references.
     Other test files may import, subclass or call it, and the test
     compile breaks.
   - Never mask failures: no `|| :`, `|| true` or `exit 0` after
     the test command, no non-fatal `%check`. Every skip is named
     and commented.
   - Don't repurpose an unrelated build property or variable to
     smuggle in a dependency (for example a test classpath entry
     passed through an LDAP jar property). Use the proper mechanism,
     or comment the workaround.
   - Keep BuildRequires sorted, one per line. Add test-only deps
     explicitly (junit, hamcrest, pytest, cmocka, ...). Don't rename
     or swap existing BuildRequires unless the tests need it, and
     then say so in `.changes`.
   - **Multi-line `%global`/`%define` bodies keep their line breaks.**
     A macro holding command arguments split with `\` expands into
     several shell commands, and the arguments are silently lost.
     Use a shell array in each section or a one-line macro, and
     check the result with `rpmspec -P <pkg>.spec`.
   - Size `_constraints` and the test runner's heap or parallelism
     from what you measured in the local build, not by guessing.
   - Remove temporary debug code (failure printers, `set -x`, extra
     logging) before the final push. If you parse JUnit XML, test
     `tc.find('failure') is not None`. Never write
     `tc.find('failure') or tc.find('error')`: in Python an element
     with no children counts as false, so every failure is dropped.
   - Add a `.changes` entry with `osc vc`. Use the real UTC time
     (`LC_ALL=C date -u "+%a %b %_d %T UTC %Y"`), never a made-up
     one. Keep one entry per change, and on later rounds amend it so
     it matches the final diff: the skips with counts, new
     BuildRequires, `_constraints`.
7. **Build and verify.** Do this once per phase.
   - **Local gate first. Nothing goes to OBS, IBS or a git branch
     (`osc ci`, `git push`) until all of these pass locally:**
     1. A full local `osc build` for Tumbleweed x86_64, running
        `%prep`, `%build`, `%check` and `%install`, with the build
        root under `/tmp/opencode`. For Phase B, also do one local
        build against the oldest SLE codestream you target. Older
        toolchains (JDK, Python, GCC, test framework versions)
        behave differently.
     2. The test counts (run, passed, skipped) and the `%check`
        wall time and peak memory, read from the log. If anything
        looks timing-sensitive, run it a second time.
     3. `spec-cleaner` (skill flags) with an empty diff, `osc
        service runall source_validator`, and the skill's
        `changes-lint.sh` and `changes-guard.sh`.
     4. The skill's adversarial change review over the whole diff.
     Save every local build log as
     `/tmp/opencode/<pkg>/logs/<target>-<timestamp>.log`. OBS keeps
     only the latest log per repository, so earlier failures are
     lost unless you keep them.
   - OBS is not the test runner. Each commit or push rebuilds every
     repository the project builds for. Collect all known failures
     locally, fix them together, and commit or push once.
   - After the commit or push, let the branch build on all repos and
     architectures. When builds finish, fetch the log of **every**
     failed repository, not just one, and group the failures by
     cause and codestream. Fix them locally and go through the local
     gate again before the next push.
   - **At most 3 OBS/IBS build rounds per phase.** If it is still red
     after the third, stop. Report the evidence (failing tests per
     repository, causes, what you tried) and don't push more guesses.
   - For a git-workflow PR: keep one commit rebased on the base
     branch, and force-push only to your fork's feature branch.
   - Phase A (Factory): Tumbleweed must build and pass on every arch
     the devel project builds (at least x86_64 and i586).
   - Phase B (IBS): all 5 architectures (aarch64, i586, ppc64le,
     s390x, x86_64) must build and pass for each codestream.
   - SLE usually ships an older upstream version, so the test set,
     needed skips and deps can differ. Adapt the change to that
     version; don't copy the Factory spec blindly (freerdp needed no
     skips on SLE 3.10.3).
   - Record the exact number of tests passed per target, and confirm
     the tests actually ran (not "0 tests").
   - If a test fails, find the root cause before skipping it. A real
     product bug gets a Bugzilla report, not a silent skip.
8. **Anticipate the maintainers.** Before you write the request, ask
   why the maintainers may push back, and answer it in the change and
   in the request message.
   - Read the package's request and pull request history. List the
     accepted, declined, revoked and superseded requests for the
     package and its devel project (`osc rq list -s all -P <prj> -p
     <pkg>`, the build service search API, and the PR list on the
     Gitea repo). For every declined or revoked one, read the review
     comments and the decline reason. Do the same for the review
     comments on accepted requests.
   - Read the maintainers' comments in the bug tracker for this
     package, and the recent `.changes` entries, to see what they care
     about.
   - Write down the concerns a maintainer could have about this
     change. The usual ones are build time and build service load, the
     build matrix and targets the devel project does not build, test
     relevance and what regression the tests actually prevent,
     flakiness, a missing bug or CVE reference, security and signing,
     scope creep, style, and "why now". Add any concern specific to
     this package or these maintainers.
   - For each concern write the evidence and the answer. Measure what
     can be measured. For build cost, compare the `%check` time with
     the same target without it. For flakiness, count the repeated
     builds of the same target and their results. State that test
     binaries do not change the shipped RPM, which code paths the
     tests cover in this package, and which targets are out of scope
     because the devel project does not build them.
   - If a past request was declined for a reason that still applies,
     stop and report it instead of resubmitting.
   - Put the concerns and answers in the report as `pushback`. The
     request message answers the top ones in one or two short
     sentences, not as a defensive list.
9. **Draft the request.**
   - Write the SR message in the reference style: 1 to 3 short
     paragraphs saying what changed and why, the skips with reasons,
     the new deps, and "<targets/archs> build and pass N tests".
   - If step 4 found that tests were turned off before, add one or two
     sentences on it to the `.changes` entry, the commit and the SR
     message: when it happened, the reason (with the bug reference),
     and why that reason no longer applies. For example: "Tests were
     disabled in 2019 because of a flaky network test (bsc#1234567).
     That test was removed upstream in 2.4." Say nothing about the
     history when the tests were never enabled.
   - Wrap lines at 72 characters. Apart from the test history above,
     don't narrate history.
   - **Show me the exact message and the `osc` / `git` command, and
     wait for my approval before you submit anything.**
   - Phase A: `osc sr` from the home branch to the devel project
     (like 1397741 / 1397742).
   - Phase B: use `osc mbranch` / `osc maintenancerequest`, which
     produces a `maintenance_incident` like 437785. Add the Factory
     SR URL to the message.
10. **Record the result** in `$ENABLER_DATA/progress.md`
   as one row per package: package | rank | test history (never
   enabled / disabled on date for reason, still valid?) | Factory SR |
   Factory status | SLE codestream(s) | SLE MR URL(s) | @IBS_GITEA@ PR
   | tests passed (TW / SLE) | skips | notes. Packages you skip (already done,
   can't be done, would need upstream changes) also get a row with the
   reason.
11. **Finish both phases.** A package counts as done only when both
   Phase A and Phase B are submitted. If Phase B is waiting for the
   Factory review, mark it `waiting-factory` in `progress.md` and
   come back to it.
12. **Move on** to the next package only after I confirm.

## Rules
- One package at a time. No batch submissions.
- Factory first: never submit to SLE before the Factory SR exists.
- Ask for all credentials at the start, in one question. Never ask
  for home directory access; work only in `/tmp/opencode/` and the
  work directory.
- Never submit, comment or file a bug without showing me the payload
  first.
- Don't claim a test count you didn't see in the build log.
- Never turn tests back on that someone disabled on purpose without
  first checking that their reason no longer applies.
- If enabling tests needs a large amount of work (heavy deps, network,
  root, a container runtime), stop and report the blocker instead of
  hacking around it.
- No commit or push without a green local build including `%check`
  (step 7). At most 3 OBS/IBS build rounds per phase, then stop and
  report.
- Don't gut the suite to get a green build. Stop and report when
  more than about half of the tests would need skipping, when
  `%check` would take more than about 60 minutes, or when it needs
  resources OBS workers don't have.
- Never merge, accept or decline a request or PR, including your own
  PR in a git-workflow devel project. The maintainers do that.
- Never open a request for a product line that is out of support.
  `osc maintained` is not a support list. Intersect it with
  `supported_codestreams.txt`, and when a codestream is missing from
  that file ask, do not submit.
- Use the TodoWrite tool to track the steps for the current package.

Start with step 1.
