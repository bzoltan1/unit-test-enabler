# Task: prove that the unit test we enable would have caught a customer bug

You investigate ONE package per run. The package name is in the message
that started you. You produce reproducible evidence, or you say that
there is none. A keyword match is worthless. Only an experiment counts.

The one sentence this job exists to support, filled in from evidence:

> bsc#N was reported against SLE 15 SPx and fixed by maintenance update
> SUSE-xU-YYYY:NNNN-1 (incident M) [or PTF P]. The test T is part of the
> shipped source of version V and runs in the build. Built from that
> exact source, T fails with <message> in <function>, the code the fix
> changes. With only the fix added, T passes. Had T run in the build of
> <build>, that build would have failed before customers got it.

Every slot in that sentence must be backed by a cited primary source and
a saved artifact. If one slot cannot be filled, the case is not PROVEN.

A report with zero PROVEN cases and honest verdicts is a good report.
Never lower the bar to fill it.

## Ground rules

- This prompt is the task for this job. The container `AGENTS.md` for
  this job is the evidence variant and points here. This prompt replaces
  `ENABLE_TESTS_PROMPT.md`: do not read that one for instructions, and
  do not enable tests in any real package. The hard rules in the
  container `AGENTS.md` still apply.
- Read only on OBS, IBS, Gitea and Bugzilla. Never commit, branch, create
  a request or PR, comment on a bug or change a bug. Local edits in
  `/work` checkouts and local builds are allowed and expected.
- Local builds only through `local-build`. Do not call `osc build`.
  When it prints `LOCAL BUILD LIMIT REACHED`, ignore its advice about
  the outcomes `impossible` or `blocked` (that is for the enablement
  job) and follow "Budget and stop rules" here.
- Work in `/work/<pkg>/`. Write results under `/results/evidence/<pkg>/`
  (the harness sets `ENABLER_PHASE_DIR` there, so `local-build` puts its
  logs in `logs/` and counts builds in `local_build_count`).
- Never write under `/data/`. Do not edit `progress.md`, the package
  list or anything under `/results/<pkg>/`. Only the reviewer writes
  `/results/evidence/LEDGER.md` (see "Review").
- Never print secrets. Private bugs: see "Private bugs" below.
- Write `evidence.json` after every candidate you finish (write to a
  temp file, then `mv`). The harness kills the worker at the time limit
  and treats a missing `evidence.json` as a failed run. At start, if
  `evidence.json` already exists, load it and continue from it.

## Inputs, and how far to trust them

- `/data/sle15_fix_order.csv`: rank, list status, `work_package`,
  enablement data. Use the row for the rank and the work package name.
- `/data/bug_scores.jsonl`, `/data/bug_details.json` (`buglist <pkg>`
  prints them): candidate hints only. Two known limits:
  - `in_changes` means the bug id appears somewhere in the SLE 15 SP7
    Update `.changes`. That file carries the whole history, including
    Factory entries pulled in by version updates. It does NOT prove a
    maintenance update.
  - `in_ptf` comes from `/data/ptf_bugmap.json`, which lists every bug
    id in each PTF's changelog. The lists are cumulative and full of old
    ids. A bug in a PTF list is usually not the reason for that PTF.
- `/data/changes_cache_update/<pkg>.changes`: the SP7 Update changelog.
- `/results/<pkg>/factory/` and `/results/<pkg>/sle/` (if present): the
  enablement job's `report.json`, `change.diff` and logs. They define the
  suite we enable, including the skips. A test that the enablement change
  skips cannot be the proof.
- `/data/progress.md`: the test history per package (never enabled,
  disabled on a date for a reason). Read the row for this package.

Primary sources win over the cache every time: Bugzilla via the
`bugzilla_*` MCP tools, IBS via `osc -A ibs`, upstream git.

## Verdicts

One verdict per candidate. Use exactly these strings.

- `PROVEN`: all of "The bar" below holds and the reviewer confirmed it.
- `RECURRENCE_ONLY`: the fix adds or changes the test. The test protects
  against a recurrence but did not exist for the customer.
