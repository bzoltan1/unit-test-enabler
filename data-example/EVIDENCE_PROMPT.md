# Task: prove that the unit test we enable would have caught a customer bug

You investigate ONE package per run. You must produce real, reproducible
evidence, or say that there is none. A summary that merely matches
keywords is worthless. The only thing that counts is an experiment that
fails on the version customers had and passes with the fix.

The package name is in the message that started you. The priority rank
and the list data are in `/data/sle15_fix_order.csv`. The cached bug data
is in `/data/bug_scores.jsonl`, `/data/bug_details.json`,
`/data/ptf_bugmap.json` and `/data/changes_cache/`.

Write everything under `/results/evidence/<pkg>/`. Never modify the
package branches, never create a request or a pull request. This job is
read only on OBS, IBS, Gitea and Bugzilla. Local builds are allowed and
expected.

## The bar

A case is PROVEN only when all of these hold.

1. The bug is real, is about this package or its real source package,
   and it affected a version we shipped. The bug id is cited, and the
   affected version is named.
2. A maintenance update or a PTF fixed it. The request, incident or PTF
   id is cited.
3. The test exists in the affected version's source tree. You show the
   file and the registration (TESTS, check_PROGRAMS, meson test(),
   pytest collection, go test package, ctest add_test). If the test was
   added by the fix, it did not exist for the customer, so it cannot be
   the proof. Record that separately as RECURRENCE_ONLY.
4. The test is part of the suite the enablement job would actually run in
   the build environment. It must not need the network, qemu, loop
   devices, mount, root, a display, a TPM or real hardware. If it does,
   it could not have run in the build either, so it is NOT_RUNNABLE.
5. The affected source, built with the tests enabled, FAILS the test for
   the reason in the bug. The failing assertion, error message or crash
   must match the bug's symptom and the same code path.
6. The fixed source PASSES the same test.
7. The whole experiment is reproducible. You save the source revision or
   tarball and its checksum, the exact commands, the experimental spec,
   and the full logs.

Anything less is one of these, and you say which.

- `NOT_CAUGHT`: the test exists and runs but passes on the affected
  source. It would not have caught the bug.
- `RECURRENCE_ONLY`: the fix added the test. It prevents a regression but
  could not have caught the original customer bug.
- `NOT_RUNNABLE`: the test cannot run in the build environment.
- `NO_UPDATE`: the bug has no maintenance update and no PTF, so it was
  not a customer-facing shipped fix.
- `INCONCLUSIVE`: the affected version or the source cannot be obtained,
  or the build cannot be reproduced. State the exact reason.
- `NOT_A_CASE`: the bug is a packaging, dependency, translation, build
  failure or configuration issue that no unit test can exercise.

## Workflow

### 1. Identify the source packages

The list name can be a wrapper. `osc -A ibs maintained <pkg>` and
`osc -A ibs ls SUSE:SLE-15-SP7:Update <pkg>` show the real source. Follow
the same rule the enablement job uses (openssl -> openssl-3, dovecot ->
dovecot23, freerdp -> freerdp2). Record the mapping and use the real
source for the whole investigation.

### 2. Build the bug candidate list

`buglist <pkg> --fixed` prints the cached bugs with the update and PTF
flags. Then verify each one in Bugzilla with the MCP tools. You need:

- the bug summary, product, component and the affected version (from the
  bug fields and the first comments),
- whether it is fixed and how, with the request/incident/PTF id,
- the comments that describe the symptom and the reproduction.

Private bugs are common. If the MCP says the bug is not accessible, say
so, use the summary, the fix patch and the changelog, and mark the case
INCONCLUSIVE unless the experiment still gives an exact match.

Start with the highest scored non-security bugs, then the PTF bugs, then
the rest. Security bugs usually need a malformed input that the unit test
does not carry. Check them only when the test already has a trigger for
that class of input. Do not spend the budget on CVEs that no unit test can
exercise.

Aim to triage at least 20 candidates, and record every one with a verdict
and one sentence of reason, even the ones you reject quickly.

### 3. Find the affected version

Use these in order.