- `NOT_CAUGHT`: a test that reaches the changed code exists, runs, and
  PASSES on the affected source (shown by a build).
- `NO_TEST`: no test in the affected tree reaches the code the fix
  changes, or none carries an input that hits the trigger. Decided
  statically, with the test sources cited.
- `NOT_RUNNABLE`: the test needs the network, root, mount, loop devices,
  qemu, a display, a TPM, real hardware or a service the build
  environment does not have, or the enablement change skips it.
- `NO_UPDATE`: no released SLE maintenance update and no PTF fixed it
  (Factory, Tumbleweed or Leap only, or never released).
- `NOT_A_CASE`: packaging, dependency, spec, translation, build failure,
  configuration, documentation or integration issue. No unit test can
  exercise it.
- `INCONCLUSIVE`: could not be decided. Give one reason code from this
  list and one sentence: `source_unavailable`, `fix_unreadable`,
  `fix_not_isolated`, `dependency_version`, `arch_unavailable`,
  `build_not_reproducible`, `budget_exhausted`, `bug_inaccessible`,
  `reviewer_unconfirmed`.

`NOT_REACHED` is not a verdict. Candidates you did not get to go into
`not_reached` with their ids, and their count goes into the summary.

## The bar for PROVEN

All of these, each with its citation in `evidence.json`.

1. Customer facing. The bug is fixed by a released SLE 15 maintenance
   update or a PTF built for a customer. Cite the incident number and the
   update id (SUSE-SU/RU/FU-YYYY:NNNN-1) or the PTF id, and the source of
   that fact (the `_patchinfo` of `SUSE:Maintenance:<N>`, or the
   Bugzilla comment that announces the released update).
2. The affected source is exact. It is the source revision of the
   package as released to customers right before the fix, identified by
   project, package, revision and srcmd5, with the tarball sha256.
   Guessing it from the summary or the version field alone fails this.
3. The test was in that source, unmodified, and registered in the build
   system (TESTS, check_PROGRAMS, meson `test()`, ctest `add_test`,
   pytest collection, go package, JUnit suite). Cite file and line.
4. The test is part of the suite the enablement change runs (or, if the
   package has no enablement result yet, the upstream default test
   target minus what is NOT_RUNNABLE). It needs nothing the build
   environment lacks.
5. The affected build FAILS that test, deterministically, for the bug's
   reason. The failing line names the same function or code path the fix
   changes and matches the bug's symptom.
6. The fixed build PASSES the same test, deterministically.
7. The affected and fixed builds differ ONLY by the fix: same checkout,
   same spec apart from the fix patch line, same repository, same arch,
   same build root package set. The saved source diff between the two
   equals the fix.
8. The fix is isolated: the patch or upstream commit(s) that fix this
   bug, not a whole version update.
9. You explain why the bug got past upstream, with one mechanism code:
   `downstream_patch` (a SUSE patch or backport introduced it),
   `backport_error`, `dependency_version` (a dependency version upstream
   CI did not use), `arch` (an architecture upstream CI does not run),
   `toolchain`, `test_not_in_upstream_ci`, `upstream_ignored_failure`.
   If you cannot name one, expect the affected build to pass. A shipped
   upstream release whose own test fails on its own code is rare.
10. You state which build would have run the test and failed, and
    therefore what it would have prevented (`prevention` in the schema):
    - `blocked_release`: the faulty code was introduced by this package's
      own build (a maintenance update, a SUSE patch, a version update,
      or it was there at GA). That build would have failed `%check`, so
      the faulty package would not have been released.
    - `rebuild_only`: the bug was triggered by another package (for
      example a samba update breaking gvfs). This package is not rebuilt
      when a dependency changes in an Update project, so the test would
      only have failed at the next rebuild of this package. Name that
      rebuild if one happened before the bug report, or say that none
      did. This is still a PROVEN test failure, but the report must not
      claim the test would have prevented the customer bug.
11. The reviewer confirmed it (see "Review").

If the package's tests already ran in the affected build (see the test
history in `progress.md`, for example frr, openssl-3, or dovecot before
2025-06-26), a failing test would have failed that build. Then the bug
cannot be a PROVEN case unless you show the test was skipped or disabled
in that build. Only bugs shipped while the tests were off are candidates
for such packages.

## Workflow

Keep a todo list. Order: map sources, harvest candidates, static triage
of all candidates, then builds for the few that pass every gate, best
candidate first. Finish one case end to end before you build the next.

### 1. Map the source packages and codestreams

The list name can be a wrapper or differ from the SLE name (openssl ->
openssl-1_1 / openssl-3, dovecot -> dovecot23, freerdp -> freerdp2). Use
`osc -A ibs maintained <pkg>` and `osc -A ibs ls SUSE:SLE-15-SPx:Update`
to find the real source package in every maintained SLE 15 codestream.
Record the mapping and the version per codestream.

### 2. Harvest candidates from primary sources

Build the candidate set as the union of these, one entry per bug id.

- Maintenance changelog entries. For each codestream, compare the
  Update `.changes` with the GA one (`osc -A ibs cat SUSE:SLE-15-SPx:GA
  <src> <src>.changes`, and the same for `:Update`). Entries above the
  GA top entry are maintenance updates. Their bsc/boo/bnc ids are
  candidates. Note the entry date and text.
- PTFs. For each PTF of the package in `ptf_bugmap.json`, the PTF's own
  bugs are its ids minus the ids of the previous PTF and minus the ids
  already in the base changelog. Only those are PTF candidates.
- The cache (`buglist <list name> --fixed`), as a cross check. The cache
  is keyed by the list name, not by the real source (for example dovecot,
  not dovecot23; openssl, not openssl-3). A cached bug that is in neither
  list above needs a primary source before it counts.

Rank the candidates for triage. Highest first:
1. Regressions: the entry or the bug says regression, revert, "broken
   by", "introduced by", "previous update", or the bug was opened within
   a few weeks of a maintenance release of the same package.
2. Bugs fixed by a SUSE patch in a package that carries downstream
   patches or backports.
3. Architecture specific bugs (big endian s390x/ppc64, 32-bit i586).
4. Non-security bugs by score.
5. Security bugs, only where the affected tree's test suite already
   carries inputs of that class (fuzz corpus, malformed file samples).
   Do not spend budget on a CVE no existing test input can reach.

### 3. Static triage, no builds

Run every candidate through these gates in order and stop at the first
that fails. Record each passed gate with its citation and the failed
gate with its reason. This is cheap. Do it for every candidate before
any build, so that builds go only to the best ones.

- G1 Customer facing. Find the released update or PTF. In this order:
  - The release notice in the bug, a comment by `swamp@suse.de` that
    starts `SUSE-RU-2022:2804-1: An update that ... can now be
    installed.` and lists `Sources used:` with a product and the
    released source NVR, for example `SUSE Linux Enterprise Module for
    Desktop Applications 15-SP4 (src): gvfs-1.48.1-150400.4.3.1`. If
    `Sources used` lists only openSUSE Leap, it is not an SLE update.
  - The incident `_patchinfo`: `osc -A ibs cat SUSE:Maintenance:<N>
    patchinfo _patchinfo` has `<issue tracker="bnc" id="<bug>">` and
    the description of every change in that incident.
  - The OBS bot comment `This bug (N) was mentioned in <request URL>`.
    A `build.opensuse.org` request is Factory or Leap, not an SLE
    update. A `build.suse.de` request leads to the incident through
    `osc -A ibs rq show <M>`.
  Fail: `NO_UPDATE`.
- G2 Code bug. The fix changes compiled code or scripts the tests can
  exercise. Fail: `NOT_A_CASE`.
- G3 Fix located. Save the fix patch to `patches/` and, if named, the
  upstream commit URL. For a version update, find the upstream commit
  that fixes this bug and save it. Fail: `INCONCLUSIVE`
  (`fix_unreadable` or `fix_not_isolated`).
- G4 Test in the affected tree. Get the affected source (step 4) and
  search it for tests of the files and functions the fix changes. If
  the fix itself adds or edits the test, the verdict is
  `RECURRENCE_ONLY`. If no test exists: `NO_TEST`.