- The patchinfo and changelog of the maintenance request. The "from"
  version is the one customers had.
- The bug's product and version fields.
- `osc -A ibs log <release-project> <pkg>` and the previous revision.
- The GA project, for example `SUSE:SLE-15-SP6:GA`.

Write down how you decided, with the revision or version string.

### 4. Find the fix

The fix is either a patch in the maintenance incident or a version
update.

- Patch: the incident source contains it. `osc -A ibs rq show <id>`
  names the source project and package. Check out that source, find the
  patch named in the changelog or patchinfo, and read its diff. Note the
  files and functions it changes.
- Version update: the old tarball is the affected source, the new one is
  the fix.

Save the patch to `patches/` and the upstream commit URL if the bug or
patch names one.

### 5. Find the test in the affected tree

Extract the affected source tarball, or check out the affected revision.
Search the tree for the test that covers the function the fix changes.

- autotools: `TESTS`, `check_PROGRAMS`, `check_SCRIPTS`, `*_test.c`
- meson: `test(` in `meson.build`
- cmake/ctest: `add_test`, `*_test.c`, `tests/`
- pytest: `test_*.py`, `conftest.py`
- go: `*_test.go`
- Java: `src/test`, JUnit classes
- Rust: `#[test]`, `tests/`
- shell: `*.sh` under `tests/`, `make check` targets

Then read the test. Does it call the function that the fix changes? Does
it have an input that reaches the bug's trigger? If not, it cannot have
caught the bug, and you should say NOT_CAUGHT without a build, citing the
test source.

The test must be from the affected tree, unmodified. You may not add the
fix's own test and call it proof.

### 6. Build the experiment

Create `experiment/` under the results directory. Copy the affected
source there. Write an experimental spec that enables the test suite, the
same way the enablement job would (the test switch, `%check`, the
BuildRequires the tests need). This spec is only for the experiment and
is never submitted.

The experiment must contain two builds.

- `affected`: the source as customers had it, with the test enabled. It
  must fail the test for the bug's reason.
- `fixed`: the same source with the fix applied, or the fixed source. It
  must pass the same test.

When the fix is a patch, the cleanest experiment is to take the incident
source, remove the fix patch, and run the test. If the patch also adds
the test, the test is not in the affected tree, so the case is
RECURRENCE_ONLY. Check that before building.

When the fix is a version update, use the old tarball as the affected
source. Do not "revert" a large update.

Build with `local-build` in the experiment checkout. Keep the build root
small and delete it after the experiment. Save every log. If you need
only one test to prove the point, narrow the experimental `%check` to
that test and say so. The full suite result is for the enablement job, not
for this proof.

Read the log carefully. The failure must be the bug. A build failure, a
missing dependency, a timeout or an unrelated test failure is not proof.
If the test passes, it is NOT_CAUGHT. Do not tune the test until it
fails.

### 7. Match the symptom

Quote the failing line and the bug's symptom side by side. They must
describe the same thing, at the same function or assertion. Example of a
match. The bug says "xfreerdp crashes with a double free in
update_pointer_color", and the test fails with
`free(): double free detected` in `update_pointer_color`. Example of a
non-match. The bug is an SMB mount failure in `gvfsbackendsmb.c`, and the
test that fails is a URI encoding test in `client/test-uri-utils`. That
is not the same code path, so the case is rejected.

### 8. Audit before you finish

A second model audits your report. It reads the prompt, `evidence.json`,
the report and the logs. For every PROVEN case it must independently
check the following.

- The test file exists in the affected tarball or revision, and its
  registration.
- The failing log is from the affected build, and the failure is the
  bug's symptom.
- The passing log is from the fixed build.
- The commands in the report reproduce the logs.

It writes `review.json` with one verdict per case: `confirmed`,
`rejected` with the reason, or `needs_more`. You fix or downgrade every
rejected case. Only confirmed cases stay PROVEN. If the reviewer cannot
reproduce a case in the time it has, it marks it `needs_more`, and you
downgrade it to INCONCLUSIVE rather than leave it PROVEN.

### 9. Record the result

Write these files.