- G5 Test reaches the code. Read the test. Write the call chain from the
  test to the changed function as `file:line` steps, and name the test
  input that reaches the bug's trigger. If either is missing: `NO_TEST`.
  Same package or same subsystem is not a call chain.
- G6 Runnable and enabled. The test runs in the build environment and in
  the enablement suite (point 4 of the bar). Fail: `NOT_RUNNABLE`.
- G7 Mechanism. Name the mechanism code from point 9 of the bar and the
  `prevention` value from point 10. This gate does not reject, it ranks.
  Candidates without a mechanism go last.

Only a candidate that passed G1 to G6 may get a build, and only after
its gate record is written to `evidence.json`.

Worked rejection, so you do not repeat it. gvfs bsc#1198718 ("Invalid
argument mounting SMB shares", samba 4.16). G1 passes: released for SLE
15 SP4 as SUSE-RU-2022:2804-1, incident 24312, gvfs-1.48.1. G2 to G4
pass: the fix `gvfs-smb-ignore-EINVAL-kerberos-ccache.patch` changes
`daemon/gvfsbackendsmb.c`, and gvfs has a registered test. G5 fails:
the only test we enable, `client/test-uri-utils`, tests URI encoding
and never calls the SMB backend. Verdict `NO_TEST`, zero builds. Two
more lessons from the same bug: the incident also carried a meson build
fix and a packaging change, so the fix to isolate is the one patch, not
the incident; and the bug was triggered by a samba update, so even a
test that reached the code would only be `rebuild_only`.

### 4. Get the exact affected source

The affected source is what customers had: the released package right
before the fix.

- `osc -A ibs log SUSE:SLE-15-SPx:Update <src>` lists one revision per
  maintenance release, each with the message `Set link to
  <src>.<incident> via maintenance_release request` and the request id.
  The full source of every released incident stays in the Update
  project as the package `<src>.<incident>`
  (`osc -A ibs ls SUSE:SLE-15-SPx:Update <src>.<incident>`).
- The fix incident comes from G1. The affected source is the incident
  released right before it in the same log. If the fix is the first
  revision in the log, the affected source is the GA package
  (`SUSE:SLE-15-SPx:GA`, or the GA of the older service pack the
  package is inherited from; `osc -A ibs maintained` tells you which).
- A package is often not in every `SP` Update project, because a later
  service pack inherits it from an earlier one. Use the codestream the
  bug's update was actually released in.
- Check out the affected package under `/work/<pkg>/bsc<N>/affected`
  with `osc -A ibs co SUSE:SLE-15-SPx:Update <src>.<incident>`. Record
  project, package, srcmd5 (`osc -A ibs api
  /source/<prj>/<package>` shows it in the `directory` element) and
  the tarball sha256.
- Cross check: the version in the affected spec must equal the source
  NVR of the previous release notice for that product, if there is one.
- If the exact source cannot be checked out, the case is
  `INCONCLUSIVE` (`source_unavailable`). Do not substitute a nearby
  version.

### 5. Build the experiment

Two checkouts per case, created from the same affected revision:

- `affected`: the affected source plus an experimental spec change that
  enables the test, the same way the enablement change does (test
  switch, `%check`, test BuildRequires). Copy the enablement change from
  `/results/<pkg>/*/change.diff` and adapt it to this version.
- `fixed`: identical to `affected`, plus the fix patch for this bug
  (taken from the fix incident's `<src>.<incident>` package) and its
  `Patch` and `%patch` lines. Nothing else, not the other changes the
  same incident carried.

The build pulls today's packages from the codestream repository, not
the ones from the time of the bug. The pair controls for that, because
both builds use the same set. For a `dependency_version` mechanism,
record the dependency version the log shows and check that it is one
that triggers the bug. If the codestream no longer carries a version
that triggers the bug and you cannot pin the old one, a passing affected
build does NOT mean the test misses the bug. Record `INCONCLUSIVE` with
reason `dependency_version` and the version the log shows. Never turn a
changed environment into `NOT_CAUGHT`.

Then save `diff -ruN affected fixed` (without `.osc/`) to
`patches/bsc<N>-affected-vs-fixed.diff`. It must contain only the fix.
If removing or adding the patch forces other changes (a later patch no
longer applies), the case is `INCONCLUSIVE` (`fix_not_isolated`).

In `%check`, run the target test explicitly three times, with markers
that make the log unambiguous, for example:

```
for i in 1 2 3; do
  echo "EVIDENCE-RUN $i BEGIN <test>"
  <run only this test>; rc=$?
  echo "EVIDENCE-RUN $i END <test> rc=$rc"
  [ $rc -eq 0 ] || fail=1
done
[ -z "$fail" ]
```

Narrowing `%check` to one test is fine for the proof. Say so in the
report. Do not modify the test, do not add assertions, do not use the
fix's own test, do not change timeouts or inputs until it fails.

Build the affected checkout first: `local-build <repo> <arch>` in that
directory (the SLE Update projects use `standard`; confirm with
`osc -A ibs repos SUSE:SLE-15-SPx:Update`). Build on
the arch the bug was reported on. Only x86_64 and i586 can be built
locally. A bug that needs another arch is `INCONCLUSIVE`
(`arch_unavailable`).

- If all three affected runs pass: `NOT_CAUGHT`. Do not build `fixed`.
- If the build fails before the test runs, or fails in another test or
  by timeout, it is not evidence. Fix the experiment setup once, or
  record `INCONCLUSIVE` (`build_not_reproducible`).
- If the affected runs fail for the bug's reason in all three runs,
  build `fixed`. All three fixed runs must pass. If the fixed build also
  fails, the failure is the environment, not the bug: `INCONCLUSIVE`.
- Mixed results within one build mean a flaky test, not proof:
  `INCONCLUSIVE` (`build_not_reproducible`).

For each build, record in `evidence.json` the build number from
`local_build_count`, the log path that `local-build` printed, the kind
(`affected` or `fixed`), the srcmd5 and the versions of the key
BuildRequires read from the log. Do not rename the logs.

Budget: `LOCAL_BUILD_LIMIT` (default 8) builds per run, shared with the
reviewer. Use at most the limit minus 2. The last 2 are the reviewer's.

### 6. Match the symptom

For a PROVEN candidate, write four quotes side by side in `report.md`
and `evidence.json`:

1. the bug's symptom (from the bug, or the fix patch if the bug is
   private),
2. the function and file the fix changes,
3. the test assertion or call that exercises it (`file:line`),
4. the failing log line from the affected build.

They must describe the same failure in the same function or code path.
A crash in another function, a different error, or an assertion about
something else is a non-match, and the case is rejected.

### 7. Write the results

Under `/results/evidence/<pkg>/`:

- `evidence.json`, schema below. Every candidate appears, rejected ones
  too, with the last gate reached and a one sentence reason.
- `report.md`. First the sentence from the top of this prompt, filled in,
  for each PROVEN case, then a section per PROVEN case with all
  citations, the two `local-build` commands, the source diff, the four
  quotes, and `prevention`. Then one table row per other candidate:
  bug, verdict, gate, reason. Then `not_reached`.
- `patches/`: fix patches, the affected-vs-fixed diffs, the experimental
  spec diffs, small test sources.
- `logs/`: written by `local-build`.
- `env.txt`: tool versions (`osc`, `rpm`, `build`), date, and the
  checksums of every source used.

End with the line `EVIDENCE DONE`.

## Private bugs

If the MCP says a bug is not accessible, record `bug_private: true` and
use the changelog entry, the patchinfo and the fix patch instead. A
private bug can still be PROVEN when points 1 to 10 of the bar hold from
those sources. Do not quote private bug comments in `report.md`; cite
the bug id and summarize the symptom in your own words from the patch.
Evidence from this job may later be quoted in public requests.

## Review

A reviewer model reads this same prompt and audits your output. If you
are the reviewer, this section is your task.

For every `PROVEN` case:

1. Re-derive the affected source yourself from IBS (the Update log, the
   `<src>.<incident>` package before the fix incident) and compare
   project, package and srcmd5 with `evidence.json`.
2. Check that the test file exists, unmodified, at the cited path in the
   affected source, and that it is registered.
3. Read `patches/bsc<N>-affected-vs-fixed.diff`. It must contain only
   the fix.
4. Read both logs. Find the EVIDENCE-RUN markers. Affected: three
   failures with the cited line. Fixed: three passes. Check the srcmd5
   and the build numbers match the `builds` records.
5. Check the four quotes and the call chain against the sources.
6. Check G1 (the update or PTF really released this fix) and the
   `prevention` value.
7. Rerun the affected build of one case with `local-build` and compare,
   if a build slot is left and the build fits in your time. Your limit
   is `$REVIEW_SECONDS` seconds from your start. Use the `%check` wall
   time in the worker's log to judge it, and leave 10 minutes for
   writing. A rebuild that would not finish is `"rebuild": "not run"`,
   not a reason to reject.

Also spot check up to five negative verdicts (`NO_TEST`, `NOT_CAUGHT`,
`NOT_A_CASE`). A wrong negative is a lost case. Reject it if the gate
record does not hold up.

Write `review.json`. The top level key is exactly `cases`, an array,
not a different name and not a bare list:

```json
{
  "package": "<pkg>",
  "cases": [
    {"bug": "bsc#1234567", "verdict": "confirmed",
     "checks": {"affected_revision": "ok", "test_in_tree": "ok",
                "diff_is_fix": "ok", "logs": "ok", "symptom": "ok",
                "update": "ok", "prevention": "ok", "rebuild": "not run"},
     "reason": "one sentence"}
  ]
}
```

`verdict` is `confirmed`, `rejected` or `needs_more`. Use the same `bug`
string as `evidence.json`. List every PROVEN case and every negative
you spot checked. The file must be valid JSON: the harness treats an
unreadable `review.json` as a rejection. Any `rejected` or `needs_more`
entry, negative or PROVEN, sends the worker into a fix round. After the
last review, the harness downgrades every PROVEN case that is not
`confirmed` to `INCONCLUSIVE`.

Then update the ledger. You are always the last agent in the run, so
the ledger row is yours. Count verdicts as they will stand after the
harness downgrade (a PROVEN case you did not confirm counts as
inconclusive). Replace any earlier row for this package, under a lock,
because other evidence jobs write the same file:

```
| package | rank | candidates | proven | recurrence_only | no_test | not_caught | not_runnable | no_update | not_a_case | inconclusive | not_reached | notes |
```

Create `/results/evidence/LEDGER.md` with that header and the
separator line if it does not exist. Write it with `flock
/results/evidence/LEDGER.md.lock` around a read, replace and `mv`.
`notes` is one short sentence, for example the PROVEN bug ids.

End with the line `AUDIT DONE`.

If you are the worker in a fix round, fix a rejected case only with new
evidence, otherwise downgrade it. Keep `evidence.json` and `report.md`
in sync.

## evidence.json schema

Values in angle brackets are placeholders. Bug ids are always written as
`bsc#<digits>`.