- `evidence.json`, the machine readable record. One object per case and
  one object per rejected candidate. Schema below.
- `report.md`, the human readable proof. One section per PROVEN case,
  with the bug, the update or PTF, the affected and fixed versions, the
  test, the two build commands, the failing and passing log lines, and
  the reviewer verdict. Then a table of every other candidate with its
  verdict and one line of reason.
- `logs/`, every build and test log, named with the target and the build
  kind (`affected` or `fixed`).
- `patches/`, the fix patch and the test source if they are small.
- `env.txt`, the container image id, the tool versions, the source
  checksums and the date.

Then append one row to the evidence ledger at
`/results/evidence/LEDGER.md` with this shape.

```
| package | rank | candidates | proven | recurrence | not caught | not runnable | no update | inconclusive | notes |
```

Nothing else. Do not edit the package list, `progress.md`, the package
branches, or any request.

## evidence.json schema

```json
{
  "package": "gvfs",
  "rank": 23,
  "source_packages": ["gvfs"],
  "run": {"started": "ISO8601", "finished": "ISO8601", "image": "sha256",
          "tools": {"osc": "version", "rpm": "version"}},
  "candidates": [
    {
      "bug": "bsc#1198718",
      "summary": "gvfs: Invalid argument mounting SMB shares",
      "product": "openSUSE Tumbleweed",
      "affected_version": "1.48.2",
      "fix": {"kind": "patch", "request": "https://build.opensuse.org/request/show/972027",
              "incident": null, "ptf": null, "patch": "gvfs-smb-ignore-EINVAL-kerberos-ccache.patch",
              "upstream": "https://gitlab.gnome.org/GNOME/gvfs/-/merge_requests/138"},
      "test": {"name": "test-uri-utils", "path": "client/test-uri-utils.c",
               "registered": "client/meson.build:test()", "in_affected_tree": true,
               "in_enablement_suite": true},
      "experiment": {"affected_source": "gvfs-1.48.2.tar.xz sha256 ...",
                     "fixed_source": "gvfs-1.48.2 + patch",
                     "affected_log": "logs/affected-standard-x86_64.log",
                     "fixed_log": "logs/fixed-standard-x86_64.log",
                     "affected_result": "FAIL: assertion ... at ...",
                     "fixed_result": "PASS"},
      "verdict": "PROVEN",
      "symptom_match": "bug says X, log says X at the same function",
      "review": "confirmed"
    }
  ],
  "summary": {"candidates": 12, "proven": 1, "recurrence": 0,
              "not_caught": 8, "not_runnable": 1, "no_update": 1,
              "inconclusive": 1, "not_a_case": 0}
}
```

Every candidate must appear, including the rejected ones, with a verdict
and a reason. The summary counts must match the candidates.

## Anti-patterns that reject a case

- The bug summary contains "crash" or "parse" and the test is somewhere
  in the same package.
- The fix patch touches a file that the test also touches, but the test
  passes on the affected code.
- The test was added by the fix.
- The test needs the network, a display, root or a device, so OBS would
  never have run it.
- The experiment modifies the test to make it fail.
- The failing log is a build error, a timeout, a missing package or a
  different test.
- The affected source is guessed from the summary, not from the version
  fields and the patchinfo.
- A PROVEN case without the reviewer's confirmation.

## Budget and stop rules

- One package per run. Work until every candidate has a verdict or the
  budget is gone.
- At most 8 local builds per run. If a candidate needs a build you cannot
  afford, record INCONCLUSIVE with the reason, do not guess.
- Max 6 hours of wall clock. Report what you did not reach.
- If the package is already enabled, the tests already run, so the
  interesting question is historical. Still do the analysis and say that
  the tests run today.
- If you cannot get the affected source, or the bug is inaccessible and
  the fix is not readable, INCONCLUSIVE.
- Never lower the bar to fill the report. A report with zero proven cases
  and honest verdicts is a good report.

Start with the source packages, then the candidate list, then the
strongest candidate. Prove one case end to end before you try the next.
A single reproducible case with two logs and a reviewer confirmation is
worth more than twenty summaries.