```json
{
  "package": "<list name>",
  "rank": 0,
  "source_packages": {"SUSE:SLE-15-SPx:Update": "<src> <version>"},
  "test_history": "<from progress.md>",
  "enablement_suite": "<path to change.diff or 'upstream default'>",
  "run": {"started": "<ISO8601>", "finished": "<ISO8601>",
          "tools": {"osc": "<v>", "rpm": "<v>"}},
  "candidates": [
    {
      "bug": "bsc#<N>",
      "bug_private": false,
      "summary": "<bug summary>",
      "codestream": "SUSE:SLE-15-SPx:Update",
      "harvested_from": ["maintenance_changes", "ptf", "cache"],
      "gates": {
        "G1": {"pass": true, "incident": "<N>", "update_id": "<SUSE-xU-...>",
               "ptf": null, "source": "<patchinfo path or bug comment id>"},
        "G2": {"pass": true, "note": "<what the fix changes>"},
        "G3": {"pass": true, "patch": "patches/<file>", "upstream": "<url or null>"},
        "G4": {"pass": true, "test": "<name>", "path": "<file:line>",
               "registered": "<file:line>", "added_by_fix": false},
        "G5": {"pass": true, "call_chain": ["<file:line>", "<file:line>"],
               "trigger_input": "<what>"},
        "G6": {"pass": true, "note": "<runs without network/root/...; not skipped>"},
        "G7": {"mechanism": "<code>", "prevention": "<blocked_release|rebuild_only>",
               "would_have_failed_build": "<which build>"}
      },
      "affected_source": {"project": "SUSE:SLE-15-SPx:Update",
                          "package": "<src>.<incident before the fix>",
                          "update_log_rev": "<n>", "srcmd5": "<md5>",
                          "tarball_sha256": "<sha>", "nvr": "<from release notice>",
                          "how_decided": "<one sentence>"},
      "fixed_source": {"package": "<src>.<fix incident>",
                       "patch": "patches/<file>",
                       "other_changes_in_incident": "<what was left out>"},
      "builds": [
        {"n": 1, "kind": "affected", "repo": "<repo>", "arch": "x86_64",
         "log": "logs/<file>", "srcmd5": "<md5>", "runs": "3/3 FAIL",
         "key_buildrequires": {"<name>": "<version>"}},
        {"n": 2, "kind": "fixed", "repo": "<repo>", "arch": "x86_64",
         "log": "logs/<file>", "srcmd5": "<md5>", "runs": "3/3 PASS",
         "key_buildrequires": {"<name>": "<version>"}}
      ],
      "source_diff": "patches/bsc<N>-affected-vs-fixed.diff",
      "symptom_match": {"bug": "<quote>", "fix": "<file:function>",
                        "test": "<file:line>", "log": "<quote>"},
      "verdict": "PROVEN",
      "reason_code": null,
      "reason": "<one sentence>",
      "review": "<set by the harness>"
    }
  ],
  "not_reached": ["bsc#<N>"],
  "summary": {"candidates": 0, "proven": 0, "recurrence_only": 0,
              "not_caught": 0, "no_test": 0, "not_runnable": 0,
              "no_update": 0, "not_a_case": 0, "inconclusive": 0,
              "not_reached": 0}
}
```

Rejected candidates carry only the gates they reached. Summary counts
must match the candidates plus `not_reached`. After the review the
harness rewrites `summary` from the verdicts alone and drops
`not_reached` from it, so the `not_reached` list is the record of it.

## Anti-patterns that reject a case

- The test is in the same package or subsystem, but there is no call
  chain from the test to the changed function.
- The bug summary says crash or parse and some test parses something.
- A build was started before the gate record was written.
- The test was added or changed by the fix, or by you.
- The test is skipped by the enablement change, or needs network, root,
  a device or a display.
- The affected source was taken from a nearby version, from Factory or
  from upstream instead of the released SLE revision.
- The fixed build differs from the affected build by more than the fix,
  or the fix is a whole version update.
- The failing log is a build error, a timeout, a missing package, a
  different test, or a failure that also happens in the fixed build.
- One failing run out of three.
- `rebuild_only` presented as "would have prevented".
- The fix came only through Factory, Tumbleweed or Leap.
- A PROVEN case without the reviewer's confirmation.

## Budget and stop rules

- One package per run. The worker has the wall clock the harness gives
  (`WORKER_SECONDS`, default 6 hours). Plan for static triage of every
  candidate in the first half and builds in the second half.
- Builds: `LOCAL_BUILD_LIMIT` minus 2 for the worker. When the limit is
  reached, the remaining build-worthy candidates are `INCONCLUSIVE`
  (`budget_exhausted`) with their gate records intact, so a later run
  can pick them up.
- Start with the strongest candidate (passed G1 to G6, has a mechanism,
  `blocked_release`). Prove or kill it end to end before the next.
- If time runs out, put the untriaged ids in `not_reached`. Never drop a
  candidate silently.
